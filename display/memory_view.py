# display/memory_view.py
# 作用：在终端里显示记忆的内容，以及一次提问前后记忆有没有变化。

import config


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
        print("提醒：记忆已超过", config.MEMORY_WARN_CHARS, "字符，建议用 manage_memory.py 整理")


def print_memory_change(before, after):
    """对比一次提问前后的记忆，显示有没有变化。"""
    if before == after:
        print("[记忆] 这次提问没有改动记忆")
        return

    print("[记忆] 这次提问更新了记忆，更新后的内容：")
    if after is None:
        print("（记忆已被删除）")
    else:
        print(after.strip())
