# stage3_tools.py
# 作用：阶段 3 入口，测试 Agent 调用自定义工具（my_tools 文件夹里登记的工具）。
# 打印方式复用流式输出，所以你会看到每一步：
# 思考、决定调用哪个工具、传了什么参数、工具返回什么、最终回答。
# 运行方式：在 deep_agent 文件夹下执行  python stage3_tools.py

import questions
from core.llm_model import create_llm
from core.agent_builder import build_agent
from display.stream_runner import ask_stream
from display.error_hint import print_failure


def main():
    try:
        llm = create_llm()

        # build_agent 默认带上 my_tools 里登记的全部工具
        agent = build_agent(llm)

        # 每个问题单独提问，互相之间没有记忆（记忆是阶段 5 的内容）
        for question in questions.STAGE3_QUESTIONS:
            ask_stream(agent, question)
    except Exception as error:
        print_failure(
            error,
            ["4. 先确认 python stage2_stream.py 能正常运行，再排查工具相关的问题"],
        )


if __name__ == "__main__":
    main()
