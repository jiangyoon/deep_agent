"""在现有 deep_agent schema 中初始化 Web 应用表并维护中文注释。"""

import argparse

from psycopg import Error, sql

from career_agent.core.app_db import (
    APP_SCHEMA,
    APP_STORAGE_VERSION,
    AppDatabaseError,
    connect_app_db,
    require_app_schema,
    require_app_storage,
)

DATA_TABLES = ("users", "auth_sessions", "chat_sessions", "store", "checkpoints",
               "checkpoint_blobs", "checkpoint_writes")
APP_DDL = (
    """
    CREATE TABLE users (
        id text PRIMARY KEY,
        username text COLLATE "C" NOT NULL UNIQUE,
        password_hash text NOT NULL,
        created_at bigint NOT NULL
    )
    """,
    """
    CREATE TABLE auth_sessions (
        token_hash text PRIMARY KEY,
        user_id text NOT NULL REFERENCES users(id),
        expires_at bigint NOT NULL
    )
    """,
    "CREATE INDEX auth_expiry ON auth_sessions(expires_at)",
    """
    CREATE TABLE chat_sessions (
        id text PRIMARY KEY,
        user_id text NOT NULL REFERENCES users(id),
        created_at bigint NOT NULL,
        chat_order bigint GENERATED ALWAYS AS IDENTITY
    )
    """,
    "CREATE INDEX chat_owner ON chat_sessions(user_id, created_at DESC, chat_order DESC)",
    "CREATE TABLE app_storage (version smallint PRIMARY KEY)",
)
SCHEMA_COMMENTS = {
    "app_storage": (
        "Web 应用存储的初始化版本。应用启动时用它确认数据库已就绪。",
        {
            "version": "应用存储结构的版本号。",
        },
    ),
    "users": (
        "可以登录职业规划助手的网页账号。",
        {
            "id": "用户的唯一标识，由应用生成。会话归属和个人记忆均使用此标识。",
            "username": "用户登录时输入的名称，唯一且区分大小写。",
            "password_hash": "密码的 PBKDF2-SHA256 校验值，编码包含算法、迭代次数和随机盐。",
            "created_at": "账号创建时间，Unix 时间戳，单位为秒。",
        },
    ),
    "auth_sessions": (
        "网页登录凭证及其有效期，用于校验浏览器的登录状态。",
        {
            "token_hash": "浏览器登录令牌的 SHA-256 摘要，用于查验登录状态。",
            "user_id": "登录凭证对应的用户 ID，关联 users.id。",
            "expires_at": "凭证失效时间，Unix 时间戳，单位为秒。",
        },
    ),
    "chat_sessions": (
        "网页聊天会话及其用户归属，用于会话列表和访问权限校验。",
        {
            "id": "网页聊天会话的唯一标识，也是该会话在 LangGraph 中的线程 ID。",
            "user_id": "会话所属用户的 ID，关联 users.id。",
            "created_at": "会话创建时间，Unix 时间戳，单位为秒。",
            "chat_order": "数据库自动生成的递增序号，用于给同一秒创建的会话排序。",
        },
    ),
    "checkpoints": (
        "LangGraph 的会话执行状态快照，用于恢复对话和图执行。",
        {
            "thread_id": "LangGraph 线程 ID。网页会话使用 chat_sessions.id 作为线程 ID。",
            "checkpoint_ns": "检查点命名空间，空字符串表示根图，子图使用自己的命名空间。",
            "checkpoint_id": "线程和命名空间内的检查点 ID。",
            "parent_checkpoint_id": "同一线程和命名空间内的父检查点 ID。首个检查点可为空。",
            "type": "LangGraph 保留的检查点类型字段，允许为空。",
            "checkpoint": "检查点的 JSONB 状态，含通道版本和可内联的值。其他通道值可存于 checkpoint_blobs。",
            "metadata": "检查点的 JSONB 元数据，例如执行步骤和触发来源。",
        },
    ),
    "checkpoint_blobs": (
        "LangGraph 按通道版本保存的序列化状态值，供检查点恢复时读取。",
        {
            "thread_id": "通道值所属的 LangGraph 线程 ID。",
            "checkpoint_ns": "通道值所属的检查点命名空间，空字符串表示根图。",
            "channel": "图状态通道的名称，例如 messages。",
            "version": "该通道值的版本标识，与检查点保存的通道版本对应。",
            "type": "二进制值所用的序列化类型标识，供读取时解码。",
            "blob": "序列化后的通道值。框架可用空值表示该版本没有通道内容。",
        },
    ),
    "checkpoint_writes": (
        "LangGraph 记录的任务中间写入，恢复时用于续用已保存的任务结果。",
        {
            "thread_id": "中间写入所属的 LangGraph 线程 ID。",
            "checkpoint_ns": "关联检查点的命名空间，空字符串表示根图。",
            "checkpoint_id": "该写入所属的检查点 ID。",
            "task_id": "产生该写入的 LangGraph 任务 ID。",
            "idx": "任务内的写入索引。负值由框架保留给错误、中断等特殊写入。",
            "channel": "该写入的目标状态通道。",
            "type": "写入值的序列化类型标识，供读取时解码。",
            "blob": "该次写入的序列化二进制值。",
            "task_path": "图执行中的任务路径，供框架在恢复时确定任务和写入顺序。",
        },
    ),
    "checkpoint_migrations": (
        "LangGraph 检查点表已执行的结构迁移记录。",
        {
            "v": "LangGraph 已执行的检查点存储迁移编号。",
        },
    ),
    "store": (
        "LangGraph 长期存储。应用使用它保存用户隔离的长期记忆。",
        {
            "prefix": "用点分隔的命名空间。应用在用户记忆的命名空间中包含用户 ID。",
            "key": "命名空间内的条目键。应用的长期记忆文档使用 /AGENTS.md。",
            "value": "条目的 JSONB 内容。记忆文档包含正文和文件元数据。",
            "created_at": "条目首次写入的时间，含时区。",
            "updated_at": "数据库最近更新该条目的时间，含时区。",
            "expires_at": "条目的到期时间，含时区。为空表示未设置到期时间。",
            "ttl_minutes": "条目的存活时长，单位为分钟。为空表示未配置。",
        },
    ),
    "store_migrations": (
        "LangGraph 长期存储表已执行的结构迁移记录。",
        {
            "v": "LangGraph 已执行的长期存储迁移编号。",
        },
    ),
}


