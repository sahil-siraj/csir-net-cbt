"""
Safe, idempotent one-way sync from local csir_net.db -> Turso cloud database.

Uses Turso's Hrana v2 HTTPS pipeline endpoint (/v2/pipeline) via Python's
built-in urllib.request so it runs natively on Windows without requiring the
libsql C++ extension.

Syncs ONLY:
  - `pyqs` (all rows)
  - `generated_questions` (only where `verified = 1`)

CRITICAL: Never drops, modifies, or overwrites `users`, `mock_tests`,
`test_responses`, `bookmarks`, or `question_flags` in Turso.
"""

import argparse
import base64
import json
import math
import os
import sqlite3
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Iterable, Sequence

ROOT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_LOCAL_DB = ROOT_DIR / "csir_net.db"

CREATE_PYQS_SQL = """
CREATE TABLE IF NOT EXISTS pyqs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    question_text TEXT UNIQUE,
    options_json TEXT,
    correct_option TEXT,
    syllabus_unit TEXT,
    part TEXT,
    exam_year TEXT
)
"""

CREATE_GENERATED_SQL = """
CREATE TABLE IF NOT EXISTS generated_questions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    syllabus_unit TEXT,
    part TEXT,
    question_text TEXT UNIQUE,
    options_json TEXT,
    correct_option TEXT,
    explanation TEXT,
    gen_model TEXT,
    verify_model TEXT,
    verifier_answer TEXT,
    verified INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    subtopic TEXT
)
"""

INSERT_PYQS_SQL_TEMPLATE = """
INSERT OR {conflict_action} INTO pyqs (
    id, question_text, options_json, correct_option, syllabus_unit, part, exam_year
)
VALUES (?, ?, ?, ?, ?, ?, ?)
"""

