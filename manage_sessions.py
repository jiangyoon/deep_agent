# manage_sessions.py
# 作用：会话管理工具，直接查看、删除数据库里的会话，不需要和 Agent 对话。
# 用法（在 deep_agent 文件夹下执行）：
#   python manage_sessions.py list              列出所有会话和消息条数
#   python manage_sessions.py show 会话编号      查看一个会话的全部消息
#   python manage_sessions.py delete 会话编号    删除一个会话（会要求确认）

import sys

from core.llm_model import create_llm
from core.agent_builder import build_agent
from core.session_store import (
    delete_session,
    get_session_db_path,
    get_session_messages,
    list_sessions,
    open_checkpointer,
    session_exists,
)
from display.printer import print_messages

USAGE = (
    "用法：\n"
    "  python manage_sessions.py list              列出所有会话和消息条数\n"
    "  python manage_sessions.py show 会话编号      查看一个会话的全部消息\n"
    "  python manage_sessions.py delete 会话编号    删除一个会话（会要求确认）\n"
)


def build_reader(saver):
    """创建一个只用来"读会话"的 Agent。

    读会话要用 agent.get_state()，所以需要一个带保存器的 Agent。
    这里只是创建对象，不会向模型发任何请求。
    """
    llm = create_llm()
    return build_agent(llm, use_skills=False, checkpointer=saver)


def cmd_list(saver):
    print("数据库文件:", get_session_db_path())
    session_ids = list_sessions(saver)
    if len(session_ids) == 0:
        print("（还没有任何会话）")
        return
    reader = build_reader(saver)
    print("会话（最近的在前）:")
    for session_id in session_ids:
        messages = get_session_messages(reader, session_id)
        print("  ", session_id, "  ", len(messages), "条消息")


def cmd_show(saver, session_id):
    if not session_exists(saver, session_id):
        print("没有这个会话:", session_id)
        return
    reader = build_reader(saver)
    messages = get_session_messages(reader, session_id)
    print_messages(messages)


def cmd_delete(saver, session_id):
    if not session_exists(saver, session_id):
        print("没有这个会话:", session_id)
        return
    answer = input("确认删除会话 " + session_id + "？输入 yes 确认：")
    if answer.strip() != "yes":
        print("已取消")
        return
    delete_session(saver, session_id)
    print("已删除")


def main():
    if len(sys.argv) < 2:
        print(USAGE)
        return

    command = sys.argv[1]

    with open_checkpointer() as saver:
        if command == "list":
            cmd_list(saver)
        elif command == "show" or command == "delete":
            if len(sys.argv) < 3:
                print(USAGE)
                return
            if command == "show":
                cmd_show(saver, sys.argv[2])
            else:
                cmd_delete(saver, sys.argv[2])
        else:
            print("不认识的命令:", command)
            print(USAGE)


if __name__ == "__main__":
    main()
