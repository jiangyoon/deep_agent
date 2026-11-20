"""职业规划助手的 Web 入口。"""

import logging
import sys
import threading
from contextlib import asynccontextmanager
from pathlib import Path

# 直接运行本文件时，解释器只把 career_agent/web 放入搜索路径。
if __name__ == "__main__" and __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, RedirectResponse, StreamingResponse
from pydantic import BaseModel, Field

from career_agent import config
from career_agent.agent.builder import build_agent
from career_agent.core.app_db import open_app_pool
from career_agent.core.llm_model import create_llm
from career_agent.core.memory_store import (
    MEMORY_TEMPLATE, create_memory_store, memory_snapshot, seed_memory_if_missing, write_memory,
)
from career_agent.core.messages import content_to_text
from career_agent.core.session_store import (
    create_checkpointer,
    delete_session,
    get_session_messages,
    make_config,
    session_exists,
)
from career_agent.core.user_store import UserStore
from career_agent.knowledge.tools import create_kb1_tools
from career_agent.web.agent_stream import stream_answer
from career_agent.web.chat_history import browser_message_count, browser_messages
from career_agent.web.auth import current_user, require_same_origin, router as auth_router


VIEW_DIR = Path(__file__).resolve().parent / "views"

logger = logging.getLogger(__name__)
session_agent_lock = threading.Lock()


@asynccontextmanager
async def lifespan(application: FastAPI):
    """Web 服务运行期间共享应用数据库连接池。"""
    with open_app_pool() as pool:
        application.state.user_store = UserStore(pool)
        application.state.kb1_tools = create_kb1_tools(pool)
        # 同步执行链串行运行，避免生成与删除同一会话相互覆盖。
        application.state.session_run_lock = threading.Lock()
        application.state.memory_store = create_memory_store(pool)
        application.state.session_saver = create_checkpointer(pool)
        application.state.session_agent = None
        yield


app = FastAPI(
    title="员工职业规划助手",
    version="7B-2",
    dependencies=[Depends(require_same_origin)],
    lifespan=lifespan,
)

app.include_router(auth_router)


@app.middleware("http")
async def private_api_cache(request: Request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


class ChatRequest(BaseModel):
    """单轮问答请求。"""

    question: str = Field(min_length=1, max_length=10000)


class SessionResponse(BaseModel):
    """浏览器使用的会话概要。"""

    id: str
    title: str
    message_count: int


class MemoryRevision(BaseModel):
    revision: str = Field(pattern=r"^[0-9a-f]{64}$")


class MemoryUpdate(MemoryRevision):
    content: str = Field(max_length=100000, pattern=r"^[^\x00]*$")


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
                tools=request.app.state.kb1_tools,
                use_skills=config.USE_SKILLS,
                use_memory=config.USE_MEMORY,
                store=request.app.state.memory_store,
                checkpointer=request.app.state.session_saver,
                require_user=True,
            )
            request.app.state.session_agent = agent
    return agent


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
        if message.type != "human" or message.additional_kwargs.get("lc_source") == "pinned_skill":
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
def chat_page(request: Request) -> Response:
    """未登录时跳转；聊天 API 仍独立验证身份。"""
    token = request.cookies.get(config.AUTH_COOKIE_NAME, "")
    if not token or request.app.state.user_store.resolve_login(token) is None:
        return RedirectResponse("/login", status_code=303)
    return view_file("chat.html")


@app.get("/login", include_in_schema=False)
def login_page(request: Request) -> Response:
    token = request.cookies.get(config.AUTH_COOKIE_NAME, "")
    if token and request.app.state.user_store.resolve_login(token) is not None:
        return RedirectResponse("/", status_code=303)
    return view_file("login.html")


@app.get("/shared.js", include_in_schema=False)
def shared_script() -> FileResponse:
    """为两个现有页面提供共享图标脚本。"""
    return view_file("shared.js")


@app.get("/chat-activity.js", include_in_schema=False)
def chat_activity_script() -> FileResponse:
    return view_file("chat-activity.js")


VENDOR_FILES = {
    "vue.global.prod.js": "application/javascript",
    "tailwind-browser.js": "application/javascript",
}


@app.get("/vendor/{name}", include_in_schema=False)
def vendor_script(name: str) -> FileResponse:
    """登录页和对话页共用的本地前端库，避免首屏依赖外部 CDN。"""
    media_type = VENDOR_FILES.get(name)
    path = VIEW_DIR / "vendor" / name
    if media_type is None or not path.is_file():
        raise HTTPException(status_code=404, detail="没有这个文件")
    return FileResponse(
        path,
        media_type=media_type,
        headers={"Cache-Control": "public, max-age=86400"},
    )


@app.get("/health")
def health() -> dict[str, str]:
    """供浏览器和部署探针确认 Web 服务已启动。"""
    return {"status": "ok", "stage": "7B-2"}


def require_owned_chat(request: Request, user: dict, session_id: str):
    """归属查询包含 user_id；不向其他用户泄露会话是否存在。"""
    if not request.app.state.user_store.owns_chat(user["id"], session_id):
        raise HTTPException(status_code=404, detail="没有这个会话")


