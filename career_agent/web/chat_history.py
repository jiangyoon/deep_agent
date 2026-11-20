"""将会话检查点还原成与网页直播一致的执行步骤。"""

from itertools import groupby

from career_agent.core.messages import content_to_text


def tool_call_data(call):
    return {"id": call.get("id", ""), "name": call.get("name", ""), "args": call.get("args", {})}


def tool_result_data(message):
    text = content_to_text(message.content)
    return {
        "id": getattr(message, "tool_call_id", ""),
        "name": getattr(message, "name", ""),
        "status": getattr(message, "status", "success"),
        "result": text if len(text) <= 2000 else text[:2000] + "…",
    }


def visible_messages(messages):
    for message in messages:
        if message.type == "human":
            if message.additional_kwargs.get("lc_source") != "pinned_skill":
                yield "user", message
        elif message.type == "ai":
            if message.content or message.tool_calls or message.additional_kwargs.get("reasoning_content"):
                yield "agent", message
        elif message.type == "tool":
            yield "agent", message


def browser_message_count(messages):
    """侧栏只计数，避免为每个历史会话拼接工具详情。"""
    count = 0
    previous_role = None
    for role, _ in visible_messages(messages):
        if role == "user" or role != previous_role:
            count += 1
        previous_role = role
    return count


def browser_messages(messages):
    """把同一员工提问后连续的模型与工具消息合并到一个回答中。"""
    result = []
    for role, group in groupby(visible_messages(messages), key=lambda pair: pair[0]):
        if role == "user":
            result.extend({"role": role, "content": content_to_text(m.content)} for _, m in group)
            continue
        events = []
        parts = []
        for _, message in group:
            if message.type == "tool":
                events.append({"name": "tool_result", "data": tool_result_data(message)})
                continue
            thought = message.additional_kwargs.get("reasoning_content")
            if thought:
                events.append({"name": "reasoning_delta", "data": {"text": thought}})
            text = content_to_text(message.content)
            if text:
                events.append({"name": "answer_delta", "data": {"text": text}})
                parts.append(text)
            for call in message.tool_calls:
                events.append({"name": "tool_call", "data": tool_call_data(call)})
        result.append({"role": role, "content": "".join(parts), "events": events})
    return result
