"""按申报渠道和通知年度核对时间事项及对应原文。

数据来源（PostgreSQL）：
  kb1_schedules   时间节点行（event_type / raw_date / certainty + 证据条款指向）
  kb1_sections    证据条款原文（同一证据支持多个节点时只向模型发一次）
  kb1_sources     文件来源与时效（doc_no / applicable_year / region）

接口要点：evidence_section_id 非空且已核验，故用 INNER JOIN——时间
事项必须有原文依据才回答。certainty / precision_raw 区分确定日期与
「预计」表述，模型须按原文口径回答，不自行换算。
"""

from langchain_core.tools import tool

from career_agent.knowledge.pg_lookup import (
    REVIEWED_FILE, Keyword, SourceId, Year, handle_db_errors, read_snapshot,
)
from career_agent.knowledge.policy_sources import Scope, region_required, resolve_regions, scope_filters


# 全部筛选参数均可空（NULL 即不过滤）；channel/route 由 scope_filters(scope)
# 注入，regions 由 resolve_regions 归一后注入。year 匹配「通知年度」
# （f.applicable_year），同一通知可含次年事项，不做日期区间推断。
SCHEDULES = f"""
SELECT t.id AS schedule_id, t.event_type, t.raw_date, t.certainty, t.precision_raw,
       t.start_date::text, t.end_date::text, f.id AS source_id, f.title AS source_title,
       f.doc_no, f.applicable_year, f.route, f.region, s.id AS section_id
FROM public.kb1_schedules t
JOIN public.kb1_sources f ON f.id = t.source_id
JOIN public.kb1_sections s ON s.id = t.evidence_section_id AND s.source_id = f.id
WHERE {REVIEWED_FILE}
  AND (%(year)s::int IS NULL OR f.applicable_year = %(year)s)
  AND (%(channel)s::text IS NULL OR f.channel = %(channel)s)
  AND (%(route)s::text IS NULL OR f.route = %(route)s)
  AND (%(regions)s::text[] IS NULL OR f.region = ANY(%(regions)s))
  AND (%(source_id)s::text IS NULL OR f.id = %(source_id)s)
  AND (%(event)s::text IS NULL OR strpos(t.event_type, %(event)s) > 0)
ORDER BY f.id, t.id
"""


def create_application_schedule_tool(pool):
    @tool
    @handle_db_errors
    def lookup_application_schedule(scope: Scope | None = None, year: Year | None = None,
                                    region: Keyword | None = None, event_type: Keyword | None = None,
                                    source_id: SourceId | None = None) -> dict:
        """查申报、材料报送、资历计算等时间事项。year 指通知年度，同一通知可含次年的事项。

        “集团高工什么时候截止”用 scope=集团内部，留空 event_type 看全部节点。
        event_type 传具体事项关键词，如“预报名”。地方人社需确认地区。预计时间按原文回答。
        """
        # 渠道是「地方人社」却未给地区：引导模型先向员工确认省市再查，
        # 避免把全部地方文件混在一起返回错误口径的节点。
        if scope == "地方人社" and not region:
            return region_required()
        with read_snapshot(pool) as db:
            params = {**scope_filters(scope), "regions": resolve_regions(db, scope, region),
                      "year": year, "event": event_type, "source_id": source_id}
            rows = db.execute(SCHEDULES, params).fetchall()
            # 同一段原文经常支持多个时间事项，只向模型发送一次。
            evidence = db.execute("""
                SELECT id AS section_id, source_id, heading, text, source_locator_json AS source_locator
                FROM public.kb1_sections WHERE id = ANY(%s::text[]) ORDER BY id
            """, (list({row["section_id"] for row in rows}),)).fetchall() if rows else []
        return {"status": "found" if rows else "no_match", "results": rows, "evidence": evidence}

    return lookup_application_schedule
