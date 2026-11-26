"""查询证书与职称对应关系及原件依据。

数据来源（PostgreSQL）：
  kb1_mappings                证书 → 职称对应行（一行一个证书，含条件与原文依据）
  kb1_certificate_aliases     员工常用简称 → 目录规范名（一建 → 一级建造师）
  kb1_sources                 文件来源与时效（仅取已审核且现行有效的行）

设计要点——双来源分流：同一张对应表在库中有两份口径不同的载体，
SCOPE_SOURCES 记录了这一映射，查询时按渠道选择，不互斥、不合并：
  - N017/N025 挂「北京人社」：带"本市"学历年限等北京口径限定
  - N035     挂「集团内部」：集团高工通知（N044）实际挂载的附件，不带地区限定
渠道未指明时全部返回，由模型在答案里分别标注两份依据。
"""

import logging
import re
import unicodedata
from typing import Annotated, Literal

import psycopg
from langchain_core.tools import tool
from pydantic import Field


logger = logging.getLogger(__name__)

# 员工口中的查询渠道。scope 是「申报渠道」而非证书类型——持有一建不能
# 推断员工走哪条申报渠道，所以工具不猜测、渠道必须由员工或模型确认。
Scope = Literal["北京人社", "集团内部"]
# 双来源分流表：scope → 对应表所在的来源文件 ID（见模块 docstring）。
SCOPE_SOURCES = {"北京人社": ["N017", "N025"], "集团内部": ["N035"]}
# 反向索引：来源 ID → scope，用于给每条命中结果标注渠道。
SOURCE_SCOPES = {
    source: scope for scope, sources in SCOPE_SOURCES.items() for source in sources
}
# 历史入库时未归一的证书名 → 现行规范名。只有对应表写法与规范名不一致、
# 且别名表覆盖不到的少数证书需要在这里兜底（如 CFA 的全称写法）。
CERTIFICATE_NAMES = {
    "CFA": "CFA特许金融分析师证书",
    "FIA/FFA": "FIA/FFA英国正精算师",
    "咨询工程师(投资)": "工程咨询(投资)专业技术人员职业资格",
}
# 部分历史记录把「(2019年以后取得)」这类条件放在证书名末尾。
ACQUISITION_SUFFIX = r"\(\d{4}[^()]*取得\)$"


def normalize_name(value):
    """证书名的展示归一：全角/半角、连排空格、CJK 字间空格全部消除。

    NFKC 统一全角数字/括号等形似字符；先压缩空白再去掉汉字之间的空格，
    使「一级 建造师」与「一级建造师」归一为同一写法。仅用于匹配前的
    预处理，不改变库中存储内容。
    """
    value = " ".join(unicodedata.normalize("NFKC", value).split())
    return re.sub(r"(?<=[\u3400-\u9fff]) (?=[\u3400-\u9fff])", "", value)


# 匹配策略（由松到严三层，命中任一即返回）：
#   1. certificate_name / certificate_family 等值——规范名精确匹配
#   2. 剥掉名称末尾「(XXXX年以后取得)」后缀再等值——历史记录的限定条件
#      写进了名称列，剥掉后才能与员工问法对上
MAPPING_QUERY = r"""
SELECT m.id AS mapping_id, m.certificate_name, m.kind AS mapping_kind,
       m.target_title_raw AS target_title,
       m.conditions_raw AS conditions, m.common_note_raw AS common_note,
       m.policy_basis_raw AS policy_basis, m.source_id, f.title AS source_title,
       f.region, m.source_locator_json AS source_locator
FROM public.kb1_mappings m
JOIN public.kb1_sources f ON f.id = m.source_id
WHERE m.record_status = 'review_ready'
  AND f.status = 'review_dataset'
  AND f.validity_status = '现行有效'
  AND m.source_id = ANY(%(source_ids)s::text[])
  AND (
      m.certificate_name = ANY(%(certificate_names)s::text[])
      OR m.certificate_family = ANY(%(certificate_names)s::text[])
      OR regexp_replace(m.certificate_name, %(acquisition_suffix)s, '') =
          ANY(%(certificate_names)s::text[])
  )
ORDER BY m.source_id, m.serial_no, m.id
"""


def create_certificate_mapping_tool(pool):
    @tool
    def lookup_certificate_mapping(
        certificate: Annotated[str, Field(min_length=1, max_length=200, pattern=r"^[^\x00]+$")],
        scope: Scope | None = None,
    ) -> dict:
        """按证书或证书简称查询对应职称、适用条件和原件出处。

        certificate 传一个证书简称或标准名，例如一建、软考高级。
        scope 仅在员工指定一个查询渠道时填写。比较渠道或渠道未知时留空，
        结果会标明各自渠道。对应关系可作为核对申报条件的依据之一。
        """
        # 名称归一（全角/空格），空输入直接拒绝。
        name = normalize_name(certificate)
        if not name:
            return {"status": "invalid_input", "message": "请输入证书名称或简称。", "matches": []}
        # 渠道未指定时取全部来源；每条命中结果用 SOURCE_SCOPES 回标渠道。
        sources = SCOPE_SOURCES[scope] if scope else list(SOURCE_SCOPES)
        try:
            with pool.connection() as db, db.transaction():
                db.execute("SET TRANSACTION READ ONLY")
                db.execute("SET LOCAL statement_timeout = '5s'")
                # 别名展开链：员工写法 → 别名表规范名 → 少数历史写法兜底，
                # 三种写法去重后一起参与匹配，避免「别名命中但规范名不中」。
                alias = db.execute(
                    "SELECT certificate_name FROM public.kb1_certificate_aliases WHERE alias = %s",
                    (name,),
                ).fetchone()
                alias_target = normalize_name(alias["certificate_name"]) if alias else name
                canonical = CERTIFICATE_NAMES.get(alias_target, alias_target)
                names = list(dict.fromkeys((name, alias_target, canonical)))
                matches = db.execute(
                    MAPPING_QUERY,
                    {
                        "source_ids": sources,
                        "certificate_names": names,
                        "acquisition_suffix": ACQUISITION_SUFFIX,
                    },
                ).fetchall()
        except psycopg.Error as error:
            logger.warning("查询证书与职称对应失败（%s）", type(error).__name__)
            return {"status": "unavailable", "message": "证书对应查询暂不可用。", "matches": []}

        result = {
            "status": "found" if matches else "no_verified_match",
            "certificate": canonical,
            "matches": [{"scope": SOURCE_SCOPES[row["source_id"]], **row} for row in matches],
        }
        # 渠道未知时的下一步引导。模型对工具结果中的指令服从率远高于技能长文；
        # 若此轮在问本人申报条件而渠道未定，务必让模型走 ask_user 工具，
        # 而不是在正文里列选项文字反问（实测模型的常见偏差）。
        if matches and scope is None:
            result["next_step_hint"] = (
                "如果员工在问本人凭此证书申报职称的适用条件，而会话中尚未明确申报渠道，"
                "下一步必须调用 ask_user 工具请员工选择渠道，等回答后再继续查询。"
                "选项：集团内部、公司内部、北京人社、其他地方人社、还不确定"
                "（其他地方人社的提交值为「地方人社」）。不要在正文里用文字列选项反问。"
            )
        return result

    return lookup_certificate_mapping
