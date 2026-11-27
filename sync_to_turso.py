"""
Standalone migration script to sync local csir_net.db -> Turso over HTTP (/v2/pipeline).

Uses only pure Python (sqlite3, urllib.request, json, plus python-dotenv if installed
with a built-in stdlib .env fallback) so it runs cleanly on Windows Python 3.14+
without requiring the libsql C++ extension.

Usage:
    python sync_to_turso.py
    python sync_to_turso.py --check-only
"""

from __future__ import annotations

import argparse
import base64
import json
import math
import os
import re
import sqlite3
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Iterable, Sequence

ROOT_DIR = Path(__file__).resolve().parent
DEFAULT_DB_PATH = ROOT_DIR / "csir_net.db"
DEFAULT_ENV_PATH = ROOT_DIR / ".env.turso"

# Ordered so parent tables (users, mock_tests) precede FK-referencing tables.
TARGET_TABLES: tuple[str, ...] = (
    "users",
    "mock_tests",
    "test_responses",
    "bookmarks",
    "question_flags",
    "pyqs",
    "generated_questions",
    "invite_codes",
)

# Tables whose schema is created on Turso but whose local rows are NOT uploaded.
EMPTY_CLOUD_TABLES: tuple[str, ...] = (
    "users",
    "mock_tests",
    "test_responses",
    "bookmarks",
    "question_flags",
)

VERIFY_COUNT_TABLES: tuple[str, ...] = (
    "pyqs",
    "generated_questions",
    "invite_codes",
    "users",
)

BATCH_SIZE = 100


def load_turso_config(env_path: Path) -> tuple[str, str]:
    """Load TURSO_DATABASE_URL and TURSO_AUTH_TOKEN from .env.turso, falling back to env vars."""
    file_values: dict[str, str] = {}

    if env_path.exists():
        try:
            from dotenv import dotenv_values

            raw_values = dotenv_values(env_path)
            file_values = {k: (v or "").strip() for k, v in raw_values.items() if k}
        except ImportError:
            for raw_line in env_path.read_text(encoding="utf-8").splitlines():
                line = raw_line.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("export "):
                    line = line[len("export ") :].strip()
                if "=" not in line:
                    continue
                key, val = line.split("=", 1)
                key = key.strip()
                val = val.strip()
                if len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"'):
                    val = val[1:-1]
                if key:
                    file_values[key] = val

    turso_url = file_values.get("TURSO_DATABASE_URL") or os.getenv("TURSO_DATABASE_URL", "").strip()
    turso_token = file_values.get("TURSO_AUTH_TOKEN") or os.getenv("TURSO_AUTH_TOKEN", "").strip()
    return turso_url, turso_token


def build_pipeline_url(turso_url: str) -> str:
    """Convert libsql:// (or https://) Turso URL into the /v2/pipeline HTTPS endpoint."""
    raw = turso_url.strip()
    if "://" not in raw:
        raw = f"https://{raw}"
    parsed = urllib.parse.urlsplit(raw)
    scheme_map = {
        "libsql": "https",
        "wss": "https",
        "ws": "http",
        "https": "https",
        "http": "http",
    }
    scheme = scheme_map.get(parsed.scheme.lower())
    if not scheme:
        raise ValueError(f"Unsupported Turso URL scheme: {parsed.scheme}")
    if not parsed.netloc:
        raise ValueError(f"Invalid TURSO_DATABASE_URL (missing host): {turso_url}")

    base_path = parsed.path.rstrip("/")
    if not base_path.endswith("/v2/pipeline"):
        base_path = f"{base_path}/v2/pipeline"
    return urllib.parse.urlunsplit((scheme, parsed.netloc, base_path, "", ""))


def encode_arg(value: Any) -> dict[str, Any]:
    """Encode a Python value into a Turso Hrana HTTP v2 argument object."""
    if value is None:
        return {"type": "null"}
    if isinstance(value, bool):
        return {"type": "integer", "value": "1" if value else "0"}
    if isinstance(value, int):
        return {"type": "integer", "value": str(value)}
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return {"type": "null"}
        return {"type": "float", "value": float(value)}
    if isinstance(value, (bytes, bytearray, memoryview)):
        return {"type": "blob", "base64": base64.b64encode(bytes(value)).decode("ascii")}
    return {"type": "text", "value": str(value)}


