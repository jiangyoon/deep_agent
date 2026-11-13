# deep_agent 学习工程

用 Deep Agents 框架做一个 Web 问答 Agent 的学习工程，后端模型是自部署的 Qwen（vLLM，OpenAI 兼容接口）。
目前完成阶段 2（基础问答、流式输出、思考模式）、阶段 3（自定义工具）、阶段 4（预置只读 Skill）、阶段 5（记忆）和阶段 6（会话持久化），记忆和会话都存于本地 SQLite。

## 一、第一次使用

1. Python 3.13，建议新建独立的 conda 环境：

   ```
   conda create -n deep_agent python=3.13 -y
   conda activate deep_agent
   pip install -r requirements.txt
   ```

2. 打开 `config.py`，把这两项改成你自己的值：
   - `MODEL_BASE_URL`：模型服务地址，结尾带 `/v1`
   - `MODEL_NAME`：模型 id，用 `curl.exe 地址/models` 可以查到

   再设置模型密钥环境变量（PowerShell 当前窗口示例）：

   ```powershell
   $env:DEEP_AGENT_MODEL_API_KEY = "你的密钥"
   ```

   这两项没改、或没有设置密钥时，程序启动会直接提示，不会去连一个不存在的地址。

3. 在 `deep_agent` 文件夹下运行下面的入口。

## 二、入口文件（都在根目录，用 `python 文件名.py` 运行）

| 文件 | 作用 |
|---|---|
| `stage2_invoke.py` | 阶段 2：invoke 方式，等 Agent 全部做完一次性显示，列出每条消息 |
| `stage2_stream.py` | 阶段 2：流式方式，每一步实时显示，思考和回答逐字显示 |
| `stage3_tools.py` | 阶段 3：测试自定义工具（查时间、除法、职业知识库检索） |
| `stage4_skills.py` | 阶段 4：测试预置的只读 Skill，步骤里会标注"读取 Skill 文件" |
| `stage5_memory.py` | 阶段 5：测试记忆，每个问题后显示记忆有没有变化 |
| `manage_memory.py` | 记忆管理：`show` 查看、`clear` 清空、`replace 文件路径` 整体替换 |
| `stage6_chat.py` | 阶段 6：交互式多轮对话，会话保存后可接着聊（/new /list /switch /quit） |
| `manage_sessions.py` | 会话管理：`list` 列出、`show 编号` 查看、`delete 编号` 删除 |

## 三、目录结构

```
deep_agent/
├── config.py            设置：模型地址、模型名、温度、思考模式、系统提示词
├── questions.py         各阶段的测试问题（想换问题测试，改这里）
├── requirements.txt     依赖，版本已固定
├── README.md
│
├── stage2_invoke.py     入口
├── stage2_stream.py     入口
├── stage3_tools.py      入口
├── stage4_skills.py     入口
├── stage5_memory.py     入口
├── manage_memory.py     记忆管理工具
├── stage6_chat.py       入口
├── manage_sessions.py   会话管理工具
│
├── core/                所有阶段共用，长期保留
│   ├── llm_model.py       创建模型连接（含"设置没填"的检查）
│   ├── thinking_mode.py   把思考模式设置翻译成发给服务端的参数
│   ├── agent_builder.py   把模型、提示词、工具、Skill 组装成 Agent
│   ├── backend_builder.py 准备文件系统：/skills/ 指向 skills 文件夹（只读），/memories/ 指向记忆数据库
│   ├── memory_store.py    记忆的存储：打开数据库、读取、替换、清空（换数据库主要改这里）
│   ├── session_store.py   会话的存储：打开会话数据库、列出、读取、删除会话
│   └── tool_call_collector.py 把流式片段里零散的工具调用拼成完整的工具调用
│
├── my_tools/            自定义工具
│   ├── __init__.py        登记所有工具（ALL_TOOLS 列表）
│   ├── time_tool.py       查询城市当前时间
│   ├── math_tool.py       除法
│   └── knowledge_tool.py  职业知识库检索（练习用，返回的是模拟数据）
│
├── skills/              预置 Skill（只读），每个 Skill 一个文件夹
│   ├── career-kb-qa/      职业知识库问答规范（严格模式：只依据检索结果回答）
│   │   └── SKILL.md
│   └── time-zone-helper/  跨城市时间换算
│       ├── SKILL.md
│       └── references/cities.md   参考文件，模型需要时才会读
│
├── display/             终端打印，学习阶段用来观察运行过程
│   ├── printer.py         invoke 方式：逐条打印消息
│   ├── stream_handler.py  流式方式：整理流式片段（思考、回答、步骤）
│   ├── stream_runner.py   流式提问（多个入口共用）
│   ├── memory_view.py     显示记忆内容和变化
│   └── error_hint.py      出错时的排查提示
```

