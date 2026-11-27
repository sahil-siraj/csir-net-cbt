"""
CLI manager for single-use, optionally expiring CSIR NET CBT invite codes.

Connects via backend/app/db.py so it targets local csir_net.db or remote Turso
automatically depending on TURSO_DATABASE_URL / TURSO_AUTH_TOKEN in .env.
"""

import argparse
import secrets
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT_DIR / "backend"))

from app.config import settings  # noqa: E402
from app.db import execute, get_connection, init_db, query_all, query_one, utc_now_iso  # noqa: E402

# Unambiguous uppercase alphanumeric characters (omits 0, O, 1, I)
CODE_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"


def generate_code_string() -> str:
    part1 = "".join(secrets.choice(CODE_ALPHABET) for _ in range(4))
    part2 = "".join(secrets.choice(CODE_ALPHABET) for _ in range(4))
    return f"CSIR-{part1}-{part2}"


def cmd_generate(count: int, days: int | None) -> int:
    if count < 1:
        print("Error: --count must be at least 1.", file=sys.stderr)
        return 1
    if days is not None and days < 1:
        print("Error: --days must be at least 1.", file=sys.stderr)
        return 1

    now = datetime.now(timezone.utc)
    created_at = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    expires_at = (
        (now + timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
        if days is not None
        else None
    )

    generated: list[str] = []
    with get_connection() as conn:
        for _ in range(count):
            while True:
                code = generate_code_string()
                existing = conn.execute(
                    "SELECT 1 FROM invite_codes WHERE code = ?", (code,)
                ).fetchone()
                if not existing:
                    conn.execute(
                        """
                        INSERT INTO invite_codes (code, is_used, expires_at, created_at)
                        VALUES (?, 0, ?, ?)
                        """,
                        (code, expires_at, created_at),
                    )
                    generated.append(code)
                    break
        conn.commit()

    expiry_label = expires_at if expires_at else "Never"
    print(f"[{settings.db_mode.upper()}] Generated {len(generated)} invite code(s) (expires: {expiry_label}):")
    for code in generated:
        print(f"  {code}")
    return 0


def cmd_list() -> int:
    rows = query_all(
        """
        SELECT
            ic.code,
            ic.is_used,
            ic.used_at,
            ic.expires_at,
            ic.created_at,
            u.username AS claimed_by
        FROM invite_codes ic
        LEFT JOIN users u ON u.id = ic.used_by_user_id
        ORDER BY ic.created_at DESC, ic.id DESC
        """
    )
    if not rows:
        print(f"[{settings.db_mode.upper()}] No invite codes found.")
        return 0

    now_iso = utc_now_iso()
    formatted_rows = []
    for r in rows:
        if int(r["is_used"]) == 1:
            status = "USED"
        elif r["expires_at"] is not None and r["expires_at"] <= now_iso:
            status = "EXPIRED"
        else:
            status = "AVAILABLE"

        formatted_rows.append(
            (
                r["code"],
                status,
                r["claimed_by"] or "-",
                r["expires_at"] or "Never",
                r["used_at"] or "-",
            )
        )

    headers = ("CODE", "STATUS", "CLAIMED BY", "EXPIRES AT (UTC)", "USED AT (UTC)")
    widths = [
        max(len(headers[i]), *(len(row[i]) for row in formatted_rows))
        for i in range(len(headers))
    ]

    def fmt_line(cols):
        return "  ".join(cols[i].ljust(widths[i]) for i in range(len(cols)))

    print(f"[{settings.db_mode.upper()}] Invite Codes ({len(formatted_rows)} total):")
    print(fmt_line(headers))
    print("  ".join("-" * w for w in widths))
    for row in formatted_rows:
        print(fmt_line(row))
    return 0


def cmd_revoke(raw_code: str) -> int:
    code = raw_code.strip().upper()
    row = query_one(
        "SELECT id, is_used, expires_at FROM invite_codes WHERE code = ?",
        (code,),
    )
    if not row:
        print(f"Error: Invite code '{code}' not found.", file=sys.stderr)
        return 1
    if int(row["is_used"]) == 1:
        print(f"Error: Invite code '{code}' has already been used and cannot be revoked.", file=sys.stderr)
        return 1

    past_iso = (datetime.now(timezone.utc) - timedelta(seconds=1)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    execute(
        "UPDATE invite_codes SET expires_at = ? WHERE code = ? AND is_used = 0",
        (past_iso, code),
    )
    print(f"[{settings.db_mode.upper()}] Revoked invite code {code} (expired at {past_iso}).")
    return 0


def main() -> int:
    init_db()

    parser = argparse.ArgumentParser(
        description="Manage single-use invite codes for CSIR NET CBT Exam App."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    gen_p = sub.add_parser("generate", help="Generate new single-use invite codes.")
    gen_p.add_argument("--count", type=int, default=12, help="Number of codes to generate (default: 12).")
    gen_p.add_argument("--days", type=int, default=None, help="Optional expiry in days from now.")

    sub.add_parser("list", help="List all invite codes and their status.")

    rev_p = sub.add_parser("revoke", help="Immediately expire/revoke an unused invite code.")
    rev_p.add_argument("code", help="Invite code to revoke (e.g., CSIR-ABCD-EFGH).")

    args = parser.parse_args()
    if args.command == "generate":
        return cmd_generate(args.count, args.days)
    if args.command == "list":
        return cmd_list()
    if args.command == "revoke":
        return cmd_revoke(args.code)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
