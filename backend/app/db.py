import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterable, Sequence

from app.config import settings


def utc_now_iso() -> str:
    """Return current UTC timestamp in YYYY-MM-DDTHH:MM:SSZ format."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _open_connection():
    if settings.use_turso:
        import libsql

        return libsql.connect(
            database=settings.TURSO_DATABASE_URL,
            auth_token=settings.TURSO_AUTH_TOKEN,
        )

    conn = sqlite3.connect(
        settings.LOCAL_DB_PATH,
        timeout=5.0,
        check_same_thread=False,
    )
    conn.execute("PRAGMA busy_timeout=5000;")
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


@contextmanager
def get_connection():
    conn = _open_connection()
    try:
        yield conn
    finally:
        conn.close()


def _cursor_to_dicts(cursor) -> list[dict[str, Any]]:
    if not cursor.description:
        return []
    columns = [col[0] for col in cursor.description]
    rows = cursor.fetchall()
    return [dict(zip(columns, row)) for row in rows]


def query_all(sql: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
    with get_connection() as conn:
        cursor = conn.execute(sql, tuple(params))
        return _cursor_to_dicts(cursor)


def query_one(sql: str, params: Sequence[Any] = ()) -> dict[str, Any] | None:
    with get_connection() as conn:
        cursor = conn.execute(sql, tuple(params))
        if not cursor.description:
            return None
        columns = [col[0] for col in cursor.description]
        row = cursor.fetchone()
        if row is None:
            return None
        return dict(zip(columns, row))


def execute(sql: str, params: Sequence[Any] = ()) -> int:
    with get_connection() as conn:
        cursor = conn.execute(sql, tuple(params))
        conn.commit()
        if sql.lstrip().upper().startswith("INSERT"):
            return int(cursor.lastrowid or 0)
        return int(cursor.rowcount if cursor.rowcount is not None else 0)


def execute_many(sql: str, seq_of_params: Iterable[Sequence[Any]]) -> int:
    params_list = [tuple(p) for p in seq_of_params]
    if not params_list:
        return 0
    with get_connection() as conn:
        cursor = conn.executemany(sql, params_list)
        conn.commit()
        return int(cursor.rowcount if cursor.rowcount is not None else len(params_list))


SCHEMA_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT NOT NULL UNIQUE,
        display_name TEXT NOT NULL,
        password_hash TEXT NOT NULL,
        role TEXT NOT NULL DEFAULT 'student',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS mock_tests (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL REFERENCES users(id),
        test_type TEXT NOT NULL,
        title TEXT,
        status TEXT NOT NULL DEFAULT 'in_progress',
        source_filter TEXT NOT NULL DEFAULT 'mixed',
        instant_feedback INTEGER NOT NULL DEFAULT 0,
        duration_minutes INTEGER,
        config_json TEXT,
        total_questions INTEGER NOT NULL DEFAULT 0,
        total_score REAL DEFAULT 0,
        max_score REAL DEFAULT 0,
        negative_loss REAL DEFAULT 0,
        correct_count INTEGER DEFAULT 0,
        incorrect_count INTEGER DEFAULT 0,
        unanswered_count INTEGER DEFAULT 0,
        summary_json TEXT,
        started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        submitted_at TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS test_responses (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        test_id INTEGER NOT NULL REFERENCES mock_tests(id),
        user_id INTEGER NOT NULL REFERENCES users(id),
        question_source TEXT NOT NULL,
        question_id INTEGER NOT NULL,
        part TEXT NOT NULL,
        syllabus_unit TEXT NOT NULL,
        question_order INTEGER NOT NULL,
        selected_option TEXT,
        status TEXT NOT NULL DEFAULT 'not_visited',
        time_spent_sec INTEGER NOT NULL DEFAULT 0,
        is_checked INTEGER NOT NULL DEFAULT 0,
        is_correct INTEGER,
        is_evaluated INTEGER,
        marks_awarded REAL DEFAULT 0,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(test_id, question_source, question_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS bookmarks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL REFERENCES users(id),
        question_source TEXT NOT NULL,
        question_id INTEGER NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(user_id, question_source, question_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS question_flags (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL REFERENCES users(id),
        question_source TEXT NOT NULL,
        question_id INTEGER NOT NULL,
        reason TEXT NOT NULL,
        details TEXT,
        resolved INTEGER NOT NULL DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS invite_codes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        code TEXT UNIQUE NOT NULL,
        is_used INTEGER NOT NULL DEFAULT 0,
        used_by_user_id INTEGER,
        used_at TEXT,
        expires_at TEXT,
        created_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_invite_codes_is_used ON invite_codes(is_used)",
    "CREATE INDEX IF NOT EXISTS idx_mock_tests_user_id ON mock_tests(user_id)",
    "CREATE INDEX IF NOT EXISTS idx_test_responses_user_id ON test_responses(user_id)",
    "CREATE INDEX IF NOT EXISTS idx_test_responses_test_id ON test_responses(test_id)",
    "CREATE INDEX IF NOT EXISTS idx_test_responses_question ON test_responses(question_source, question_id)",
    "CREATE INDEX IF NOT EXISTS idx_bookmarks_user_id ON bookmarks(user_id)",
    "CREATE INDEX IF NOT EXISTS idx_bookmarks_question ON bookmarks(question_source, question_id)",
    "CREATE INDEX IF NOT EXISTS idx_question_flags_user_id ON question_flags(user_id)",
    "CREATE INDEX IF NOT EXISTS idx_question_flags_question ON question_flags(question_source, question_id)",
]


def init_db() -> None:
    with get_connection() as conn:
        for stmt in SCHEMA_STATEMENTS:
            conn.execute(stmt)
        conn.commit()
