"""按主文件版本列附件，并沿知识性附件定位政策正文。

数据来源（PostgreSQL）：
  kb1_attachments   主文件 → 附件挂接（main_source_id 外键 + purpose/编号/下载策略）
  kb1_sources        来源与时效（附件本身也是一行来源，才能复用有效性条件）

设计背景（对应清洗方案「附件的信息入库、内容不入库」原则）：
  - 报名表/评审表等空白模板不解析内容、不进向量库，只登记元数据供下载；
  - 知识性附件（办法正文等）已切块入向量库，可继续查条款。

两个入口的区别：
  - related_bodies()      内部函数：给 policy_document 用，只列已审核且
                           content_indexed 的知识性附件（模型据此继续查条款）；
  - list_notice_attachments 工具：给模型/员工用，列全部附件（含模板），
                           purpose=报名模板 时把「材料包内附件」一并算入。
"""

from typing import Literal

from langchain_core.tools import tool

from career_agent.knowledge.pg_lookup import (
    CURRENT_FILE, REVIEWED_FILE, SourceId, handle_db_errors, read_snapshot,
)


Purpose = Literal["报名模板", "知识性附件", "材料包内附件", "修订对比资料"]

# 附件有效性放宽到 CURRENT_FILE（不要求 review_dataset）：报名模板等
# 空白表单本来就不可做正文解析，其来源 status 是 opaque_*，但挂接关系
# 仍应展示。purpose 过滤的兼容规则：员工问「报名模板」时，「材料包内
# 附件」中 kind 为报名模板的也算——一个材料包下模板散装两层。
ATTACHMENTS = f"""
SELECT a.attachment_source_id AS source_id, a.attachment_no, a.display_name,
       a.purpose, f.source_kind, a.content_indexed, a.download_policy, f.status AS source_status
FROM public.kb1_attachments a
JOIN public.kb1_sources f ON f.id = a.attachment_source_id
WHERE a.main_source_id = %(parent)s
  AND (%(purpose)s::text IS NULL OR a.purpose = %(purpose)s
       OR (%(purpose)s = '报名模板' AND a.purpose = '材料包内附件' AND f.source_kind = '报名模板'))
  AND {CURRENT_FILE}
  AND f.status IN ('review_dataset', 'opaque_attachment_no_body_parsing', 'history_metadata_only')
ORDER BY a.sort_order, a.id
"""


def related_bodies(db, parent):
    """用于后续条款检索，只返回已审核且关系标为已索引的知识性附件。"""
    rows = db.execute(ATTACHMENTS, {"parent": parent, "purpose": "知识性附件"})
    return [{"source_id": row["source_id"], "display_name": row["display_name"]}
            for row in rows if row["content_indexed"] and row["source_status"] == "review_dataset"]


def create_notice_attachments_tool(pool):
    @tool
    @handle_db_errors
    def list_notice_attachments(source_id: SourceId, purpose: Purpose | None = None) -> dict:
        """按已定位的通知或材料包 source_id 列出直属附件。只找报名表时传 purpose=报名模板。

        返回原文件名、附件编号、用途及下载策略。需审核的附件只展示名称和编号。
        """
        with read_snapshot(pool) as db:
            # 先验证父来源本身有效（已审核且现行有效），防止给一个
            # 已失效文件列出附件；再查挂接关系。
            parent = db.execute(f"""
                SELECT f.id AS source_id, f.title AS source_title, f.doc_no, f.applicable_year
                FROM public.kb1_sources f WHERE f.id = %s AND {REVIEWED_FILE}
            """, (source_id,)).fetchone()
            if not parent:
                return {"status": "no_verified_source", "results": []}
            rows = db.execute(ATTACHMENTS, {"parent": source_id, "purpose": purpose}).fetchall()
        return {"status": "found" if rows else "no_match", "parent": parent, "results": rows}

    return list_notice_attachments
