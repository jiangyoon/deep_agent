# my_tools/math_tool.py
# 作用：一个"除法"工具。
# 为什么要做这个：大模型不擅长精确的数字运算，算数这类事交给工具更可靠。
#
# 要点：除数为 0 时，Python 默认会抛出 ZeroDivisionError，
# 而工具里抛出异常会让整个 Agent 崩溃。
# 所以这里先自己判断，返回一段错误说明文字，让模型能看到并向用户解释。

from langchain_core.tools import tool


@tool(parse_docstring=True)
def divide(a: float, b: float) -> str:
    """计算 a 除以 b 的结果。

    Args:
        a: 被除数。
        b: 除数，不能为 0。
    """
    # 先检查除数，避免触发 ZeroDivisionError
    if b == 0:
        return "错误：除数不能为 0，无法计算。"

    result = a / b

    # 工具的返回值最终会变成文字消息发给模型，这里直接转成字符串，意思更明确
    return str(result)