INSERT_GENERATED_SQL_TEMPLATE = """
INSERT OR {conflict_action} INTO generated_questions (
    id, syllabus_unit, part, question_text, options_json, correct_option,
    explanation, gen_model, verify_model, verifier_answer, verified,
    created_at, subtopic
)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""


def load_env_file(env_path: Path) -> None:
    """Load .env variables using python-dotenv if present, else a stdlib parser."""
    try:
        from dotenv import load_dotenv

        load_dotenv(env_path)
        return
    except ImportError:
        pass

    if not env_path.exists():
        return

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
            os.environ.setdefault(key, val)


def chunked(items: Sequence[Any], size: int) -> Iterable[Sequence[Any]]:
    for i in range(0, len(items), size):
        yield items[i : i + size]


def resolve_local_db(cli_path: str | None) -> Path:
    if cli_path:
        return Path(cli_path).expanduser().resolve()
    env_path = os.getenv("LOCAL_DB_PATH", "").strip()
    if env_path:
        p = Path(env_path)
        if not p.is_absolute():
            # Backend config resolves relative LOCAL_DB_PATH from backend/
            candidate_backend = (ROOT_DIR / "backend" / p).resolve()
            if candidate_backend.exists():
                return candidate_backend
            return (ROOT_DIR / p).resolve()
        return p
    return DEFAULT_LOCAL_DB


def read_local_questions(db_path: Path) -> tuple[list[tuple[Any, ...]], list[tuple[Any, ...]]]:
    if not db_path.exists():
        raise FileNotFoundError(f"Local SQLite database not found: {db_path}")

    uri = f"file:{db_path.as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=5.0)
    try:
        conn.execute("PRAGMA busy_timeout=5000;")
        pyqs = conn.execute(
            """
            SELECT id, question_text, options_json, correct_option,
                   syllabus_unit, part, exam_year
            FROM pyqs
            ORDER BY id ASC
            """
        ).fetchall()

        gen_qs = conn.execute(
            """
            SELECT id, syllabus_unit, part, question_text, options_json,
                   correct_option, explanation, gen_model, verify_model,
                   verifier_answer, verified, created_at, subtopic
            FROM generated_questions
            WHERE verified = 1
            ORDER BY id ASC
            """
        ).fetchall()
        return pyqs, gen_qs
    finally:
        conn.close()


def build_pipeline_url(turso_url: str) -> str:
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
        raise ValueError(f"Invalid Turso URL (missing host): {turso_url}")

    base_path = parsed.path.rstrip("/")
    if not base_path.endswith("/v2/pipeline"):
        base_path = f"{base_path}/v2/pipeline"
    return urllib.parse.urlunsplit((scheme, parsed.netloc, base_path, "", ""))


def encode_hrana_arg(value: Any) -> dict[str, Any]:
    if value is None:
        return {"type": "null"}
    if isinstance(value, bool):
        return {"type": "integer", "value": "1" if value else "0"}
    if isinstance(value, int):
        return {"type": "integer", "value": str(value)}
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return {"type": "null"}
        return {"type": "float", "value": value}
    if isinstance(value, (bytes, bytearray, memoryview)):
        return {"type": "blob", "base64": base64.b64encode(bytes(value)).decode("ascii")}
    return {"type": "text", "value": str(value)}


def decode_hrana_cell(cell: dict[str, Any]) -> Any:
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


def run_turso_pipeline(
    endpoint: str,
    turso_token: str,
    statements: Sequence[tuple[str, Sequence[Any]]],
    *,
    transactional: bool = False,
    timeout: float = 30.0,
) -> list[dict[str, Any]]:
    requests_payload: list[dict[str, Any]] = []
    if transactional:
        requests_payload.append({"type": "execute", "stmt": {"sql": "BEGIN"}})

    for sql, args in statements:
        stmt: dict[str, Any] = {"sql": sql}
        if args:
            stmt["args"] = [encode_hrana_arg(a) for a in args]
        requests_payload.append({"type": "execute", "stmt": stmt})

    if transactional:
        requests_payload.append({"type": "execute", "stmt": {"sql": "COMMIT"}})

    requests_payload.append({"type": "close"})

    body = json.dumps({"requests": requests_payload}).encode("utf-8")
    req = urllib.request.Request(
        endpoint,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {turso_token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "csir-net-prep-turso-sync/1.0",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw_resp = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        err_body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"Turso HTTP pipeline request failed ({exc.code} {exc.reason}): {err_body}"
        ) from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Turso HTTP connection failed: {exc.reason}") from exc

    payload = json.loads(raw_resp)
    results = payload.get("results", [])
    if len(results) != len(requests_payload):
        raise RuntimeError(
            f"Unexpected Turso pipeline response length: expected {len(requests_payload)}, got {len(results)}"
        )

    for idx, item in enumerate(results):
        if item.get("type") == "error":
            err = item.get("error", {})
            msg = err.get("message", "Unknown Turso error")
            code = err.get("code", "")
            raise RuntimeError(f"Turso SQL step {idx} failed [{code}]: {msg}")

    start_idx = 1 if transactional else 0
    end_idx = start_idx + len(statements)
    return [results[i]["response"]["result"] for i in range(start_idx, end_idx)]


def sync_to_turso(
    pyqs: list[tuple[Any, ...]],
    gen_qs: list[tuple[Any, ...]],
    turso_url: str,
    turso_token: str,
    batch_size: int,
    replace_existing: bool = False,
) -> None:
    endpoint = build_pipeline_url(turso_url)
    conflict_action = "REPLACE" if replace_existing else "IGNORE"
    insert_pyqs_sql = INSERT_PYQS_SQL_TEMPLATE.format(conflict_action=conflict_action)
    insert_generated_sql = INSERT_GENERATED_SQL_TEMPLATE.format(conflict_action=conflict_action)

    # Ensure ONLY question tables exist; never touch user/test tables
    init_results = run_turso_pipeline(
        endpoint,
        turso_token,
        [
            (CREATE_PYQS_SQL, ()),
            (CREATE_GENERATED_SQL, ()),
            ("SELECT COUNT(*) FROM pyqs", ()),
            ("SELECT COUNT(*) FROM generated_questions", ()),
        ],
    )
    before_pyqs = decode_hrana_cell(init_results[2]["rows"][0][0])
    before_gen = decode_hrana_cell(init_results[3]["rows"][0][0])

    for batch in chunked(pyqs, batch_size):
        run_turso_pipeline(
            endpoint,
            turso_token,
            [(insert_pyqs_sql, row) for row in batch],
            transactional=True,
        )

    for batch in chunked(gen_qs, batch_size):
        run_turso_pipeline(
            endpoint,
            turso_token,
            [(insert_generated_sql, row) for row in batch],
            transactional=True,
        )

    count_results = run_turso_pipeline(
        endpoint,
        turso_token,
        [
            ("SELECT COUNT(*) FROM pyqs", ()),
            ("SELECT COUNT(*) FROM generated_questions", ()),
        ],
    )
    after_pyqs = decode_hrana_cell(count_results[0]["rows"][0][0])
    after_gen = decode_hrana_cell(count_results[1]["rows"][0][0])

    print(
        f"[sync] pyqs: local={len(pyqs)} | turso_before={before_pyqs} -> "
        f"turso_after={after_pyqs} (+{after_pyqs - before_pyqs} new)"
    )
    print(
        f"[sync] generated_questions (verified=1): local={len(gen_qs)} | "
        f"turso_before={before_gen} -> turso_after={after_gen} (+{after_gen - before_gen} new)"
    )


def main() -> int:
    load_env_file(ROOT_DIR / ".env")

    parser = argparse.ArgumentParser(
        description="Idempotently sync local PYQs and verified AI questions to Turso over HTTPS."
    )
    parser.add_argument(
        "--local-db",
        default=None,
        help="Optional path to local csir_net.db (defaults to project root csir_net.db).",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=100,
        help="Rows per Turso HTTP pipeline batch (default: 100).",
    )
    parser.add_argument(
        "--replace",
        action="store_true",
        help="Use INSERT OR REPLACE instead of INSERT OR IGNORE.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Read local counts without writing to Turso.",
    )
    args = parser.parse_args()

    if args.batch_size < 1:
        print("Error: --batch-size must be >= 1", file=sys.stderr)
        return 1

    local_db = resolve_local_db(args.local_db)
    pyqs, gen_qs = read_local_questions(local_db)
    print(
        f"[sync] Local DB ({local_db}): {len(pyqs)} pyqs, "
        f"{len(gen_qs)} verified generated_questions"
    )

    if args.dry_run:
        print("[sync] Dry run enabled; skipping Turso writes.")
        return 0

    turso_url = os.getenv("TURSO_DATABASE_URL", "").strip()
    turso_token = os.getenv("TURSO_AUTH_TOKEN", "").strip()
    if not turso_url or not turso_token:
        print(
            "Error: TURSO_DATABASE_URL and TURSO_AUTH_TOKEN must be set in .env or environment.",
            file=sys.stderr,
        )
        return 1

    sync_to_turso(
        pyqs,
        gen_qs,
        turso_url,
        turso_token,
        args.batch_size,
        replace_existing=args.replace,
    )
    print("[sync] Sync complete. User progress tables in Turso were untouched.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
