# Deep Agent 项目交接状态

> 最后更新：2026-11-15（macOS → 单位 Windows 交接）
>
> 当前分支：`main`
>
> 7B 与记忆管理功能提交：`dfc744b 增加7A7B实现`

> 7A 功能基线：`a17a320 feat: complete single-user web chat`
>
> 7B-1 后端认证与隔离、7B-2 页面接入、Web 记忆管理及 macOS 环境配置改动已纳入上述提交。
> 单位电脑应拉取 `origin/main`，确认包含该功能提交及后续交接文档更新。

这份文档用于在另一台电脑或新的 Codex 任务中继续开发。它记录当前真实进度、
关键设计、运行方式、已知限制和下一步，不包含模型密钥或真实会话内容。

## 0. 明天在单位 Windows 电脑继续（2026-11-16）

### 0.1 先确认代码已经同步

7B 和记忆管理代码已经提交为 `dfc744b`。用户已要求将代码和交接文档推送到
`origin/main`。单位电脑更新后，确认日志包含该功能提交和后续交接文档更新，
不要只根据文件夹名称判断代码版本。

功能提交包含以下内容：

- 新增：`core/user_store.py`、`web/auth.py`、`manage_users.py`、`tests/`、`docs/plans/`。
- 修改：`core/agent_builder.py`、`core/backend_builder.py`、`core/memory_store.py`、
  `web/agent_stream.py`、`web/app.py`、`view/chat.html`、`view/login.html`、`view/shared.js`。
- 配置与文档：`config.py`、`core/llm_model.py`、`requirements.txt`、`README.md`、本文档。
- 工作记录：`task_plan.md`、`findings.md`、`progress.md`。

在单位电脑先检查本地改动和提交，再拉取；不要覆盖未确认的本地工作：

```powershell
git status --short
git log -4 --oneline
git pull --ff-only origin main
git log -4 --oneline
```

如果本地存在未提交工作或分支已分叉，应先检查差异，不要自动重置或强制覆盖。

### 0.2 复用单位原来的环境

单位电脑原环境已存在时，先激活并检查，不要重复创建：

```powershell
conda activate deep_agent
python --version
```

项目继续使用 Python 3.13。macOS 初始化时新增了 `python-dotenv==1.2.2`，用于读取
`.env`；单位环境如果缺少它或其他固定依赖，由用户手动执行
`python -m pip install -r requirements.txt`。Codex 不自行安装或升级。
Node 用于页面行为测试；如果单位电脑没有 Node，先说明测试限制，不自行安装。

### 0.3 重新配置本机模型和测试账号

`.env`、数据库和浏览器 Cookie 不随 Git 同步。必须在单位电脑重新配置模型并创建
Web 用户；具体变量见 2.3。当前代码读取的密钥变量是 `MINIMAX_API_KEY`，
不要只设置旧的 `DEEP_AGENT_MODEL_API_KEY` 就认为已配置成功。

用户已决定按全新项目处理，不做旧单用户数据库迁移。如果单位目录已有原来的
`data/` 数据，应先停止服务并备份，将旧数据库与本轮新的测试数据区分开；
不要自动删除，不要把旧会话直接分配给新用户。

```powershell
python manage_users.py create henry
python stage7_web.py
```

脚本提示输入两次密码，输入不回显。已有同名用户时会提示重复，不会覆盖。
访问 `http://127.0.0.1:8000` 应先进入登录页，登录后进入聊天页。

### 0.4 继续开发前先完成 Windows 实测

```powershell
python -m unittest discover -s tests -v
node --test tests/test_pages.js
```

记忆管理完成后，最新验证为 26 项 Python 测试、12 项 Node 页面行为测试通过。
自动测试使用临时数据库和假模型，不能代替真实模型连通性验证。

接下来优先检查：

1. 登录、错误密码提示、用户名显示、退出，以及未登录访问聊天页的跳转。
2. 真实模型普通问答、工具调用、思考展示（如启用）和上下文圆环。
3. 新建、继续、重启恢复、删除会话。
4. 记忆管理窗口查看、编辑、取消、保存、重新加载和清空；保存后新建会话验证加载，
   清空后确认历史聊天仍在。
