# 员工职业规划智能体项目状态

更新于 **2026-11-20**。当前分支：`codex/employee-career-planning`。

**第一批知识库数据已入库，应用 PostgreSQL 表已初始化。** 员工证书与职称咨询只提供政策依据、路径建议和流程指引；不代办申报、发放奖励或报销。员工资料保存在各自的记忆里，暂不接员工系统；外部政策查询先预留客户 WebSearch 工具接口。

## 工程

- 应用代码位于 `career_agent/`。正式 Web 启动、用户创建和运维命令见 [README](README.md)。
- 登录、会话持久化和用户记忆使用 `career` 数据库中的 `deep_agent` schema。应用以 `sa` 连接，复用 `.env` 中的 `PG*` 参数。
- 目标库已初始化并回读核实 10 张表。初始化时账号、登录凭证、会话、检查点和记忆表均为空；旧 SQLite 测试数据留在本机。已验证 Web 应用能连接目标库启动，健康接口和登录页面可访问。创建网页账号的方法见 [README](README.md#启动)。
- 应用 schema 的 10 张表和 43 个字段已补齐中文注释，并从 PostgreSQL 数据字典回读核实。
- 网页 Agent 已注册 `lookup_certificate_mapping`，并用配置的模型验证“一建在集团和北京分别对应什么”。工具从对应表和来源表查询职称、适用条件、文件名和原件位置。CFA 与数据管理工程师（高级）经用户确认已核实，可按北京人社渠道查询。
- 网页 Agent 已注册 `search_policy_sections`。PostgreSQL 筛出适用文件后，两路 Milvus 检索使用同一份来源清单，排序后回查正文和出处。已实测集团 2026 年高工条款、常设办法、无适用来源、待复核来源隔离和向量异常时的 BM25 回退。年度过滤只约束年度通知及其挂接的知识性附件；制度办法、奖励目录等常设文件不按年排除，奖励目录的版本年仅用于注明出处。已复验 2026 年奖励查询命中 2025 年版目录（出处注明版本年）、2025 年查询正确筛掉 2026 年度通知与附件、北京海淀范围互斥等场景。
- 网页问答已接通：Agent 按问题调用六项知识工具（对应/奖励/时间/文号/附件/检索），多轮追问并引用原文出处。实际多轮联调已验证先问渠道、再调用工具并引用原文。模型仍可能根据一建批准年份直接推算个人资历起算和最早申报年，个人资格结论的验收待完成。
- 附件原件下载（2026-11-20）：`list_notice_attachments` 每行带 `attachment_id` 与 `download_url`；新增 `GET /api/attachments/{attachment_id}/download`（登录校验 + 现行有效与状态白名单同 `ATTACHMENTS` 口径，被完全排除的来源天然拿不到）。模型把 `download_url` 原样写进答案；前端对裸附件地址兜底转成「点击下载」链接（`chat.html` `linkifyAttachments`）。浏览器实测点链接真实下载评审表模板（中文文件名正确）。
- 奖励证书名称标蓝（2026-11-20）：后端在奖励工具的 `tool_result` 事件上独立下发 `blueNames`（`chat_history.py` 对完整结果提取，不受展示截断影响）；前端 `collectBlueNames` 累积名单并派生「注册证书/注册证」后缀剥离的简称（模型常写「一级建造师」而蓝名原文是「一级建造师注册证书」），`applyBlueNames` 对 markdown 后的 HTML 文本节点做长名优先占位替换包 `span.blue-cert`（hover 提示“考过后可视同为相应级别的职称证书”）。直播与历史回放双路径实测标蓝生效。
- 代码审查与修复（2026-11-23）：独立评审（连库实证 + 浏览器复测 12 用例）判定「需修改后合并」，4 个 Important 全部修复——①蓝名匹配补全半角等价字符类与空白折叠（此前 4/19 个含括号蓝名因模型写法差异永不渲染；span 保留页面原文写法，上色不改字）②附件下载补父来源 `REVIEWED_FILE` 校验（`create_attachment_reader` 两步查询，与工具侧 parent 校验同一片段，REPEATABLE READ 下口径零漂移）③测试沉淀为 `tests/`（8 个用例全过：蓝名提取五态 + 下载口径三态含「reader 集合 == 白名单∩policy∩父有效」断言）④`download_policy` 显式进下载 SQL 判定。顺手修 linkify 对已有 `<a>` 的嵌套包裹。回归：下载三态（200/404/401）、浏览器端到端标蓝、前端 8 边界用例全过。
- 员工画像沉淀（2026-11-20）：SKILL 指引模型在员工主动说明学历/专业/证书/年限/意向时用 `edit_file` 更新 `/memories/AGENTS.md` 用户背景（一句话一条，更正覆盖旧记录）。实测新会话一轮问答后画像三条正确写入记忆面板；同会话跨轮上下文复用（材料清单问题沿用前轮“评高工”语境）已验收通过。
- 前端交互重构（另一会话完成，2026-11-20 在用）：渲染逻辑独立成 `views/chat-activity.js`（`app.py` 挂 `/chat-activity.js` 路由），事件构造收敛到 `web/chat_history.py`（`tool_call_data`/`tool_result_data`/`browser_messages` 统一出口），`chat.html` 为纯模板+入口。对话过程按轮折叠展示：思考/命令按轮分组，流式只展开最新一轮、已完成的自动收起。
- 上下文 token 记录修复（2026-11-19）：问答页仪表盘曾出现“刷新后爆表”（历史会话最高显示 3850%）。根因是三层叠加：①历史会话的持久化 usage 虚高（旧环境 chunk 相加）②分母写死 16384 ③前端未从后端取分母。已修复：清理历史测试会话；`MODEL_CONTEXT_TOKENS` 改为 .env 下发（`DEEP_AGENT_MODEL_CONTEXT_TOKENS`，默认 1000000，qwen3.6-plus 规格）；新增 `/api/context-config` 接口并改前端三处硬编码；模型流在源头修正 chunk 相加（`career_agent/core/stream_usage.py`，算法唯一实现——SSE 显示层 `web/usage_tracker.py` 复用同一累加器，补丁挂载点在 `core/llm_model.py` 的 create_llm）。端到端实测：SSE usage 与刷新后读数一致（5290=5290，仪表盘 0.53%→1%），`career_agent/core/memory_store.py` 的 PostgresStore import 已做新旧包双路径兼容（本机缺 `langgraph-checkpoint-postgres`，已装 3.2.1）。持久化层、多轮增长、浏览器 UI 均已验证。

## 已完成的数据

| 数据 | 当前结果 |
|---|---|
| PostgreSQL `career.public` | 44 个来源、408 条正文、244 条职称对应、70 条奖励、17 条时间事项、33 条附件关系。结构化业务数据以 PostgreSQL 为准，旧达梦库不再使用。 |
| Milvus `career.career_policy_sections` | 408 条索引，与 PostgreSQL 正文对账一致。Qwen/Qwen3-VL-Embedding-8B，稠密向量 1024 维，另有 BM25 稀疏向量；已抽查 BM25 和 `TEXT_MATCH`。索引文本是「章名＋正文」，不拼文件标题。 |
| 职称对应 | 北京人社和集团内部的国内对应各 105 条，另有北京人社国际职业资格对应 34 条，均按记录状态、来源状态及渠道查询。支持「一建」「软考」「CFA」等简称，软考可按级别区分。 |
| 文号 | 已补 4 个明确文号及已知别名。查「182 号」先定位印发通知，再沿附件关系找到制度正文。 |
| N019 校对放行 | 《北京市职称评审管理暂行办法》（京人社事业发〔2020〕12号）按政府公报 2020 年第 39 期逐条校对后放行为 `review_dataset`：33 条一致、12 条正文修正（第四、六、七、八、九、十、十一、十八、二十八、三十二、四十二、四十三条），54 条重灌并双库对账一致；已实测北京人社渠道命中第七条官方口径、集团渠道正确隔离。备份在 `data/material_review/n019_proofread/`。 |
| 奖励 | 2026 年沿用 2025 年版目录。级别允许 S、A、A-、B、C 或空值；70 条奖励未变。目录共用说明合并成一条，按 `source_id` 查询；旧关联表已删除。`blue_display_names` 已入数据，网页答案中蓝色名单渲染已实现（同轮）。组合奖励仍是一条奖励。 |

文件用 `source_id` 标识，新版分配新 ID。用户已将现有来源设为「现行有效」；在线查询仍须按已确认的渠道、地区、年度和资料类型筛选。这一设置不证明某份目录仍由发文方沿用，也不会使历史、排除或未处理材料成为可引用的正文。

## 下一步

1. 个人资格结论验收：真实问法「我本科机械工程，工作 5 年，有一建，想评高工，需要什么条件？」已实测（不预填渠道）——识别证书、区分总工龄与前置职称后年限、追问批准时间均正确；遗留观察项是模型有时把“证书批准日”直接当资历起算日表述（SKILL 已禁止代算，个别轮次仍会说满），以及个别轮次思考内容泄漏进正文（2026-11-20 观察到一次，模型行为问题非前端缺陷）。
2. 补录报销流程正式资料（奖励目录的报销上限不能当成办理指引）。
3. 河北 7 份语料清洗入库（先专业目录，再按优先专业处理 883 页上下册）。
4. 高工操作手册 33 张图 OCR 后入向量索引。
5. 审查遗留的两个记录性 Minor：附件 `original_path` 是本机绝对路径（`E:\code\deep_agent\...`），换机部署需同步数据目录或改配置化路径；蓝名个别为整句（如「注册渗透测试专家（CISP-PTS）、注册信息安全开发人员（CISD）；…」），模型答案拆开单项写时不匹配，属数据粒度问题，后续语料治理时拆分为逐证书名。

## 剩余资料

- **7 份未清洗入库**：河北职称专业目录和上下册汇编 3 份、培训攻略 2 份、职称渠道简介 2 份。河北先做目录，再按优先专业处理汇编。完整清单见 [证书资料处理待办](证书资料处理待办.md)。
- **缺正式报销流程**。奖励目录里的报销上限不能当成办理指引。
- 高工操作手册已入库 28 条文字。原件有 33 张图，正文范围内的 32 张已保存并记录段落位置；未 OCR、未入向量索引，截图独有内容目前不可搜。

## 交接入口

- 前端：`views/chat.html`（模板+入口+标蓝/链接兜底）、`views/chat-activity.js`（折叠渲染组件）。后端：`web/app.py`（附件下载路由）、`web/chat_history.py`（事件构造+蓝名提取）、`knowledge/notice_attachments.py`（download_url+下载查询）、`skills/certificate-consultation/SKILL.md`（附件链接、蓝名、画像沉淀指引）。
- 现有脚本：[证书映射及简称查询](scripts/kb1/normalize_certificate_mappings.py)、[文号查询](scripts/kb1/add_document_number_lookup.py)、[奖励表迁移](scripts/kb1/simplify_reward_rules.py)、[Milvus 建库](scripts/kb1/create_vector_store.py)、[正文入索引](scripts/kb1/index_sections.py)。**上述数据库迁移和第一批入索引已执行，不要作为接网页的前置步骤重跑。**
- 第一批基线由 `data/material_review/batches/0848fe37e7ed/first_batch/LATEST.json` 指向 `run08`，包含清洗快照和截图；该目录不进 Git。保留原件和回滚备份。

业务背景见 [证书咨询需求](docs/intent-certificate-consultation.md)，接入设计见 [Agentic RAG 方案](docs/career-planning-agentic-rag-proposal.md)，材料处理规则见 [证书政策文件清洗处理方案](docs/证书政策文件清洗处理方案.md)。
