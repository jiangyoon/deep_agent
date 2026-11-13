"""FastAPI 应用：阶段 7A-5.1 的单用户持久化会话 Web 接口。"""

import logging
import threading
import uuid
from contextlib import asynccontextmanager
from functools import lru_cache
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

import config
from core.agent_builder import build_agent
from core.llm_model import create_llm
from core.memory_store import open_store, seed_memory_if_missing
from core.session_store import (
    delete_session,
    get_session_messages,
    list_sessions,
    make_config,
    new_session_id,
    open_checkpointer,
    session_exists,
)
from display.printer import content_to_text
from web.agent_stream import stream_answer


PROJECT_DIR = Path(__file__).resolve().parent.parent
VIEW_DIR = PROJECT_DIR / "view"

logger = logging.getLogger(__name__)
session_agent_lock = threading.Lock()


@asynccontextmanager
async def lifespan(application: FastAPI):
    """Web 服务运行期间持续保持记忆库和会话库连接。"""
    with open_store() as store, open_checkpointer() as saver:
        if config.USE_MEMORY:
            seed_memory_if_missing(store)

        application.state.memory_store = store
        application.state.session_saver = saver
        application.state.session_agent = None
        yield


app = FastAPI(
    title="Deep Agent",
    version="7A-5.1",
    lifespan=lifespan,
)


class ChatRequest(BaseModel):
    """单轮问答请求。"""

    question: str = Field(min_length=1, max_length=10000)


class ChatResponse(BaseModel):
    """单轮问答响应。"""

    answer: str


class SessionResponse(BaseModel):
    """浏览器使用的会话概要。"""

    id: str
    title: str
    message_count: int


@lru_cache(maxsize=1)
def get_agent():
    """首次提问时创建 Agent，后续请求复用同一个无会话 Agent。"""
    llm = create_llm()
    return build_agent(llm)


def get_session_agent(request: Request):
    """创建或复用带记忆和检查点保存器的 Web Agent。"""
    agent = request.app.state.session_agent
    if agent is not None:
        return agent

    with session_agent_lock:
        agent = request.app.state.session_agent
        if agent is None:
            llm = create_llm()
            agent = build_agent(
                llm,
                use_skills=config.USE_SKILLS,
                use_memory=config.USE_MEMORY,
                store=request.app.state.memory_store,
                checkpointer=request.app.state.session_saver,
            )
            request.app.state.session_agent = agent
    return agent


def message_role(message) -> str | None:
    """把 LangChain 消息类型转换成前端角色。"""
    message_type = getattr(message, "type", "")
    if message_type == "human":
        return "user"
    if message_type == "ai":
        return "agent"
    return None


def browser_messages(messages) -> list[dict[str, str]]:
    """只返回浏览器需要展示的用户消息和有正文的 AI 消息。"""
    result = []
    for message in messages:
        role = message_role(message)
        if role is None:
            continue
        text = content_to_text(message.content).strip()
        if text == "":
            continue
        result.append({"role": role, "content": text})
    return result


def session_context_usage(messages) -> dict:
    """用最近一次模型调用的用量表示当前会话上下文占用。

    不能把历次调用的 token 相加，因为每次调用的 input_tokens
    已经包含前面的会话历史，累加会重复计算同一批消息。
    """
    context_limit = getattr(config, "MODEL_CONTEXT_TOKENS", 0) or 0
    input_tokens = 0
    output_tokens = 0

    for message in reversed(messages):
        usage = getattr(message, "usage_metadata", None)
        if not usage:
            continue
        input_tokens = usage.get("input_tokens") or 0
        output_tokens = usage.get("output_tokens") or 0
        break

    used_tokens = input_tokens + output_tokens
    percent = used_tokens * 100 / context_limit if context_limit else 0
    return {
        "used_tokens": used_tokens,
        "limit_tokens": context_limit,
        "percent": percent,
    }


def session_title(messages, session_id: str) -> str:
    """用第一条用户消息生成侧栏标题。"""
    for message in messages:
        if message_role(message) != "user":
            continue
        text = " ".join(content_to_text(message.content).split())
        if text == "":
            continue
        if len(text) > 28:
            return text[:28] + "..."
        return text
    return session_id


def view_file(name: str) -> FileResponse:
    """返回预置页面或脚本；文件缺失时给出明确错误。

    开发阶段关掉缓存，避免改了 shared.js / html 后浏览器仍用旧文件，
    出现 IconXxx is not defined 这类莫名错误。
    """
    path = VIEW_DIR / name
    if not path.is_file():
        raise RuntimeError("找不到 Web 视图文件：" + str(path))
    return FileResponse(
        path,
        headers={"Cache-Control": "no-store, max-age=0"},
    )