def decode_cell(cell: dict[str, Any]) -> Any:
    """Decode a Turso Hrana HTTP v2 result cell to a Python value."""
    cell_type = cell.get("type")
    if cell_type == "null":
        return None
    if cell_type == "integer":
        return int(cell["value"])
    if cell_type == "float":
        return float(cell["value"])
    if cell_type == "text":
        return cell["value"]
    if cell_type == "blob":
        return base64.b64decode(cell["base64"])
    return cell.get("value")


def post_pipeline(
    endpoint: str,
    token: str,
    statements: Sequence[tuple[str, Sequence[Any]]],
    timeout: float = 60.0,
) -> list[dict[str, Any]]:
    """Send a batch of SQL statements to Turso /v2/pipeline and fail loudly on any error."""
    requests_payload: list[dict[str, Any]] = []
    for sql, args in statements:
        stmt: dict[str, Any] = {"sql": sql}
        if args:
            stmt["args"] = [encode_arg(v) for v in args]
        requests_payload.append({"type": "execute", "stmt": stmt})
    requests_payload.append({"type": "close"})

    body = json.dumps({"requests": requests_payload}).encode("utf-8")
    req = urllib.request.Request(
        endpoint,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "csir-net-prep-sync-to-turso/1.0",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw_body = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        err_text = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"Turso HTTP error {exc.code} {exc.reason} at {endpoint}: {err_text}"
        ) from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Turso connection error at {endpoint}: {exc.reason}") from exc

    payload = json.loads(raw_body)
    results = payload.get("results")
    if not isinstance(results, list) or len(results) != len(requests_payload):
        raise RuntimeError(
            f"Unexpected Turso pipeline response (expected {len(requests_payload)} results): {payload}"
        )

    for idx, item in enumerate(results):
        if item.get("type") == "error":
            err = item.get("error") or {}
            code = err.get("code", "UNKNOWN")
            msg = err.get("message", "Unknown Turso SQL error")
            failed_sql = statements[idx][0].strip() if idx < len(statements) else "<close>"
            raise RuntimeError(
                f"Turso pipeline error at step {idx} [{code}]: {msg}\nSQL: {failed_sql}"
            )
        if item.get("type") != "ok":
            raise RuntimeError(f"Unexpected Turso result item at step {idx}: {item}")

    return [results[i]["response"]["result"] for i in range(len(statements))]


def chunked(items: Sequence[Any], size: int) -> Iterable[Sequence[Any]]:
    for i in range(0, len(items), size):
        yield items[i : i + size]


def make_idempotent_ddl(sql: str, obj_type: str) -> str:
    """Convert CREATE TABLE / CREATE INDEX DDL to IF NOT EXISTS form."""
    cleaned = sql.strip().rstrip(";")
    if obj_type == "table":
        return re.sub(
            r"^CREATE\s+TABLE\s+(?!IF\s+NOT\s+EXISTS\b)",
            "CREATE TABLE IF NOT EXISTS ",
            cleaned,
            count=1,
            flags=re.IGNORECASE,
        )
    if obj_type == "index":
        return re.sub(
            r"^CREATE\s+(UNIQUE\s+)?INDEX\s+(?!IF\s+NOT\s+EXISTS\b)",
            r"CREATE \1INDEX IF NOT EXISTS ",
            cleaned,
            count=1,
            flags=re.IGNORECASE,
        )
    raise ValueError(f"Unsupported DDL object type: {obj_type}")


