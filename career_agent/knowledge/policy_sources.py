"""从 PostgreSQL 选适用的政策来源并读取条款原文。

职责（被 policy_search / application_schedule / certificate_reward 共用）：
  - scope_filters       scope（员工口中的渠道）→ channel/route 列的查询条件
  - resolve_regions     员工写的地区名 → 库中完整行政路径（含简称匹配）
  - _select_sources     组合以上条件选出有效来源（白名单），并计算
                        applicable_years（含年度通知挂载附件的年度继承）
  - find_source_ids     白名单入口（只返回 ID 列表，给 Milvus 过滤用）
  - load_ranked_sections 检索命中后回 PG 取原文，白名单复核 + 排名定序

年度语义（易混淆处）：applicable_year 指来源文件自身记录的年度；
年度通知的知识性附件通过 attachment_years CTE 继承主通知的年度。
"""

import re
from typing import Literal

from career_agent.agent.ask_user import ToolClarification
from career_agent.knowledge.pg_lookup import REVIEWED_FILE, read_snapshot


# 员工口中的四类申报渠道。地方渠道必须先确认地区再查。
Scope = Literal["集团内部", "公司内部", "北京人社", "地方人社"]
LOCAL_SCOPES = {"北京人社", "地方人社"}
BEIJING = "北京市"
# 地区名后缀模式，用于生成简称（北京市 → 北京；海淀区 → 海淀）。
REGION_SUFFIX = re.compile(r"(特别行政区|自治区|自治州|省|市|区|县)$")
# 奖励目录标题中的版次年份（「2025年版」），决定目录适用的默认年度。
EDITION_YEAR = re.compile(r"([12][0-9]{3})年版")


def region_required():
    """给查询调用方一个明确的地区确认入口。"""
    return {
        "status": "needs_region",
        "message": "地方人社查询需要申报省市。已有地区时补上 region 重查，否则调用 ask_user。",
        "results": [],
        "clarification": ToolClarification(
            tool="ask_user", field="region", question="你准备在哪个省市申报？",
        ).model_dump(exclude_none=True),
    }


# 来源白名单主查询。三个关键点：
#   attachment_years CTE——「年度通知挂载的知识性附件」自身 applicable_year
#     为空，其适用年度继承自主通知（notice_years），否则按年度过滤会漏；
#   EXISTS embedding_status='已入库'——只把已进向量库的来源交给 Milvus，
#     避免检索候选回表时才发现无正文；
#   全部筛选条件可空（NULL 不过滤），实际值由 scope_filters/resolve_regions 注入。
SOURCE_QUERY = f"""
WITH attachment_years AS (
    SELECT a.attachment_source_id,
           array_agg(DISTINCT notice.applicable_year) FILTER (
               WHERE notice.status = 'review_dataset' AND notice.validity_status = '现行有效'
                 AND notice.applicable_year IS NOT NULL
                 AND (notice.effective_from IS NULL OR notice.effective_from <= CURRENT_DATE)
                 AND (notice.effective_to IS NULL OR notice.effective_to >= CURRENT_DATE)
           ) AS notice_years
    FROM public.kb1_attachments a
    JOIN public.kb1_sources notice ON notice.id = a.main_source_id
    WHERE a.content_indexed AND a.purpose = '知识性附件' AND notice.source_kind = '年度通知'
    GROUP BY a.attachment_source_id
)
SELECT f.id AS source_id, f.title AS source_title, f.doc_no, f.source_kind,
       f.channel, f.route, f.region, f.applicable_year, f.effective_from::text,
       yearly.attachment_source_id IS NOT NULL AS annual_attachment, yearly.notice_years
FROM public.kb1_sources f
LEFT JOIN attachment_years yearly ON yearly.attachment_source_id = f.id
WHERE {REVIEWED_FILE}
  AND (%(source_id)s::text IS NULL OR f.id = %(source_id)s)
  AND (%(source_kind)s::text IS NULL OR f.source_kind = %(source_kind)s)
  AND (%(channel)s::text IS NULL OR f.channel = %(channel)s)
  AND (%(route)s::text IS NULL OR f.route = %(route)s)
  AND (%(regions)s::text[] IS NULL OR f.region = ANY(%(regions)s))
  AND EXISTS (
      SELECT 1 FROM public.kb1_sections s
      WHERE s.source_id = f.id AND s.embedding_status = '已入库'
  )
ORDER BY f.id
"""

# 回表取条款原文：双白名单（条款 ID + 来源 ID）+ 已入库校验。
SECTION_QUERY = """
SELECT id AS section_id, source_id, chapter, heading, text, source_locator_json AS source_locator
FROM public.kb1_sections
WHERE id = ANY(%s::text[]) AND source_id = ANY(%s::text[]) AND embedding_status = '已入库'
"""

# 某渠道下实际出现过的地区清单，供 resolve_regions 做别名归一。
REGION_QUERY = """
SELECT DISTINCT region
FROM public.kb1_sources
WHERE region IS NOT NULL AND region <> ''
  AND (%(channel)s::text IS NULL OR channel = %(channel)s)
  AND (%(route)s::text IS NULL OR route = %(route)s)
"""


