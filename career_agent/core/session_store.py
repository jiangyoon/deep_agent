# core/session_store.py
# 作用：会话的存储。所有"和会话数据库打交道"的代码都集中在这一个文件里。
# 检查点存放在 PostgreSQL 的应用 schema。
#
# 几个概念：
#   会话编号（thread_id）：一次对话的名字。带同一个编号提问，就是接着这次对话往下聊
#   检查点（checkpoint）：框架在对话的每一步都存一个，记录这一步变化的部分
#   保存器（checkpointer）：负责写入和恢复检查点，这里用 PostgresSaver。

from datetime import datetime


def create_checkpointer(pool):
    """通过应用连接池读写 LangGraph 会话检查点。"""
    from langgraph.checkpoint.postgres import PostgresSaver

    return PostgresSaver(pool)


def new_session_id():
    """生成一个新的会话编号，用当前时间，方便人看，例如 20261112-153045。"""
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def make_config(session_id):
    """把会话编号包装成框架要求的格式。

    调用 agent.stream / agent.invoke / agent.get_state 时，都要通过 config 参数带上它，
    框架才知道这次属于哪个会话。
    """
    return {"configurable": {"thread_id": session_id}}


def list_sessions(saver):
    """列出数据库里所有的会话编号，最近写入的排在前面。

    saver.list(None) 会把所有会话的所有检查点都列出来（一个会话有很多个），
    所以这里要去重，每个会话编号只保留一次。
    """
    session_ids = []
    for checkpoint_tuple in saver.list(None):
        session_id = checkpoint_tuple.config["configurable"]["thread_id"]
        if session_id not in session_ids:
            session_ids.append(session_id)
    return session_ids


def session_exists(saver, session_id):
    """判断这个会话编号在数据库里是否存在。"""
    checkpoint_tuple = saver.get_tuple(make_config(session_id))
    return checkpoint_tuple is not None


def get_session_messages(agent, session_id):
    """读取一个会话的全部消息。

    注意：不能直接从某一个检查点里读消息，因为每个检查点只记录那一步变化的部分；
    完整的会话要用 agent.get_state() 让框架把所有检查点拼起来。
    """
    state = agent.get_state(make_config(session_id))
    return state.values.get("messages", [])


def delete_session(saver, session_id):
    """删除一个会话的全部检查点。"""
    saver.delete_thread(session_id)
