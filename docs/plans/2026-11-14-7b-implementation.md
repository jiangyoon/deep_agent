# 7B-1 Implementation Plan

**Goal:** 实现手动创建用户、7 天登录和后端用户数据隔离。

**Architecture:** users.db 管理身份、登录和会话所有权；sessions.db 管理检查点；memory.db 使用用户 namespace。后端校验身份后再访问 Agent。

**Tech Stack:** Python 3.13、sqlite3、hashlib、secrets、FastAPI、LangGraph、unittest。

## Task 1 — 用户、登录和归属存储

Files: core/user_store.py, manage_users.py, config.py, tests/test_users.py。

1. 先写临时库测试：密码不明文保存、重复用户、密码校验、过期和退出、会话归属。
2. 执行 `python -m unittest discover -s tests -v`，确认因缺少实现失败。
3. 实现每操作独立连接和事务、PBKDF2 密码、随机凭证摘要、归属 CRUD；实现隐藏密码输入脚本。
4. 重跑测试通过。

## Task 2 — 记忆隔离

Files: core/memory_store.py, core/backend_builder.py, core/agent_builder.py, tests/test_memory_isolation.py。

1. 先验证两个用户的记忆互不覆盖，Web 缺少用户身份时拒绝读取共享记忆。
2. 执行测试确认失败。
3. 实现运行上下文传递 user_id、独立 namespace 和用户记忆预置；保留终端入口独立默认值。
4. 重跑测试通过。

## Task 3 — Web 认证和会话权限

Files: web/auth.py, web/app.py, tests/test_web_auth.py。

1. 先写接口测试：未登录 401、正确/错误登录、Cookie 属性、退出/过期、跨源拒绝、跨用户读/续聊/删除 404、空会话和重启恢复。
2. 执行测试确认失败。
3. 实现依赖、登录/退出/me 和归属检查，移除旧无会话接口。
4. 重跑全部测试，使用真实 SQLite 和框架验证记忆/检查点。

## Task 4 — 验证、说明和评审

Files: README.md, PROJECT_STATUS.md。

更新运行步骤和阶段状态；运行全部 unittest、Python 编译、内联 JavaScript 语法检查和 diff 检查。请求独立代码评审，修复实际问题。向用户说明代码及页面尚未接入登录的范围。
