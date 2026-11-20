"""应用配置。系统环境变量优先于项目根目录的 .env。"""

import os
from pathlib import Path

from dotenv import dotenv_values
from psycopg.conninfo import make_conninfo

APP_DIR = Path(__file__).resolve().parent
PROJECT_DIR = APP_DIR.parent
_DOTENV_VALUES = dotenv_values(PROJECT_DIR / ".env")


def _setting(name: str) -> str:
    """系统环境变量优先；未设置或为空时，再用 .env 里的值。"""
    system_value = os.environ.get(name)
    if system_value is not None and system_value.strip() != "":
        return system_value.strip()
    file_value = _DOTENV_VALUES.get(name)
    if file_value is None:
        return ""
    return file_value.strip()


def _app_db_dsn() -> str:
    """优先使用应用专用连接串，也可使用同库的 PostgreSQL 参数。"""
    conninfo = _setting("CAREER_APP_DB_DSN")
    if conninfo:
        return conninfo
    options = {
        "host": _setting("PGHOST"),
        "port": _setting("PGPORT"),
        "dbname": _setting("PGDATABASE"),
        "user": _setting("PGUSER"),
        "password": _setting("PGPASSWORD"),
    }
    return make_conninfo(**options) if all(options.values()) else ""

# ---------------------------------------------------------------
# 一、模型服务（必须改这两项）
# ---------------------------------------------------------------

# 模型服务的地址，注意结尾要带 /v1
# 例如：http://192.168.1.10:8000/v1
MODEL_BASE_URL = _setting("DEEP_AGENT_MODEL_BASE_URL")

# 模型名，要和 curl .../v1/models 返回结果里的 id 完全一致（区分大小写）
MODEL_NAME = _setting("DEEP_AGENT_MODEL_NAME")

# ---------------------------------------------------------------
# 二、模型调用参数（可以先不改）
# ---------------------------------------------------------------

# 自己部署的服务一般不校验密钥，但 ChatOpenAI 要求必须传一个值。
# 不在代码中保存真实值；可写在系统环境变量或项目根目录的 .env 里，系统环境变量优先。
MODEL_API_KEY = _setting("DEEP_AGENT_MODEL_API_KEY")
# MODEL_API_KEY = _setting("MINIMAX_API_KEY")

# 温度：越低回答越稳定，越高越发散。做工具调用时建议偏低
# 注意：Qwen3.8 官方给的非思考模式推荐采样参数是 temperature 0.7、top_p 0.8、
# top_k 20、presence_penalty 1.5。这里先保持 0.2，等对比测试后再决定是否调整
TEMPERATURE = 0.2

# 思考模式，三选一：
#   "default" 不干预（模型默认开启思考，强度最高，回答前会先想很久）
#   "low"     保留思考，但强度调到最低
#   "off"     完全关闭思考
# 关闭思考后速度会明显变快，但工具调用的准确率有没有下降，需要用真实问题测
THINKING_MODE = "default"

# 单次请求最长等待时间（秒）。本地大模型生成较慢，给宽松一点
REQUEST_TIMEOUT = 300

# 请求失败后自动重试的次数
MAX_RETRIES = 1

# ---------------------------------------------------------------
# 三、Agent 的设定
# ---------------------------------------------------------------

# Agent 的 Skill 与记忆
USE_SKILLS = True
SKILLS_DIR = APP_DIR / "skills"
USE_MEMORY = True

# 记忆超过这么多字符时提醒整理（记忆每次请求都会整个放进系统提示）
MEMORY_WARN_CHARS = 2000

# ---------------------------------------------------------------
# 四、会话
# ---------------------------------------------------------------

# 模型服务的上下文长度（输入加输出的总上限），换模型时在 .env 配 DEEP_AGENT_MODEL_CONTEXT_TOKENS。
# 默认值 1000000 是移动云 qwen/qwen3.6-plus 的规格（阿里云百炼 qwen3.6-plus 文档：
# 上下文 1,000,000 tokens、最大输出 65,536、思考模式下最大输入 983,616）。
MODEL_CONTEXT_TOKENS = int(_setting("DEEP_AGENT_MODEL_CONTEXT_TOKENS") or 1000000)

# 是否修正流式输出时被夸大的 token 用量显示。
# True ：用流式片段还原真实 input/output
# False：直接显示 LangChain 合并后的 usage_metadata（流式下 input 常会虚高）
FIX_STREAM_USAGE = True

# 告诉框架的"最大输入长度"。框架估算的 token 数达到它的 85% 时，会自动压缩对话。
# 为什么不直接填 MODEL_CONTEXT_TOKENS：
#   1. 框架估算 token 的方法是"字符数除以 4"，实测对中文大约只算到真实值的一半，
#      填满的话，等框架以为快满了，真实长度早就超过上下文了
#   2. 还要给模型的输出（思考加回答）留出空间
# 所以默认取上下文长度的一半，留足余量；如需收紧（例如控制长会话成本），
# 在 .env 配 DEEP_AGENT_MODEL_MAX_INPUT_TOKENS。运行时会显示每次请求的真实 token 数，可以据此调整
MODEL_MAX_INPUT_TOKENS = int(_setting("DEEP_AGENT_MODEL_MAX_INPUT_TOKENS") or MODEL_CONTEXT_TOKENS // 2)

# 五、应用数据库与 Web 身份验证
APP_DB_DSN = _app_db_dsn()
AUTH_SESSION_TTL_SECONDS = 7 * 24 * 60 * 60
AUTH_COOKIE_NAME = "deep_agent_session"
# 本机 HTTP 使用 False；HTTPS 部署时通过环境变量或 .env 设置为 true。
AUTH_COOKIE_SECURE = _setting("DEEP_AGENT_COOKIE_SECURE").lower() == "true"
