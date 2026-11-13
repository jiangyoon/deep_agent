# core/agent_builder.py
# 作用：把"模型"、"系统提示词"、"工具"、"Skill"、"记忆"、"会话保存"组装成一个 Deep Agent。

from deepagents import create_deep_agent

import config
from my_tools import ALL_TOOLS
from core.backend_builder import SKILLS_ROUTE, build_backend, build_permissions
from core.memory_store import MEMORY_FILE_PATH


def build_agent(llm, tools=None, use_skills=None, use_memory=False, store=None, checkpointer=None):
    """创建 Agent。

    参数 llm       ：模型连接对象，由 core/llm_model.py 的 create_llm() 生成。
    参数 tools     ：自定义工具列表。不传就使用 my_tools 里登记的全部工具；
                     想要一个不带任何自定义工具的 Agent，传空列表 [] 即可。
    参数 use_skills：是否启用 Skill。不传就按 config.py 里的 USE_SKILLS 决定。
    参数 use_memory：是否启用记忆。默认不启用，所以阶段 2 到 4 的入口不受影响。
    参数 store     ：记忆数据库，由 core/memory_store.py 的 open_store() 打开。
                     启用记忆时必须传。
    参数 checkpointer：会话保存器，由 core/session_store.py 的 open_checkpointer() 打开。
                     不传就不保存会话，每次提问都是独立的，和阶段 2 到 5 一样。
    """
    if tools is None:
        tools = ALL_TOOLS

    if use_skills is None:
        use_skills = config.USE_SKILLS

    if use_memory and store is None:
        raise ValueError(
            "启用记忆时必须传入 store（记忆数据库），请先用 core.memory_store.open_store() 打开"
        )

    # 都不启用时，这几项都是 None，框架会用默认值
    backend = None
    skills = None
    permissions = None
    memory = None

    if use_skills or use_memory:
        backend = build_backend(use_skills=use_skills, use_memory=use_memory)

    if use_skills:
        skills = [SKILLS_ROUTE]
        permissions = build_permissions()

    if use_memory:
        # 告诉框架：每次运行开始时，把这个文件的内容放进系统提示
        memory = [MEMORY_FILE_PATH]

    agent = create_deep_agent(
        model=llm,
        tools=tools,
        system_prompt=config.SYSTEM_PROMPT,
        backend=backend,
        skills=skills,
        permissions=permissions,
        memory=memory,
        store=store,
        checkpointer=checkpointer,
    )
    return agent