def read_schema_ddl(conn: sqlite3.Connection) -> tuple[list[str], list[str]]:
    """Read CREATE TABLE and CREATE INDEX statements for TARGET_TABLES (excluding sqlite_sequence, cost_log)."""
    placeholders = ", ".join("?" for _ in TARGET_TABLES)
    rows = conn.execute(
        f"""
        SELECT type, name, tbl_name, sql
        FROM sqlite_master
        WHERE sql IS NOT NULL
          AND type IN ('table', 'index')
          AND tbl_name IN ({placeholders})
          AND tbl_name NOT IN ('sqlite_sequence', 'cost_log')
        """,
        TARGET_TABLES,
    ).fetchall()

    table_order = {name: idx for idx, name in enumerate(TARGET_TABLES)}
    table_ddls: list[tuple[int, str]] = []
    index_ddls: list[tuple[int, str, str]] = []

    for obj_type, name, tbl_name, sql in rows:
        if obj_type == "table":
            table_ddls.append((table_order[tbl_name], make_idempotent_ddl(sql, "table")))
        elif obj_type == "index":
            index_ddls.append((table_order[tbl_name], name, make_idempotent_ddl(sql, "index")))

    table_ddls.sort(key=lambda item: item[0])
    index_ddls.sort(key=lambda item: (item[0], item[1]))
    return [sql for _, sql in table_ddls], [sql for _, _, sql in index_ddls]


def sync_schema(conn: sqlite3.Connection, endpoint: str, token: str) -> None:
    table_ddls, index_ddls = read_schema_ddl(conn)
    all_ddls = table_ddls + index_ddls
    print(f"[schema] Applying {len(table_ddls)} tables and {len(index_ddls)} indexes to Turso...")
    for batch in chunked(all_ddls, BATCH_SIZE):
        post_pipeline(endpoint, token, [(sql, ()) for sql in batch])
    print(
        "[schema] Schema synced. Cloud tables kept empty (no local rows uploaded): "
        + ", ".join(EMPTY_CLOUD_TABLES)
    )


def upload_table(
    conn: sqlite3.Connection,
    endpoint: str,
    token: str,
    table_name: str,
    where_clause: str = "",
) -> int:
    query = f"SELECT * FROM {table_name}"
    if where_clause:
        query += f" WHERE {where_clause}"
    query += " ORDER BY id ASC"

    cursor = conn.execute(query)
    columns = [col[0] for col in cursor.description]
    rows = cursor.fetchall()
    total = len(rows)

    col_list = ", ".join(columns)
    placeholders = ", ".join("?" for _ in columns)
    insert_sql = f"INSERT OR REPLACE INTO {table_name} ({col_list}) VALUES ({placeholders})"

    label = f"{table_name} ({where_clause})" if where_clause else table_name
    if total == 0:
        print(f"[upload] {label}: 0 rows to upload.")
        return 0

    uploaded = 0
    total_batches = (total + BATCH_SIZE - 1) // BATCH_SIZE
    for batch_idx, batch in enumerate(chunked(rows, BATCH_SIZE), start=1):
        stmts = [(insert_sql, row) for row in batch]
        post_pipeline(endpoint, token, stmts)
        uploaded += len(batch)
        print(
            f"[upload] {label}: batch {batch_idx}/{total_batches} "
            f"({uploaded}/{total} rows)"
        )

    return uploaded


def fetch_local_summary(conn: sqlite3.Connection) -> tuple[dict[str, int], int, dict[str, int]]:
    counts: dict[str, int] = {}
    for tbl in VERIFY_COUNT_TABLES:
        counts[tbl] = conn.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]

    gen_verified_count = conn.execute(
        "SELECT COUNT(*) FROM generated_questions WHERE verified = 1"
    ).fetchone()[0]

    part_rows = conn.execute(
        """
        SELECT COALESCE(part, '<NULL>') AS part_name, COUNT(*)
        FROM generated_questions
        WHERE verified = 1
        GROUP BY part
        ORDER BY part_name ASC
        """
    ).fetchall()
    part_counts = {part_name: cnt for part_name, cnt in part_rows}
    return counts, gen_verified_count, part_counts


def fetch_remote_summary(endpoint: str, token: str) -> tuple[dict[str, int], dict[str, int]]:
    statements: list[tuple[str, Sequence[Any]]] = [
        (f"SELECT COUNT(*) FROM {tbl}", ()) for tbl in VERIFY_COUNT_TABLES
    ]
    statements.append(
        (
            "SELECT COALESCE(part, '<NULL>') AS part_name, COUNT(*) "
            "FROM generated_questions GROUP BY part ORDER BY part_name ASC",
            (),
        )
    )

    results = post_pipeline(endpoint, token, statements)
    counts: dict[str, int] = {}
    for idx, tbl in enumerate(VERIFY_COUNT_TABLES):
        counts[tbl] = int(decode_cell(results[idx]["rows"][0][0]))

    part_counts: dict[str, int] = {}
    for row in results[-1]["rows"]:
        part_name = str(decode_cell(row[0]))
        part_counts[part_name] = int(decode_cell(row[1]))

    return counts, part_counts


