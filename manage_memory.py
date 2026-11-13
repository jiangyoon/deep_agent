# manage_memory.py
# 作用：记忆管理工具，直接查看、清空、替换数据库里的记忆，不经过 Agent。
# 用法（在 deep_agent 文件夹下执行）：
#   python manage_memory.py show              查看记忆
#   python manage_memory.py clear             清空记忆（会要求确认）
#   python manage_memory.py replace 文件路径   用一个文本文件的内容整体替换记忆
# 想手工修改记忆：先 show 把内容复制到一个文本文件里，改好后再 replace

import os
import sys

from core.memory_store import (
    clear_memory,
    get_db_path,
    open_store,
    read_memory,
    write_memory,
)
from display.memory_view import print_memory

USAGE = (
    "用法：\n"
    "  python manage_memory.py show              查看记忆\n"
    "  python manage_memory.py clear             清空记忆（会要求确认）\n"
    "  python manage_memory.py replace 文件路径   用一个文本文件的内容整体替换记忆\n"
)


def cmd_show(store):
    print("数据库文件:", get_db_path())
    print_memory(read_memory(store), "当前记忆")


def cmd_clear(store):
    answer = input("确认清空全部记忆？输入 yes 确认：")
    if answer.strip() != "yes":
        print("已取消")
        return
    clear_memory(store)
    print("已清空。下次运行 stage5_memory.py 时会重新创建空模板。")


def cmd_replace(store, file_path):
    if not os.path.isfile(file_path):
        print("找不到文件:", file_path)
        return
    with open(file_path, encoding="utf-8") as f:
        text = f.read()
    write_memory(store, text)
    print("已替换，共", len(text), "字符")


def main():
    if len(sys.argv) < 2:
        print(USAGE)
        return

    command = sys.argv[1]

    with open_store() as store:
        if command == "show":
            cmd_show(store)
        elif command == "clear":
            cmd_clear(store)
        elif command == "replace":
            if len(sys.argv) < 3:
                print(USAGE)
                return
            cmd_replace(store, sys.argv[2])
        else:
            print("不认识的命令:", command)
            print(USAGE)


if __name__ == "__main__":
    main()
