"""Users and sign-in sessions, kept in a small SQLite file next to the
uploads (APP_DB_PATH). The knowledge graph stays in FalkorDB; this is only
the application's own bookkeeping."""

import hashlib
import secrets
import sqlite3
import threading
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterator, Literal, Optional

Role = Literal["admin", "doctor", "student"]
ROLES: tuple[str, ...] = ("admin", "doctor", "student")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    email TEXT PRIMARY KEY,
    name TEXT NOT NULL DEFAULT '',
    role TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    invited_by TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL,
    last_login_at REAL
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    email TEXT NOT NULL REFERENCES users(email) ON DELETE CASCADE,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS sessions_email ON sessions(email);
"""


@dataclass
class User:
    email: str
    name: str
    role: str
    active: bool = True
    invited_by: str = ""
    created_at: float = 0.0
    last_login_at: Optional[float] = None

    @property
    def can_ingest(self) -> bool:
        return self.role in ("admin", "doctor")

    @property
    def can_see_patient_records(self) -> bool:
        return self.role in ("admin", "doctor")

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"

    def to_dict(self) -> dict:
        return asdict(self)


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _row_to_user(row: sqlite3.Row) -> User:
    return User(
        email=row["email"],
        name=row["name"],
        role=row["role"],
        active=bool(row["active"]),
        invited_by=row["invited_by"],
        created_at=row["created_at"],
        last_login_at=row["last_login_at"],
    )


class AppStore:
    def __init__(self, path: str):
        self.path = path
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        # One shared connection, serialized by the lock: the app makes few,
        # small writes, and this keeps ":memory:" stores working in tests.
        self._conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        if path != ":memory:":
            self._conn.execute("PRAGMA journal_mode = WAL")
        self._conn.executescript(_SCHEMA)
        self._migrations()

    def _migrations(self) -> None:
        """Later features add their own tables through this hook."""

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self._conn.execute("BEGIN")
            try:
                yield self._conn
                self._conn.execute("COMMIT")
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise

    # Users

    def get_user(self, email: str) -> Optional[User]:
        with self.tx() as db:
            row = db.execute("SELECT * FROM users WHERE email = ?", (email.lower(),)).fetchone()
        return _row_to_user(row) if row else None

    def list_users(self) -> list[User]:
        with self.tx() as db:
            rows = db.execute("SELECT * FROM users ORDER BY role, email").fetchall()
        return [_row_to_user(r) for r in rows]

    def add_user(self, email: str, role: str, name: str = "", invited_by: str = "") -> User:
        if role not in ROLES:
            raise ValueError(f"Unknown role '{role}'")
        email = email.strip().lower()
        with self.tx() as db:
            db.execute(
                "INSERT INTO users (email, name, role, invited_by, created_at) VALUES (?, ?, ?, ?, ?)",
                (email, name, role, invited_by, time.time()),
            )
        return self.get_user(email)  # type: ignore[return-value]

    def update_user(
        self, email: str, role: Optional[str] = None, active: Optional[bool] = None, name: Optional[str] = None
    ) -> Optional[User]:
        if role is not None and role not in ROLES:
            raise ValueError(f"Unknown role '{role}'")
        email = email.lower()
        with self.tx() as db:
            if role is not None:
                db.execute("UPDATE users SET role = ? WHERE email = ?", (role, email))
            if name is not None:
                db.execute("UPDATE users SET name = ? WHERE email = ?", (name, email))
            if active is not None:
                db.execute("UPDATE users SET active = ? WHERE email = ?", (int(active), email))
                if not active:
                    db.execute("DELETE FROM sessions WHERE email = ?", (email,))
        return self.get_user(email)

    def delete_user(self, email: str) -> bool:
        with self.tx() as db:
            db.execute("DELETE FROM sessions WHERE email = ?", (email.lower(),))
            cursor = db.execute("DELETE FROM users WHERE email = ?", (email.lower(),))
        return cursor.rowcount > 0

    def record_login(self, email: str, name: str) -> None:
        with self.tx() as db:
            db.execute(
                "UPDATE users SET last_login_at = ?, name = CASE WHEN name = '' THEN ? ELSE name END WHERE email = ?",
                (time.time(), name, email.lower()),
            )

    # Sessions

    def create_session(self, email: str, days: int) -> str:
        token = secrets.token_urlsafe(32)
        now = time.time()
        with self.tx() as db:
            db.execute("DELETE FROM sessions WHERE expires_at < ?", (now,))
            db.execute(
                "INSERT INTO sessions (token_hash, email, created_at, expires_at) VALUES (?, ?, ?, ?)",
                (_hash_token(token), email.lower(), now, now + days * 86400),
            )
        return token

    def user_for_session(self, token: str) -> Optional[User]:
        with self.tx() as db:
            row = db.execute(
                "SELECT u.* FROM sessions s JOIN users u ON u.email = s.email "
                "WHERE s.token_hash = ? AND s.expires_at > ? AND u.active = 1",
                (_hash_token(token), time.time()),
            ).fetchone()
        return _row_to_user(row) if row else None

    def delete_session(self, token: str) -> None:
        with self.tx() as db:
            db.execute("DELETE FROM sessions WHERE token_hash = ?", (_hash_token(token),))


_store: Optional[AppStore] = None
_store_lock = threading.Lock()


def get_store() -> AppStore:
    global _store
    with _store_lock:
        if _store is None:
            from jivaka.config import get_settings

            _store = AppStore(get_settings().app_db_path)
        return _store


def set_store(store: Optional[AppStore]) -> None:
    """For tests: swap in an in-memory store."""
    global _store
    with _store_lock:
        _store = store
