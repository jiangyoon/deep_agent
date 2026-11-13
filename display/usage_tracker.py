# display/usage_tracker.py
# 作用：从流式 chunk 里还原真实的 token 用量，避免被 LangChain 合并时加错。
#
# 背景：
#   stream_usage=True 时，服务端几乎每个 chunk 都带 usage，
#   且 input_tokens 往往是"整次请求的完整值"（不是增量）。
#   LangChain 把 chunk 拼成最终消息时会把这些数字相加，
#   于是 input_tokens ≈ 真实值 × chunk 次数，严重虚高；
#   output_tokens 若按增量上报则大致正确，若按累计上报也会被加坏。
#
# 本模块的还原规则：
#   - input_tokens ：取各 chunk 的最大值
#   - output_tokens：若一路非递减（像累计），取最后一个；否则按增量求和
#
# 开关：config.FIX_STREAM_USAGE
#   True  → 使用本模块还原后的数字（默认）
#   False → 调用方应回退到 message.usage_metadata（框架合并后的原值）

import config


def fix_stream_usage_enabled():
    """是否启用流式 usage 修正。config 里没有这项时默认开启。"""
    return getattr(config, "FIX_STREAM_USAGE", True)


class StreamUsageTracker:
    """在一次模型流式调用期间，收集各 chunk 的 usage 并还原真实总量。"""

    def __init__(self):
        self.reset()

    def reset(self):
        """清空，准备下一轮模型调用。"""
        self._seen = False
        self._input_max = None
        self._output_sum = 0
        self._output_last = None
        self._output_nondecreasing = True

    def add_chunk(self, chunk):
        """吸入一个流式消息片段上的 usage（没有 usage 的片段会忽略）。"""
        if not fix_stream_usage_enabled():
            return

        usage = getattr(chunk, "usage_metadata", None)
        if not usage:
            return

        self._seen = True

        input_tokens = usage.get("input_tokens")
        if input_tokens is not None:
            if self._input_max is None or input_tokens > self._input_max:
                self._input_max = input_tokens

        output_tokens = usage.get("output_tokens")
        if output_tokens is not None:
            if self._output_last is not None and output_tokens < self._output_last:
                self._output_nondecreasing = False
            self._output_sum = self._output_sum + output_tokens
            self._output_last = output_tokens

    def get_usage(self):
        """返回还原后的用量字典；未启用、或本轮没收到任何 usage 时返回 None。"""
        if not fix_stream_usage_enabled() or not self._seen:
            return None

        if self._output_nondecreasing and self._output_last is not None:
            output_tokens = self._output_last
        else:
            output_tokens = self._output_sum

        return {
            "input_tokens": self._input_max,
            "output_tokens": output_tokens,
        }