5. 创建第二个账号，用不同浏览器或独立浏览器配置分别登录，验证会话与记忆隔离。
   同一浏览器配置中的多个标签页会共享 Cookie，不能当作两个独立登录用户。
6. 前端 Vue 和 Tailwind 通过 CDN 加载；若页面空白，检查单位网络能否加载页面脚本。

此前浏览器连接工具超时，尚未完成实际浏览器点击检查，也未调用真实模型。
先补齐这些验证，不要重新实现已经完成的 7B 和记忆管理。
用户已明确：本轮练习补齐记忆相关功能即可，其他细节和增强项暂不做。

## 1. 项目目标

这是一个 Deep Agents 学习工程，模型使用自部署的 Qwen/vLLM，
通过 OpenAI 兼容接口连接。工程从终端版 Agent 逐步扩展到 Web 版，
重点学习工具、Skill、记忆、会话持久化、流式输出和多用户隔离。

## 2. 在另一台电脑开始

### 2.1 获取代码

```powershell
git clone https://github.com/jiangyoon/deep_agent.git
cd deep_agent
```

然后在 Codex 中打开克隆后的 `deep_agent` 文件夹。

### 2.2 创建环境

原开发环境名称为 `deep_agent`，Python 版本为 3.13：

```powershell
conda create -n deep_agent python=3.13 -y
conda activate deep_agent
pip install -r requirements.txt
```

协作约定：除非用户明确要求 Codex 安装，否则 Codex 只给出依赖用途、版本和安装命令，
由用户手动安装。

### 2.3 配置模型

当前 `config.py` 通过 `_setting()` 从系统环境变量或项目根目录 `.env` 读取：

- `DEEP_AGENT_MODEL_BASE_URL` → `MODEL_BASE_URL`：OpenAI 兼容地址，末尾带 `/v1`。
- `DEEP_AGENT_MODEL_NAME` → `MODEL_NAME`：服务端模型 ID。
- `MINIMAX_API_KEY` → `MODEL_API_KEY`：当前实际启用的密钥变量。

`DEEP_AGENT_MODEL_API_KEY` 的读取行当前被注释，尚未实现两种密钥变量自动回退。
这是用户在 Mac 初始化环境时的配置改动，换回单位模型时需按实际服务确认，
不要擅自覆盖或把真实密钥写回代码。

上下文设置仍在 `config.py`：`MODEL_CONTEXT_TOKENS=16384`、
`MODEL_MAX_INPUT_TOKENS=8192`，应与实际模型服务能力核对。

当前 macOS 环境已经由用户手动初始化，Python 3.13 Conda 环境名为 `deep_agent`。
配置已支持系统环境变量和项目根目录 `.env`；具体使用的模型密钥变量以当前
`config.py` 的 `MODEL_API_KEY` 为准，不要覆盖用户正在使用的配置。

系统环境变量优先；未设置或为空时，使用 `.env` 中的值。
Windows PowerShell 当前窗口配置示例：

```powershell
$env:DEEP_AGENT_MODEL_BASE_URL = "https://你的模型服务/v1"
$env:DEEP_AGENT_MODEL_NAME = "服务端模型ID"
$env:MINIMAX_API_KEY = "你的密钥"
```

也可以在未被 Git 跟踪的 `.env` 中写相同变量。这里只记录变量名与示例，
没有读取或收录本机 `.env` 的实际内容。

不要把真实密钥写进 `config.py`、本文档或 Git。

### 2.4 启动 Web

```powershell
conda activate deep_agent
python stage7_web.py
```

浏览器打开：

```text
http://127.0.0.1:8000
```

健康检查：

```text
http://127.0.0.1:8000/health
```

## 3. 当前阶段

已完成：

1. 环境与模型连通
2. 最小 Agent、流式输出、思考模式
3. 自定义工具
4. 预置只读 Skill
5. 长期记忆
6. 会话持久化
7A. 单用户 Web

7A 已细分完成：

