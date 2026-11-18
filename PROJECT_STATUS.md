# 员工职业规划智能体项目状态

当前分支为 `codex/employee-career-planning`。首个场景是员工证书与职称咨询。

## 工程

- 应用代码位于 `career_agent/`。正式 Web 启动、用户创建和运维命令见 [README](README.md)。
- 登录、会话持久化和用户记忆继续使用项目根目录 `data/` 下原有的 SQLite 文件。模型配置从根目录 `.env` 或系统环境变量读取。
- Agent 已加载证书咨询 Skill。在线 KB1 检索待接入。

## 知识库

- 上一轮完成 PostgreSQL 政策资料与 Milvus 索引准备，已核对 `career.career_policy_sections` 的 408 条已入库正文，并抽查 BM25 与 `TEXT_MATCH`。
- 用户确认已将 44 个 PostgreSQL 来源设为「现行有效」。问答仍需核对资料的适用渠道、地区和年度。
- 离线处理与入库脚本集中在 `scripts/kb1/`。下一步用现有 KB1 接通真实检索及条款引用。

业务背景见 [证书咨询需求](docs/intent-certificate-consultation.md) 和 [Agentic RAG 方案](docs/career-planning-agentic-rag-proposal.md)。学习阶段的设计和工作记录见 [历史计划](docs/plans/)。
