# display/stream_handler.py
# 作用：处理 agent.stream() 一个接一个吐出来的片段，把它们整理成终端上的显示效果。
#
# 同时开启两种流式模式时，每个片段是一个二元组：(模式名, 数据)
#   模式名 "messages"：模型正在生成的小片段（用来做打字机效果）
#   模式名 "updates" ：某个步骤刚刚执行完（用来显示"现在做到哪一步了"）
#
# 分工原则（很重要）：
#   模型的正文，只在 messages 里逐字打印；
#   updates 里模型也会再给一次完整消息，那一份不打印正文，否则同一段话会显示两遍。

import time

import config

from langchain_core.messages import AIMessageChunk

from core.tool_call_collector import ToolCallCollector
from display.printer import content_to_text
from display.usage_tracker import StreamUsageTracker

# 工具返回内容在步骤提示里最多显示多少个字符
STEP_TEXT_LIMIT = 80


class StreamPrinter:
    """负责把流式片段打印到终端。

    这里用了一个"类"，是因为打印过程中要记住几个状态，
    比如"模型正文有没有开始输出"。类里的 self.xxx 就是这些要记住的状态。
    """

    def __init__(self):
        # 记录开始时间，用来显示每一步经过了多少秒
        self.start_time = time.time()
        # 本轮模型回复，正文是否已经开始输出
        self.text_started = False
        # 本轮模型回复，思考内容是否已经开始输出
        self.reasoning_started = False
        # 被暂时扣住的"末尾空白和换行"（思考、正文各一份），见 print_without_trailing_blank
        self.reasoning_pending = ""
        self.text_pending = ""
        # 拼接工具调用片段用，以及记录哪些工具调用已经提示过
        self.collector = ToolCallCollector()
        self.announced = []
        # 从流式 chunk 还原真实 token 用量（见 display/usage_tracker.py）
        self.usage_tracker = StreamUsageTracker()

    def elapsed(self):
        """返回从开始到现在经过的秒数，格式如 +1.8s。"""
        seconds = time.time() - self.start_time
        return "+" + format(seconds, ".1f") + "s"

    def handle(self, mode, payload):
        """入口：根据模式名，把片段交给对应的处理函数。"""
        if mode == "messages":
            self.handle_messages(payload)
        elif mode == "updates":
            self.handle_updates(payload)

    # ---------------------------------------------------------
    # messages 模式：负责打字机效果
    # ---------------------------------------------------------
    def handle_messages(self, payload):
        # messages 模式的数据是 (消息片段, 元数据)
        chunk = payload[0]
        meta = payload[1]

        # 只处理"模型节点"产生的文字片段，
        # 工具返回的内容属于 ToolMessage，交给 updates 去显示，这里跳过
        if not isinstance(chunk, AIMessageChunk):
            return
        if meta.get("langgraph_node") != "model":
            return

        # 先把工具调用片段交给 collector 拼接。
        # 工具名一出现，就立刻提示一次"正在生成工具调用"，不用等整轮结束
        self.collector.add_chunk(chunk)
        self.announce_new_tool_calls()

        # 同步收集本轮 usage，供结束后还原真实 token 数
        self.usage_tracker.add_chunk(chunk)

        # 再处理思考内容（由 llm_model.py 里的 ChatDeepSeek 放进 additional_kwargs）
        reasoning = chunk.additional_kwargs.get("reasoning_content", "")
        if reasoning != "":
            self.show_reasoning(reasoning)

        text = content_to_text(chunk.content)
        if text == "":
            return

        # 正文要开始了：如果刚才在打印思考，先把思考那一行收尾换行
        self.finish_reasoning_line()

        # 本轮第一次出现正文：先去掉开头的空白和换行，再打印一个前缀
        if not self.text_started:
            text = text.lstrip()
            if text == "":
                return
            print("[" + self.elapsed() + "] 模型回答: ", end="", flush=True)
            self.text_started = True

        # end="" 表示不换行，flush=True 表示立刻显示，这两个参数就是打字机效果的关键
        self.text_pending = self.print_without_trailing_blank(text, self.text_pending)

    # ---------------------------------------------------------
    # updates 模式：负责显示步骤
    # ---------------------------------------------------------
    def handle_updates(self, payload):
        # updates 模式的数据是 {节点名: 这个节点产生的新内容}
        for node_name in payload:
            data = payload[node_name]
            if data is None:
                continue

            if node_name == "model":
                self.show_model_step(data)
            elif node_name == "tools":
                self.show_tool_step(data)

    def get_messages(self, data):
        """从节点的新内容里取出消息列表。"""
        messages = data.get("messages", [])
        if not isinstance(messages, list):
            messages = [messages]
        return messages

    def announce_new_tool_calls(self):
        """模型刚开始生成某个工具调用（工具名已经出现）时，立刻提示一次。

        每个工具调用只提示一次，用 self.announced 记录已经提示过的序号。
        """
        for index in self.collector.calls:
            record = self.collector.calls[index]
            if record["name"] != "" and index not in self.announced:
                # 如果思考或正文正在打印，先收尾换行，避免提示接在它们后面
                self.finish_text_line()
                print("[" + self.elapsed() + "] 正在生成工具调用:", record["name"])
                self.announced.append(index)

    def show_reasoning(self, reasoning):
        """逐字打印思考内容，和正文用不同的前缀区分。"""
        # 本轮第一次出现思考：去掉开头的空白，再打印前缀
        if not self.reasoning_started:
            reasoning = reasoning.lstrip()
            if reasoning == "":
                return
            print("[" + self.elapsed() + "] 思考: ", end="", flush=True)
            self.reasoning_started = True

        self.reasoning_pending = self.print_without_trailing_blank(
            reasoning, self.reasoning_pending
        )

    def print_without_trailing_blank(self, text, pending):
        """打印文字，但先扣住末尾的空白和换行，返回被扣住的部分。

        为什么要这样：模型的输出末尾常常带着换行，如果直接打印，
        后面再换一行，就会多出一个空行。
        做法：每次把"上次扣住的空白 + 这次的文字"拼起来，
        只打印去掉末尾空白后的部分，末尾空白留到下一次再决定：
          后面还有文字接上来：说明这些空白在句子中间，补打出来；
          后面没有文字了（这一段结束）：直接丢掉，不再打印。
        """
        text = pending + text
        body = text.rstrip()
        new_pending = text[len(body):]
        if body != "":
            print(body, end="", flush=True)
        return new_pending

    def finish_reasoning_line(self):
        """如果思考正在打印中，先换行，让后面的内容另起一行。"""
        if self.reasoning_started:
            print("")
            self.reasoning_started = False
        # 这一段已经结束，被扣住的末尾空白直接丢掉
        self.reasoning_pending = ""

    def finish_text_line(self):
        """如果思考或正文正在打印中，先换行，让后面的步骤提示另起一行。"""
        self.finish_reasoning_line()
        if self.text_started:
            print("")
            self.text_started = False
        # 这一段已经结束，被扣住的末尾空白直接丢掉
        self.text_pending = ""

    def show_model_step(self, data):
        """模型完成了一次回复。"""
        for message in self.get_messages(data):
            # 先把正在打印的正文收尾换行
            self.finish_text_line()

            if len(message.tool_calls) > 0:
                for call in message.tool_calls:
                    self.show_tool_call(call)
            else:
                print("[" + self.elapsed() + "] 步骤: 模型给出最终回复，循环结束")

            # 显示这次模型调用的真实 token 用量（服务端返回了才有）
            self.show_usage(message)

        # 这一轮模型回复结束了：清空拼接记录，准备拼下一轮
        self.collector.reset()
        self.announced = []
        self.usage_tracker.reset()

    def show_tool_call(self, call):
        """显示模型决定调用的一个工具。

        如果是读取 /skills/ 下的文件，说明模型在读 Skill，单独标注出来。
        """
        name = call["name"]
        args = call["args"]
        path = str(args.get("file_path", ""))

        if name == "read_file" and path.startswith("/skills/"):
            print("[" + self.elapsed() + "] 步骤: 读取 Skill 文件", path)
        else:
            print(
                "[" + self.elapsed() + "] 步骤: 模型决定调用工具",
                name,
                "参数:",
                args,
            )

    def show_usage(self, message):
        """显示一次模型调用的 token 用量，以及占上下文长度的比例。

        优先用 usage_tracker 从流式 chunk 还原后的数字；
        未启用修正或本轮没收集到时，再回退到 message.usage_metadata。
        """
        usage = self.usage_tracker.get_usage()
        if usage is None:
            usage = message.usage_metadata
        if usage is None:
            return

        input_tokens = usage.get("input_tokens")
        output_tokens = usage.get("output_tokens")
        text = "    本次调用模型: 输入 " + str(input_tokens) + " token，输出 " + str(output_tokens) + " token"

        # config 里有 MODEL_CONTEXT_TOKENS（阶段 6 新增）时，顺便算一下占了上下文的多少
        context_tokens = getattr(config, "MODEL_CONTEXT_TOKENS", None)
        if context_tokens is not None and input_tokens is not None:
            total = input_tokens
            if output_tokens is not None:
                total = total + output_tokens
            percent = total * 100 / context_tokens
            text = text + "，合计占上下文 " + format(percent, ".1f") + "%"

        print(text)

    def show_tool_step(self, data):
        """某个工具执行完了。"""
        for message in self.get_messages(data):
            text = content_to_text(message.content)
            text = text.replace("\n", " ")
            if len(text) > STEP_TEXT_LIMIT:
                text = text[:STEP_TEXT_LIMIT] + "..."
            print("[" + self.elapsed() + "] 步骤: 工具", message.name, "返回:", text)

    def finish(self):
        """全部结束后，打印总耗时。"""
        self.finish_text_line()
        print("[" + self.elapsed() + "] 全部完成")
