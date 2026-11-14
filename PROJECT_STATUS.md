# Deep Agent 项目交接状态

> 最后更新：2026-11-14
>
> 当前分支：`main`
>
> 交接基线提交：`a17a320 feat: complete single-user web chat`

这份文档用于在另一台电脑或新的 Codex 任务中继续开发。它记录当前真实进度、
关键设计、运行方式、已知限制和下一步，不包含模型密钥或真实会话内容。

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

在 `config.py` 中设置：

- `MODEL_BASE_URL`：vLLM 的 OpenAI 兼容地址，末尾带 `/v1`
- `MODEL_NAME`：服务端模型 ID
- `MODEL_CONTEXT_TOKENS`：当前为 `16384`
- `MODEL_MAX_INPUT_TOKENS`：当前为 `8192`

模型密钥只通过环境变量提供：

```powershell
$env:DEEP_AGENT_MODEL_API_KEY = "你的密钥"
```

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

下一阶段：

- 7B：登录和多用户隔离

`README.md` 的“后续阶段”部分尚未同步最新编号；以本文档为准。

## 4. 当前系统结构

```text
浏览器 view/chat.html
        │
        │ HTTP + SSE
        ▼
FastAPI web/app.py
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

两个数据库职责不同：

- `memory.db`：跨会话长期记忆。
- `sessions.db`：某个会话的消息和检查点，用 `thread_id` 继续对话。

数据库文件包含用户原文，默认被 Git 忽略，不会自动同步到另一台电脑。

## 5. Web 入口与接口

主要入口：

- `stage7_web.py`：启动 Uvicorn
- `web/app.py`：FastAPI 路由、Agent 生命周期、会话接口
- `web/agent_stream.py`：思考、工具、回答和 usage 的 SSE 转换
- `view/chat.html`：Vue 3 聊天工作台
- `view/shared.js`：共享 Vue API 和图标
- `view/login.html`：登录页面外观，目前尚未接入真实认证

当前主要接口：

| 方法 | 路径 | 作用 |
|---|---|---|
| `GET` | `/health` | 服务健康检查 |
| `GET` | `/` | 聊天页面 |
| `POST` | `/api/sessions` | 生成新会话编号 |
| `GET` | `/api/sessions` | 列出持久化会话 |
| `GET` | `/api/sessions/{id}` | 读取会话消息与上下文占用 |
| `DELETE` | `/api/sessions/{id}` | 删除会话全部检查点 |
| `POST` | `/api/sessions/{id}/stream` | 在指定会话中流式提问 |

`/api/chat` 和 `/api/chat/stream` 是早期无会话接口，目前仍保留。

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

### 7.1 当前是单用户

- 没有真实登录和身份验证。
- 所有浏览器访问者共享同一个 `memory.db` 和 `sessions.db`。
- 知道会话编号的请求都能读取或删除该会话。
- 在完成 7B 前，不应直接暴露到不可信网络。

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

## 8. 下一阶段 7B：登录和多用户隔离

建议保持“两步走”：

### 7B-1：先完成后端身份与隔离

目标：

1. 用户表和密码哈希。
2. 登录、退出、当前用户接口。
3. 使用安全 Cookie 或服务端 Session 保存登录状态。
4. 每个会话必须记录所属用户。
5. 读取、继续、删除会话时校验所有权。
6. 长期记忆按用户隔离。
7. 终端或自动测试验证用户 A 无法读取用户 B 的数据。

隔离至少覆盖：

```text
用户
├── 自己的长期记忆
└── 自己的多个会话
```

不能只在前端隐藏其他用户数据，权限检查必须在后端和存储层执行。

### 7B-2：再接入页面

目标：

1. 将现有 `view/login.html` 接入真实登录接口。
2. 未登录访问聊天页时跳转登录页。
3. 聊天页显示当前用户并支持退出。
4. 会话列表只展示当前用户的数据。
5. 登录过期后给出清晰提示，不继续发送匿名请求。

在确定用户模型、Cookie 策略和数据库结构之前，先写清楚规格和验收标准，再修改代码。

## 9. 开发与验证约定

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

## 10. 给新 Codex 的建议开场指令

在另一台电脑打开仓库后，可以直接告诉 Codex：

```text
请先完整阅读 PROJECT_STATUS.md 和 README.md，熟悉当前工程。
当前要从 7B 登录与多用户隔离继续。
不要自行安装或升级依赖；如果需要依赖，说明用途、版本和命令，由我安装。
每次修改代码后解释新代码，并在提交前运行与改动相称的测试。
```
