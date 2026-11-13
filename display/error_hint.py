# display/error_hint.py
# 作用：程序出错时，打印"看得懂的提示"，而不是一大串报错。
# 几个入口文件都要用，所以单独放在这里。


def print_failure(error, extra_hints=None):
    """打印错误信息和排查清单。

    参数 error      ：捕获到的异常对象。
    参数 extra_hints：可选，额外的排查提示（字符串列表），不同入口可以补充各自的提示。
    """
    print("调用失败，错误类型:", type(error).__name__)
    print("错误信息:", error)
    print("")
    print("请依次检查：")
    print("1. config.py 里的 MODEL_BASE_URL 和 MODEL_NAME 是否已改成真实值")
    print("2. 用 curl.exe 访问 MODEL_BASE_URL/models 是否能通")
    print("3. 模型服务是否开启了工具调用（vLLM 的 --enable-auto-tool-choice）")

    if extra_hints is not None:
        for hint in extra_hints:
            print(hint)