- 7A-1：FastAPI、Uvicorn、静态页面和健康检查
- 7A-2：非流式问答接口
- 7A-3：SSE 流式回答
- 7A-4：思考过程、工具调用和工具结果展示
- 7A-5：单用户会话列表、新建、继续、删除和 SQLite 持久化
- 7A-5.1：输入框旁显示当前会话上下文占比圆环

7B 已完成：

- 7B-1 已完成后端登录、用户创建脚本、会话归属和长期记忆隔离。
- 7B-2 已完成登录页面接入、当前用户与退出、登录过期提示。
- Web 记忆管理已完成：查看、全文编辑、保存、取消和清空。
- 用户确定其他增强项暂不做，接下来仅验证和解释现有功能。

`README.md` 已补充 7B 使用方式。详细设计见 `docs/plans/2026-11-14-7b-design.md`，页面接入计划见 `docs/plans/2026-11-14-7b-pages.md`。

## 4. 当前系统结构

```text
浏览器 view/chat.html
        │
        │ HTTP + SSE
        ▼
FastAPI web/app.py
        │
        ├── web/auth.py               Cookie 登录与身份检查
        │       └── core/user_store.py
        │               └── data/users.db（用户、登录凭证摘要、会话归属）
        │
        ├── web/agent_stream.py       将 Agent 流转换成 SSE 事件
        │
        ├── core/agent_builder.py     组装模型、工具、Skill、记忆、会话
        │
        ├── core/memory_store.py      长期记忆
        │       └── data/memory.db
        │
        └── core/session_store.py     对话检查点
                └── data/sessions.db
```

三个数据库职责不同：

- `users.db`：用户、登录 Session 和聊天会话归属，由 `core/user_store.py` 管理。
- `memory.db`：跨会话长期记忆。
- `sessions.db`：某个会话的消息和检查点，用 `thread_id` 继续对话。

这些数据库含账号信息、凭证摘要或用户原文，默认被 Git 忽略，不会自动同步到另一台电脑。
本次按新项目接续，不要求搬运 Mac 的账号和会话数据。

## 5. Web 入口与接口

主要入口：

- `stage7_web.py`：启动 Uvicorn
- `web/app.py`：FastAPI 路由、Agent 生命周期、会话接口
- `web/agent_stream.py`：思考、工具、回答和 usage 的 SSE 转换
- `view/chat.html`：Vue 3 聊天工作台
- `view/shared.js`：共享 Vue API、图标和认证请求包装器 `apiFetch()`
- `view/login.html`：真实登录、失败和过期提示
- `web/auth.py`：登录、退出、当前用户和同源检查
- `manage_users.py`：通过隐藏密码输入手动创建用户

当前主要接口：

| 方法 | 路径 | 作用 |
|---|---|---|
| `GET` | `/health` | 服务健康检查 |
| `GET` | `/` | 聊天页面 |
| `POST` | `/api/auth/login` | 校验账号密码并设置 7 天 Cookie |
| `POST` | `/api/auth/logout` | 撤销登录凭证并清除 Cookie |
| `GET` | `/api/auth/me` | 获取当前用户 |
| `GET` | `/api/memory` | 获取当前用户主记忆、版本、字符数与更新时间 |
| `PUT` | `/api/memory` | 校验版本后保存当前用户记忆 |
| `DELETE` | `/api/memory` | 校验版本后恢复当前用户的空记忆模板 |
| `POST` | `/api/sessions` | 生成新会话编号 |
| `GET` | `/api/sessions` | 列出持久化会话 |
| `GET` | `/api/sessions/{id}` | 读取会话消息与上下文占用 |
| `DELETE` | `/api/sessions/{id}` | 删除会话全部检查点 |
| `POST` | `/api/sessions/{id}/stream` | 在指定会话中流式提问 |

`/api/chat` 和 `/api/chat/stream` 已移除。所有会话 API 均要求登录。
未登录返回 401；无归属或不存在返回 404；生成执行链忙时返回 409。

## 6. 会话上下文圆环

页面输入框左侧有一个圆环，显示当前会话占模型上下文窗口的比例：

