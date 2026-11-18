# 发现

## 记忆管理

- 用户明确本轮只补齐记忆管理，其他增强暂不做。
- 原有 manage_memory.py 不传 user_id，仅管理终端 namespace；本轮以 Web 页管理当前用户主记忆。
- 新 API 复用生成运行锁并校验 revision；清空恢复模板，不删除聊天历史。
- 浏览器原生 dialog 提供模态交互，不增加前端依赖。

- Conda deep_agent 环境已存在，Python 3.13.18，既有依赖可用，包含 httpx。
- 用户已添加 dotenv 支持，不能覆盖已有配置改动。
- StoreBackend namespace 工厂接收 Runtime，可通过 runtime.context 传递服务器用户身份。
- 7B-1 不接入前端；测试用 TestClient 登录。
- 独立评审发现同步 SSE 跨 yield 等待全局锁可能耗尽 AnyIO 线程池；改用非阻塞依赖锁和 409。当前仅支持同时一条生成请求，其他用户/会话也会收到忙提示。
