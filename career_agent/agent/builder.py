"""组装职业规划 Agent 及其工具、Skill、记忆和会话。"""

from typing import TypedDict

from deepagents import create_deep_agent

from career_agent import config
from career_agent.core.backend_builder import SKILLS_ROUTE, build_backend, build_permissions
from career_agent.core.memory_store import MEMORY_FILE_PATH


SYSTEM_PROMPT = (
    "你是面向员工的职业规划助手，当前重点是证书与职称咨询。"
    "用中文回答，并按需要确认地区、申报渠道、年度和员工条件。"
    "涉及资格、奖励、期限及报销等具体政策时，须依据可核对的资料。"
    "资料无法核实时，说明哪些内容暂时无法确认。"
    "需要读取或保存用户确认的记忆时，使用文件工具。"
)


class AgentContext(TypedDict):
    user_id: str


def build_agent(llm, tools=None, use_skills=None, use_memory=False, store=None,
                checkpointer=None, require_user=False):
    """使用调用方提供的业务工具。Web 通过用户上下文隔离记忆。"""
    if use_skills is None:
        use_skills = config.USE_SKILLS

    if use_memory and store is None:
        raise ValueError(
            "启用记忆时必须传入由 open_store() 打开的记忆数据库"
        )

    # 都不启用时，这几项都是 None，框架会用默认值
    backend = None
    skills = None
    permissions = None
    memory = None

    if use_skills or use_memory:
        backend = build_backend(use_skills=use_skills, use_memory=use_memory,
                                require_user=require_user)

    if use_skills:
        skills = [SKILLS_ROUTE]
        permissions = build_permissions()

    if use_memory:
        # 告诉框架：每次运行开始时，把这个文件的内容放进系统提示
        memory = [MEMORY_FILE_PATH]

    agent = create_deep_agent(
        model=llm,
        tools=tools,
        system_prompt=SYSTEM_PROMPT,
        backend=backend,
        skills=skills,
        permissions=permissions,
        memory=memory,
        store=store,
        checkpointer=checkpointer,
        context_schema=AgentContext if require_user else None,
    )
    return agent
