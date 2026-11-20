# core/memory_store.py
# 作用：记忆的存储。所有"和数据库打交道"的代码都集中在这一个文件里。
# 记忆存放在 PostgreSQL 的应用 schema。
#
# 几个名字的对应关系（容易混，单独写清楚）：
#   Agent 眼里的记忆文件路径：/memories/AGENTS.md
#   数据库里的分组（namespace）：("deep_agent", "memories")
#   数据库里的键（key）：/AGENTS.md          <- 就是路径去掉 /memories 前缀之后的部分
#   数据库里的值（value）：一个字典，记忆正文在 "content" 里，另外记录编码、创建时间、修改时间
#
# 值的格式不是我们自己定的，而是用框架提供的三个函数生成和读取：
#   create_file_data、update_file_data、file_data_to_string
# 这样和 Agent 自己写入的格式完全一致。

import hashlib
import json

from deepagents.backends.utils import (
    create_file_data,
    file_data_to_string,
    update_file_data,
)
# Agent 眼里的记忆路径。注意 MEMORY_ROUTE 结尾的斜杠
MEMORY_ROUTE = "/memories/"
MEMORY_FILE_PATH = "/memories/AGENTS.md"

# 数据库里的分组和键
MEMORY_NAMESPACE = ("deep_agent", "memories")
MEMORY_KEY = "/AGENTS.md"


def get_memory_namespace(user_id=None):
    """Web 用户各自独立，记忆管理命令可访问原有本地分组。"""
    if user_id is None:
        return MEMORY_NAMESPACE
    if not isinstance(user_id, str) or not user_id:
        raise ValueError("记忆用户 ID 不能为空")
    return ("deep_agent", "users", user_id, "memories")

# 第一次运行时预置的模板。
# 为什么要预置：框架让模型用 edit_file 更新记忆，而 edit_file 只能改"已经存在的文件"，
# 文件不存在时会报错，所以先放一个带标题的空模板
MEMORY_TEMPLATE = (
    "# 用户记忆\n"
    "\n"
    "## 用户偏好\n"
    "\n"
    "## 用户背景\n"
)


def create_memory_store(pool):
    """通过应用连接池读写 LangGraph 长期记忆。

    langgraph-store-postgres 0.1.0 起从命名空间包改成了顶层包，
    两种安装形态都支持。
    """
    try:
        from langgraph.store.postgres import PostgresStore
    except ModuleNotFoundError:
        from langgraph_store_postgres import PostgresStore

    return PostgresStore(pool)


def read_memory(store, user_id=None):
    """读取记忆正文。还没有记忆时返回 None。"""
    item = store.get(get_memory_namespace(user_id), MEMORY_KEY)
    if item is None:
        return None
    return file_data_to_string(item.value)


def memory_snapshot(store, user_id):
    """返回当前用户的正文与版本指纹；只读，不预置或修改记忆。"""
    item = store.get(get_memory_namespace(user_id), MEMORY_KEY)
    value = item.value if item is not None else None
    content = file_data_to_string(value) if value is not None else ""
    serialized = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return {
        "content": content,
        "revision": hashlib.sha256(serialized.encode("utf-8")).hexdigest(),
        "char_count": len(content),
        "updated_at": value.get("modified_at") if value is not None else None,
    }


def write_memory(store, text, user_id=None):
    """用 text 整体替换记忆正文。已有记忆时保留它的创建时间。"""
    namespace = get_memory_namespace(user_id)
    item = store.get(namespace, MEMORY_KEY)
    if item is None:
        file_data = create_file_data(text)
    else:
        file_data = update_file_data(item.value, text)
    store.put(namespace, MEMORY_KEY, file_data)


def seed_memory_if_missing(store, user_id=None):
    """还没有记忆时，写入空模板。返回 True 表示这次新建了模板。"""
    if read_memory(store, user_id) is None:
        write_memory(store, MEMORY_TEMPLATE, user_id)
        return True
    return False


def clear_memory(store, user_id=None):
    """删除记忆。"""
    store.delete(get_memory_namespace(user_id), MEMORY_KEY)