文件之间的依赖方向是单向的：入口依赖 `core`、`display`，`core` 依赖 `my_tools` 和 `config`，
`display` 和 `my_tools` 互不依赖。入口文件之间不互相导入。

## 四、常用设置

- 思考模式：`config.py` 里的 `THINKING_MODE`，`"default"` 开启思考，`"low"` 缩短思考，`"off"` 关闭思考。
- 换测试问题：改 `questions.py`。
- 加工具：在 `my_tools` 里新建一个工具文件，然后在 `my_tools/__init__.py` 里导入并加进 `ALL_TOOLS`。
- 工具里不要抛异常：工具一旦抛出异常，整个 Agent 会直接崩溃，模型看不到错误。遇到问题请返回一段说明文字。

## 五、Skill 怎么用

- 一个 Skill 就是 `skills/` 下的一个文件夹，里面有 `SKILL.md`，开头写名称和简介。
- 名称只能用小写字母、数字和连字符，不超过 64 个字符，**必须和文件夹名一致**；简介不超过 1024 个字符。
- 模型一开始只在系统提示里看到每个 Skill 的名字和简介，需要时才去读 `SKILL.md` 全文，
  `SKILL.md` 里提到的参考文件，它也是需要时才读。
- 实测：启用 Skill 后系统提示固定增加约 1900 个字符（使用说明），每多一个 Skill 再增加约 130 个字符。
- Skill 是只读的：`core/backend_builder.py` 里设置了权限规则，禁止对 `/skills/` 下的文件做修改、新建、删除。
- 想对比"没有 Skill"的效果：把 `config.py` 里的 `USE_SKILLS` 改成 `False`。

## 六、记忆怎么用

- 记忆是一个文件 `/memories/AGENTS.md`（Agent 眼里的路径），实际存在 `data/memory.db`（SQLite 数据库）里，程序重启后还在。
- 每次运行开始时，记忆全文会放进系统提示；Agent 根据框架自带的规则，自己决定什么时候用 `edit_file` 更新它。
- 第一次运行时会预置一个带标题的空模板，因为 `edit_file` 只能改已经存在的文件。
- 记忆每次请求都会带上，`config.py` 里的 `MEMORY_WARN_CHARS` 是提醒整理的字数。
- 手工查看或修改记忆用 `manage_memory.py`，不经过 Agent。
- `stage5_memory.py` 里关掉了 Skill，避免 `career-kb-qa` 的"只依据知识库回答"规则干扰对记忆的观察。
- 以后换成达梦等数据库，主要改 `core/memory_store.py` 里的 `open_store()`，但需要先有该数据库的 LangGraph Store 实现。

## 七、会话怎么用

- 会话保存在 `data/sessions.db`，和记忆的 `data/memory.db` 是两个独立的数据库。
- 每个会话有一个编号（thread_id），带同一个编号提问就是接着聊；程序重启后历史还在。
- 每一轮发给模型的都是这个会话的全部历史，会话越长，每次请求越大；终端里会显示每次请求的真实 token 数。
- 对话太长时框架会自动压缩：用一次额外的模型调用把早期对话总结成摘要，这次调用不会显示在终端上。
  会话里保存的原始消息不会被删，只是发给模型时用摘要代替。
- 同一个会话里，记忆只在会话开始时加载一次，之后记忆有更新也不会进入这个会话，开新会话才会看到。
- 对话原文会明文保存在会话数据库里，包括用户说出的密码、密钥等，删除用 `manage_sessions.py delete`。
- `config.py` 里的 `MODEL_MAX_INPUT_TOKENS` 决定什么时候自动压缩，设成了上下文长度的一半，原因见那里的注释。

## 八、维护约定

- 新增一个学习阶段，就在根目录新增一个 `stageN_xxx.py` 入口，共用代码放进 `core` 或 `display`，不要让入口互相导入。
- 本地文件夹名不要和第三方包重名（例如不要新建 `langchain.py`、`openai.py`），否则会出现难以看懂的导入错误。
- `requirements.txt` 里的版本号不要随意升级。`langchain-deepseek` 用于保存 vLLM 返回的思考内容，升级前先确认思考内容还能正常显示。

## 九、后续阶段

阶段 7：多用户隔离；阶段 8：Web 层（FastAPI + 流式，含登录）。

## 十、本地敏感数据

- `DEEP_AGENT_MODEL_API_KEY` 只从环境变量读取，不要写回 `config.py` 或提交到版本库。
- `data/*.db` 含有记忆与会话原文，默认已被 Git 忽略；不要把它们发给他人。
