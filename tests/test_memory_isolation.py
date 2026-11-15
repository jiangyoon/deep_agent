import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, AIMessageChunk
from langchain_core.outputs import ChatGenerationChunk

import config
from core.agent_builder import build_agent
from core.backend_builder import memory_namespace
from core.memory_store import open_store, read_memory, seed_memory_if_missing, write_memory


class ToolModel(FakeMessagesListChatModel):
    def bind_tools(self, tools, **kwargs):
        return self

    def _stream(self, messages, stop=None, run_manager=None, **kwargs):
        message = self._generate(messages, stop=stop).generations[0].message
        yield ChatGenerationChunk(message=AIMessageChunk(**message.model_dump(exclude={"type"})))


class MemoryIsolationTests(unittest.TestCase):
    def test_web_namespace_requires_identity(self):
        for runtime in [None, SimpleNamespace(context=None), SimpleNamespace(context={})]:
            with self.assertRaises(ValueError):
                memory_namespace(runtime, require_user=True)

    def test_real_agent_writes_only_current_users_memory(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(config, "MEMORY_DB_PATH", str(Path(tmp) / "m.db")):
            with open_store() as store:
                seed_memory_if_missing(store, user_id="alice")
                seed_memory_if_missing(store, user_id="bob")
                write_memory(store, "# 用户记忆\n\nalice-only", user_id="alice")
                write_memory(store, "# 用户记忆\n\nbob-only", user_id="bob")
                llm = ToolModel(responses=[
                    AIMessage(content="", tool_calls=[{
                        "name": "edit_file", "id": "memory-edit", "args": {
                            "file_path": "/memories/AGENTS.md",
                            "old_string": "alice-only", "new_string": "alice-updated",
                        },
                    }]),
                    AIMessage(content="已更新"),
                    AIMessage(content="", tool_calls=[{
                        "name": "edit_file", "id": "bob-edit", "args": {
                            "file_path": "/memories/AGENTS.md",
                            "old_string": "bob-only", "new_string": "bob-updated",
                        },
                    }]),
                    AIMessage(content="已更新 Bob"),
                ])
                agent = build_agent(llm, tools=[], use_skills=False, use_memory=True,
                                    store=store, require_user=True)
                result = agent.invoke({"messages": [{"role": "user", "content": "更新记忆"}]},
                                      context={"user_id": "alice"})
                self.assertEqual(result["messages"][-1].content, "已更新")
                self.assertIn("alice-updated", read_memory(store, user_id="alice"))
                self.assertIn("bob-only", read_memory(store, user_id="bob"))
                agent.invoke({"messages": [{"role": "user", "content": "更新 Bob 记忆"}]},
                             context={"user_id": "bob"})
                self.assertIn("bob-updated", read_memory(store, user_id="bob"))
                self.assertIn("alice-updated", read_memory(store, user_id="alice"))
                self.assertIsNone(read_memory(store))
                with self.assertRaises(ValueError):
                    agent.invoke({"messages": [{"role": "user", "content": "没有身份"}]})


if __name__ == "__main__":
    unittest.main()