def write_schema_comments(connection):
    for table, (description, columns) in SCHEMA_COMMENTS.items():
        connection.execute(
            sql.SQL("COMMENT ON TABLE {} IS {}").format(
                sql.Identifier(APP_SCHEMA, table), sql.Literal(description)
            )
        )
        for column, comment in columns.items():
            connection.execute(
                sql.SQL("COMMENT ON COLUMN {} IS {}").format(
                    sql.Identifier(APP_SCHEMA, table, column), sql.Literal(comment)
                )
            )


def table_exists(connection, table):
    return connection.execute(
        "SELECT to_regclass(%s) IS NOT NULL AS present", (f"{APP_SCHEMA}.{table}",)
    ).fetchone()["present"]


def initialize(connection):
    from langgraph.checkpoint.postgres import PostgresSaver
    from langgraph.store.postgres import PostgresStore

    require_app_schema(connection)
    locked = connection.execute(
        "SELECT pg_try_advisory_lock(hashtext(%s)) AS locked", (f"{APP_SCHEMA}:init",)
    ).fetchone()["locked"]
    if not locked:
        raise AppDatabaseError("另一个进程正在初始化应用库")
    try:
        if table_exists(connection, "app_storage"):
            require_app_storage(connection)
            missing = [table for table in DATA_TABLES if not table_exists(connection, table)]
            if missing:
                raise AppDatabaseError(f"{APP_SCHEMA} 缺少应用表：{', '.join(missing)}")
            with connection.transaction():
                write_schema_comments(connection)
            return False

        for table in DATA_TABLES:
            if table_exists(connection, table) and connection.execute(
                sql.SQL("SELECT EXISTS (SELECT 1 FROM {}) AS occupied").format(sql.Identifier(table))
            ).fetchone()["occupied"]:
                raise AppDatabaseError(f"{APP_SCHEMA}.{table} 已有数据，初始化已停止")

        PostgresStore(connection).setup()
        PostgresSaver(connection).setup()
        with connection.transaction():
            for statement in APP_DDL:
                connection.execute(statement)
            connection.execute("INSERT INTO app_storage (version) VALUES (%s)", (APP_STORAGE_VERSION,))
            write_schema_comments(connection)
        return True
    finally:
        connection.execute("SELECT pg_advisory_unlock(hashtext(%s))", (f"{APP_SCHEMA}:init",))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    try:
        with connect_app_db() as connection:
            created = initialize(connection)
        print(f"{APP_SCHEMA} 应用表{'已初始化' if created else '已就绪'}")
    except AppDatabaseError as error:
        parser.exit(1, f"{error}\n")
    except (Error, ImportError) as error:
        parser.exit(1, f"应用表初始化失败（{type(error).__name__}），请检查依赖和数据库权限。\n")


if __name__ == "__main__":
    main()