def owned_session_user(request: Request, session_id: str,
                       user: dict = Depends(current_user)) -> dict:
    require_owned_chat(request, user, session_id)
    return user


def session_operation(request: Request):
    """锁的生存期覆盖整个响应；不阻塞等待，避免占满流式线程池。"""
    lock = request.app.state.session_run_lock
    if not lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="对话生成或数据更新正在进行，请稍后重试")
    try:
        yield
    finally:
        lock.release()


@app.get("/api/context-config")
def get_context_config(user: dict = Depends(current_user)) -> dict:
    """前端上下文仪表盘的分母（模型上下文窗口）统一下发，不在页面里写死。"""
    limit = getattr(config, "MODEL_CONTEXT_TOKENS", 0) or 0
    return {"limit_tokens": limit}


@app.get("/api/memory")
def get_memory(request: Request, user: dict = Depends(current_user)) -> dict:
    return memory_snapshot(request.app.state.memory_store, user["id"])


def check_memory_revision(request: Request, user: dict, revision: str):
    current = memory_snapshot(request.app.state.memory_store, user["id"])
    if revision != current["revision"]:
        raise HTTPException(status_code=409, detail="记忆已被其他操作更新，请保留草稿并重新加载后再编辑")


@app.put("/api/memory")
def update_memory(body: MemoryUpdate, request: Request,
                  user: dict = Depends(current_user),
                  operation=Depends(session_operation, scope="request")) -> dict:
    check_memory_revision(request, user, body.revision)
    write_memory(request.app.state.memory_store, body.content, user["id"])
    return memory_snapshot(request.app.state.memory_store, user["id"])


@app.delete("/api/memory")
def reset_memory(body: MemoryRevision, request: Request,
                 user: dict = Depends(current_user),
                 operation=Depends(session_operation, scope="request")) -> dict:
    check_memory_revision(request, user, body.revision)
    # 保留可编辑的空模板，后续 Agent 的 edit_file 仍能正常使用。
    write_memory(request.app.state.memory_store, MEMORY_TEMPLATE, user["id"])
    return memory_snapshot(request.app.state.memory_store, user["id"])


def chat_messages(request: Request, session_id: str):
    if not session_exists(request.app.state.session_saver, session_id):
        return []
    return get_session_messages(get_session_agent(request), session_id)


@app.post("/api/sessions", response_model=SessionResponse)
def create_session(request: Request, user: dict = Depends(current_user)) -> SessionResponse:
    """创建会话时立即记录归属；尚未提问的空会话也会出现在列表中。"""
    session_id = request.app.state.user_store.create_chat(user["id"])
    return SessionResponse(id=session_id, title="新会话", message_count=0)


@app.get("/api/sessions", response_model=list[SessionResponse])
def get_sessions(request: Request, user: dict = Depends(current_user)) -> list[SessionResponse]:
    result = []
    for session_id in request.app.state.user_store.list_chats(user["id"]):
        messages = chat_messages(request, session_id)
        result.append(SessionResponse(
            id=session_id,
            title=session_title(messages, session_id) if messages else "新会话",
            message_count=browser_message_count(messages),
        ))
    return result


@app.get("/api/sessions/{session_id}")
def get_session(request: Request, session_id: str, user: dict = Depends(current_user)) -> dict:
    require_owned_chat(request, user, session_id)
    messages = chat_messages(request, session_id)
    return {
        "id": session_id,
        "messages": browser_messages(messages),
        "context_usage": session_context_usage(messages),
    }


@app.delete("/api/sessions/{session_id}", status_code=204)
def remove_session(request: Request, session_id: str,
                   user: dict = Depends(owned_session_user),
                   operation=Depends(session_operation, scope="request")) -> None:
    require_owned_chat(request, user, session_id)
    # 先清检查点，再清归属；中途失败时保留归属，允许用户重试删除。
    delete_session(request.app.state.session_saver, session_id)
    request.app.state.user_store.delete_chat(user["id"], session_id)


@app.post("/api/sessions/{session_id}/stream")
def session_chat_stream(
    request: Request,
    session_id: str,
    chat_request: ChatRequest,
    user: dict = Depends(owned_session_user),
    operation=Depends(session_operation, scope="request"),
) -> StreamingResponse:
    # 获取运行锁后再次核对，防止依赖校验与加锁之间会话已被删除。
    require_owned_chat(request, user, session_id)
    question = chat_request.question.strip()
    if question == "":
        raise HTTPException(status_code=422, detail="问题不能为空")
    try:
        agent = get_session_agent(request)
    except Exception:
        logger.exception("创建持久化 Web Agent 失败")
        raise HTTPException(status_code=503, detail="模型初始化失败，请检查服务端日志和模型配置") from None

    def owned_stream():
        if config.USE_MEMORY:
            seed_memory_if_missing(request.app.state.memory_store, user_id=user["id"])
        yield from stream_answer(
            agent, question, run_config=make_config(session_id),
            context={"user_id": user["id"]},
        )

    return StreamingResponse(
        owned_stream(), media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)
