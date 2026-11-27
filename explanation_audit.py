import argparse
import json
import re
import sqlite3
from collections import defaultdict

DB_PATH = "csir_net.db"
BACKUP_PATH = "csir_net.db.pre_expl_audit.bak"
REPORT_PATH = "explanation_audit_report.json"

FLAW_PATTERNS = [
    (
        "none_of_options",
        re.compile(
            r"\bnone\s+of\s+the\s+(?:listed\s+|given\s+|above\s+|provided\s+)?(?:options|choices)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "needs_correction",
        re.compile(r"\b(?:requires?|needs?)\s+(?:a\s+)?correction\b", re.IGNORECASE),
    ),
    (
        "no_correct_option",
        re.compile(r"\bno\s+correct\s+(?:option|answer|choice)\b", re.IGNORECASE),
    ),
    (
        "not_in_options",
        re.compile(
            r"\b(?:not\s+among|missing\s+from|absent\s+from)\s+the\s+(?:given\s+|listed\s+)?(?:options|choices)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "closest_option",
        re.compile(r"\bclosest\s+(?:option|answer|match|choice)\b", re.IGNORECASE),
    ),
    (
        "typo_misprint_flawed",
        re.compile(r"\b(?:typo|misprint|erroneous|flawed)\b", re.IGNORECASE),
    ),
    (
        "answer_should_be",
        re.compile(r"\b(?:correct\s+)?answer\s+should\s+be\b", re.IGNORECASE),
    ),
]

ANSWER_LETTER_PATTERNS = [
    re.compile(
        r"\b(?:correct\s+(?:answer|option|choice)\s*(?:is|:|=)\s*(?:option\s*)?\(?([A-D])\)?)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:(?:option|choice)\s+\(?([A-D])\)?\s+is\s+(?:the\s+)?(?:correct|right|best|unique))\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:therefore|thus|hence|consequently|so)\s*,?\s*(?:the\s+)?(?:(?:correct|right)\s+)?(?:(?:answer|option|choice)\s*(?:is|:)?\s*)?\(?([A-D])\)?(?:\s+is\s+(?:the\s+)?(?:correct|right|best|unique)|\s*(?=[.,;]|$))",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:^|\n)\s*(?:answer\s*(?:is|:|=)\s*(?:option\s*)?\(?([A-D])\)?)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:^|[.!?]\s+)\(?([A-D])\)?\s+is\s+(?:the\s+)?(?:correct|right|best|unique)\s+(?:option|answer|choice)\b",
        re.IGNORECASE,
    ),
]


def extract_snippet(text: str, start: int, end: int, pad: int = 75) -> str:
    lo = max(0, start - pad)
    hi = min(len(text), end + pad)
    snippet = text[lo:hi].replace("\n", " ").strip()
    if lo > 0:
        snippet = "..." + snippet
    if hi < len(text):
        snippet = snippet + "..."
    return snippet


def scan_flaws(explanation: str) -> list[dict]:
    hits = []
    for name, pat in FLAW_PATTERNS:
        m = pat.search(explanation)
        if m:
            hits.append(
                {
                    "rule": name,
                    "match": m.group(0),
                    "snippet": extract_snippet(explanation, m.start(), m.end()),
                }
            )
    return hits


def scan_letter_mismatch(explanation: str, correct_option: str) -> dict | None:
    matches = []
    for pat in ANSWER_LETTER_PATTERNS:
        for m in pat.finditer(explanation):
            letter = m.group(1).upper()
            matches.append((m.start(), m.end(), letter, m.group(0)))

    if not matches:
        return None

    # Sort by position in explanation and inspect the last explicit final-answer assertion
    matches.sort(key=lambda x: x[0])
    start, end, declared_letter, raw_match = matches[-1]
    if declared_letter != correct_option:
        return {
            "declared_letter": declared_letter,
            "stored_correct_option": correct_option,
            "match": raw_match,
            "snippet": extract_snippet(explanation, start, end),
        }
    return None


