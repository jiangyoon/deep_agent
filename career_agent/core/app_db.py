"""PostgreSQL 连接与运行数据的初始化检查。"""

from contextlib import contextmanager

import psycopg
from psycopg.conninfo import conninfo_to_dict
from psycopg.errors import UndefinedTable
from psycopg.rows import dict_row

from career_agent import config

APP_SCHEMA = "deep_agent"
APP_STORAGE_VERSION = 1
CONNECTION_OPTIONS = {
    "autocommit": True,
    "row_factory": dict_row,
    "connect_timeout": 10,
    "options": f"-c search_path={APP_SCHEMA}",
    "prepare_threshold": None,
}


class AppDatabaseError(RuntimeError):
    """可以安全输出到终端的应用库错误。"""


@contextmanager
def connect_app_db():
    """每条连接都将未限定名称解析到应用 schema。"""
    if not config.APP_DB_DSN:
        raise AppDatabaseError("请在根目录 .env 或环境变量中配置 CAREER_APP_DB_DSN 或完整 PG 连接参数")
    try:
        connection = psycopg.connect(config.APP_DB_DSN, **CONNECTION_OPTIONS)
    except psycopg.Error as error:
        raise AppDatabaseError(f"无法连接应用 PostgreSQL（{type(error).__name__}）") from None
    with connection:
        yield connection


@contextmanager
def open_app_pool():
    """服务共享一组连接，失效连接在归还或取用时由连接池更换。"""
    from psycopg_pool import ConnectionPool

    with connect_app_db() as connection:
        require_app_storage(connection)
    dsn = conninfo_to_dict(config.APP_DB_DSN)
    with ConnectionPool(
        kwargs={**dsn, **CONNECTION_OPTIONS},
        min_size=1,
        max_size=4,
        timeout=10,
        check=ConnectionPool.check_connection,
        open=False,
    ) as pool:
        yield pool


def require_app_schema(connection):
    schema = connection.execute("SELECT current_schema() AS name").fetchone()["name"]
    if schema != APP_SCHEMA:
        raise AppDatabaseError(f"应用连接需要可访问的 {APP_SCHEMA} schema")


def require_app_storage(connection):
    require_app_schema(connection)
    try:
        row = connection.execute("SELECT max(version) AS version FROM app_storage").fetchone()
    except UndefinedTable:
        row = None
    if row is None or row["version"] != APP_STORAGE_VERSION:
        raise AppDatabaseError("请先执行 python -m scripts.init_app_db 初始化应用库")
