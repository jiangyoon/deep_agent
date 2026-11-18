# core/backend_builder.py
# 作用：为 Agent 准备"文件系统"。
#
# 背景：Agent 默认的文件系统在内存里，程序结束就没了。这里用组合文件系统，按路径分流：
#   /skills/   -> 硬盘上的 skills 文件夹（只读，见 build_permissions）
#   /memories/ -> 记忆数据库（可读可写，程序重启后还在）
#   其他路径   -> 仍然在内存里，和之前一样

from functools import partial

from deepagents.backends import (
    CompositeBackend,
    FilesystemBackend,
    StateBackend,
    StoreBackend,
)
from deepagents.middleware.filesystem import FilesystemPermission

from career_agent import config
from career_agent.core.memory_store import MEMORY_ROUTE, get_memory_namespace

# Agent 眼里的 Skill 路径。注意结尾的斜杠
SKILLS_ROUTE = "/skills/"


def memory_namespace(runtime, require_user=False):
    """告诉 StoreBackend：记忆存在数据库的哪个分组里。

    Web 身份只由后端放入 runtime.context；缺失时禁止回退到终端共享分组。
    """
    context = getattr(runtime, "context", None)
    user_id = context.get("user_id") if isinstance(context, dict) else None
    if require_user and not user_id:
        raise ValueError("Web 记忆访问缺少用户身份")
    return get_memory_namespace(user_id)


def build_backend(use_skills=True, use_memory=False, require_user=False):
    """创建组合文件系统。

    参数 use_skills：是否把 /skills/ 指向 skills 文件夹
    参数 use_memory：是否把 /memories/ 指向记忆数据库
    """
    routes = {}

    if use_skills:
        skills_folder = config.SKILLS_DIR
        if not skills_folder.is_dir():
            raise FileNotFoundError(f"找不到 Skill 文件夹：{skills_folder}")
        # virtual_mode=True：Agent 只能访问 skills_folder 以内的文件，出不去
        routes[SKILLS_ROUTE] = FilesystemBackend(root_dir=str(skills_folder), virtual_mode=True)

    if use_memory:
        # 这里没有直接传数据库，StoreBackend 会使用创建 Agent 时传入的 store
        routes[MEMORY_ROUTE] = StoreBackend(
            namespace=partial(memory_namespace, require_user=require_user)
        )

    backend = CompositeBackend(default=StateBackend(), routes=routes)
    return backend


def build_permissions():
    """创建权限规则：禁止对 /skills/ 下的文件写入。

    读取不受影响，所以 Agent 仍然可以读 Skill。/memories/ 不在规则里，所以记忆可以写。
    """
    rule = FilesystemPermission(
        operations=["write"],
        paths=[SKILLS_ROUTE + "**"],
        mode="deny",
    )
    return [rule]
