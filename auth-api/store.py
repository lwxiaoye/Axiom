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


# 前端「个人资料」弹窗读写的字段（realname 在 users 建表时已有；administrator 需要补）。
# 值是 ALTER TABLE ADD COLUMN 用的列定义：sex 用 Jeecg 的约定 0 未填 / 1 男 / 2 女。
PROFILE_COLUMNS: dict[str, str] = {
    "realname": "TEXT NOT NULL DEFAULT ''",
    "avatar": "TEXT NOT NULL DEFAULT ''",
    "birthday": "TEXT NOT NULL DEFAULT ''",
    "sex": "INTEGER NOT NULL DEFAULT 0",
    "email": "TEXT NOT NULL DEFAULT ''",
    "phone": "TEXT NOT NULL DEFAULT ''",
}
PROFILE_FIELDS = tuple(PROFILE_COLUMNS)


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
            # 个人资料（头像/生日/性别/邮箱/手机）挂在账号行上：管理员在 administrator，
            # 成员在 users。旧库没有这些列，用幂等 ALTER 原地补齐，重启多少次都安全。
            for table in ("administrator", "users"):
                self._ensure_columns(db, table, PROFILE_COLUMNS)
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT username, salt, password_hash FROM administrator WHERE id=1").fetchone()
            if row is None or row[0] != username or not hmac.compare_digest(
                self.password_hash(password, row[1]), row[2]
            ):
                salt = secrets.token_bytes(32)
                credentials = (username, salt, self.password_hash(password, salt))
                if row is None:
                    db.execute("INSERT INTO administrator (id, username, salt, password_hash) VALUES (1, ?, ?, ?)",
                               credentials)
                else:
                    # 用 UPDATE 而不是 INSERT OR REPLACE：REPLACE 会整行重建，
                    # 轮换密码就把管理员已设置的头像和名称清掉了。
                    db.execute("UPDATE administrator SET username=?, salt=?, password_hash=? WHERE id=1",
                               credentials)
                # Changing the configured account revokes every prior session.
                db.execute("DELETE FROM sessions")
                db.execute("DELETE FROM captchas")
            # Sessions predate multi-user support; add the owner column in place.
            columns = {r[1] for r in db.execute("PRAGMA table_info(sessions)")}
            if "username" not in columns:
                db.execute("ALTER TABLE sessions ADD COLUMN username TEXT NOT NULL DEFAULT ''")
                db.execute("UPDATE sessions SET username=?", (username,))
        self.admin_username = username

    @staticmethod
    def _ensure_columns(db: sqlite3.Connection, table: str, columns: dict[str, str]):
        """给已存在的表补列。SQLite 没有 ADD COLUMN IF NOT EXISTS，先查 PRAGMA 再加。"""
        existing = {r[1] for r in db.execute(f"PRAGMA table_info({table})")}
        for name, definition in columns.items():
            if name not in existing:
                db.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")

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
                "INSERT INTO users (username, realname, salt, password_hash, created_at) VALUES (?, ?, ?, ?, ?)",
                (username, realname or username, salt, digest_, time.time()),
            )
        return ""

    @staticmethod
    def _record(row: sqlite3.Row | tuple, is_admin: bool) -> dict:
        record = {"username": row[0], "created_at": row[1], "is_admin": is_admin}
        record.update(zip(PROFILE_FIELDS, row[2:]))
        if is_admin and not record["realname"]:
            # 管理员没改过名字时沿用旧显示名，前端和以前看到的一致。
            record["realname"] = "管理员"
        return record

    def list_users(self) -> list[dict]:
        columns = ", ".join(PROFILE_FIELDS)
        with self.connect() as db:
            rows = db.execute(
                f"SELECT username, created_at, {columns} FROM users ORDER BY created_at"
            ).fetchall()
        return [self._record(r, False) for r in rows]

    def user_record(self, username: str) -> dict | None:
        columns = ", ".join(PROFILE_FIELDS)
        with self.connect() as db:
            if username == self.admin_username:
                row = db.execute(
                    f"SELECT username, 0.0, {columns} FROM administrator WHERE id=1"
                ).fetchone()
                return self._record(row, True) if row else None
            row = db.execute(
                f"SELECT username, created_at, {columns} FROM users WHERE username=?", (username,)
            ).fetchone()
        if row is None:
            return None
        return self._record(row, False)

    def update_user_profile(self, username: str, fields: dict) -> bool:
        """只写 PROFILE_FIELDS 里的键；调用方负责校验取值。返回是否真的更新了某一行。"""
        updates = {k: v for k, v in fields.items() if k in PROFILE_COLUMNS}
        if not updates:
            return False
        assignments = ", ".join(f"{k}=?" for k in updates)
        with self.connect() as db:
            if username == self.admin_username:
                cursor = db.execute(f"UPDATE administrator SET {assignments} WHERE id=1", tuple(updates.values()))
            else:
                cursor = db.execute(f"UPDATE users SET {assignments} WHERE username=?",
                                    (*updates.values(), username))
        return cursor.rowcount > 0

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
