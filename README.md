# 员工职业规划智能体

首个业务场景是员工证书与职称咨询。Web 端提供登录、会话恢复、用户记忆和流式聊天。当前处于工程迁移阶段，KB1 已入库，在线问答将在下一步接入。进度见 [PROJECT_STATUS.md](PROJECT_STATUS.md)。

## 启动

在项目根目录运行。已有环境和账号可直接沿用。首次配置时，以 [配置模板](.env.example) 为模板建立根目录的 `.env`，填入模型地址、模型名和密钥。系统环境变量优先。

```powershell
conda activate deep_agent
python -m uvicorn career_agent.web.app:app --host 127.0.0.1 --port 8000
```

浏览器打开 [本地工作台](http://127.0.0.1:8000)。健康检查地址为 [`/health`](http://127.0.0.1:8000/health)。

如需新建账号，在项目根目录执行以下命令，按提示输入密码。

```powershell
python -m scripts.manage_users create 用户名
```

其他管理命令使用 `python -m scripts.manage_sessions` 和 `python -m scripts.manage_memory` 查看用法。Web 用户在聊天页管理自己的记忆。终端记忆命令管理本地共享记忆。

## 目录

- `career_agent/agent/` 组装 Deep Agent，定义系统提示词及业务工具入口。
- `career_agent/skills/` 存放证书与职称咨询 Skill。Agent 以只读方式加载。
- `career_agent/knowledge/` 是在线 KB1 代码的接入位置。
- `career_agent/core/` 提供模型、用户、会话和记忆存储。
- `career_agent/web/` 提供 FastAPI、页面和 SSE。
- `scripts/kb1/` 存放离线清洗、迁移、校验及向量入库命令，依赖和连接参数示例随脚本存放。
- `data/` 存放本地 SQLite、客户原始资料和处理产物。运行数据和真实配置保存在本机。

KB1 的清洗与只读校验命令见 [第一批资料处理结果](docs/第一批资料处理结果.md)。
