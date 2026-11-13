# stage4_skills.py
# 作用：阶段 4 入口，测试 Agent 使用预置的 Skill（只读）。
# 你会看到：模型一开始只知道 Skill 的名字和简介，需要时才去读 SKILL.md 的全文，
# 步骤里会标注"读取 Skill 文件"。
# 运行方式：在 deep_agent 文件夹下执行  python stage4_skills.py
# 想对比"没有 Skill"的效果：把 config.py 里的 USE_SKILLS 改成 False 再运行。

import config
import questions
from core.llm_model import create_llm
from core.agent_builder import build_agent
from display.stream_runner import ask_stream
from display.error_hint import print_failure


def main():
    try:
        llm = create_llm()

        # build_agent 会按 config.USE_SKILLS 决定是否启用 Skill
        agent = build_agent(llm)

        if config.USE_SKILLS:
            print("Skill 已启用（只读），文件夹:", config.SKILLS_DIR)
        else:
            print("Skill 未启用（config.py 里 USE_SKILLS = False）")
        print("")

        for question in questions.STAGE4_QUESTIONS:
            ask_stream(agent, question)
    except Exception as error:
        print_failure(
            error,
            ["4. 先确认 python stage3_tools.py 能正常运行，再排查 Skill 相关的问题"],
        )


if __name__ == "__main__":
    main()
