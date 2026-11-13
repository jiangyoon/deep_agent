# stage2_invoke.py
# 作用：阶段 2 入口（invoke 版）。创建模型和 Agent，依次问两个问题，并打印整个过程。
# 特点：用 invoke，等 Agent 全部做完才一次性返回结果。
# 运行方式：在 deep_agent 文件夹下执行  python stage2_invoke.py

import questions
from core.llm_model import create_llm
from core.agent_builder import build_agent
from display.printer import print_messages, print_files
from display.error_hint import print_failure


def ask(agent, question):
    """向 Agent 提一个问题，并打印整个过程。"""
    print("=" * 60)
    print("问题:", question)
    print("=" * 60)

    # invoke 会让 Agent 一直运行到它认为完成为止，然后一次性返回结果。
    # 传入的是"消息列表"，这里只有一条用户消息。
    result = agent.invoke(
        {"messages": [{"role": "user", "content": question}]}
    )

    print_messages(result["messages"])
    print_files(result.get("files"))
    print("")
    return result


def main():
    # 外面包一层 try，是为了在出错时给出看得懂的提示，而不是一大串报错。
    try:
        # 第一步：创建模型连接
        llm = create_llm()

        # 第二步：用模型创建 Agent
        agent = build_agent(llm)

        # 第三步：依次提问
        ask(agent, questions.QUESTION_1)
        ask(agent, questions.QUESTION_2)
    except Exception as error:
        print_failure(error)


if __name__ == "__main__":
    main()
