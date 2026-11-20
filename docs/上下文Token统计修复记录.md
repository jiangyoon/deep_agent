# 上下文 Token 统计修复记录

> 时间：2026-11-19 ｜ 涉及模块：`career_agent/core`、`career_agent/web` ｜ 状态：已修复并验证

问答页面有一个「上下文窗口占用」仪表盘：显示当前会话消耗的 token 数、占模型上下文窗口的百分比，接近上限时提示自动压缩。本文记录它曾经「记录很有问题」的来龙去脉——问题现象、诊断证据链、根因、修复方案、二次重构与验证结果。

---

## 一、问题现象

**同一会话，两个入口读数精神分裂：**

- **问答过程中**：仪表盘显示正常（如 8K / 16K）
- **刷新页面后**：仪表盘爆表——历史会话最高显示 **3850%**，used_tokens 达 63 万

七个历史会话按 16384 分母换算：

| 会话 | 持久化 used_tokens | 仪表盘显示 | 判定 |
|---|---|---|---|
| 07c0f9… | 630,812 | **3850%** | 爆表 |
| 86fbb7… | 563,352 | **3438%** | 爆表 |
| d3d73b… | 421,081 | **2570%** | 爆表 |
| 46d3a9… | 226,593 | **1383%** | 爆表 |
| 98e232… | 178,666 | **1090%** | 爆表 |
| 650d74… | 7,554 | 46% | 正常 |
| dc1366… | 8,056 | 49% | 正常 |

---

## 二、诊断过程（证据链）

### 2.1 链路梳理

上下文统计有三条路径，问题出在「显示层与存储层各修一半」：

```
模型流式输出（chunk）
  ├─ SSE 路径：chunk → web/usage_tracker.py（显示层修正）→ 前端仪表盘   ← 当时已修
  └─ 持久化路径：chunk 合并 → usage_metadata → LangGraph checkpointer  ← 没人修
                     ↑ 刷新页面后 web/app.py 的 session_context_usage() 从这里读数
```

### 2.2 关键证据一：checkpointer 里的 usage 是虚高值

用 LangGraph 自带 `JsonPlusSerializer` 解包 `deep_agent.checkpoint_writes` 中 62 条 AI 消息，爆表会话的 `input_tokens / 正文字数` 比值达 **950–1200**（正常应 5–15）——input 被虚高了几十倍，且这些值已随消息持久化进库。

### 2.3 关键证据二：LangChain 合并规则是逐字段相加

`langchain_core/messages/ai.py` 的 `add_usage`：chunk 合并成完整消息时，`input_tokens` 逐字段相加。这是 LangChain 通用规则，对 OpenAI 风格（只有最后一个 chunk 带 usage）无害，但对旧服务端是灾难。

### 2.4 关键实验：判别两种服务端风格

| 服务端 | chunk usage 行为 | 合并结果 |
|---|---|---|
| **旧本地 vLLM**（thunisoft Qwen3.8-27B，`stream_usage=True`） | 几乎每个 chunk 都带 usage，`input_tokens` 是**全量值** | input ≈ 真实值 × chunk 数 → **虚高** |
| **移动云 moma**（`qwen/qwen3.6-plus`，现行） | OpenAI 风格 final-only（实测 18 chunks 中 1 个带 usage） | 相加 = 真实值 → 健康 |

爆表数据全部来自旧 vLLM 时代的 5 个会话，已凝固在库里；切到 moma 后的新会话全部正常。

### 2.5 分母也过期了

`config.MODEL_CONTEXT_TOKENS = 16384`，注释自述「vLLM 启动参数里的 max-model-len」——**旧本地部署模型的值**，切移动云后未更新（qwen3.6-plus 真实规格：上下文 1M tokens、最大输出 65,536，阿里云百炼文档）。连带 `MODEL_MAX_INPUT_TOKENS`（自动压缩触发线）同基于旧值。前端 `chat.html` 还有 3 处 `16384` 硬编码 fallback，与后端配置不同源。

---

## 三、根因：三层叠加

1. **历史脏数据**：旧 vLLM 的 chunk 相加虚高值被 checkpointer 持久化，显示层修正管不到它
2. **分母过期**：16384 是旧模型规格，硬编码且前后端各写一份
3. **显示层修了、存储层没修**：`usage_tracker.py` 只修正 SSE 数字，持久化路径照旧虚高——将来再接 chunk-全量上报风格的服务端（vLLM 系），同样的问题会复发

---

## 四、修复方案（四项裁决）

