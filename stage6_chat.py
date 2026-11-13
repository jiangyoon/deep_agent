# stage6_chat.py
# 作用：阶段 6 入口，交互式的多轮对话，会话会保存到 data/sessions.db。
# 和前面的入口不同，这里不是固定的问题列表，而是你自己一轮一轮地输入。
# 关掉程序再打开，输入同一个会话编号，就能接着上次聊。
#
# 对话中可用的命令：
#   /new           开一个新会话
#   /list          列出所有会话
#   /switch 编号   切换到某个已有的会话
#   /quit          退出
#
# 运行方式：在 deep_agent 文件夹下执行  python stage6_chat.py

from core.llm_model import create_llm
from core.agent_builder import build_agent
from core.memory_store import open_store, read_memory, seed_memory_if_missing
from core.session_store import (
    get_session_messages,
    list_sessions,
    make_config,
    new_session_id,
    open_checkpointer,
    session_exists,
)
from display.stream_runner import ask_stream
from display.memory_view import print_memory_change
from display.error_hint import print_failure

HELP_TEXT = (
    "命令：/new 开新会话，/list 列出会话，/switch 编号 切换会话，/quit 退出\n"
    "其他输入都会当作问题发给 Agent。"
)


def show_sessions(saver):
    """列出所有会话编号。"""
    session_ids = list_sessions(saver)
    if len(session_ids) == 0:
        print("（还没有任何会话）")
        return
    print("已有的会话（最近的在前）:")
    for session_id in session_ids:
        print("  ", session_id)


def choose_session(saver):
    """启动时选择会话：直接回车开新会话，输入编号继续旧会话。"""
    show_sessions(saver)
    answer = input("输入会话编号继续旧会话，直接回车开新会话：").strip()
    if answer == "":
        return new_session_id()
    if not session_exists(saver, answer):
        print("没有这个会话，已为你开新会话")
        return new_session_id()
    return answer


def show_session_size(agent, session_id):
    """显示当前会话一共有多少条消息。"""
    messages = get_session_messages(agent, session_id)
    print("[会话] 当前会话", session_id, "共", len(messages), "条消息")


def main():
    try:
        # 同时打开两个数据库：记忆（阶段 5）和会话（阶段 6）
        with open_store() as store, open_checkpointer() as saver:
            seed_memory_if_missing(store)

            llm = create_llm()

            # 关掉 Skill 的原因和阶段 5 一样：避免职业知识库的规则干扰对记忆的观察
            agent = build_agent(
                llm,
                use_skills=False,
                use_memory=True,
                store=store,
                checkpointer=saver,
            )

            print(HELP_TEXT)
            print("")
            session_id = choose_session(saver)
            print("当前会话:", session_id)
            print("")

            while True:
                # input 在等你输入；按 Ctrl+C 或关闭输入时，会抛出异常，这里当作退出处理
                try:
                    text = input("你> ").strip()
                except (EOFError, KeyboardInterrupt):
                    print("")
                    break

                if text == "":
                    continue

                if text == "/quit":
                    break

                if text == "/new":
                    session_id = new_session_id()
                    print("已开新会话:", session_id)
                    continue

                if text == "/list":
                    show_sessions(saver)
                    continue

                if text.startswith("/switch "):
                    target = text[len("/switch "):].strip()
                    if session_exists(saver, target):
                        session_id = target
                        print("已切换到会话:", session_id)
                    else:
                        print("没有这个会话:", target)
                    continue

                # 普通输入：带上会话编号提问，框架会自动读出这个会话的历史，并在结束后保存
                memory_before = read_memory(store)
                ask_stream(agent, text, config=make_config(session_id))
                memory_after = read_memory(store)

                # 记忆有变化时才提示，避免每轮都刷屏
                if memory_before != memory_after:
                    print_memory_change(memory_before, memory_after)
                show_session_size(agent, session_id)
                print("")

            print("已退出。下次运行时输入会话编号", session_id, "可以接着聊。")
    except Exception as error:
        print_failure(
            error,
            ["4. 先确认 python stage5_memory.py 能正常运行，再排查会话相关的问题"],
        )


if __name__ == "__main__":
    main()
