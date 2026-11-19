"""整理职称对应表。默认从本地快照预览，--apply 才修改 PostgreSQL。"""

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import getpass
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import unicodedata


DOMESTIC = ("N017", "N035")
SCOPES = {"北京人社": "N017", "集团内部": "N035"}
SOFTWARE = "计算机技术与软件专业技术资格"
ROOT = Path(__file__).resolve().parents[2] / "data" / "material_review"
BATCH = ROOT / "batches" / "0848fe37e7ed" / "first_batch"


def normalize_name(value):
    value = " ".join(unicodedata.normalize("NFKC", value).split())
    return re.sub(r"(?<=[\u3400-\u9fff]) (?=[\u3400-\u9fff])", "", value)


def compact(value):
    return "".join(unicodedata.normalize("NFKC", value).split())


def split_reference(path):
    groups = defaultdict(list)
    for row in json.loads(path.read_text(encoding="utf-8"))["rows"]:
        if row["source_id"] != "N017" or not re.fullmatch(r"N017-M\d{3}(?:-\d{2})?", row["id"]):
            raise ValueError(f"Unexpected split reference: {row['id']}")
        groups[row["id"][:9]].append(row)
    if len(groups) != 62 or sum(map(len, groups.values())) != 105:
        raise ValueError("Unexpected number of source branches in the split reference")
    return {k: sorted(rows, key=lambda row: row["id"]) for k, rows in groups.items()}


def branches(row, references):
    candidates = references[f"N017-{row['id'].split('-', 1)[1]}"]
    if len(candidates) == 1:
        return [(row["id"], normalize_name(row["certificate_name"]), row["target_title_raw"])]

    # 每个拆分候选必须逐个匹配本渠道的条目原文，不能直接复制北京的条件到集团。
    rest = compact(row["target_title_raw"])
    family = compact(row["certificate_name"])
    family_head = family.split("(", 1)[0]
    output = []
    for candidate in candidates:
        label, colon, rest = rest.partition(":")
        title = compact(candidate["target_title_raw"])
        branch_name = normalize_name(candidate["certificate_name"])
        accepted_names = {label, family + label, f"{family}({label})", f"{family_head}({label})"}
        if not colon or not label or compact(branch_name) not in accepted_names or not rest.startswith(title):
            raise ValueError(f"Split candidate does not match its original label and title: {row['id']}")
        rest = rest[len(title):]
        output.append((f"{row['source_id']}-{candidate['id'].split('-',1)[1]}", branch_name,
                       normalize_name(candidate["target_title_raw"])))
    if rest:
        raise ValueError(f"Unmatched text in original mapping: {row['id']}")
    return output


def build(rows, references):
    aliases = {"一建": "一级建造师", "二建": "二级建造师", "软考": SOFTWARE}
    normalized_rows = []
    for row in rows:
        name = normalize_name(row["certificate_name"])
        if row["source_id"] not in DOMESTIC:
            normalized_rows.append(dict(row, certificate_name=name, certificate_family=None))
            continue
        for identifier, branch, title in branches(row, references):
            normalized_rows.append(dict(row, id=identifier, certificate_name=branch,
                                        certificate_family=name if branch != name else None,
                                        target_title_raw=title))
    for level in ("初级", "中级", "高级"):
        name = f"{SOFTWARE}({level})"
        aliases.update({f"软考{level}": name, f"{level}软考": name})
    keys = [row["id"] for row in normalized_rows]
    names = {row[key] for row in normalized_rows for key in ("certificate_name", "certificate_family")}
    counts = Counter(row["source_id"] for row in normalized_rows)
    if len(set(keys)) != len(keys) or counts != {"N017": 105, "N035": 105, "N025": 34}:
        raise ValueError(f"Unexpected normalized mapping distribution: {counts}")
    if any(alias == target or target not in names for alias, target in aliases.items()):
        raise ValueError("Alias points to a missing or identical name")
    return normalized_rows, aliases


def offline_rows(latest):
    manifest = json.loads(latest.read_text(encoding="utf-8"))
    database = Path(manifest["database"]).resolve()
    if hashlib.sha256(database.read_bytes()).hexdigest() != manifest["database_sha256"]:
        raise ValueError("Unverified SQLite snapshot")
    with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        return [dict(row) for row in db.execute("SELECT * FROM mappings ORDER BY id")]


def connect():
    import psycopg
    password = os.getenv("PGPASSWORD") or getpass.getpass("PostgreSQL password: ")
    return psycopg.connect(host=os.getenv("PGHOST", "172.18.10.133"), port=os.getenv("PGPORT", "6543"),
                           dbname="career", user=os.getenv("PGUSER", "sa"), password=password, connect_timeout=10)


