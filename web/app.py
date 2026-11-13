"""FastAPI 应用：阶段 7A-1 的服务与静态页面入口。"""

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse


PROJECT_DIR = Path(__file__).resolve().parent.parent
VIEW_DIR = PROJECT_DIR / "view"

app = FastAPI(title="Deep Agent", version="7A-1")


def view_file(name: str) -> FileResponse:
    """返回预置页面或脚本；文件缺失时给出明确错误。"""
    path = VIEW_DIR / name
    if not path.is_file():
        raise RuntimeError("找不到 Web 视图文件：" + str(path))
    return FileResponse(path)


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
    return {"status": "ok", "stage": "7A-1"}