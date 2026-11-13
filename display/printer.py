# display/printer.py
# 作用：把 Agent 一次运行中产生的所有消息，按顺序整齐地打印出来。
# 学习阶段最重要的事，就是看清楚 Agent 每一步"收到了什么、说了什么、调用了什么工具"。

# 消息类型 -> 中文名称
TYPE_NAMES = {
    "human": "用户",
    "ai": "模型",
    "tool": "工具返回",
    "system": "系统",
}

# 工具返回的内容可能很长，只显示前面这么多字符
TOOL_TEXT_LIMIT = 300


def content_to_text(content):
    """把消息内容统一转成字符串。

    消息的 content 有时是字符串，有时是"分段列表"，这里统一处理。
    """
    if isinstance(content, str):
        return content

    text = ""
    for part in content:
        if isinstance(part, dict):
            text = text + str(part.get("text", ""))
        else:
            text = text + str(part)
    return text


def print_one_message(index, message):
    """打印单条消息。"""
    kind = message.type
    name = TYPE_NAMES.get(kind, kind)
    print("--- 第 " + str(index) + " 条 [" + name + "] ---")

    text = content_to_text(message.content)

    # 模型消息：如果服务端返回了思考内容，先打印它
    if kind == "ai":
        reasoning = message.additional_kwargs.get("reasoning_content", "")
        if reasoning != "":
            print("思考:", reasoning.strip())

    # 工具返回的内容过长时截断
    if kind == "tool":
        print("工具名:", message.name)
        if len(text) > TOOL_TEXT_LIMIT:
            text = text[:TOOL_TEXT_LIMIT] + "...(已截断)"

    if text != "":
        print("内容:", text)

    # 模型消息：如果它决定调用工具，会带 tool_calls
    if kind == "ai":
        for call in message.tool_calls:
            print("要调用的工具:", call["name"])
            print("  参数:", call["args"])

        # token 用量（服务端返回了才有）
        usage = message.usage_metadata
        if usage is not None:
            print(
                "token 用量: 输入",
                usage.get("input_tokens"),
                "输出",
                usage.get("output_tokens"),
            )


def print_messages(messages):
    """按顺序打印全部消息，并统计模型被调用了几次。"""
    index = 1
    model_calls = 0

    for message in messages:
        print_one_message(index, message)
        if message.type == "ai":
            model_calls = model_calls + 1
        index = index + 1

    print("")
    print("共", len(messages), "条消息，其中模型回复", model_calls, "次")


def print_files(files):
    """打印 Agent 在虚拟文件系统里创建的文件。

    注意：这些文件只存在于内存里（默认的 StateBackend），
    不会写到你电脑的硬盘上。
    """
    if not files:
        print("本次运行没有创建任何文件")
        return

    print("虚拟文件系统里的文件:")
    for path in files:
        print("  ", path)