def scope_filters(scope):
    """把员工口中的渠道翻译成 sources 表的 channel/route 查询条件。

    库里的标注口径：地方文件 channel='人社职称评价'；内部文件按 route
    区分（公司内部/集团内部）。scope 为 None 时两者都不约束（全渠道）。
    """
    if scope in LOCAL_SCOPES:
        return {"channel": "人社职称评价", "route": None}
    return {"channel": None, "route": scope}


def _region_aliases(name, regions):
    """从完整地区名生成简称，例如「北京市海淀区」可写成「海淀」「北京海淀」。"""
    parent = max(
        (item for item in regions if item != name and name.startswith(item)),
        key=len,
        default="",
    )
    leaf = name[len(parent):]
    short_parent = REGION_SUFFIX.sub("", parent)
    short_leaf = REGION_SUFFIX.sub("", leaf)
    return {
        name, parent + short_leaf, short_parent + leaf, short_parent + short_leaf, leaf, short_leaf,
    }


def resolve_regions(db, scope, region):
    """把员工写的地区名归一为库中的完整行政路径集合。

    返回三种值，语义各不相同：
      None  不按地区过滤（scope 为空 / 非地方渠道且未给地区）；
      []    有歧义或无法识别——调用方必须当作「该地区无可用文件」处理；
      [...] 库中与目标地区匹配的完整地区名（含上级，见下方区县规则）。

    北京人社渠道缺 region 时默认北京市——渠道名本身已含地区。
    """
    if not region and scope != "北京人社":
        return None
    requested = re.sub(r"[\s/／]+", "", region or BEIJING)
    known = {row["region"] for row in db.execute(REGION_QUERY, scope_filters(scope))}
    if scope == "北京人社":
        known = {name for name in known if name.startswith(BEIJING)}
    if requested not in known:
        # 未精确命中时先做简称匹配（海淀 → 北京市海淀区）；多个地区
        # 同名简称时返回 []（不猜），完全不像地区名也返回 []。
        matches = [name for name in known if requested in _region_aliases(name, known)]
        if len(matches) > 1:
            return []
        if matches:
            requested = matches[0]
        elif not REGION_SUFFIX.search(requested):
            return []

    # 库中的地区使用完整行政路径。区县可引用上级文件，市级问题只用市级文件。
    return sorted(name for name in known if requested.startswith(name))


def _select_sources(db, *, scope, region, year, source_kind, source_id):
    """白名单核心：SQL 粗筛 → Python 细筛，返回 {source_id: 来源信息}。

    细筛三条规则（SQL 表达不了或表达不清的口径）：
      - 年度附件的主通知不在有效范围内 → 该附件不可用（notice_years 为空）；
      - 给定 year 时，年度类来源（年度通知/其附件）必须覆盖该年度；
      - 输出形态统一：applicable_years 数组 + 奖励目录补 edition_year。
    regions == [] 是「地区歧义/未知」信号，直接返回空白名单。
    """
    regions = resolve_regions(db, scope, region)
    if regions == []:
        return {}
    params = {
        "source_kind": source_kind,
        "source_id": source_id,
        "regions": regions,
        **scope_filters(scope),
    }
    sources = {}
    for row in db.execute(SOURCE_QUERY, params):
        # 年度附件继承了主通知的年度；主通知失效/未审核时无可继承年度，剔除。
        if row["annual_attachment"] and not row["notice_years"]:
            continue
        recorded_year = row["applicable_year"]
        years = [recorded_year] if recorded_year is not None else sorted(row["notice_years"] or [])
        is_annual = row["source_kind"] == "年度通知" or row["annual_attachment"]
        # 非年度来源（制度办法等）不按 year 过滤——常设办法跨年度适用。
        if year is not None and is_annual and year not in years:
            continue

        # 统一输出形态：扁平化字段 + applicable_years 数组。
        source = dict(row)
        del source["annual_attachment"], source["notice_years"], source["applicable_year"]
        source["applicable_years"] = years
        if source["source_kind"] == "奖励目录":
            edition = EDITION_YEAR.search(source["source_title"])
            if edition:
                source["edition_year"] = int(edition[1])
        sources[source["source_id"]] = source
    return sources


def find_source_ids(pool, filters):
    """先选文件，再把同一份文件 ID 交给两路检索。"""
    with read_snapshot(pool) as db:
        return list(_select_sources(db, **filters))


def load_ranked_sections(pool, candidates, filters, limit):
    """用同一数据库快照复核来源和正文，按 Milvus 排名返回原文。

    candidates 是 [(section_id, source_id), ...]（按检索相关性排序）。
    白名单在此二次复核：检索到结果后过滤条件若发生变化（换版、
    置失效），在这里被挡下，保证「答案里的原文」与「白名单」一致。
    """
    with read_snapshot(pool) as db:
        sources = _select_sources(db, **filters)
        section_ids = [section_id for section_id, source_id in candidates if source_id in sources]
        if not section_ids:
            return []
        rows = db.execute(SECTION_QUERY, (section_ids, list(sources))).fetchall()
    sections = {(row["section_id"], row["source_id"]): row for row in rows}

    results = []
    for key in candidates:
        # 按候选排名顺序输出；source 信息与 section 字段合并为一条结果。
        section = sections.get(key)
        if section is not None:
            results.append({**sources[section["source_id"]], **section})
            if len(results) == limit:
                break
    return results
