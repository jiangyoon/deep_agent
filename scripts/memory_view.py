"""在终端显示共享记忆。"""

from career_agent import config


def print_memory(text, title):
    """显示记忆全文和字数。字数超过 config.MEMORY_WARN_CHARS 时提醒整理。"""
    print("-" * 60)
    print(title)
    print("-" * 60)

    if text is None:
        print("（还没有记忆）")
        return

    print(text.strip())
    print("")
    print("共", len(text), "字符")

    # 记忆每次请求都会整个放进系统提示，越长越占 token
    if len(text) > config.MEMORY_WARN_CHARS:
        print("提醒：记忆已超过", config.MEMORY_WARN_CHARS, "字符，可用 replace 命令整理")
