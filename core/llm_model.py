# core/llm_model.py
# 作用：创建"模型连接对象"。
# 你的 Qwen 服务对外提供的是 OpenAI 兼容接口。
#
# 这里用的是 langchain-deepseek 里的 ChatDeepSeek，而不是 langchain-openai 里的 ChatOpenAI。
# 原因：ChatOpenAI 只按官方 OpenAI 规范解析返回，vLLM 返回的思考内容（reasoning 字段）
# 会被丢掉；LangChain 官方文档建议，遇到这种情况改用 ChatDeepSeek。
# 它会把思考内容统一保存到消息的 additional_kwargs["reasoning_content"] 里，
# 后面的打印代码（display/printer.py、display/stream_handler.py）都从这个位置取。
#
# 注意：ChatDeepSeek 只是借来用，我们连的仍然是你自己的 Qwen 服务，不会访问 DeepSeek。
# 以后如果 ChatOpenAI 原生支持了思考内容，只需要把这个文件里的类名换回去。

from langchain_deepseek import ChatDeepSeek

import config
from core.thinking_mode import get_extra_body

# config.py 里的示例值里会出现这些文字，说明用户还没有改成真实值
PLACEHOLDER_URL_TEXT = "你的服务器地址"
PLACEHOLDER_MODEL_TEXT = "把这里换成"


def check_settings():
    """检查 config.py 里的模型地址和模型名是不是还是示例值。

    如果还是示例值，直接给出明确的中文提示。
    否则程序会去连一个根本不存在的地址，报出一大串让人看不懂的网络错误。
    """
    if PLACEHOLDER_URL_TEXT in config.MODEL_BASE_URL:
        raise ValueError(
            "config.py 里的 MODEL_BASE_URL 还是示例值，请改成你的模型服务地址，"
            "例如 http://192.168.1.10:8000/v1"
        )

    if config.MODEL_NAME.startswith(PLACEHOLDER_MODEL_TEXT):
        raise ValueError(
            "config.py 里的 MODEL_NAME 还是示例值，请改成模型 id，"
            "可以用 curl.exe 访问 MODEL_BASE_URL/models 查看"
        )

    if config.MODEL_API_KEY == "":
        raise ValueError(
            "未设置模型密钥。请设置环境变量 DEEP_AGENT_MODEL_API_KEY，"
            "或写在项目根目录的 .env 中"
        )


def create_llm():
    """根据 config.py 里的设置，创建并返回模型连接对象。

    注意：这里只是"准备好连接"，并不会真的发请求。
    只有 Agent 真正调用模型时，才会联网。
    """
    # 先检查设置有没有填
    check_settings()

    # 根据 THINKING_MODE，得到要额外发给服务端的参数（可能是空字典）
    extra_body = get_extra_body(config.THINKING_MODE)

    # 告诉框架模型的最大输入长度，框架据此决定什么时候自动压缩对话（阶段 6 新增）。
    # getattr 的意思是：config 里有 MODEL_MAX_INPUT_TOKENS 就取它，没有就当作 None，
    # 这样没有追加阶段 6 配置的旧 config.py 也能正常运行
    max_input_tokens = getattr(config, "MODEL_MAX_INPUT_TOKENS", None)
    profile = None
    if max_input_tokens is not None:
        profile = {"max_input_tokens": max_input_tokens}

    llm = ChatDeepSeek(
        model=config.MODEL_NAME,
        api_base=config.MODEL_BASE_URL,
        api_key=config.MODEL_API_KEY,
        temperature=config.TEMPERATURE,
        timeout=config.REQUEST_TIMEOUT,
        max_retries=config.MAX_RETRIES,
        extra_body=extra_body,
        # 流式输出时，要求服务端在最后返回这次请求的 token 用量（阶段 6 新增）
        stream_usage=True,
        # 最大输入长度
        profile=profile,
    )
    return llm
