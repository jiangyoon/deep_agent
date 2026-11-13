"""阶段 7A-1 入口：启动本地 FastAPI 服务。"""

import uvicorn


if __name__ == "__main__":
    uvicorn.run("web.app:app", host="127.0.0.1", port=8000, reload=False)