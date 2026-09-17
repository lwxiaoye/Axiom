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
        valid = hmac.compare_digest(self.password_hash(password, row[1]), row[2])
        return valid and hmac.compare_digest(username.encode(), row[0].encode())

    def create_session(self, ttl: int) -> str:
        token = secrets.token_urlsafe(32)
        with self.connect() as db:
            db.execute("DELETE FROM sessions WHERE expires <= ?", (time.time(),))
            db.execute("INSERT INTO sessions VALUES (?, ?)", (digest(token), time.time() + ttl))
        return token

    def valid_session(self, token: str) -> bool:
        with self.connect() as db:
            return db.execute("SELECT 1 FROM sessions WHERE token_hash=? AND expires>?",
                              (digest(token), time.time())).fetchone() is not None

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