def print_grouped_counts(title: str, flagged_rows: list[dict]):
    counts = defaultdict(int)
    for row in flagged_rows:
        counts[(row["part"], row["gen_model"])] += 1

    print(f"\n=== {title} (Total: {len(flagged_rows)}) ===")
    if not counts:
        print("  None flagged.")
        return
    for (part, gen_model), cnt in sorted(counts.items()):
        print(f"  Part {part} | {gen_model:<15}: {cnt}")


def apply_retire_flaws(conn: sqlite3.Connection, flaw_rows: list[dict]):
    ids_to_retire = sorted({int(r["id"]) for r in flaw_rows})
    dst = sqlite3.connect(BACKUP_PATH)
    conn.backup(dst)
    dst.close()
    print(f"\nBackup created: {BACKUP_PATH}")

    for qid in ids_to_retire:
        conn.execute("UPDATE generated_questions SET verified = 0 WHERE id = ?", (qid,))
    conn.commit()

    remaining = conn.execute(
        "SELECT COUNT(*) FROM generated_questions WHERE verified = 1"
    ).fetchone()[0]
    print(f"Retired (set verified=0) on Check #1 flaws: {len(ids_to_retire)} rows")
    print(f"Remaining verified=1 rows: {remaining}")


def main():
    parser = argparse.ArgumentParser(
        description="Audit explanations of verified=1 generated questions"
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Back up DB and set verified=0 on rows flagged under Check #1",
    )
    args = parser.parse_args()

    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        """
        SELECT id, syllabus_unit, part, gen_model, correct_option, explanation
        FROM generated_questions
        WHERE verified = 1
        ORDER BY id ASC
        """
    ).fetchall()

    print(f"Auditing {len(rows)} verified=1 rows in generated_questions...")

    flaw_flagged = []
    mismatch_flagged = []

    for qid, unit, part, gen_model, corr, expl in rows:
        expl_text = str(expl or "")
        corr_clean = str(corr or "").strip().upper()

        flaws = scan_flaws(expl_text)
        if flaws:
            flaw_flagged.append(
                {
                    "id": int(qid),
                    "syllabus_unit": unit,
                    "part": part,
                    "gen_model": gen_model,
                    "correct_option": corr_clean,
                    "flaw_hits": flaws,
                    "snippet": flaws[0]["snippet"],
                }
            )

        mismatch = scan_letter_mismatch(expl_text, corr_clean)
        if mismatch:
            mismatch_flagged.append(
                {
                    "id": int(qid),
                    "syllabus_unit": unit,
                    "part": part,
                    "gen_model": gen_model,
                    "correct_option": corr_clean,
                    **mismatch,
                }
            )

    print_grouped_counts("Check #1: Self-Admitted Flaw Scan", flaw_flagged)
    print_grouped_counts("Check #2: Option-Letter Mismatch Scan (Post-Shuffle)", mismatch_flagged)

    print("\n=== Sample Check #1 Flagged Rows (up to 15) ===")
    for r in flaw_flagged[:15]:
        rules = ", ".join(h["rule"] for h in r["flaw_hits"])
        print(
            f"  ID #{r['id']} | Part {r['part']} | {r['gen_model']} | Ans: {r['correct_option']} | [{rules}]\n"
            f"    Snippet: {r['snippet']}"
        )

    print("\n=== Sample Check #2 Flagged Rows (up to 15) ===")
    for r in mismatch_flagged[:15]:
        print(
            f"  ID #{r['id']} | Part {r['part']} | {r['gen_model']} | Stored: {r['correct_option']} vs Expl: {r['declared_letter']}\n"
            f"    Snippet: {r['snippet']}"
        )

    report = {
        "total_verified_audited": len(rows),
        "check_1_self_admitted_flaws": {
            "total": len(flaw_flagged),
            "rows": flaw_flagged,
        },
        "check_2_option_letter_mismatches": {
            "total": len(mismatch_flagged),
            "rows": mismatch_flagged,
        },
    }
    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    print(f"\nSaved full audit report to {REPORT_PATH}")

    if args.apply:
        apply_retire_flaws(conn, flaw_flagged)

    conn.close()


if __name__ == "__main__":
    main()
