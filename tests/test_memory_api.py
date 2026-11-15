import unittest
from langchain_core.messages import AIMessage

import web.app as web_app
import test_web_auth
from core.memory_store import MEMORY_TEMPLATE, read_memory, write_memory


class CapturingMemoryModel(test_web_auth.ToolModel):
    prompts: list[str] = []

    def _generate(self, messages, **kwargs):
        self.prompts.append(str(messages[0].content))
        return super()._generate(messages, **kwargs)


class MemoryAPITests(unittest.TestCase):
    setUp = test_web_auth.WebAuthTests.setUp
    login = test_web_auth.WebAuthTests.login

    def snapshot(self):
        response = self.client.get("/api/memory")
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_login_required_for_all_memory_operations(self):
        for method in ["GET", "PUT", "DELETE"]:
            r = self.client.request(method, "/api/memory", json={"content": "x", "revision": "0" * 64})
            self.assertEqual(r.status_code, 401)
        self.llm.assert_not_called()

    def test_read_save_and_restart_without_model(self):
        self.login()
        initial = self.snapshot()
        self.assertEqual(initial["content"], "")
        self.assertEqual(initial["char_count"], 0)
        self.assertIsNone(initial["updated_at"])
        text = "# 用户记忆\n\n我喜欢简洁的中文回答。\n"
        r = self.client.put("/api/memory", json={"content": text, "revision": initial["revision"]})
        self.assertEqual(r.status_code, 200, r.text)
        saved = r.json()
        self.assertEqual(saved["content"], text)
        self.assertEqual(saved["char_count"], len(text))
        self.assertIsNotNone(saved["updated_at"])
        self.assertNotEqual(saved["revision"], initial["revision"])
        from fastapi.testclient import TestClient
        with TestClient(web_app.app) as restarted:
            restarted.cookies.update(self.client.cookies)
            self.assertEqual(restarted.get("/api/memory").json(), saved)
        self.llm.assert_not_called()

    def test_users_only_read_and_change_their_own_memory(self):
        self.login()
        a = self.snapshot()
        self.client.put("/api/memory", json={"content": "alice-private", "revision": a["revision"]})
        self.login("bob")
        b = self.client.get("/api/memory", params={"user_id": self.alice["id"]}).json()
        self.assertEqual(b["content"], "")
        r = self.client.put("/api/memory", json={"content": "bob-private", "revision": b["revision"]})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(read_memory(web_app.app.state.memory_store, self.alice["id"]), "alice-private")
        self.client.request("DELETE", "/api/memory", json={"revision": r.json()["revision"]})
        self.login()
        self.assertEqual(self.snapshot()["content"], "alice-private")

    def test_stale_save_and_clear_never_overwrite_new_memory(self):
        self.login()
        old = self.snapshot()
        write_memory(web_app.app.state.memory_store, "Agent 刚更新的记忆", self.alice["id"])
        for method, body in [("PUT", {"content": "旧草稿", "revision": old["revision"]}),
                             ("DELETE", {"revision": old["revision"]})]:
            self.assertEqual(self.client.request(method, "/api/memory", json=body).status_code, 409)
        self.assertEqual(self.snapshot()["content"], "Agent 刚更新的记忆")

    def test_clear_restores_template_and_keeps_other_users_memory(self):
        self.login()
        write_memory(web_app.app.state.memory_store, "private", self.alice["id"])
        snapshot = self.snapshot()
        r = self.client.request("DELETE", "/api/memory", json={"revision": snapshot["revision"]})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["content"], MEMORY_TEMPLATE)
        self.assertEqual(read_memory(web_app.app.state.memory_store, self.alice["id"]), MEMORY_TEMPLATE)

    def test_busy_and_invalid_requests_leave_memory_unchanged(self):
        self.login()
        snapshot = self.snapshot()
        lock = web_app.app.state.session_run_lock
        lock.acquire()
        try:
            for method, body in [("PUT", {"content": "x", "revision": snapshot["revision"]}),
                                 ("DELETE", {"revision": snapshot["revision"]})]:
                self.assertEqual(self.client.request(method, "/api/memory", json=body).status_code, 409)
            self.assertEqual(self.snapshot(), snapshot)
        finally:
            lock.release()
        self.assertEqual(self.client.put("/api/memory", json={"content": "x"}).status_code, 422)
        self.assertEqual(self.client.put("/api/memory", json={
            "content": "x" * 100001, "revision": snapshot["revision"]}).status_code, 422)
        self.assertEqual(self.client.put("/api/memory", headers={"Origin": "https://elsewhere.example"},
            json={"content": "x", "revision": snapshot["revision"]}).status_code, 403)
        self.assertEqual(self.snapshot(), snapshot)

    def test_new_chat_uses_saved_memory_and_clear_keeps_history(self):
        model = CapturingMemoryModel(responses=[AIMessage(content="已回答")])
        self.llm.side_effect = None
        self.llm.return_value = model
        self.login()
        initial = self.snapshot()
        marker = "记忆测试标记：回答前称呼我为小林"
        self.assertEqual(self.client.put("/api/memory", json={
            "content": marker, "revision": initial["revision"]}).status_code, 200)
        first = self.client.post("/api/sessions").json()["id"]
        r = self.client.post(f"/api/sessions/{first}/stream", json={"question": "你好"})
        self.assertIn("event: done", r.text)
        self.assertIn(marker, model.prompts[-1])
        history = self.client.get(f"/api/sessions/{first}").json()
        self.assertEqual(self.client.request("DELETE", "/api/memory", json={
            "revision": self.snapshot()["revision"]}).status_code, 200)
        self.assertEqual(self.client.get(f"/api/sessions/{first}").json(), history)
        second = self.client.post("/api/sessions").json()["id"]
        r = self.client.post(f"/api/sessions/{second}/stream", json={"question": "你好"})
        self.assertIn("event: done", r.text)
        self.assertNotIn(marker, model.prompts[-1])