def lookup_mappings(db, certificate, scope=None):
    """输入简称或完整证书名，返回各自的条件、出处和附表适用范围。"""
    name = normalize_name(certificate)
    source = SCOPES[scope] if scope else None
    alias = db.execute("SELECT certificate_name FROM public.kb1_certificate_aliases WHERE alias=%s", (name,)).fetchone()
    names = [name] if alias is None else [name, normalize_name(alias[0])]
    return db.execute(r"""
        SELECT m.certificate_name, m.target_title_raw, m.conditions_raw, m.source_id,
               m.evidence_section_id, f.title, f.region, f.route
        FROM public.kb1_mappings m JOIN public.kb1_sources f ON f.id=m.source_id
        WHERE m.record_status = 'review_ready' AND f.validity_status = '现行有效'
          AND (%s::text IS NULL OR m.source_id = %s)
          AND EXISTS (
              SELECT 1 FROM unnest(%s::text[]) AS terms(name)
              WHERE m.certificate_name = terms.name OR m.certificate_family = terms.name
                 OR (strpos(m.certificate_name, terms.name || '(') = 1
                     AND substring(m.certificate_name FROM char_length(terms.name)+1) ~ '^\(\d{4}[^()]*取得\)$')
          )
        ORDER BY m.source_id, m.serial_no, m.id
    """, (source, source, names)).fetchall()


def dump_previous_rows(path, rows):
    with path.open("x", encoding="utf-8") as handle:
        json.dump(rows, handle, ensure_ascii=False, default=str, indent=2)


