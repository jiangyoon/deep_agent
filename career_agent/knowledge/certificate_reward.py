"""查询奖励目录的单证、组合及年度专项，保留金额单位和表格出处。

数据来源（PostgreSQL）：
  kb1_rewards               奖励目录行（一行 = 一个证书/组合 × 一个专业分项）
  kb1_shared_rules          目录级通用规则（每行带溯源，随命中结果一并返回）
  kb1_certificate_aliases   简称 → 规范名（一建 → 一级建造师，组合内逐项展开）

返回约定（面向模型，与 SKILL.md 一致）：
  - 金额字段为空表示目录未列金额，不能当作 0；
  - blue_display_names 仅为展示样式信息，不影响资格判断；
  - no_matching_variant 时附带 catalog_entries（同证书其他分项的说明与
    补贴口径），供模型提示员工正确分项，但禁止套用其他分项的一次性金额。

与其他工具的关系：奖励金额走本工具（精确查询）；「对应什么职称」走
certificate_mapping；两表的证书写法口径不完全一致（见 _certificate_names）。
"""

import re
from datetime import date
from itertools import groupby
from operator import itemgetter
from typing import Literal

from langchain_core.tools import tool

from career_agent.knowledge.certificate_mapping import normalize_name
from career_agent.knowledge.pg_lookup import REVIEWED_FILE, Keyword, Year, handle_db_errors, read_snapshot
from career_agent.knowledge.policy_sources import EDITION_YEAR


# C2 已确认这版目录沿用至 2026 年。新年度需要新的依据，专项仍按行内起止日期。
CATALOG_EXTENSIONS = {"N034": {2026}}
Category = Literal["单证奖励", "一人多证奖励", "报销考试费", "年度专项奖励"]

# 查询分三层：
#   来源有效性  REVIEWED_FILE——已审核且现行有效（见 pg_lookup.py）
#   可选过滤    category 精确匹配；effective_start/end 与查询年度有交集
#               （年度专项常带固定有效期，过期年度自然查不到）
#   名称匹配    只用 names[0]（组合的第一个成员）做子串粗召回；组合成员
#               完整性校验交给 Python 侧 _matches_combination——SQL 不做
#               组合语义，防止把组合行当成单证行误召回。
REWARDS = f"""
SELECT r.id AS reward_id, r.source_id, f.title AS source_title, f.applicable_year,
       r.certificate_name, r.source_row, r.variant, r.category, r.grade, r.grade_raw,
       r.one_time_reward_yuan, r.leave_days, r.registration_yuan, r.registration_period,
       r.registration_raw, r.usage_cap_yuan, r.usage_period, r.usage_raw,
       r.reimbursement_cap_yuan, r.annual_cap_yuan, r.per_project_yuan,
       r.effective_start::text, r.effective_end::text, r.conditions_raw, r.acquisition_raw,
       r.name_note, r.combination_alternatives, r.blue_display_names,
       r.evidence_section_id, r.source_locator_json AS source_locator
FROM public.kb1_rewards r
JOIN public.kb1_sources f ON f.id = r.source_id
WHERE {REVIEWED_FILE}
  AND (%(category)s::text IS NULL OR r.category = %(category)s)
  AND (r.effective_start IS NULL OR r.effective_start <= make_date(%(year)s, 12, 31))
  AND (r.effective_end IS NULL OR r.effective_end >= make_date(%(year)s, 1, 1))
  AND (strpos(lower(translate(r.certificate_name, '（） ', '() ')), %(name)s) > 0
       OR strpos(lower(r.variant), %(name)s) > 0
       OR lower(r.variant || r.certificate_name) = %(name)s)
ORDER BY r.source_id, r.source_row, r.id
"""


def _certificate_names(db, certificate):
    """分别展开组合中的简称，使「一建+CISP」与目录里的组合名称一致。

    返回：按 + 拆开后逐项经别名表还原、归一、小写化的名称列表；
    names[0] 用作 SQL 召回词，长度 > 1 表示组合查询。注意奖励目录与
    对应表的证书写法可能不同（如「一级建筑师注册证」vs「一级注册
    建筑师」），别名统一指向对应表口径，本工具靠子串宽松匹配兜住。
    """
    parts = [normalize_name(part) for part in normalize_name(certificate).split("+")]
    aliases = db.execute("""
        SELECT alias, certificate_name FROM public.kb1_certificate_aliases WHERE alias = ANY(%s)
    """, (parts,))
    names = {row["alias"]: row["certificate_name"] for row in aliases}
    return [normalize_name(names.get(part, part)).lower() for part in parts]


def _contains_name(text, name):
    """英文证书代码按完整词识别，例如 DCM 与 DCMM 是两张证书。

    ASCII 名称用词边界正则（前后不能紧跟字母/数字/连字符段），防止
    「DCM」误命中「DCMM」；中文无词边界问题，直接子串匹配。
    """
    text = normalize_name(text or "").lower()
    if name.isascii():
        return re.search(r"(?<![a-z0-9-])" + re.escape(name) + r"(?![a-z0-9]|-[a-z0-9])", text) is not None
    return name in text


