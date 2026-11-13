# stage5_memory.py
# 作用：阶段 5 入口，测试 Agent 的记忆。
# 每个问题都是一次独立的运行（互相之间没有对话历史），
# 所以如果后面的问题能答出前面说过的事，靠的就是记忆。
# 运行方式：在 deep_agent 文件夹下执行  python stage5_memory.py
# 想对比"没有记忆"的效果：把 config.py 里的 USE_MEMORY 改成 False 再运行。

import config
import questions
from core.llm_model import create_llm
from core.agent_builder import build_agent
from core.memory_store import open_store, read_memory, seed_memory_if_missing
from display.stream_runner import ask_stream
from display.memory_view import print_memory, print_memory_change
from display.error_hint import print_failure


def main():
    try:
        # 打开记忆数据库，整个运行期间保持打开，with 块结束时自动关闭
        with open_store() as store:
            created = seed_memory_if_missing(store)
            if created:
                print("首次运行：已创建空的记忆模板")

            print_memory(read_memory(store), "运行前的记忆")
            print("")

            llm = create_llm()

            # 这里关掉了 Skill（use_skills=False），原因：
            # career-kb-qa 这个 Skill 规定"职业问题只能依据知识库回答"，
            # 会和"从记忆里回答我的职业方向"冲突，干扰对记忆本身的观察
            agent = build_agent(
                llm,
                use_skills=False,
                use_memory=config.USE_MEMORY,
                store=store,
            )

            if config.USE_MEMORY:
                print("记忆已启用，数据库:", config.MEMORY_DB_PATH)
            else:
                print("记忆未启用（config.py 里 USE_MEMORY = False）")
            print("")

            for question in questions.STAGE5_QUESTIONS:
                before = read_memory(store)
                ask_stream(agent, question)
                after = read_memory(store)
                print_memory_change(before, after)
                print("")
    except Exception as error:
        print_failure(
            error,
            ["4. 先确认 python stage4_skills.py 能正常运行，再排查记忆相关的问题"],
        )


if __name__ == "__main__":
    main()