```text
占用 token = 最近一次模型调用的 input_tokens + output_tokens
占比 = 占用 token / MODEL_CONTEXT_TOKENS
```

不能把每轮调用的 token 相加，因为后续调用的输入已经包含之前的会话历史，
相加会重复计算。

颜色规则：

- 低于 65%：紫色
- 65% 到 85%：橙色
- 85% 以上：红色

鼠标悬停圆环会显示 `已用 token / 上限 token`。

实时回答时使用 `StreamUsageTracker` 修正流式 usage；重新打开历史会话时，
后端从最后一条带 `usage_metadata` 的消息恢复占比。部分 vLLM 服务会在每个流式
chunk 重复上报 usage，因此实时值与重新打开后的数据库原始值可能略有差异。

## 7. 已知行为和限制

### 7.1 身份与隔离

- 用户通过 `python manage_users.py create 用户名` 手动创建。
- 用户名唯一，密码使用随机盐和 PBKDF2-HMAC-SHA256（600000 次）哈希。
- 服务端登录凭证有效期 7 天，数据库只保存凭证摘要；Cookie 为 HttpOnly、SameSite=Lax。
- 7 天从登录时计算，普通请求不会自动续期；退出和重新登录会撤销当前旧凭证。
- 会话归属在创建时写入 users.db，读取、继续和删除由后端校验；记忆按服务端用户 ID 分组。
- 不做旧数据兼容或迁移；终端学习入口与 Web 用户记忆独立。
- 未登录访问聊天页跳转登录页，登录后显示用户名并可退出；401 时提示过期并停止后续匿名请求。
- 当前仅同时允许一条生成请求，其他生成或删除请求收到 409；不阻塞线程池等待。
- HTTPS 部署可设置 `DEEP_AGENT_COOKIE_SECURE=true`；当前仍按本地学习工程运行。
- 当前本地 HTTP 测试保持 `DEEP_AGENT_COOKIE_SECURE` 未设置或为 false；启用前先确认 HTTPS 访问方式。

### 7.2 历史会话显示

- 历史页面只恢复用户问题和 Agent 最终回答。
- 工具调用、工具结果和思考内容可能已经在检查点中，但页面尚未重建这些步骤。
- token usage 不作为每轮问答步骤展示，只在会话圆环显示。

### 7.3 记忆加载

同一个会话中的记忆是在会话开始时加载并随检查点保存的。会话进行期间即使长期记忆
发生变化，旧会话也不一定看到新内容；新建会话才能稳定加载最新记忆。

### 7.4 停止生成

浏览器的“停止生成”会中止浏览器 Fetch。模型端或后端已经开始的生成不保证立即停止，
需要后续考虑服务端取消机制。

### 7.5 自动压缩

`MODEL_MAX_INPUT_TOKENS=8192` 是框架的输入阈值配置，不是模型完整上下文上限。
设置为 16K 的一半，是因为框架估算中文 token 时可能偏小，需要提前触发压缩并给输出留空间。

## 8. 阶段 7B：登录和多用户隔离

本轮按以下两步完成：

### 7B-1：后端身份与隔离（已完成）

已实现：

1. 用户表和密码哈希。
2. 登录、退出、当前用户接口。
3. 使用安全 Cookie 或服务端 Session 保存登录状态。
4. 每个会话必须记录所属用户。
5. 读取、继续、删除会话时校验所有权。
6. 长期记忆按用户隔离。
7. 终端或自动测试验证用户 A 无法读取用户 B 的数据。

验证：`python -m unittest discover -s tests -v`，使用临时 SQLite 数据库和本地假模型，覆盖真实 Agent 文件工具和 SSE，不发送真实模型请求。新增标准库代码，无额外依赖。

隔离至少覆盖：

```text
用户
├── 自己的长期记忆
└── 自己的多个会话
```

不能只在前端隐藏其他用户数据，权限检查必须在后端和存储层执行。

### 7B-2：页面接入（已完成）

已实现：

