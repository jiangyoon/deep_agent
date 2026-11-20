"""补文号查询入口和奖励级别约束。默认只预览，--apply 才修改 PostgreSQL。"""

import argparse
import getpass
import json
import os
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from career_agent.knowledge.document_number import is_candidate, normalize_reference, parse_reference


DOCUMENT_NUMBERS = {
    "N003": "人通〔2026〕182号",
    "N006": "人通〔2026〕220号",
    "N019": "京人社事业发〔2020〕12号",
    "N044": "人通〔2026〕244号",
}
ALIASES = {"N003": ["移集成人〔2026〕182号"]}
VALID_GRADES = {"S", "A", "A-", "B", "C"}
GRADE_CONSTRAINT = "kb1_rewards_grade_check"


def resolve_document_number(db, text):
    """先定位文号所在文件，再返回其知识性附件；有多条候选时保留各自的出处。"""
    from psycopg.rows import dict_row

    normalized = normalize_reference(text)
    issuer, year, serial = parse_reference(normalized)
    with db.cursor(row_factory=dict_row) as cursor:
        columns = "SELECT id AS source_id,title,doc_no,doc_no_aliases,validity_status FROM public.kb1_sources"
        matches = []
        # 完整文号走等值查询，简称要按序号和可选的发文单位、年份消歧。
        if issuer and year:
            exact = f"{issuer}〔{year}〕{serial}号"
            matches = cursor.execute(columns + """
                WHERE doc_no=%s OR %s=ANY(doc_no_aliases)
                ORDER BY id
            """, (exact, exact)).fetchall()
        if not matches:
            candidates = cursor.execute(columns + " WHERE doc_no IS NOT NULL ORDER BY id").fetchall()
            matches = [row for row in candidates if any(
                is_candidate((issuer, year, serial), variant)
                for variant in [row["doc_no"], *(row["doc_no_aliases"] or [])]
            )]
        for row in matches:
            # 法规正文已按知识性附件关联；历史修订对照和报名模板不作为正文候选。
            row["related_body"] = cursor.execute("""
                SELECT DISTINCT f.id AS source_id,f.title,f.validity_status
                FROM public.kb1_attachments a
                JOIN public.kb1_sources f ON f.id=a.attachment_source_id
                WHERE a.main_source_id=%s AND a.purpose='知识性附件' AND a.content_indexed
                ORDER BY f.id
            """, (row["source_id"],)).fetchall()
        return matches


def connect():
    import psycopg

    password = os.getenv("PGPASSWORD") or getpass.getpass("PostgreSQL password: ")
    return psycopg.connect(host=os.getenv("PGHOST", "172.18.10.133"), port=os.getenv("PGPORT", "6543"),
                           dbname="career", user=os.getenv("PGUSER", "sa"), password=password, connect_timeout=10)


def grades(db):
    return db.execute("SELECT grade,count(*) FROM public.kb1_rewards GROUP BY 1 ORDER BY 1 NULLS LAST").fetchall()


