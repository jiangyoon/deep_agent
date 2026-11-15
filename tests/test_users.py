import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch
import manage_users

from core.user_store import UserStore


class UserStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "users.db"
        self.store = UserStore(self.path)
        self.alice = self.store.create_user("alice", "alice-password")

    def test_password_hash_and_authentication(self):
        bob = self.store.create_user("bob", "alice-password")
        with closing(sqlite3.connect(self.path)) as conn:
            hashes = [r[0] for r in conn.execute("SELECT password_hash FROM users")]
        self.assertNotEqual(hashes[0], hashes[1])
        self.assertNotIn("alice-password", str(hashes))
        self.assertEqual(self.store.authenticate("alice", "alice-password"), self.alice)
        self.assertIsNone(self.store.authenticate("alice", "wrong-password"))
        self.assertIsNone(self.store.authenticate("missing", "alice-password"))
        self.assertNotEqual(self.alice["id"], bob["id"])

    def test_validation_and_duplicate(self):
        for name, password in [("", "long-password"), ("alice", "short"), ("a b", "long-password")]:
            with self.assertRaises(ValueError):
                self.store.create_user(name, password)
        with self.assertRaises(ValueError):
            self.store.create_user("alice", "another-password")

    def test_session_expiration_logout_and_restart(self):
        with patch("core.user_store.time.time", return_value=1000):
            token = self.store.create_login(self.alice["id"])
            self.assertEqual(UserStore(self.path).resolve_login(token), self.alice)
        with closing(sqlite3.connect(self.path)) as conn:
            self.assertNotIn(token, str(conn.execute("SELECT * FROM auth_sessions").fetchall()))
        with patch("core.user_store.time.time", return_value=1000 + 7 * 24 * 3600):
            self.assertIsNone(self.store.resolve_login(token))
        token = self.store.create_login(self.alice["id"])
        self.store.delete_login(token)
        self.assertIsNone(self.store.resolve_login(token))
        self.assertIsNone(self.store.resolve_login("invented"))

    def test_owned_sessions_survive_restart(self):
        bob = self.store.create_user("bob", "bob-password")
        sid = self.store.create_chat(self.alice["id"])
        self.assertTrue(self.store.owns_chat(self.alice["id"], sid))
        self.assertFalse(self.store.owns_chat(bob["id"], sid))
        self.assertEqual(UserStore(self.path).list_chats(self.alice["id"]), [sid])
        self.assertEqual(self.store.list_chats(bob["id"]), [])
        self.assertFalse(self.store.delete_chat(bob["id"], sid))
        self.assertTrue(self.store.delete_chat(self.alice["id"], sid))
        self.assertFalse(self.store.owns_chat(self.alice["id"], sid))

    def test_cli_creates_user_with_hidden_password_prompt(self):
        with patch("sys.argv", ["manage_users.py", "create", "cli_user"]), \
             patch("manage_users.UserStore", return_value=self.store), \
             patch("getpass.getpass", side_effect=["cli-password", "cli-password"]), \
             patch("builtins.print"):
            manage_users.main()
        self.assertIsNotNone(self.store.authenticate("cli_user", "cli-password"))

    def test_cli_rejects_password_mismatch(self):
        with patch("sys.argv", ["manage_users.py", "create", "cli_user"]), \
             patch("getpass.getpass", side_effect=["cli-password", "different"]), \
             patch("manage_users.UserStore") as store, \
             patch("sys.stderr"), self.assertRaises(SystemExit) as error:
            manage_users.main()
        self.assertEqual(error.exception.code, 1)
        store.assert_not_called()


if __name__ == "__main__":
    unittest.main()