def _matches_combination(row, names):
    # 证书顺序可互换。每项必须唯一对应，同一个简称不能用两次来凑齐组合。
    if len(set(names)) != len(names):
        return False
    for option in row["combination_alternatives"] or []:
        matched = [[i for i, item in enumerate(option) if _contains_name(item, name)] for name in names]
        if (len(option) == len(names) and all(len(indices) == 1 for indices in matched)
                and len({indices[0] for indices in matched}) == len(option)):
            return True
    return False


def _select_variants(rows, name, variant):
    """每一原表行独立选分项，先看完整名称，再看分项里的专业或代码。

    奖励目录一张原表行常拆成多个专业分项（一建 → 机电/公路/水利）。
    同一 (source_id, source_row) 组内：分项名精确命中优先，其次子串
    命中；员工未指定 variant 或原行本身无分项时整组保留。
    """
    term = normalize_name(variant).lower() if variant else name
    selected = []
    for _, group in groupby(rows, key=itemgetter("source_id", "source_row")):
        entries = list(group)
        exact = [r for r in entries if normalize_name(r["variant"] or "").lower() == term]
        partial = [r for r in entries if _contains_name(r["variant"], term)]
        if exact or partial:
            selected.extend(exact or partial)
        elif not variant or (not entries[0]["variant"] and _contains_name(entries[0]["certificate_name"], term)):
            selected.extend(entries)
    return selected


def _for_year(rows, year):
    """按三个口径判定行对查询年度有效，命中行附加 edition_year 字段。

    口径优先级：① 目录标题「XXXX年版」；② 来源 applicable_year；
    ③ CATALOG_EXTENSIONS 白名单（目录未标次年、但业务已确认沿用的）。
    年度专项的有效期走行内 effective_start/end（SQL 层已过滤）。
    """
    results = []
    for row in rows:
        edition = EDITION_YEAR.search(row["source_title"])
        edition_year = int(edition[1]) if edition else None
        if year in (edition_year, row["applicable_year"]) or year in CATALOG_EXTENSIONS.get(row["source_id"], ()):
            results.append({**row, "edition_year": edition_year})
    return results


def _variant_evidence(rows):
    """保留原表对其他方向的说明与补贴口径，避免套用某一分项的一次性金额。"""
    fields = ("source_id", "source_title", "source_locator", "edition_year", "certificate_name",
              "variant", "name_note", "registration_yuan", "registration_period", "usage_cap_yuan",
              "usage_period", "registration_raw", "usage_raw", "conditions_raw")
    return [{key: row[key] for key in fields} for row in rows if row["variant"] or row["name_note"]]


def create_certificate_reward_tool(pool):
    @tool
    @handle_db_errors
    def lookup_certificate_reward(certificate: Keyword, variant: Keyword | None = None,
                                  year: Year | None = None, category: Category | None = None) -> dict:
        """查公司奖励金额、补贴周期、条件和原表位置。certificate 填一个证书或用 + 连接组合。

        “一建机电”传 certificate=一建、variant=机电。留空 variant 可查看专业分项。
        year 默认今年。明确问年度专项或考试费时可指定 category。组合按整行解释，
        同一证书列出多个档次时须核对分项。金额为空表示目录未列，蓝色名称仅供展示。
        """
        year = year or date.today().year
        with read_snapshot(pool) as db:
            # ① 名称解析：组合拆项 + 别名还原，得到参与匹配的名称列表。
            names = _certificate_names(db, certificate)
            if not all(names):
                return {"status": "invalid_input", "results": []}
            # ② SQL 粗召回：按 names[0] 子串匹配 + 年度/类别过滤。
            rows = db.execute(REWARDS, {"name": names[0], "year": year, "category": category}).fetchall()
            # ③ 年度口径过滤（目录版次 / applicable_year / 沿用白名单）。
            rows = _for_year(rows, year)
            # ④ ASCII 名称防误召：词边界校验替代 SQL 的朴素子串命中。
            if names[0].isascii():
                rows = [r for r in rows if _contains_name(r["certificate_name"], names[0])
                        or _contains_name(r["variant"], names[0])]
            # ⑤ 组合校验：每张证书唯一对应组合内的一个槽位，防单证误召回组合行。
            if len(names) > 1:
                rows = [row for row in rows if _matches_combination(row, names)]

            # ⑥ 分项选择（variant 或整组保留），并按来源查目录级通用规则。
            matches = _select_variants(rows, names[0], variant)
            source_ids = sorted({row["source_id"] for row in matches or rows})
            rules = db.execute("""
                SELECT id AS rule_id, source_id, text, source_locator_json AS source_locator
                FROM public.kb1_shared_rules WHERE source_id = ANY(%s) ORDER BY id
            """, (source_ids,)).fetchall() if source_ids else []
        # 有证书命中但分项不对：返回目录证据（catalog_entries）供模型引导员工，
        # 不返回错误金额；正常路径返回 matches + shared_rules。
        if rows and not matches:
            return {"status": "no_matching_variant", "year": year, "results": [], "shared_rules": rules,
                    "message": "未找到该分项。仅在 name_note 明示适用于其他方向时引用相应补贴。",
                    "catalog_entries": _variant_evidence(rows)}
        return {"status": "found" if matches else "no_verified_match", "year": year,
                "results": matches, "shared_rules": rules}

    return lookup_certificate_reward
