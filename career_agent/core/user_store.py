"""存取 PostgreSQL 中的用户、登录凭证和 Web 会话归属。"""

import hashlib
import hmac
import re
import secrets
import time
import uuid
from contextlib import contextmanager

from psycopg.errors import UniqueViolation

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
    def __init__(self, pool):
        self.pool = pool

    @contextmanager
    def connect(self):
        with self.pool.connection() as connection, connection.transaction():
            yield connection

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
                conn.execute("INSERT INTO users (id, username, password_hash, created_at) VALUES (%s, %s, %s, %s)",
                             (user["id"], username, encoded, int(time.time())))
        except UniqueViolation:
            raise ValueError("用户名已存在") from None
        return user

    def authenticate(self, username: str, password: str) -> dict | None:
        row = None
        if "\x00" not in username:
            with self.connect() as conn:
                row = conn.execute(
                    "SELECT id, username, password_hash FROM users WHERE username = %s", (username,)
                ).fetchone()
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
            conn.execute("DELETE FROM auth_sessions WHERE expires_at <= %s", (now,))
            conn.execute("INSERT INTO auth_sessions (token_hash, user_id, expires_at) VALUES (%s, %s, %s)",
                         (token_hash(token), user_id, now + config.AUTH_SESSION_TTL_SECONDS))
        return token

    def resolve_login(self, token: str) -> dict | None:
        with self.connect() as conn:
            row = conn.execute("""
                SELECT u.id, u.username FROM auth_sessions a
                JOIN users u ON u.id = a.user_id
                WHERE a.token_hash = %s AND a.expires_at > %s
            """, (token_hash(token), int(time.time()))).fetchone()
        return dict(row) if row else None

    def delete_login(self, token: str):
        with self.connect() as conn:
            conn.execute("DELETE FROM auth_sessions WHERE token_hash = %s", (token_hash(token),))

    def create_chat(self, user_id: str) -> str:
        session_id = uuid.uuid4().hex
        with self.connect() as conn:
            conn.execute("INSERT INTO chat_sessions (id, user_id, created_at) VALUES (%s, %s, %s)",
                         (session_id, user_id, int(time.time())))
        return session_id

    def owns_chat(self, user_id: str, session_id: str) -> bool:
        if "\x00" in session_id:
            return False
        with self.connect() as conn:
            return conn.execute("SELECT 1 FROM chat_sessions WHERE id = %s AND user_id = %s",
                                (session_id, user_id)).fetchone() is not None

    def list_chats(self, user_id: str) -> list[str]:
        with self.connect() as conn:
            rows = conn.execute("""SELECT id FROM chat_sessions WHERE user_id = %s
                                   ORDER BY created_at DESC, chat_order DESC""", (user_id,)).fetchall()
        return [row["id"] for row in rows]

    def delete_chat(self, user_id: str, session_id: str) -> bool:
        with self.connect() as conn:
            return conn.execute("DELETE FROM chat_sessions WHERE id = %s AND user_id = %s",
                                (session_id, user_id)).rowcount == 1
