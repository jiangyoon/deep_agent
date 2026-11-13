import json


class ToolCallCollector:
    """把流式片段里零散的工具调用信息，拼成完整的工具调用。"""

    def __init__(self):
        # 键是工具调用的序号 index，值是正在拼接的内容
        self.calls = {}

    def add_chunk(self, chunk):
        """处理一个消息片段。没有工具调用信息的片段，直接跳过。"""
        for piece in chunk.tool_call_chunks:
            index = piece["index"]
            if index is None:
                index = 0

            # 这个序号第一次出现，先建一条空记录
            if index not in self.calls:
                self.calls[index] = {"id": "", "name": "", "args_text": ""}

            record = self.calls[index]

            # id 和 name 只在第一片里有，后面的片段里是 None，所以要先判断
            if piece["id"] is not None:
                record["id"] = piece["id"]
            if piece["name"] is not None:
                record["name"] = piece["name"]

            # 参数每一片都带一小段，直接接在后面
            if piece["args"] is not None:
                record["args_text"] = record["args_text"] + piece["args"]

    def get_calls(self):
        """返回拼好的工具调用列表，参数文字会尝试解析成字典。"""
        result = []
        for index in sorted(self.calls.keys()):
            record = self.calls[index]

            # 参数拼出来不一定是合法的 JSON（比如输出被截断），所以要用 try 接住
            try:
                args = json.loads(record["args_text"])
            except Exception:
                args = None

            result.append({
                "id": record["id"],
                "name": record["name"],
                "args_text": record["args_text"],
                "args": args,
            })
        return result

    def reset(self):
        """一次模型回复结束后清空，准备拼下一次。"""
        self.calls = {}