def migrate(db):
    from psycopg.errors import CheckViolation

    db.execute("SET LOCAL lock_timeout='5s'")
    db.execute("SET LOCAL statement_timeout='30s'")
    sources = dict(db.execute("SELECT id,title FROM public.kb1_sources WHERE id=ANY(%s)",
                              (list(DOCUMENT_NUMBERS),)).fetchall())
    for source_id, number in DOCUMENT_NUMBERS.items():
        if number not in normalize_reference(sources.get(source_id, "")):
            raise ValueError(f"Source title no longer matches the confirmed document number: {source_id}")
    initial_grades = grades(db)
    if any(grade is not None and grade not in VALID_GRADES for grade, _ in initial_grades):
        raise ValueError("Unexpected existing reward grade; no data changed")
    db.execute("""
        ALTER TABLE public.kb1_sources
          ADD COLUMN IF NOT EXISTS doc_no varchar(128),
          ADD COLUMN IF NOT EXISTS doc_no_aliases text[]
    """)
    db.execute("COMMENT ON COLUMN public.kb1_sources.doc_no IS '文件自身的正式文号；不得把正文引用的其他文号写入本列'")
    db.execute("COMMENT ON COLUMN public.kb1_sources.doc_no_aliases IS '已知替代写法，用于查找本文件；简写由查询解析'")
    db.execute("CREATE INDEX IF NOT EXISTS idx_sources_doc_no ON public.kb1_sources(doc_no)")
    for source_id, number in DOCUMENT_NUMBERS.items():
        existing, current_aliases = db.execute("SELECT doc_no,doc_no_aliases FROM public.kb1_sources WHERE id=%s",
                                               (source_id,)).fetchone()
        if existing is not None and normalize_reference(existing) != number:
            raise ValueError(f"Existing document number conflicts for {source_id}")
        aliases = list(dict.fromkeys((current_aliases or []) + ALIASES.get(source_id, [])))
        db.execute("UPDATE public.kb1_sources SET doc_no=%s, doc_no_aliases=%s WHERE id=%s",
                   (number, aliases or None, source_id))

    if not db.execute("""
            SELECT EXISTS (SELECT 1 FROM pg_constraint
            WHERE conrelid='public.kb1_rewards'::regclass AND conname=%s AND contype='c')
        """, (GRADE_CONSTRAINT,)).fetchone()[0]:
        db.execute("""
            ALTER TABLE public.kb1_rewards ADD CONSTRAINT kb1_rewards_grade_check
                CHECK (grade IS NULL OR grade IN ('S','A','A-','B','C'))
        """)
    db.execute("ALTER TABLE public.kb1_rewards VALIDATE CONSTRAINT kb1_rewards_grade_check")

    checks = [
        ("182号", "N003"), ("人通〔2026〕182号", "N003"), ("移集成人〔2026〕182号", "N003"),
        ("移集成人182号", "N003"), ("京人社12号", "N019"), ("220号", "N006"), ("244号", "N044"),
    ]
    for ref, expected in checks:
        matches = resolve_document_number(db, ref)
        if [match["source_id"] for match in matches] != [expected]:
            raise ValueError(f"Document lookup failed verification: {ref}")
        if expected == "N003" and [related["source_id"] for related in matches[0]["related_body"]] != ["N001"]:
            raise ValueError("The 182 notice did not resolve to the management policy body")

    sample = db.execute("SELECT id FROM public.kb1_rewards ORDER BY id LIMIT 1").fetchone()
    if sample is None:
        raise ValueError("No existing reward available for the constraint verification")
    # 内层事务是保存点；验证非法值被拒绝，不留下测试数据。
    try:
        with db.transaction():
            db.execute("UPDATE public.kb1_rewards SET grade='X' WHERE id=%s", (sample[0],))
    except CheckViolation as error:
        if error.diag.constraint_name != GRADE_CONSTRAINT:
            raise
    else:
        raise ValueError("The reward grade constraint accepted X")
    if grades(db) != initial_grades:
        raise ValueError("Reward records changed during validation")
    return {"document_numbers_checked": len(DOCUMENT_NUMBERS), "lookups_verified": len(checks),
            "grade_distribution": initial_grades, "invalid_grade_rejected": True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true", help="在 PostgreSQL 执行迁移并验证")
    mode.add_argument("--lookup", help="只读查询一个完整文号或简称")
    args = parser.parse_args()
    if args.lookup is None and not args.apply:
        print(json.dumps({"document_numbers": DOCUMENT_NUMBERS, "aliases": ALIASES,
                          "allowed_grades": sorted(VALID_GRADES) + [None]}, ensure_ascii=False))
        return
    with connect() as db:
        if args.lookup is not None:
            db.execute("SET TRANSACTION READ ONLY")
            result = resolve_document_number(db, args.lookup)
        else:
            result = migrate(db)
    if args.apply:
        result["committed"] = True
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
