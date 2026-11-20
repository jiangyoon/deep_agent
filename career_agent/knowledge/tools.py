"""网页 Agent 的知识库（KB1）工具注册入口。

create_kb1_tools(pool) 由 web/app.py 的 lifespan 调用：应用启动时建一次连接池，
随后把整套工具挂到 application.state，供 deepagent 每轮调用。全部工具只读，
不写知识库。

六个工具按「精确查询 / 半结构化定位 / 语义检索」三类分工：

  lookup_certificate_mapping    证书/简称 → 对应职称（kb1_mappings，按渠道双来源）
  search_policy_sections        政策关键词 → 条款原文（Milvus 混合检索 + PG 回表）
  lookup_certificate_reward      证书/组合 → 奖励金额与补贴（kb1_rewards）
  lookup_application_schedule    渠道/年度 → 申报时间节点（kb1_schedules）
  find_policy_document          文号/标题 → 文件定位（kb1_sources.doc_no）
  list_notice_attachments       文件 ID → 附件清单（kb1_attachments，模板下载入口）

新增工具时：在本文件 import 并加入返回列表，工具内部自行保证只读、
异常兜底（handle_db_errors）和 5s 超时，与既有工具保持一致。
"""

from .application_schedule import create_application_schedule_tool
from .certificate_mapping import create_certificate_mapping_tool
from .certificate_reward import create_certificate_reward_tool
from .notice_attachments import create_notice_attachments_tool
from .policy_document import create_policy_document_tool
from .policy_search import create_policy_search_tool


def create_kb1_tools(pool):
    """把连接池注入各工具工厂，返回可直接 bind_tools 的工具列表。

    pool 由 career_agent.core.app_db.open_app_pool 提供；每个工厂返回的
    是 LangChain BaseTool，模型通过其 docstring 判断何时调用。
    """
    return [
        create_certificate_mapping_tool(pool),
        create_policy_search_tool(pool),
        create_certificate_reward_tool(pool),
        create_application_schedule_tool(pool),
        create_policy_document_tool(pool),
        create_notice_attachments_tool(pool),
    ]