def verify_and_print(conn: sqlite3.Connection, endpoint: str, token: str) -> None:
    local_counts, local_gen_verified, local_parts = fetch_local_summary(conn)
    remote_counts, remote_parts = fetch_remote_summary(endpoint, token)

    print("\n=== Post-Sync Verification: Table Counts ===")
    print(f"{'Table':<24} {'Local (target)':>16} {'Turso':>12} {'Note'}")
    print("-" * 74)
    for tbl in VERIFY_COUNT_TABLES:
        if tbl == "generated_questions":
            note = f"local all={local_counts[tbl]} (verified=1 uploaded)"
            local_target = local_gen_verified
        elif tbl == "users":
            note = "intentionally kept empty on cloud"
            local_target = local_counts[tbl]
        else:
            note = "match" if local_counts[tbl] == remote_counts[tbl] else "MISMATCH"
            local_target = local_counts[tbl]

        if tbl == "generated_questions":
            note = (
                f"match; local all={local_counts[tbl]}"
                if local_gen_verified == remote_counts[tbl]
                else f"MISMATCH; local all={local_counts[tbl]}"
            )

        print(f"{tbl:<24} {local_target:>16} {remote_counts[tbl]:>12} {note}")

    print("\n=== generated_questions Breakdown by Part (verified=1) ===")
    print(f"{'Part':<24} {'Local (verified=1)':>18} {'Turso':>12} {'Status'}")
    print("-" * 66)
    all_parts = sorted(set(local_parts) | set(remote_parts))
    for part_name in all_parts:
        lc = local_parts.get(part_name, 0)
        rc = remote_parts.get(part_name, 0)
        status = "match" if lc == rc else "MISMATCH"
        print(f"{part_name:<24} {lc:>18} {rc:>12} {status}")


def open_local_ro(db_path: Path) -> sqlite3.Connection:
    if not db_path.exists():
        raise FileNotFoundError(f"Local SQLite database not found: {db_path}")
    conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=5.0)
    conn.execute("PRAGMA busy_timeout=5000;")
    return conn


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Migrate local csir_net.db schema and content to Turso via HTTP /v2/pipeline."
    )
    parser.add_argument(
        "--db",
        default=str(DEFAULT_DB_PATH),
        help=f"Path to local SQLite DB (default: {DEFAULT_DB_PATH.name}).",
    )
    parser.add_argument(
        "--env-file",
        default=str(DEFAULT_ENV_PATH),
        help=f"Path to Turso env file (default: {DEFAULT_ENV_PATH.name}).",
    )
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="Only run remote COUNT(*) and part breakdown verification without uploading anything.",
    )
    args = parser.parse_args()

    turso_url, turso_token = load_turso_config(Path(args.env_file))
    if not turso_url or not turso_token:
        print(
            "Error: TURSO_DATABASE_URL and TURSO_AUTH_TOKEN must be set in .env.turso or environment.",
            file=sys.stderr,
        )
        return 1

    endpoint = build_pipeline_url(turso_url)
    db_path = Path(args.db).expanduser().resolve()

    conn = open_local_ro(db_path)
    try:
        if args.check_only:
            print(f"[check-only] Querying Turso endpoint {endpoint} (no writes)...")
            verify_and_print(conn, endpoint, turso_token)
            return 0

        print(f"[sync] Source DB: {db_path}")
        print(f"[sync] Target endpoint: {endpoint}")

        sync_schema(conn, endpoint, turso_token)
        upload_table(conn, endpoint, turso_token, "pyqs")
        upload_table(conn, endpoint, turso_token, "generated_questions", where_clause="verified = 1")
        upload_table(conn, endpoint, turso_token, "invite_codes")

        verify_and_print(conn, endpoint, turso_token)
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
