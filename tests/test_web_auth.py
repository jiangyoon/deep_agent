import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

import config
import web.app as web_app
from core.memory_store import read_memory
from core.user_store import UserStore
from test_memory_isolation import ToolModel


class WebAuthTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        for setting, name in [("AUTH_DB_PATH", "users.db"), ("MEMORY_DB_PATH", "memory.db"),
                              ("SESSION_DB_PATH", "sessions.db")]:
            p = patch.object(config, setting, str(Path(self.tmp.name) / name))
            p.start()
            self.addCleanup(p.stop)
        self.store = UserStore()
        self.alice = self.store.create_user("alice", "alice-password")
        self.store.create_user("bob", "bob-password")
        p = patch.object(web_app, "create_llm", side_effect=lambda: ToolModel(responses=[
            AIMessage(content="测试回答", usage_metadata={"input_tokens": 12,
                      "output_tokens": 3, "total_tokens": 15})
        ]))
        self.llm = p.start()
        self.addCleanup(p.stop)
        self.client = TestClient(web_app.app)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)

    def login(self, name="alice"):
        response = self.client.post("/api/auth/login", json={
            "username": name, "password": name + "-password"})
        self.assertEqual(response.status_code, 200, response.text)
        return response

    def create_chat(self):
        r = self.client.post("/api/sessions")
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()["id"]

    def test_anonymous_protected_and_legacy_routes_removed(self):
        for method, url in [("GET", "/api/auth/me"), ("POST", "/api/auth/logout"),
                            ("GET", "/api/sessions"), ("POST", "/api/sessions"),
                            ("GET", "/api/sessions/unknown"), ("DELETE", "/api/sessions/unknown"),
                            ("POST", "/api/sessions/unknown/stream")]:
            self.assertEqual(self.client.request(method, url, json={"question": "hello"}).status_code, 401)
        for url in ["/api/chat", "/api/chat/stream"]:
            self.assertEqual(self.client.post(url, json={"question": "hello"}).status_code, 404)
        self.assertEqual(self.client.get("/health").status_code, 200)
        self.llm.assert_not_called()

    def test_page_access_redirects(self):
        r = self.client.get("/", follow_redirects=False)
        self.assertEqual(r.status_code, 303)
        self.assertEqual(r.headers["location"], "/login")
        self.assertEqual(self.client.get("/login").status_code, 200)
        self.login()
        self.assertEqual(self.client.get("/").status_code, 200)
        r = self.client.get("/login", follow_redirects=False)
        self.assertEqual(r.status_code, 303)
        self.assertEqual(r.headers["location"], "/")

    def test_login_cookie_logout_and_wrong_password(self):
        self.assertEqual(self.client.post("/api/auth/login", json={
            "username": "alice", "password": "wrong-password"}).status_code, 401)
        r = self.login()
        cookie = r.headers["set-cookie"].lower()
        for expected in ["httponly", "samesite=lax", "max-age=604800", "path=/"]:
            self.assertIn(expected, cookie)
        self.assertEqual(self.client.get("/api/auth/me").json(), self.alice)
        token = self.client.cookies.get(config.AUTH_COOKIE_NAME)
        # 再次登录撤销旧凭证。
        self.login()
        self.assertIsNone(self.store.resolve_login(token))
        token = self.client.cookies.get(config.AUTH_COOKIE_NAME)
        self.assertEqual(self.client.post("/api/auth/logout").status_code, 204)
        self.assertIsNone(self.store.resolve_login(token))
        self.assertEqual(self.client.get("/api/auth/me").status_code, 401)

    def test_expired_and_forged_cookie(self):
        self.login()
        with self.store.connect() as conn:
            conn.execute("UPDATE auth_sessions SET expires_at = 0")
        self.assertEqual(self.client.get("/api/sessions").status_code, 401)
        self.client.cookies.set(config.AUTH_COOKIE_NAME, "forged")
        self.assertEqual(self.client.get("/api/auth/me").status_code, 401)

    def test_https_cookie_secure(self):
        with patch.object(config, "AUTH_COOKIE_SECURE", True):
            response = self.login()
            self.assertIn("Secure", response.headers["set-cookie"])

    def test_reasoning_and_real_tool_events(self):
        self.llm.side_effect = lambda: ToolModel(responses=[
            AIMessage(content="", additional_kwargs={"reasoning_content": "调用除法工具"},
                      tool_calls=[{"name": "divide", "id": "division", "args": {"a": 8, "b": 2}}]),
            AIMessage(content="结果是 4"),
        ])
        self.login()
        sid = self.create_chat()
        r = self.client.post(f"/api/sessions/{sid}/stream", json={"question": "8 除以 2"})
        self.assertEqual(r.status_code, 200)
        for event in ["reasoning_delta", "tool_call", "tool_result", "answer_delta", "done"]:
            self.assertIn("event: " + event, r.text)
        self.assertNotIn("event: error", r.text)

    def test_busy_generation_rejected_without_blocking_or_model_call(self):
        self.login()
        sid = self.create_chat()
        lock = web_app.app.state.session_run_lock
        executor = ThreadPoolExecutor(max_workers=1)
        lock.acquire()
        try:
            pending = executor.submit(self.client.post, f"/api/sessions/{sid}/stream",
                                      json={"question": "busy"})
            r = pending.result(timeout=2)
            self.assertEqual(r.status_code, 409)
            self.assertEqual(self.client.delete(f"/api/sessions/{sid}").status_code, 409)
            self.llm.assert_not_called()
        finally:
            lock.release()
            executor.shutdown(wait=True)
        self.assertEqual(self.client.delete(f"/api/sessions/{sid}").status_code, 204)

    def test_cross_origin_writes_rejected(self):
        for headers in [{"Origin": "https://other.example"}, {"Sec-Fetch-Site": "cross-site"},
                        {"Origin": "null"}]:
            self.assertEqual(self.client.post("/api/auth/login", headers=headers,
                json={"username": "alice", "password": "alice-password"}).status_code, 403)
        self.login()
        self.assertEqual(self.client.post("/api/sessions", headers={
            "Origin": "http://testserver"}).status_code, 200)
        self.assertEqual(self.client.post("/api/auth/logout", headers={
            "Origin": "https://other.example"}).status_code, 403)

    def test_private_api_responses_are_not_cached(self):
        self.assertEqual(self.client.get("/api/sessions").headers.get("cache-control"), "no-store")
        self.login()
        sid = self.create_chat()
        for path in ["/api/sessions", f"/api/sessions/{sid}"]:
            self.assertEqual(self.client.get(path).headers.get("cache-control"), "no-store")

    def test_owner_isolation_empty_chat_and_unknown_stream(self):
        self.login()
        sid = self.create_chat()
        self.assertEqual(self.client.get(f"/api/sessions/{sid}").json()["messages"], [])
        self.assertEqual([s["id"] for s in self.client.get("/api/sessions").json()], [sid])
        self.login("bob")
        self.assertEqual(self.client.get("/api/sessions").json(), [])
        for method, path in [("GET", sid), ("DELETE", sid), ("POST", sid + "/stream"),
                             ("POST", "invented/stream")]:
            r = self.client.request(method, "/api/sessions/" + path, json={"question": "hello"})
            self.assertEqual(r.status_code, 404, r.text)
        self.assertTrue(self.store.owns_chat(self.alice["id"], sid))
        self.llm.assert_not_called()

    def test_real_stream_persistence_context_and_delete(self):
        self.login()
        sid = self.create_chat()
        url = f"/api/sessions/{sid}"
        for question in ["第一问", "继续"]:
            r = self.client.post(url + "/stream", json={"question": question, "user_id": "bob"})
            self.assertEqual(r.status_code, 200)
            self.assertIn("event: answer_delta", r.text)
            self.assertIn("event: done", r.text)
            self.assertNotIn("event: error", r.text)
        detail = self.client.get(url).json()
        self.assertEqual(len(detail["messages"]), 4)
        self.assertEqual(detail["context_usage"]["used_tokens"], 15)
        self.assertIsNotNone(read_memory(web_app.app.state.memory_store, self.alice["id"]))
        # 同一 Cookie、同一临时数据库，重新建立应用生命周期。
        with TestClient(web_app.app) as restarted:
            restarted.cookies.update(self.client.cookies)
            self.assertEqual(restarted.get(url).json(), detail)
            self.assertEqual(restarted.delete(url).status_code, 204)
            self.assertEqual(restarted.get(url).status_code, 404)
            self.assertEqual(restarted.post(url + "/stream", json={"question": "again"}).status_code, 404)
            self.assertIsNone(web_app.app.state.session_saver.get_tuple(
                {"configurable": {"thread_id": sid}}))


if __name__ == "__main__":
    unittest.main()
