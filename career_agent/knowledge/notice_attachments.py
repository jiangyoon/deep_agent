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
SELECT a.id AS attachment_id, a.attachment_source_id AS source_id, a.attachment_no, a.display_name,
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

# 网页端的附件下载端点（web/app.py 提供同名路由，登录后可下载原件）。
# 相对地址随站点部署走，模型把这一字段原样放进答案的 markdown 链接即可。
DOWNLOAD_URL = "/api/attachments/{}/download"


def related_bodies(db, parent):
    """用于后续条款检索，只返回已审核且关系标为已索引的知识性附件。"""
    rows = db.execute(ATTACHMENTS, {"parent": parent, "purpose": "知识性附件"})
    return [{"source_id": row["source_id"], "display_name": row["display_name"]}
            for row in rows if row["content_indexed"] and row["source_status"] == "review_dataset"]


# 可下载附件的定位查询：附件自身口径与上面 ATTACHMENTS 一致（现行有效 +
# 状态白名单），另加 download_policy 判定——只有显式标注
# 「需登录 + 需发布审核」策略的附件才可能下载；父来源（主文件）是否
# 同样已审核且现行有效，由 create_attachment_reader 用与工具侧 parent
# 查询完全同文的 REVIEWED_FILE 片段二次校验，保证换版/停用时
# 「工具列得出、路由下不了」的口径漂移不会发生。
DOWNLOADABLE_ATTACHMENT = f"""
    SELECT a.display_name, a.main_source_id, f.original_path
    FROM public.kb1_attachments a
    JOIN public.kb1_sources f ON f.id = a.attachment_source_id
    WHERE a.id = %s
      AND {CURRENT_FILE}
      AND f.status IN ('review_dataset', 'opaque_attachment_no_body_parsing', 'history_metadata_only')
      AND a.download_policy = 'requires_authenticated_internal_access_and_release_review'
"""


def create_attachment_reader(pool):
    """下载路由的库侧闭包：attachment_id → (display_name, original_path)。

    与工具共用同一连接池；查询本身不校验登录（路由侧 current_user 兑现
    策略里的 authenticated 部分），这里兑现策略名里的 release_review
    部分：附件自身满足 ATTACHMENTS 同款条件（download_policy 显式放行
    + 现行有效 + 状态白名单），且父来源满足 REVIEWED_FILE（已审核 +
    现行有效，与工具侧 parent 校验同一片段）。REPEATABLE READ 下两步
    看到同一版本数据。
    """

    def find_downloadable_attachment(attachment_id):
        with read_snapshot(pool) as db:
            row = db.execute(DOWNLOADABLE_ATTACHMENT, (attachment_id,)).fetchone()
            if row is None:
                return None
            parent_ok = db.execute(f"""
                SELECT 1 FROM public.kb1_sources f WHERE f.id = %s AND {REVIEWED_FILE}
            """, (row["main_source_id"],)).fetchone()
            if not parent_ok:
                return None
        return row["display_name"], row["original_path"]

    return find_downloadable_attachment


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
        # download_url 是网页上员工可直接下载原件的地址。下载策略
        # requires_authenticated_internal_access_and_release_review 分两半
        # 兑现：路由侧登录校验（authenticated）+ 库侧 create_attachment_reader
        # 的口径校验（release_review），工具本身不做二次判断。
        for row in rows:
            row["download_url"] = DOWNLOAD_URL.format(row["attachment_id"])
        return {"status": "found" if rows else "no_match", "parent": parent, "results": rows}

    return list_notice_attachments
