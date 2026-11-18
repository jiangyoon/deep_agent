"""提取消息文本，并为会话管理命令提供终端输出。"""

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
