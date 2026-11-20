"""文号的全角符号、年份和序号归一化，供在线查询和资料校验共用。

文号形态示例（REF 全覆盖）：
  182号                       仅序号
  京人社12号                   发文单位简称 + 序号
  人通〔2026〕182号            全量：单位 + 〔年份〕 + 序号

该模块同时服务于两条链路：
  - 在线查询（policy_document 工具）——员工问「182号怎么规定」时定位文件；
  - 资料校验（入库脚本）——比对新旧文号是否指向同一文件。
"""

import re
import unicodedata


# 发文单位（汉字）可选 + 两种年份写法（〔YYYY〕/ YYYY年）+ 1~8 位序号。
# 全角〔〕是规范写法；normalize_reference 会把 [ ] ( ) 先归一再进入匹配。
REF = re.compile(r"(?P<issuer>[\u3400-\u9fff]*)(?:〔(?P<year>\d{4})〕|(?P<short_year>\d{4})年)?(?P<serial>\d{1,8})号")


def normalize_reference(value):
    """归一为可比对的标准形态：NFKC、去空白、括号统一为全角〔〕。"""
    value = "".join(unicodedata.normalize("NFKC", value).split())
    return value.translate(str.maketrans({"[": "〔", "]": "〕", "(": "〔", ")": "〕"}))


def parse_reference(value):
    """解析文号为 (发文单位, 年份, 序号)；不符合文号形态时抛 ValueError。

    上层用 ValueError 分流：解析成功走文号比对路径，失败则按标题
    关键词处理（见 policy_document._find_documents）。
    """
    match = REF.fullmatch(normalize_reference(value))
    if not match:
        raise ValueError("请输入文号，例如 182号、人通〔2026〕182号、京人社12号")
    return match["issuer"], match["year"] or match["short_year"], int(match["serial"])


def is_candidate(reference, stored):
    """判断存储文号 stored 是否与用户给出的 reference 指向同一文件。

    比对规则（都按「用户没提供的分量不约束」设计）：
      - 序号必须相等（核心比对项，12号 与 112号 必须区分开）；
      - 年份：双方都有时须相等，用户没写年份则不约束；
      - 发文单位：存储文号的单位须以用户给出的单位开头（支持「京人社」
        匹配「京人社事业发」这类简写）。
    stored 无法解析（非文号文本）时按不匹配处理。
    """
    issuer, year, serial = reference
    try:
        stored_issuer, stored_year, stored_serial = parse_reference(stored)
    except ValueError:
        return False
    return (serial == stored_serial
            and (not year or year == stored_year)
            and (not issuer or stored_issuer.startswith(issuer)))
