"""暂停当前问答，等待员工补充一项信息。"""

import json
from typing import Annotated, Literal

from langchain.agents.middleware import wrap_model_call
from langchain.tools import ToolRuntime
from langchain_core.messages import ToolMessage
from langchain_core.tools import tool
from langgraph.types import interrupt
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError, model_validator

from career_agent.core.messages import content_to_text


NonBlankText = Annotated[str, StringConstraints(strict=True, strip_whitespace=True, min_length=1)]


class AskOption(BaseModel):
    label: NonBlankText
    value: NonBlankText


class AskReply(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["answered", "skipped"]
    value: NonBlankText | None = None

    @model_validator(mode="after")
    def check_value(self):
        if self.status == "answered" and self.value is None:
            raise ValueError("提交回答时需要提供答案。")
        if self.status == "skipped" and self.value is not None:
            raise ValueError("跳过问题时无需提供答案。")
        return self


class AskQuestion(BaseModel):
    question: NonBlankText
    options: list[AskOption] | None = None

    @model_validator(mode="after")
    def check_option_values(self):
        values = [option.value for option in self.options or []]
        if len(values) != len(set(values)):
            raise ValueError("候选选项的值必须唯一。")
        return self

    def validate_reply(self, raw_reply: object) -> AskReply:
        """恢复前可用同一规则验证提交，避免把错误选项交给模型。"""
        reply = AskReply.model_validate(raw_reply)
        if reply.status == "answered" and self.options:
            if reply.value not in {option.value for option in self.options}:
                raise ValueError("请选择当前问题提供的选项。")
        return reply


class AskToolInput(AskQuestion):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    runtime: ToolRuntime = Field(exclude=True)


class ToolClarification(AskQuestion):
    """业务工具通过结果的 clarification 字段建议向用户确认。"""

    tool: Literal["ask_user"]
    field: NonBlankText


def has_tool_clarification(messages) -> bool:
    """只检查刚返回的工具批次，下一次查询后重新判断。"""
    for message in reversed(messages):
        if not isinstance(message, ToolMessage):
            break
        if message.status != "success":
            continue
        try:
            result = json.loads(content_to_text(message.content))
            if isinstance(result, dict) and "clarification" in result:
                ToolClarification.model_validate(result["clarification"])
                return True
        except (json.JSONDecodeError, ValidationError):
            continue
    return False


def ask_tool_reply(message) -> dict | None:
    if message.type != "tool" or message.name != "ask_user" or message.status != "success":
        return None
    try:
        reply = json.loads(content_to_text(message.content))
    except ValueError:
        return None
    if isinstance(reply, dict) and reply.get("status") in {"answered", "skipped", "deferred"}:
        return reply
    return None


def turn_has_ask_answer(messages) -> bool:
    for message in reversed(messages):
        if message.type == "human" and message.additional_kwargs.get("lc_source") != "pinned_skill":
            break
        reply = ask_tool_reply(message)
        if reply and reply["status"] in {"answered", "skipped"}:
            return True
    return False


@wrap_model_call
def reject_empty_ask_answer(request, handler):
    """空答复在模型节点内报错，让已提交的 Ask 答案可以继续使用。"""
    response = handler(request)
    if (not any(message.tool_calls or content_to_text(message.content).strip() for message in response.result)
            and turn_has_ask_answer(request.messages)):
        raise RuntimeError("收到 Ask 答案后模型未生成后续答复")
    return response


@tool(args_schema=AskToolInput)
def ask_user(question: str, runtime: ToolRuntime, options: list[AskOption] | None = None) -> dict:
    """缺少会影响下一步的员工信息时，暂停问答并提出一个问题。

    候选来自系统或工具结果时，label 用用户能识别的名称，value 用对应的实际查询值。
    省略 options 以接收自由文本。优先沿用已确认的信息，一次只调用一次。
    """
    # 模型可能在一条消息里并行发起多个 Ask。先处理第一题，后续由模型按答案再决定。
    model_message = next(message for message in reversed(runtime.state["messages"]) if message.type == "ai")
    first_ask = next(call for call in model_message.tool_calls if call["name"] == "ask_user")
    if first_ask["id"] != runtime.tool_call_id:
        return {"status": "deferred", "message": "先使用上一题的答案，再决定是否需要追问。"}

    prompt = AskQuestion(question=question, options=options)
    payload = {"type": "ask_user", "tool_call_id": runtime.tool_call_id,
               **prompt.model_dump(mode="json", exclude_none=True)}
    raw_reply = interrupt(payload)
    return prompt.validate_reply(raw_reply).model_dump(mode="json", exclude_none=True)
