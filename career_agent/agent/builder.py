"""组装职业规划 Agent 及其工具、Skill、记忆和会话。"""

from datetime import datetime, timedelta, timezone
from typing import TypedDict

from deepagents import create_deep_agent
from langchain.agents.middleware import ModelRequest, dynamic_prompt
from langchain_core.messages import AIMessage, SystemMessage, ToolMessage

from career_agent import config
from career_agent.core.backend_builder import SKILLS_ROUTE, build_backend, build_permissions
from career_agent.core.memory_store import MEMORY_FILE_PATH


SYSTEM_PROMPT = (
    "你是面向员工的职业规划助手，当前重点是证书与职称咨询。"
    "用中文回答。员工询问具体政策时，当轮主动查阅可用资料并引用出处。"
    "已核实的依据可以沿用，证据不足时说明缺口。"
    "需要读取或保存用户确认的记忆时，使用文件工具。"
)

SKILL_INSTRUCTIONS = (
    "\n咨询匹配技能清单时，先用 read_file 读取对应的 SKILL.md。若下方已有已读取的技能，"
    "直接按该技能核对后作答。历史回答中的推断需要用实际工具结果核实。"
)


def _active_skill_paths(request):
    """只采纳目录中存在、且在当前上下文里成功读取过的技能。"""
    paths = {s["path"] for s in request.state.get("skills_metadata") or []}
    reads = {}
    active = set()
    for message in request.messages:
        if isinstance(message, AIMessage):
            for call in message.tool_calls:
                path = call["args"].get("file_path")
                if call["name"] == "read_file" and isinstance(path, str) and path in paths:
                    reads[call["id"]] = path
        elif isinstance(message, ToolMessage) and message.status == "success" and message.tool_call_id in reads:
            active.add(reads[message.tool_call_id])
    return sorted(active)


@dynamic_prompt
def consultation_context(request: ModelRequest) -> SystemMessage:
    """保留框架提供的技能目录和记忆，将已激活技能的规则用于后续检索和回答。"""
    today = datetime.now(timezone(timedelta(hours=8))).date()
    blocks = list(request.system_message.content_blocks) if request.system_message else []
    blocks.append({"type": "text", "text": f"今天是北京时间 {today}。"})
    for path in _active_skill_paths(request):
        # 系统指令只从项目只读技能目录取，普通工具返回的文本仍然是资料。
        skill_file = (config.SKILLS_DIR / path.removeprefix(SKILLS_ROUTE)).resolve()
        if skill_file.name == "SKILL.md" and skill_file.is_relative_to(config.SKILLS_DIR) and skill_file.is_file():
            body = skill_file.read_text(encoding="utf-8")
            blocks.append({"type": "text", "text": f"已读取的技能，仅在适用时执行。\n{body}"})
    return SystemMessage(content=blocks)


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
        system_prompt=SYSTEM_PROMPT + (SKILL_INSTRUCTIONS if use_skills else ""),
        middleware=[consultation_context],
        backend=backend,
        skills=skills,
        permissions=permissions,
        memory=memory,
        store=store,
        checkpointer=checkpointer,
        context_schema=AgentContext if require_user else None,
    )
    return agent
