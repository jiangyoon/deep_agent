# my_tools/time_tool.py
# 作用：一个"查询城市当前时间"的工具，供 Agent 调用。
#
# 要点（阶段 3 的核心）：
#   1. 函数名、注释、参数类型，会被转成"工具说明书"发给模型，模型靠它决定要不要用、怎么传参
#   2. 工具内部不要抛异常！工具里一旦抛出异常，整个 Agent 会直接崩溃，模型根本看不到错误。
#      遇到问题，应该返回一段说明文字，让模型知道发生了什么

from datetime import datetime
from zoneinfo import ZoneInfo

from langchain_core.tools import tool

# 城市名 -> 时区名。想支持更多城市，往这里加一行就行
CITY_TIMEZONES = {
    "北京": "Asia/Shanghai",
    "上海": "Asia/Shanghai",
    "东京": "Asia/Tokyo",
    "纽约": "America/New_York",
    "伦敦": "Europe/London",
    "巴黎": "Europe/Paris",
    "洛杉矶": "America/Los_Angeles",
}


def format_offset(now):
    """把时区偏移格式化成 UTC+08:00 的样子。"""
    raw = now.strftime("%z")  # 例如 +0800
    sign = raw[0]
    hours = raw[1:3]
    minutes = raw[3:5]
    return "UTC" + sign + hours + ":" + minutes


# @tool(parse_docstring=True) 的作用：
#   把下面注释里 Args: 部分的说明，拆出来作为"每个参数各自的说明"发给模型。
#   参数说明越清楚，模型越不容易传错。
@tool(parse_docstring=True)
def get_current_time(city: str) -> str:
    """查询某个城市的当前日期和时间。

    Args:
        city: 城市名称（中文），例如"北京"、"东京"。
    """
    # 去掉空格，以及模型可能多带的"市"字（比如"北京市"）
    city = city.strip()
    if city.endswith("市"):
        city = city[:-1]

    # 不认识的城市：返回提示文字，并告诉模型目前支持哪些
    if city not in CITY_TIMEZONES:
        supported = "、".join(CITY_TIMEZONES.keys())
        return "不支持的城市：" + city + "。目前支持：" + supported

    # 读取时区数据。Windows 系统自带没有时区数据，需要安装 tzdata 包，
    # 没装的话这里会失败，所以用 try 接住，返回提示而不是让程序崩溃
    try:
        zone = ZoneInfo(CITY_TIMEZONES[city])
    except Exception:
        return "无法读取时区数据，请在运行环境里执行 pip install tzdata 后重试。"

    now = datetime.now(zone)
    text = now.strftime("%Y-%m-%d %H:%M:%S")
    return city + "当前时间：" + text + "（" + format_offset(now) + "）"
