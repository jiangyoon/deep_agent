"""按文号、别名或标题定位现行文件，保留同号候选供员工确认。

数据来源（PostgreSQL）：
  kb1_sources   文件来源（doc_no 规范文号 / doc_no_aliases 别名 / title）

定位分两条路（_find_documents 内分流）：
  1. 文号解析成功（如「182号」「人通〔2026〕182号」）→ 在有文号的行里
     逐行比对序号（is_candidate：序号须相等，年份/发文单位前缀宽松），
     避免「12号」误命中「112号」；
  2. 解析失败（视为标题关键词）→ 对 title 做连续子串匹配，可再按
     年份（applicable_year 或文号年份）与发文单位消歧。

唯一命中时附 related_bodies：印发通知 → 制度正文等知识性附件的
source_id，供模型继续调 search_policy_sections 查条款。
"""

from langchain_core.tools import tool

from career_agent.agent.ask_user import AskOption, ToolClarification
from career_agent.knowledge.document_number import is_candidate, normalize_reference, parse_reference
from career_agent.knowledge.notice_attachments import related_bodies
from career_agent.knowledge.pg_lookup import REVIEWED_FILE, Keyword, Year, handle_db_errors, read_snapshot


DOCUMENTS = f"""
SELECT f.id AS source_id, f.title AS source_title, f.doc_no, f.doc_no_aliases,
       f.source_kind, f.organization, f.route, f.region, f.applicable_year
FROM public.kb1_sources f
WHERE {REVIEWED_FILE}
  AND (%(organization)s::text IS NULL OR strpos(f.organization, %(organization)s) > 0)
"""


def _find_documents(db, query, year, organization):
    """返回来源行列表；空列表表示无命中。文号与标题两条路见模块 docstring。"""
    text = normalize_reference(query)
    try:
        issuer, reference_year, serial = parse_reference(text + "号" if text.isdecimal() else text)
    except ValueError:
        # ---- 路径 2：标题关键词匹配。纯数字（如「182」）补「号」后可走文号
        # 路径，只有解析仍失败才落到这里。文号年份与应用年度任一匹配即可，
        # 覆盖「无 applicable_year 的印发文件」也能按年份定位的场景。
        return db.execute(DOCUMENTS + """
            AND strpos(f.title, %(title)s) > 0
            AND (%(year)s::int IS NULL OR f.applicable_year = %(year)s
                 OR strpos(f.doc_no, '〔' || %(year)s::text || '〕') > 0)
            ORDER BY f.id
        """, {"title": query, "year": year, "organization": organization}).fetchall()

    # ---- 路径 1：文号比对。用户给的年份与文号里的年份冲突则直接无命中，
    # 不做「就近」猜测；reference 里缺的分量（单位/年份）不参与比对。
    if reference_year and year and int(reference_year) != year:
        return []
    reference = (issuer, reference_year or (str(year) if year else None), serial)
    rows = db.execute(DOCUMENTS + " AND f.doc_no IS NOT NULL ORDER BY f.id",
                      {"organization": organization})
    # 对比文号的序号部分，避免「12号」同时命中「112号」。
    return [row for row in rows if any(
        is_candidate(reference, number)
        for number in [row["doc_no"], *(row["doc_no_aliases"] or [])]
    )]


def create_policy_document_tool(pool):
    @tool
    @handle_db_errors
    def find_policy_document(query: Keyword, year: Year | None = None,
                             organization: Keyword | None = None) -> dict:
        """按文号、文号别名或标题关键词找文件，如“182号”“集团公司2026年度高级工程师”。

        可按文件年份、单位消歧。唯一命中时，related_bodies 列出可继续查条款的附件 ID。
        多个候选请按文件标题、年份或单位确认。
        """
        with read_snapshot(pool) as db:
            rows = _find_documents(db, query, year, organization)
            if len(rows) > 1:
                options = []
                for row in rows:
                    title, doc_no = row["source_title"], row["doc_no"]
                    details = [title, row["organization"], str(row["applicable_year"] or "")]
                    if doc_no and doc_no not in title:
                        details.append(doc_no)
                    options.append(AskOption(label=" · ".join(filter(None, details)), value=row["source_id"]))
                clarification = ToolClarification(
                    tool="ask_user", field="source_id", question="请选择你要查的文件。", options=options,
                )
                return {
                    "status": "ambiguous",
                    "results": rows,
                    "message": "已有年份或发文单位时先缩小范围。仍有多份时请员工选定文件。",
                    "clarification": clarification.model_dump(exclude_none=True),
                }
            if not rows:
                return {"status": "no_match", "results": []}
            rows[0]["related_bodies"] = related_bodies(db, rows[0]["source_id"])
        return {"status": "found", "results": rows}

    return find_policy_document
