# stage2_stream.py
# 作用：阶段 2 入口（流式版）。
# 和 stage2_invoke.py 的区别：
#   stage2_invoke.py 用 invoke，等全部做完才一次性显示；
#   这里用 stream，做到哪一步就显示哪一步，思考和回答都逐字显示。
# 运行方式：在 deep_agent 文件夹下执行  python stage2_stream.py

import questions
from core.llm_model import create_llm
from core.agent_builder import build_agent
from display.stream_runner import ask_stream
from display.error_hint import print_failure


def main():
    try:
        llm = create_llm()
        agent = build_agent(llm)

        ask_stream(agent, questions.QUESTION_1)
        ask_stream(agent, questions.QUESTION_2)
    except Exception as error:
        print_failure(
            error,
            [
                "4. 先确认 python stage2_invoke.py 能正常运行，再排查流式问题",
                "5. 如果只有流式失败，常见原因是模型服务不支持流式返回",
            ],
        )


if __name__ == "__main__":
    main()