@app.get("/", include_in_schema=False)
def chat_page() -> FileResponse:
    """返回单用户聊天页面。"""
    return view_file("chat.html")


@app.get("/login", include_in_schema=False)
def login_page() -> FileResponse:
    """预留登录页；认证会在阶段 7B 接入。"""
    return view_file("login.html")


@app.get("/shared.js", include_in_schema=False)
def shared_script() -> FileResponse:
    """为两个现有页面提供共享图标脚本。"""
    return view_file("shared.js")


@app.get("/health")
def health() -> dict[str, str]:
    """供浏览器和部署探针确认 Web 服务已启动。"""
    return {"status": "ok", "stage": "7A-5.1"}


@app.post("/api/chat", response_model=ChatResponse)
def chat(request: ChatRequest) -> ChatResponse:
    """执行一次独立问答；本阶段暂不保存会话历史。"""
    question = request.question.strip()
    if question == "":
        raise HTTPException(status_code=422, detail="问题不能为空")

    try:
        result = get_agent().invoke(
            {"messages": [{"role": "user", "content": question}]}
        )
        messages = result.get("messages", [])
        if not messages:
            raise RuntimeError("Agent 没有返回消息")

        answer = content_to_text(messages[-1].content).strip()
        if answer == "":
            raise RuntimeError("Agent 返回了空回答")
        return ChatResponse(answer=answer)
    except HTTPException:
        raise
    except Exception:
        logger.exception("Web 单轮问答失败")
        raise HTTPException(
            status_code=503,
            detail="模型调用失败，请检查服务端日志和模型配置",
        ) from None


@app.post("/api/chat/stream")
def chat_stream(request: ChatRequest) -> StreamingResponse:
    """以 SSE 事件流返回一次独立问答的回答正文。"""
    question = request.question.strip()
    if question == "":
        raise HTTPException(status_code=422, detail="问题不能为空")

    try:
        agent = get_agent()
    except Exception:
        logger.exception("创建 Web Agent 失败")
        raise HTTPException(
            status_code=503,
            detail="模型初始化失败，请检查服务端日志和模型配置",
        ) from None

    return StreamingResponse(
        stream_answer(agent, question),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/api/sessions", response_model=SessionResponse)
def create_session() -> SessionResponse:
    """生成一个新的 Web 会话编号；首次提问后才会写入检查点。"""
    session_id = new_session_id() + "-" + uuid.uuid4().hex[:6]
    return SessionResponse(
        id=session_id,
        title="新会话",
        message_count=0,
    )


@app.get("/api/sessions", response_model=list[SessionResponse])
def get_sessions(request: Request) -> list[SessionResponse]:
    """列出当前单用户数据库中的全部持久化会话。"""
    saver = request.app.state.session_saver
    session_ids = list_sessions(saver)
    if not session_ids:
        return []

    agent = get_session_agent(request)
    result = []
    for session_id in session_ids:
        try:
            messages = get_session_messages(agent, session_id)
            visible_messages = browser_messages(messages)
            result.append(
                SessionResponse(
                    id=session_id,
                    title=session_title(messages, session_id),
                    message_count=len(visible_messages),
                )
            )
        except Exception:
            logger.exception("读取会话失败：%s", session_id)
    return result


@app.get("/api/sessions/{session_id}")
def get_session(request: Request, session_id: str) -> dict:
    """读取一个会话的可展示消息。"""
    saver = request.app.state.session_saver
    if not session_exists(saver, session_id):
        raise HTTPException(status_code=404, detail="没有这个会话")

    agent = get_session_agent(request)
    messages = get_session_messages(agent, session_id)
    return {
        "id": session_id,
        "messages": browser_messages(messages),
        "context_usage": session_context_usage(messages),
    }


@app.delete("/api/sessions/{session_id}", status_code=204)
def remove_session(request: Request, session_id: str) -> None:
    """删除一个会话的全部检查点。"""
    saver = request.app.state.session_saver
    if not session_exists(saver, session_id):
        raise HTTPException(status_code=404, detail="没有这个会话")
    delete_session(saver, session_id)


@app.post("/api/sessions/{session_id}/stream")
def session_chat_stream(
    request: Request,
    session_id: str,
    chat_request: ChatRequest,
) -> StreamingResponse:
    """在指定会话中提问，并将本轮状态写入检查点数据库。"""
    question = chat_request.question.strip()
    if question == "":
        raise HTTPException(status_code=422, detail="问题不能为空")

    try:
        agent = get_session_agent(request)
    except Exception:
        logger.exception("创建持久化 Web Agent 失败")
        raise HTTPException(
            status_code=503,
            detail="模型初始化失败，请检查服务端日志和模型配置",
        ) from None

    return StreamingResponse(
        stream_answer(
            agent,
            question,
            run_config=make_config(session_id),
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
