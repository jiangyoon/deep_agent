# core/stream_usage.py
# 作用：模型流式输出的 token 用量"还原与修正"——全项目唯一的算法实现。
#
# 背景：
#   stream_usage=True 时，部分服务端（如 vLLM）几乎每个 chunk 都带 usage，
#   且 input_tokens 往往是"整次请求的完整值"（不是增量）。
#   LangChain 把 chunk 合并成完整消息时会把这些数字相加，
#   于是 input_tokens ≈ 真实值 × chunk 次数，严重虚高；
#   output_tokens 若按增量上报则大致正确，若按累计上报也会被加坏。
#   虚高数字一旦被 LangGraph 持久化进会话记录，刷新页面后仪表盘就会爆表。
#
# 还原口径（两个服务端风格通吃）：
#   - input_tokens ：各 chunk 取最大值（vLLM 每个 chunk 报全量，最大值即真实值；
#     OpenAI 风格只在最后一个 chunk 报，最大值同样是真实值）
#   - output_tokens：一路非递减（累计上报）取最后一个；中途变小（增量上报）则求和
#
# 本模块的两种用法：
#   1. 源头修正（持久化治本）：patch_stream_usage(llm) 在模型流出口拦截——
#      中间 chunk 剥掉 usage，流结束补一个带真实终值的空 chunk。
#      这样 LangChain 合并出的 usage_metadata 恒等于真实值，
#      会话持久化、SSE 显示两条路径同时拿到正确的数。
#      挂载点在 core/llm_model.py 的 create_llm()。
#   2. 显示层兜底：web/usage_tracker.py 复用本模块的累加器，
#      在 FIX_STREAM_USAGE=False（源头补丁不挂）时，
#      显示侧仍能从原始 chunk 还原出正确数字。
#
# 开关：config.FIX_STREAM_USAGE
#   True  → 生效（默认）
#   False → 补丁不挂、显示层回退到框架合并原值，行为一致

from langchain_core.messages import AIMessageChunk
from langchain_core.outputs import ChatGenerationChunk

from career_agent import config


def fix_stream_usage_enabled():
    """是否启用流式 usage 修正。config 里没有这项时默认开启。"""
    return getattr(config, "FIX_STREAM_USAGE", True)


class StreamUsageAccumulator:
    """从流式 chunk 收集用量并还原真实值。纯算法，不感知开关。

    用法：每个带 usage 的 chunk 调一次 add(dict)，流结束后调 result()。
    """

    def __init__(self):
        self.reset()

    def reset(self):
        """清空状态，可复用于下一轮流式调用。"""
        self.seen = False
        self.input_max = None
        self.output_sum = 0
        self.output_last = None
        self.output_nondecreasing = True
        self.input_details = None
        self.output_details = None

    def add(self, usage):
        """吸入一个 usage 字典（LangChain 的 usage_metadata 形态）。"""
        if not usage:
            return
        self.seen = True

        input_tokens = usage.get("input_tokens")
        if input_tokens is not None:
            if self.input_max is None or input_tokens > self.input_max:
                self.input_max = input_tokens

        output_tokens = usage.get("output_tokens")
        if output_tokens is not None:
            if self.output_last is not None and output_tokens < self.output_last:
                self.output_nondecreasing = False
            self.output_sum = self.output_sum + output_tokens
            self.output_last = output_tokens

        # 细分字段（思考链、缓存命中等）取最新一次上报的
        if usage.get("input_token_details"):
            self.input_details = dict(usage["input_token_details"])
        if usage.get("output_token_details"):
            self.output_details = dict(usage["output_token_details"])

    def result(self):
        """返回还原后的 usage 字典；本轮没有收到任何 usage 时返回 None。"""
        if not self.seen:
            return None

        if self.output_nondecreasing and self.output_last is not None:
            output_tokens = self.output_last
        else:
            output_tokens = self.output_sum

        result = {
            "input_tokens": self.input_max,
            "output_tokens": output_tokens,
            "total_tokens": (self.input_max or 0) + output_tokens,
        }
        if self.input_details is not None:
            result["input_token_details"] = self.input_details
        if self.output_details is not None:
            result["output_token_details"] = self.output_details
        return result


def strip_chunk_usage(message):
    """剥掉 chunk 消息上携带的原始 usage（流式合并时这些数字会被相加）。"""
    if message.usage_metadata:
        message.usage_metadata = None
    metadata = message.response_metadata
    if metadata and ("token_usage" in metadata or "usage" in metadata):
        message.response_metadata = {
            key: value for key, value in metadata.items()
            if key not in ("token_usage", "usage")
        }


def corrected_chunks(chunks):
    """包装模型流：中间 chunk 的 usage 全部剥掉，流结束时补一个带真实终值的空 chunk。

    这样无论服务端是 vLLM 风格（每个 chunk 都报全量 usage）还是 OpenAI 风格
    （只在最后一个 chunk 报），LangChain 合并 chunk 得到的 usage_metadata
    都等于真实值——持久化进会话记录的数字不再虚高。
    """
    accumulator = StreamUsageAccumulator()
    for chunk in chunks:
        message = chunk.message
        if message.usage_metadata:
            accumulator.add(dict(message.usage_metadata))
            strip_chunk_usage(message)
        yield chunk

    final_usage = accumulator.result()
    if final_usage is not None:
        yield ChatGenerationChunk(
            message=AIMessageChunk(content="", usage_metadata=final_usage),
            generation_info={},
        )


def patch_stream_usage(llm):
    """给模型实例的流式方法打 usage 修正补丁（实例级，不改类定义）。

    为什么修在流出口：deepagents 的模型节点会把流式 chunk 合并成完整消息再
    存进会话状态（LangChain 合并规则是逐字段相加，虚高就发生在这一步）。
    只有在模型流的源头剥掉错误数字、补回真实终值，持久化、SSE 显示
    两条路径才能同时拿到正确的数。
    """
    original_stream = llm._stream
    original_astream = llm._astream

    def stream_wrapped(*args, **kwargs):
        yield from corrected_chunks(original_stream(*args, **kwargs))

    async def astream_wrapped(*args, **kwargs):
        async for chunk in corrected_chunks(original_astream(*args, **kwargs)):
            yield chunk

    llm._stream = stream_wrapped
    llm._astream = astream_wrapped
    return llm
