"""Persistent state for the local single-administrator authentication service."""
from __future__ import annotations

import hashlib
import hmac
import secrets
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


class AuthStore:
    def __init__(self, path: str, username: str, password: str):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS administrator (
                    id INTEGER PRIMARY KEY CHECK (id = 1), username TEXT NOT NULL,
                    salt BLOB NOT NULL, password_hash BLOB NOT NULL);
                CREATE TABLE IF NOT EXISTS sessions (
                    token_hash TEXT PRIMARY KEY, expires REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS users (
                    username TEXT PRIMARY KEY,
                    realname TEXT NOT NULL DEFAULT '',
                    salt BLOB NOT NULL,
                    password_hash BLOB NOT NULL,
                    created_at REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS captchas (
                    key TEXT PRIMARY KEY, code_hash TEXT NOT NULL, expires REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS rate_limits (
                    key TEXT PRIMARY KEY, attempts INTEGER NOT NULL, expires REAL NOT NULL);
            """)
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT username, salt, password_hash FROM administrator WHERE id=1").fetchone()
            if row is None or row[0] != username or not hmac.compare_digest(
                self.password_hash(password, row[1]), row[2]
            ):
                salt = secrets.token_bytes(32)
                db.execute("INSERT OR REPLACE INTO administrator VALUES (1, ?, ?, ?)",
                           (username, salt, self.password_hash(password, salt)))
                # Changing the configured account revokes every prior session.
                db.execute("DELETE FROM sessions")
                db.execute("DELETE FROM captchas")
            # Sessions predate multi-user support; add the owner column in place.
            columns = {r[1] for r in db.execute("PRAGMA table_info(sessions)")}
            if "username" not in columns:
                db.execute("ALTER TABLE sessions ADD COLUMN username TEXT NOT NULL DEFAULT ''")
                db.execute("UPDATE sessions SET username=?", (username,))
        self.admin_username = username

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def password_hash(password: str, salt: bytes) -> bytes:
        return hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 600_000)

    def verify_password(self, username: str, password: str) -> bool:
        with self.connect() as db:
            row = db.execute("SELECT username, salt, password_hash FROM administrator WHERE id=1").fetchone()
            if hmac.compare_digest(username.encode(), row[0].encode()):
                return hmac.compare_digest(self.password_hash(password, row[1]), row[2])
            member = db.execute(
                "SELECT salt, password_hash FROM users WHERE username=?", (username,)
            ).fetchone()
        if member is None:
            # Hash anyway so a missing account costs the same time as a wrong password.
            self.password_hash(password, b"\x00" * 32)
            return False
        return hmac.compare_digest(self.password_hash(password, member[0]), member[1])

    def register_user(self, username: str, password: str, realname: str) -> str:
        """Create a member account. Returns "" on success, or a message on refusal."""
        if username == self.admin_username:
            return "该用户名已被占用"
        salt = secrets.token_bytes(32)
        digest_ = self.password_hash(password, salt)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone():
                return "该用户名已被占用"
            db.execute(
                "INSERT INTO users VALUES (?, ?, ?, ?, ?)",
                (username, realname or username, salt, digest_, time.time()),
            )
        return ""

    def list_users(self) -> list[dict]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT username, realname, created_at FROM users ORDER BY created_at"
            ).fetchall()
        return [{"username": r[0], "realname": r[1], "is_admin": False, "created_at": r[2]} for r in rows]

    def user_record(self, username: str) -> dict | None:
        if username == self.admin_username:
            return {"username": username, "realname": "管理员", "is_admin": True, "created_at": 0.0}
        with self.connect() as db:
            row = db.execute(
                "SELECT username, realname, created_at FROM users WHERE username=?", (username,)
            ).fetchone()
        if row is None:
            return None
        return {"username": row[0], "realname": row[1], "is_admin": False, "created_at": row[2]}

    def create_session(self, ttl: int, username: str) -> str:
        token = secrets.token_urlsafe(32)
        with self.connect() as db:
            db.execute("DELETE FROM sessions WHERE expires <= ?", (time.time(),))
            db.execute("INSERT INTO sessions VALUES (?, ?, ?)",
                       (digest(token), time.time() + ttl, username))
        return token

    def valid_session(self, token: str) -> bool:
        return self.session_username(token) is not None

    def session_username(self, token: str) -> str | None:
        with self.connect() as db:
            row = db.execute("SELECT username FROM sessions WHERE token_hash=? AND expires>?",
                             (digest(token), time.time())).fetchone()
        return row[0] if row else None

    def revoke(self, token: str):
        with self.connect() as db:
            db.execute("DELETE FROM sessions WHERE token_hash=?", (digest(token),))

    def save_captcha(self, key: str, code: str):
        with self.connect() as db:
            db.execute("DELETE FROM captchas WHERE expires <= ?", (time.time(),))
            db.execute("INSERT OR REPLACE INTO captchas VALUES (?, ?, ?)",
                       (key, digest(code.lower()), time.time() + 300))

    def consume_captcha(self, key: str, code: str) -> bool:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT code_hash, expires FROM captchas WHERE key=?", (key,)).fetchone()
            db.execute("DELETE FROM captchas WHERE key=?", (key,))
        return bool(row and row[1] > time.time() and hmac.compare_digest(row[0], digest(code.lower())))

    def allow_attempt(self, key: str, limit: int, window: int) -> bool:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM rate_limits WHERE expires <= ?", (time.time(),))
            row = db.execute("SELECT attempts FROM rate_limits WHERE key=?", (key,)).fetchone()
            if row and row[0] >= limit:
                return False
            if row:
                db.execute("UPDATE rate_limits SET attempts=attempts+1 WHERE key=?", (key,))
            else:
                db.execute("INSERT INTO rate_limits VALUES (?, 1, ?)", (key, time.time() + window))
        return True
