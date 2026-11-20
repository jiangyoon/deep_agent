"""把 Deep Agent 的流式片段转换成浏览器可消费的 SSE 事件。"""

import json
import logging

from langchain_core.messages import AIMessageChunk

from career_agent import config
from career_agent.core.messages import content_to_text
from career_agent.web.chat_history import tool_call_data, tool_result_data
from career_agent.web.usage_tracker import StreamUsageTracker


logger = logging.getLogger(__name__)


def sse_event(event: str, data: dict) -> str:
    """把事件名和数据编码成一条 Server-Sent Event。"""
    payload = json.dumps(
        data,
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
    )
    return "event: " + event + "\ndata: " + payload + "\n\n"


def message_list(data) -> list:
    """从 LangGraph 节点更新中取出消息列表。"""
    messages = data.get("messages", [])
    if isinstance(messages, list):
        return messages
    return [messages]


def usage_data(usage) -> dict | None:
    """把模型 usage 转成适合前端展示的数据。"""
    if not usage:
        return None

    input_tokens = usage.get("input_tokens")
    output_tokens = usage.get("output_tokens")
    result = {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
    }

    context_tokens = getattr(config, "MODEL_CONTEXT_TOKENS", None)
    result["limit_tokens"] = context_tokens or 0
    if context_tokens and input_tokens is not None:
        total = input_tokens + (output_tokens or 0)
        result["context_percent"] = total * 100 / context_tokens

    return result


def stream_answer(agent, question: str, run_config=None, context=None):
    """运行一次独立问答，发送思考、工具步骤、用量和回答正文。"""
    text_emitted = False
    usage_tracker = StreamUsageTracker()

    try:
        stream_arguments = {
            "stream_mode": ["messages", "updates"],
        }
        if run_config is not None:
            stream_arguments["config"] = run_config
        if context is not None:
            stream_arguments["context"] = context

        chunks = agent.stream(
            {"messages": [{"role": "user", "content": question}]},
            **stream_arguments,
        )

        for mode, payload in chunks:
            if mode == "messages":
                chunk, metadata = payload
                if not isinstance(chunk, AIMessageChunk):
                    continue
                if metadata.get("langgraph_node") != "model":
                    continue

                usage_tracker.add_chunk(chunk)

                reasoning = chunk.additional_kwargs.get(
                    "reasoning_content",
                    "",
                )
                if reasoning != "":
                    yield sse_event(
                        "reasoning_delta",
                        {"text": reasoning},
                    )

                text = content_to_text(chunk.content)
                if text != "":
                    text_emitted = True
                    yield sse_event("answer_delta", {"text": text})

            elif mode == "updates":
                for node_name, data in payload.items():
                    if data is None:
                        continue

                    if node_name == "model":
                        for message in message_list(data):
                            for call in getattr(message, "tool_calls", []):
                                yield sse_event("tool_call", tool_call_data(call))

                            usage = usage_tracker.get_usage()
                            if usage is None:
                                usage = getattr(
                                    message,
                                    "usage_metadata",
                                    None,
                                )
                            data_for_frontend = usage_data(usage)
                            if data_for_frontend is not None:
                                yield sse_event(
                                    "usage",
                                    data_for_frontend,
                                )

                        usage_tracker.reset()

                    elif node_name == "tools":
                        for message in message_list(data):
                            yield sse_event("tool_result", tool_result_data(message))

        if not text_emitted:
            raise RuntimeError("Agent 没有返回回答正文")

        yield sse_event("done", {})
    except Exception:
        logger.exception("Web 流式问答失败")
        yield sse_event(
            "error",
            {"message": "模型调用失败，请检查服务端日志和模型配置"},
        )
