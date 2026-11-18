"""Cookie 身份验证。所有用户身份均来自服务端 Session。"""

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from career_agent import config


def require_same_origin(request: Request):
    """浏览器写请求只接受本站；无 Origin 的命令行客户端可以正常调用。"""
    if request.method in {"GET", "HEAD", "OPTIONS"}:
        return
    origin = request.headers.get("origin")
    expected = str(request.base_url).rstrip("/")
    if (origin is not None and origin != expected) or (
        request.headers.get("sec-fetch-site") == "cross-site"
    ):
        raise HTTPException(status_code=403, detail="不允许跨站请求")


def current_user(request: Request) -> dict:
    token = request.cookies.get(config.AUTH_COOKIE_NAME, "")
    user = request.app.state.user_store.resolve_login(token) if token else None
    if user is None:
        raise HTTPException(status_code=401, detail="请先登录，或登录已过期")
    return user


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)


router = APIRouter(prefix="/api/auth", tags=["认证"])


@router.post("/login")
def login(body: LoginRequest, request: Request, response: Response) -> dict:
    store = request.app.state.user_store
    user = store.authenticate(body.username, body.password)
    if user is None:
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    token = store.create_login(user["id"])
    old_token = request.cookies.get(config.AUTH_COOKIE_NAME)
    if old_token:
        store.delete_login(old_token)
    response.set_cookie(
        config.AUTH_COOKIE_NAME, token,
        max_age=config.AUTH_SESSION_TTL_SECONDS,
        httponly=True, secure=config.AUTH_COOKIE_SECURE,
        samesite="lax", path="/",
    )
    response.headers["Cache-Control"] = "no-store"
    return user


@router.get("/me")
def me(response: Response, user: dict = Depends(current_user)) -> dict:
    response.headers["Cache-Control"] = "no-store"
    return user


@router.post("/logout", status_code=204)
def logout(request: Request, response: Response, user: dict = Depends(current_user)) -> None:
    request.app.state.user_store.delete_login(request.cookies[config.AUTH_COOKIE_NAME])
    response.delete_cookie(config.AUTH_COOKIE_NAME, path="/", httponly=True,
                           secure=config.AUTH_COOKIE_SECURE, samesite="lax")
    response.headers["Cache-Control"] = "no-store"