1. 将现有 `view/login.html` 接入真实登录接口。
2. 未登录访问聊天页时跳转登录页。
3. 聊天页显示当前用户并支持退出。
4. 会话列表只展示当前用户的数据。
5. 登录过期后给出清晰提示，不继续发送匿名请求。

本轮先确认用户模型、Cookie 策略和数据库结构，再记录设计与计划并实施。

7B 原验证为 19 项 Python 测试和 6 项 Node 页面行为测试。记忆管理完成后的最新总验证见第 9 节。

## 9. Web 记忆管理（已完成）

范围：只管理当前用户的 `/memories/AGENTS.md`，支持查看、全文编辑、保存、取消、
重新加载、清空，并显示长度和更新时间。侧栏“记忆管理”和手机端顶部“记忆”打开
浏览器原生对话框。

- `core/memory_store.py` 的 `memory_snapshot()` 返回正文、版本指纹、字符数和更新时间。
- `web/app.py` 的 GET/PUT/DELETE `/api/memory` 使用服务端登录用户，不能指定其他用户。
- PUT/DELETE 携带读取时的 `revision`；获得现有运行锁后再检查指纹，变化时返回 409，
  防止覆盖另一个窗口或 Agent 的更新。当前单进程服务中，生成期间也不允许手动写入。
- 保存最多 100000 字符，前端有长度限制，后端再次校验；查看不需要创建模型或调用模型。
- 初始没有记忆时显示为空；清空需要确认，确认后恢复空模板，以便 Agent 的 `edit_file` 继续工作。
- 保存失败、版本冲突和重新加载失败均保留草稿；重新加载或关闭窗口丢弃草稿时需要确认。
- 修改后的记忆由新会话稳定加载；清空不删除已有聊天或检查点中的内容。
- `manage_memory.py` 仍只管理终端独立记忆分组；Web 用户使用页面管理，不要求扩展该旧脚本。
- 本轮不做导入导出、历史版本、会话重命名等其他增强。

设计和步骤：`docs/plans/2026-11-15-memory-management.md`。
最新自动验证：26 项 Python 测试、12 项 Node 页面行为测试通过；40 个 Python 文件编译、
页面 JavaScript 语法和 `git diff --check` 通过，独立评审未发现需修复问题。
测试还通过真实 Agent 配合本地假模型验证了保存内容进入新会话系统提示、清空后不再加载原内容、
历史聊天保留。未使用真实用户记忆或调用真实模型，实际浏览器点击检查仍待补齐。

## 10. 开发与验证约定

- 用户要求：每次修改代码后，都要解释新增或修改的代码。
- 不要未经明确要求自动安装或升级依赖。
- 使用现有固定版本，升级前先确认 Deep Agents、LangGraph 和思考内容兼容性。
- 修改 Web 后至少检查：
  - Python 文件能否编译
  - 浏览器内联 JavaScript 能否通过语法检查
  - 会话新建、继续、重启恢复和删除
  - 普通回答、工具调用、记忆和上下文圆环
- 不要提交：
  - `.env`
  - `data/*.db`
  - API Key
  - 用户真实对话和记忆
  - 缓存、日志和临时测试文件

## 11. 给新 Codex 的建议开场指令

在另一台电脑打开仓库后，可以直接告诉 Codex：

```text
请先完整阅读 PROJECT_STATUS.md 和 README.md，熟悉当前工程。
当前 7B 登录和多用户隔离、Web 记忆管理已实现，请先阅读工作区改动，再根据我的下一步需求继续。
这是从个人 Mac 切回单位 Windows；请先按文档第 0 节检查代码同步、环境和模型配置。
注意当前 MODEL_API_KEY 实际读取 MINIMAX_API_KEY，不要直接沿用旧密钥变量假设。
先补齐 Windows 浏览器与真实模型联调，用两个独立浏览器登录测试账号验证隔离。
不要重做 7B 或记忆管理，也不要自动删除或迁移单位电脑原有数据库。
本轮练习只补齐记忆相关功能，其他增强项暂不做。
不要自行安装或升级依赖；如果需要依赖，说明用途、版本和命令，由我安装。
每次修改代码后解释新代码，并在提交前运行与改动相称的测试。
```