def apply(db, reference, output):
    from psycopg import sql
    from psycopg.rows import dict_row
    from psycopg.types.json import Jsonb

    if not output.resolve().is_relative_to(ROOT):
        raise ValueError("Backup must be written under ignored data/material_review")
    db.execute("SET LOCAL lock_timeout='5s'")
    db.execute("SET LOCAL statement_timeout='40s'")
    db.execute("LOCK TABLE public.kb1_mappings IN SHARE ROW EXCLUSIVE MODE")
    db.execute("LOCK TABLE public.kb1_mappings_bak IN SHARE MODE")
    existing_alias_table = db.execute("SELECT to_regclass('public.kb1_certificate_aliases')").fetchone()[0]
    existing_aliases = {}
    if existing_alias_table:
        db.execute("LOCK TABLE public.kb1_certificate_aliases IN SHARE ROW EXCLUSIVE MODE")
        existing_aliases = dict(db.execute("SELECT alias,certificate_name FROM public.kb1_certificate_aliases").fetchall())
    if db.execute("""SELECT EXISTS (SELECT 1 FROM pg_constraint
                  WHERE contype='f' AND confrelid='public.kb1_mappings'::regclass)""").fetchone()[0]:
        raise ValueError("Dependent foreign keys would prevent a scope-safe restore")
    with db.cursor(row_factory=dict_row) as cursor:
        before = cursor.execute("SELECT * FROM public.kb1_mappings ORDER BY id").fetchall()
        restored = cursor.execute("""SELECT * FROM public.kb1_mappings_bak
                                   WHERE source_id IN ('N017','N035') ORDER BY id""").fetchall()
    if Counter(row["source_id"] for row in before) != {"N017": 62, "N025": 34}:
        raise ValueError("Unexpected current scope; no data overwritten")
    if Counter(row["source_id"] for row in restored) != {"N017": 62, "N035": 62}:
        raise ValueError("Unexpected backup scope; no data overwritten")
    if {row["id"]: row for row in restored if row["source_id"] == "N017"} != {
            row["id"]: row for row in before if row["source_id"] == "N017"}:
        raise ValueError("Live N017 no longer matches the backup; no data overwritten")
    columns = list(before[0])
    if any(set(row) != set(columns) for row in restored):
        raise ValueError("Backup fields differ from the live table")
    wanted, aliases = build(restored + [row for row in before if row["source_id"] == "N025"], reference)
    for alias, target in aliases.items():
        if alias in existing_aliases and normalize_name(existing_aliases[alias]) != target:
            raise ValueError(f"Existing alias conflicts with the migration plan: {alias}")
    dump_previous_rows(output, before)

    db.execute("ALTER TABLE public.kb1_mappings ADD COLUMN certificate_family text")
    db.execute("COMMENT ON COLUMN public.kb1_mappings.certificate_family IS '原条目证书系列；同一系列可含多个级别、时期或专业分支'")
    columns.append("certificate_family")
    db.execute("DELETE FROM public.kb1_mappings WHERE source_id IN ('N017','N035')")
    fields = sql.SQL(",").join(map(sql.Identifier, columns))
    placeholders = sql.SQL(",").join(sql.Placeholder() for _ in columns)
    with db.cursor() as cursor:
        insert = sql.SQL("INSERT INTO public.kb1_mappings ({}) VALUES ({})").format(fields, placeholders)
        cursor.executemany(insert, [
            tuple(Jsonb(value) if isinstance(value, (dict, list)) else value
                  for value in (row[column] for column in columns))
            for row in wanted if row["source_id"] in DOMESTIC
        ])
        cursor.executemany("UPDATE public.kb1_mappings SET certificate_name=%s WHERE id=%s AND source_id='N025'",
                           [(row["certificate_name"], row["id"]) for row in wanted if row["source_id"] == "N025"])
        if not existing_alias_table:
            cursor.execute("""
                CREATE TABLE public.kb1_certificate_aliases (
                    alias text PRIMARY KEY,
                    certificate_name text NOT NULL,
                    note text,
                    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """)
            cursor.execute("COMMENT ON TABLE public.kb1_certificate_aliases IS '证书简称与标准证书名称或系列的对应关系'")
            cursor.execute("COMMENT ON COLUMN public.kb1_certificate_aliases.alias IS '员工常用证书简称'")
            cursor.execute("COMMENT ON COLUMN public.kb1_certificate_aliases.certificate_name IS '标准证书名称或证书系列'")
            cursor.execute("COMMENT ON COLUMN public.kb1_certificate_aliases.created_at IS '别名入库时间'")
        # 已有别名及备注原样保留，只补缺少的叫法。
        cursor.executemany("INSERT INTO public.kb1_certificate_aliases(alias,certificate_name) VALUES (%s,%s)",
                           [(alias, name) for alias, name in aliases.items() if alias not in existing_aliases])

    with db.cursor(row_factory=dict_row) as cursor:
        actual = cursor.execute("SELECT * FROM public.kb1_mappings ORDER BY id").fetchall()
    if actual != sorted(wanted, key=lambda row: row["id"]):
        raise ValueError("Stored mapping fields differ from the migration plan")
    expected_aliases = {**aliases, **existing_aliases}
    for scope, source in SCOPES.items():
        qualified = lookup_mappings(db, "一建", scope)
        if len(qualified) != 1 or qualified[0][1] != "工程师" or qualified[0][3] != source:
            raise ValueError(f"Incorrect alias or scope lookup: {scope}")
        if len(lookup_mappings(db, "软考", scope)) != 3 or len(lookup_mappings(db, "软考高级", scope)) != 1:
            raise ValueError(f"Incorrect software qualification grouping: {scope}")
        if {row[0] for row in lookup_mappings(db, "建造师", scope)} != {"一级建造师", "二级建造师"}:
            raise ValueError(f"Certificate family lookup lost a level: {scope}")
        if "一造" in expected_aliases and not lookup_mappings(db, "一造", scope):
            raise ValueError(f"Existing dated certificate alias lost its target: {scope}")
    if db.execute("SELECT count(*) FROM public.kb1_mappings WHERE certificate_name='一级建造师'").fetchone()[0] != 2:
        raise ValueError("Canonical lookup did not return both sources")
    actual_aliases = dict(db.execute("SELECT alias,certificate_name FROM public.kb1_certificate_aliases").fetchall())
    if actual_aliases != expected_aliases:
        raise ValueError("Alias records differ from the plan")
    return Counter(row["source_id"] for row in wanted), len(actual_aliases)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true", help="按计划更新 PostgreSQL")
    mode.add_argument("--lookup", help="只读查询证书标准名或简称")
    parser.add_argument("--scope", choices=SCOPES, help="查询时指定适用渠道")
    parser.add_argument("--latest", type=Path, default=BATCH / "LATEST.json")
    parser.add_argument("--split-reference", type=Path, default=BATCH / "pg_n017_before_restore_20261118T092248237040Z.json")
    args = parser.parse_args()
    if args.scope and not args.lookup:
        parser.error("--scope only applies to --lookup")
    if args.lookup:
        with connect() as db:
            db.execute("SET TRANSACTION READ ONLY")
            print(json.dumps(lookup_mappings(db, args.lookup, args.scope), ensure_ascii=False, default=str))
        return
    reference = split_reference(args.split_reference)
    plan, aliases = build(offline_rows(args.latest), reference)
    if not args.apply:
        print(json.dumps({"mode": "offline", "rows": Counter(row["source_id"] for row in plan),
                          "aliases": len(aliases)}, ensure_ascii=False))
        return
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup = BATCH / f"pg_before_normalize_mappings_{stamp}.json"
    with connect() as db:
        counts, alias_count = apply(db, reference, backup)
    print(json.dumps({"committed": True, "rows": counts, "aliases": alias_count,
                      "pre_change_backup": str(backup)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
