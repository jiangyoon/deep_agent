"""回填奖励显示颜色、合并目录说明；默认只做离线预览。"""

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3

from add_document_number_lookup import connect


BATCH = Path(__file__).resolve().parents[2] / "data/material_review/batches/0848fe37e7ed/first_batch"


def blue_names(row):
    if not row["title_hint_color_checked"]:
        raise ValueError(f"Unverified source color: {row['id']}")
    if not row["title_hint_from_markdown"]:
        return []
    alternatives = row["combination_alternatives"]
    if not alternatives:
        name = row["certificate_name"].split("：", 1)[0]
        return [name.removesuffix("(仅限通过河北、北京考取)")]
    names = [name for group in alternatives for name in group]
    runs = row["certificate_font_runs"]
    if not runs:
        return list(dict.fromkeys(names))
    text = "".join(run["text"] for run in runs)
    mask = [run["blue"] for run in runs for _ in run["text"]]
    offset, colors = 0, {}
    for name in names:
        start = text.find(name, offset)
        if start < 0:
            raise ValueError(f"Certificate does not match its source text: {row['id']}")
        offset = start + len(name)
        color = set(mask[start:offset])
        if len(color) != 1 or (name in colors and colors[name] not in color):
            raise ValueError(f"Certificate needs a per-occurrence color: {row['id']}")
        colors[name] = color.pop()
    return [name for name, blue in colors.items() if blue]


def plan():
    manifest = json.loads((BATCH / "LATEST.json").read_text(encoding="utf-8"))
    path = Path(manifest["database"]).resolve()
    if hashlib.sha256(path.read_bytes()).hexdigest() != manifest["database_sha256"]:
        raise ValueError("Original extraction snapshot has changed")
    with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as db:
        rows = [json.loads(raw) for (raw,) in db.execute("SELECT data FROM rewards ORDER BY id")]
    colors = {row["id"]: blue_names(row) for row in rows}
    checks = {
        "N034-R048-01": ["一级建造师", "信息安全工程师"],
        "N034-R049-01": ["信息系统项目管理师", "信息安全工程师"],
        "N034-R050-01": ["信息系统项目管理师", "系统架构设计师", "系统分析师"],
    }
    if (len(colors) != 70 or sum(bool(names) for names in colors.values()) != 21
            or len({row["source_row"] for row in rows if colors[row["id"]]}) != 16
            or any(colors[key] != names for key, names in checks.items())
            or any(colors[f"N034-R004-0{n}"] != ["一级建造师注册证书"] for n in range(1, 4))):
        raise ValueError("Unexpected source color distribution")
    return rows, colors


def migrate(db, source_rows, colors, backup):
    from psycopg.types.json import Jsonb

    db.execute("SET LOCAL lock_timeout='5s'")
    db.execute("SET LOCAL statement_timeout='30s'")
    db.execute("LOCK TABLE public.kb1_rewards, public.kb1_shared_rules, public.kb1_reward_rules IN ACCESS EXCLUSIVE MODE")
    def read(table):
        return [row for (row,) in db.execute(f"SELECT to_jsonb(t) FROM public.kb1_{table} t")]
    rewards = {row["id"]: row for row in read("rewards")}
    rules = read("shared_rules")
    links = read("reward_rules")
    if set(rewards) != set(colors) or len(rules) != 6 or len(links) != 420:
        raise ValueError("Unexpected live inventory; no writes performed")
    for original in source_rows:
        live = rewards[original["id"]]
        for name in ("source_id", "source_row", "category", "variant", "combination_alternatives"):
            if live[name] != original[name]:
                raise ValueError(f"Live record no longer matches original color source: {live['id']}.{name}")
    grouped = defaultdict(list)
    for rule in sorted(rules, key=lambda row: row["id"]):
        grouped[rule["source_id"]].append(rule)
    expected_links = {(row["id"], rule["id"]) for row in rewards.values()
                      for rule in grouped.get(row["source_id"], [])}
    if {(row["reward_id"], row["rule_id"]) for row in links} != expected_links:
        raise ValueError("Rules are not uniformly shared by the catalog; links cannot be removed")
    with backup.open("x", encoding="utf-8") as handle:
        json.dump({"rewards": list(rewards.values()), "shared_rules": rules, "reward_rules": links},
                  handle, ensure_ascii=False, indent=2)

    db.execute("ALTER TABLE public.kb1_rewards ADD COLUMN blue_display_names text[] NOT NULL DEFAULT ARRAY[]::text[]")
    db.execute("COMMENT ON COLUMN public.kb1_rewards.blue_display_names IS '原奖励目录中标蓝的显示名称，只用于还原展示，不用于判断职称'")
    with db.cursor() as cursor:
        cursor.executemany("UPDATE public.kb1_rewards SET blue_display_names=%s WHERE id=%s",
                           [(names, identifier) for identifier, names in colors.items() if names])
    # 不使用 CASCADE；如果仍有其他数据库对象依赖旧表，整次迁移回滚。
    db.execute("DROP TABLE public.kb1_reward_rules")
    combined = {}
    for source, group in grouped.items():
        keep, *others = group
        locators = [row["source_locator_json"] for row in group]
        locator = locators[0] if all(item == locators[0] for item in locators) else locators
        text = "\n".join(row["text"] for row in group)
        db.execute("UPDATE public.kb1_shared_rules SET text=%s,source_locator_json=%s WHERE id=%s",
                   (text, Jsonb(locator) if locator is not None else None, keep["id"]))
        db.execute("DELETE FROM public.kb1_shared_rules WHERE id=ANY(%s)", ([row["id"] for row in others],))
        combined[source] = (text, locator)
    db.execute("ALTER TABLE public.kb1_shared_rules ADD CONSTRAINT kb1_shared_rules_source_unique UNIQUE (source_id)")
    for row in read("rewards"):
        if row.pop("blue_display_names") != colors[row["id"]] or row != rewards[row["id"]]:
            raise ValueError("Reward verification failed")
    if {row["source_id"]: (row["text"], row["source_locator_json"]) for row in read("shared_rules")} != combined:
        raise ValueError("Merged shared text does not match originals")
    return {"rewards": len(rewards), "marked_rewards": 21, "catalog_notes": len(combined),
            "archived_links": len(links), "backup": str(backup)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="执行 PostgreSQL 迁移")
    args = parser.parse_args()
    rows, colors = plan()
    if not args.apply:
        print(json.dumps({"rewards": len(rows), "marked_rewards": sum(map(bool, colors.values()))}))
        return
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    with connect() as db:
        result = migrate(db, rows, colors, BATCH / f"pg_reward_rules_before_merge_{stamp}.json")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
