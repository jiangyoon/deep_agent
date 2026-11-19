# 员工职业规划智能体

首个业务场景是员工证书与职称咨询。Web 端提供登录、会话恢复、用户记忆和流式聊天。应用运行数据使用 PostgreSQL 的 `deep_agent` schema。进度见 [PROJECT_STATUS.md](PROJECT_STATUS.md)。

## 初始化应用数据库

安装 `requirements.txt` 中的依赖。应用优先读取根目录 `.env` 中的 `CAREER_APP_DB_DSN`，也可直接沿用 `PGHOST`、`PGPORT`、`PGDATABASE`、`PGUSER` 和 `PGPASSWORD`。当前项目使用 `career` 数据库、`sa` 账号和手动创建的 `deep_agent` schema。使用数据库直连或会话池化地址，连接必须支持设置 `search_path`。

执行以下命令初始化账号、登录凭证、会话和记忆表。

```powershell
python -m scripts.init_app_db
```

初始化成功后，按下面的命令创建首个网页账号。

## 启动

在项目根目录运行。以 [配置模板](.env.example) 为模板配置模型与数据库。系统环境变量优先。

```powershell
conda activate deep_agent
python -m uvicorn career_agent.web.app:app --host 127.0.0.1 --port 8000
```

浏览器打开 [本地工作台](http://127.0.0.1:8000)。健康检查地址为 [`/health`](http://127.0.0.1:8000/health)。

如需新建账号，在项目根目录执行以下命令，按提示输入密码。

```powershell
python -m scripts.manage_users create 用户名
```

其他管理命令使用 `python -m scripts.manage_sessions` 和 `python -m scripts.manage_memory` 查看用法。Web 用户在聊天页管理自己的记忆。终端记忆命令管理共享记忆。

## 目录

- `career_agent/agent/` 组装 Deep Agent，定义系统提示词及业务工具入口。
- `career_agent/skills/` 存放证书与职称咨询 Skill。Agent 以只读方式加载。
- `career_agent/knowledge/` 是在线 KB1 代码的接入位置。
- `career_agent/core/` 提供模型、用户、会话和记忆存储。
- `career_agent/web/` 提供 FastAPI、页面和 SSE。
- `scripts/kb1/` 存放离线清洗、迁移、校验及向量入库命令。依赖统一放在根目录 `requirements.txt`；连接参数见[知识库配置示例](scripts/kb1/.env.example)。
- `data/` 保留旧 SQLite 测试文件、客户原始资料和处理产物。应用运行数据保存在 PostgreSQL。

应用运行数据使用 `career.deep_agent`，知识库结构化数据使用 `career.public`，正文向量在 Milvus 的 `career.career_policy_sections`。第一批已入库，网页 Agent 检索工具待接；无需为启动网页重跑迁移或重建向量。当前进度和验收任务见 [PROJECT_STATUS.md](PROJECT_STATUS.md)，本地清洗基线见[第一批资料处理结果](docs/第一批资料处理结果.md)。