| # | 裁决 | 实现 |
|---|---|---|
| 1 | 历史数据可清（都是测试数据） | 事务内清空 `deep_agent` 的 checkpoints 三表 + `chat_sessions`，对账归零；`users`/`auth_sessions` 保留 |
| 2 | 分母从配置取 | 新增 `DEEP_AGENT_MODEL_CONTEXT_TOKENS`（.env，默认 1000000）；`config.MODEL_CONTEXT_TOKENS` 改为 env 驱动；`MODEL_MAX_INPUT_TOKENS` 联动 |
| 3 | 存储层治本（可以） | 模型流出口打补丁：中间 chunk 剥 usage、流结束补一个带真实终值的 chunk（详见第五节） |
| 4 | 前端改后端下发（可以） | 新增 `GET /api/context-config` 返回 `limit_tokens`；前端三处 16384 硬编码全部改为后端取值，tooltip 增加「分母未配置」分支 |

**方案 3 的技术要点**（当时定位为防御性治本——moma 是 final-only 风格、当前无害，但对将来再接 vLLM 系服务端防复发）：

- 实例属性覆盖 `_stream`/`_astream`（pydantic 允许、调用链已验证穿透 `bind_tools`）
- 累加器还原口径（与显示层完全一致）：**input 取各 chunk 最大值**（全量上报时最大值即真实值）、**output 非递减取末值 / 增量上报则求和**
- 流末把真实终值作为额外 chunk 补出，交给 LangChain 常规合并——**不改框架一行代码，持久化与 SSE 两条路径同时干净**
- 开关 `config.FIX_STREAM_USAGE`：False 时补丁不挂，`StreamUsageTracker.get_usage()` 返回 None、调用方回退框架原值

---

## 五、二次重构：算法收敛为唯一实现

初版实现把 120 行 usage 修正逻辑直接塞进 `core/llm_model.py`（本职是「创建模型连接对象」），且与 web 侧 `StreamUsageTracker` 形成**同一算法的两份拷贝**——同样四个字段、同样还原口径、同一个开关，将来漏同步一处就是「显示与持久化不一致」复发。按评审意见重构：

| 文件 | 行数变化 | 职责 |
|---|---|---|
| `core/stream_usage.py`（新建） | 162 | **唯一算法实现**：`StreamUsageAccumulator` 累加器、`corrected_chunks` chunk 剥离/补终值、`patch_stream_usage` 实例补丁、开关判断 |
| `core/llm_model.py` | 213 → 90 | 回到本职；`create_llm` 末尾一行 `patch_stream_usage(llm)` 挂载 |
| `web/usage_tracker.py` | 78 → 59 | 显示层薄壳：对外接口不变（`add_chunk/get_usage/reset`），内部复用 core 累加器，消灭第二份实现 |

`web/agent_stream.py` 零改动（薄壳保持原接口）。

---

## 六、验证结果（全绿）

**单元测试 6 用例**（算法层）：
- vLLM 风格（每 chunk 全量 input）：合并 3000 → 还原 1000 ✓
- output 增量上报：3+4+2=9 求和 ✓
- OpenAI 风格 final-only + `input_token_details` 透传 ✓
- 全流无 usage：不补 chunk、保持 None ✓
- `response_metadata` 剥 `token_usage`、保留 `model_name` 等其他元数据 ✓
- 累加器 reset 复用 ✓

**真实回归**：moma 下 patch 前后数值一致（final-only 风格不受影响）；`bind_tools` 路径穿透 ✓；显示层（SSE）与源头（持久化）读数逐字一致 ✓

**端到端 HTTP 实测**（独立实例 8765 端口）：

1. 登录 → `/api/context-config` 返回 1,000,000 ✓
2. 流式问答 → SSE usage：in=5244, pct=0.53% ✓
3. 刷新会话 → `context_usage.used_tokens = 5290` 与 SSE 逐字一致（**修复前同一位置爆表 3850%**）✓
4. checkpointer 持久化值 in=5244 真实，无相加虚高 ✓
5. 多轮增长正常（第二轮 in 5244→5311，仅增加上下文增量）✓
6. 浏览器 UI 实看：仪表盘 1%、tooltip「5.4K / 1000K token」✓

测试账号与会话验证后已清理。

---

## 七、遗留与注意事项

1. **8000 端口长驻调试实例**（VSCode 启动）跑的是修复前代码，重启调试会话后新逻辑才生效
2. **改动未提交**：与同批工作（政策搜索工具等）一并待验证后推送
3. **口径备注**：`used = input + output` 在思考模式下会高估（reasoning token 不进入下一轮历史）——当前 `THINKING_MODE=off` 无影响，将来开启思考需重新审视
4. **顺手修复**（本机首次跑 web 暴露的环境问题）：安装 `langgraph-checkpoint-postgres` 3.2.1；`core/memory_store.py` 的 `PostgresStore` import 做了新旧包双路径兼容（`langgraph-store-postgres` 0.1.0 起从命名空间包改为顶层包）

---

## 附：问题快速定位口诀

刷新后爆表、问答时正常 → **读的是持久化值，不是 SSE 值** → 查 checkpointer 里消息的 `usage_metadata` 是否虚高 → 虚高则查服务端 chunk usage 风格（vLLM 全量 vs OpenAI final-only）。分母异常 → 查 `MODEL_CONTEXT_TOKENS` 是否还是旧模型规格。
