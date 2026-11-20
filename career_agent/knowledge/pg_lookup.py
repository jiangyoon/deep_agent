"""结构化查询共用的输入约束、只读事务和来源有效性条件。

本模块是 knowledge 包的公共地基，被 certificate_reward / application_schedule /
policy_document / notice_attachments / policy_sources 共用，提供四类设施：

  1. 输入类型注解（Keyword / Year / SourceId）——pydantic 校验在工具入参
     生成 JSON Schema 时同步下发给模型，越界值在进入函数体前就被拒绝；
  2. CURRENT_FILE / REVIEWED_FILE——来源有效性 SQL 片段，所有查询必须携带，
     保证「员工问答只用已审核且现行有效的文件」这一口径全库一致；
  3. read_snapshot()——REPEATABLE READ 只读事务 + 5s 语句超时。多步读取
     （如先查来源再查条款）看到同一版本数据，不被换版写入撕裂；
  4. handle_db_errors()——装饰器：数据库异常转 unavailable 状态返回，
     避免模型把「库暂时连不上」误解为「库中没有这条记录」。

两套有效性条件的语义区别（对应 kb1_sources 的两列，不可互换）：
  - f.status='review_dataset'         处理状态：数据是否清洗完毕、可供检索
  - f.validity_status='现行有效'      政策状态：文件是否仍是现行依据
查询用 REVIEWED_FILE（两者都要求）；仅列附件信息、不读正文的场景可放宽到
CURRENT_FILE（见 notice_attachments.py）。
"""

import logging
from contextlib import contextmanager
from functools import wraps
from typing import Annotated

import psycopg
from pydantic import Field, StringConstraints


# 证书名/关键词/地区等自由文本输入：去首尾空白、限长、拒绝 NUL（防注入截断）。
Keyword = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1,
                                           max_length=200, pattern=r"^[^\x00]+$")]
# 年度参数：通知年度或目录版次年份。
Year = Annotated[int, Field(ge=1900, le=2200)]
# 来源 ID：kb1_sources.id，形如 N001、N035-S006 的字母数字串。
SourceId = Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{1,128}$")]

# 在引用这两个片段的查询中，f 均指向 kb1_sources 的一行。
CURRENT_FILE = """
    f.validity_status = '现行有效'
    AND (f.effective_from IS NULL OR f.effective_from <= CURRENT_DATE)
    AND (f.effective_to IS NULL OR f.effective_to >= CURRENT_DATE)
"""
REVIEWED_FILE = f"f.status = 'review_dataset' AND {CURRENT_FILE}"


@contextmanager
def read_snapshot(pool):
    """多次读取看到同一版本的来源、记录和附件关系。

    REPEATABLE READ：事务内无论查询多少次，换版/标注修正等写入都不会
    造成前后不一致（例如第一步选出的来源、第二步仍能查到条款）。
    READ ONLY + 5s 超时：防御性约束——知识库工具永远不写、不拖垮会话。
    """
    with pool.connection() as db, db.transaction():
        db.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        db.execute("SET LOCAL statement_timeout = '5s'")
        yield db


def handle_db_errors(lookup):
    """数据库异常单独返回 unavailable，避免被理解为库中没有记录。

    模型只认状态码：no_match 意味着「查过了、确实没有」，而连不上库时
    若也走 no_match，模型会自信地告诉员工「没有这个政策」——答错的根源。
    unavailable 让模型转述「知识库暂不可用」并可稍后重试。
    """
    @wraps(lookup)
    def guarded(*args, **kwargs):
        try:
            return lookup(*args, **kwargs)
        except psycopg.Error as error:
            logging.getLogger(lookup.__module__).warning("%s 查询失败（%s）", lookup.__name__, type(error).__name__)
            return {"status": "unavailable", "message": "知识库查询暂不可用。", "results": []}

    return guarded
