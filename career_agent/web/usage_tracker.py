# web/usage_tracker.py
# 作用：SSE 显示层的 token 用量还原薄壳。
#
# 背景：流式 chunk 的 usage 会被 LangChain 合并相加导致虚高，
#       完整背景、还原算法、开关说明见 core/stream_usage.py（唯一实现）。
#
# 本模块与 core/stream_usage.py 的分工：
#   - core 的 patch_stream_usage(llm)：挂在模型流出口做"源头修正"，
#     保证持久化（LangGraph 会话记录）和 SSE 两条路径拿到正确数字；
#   - 本模块：显示层兜底——即使 FIX_STREAM_USAGE=False（源头补丁不挂）、
#     chunk 原样流过，SSE 侧仍能用同一个累加器还原出正确数字。
#     挂了补丁时本模块只会收到一个带终值的 chunk，取值逻辑天然退化正确。
#
# 开关：config.FIX_STREAM_USAGE
#   True  → 显示还原后的数字（默认）
#   False → get_usage() 恒返回 None，调用方应回退到 message.usage_metadata

from career_agent.core.stream_usage import (
    StreamUsageAccumulator,
    fix_stream_usage_enabled,
)

__all__ = ["StreamUsageTracker", "fix_stream_usage_enabled"]


class StreamUsageTracker:
    """在一次模型流式调用期间，从 chunk 收集 usage 并还原真实总量。

    对外接口保持稳定：add_chunk(chunk) / get_usage() / reset()，
    内部复用 core/stream_usage.py 的累加器，不在 web 层再写一份算法。
    """

    def __init__(self):
        self._accumulator = StreamUsageAccumulator()

    def reset(self):
        """清空，准备下一轮模型调用。"""
        self._accumulator.reset()

    def add_chunk(self, chunk):
        """吸入一个流式消息片段上的 usage（没有 usage 的片段会忽略）。"""
        if not fix_stream_usage_enabled():
            return
        usage = getattr(chunk, "usage_metadata", None)
        if usage:
            self._accumulator.add(dict(usage))

    def get_usage(self):
        """返回还原后的用量字典；未启用、或本轮没收到任何 usage 时返回 None。"""
        if not fix_stream_usage_enabled():
            return None
        full = self._accumulator.result()
        if full is None:
            return None
        # 保持既有返回形状：调用方只认 input_tokens / output_tokens 两个键
        return {
            "input_tokens": full["input_tokens"],
            "output_tokens": full["output_tokens"],
        }
