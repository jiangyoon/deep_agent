"""用户、登录凭证和 Web 会话归属。每次操作使用独立 SQLite 连接。"""

import hashlib
import hmac
import re
import secrets
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from career_agent import config


def password_hash(password: str, salt: str | None = None) -> str:
    """每个密码使用独立随机盐；格式包含算法和迭代次数，便于识别。"""
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 600_000)
    return f"pbkdf2_sha256$600000${salt}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    return hmac.compare_digest(password_hash(password, encoded.split("$")[2]), encoded)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class UserStore:
    def __init__(self, path=None):
        self.path = Path(path) if path is not None else config.PROJECT_DIR / config.AUTH_DB_PATH
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY,
                    username TEXT NOT NULL UNIQUE,
                    password_hash TEXT NOT NULL,
                    created_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS auth_sessions (
                    token_hash TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL REFERENCES users(id),
                    expires_at INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS auth_expiry ON auth_sessions(expires_at);
                CREATE TABLE IF NOT EXISTS chat_sessions (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL REFERENCES users(id),
                    created_at INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS chat_owner ON chat_sessions(user_id, created_at);
            """)

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def create_user(self, username: str, password: str) -> dict:
        """用户名 1–64 位字母、数字、下划线或连字符；密码 8–128 位。"""
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", username):
            raise ValueError("用户名须为 1–64 位字母、数字、下划线或连字符")
        if not 8 <= len(password) <= 128:
            raise ValueError("密码长度须为 8–128 位")
        user = {"id": uuid.uuid4().hex, "username": username}
        encoded = password_hash(password)
        try:
            with self.connect() as conn:
                conn.execute("INSERT INTO users VALUES (?, ?, ?, ?)",
                             (user["id"], username, encoded, int(time.time())))
        except sqlite3.IntegrityError:
            raise ValueError("用户名已存在") from None
        return user

    def authenticate(self, username: str, password: str) -> dict | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        # 不存在的账号也执行同等成本的哈希，避免快速区分用户是否存在。
        encoded = row["password_hash"] if row else (
            "pbkdf2_sha256$600000$" + "00" * 16 + "$" + "00" * 32
        )
        valid = verify_password(password, encoded)
        if not row or not valid:
            return None
        return {"id": row["id"], "username": row["username"]}

    def create_login(self, user_id: str) -> str:
        token = secrets.token_urlsafe(32)
        now = int(time.time())
        with self.connect() as conn:
            conn.execute("DELETE FROM auth_sessions WHERE expires_at <= ?", (now,))
            conn.execute("INSERT INTO auth_sessions VALUES (?, ?, ?)",
                         (token_hash(token), user_id, now + config.AUTH_SESSION_TTL_SECONDS))
        return token

    def resolve_login(self, token: str) -> dict | None:
        with self.connect() as conn:
            row = conn.execute("""
                SELECT u.id, u.username FROM auth_sessions a
                JOIN users u ON u.id = a.user_id
                WHERE a.token_hash = ? AND a.expires_at > ?
            """, (token_hash(token), int(time.time()))).fetchone()
        return dict(row) if row else None

    def delete_login(self, token: str):
        with self.connect() as conn:
            conn.execute("DELETE FROM auth_sessions WHERE token_hash = ?", (token_hash(token),))

    def create_chat(self, user_id: str) -> str:
        session_id = uuid.uuid4().hex
        with self.connect() as conn:
            conn.execute("INSERT INTO chat_sessions VALUES (?, ?, ?)",
                         (session_id, user_id, int(time.time())))
        return session_id

    def owns_chat(self, user_id: str, session_id: str) -> bool:
        with self.connect() as conn:
            return conn.execute("SELECT 1 FROM chat_sessions WHERE id = ? AND user_id = ?",
                                (session_id, user_id)).fetchone() is not None

    def list_chats(self, user_id: str) -> list[str]:
        with self.connect() as conn:
            rows = conn.execute("""SELECT id FROM chat_sessions WHERE user_id = ?
                                   ORDER BY created_at DESC, rowid DESC""", (user_id,)).fetchall()
        return [row["id"] for row in rows]

    def delete_chat(self, user_id: str, session_id: str) -> bool:
        with self.connect() as conn:
            return conn.execute("DELETE FROM chat_sessions WHERE id = ? AND user_id = ?",
                                (session_id, user_id)).rowcount == 1
