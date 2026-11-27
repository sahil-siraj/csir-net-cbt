import argparse
import difflib
import json
import math
import re
import sqlite3
from collections import Counter, defaultdict

try:
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.metrics.pairwise import cosine_similarity

    HAS_SKLEARN = True
except ImportError:
    HAS_SKLEARN = False

DB_PATH = "csir_net.db"
BACKUP_PATH = "csir_net.db.pre_dedup.bak"
REPORT_PATH = "duplicates_report.json"

IMAGE_INDICATORS = (
    "![",
    "<img",
    "figure",
    "diagram",
    "graph",
    "shown below",
    "given below",
    "plot shown",
    "image",
)


def normalize_text(text: str) -> str:
    """Lowercase and strip $, backslashes, punctuation, and extra whitespace."""
    s = str(text or "").lower()
    s = s.replace("$", " ").replace("\\", " ")
    s = re.sub(r"[^\w\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def unit_sort_key(unit_name: str):
    if unit_name == "General Aptitude":
        return (0, 0, unit_name)
    m = re.match(r"Unit\s+(\d+)\b", str(unit_name), re.IGNORECASE)
    if m:
        return (1, int(m.group(1)), str(unit_name))
    return (2, 0, str(unit_name))


def check_backend_sampling_repeats(conn: sqlite3.Connection) -> dict:
    """Check test_responses for duplicate (test_id, question_source, question_id) rows."""
    total_rows = conn.execute("SELECT COUNT(*) FROM test_responses").fetchone()[0]
    repeats = conn.execute(
        """
        SELECT test_id, question_source, question_id, COUNT(*) AS cnt
        FROM test_responses
        GROUP BY test_id, question_source, question_id
        HAVING cnt > 1
        ORDER BY cnt DESC, test_id ASC
        """
    ).fetchall()

    repeat_list = [
        {
            "test_id": r[0],
            "question_source": r[1],
            "question_id": r[2],
            "count": r[3],
        }
        for r in repeats
    ]

    print("\n=== 1. Backend Sampling Repeat Check (test_responses) ===")
    print(f"Total test_responses rows inspected: {total_rows}")
    if repeat_list:
        print(f"[WARN] Found {len(repeat_list)} duplicate question entries within a single test:")
        for item in repeat_list[:20]:
            print(
                f"  test_id={item['test_id']} | {item['question_source']} #{item['question_id']} | count={item['count']}"
            )
    else:
        print("[OK] Zero backend-level duplicate sampling occurrences found in test_responses.")

    return {"total_test_responses": total_rows, "duplicate_entries": repeat_list}


def load_active_questions(conn: sqlite3.Connection) -> list[dict]:
    """Load all usable non-image PYQs and verified generated questions."""
    items = []

    pyq_rows = conn.execute(
        """
        SELECT id, syllabus_unit, part, question_text
        FROM pyqs
        WHERE syllabus_unit IS NOT NULL AND syllabus_unit != ''
          AND part IN ('A', 'B', 'C')
        ORDER BY id ASC
        """
    ).fetchall()

    pyq_excluded = 0
    for qid, unit, part, qtext in pyq_rows:
        q_lower = str(qtext or "").lower()
        if any(ind in q_lower for ind in IMAGE_INDICATORS):
            pyq_excluded += 1
            continue
        items.append(
            {
                "uid": ("pyqs", int(qid)),
                "source": "pyqs",
                "id": int(qid),
                "syllabus_unit": unit,
                "part": part,
                "gen_model": "pyqs",
                "question_text": str(qtext),
                "norm_text": normalize_text(qtext),
            }
        )

    gen_rows = conn.execute(
        """
        SELECT id, syllabus_unit, part, gen_model, question_text
        FROM generated_questions
        WHERE verified = 1
        ORDER BY id ASC
        """
    ).fetchall()

    for qid, unit, part, gen_model, qtext in gen_rows:
        items.append(
            {
                "uid": ("generated", int(qid)),
                "source": "generated",
                "id": int(qid),
                "syllabus_unit": unit or "Unknown",
                "part": part or "?",
                "gen_model": gen_model or "unknown",
                "question_text": str(qtext),
                "norm_text": normalize_text(qtext),
            }
        )

    print("\n=== 2. Loaded Active Question Pool ===")
    print(f"PYQs usable (non-image): {len(pyq_rows) - pyq_excluded} (excluded {pyq_excluded} image-based PYQs)")
    print(f"Generated (verified=1):  {len(gen_rows)}")
    print(f"Total active questions:  {len(items)}")
    return items


def word_shingles_3(norm_text: str) -> set:
    words = norm_text.split()
    if len(words) < 3:
        return set(words)
    return set(zip(words, words[1:], words[2:]))


def jaccard_3gram(text_a: str, text_b: str) -> float:
    sa = word_shingles_3(text_a)
    sb = word_shingles_3(text_b)
    if not sa or not sb:
        return 0.0
    inter = len(sa & sb)
    union = len(sa) + len(sb) - inter
    return inter / union if union else 0.0


def build_pair_record(item_a: dict, item_b: dict, sim: float, tier: str, method: str) -> dict:
    shingle_j = jaccard_3gram(item_a["norm_text"], item_b["norm_text"])
    seq_ratio = difflib.SequenceMatcher(None, item_a["norm_text"], item_b["norm_text"]).ratio()
    return {
        "unit": item_a["syllabus_unit"] if item_a["syllabus_unit"] == item_b["syllabus_unit"] else f"{item_a['syllabus_unit']} vs {item_b['syllabus_unit']}",
        "part": item_a["part"] if item_a["part"] == item_b["part"] else f"{item_a['part']} vs {item_b['part']}",
        "tier": tier,
        "similarity": round(float(sim), 4),
        "shingle_jaccard_3": round(float(shingle_j), 4),
        "difflib_ratio": round(float(seq_ratio), 4),
        "method": method,
        "a": {
            "source": item_a["source"],
            "id": item_a["id"],
            "gen_model": item_a["gen_model"],
            "unit": item_a["syllabus_unit"],
            "part": item_a["part"],
            "preview": item_a["question_text"][:160].replace("\n", " "),
        },
        "b": {
            "source": item_b["source"],
            "id": item_b["id"],
            "gen_model": item_b["gen_model"],
            "unit": item_b["syllabus_unit"],
            "part": item_b["part"],
            "preview": item_b["question_text"][:160].replace("\n", " "),
        },
    }


def find_exact_normalized_matches(items: list[dict]) -> tuple[list[dict], set]:
    """Flag all pairs sharing identical normalized stems."""
    by_norm = defaultdict(list)
    for item in items:
        if item["norm_text"]:
            by_norm[item["norm_text"]].append(item)

    exact_pairs = []
    seen_pair_keys = set()
    within_gen = 0
    pyqs_vs_gen = 0
    within_pyqs = 0

    for _, group in by_norm.items():
        if len(group) < 2:
            continue
        for i in range(len(group)):
            for j in range(i + 1, len(group)):
                a, b = group[i], group[j]
                key = tuple(sorted([a["uid"], b["uid"]]))
                if key in seen_pair_keys:
                    continue
                seen_pair_keys.add(key)
                sources = {a["source"], b["source"]}
                if sources == {"generated"}:
                    within_gen += 1
                elif sources == {"pyqs", "generated"}:
                    pyqs_vs_gen += 1
                else:
                    within_pyqs += 1
                exact_pairs.append(build_pair_record(a, b, 1.0, "exact_normalized", "normalized_exact"))

    print("\n=== 3. Normalized Exact-Match Check ===")
    print(f"Exact normalized duplicate pairs: {len(exact_pairs)}")
    print(f"  - Within generated_questions:    {within_gen}")
    print(f"  - Across pyqs vs generated:      {pyqs_vs_gen}")
    print(f"  - Within pyqs:                   {within_pyqs}")
    return exact_pairs, seen_pair_keys


def _pairwise_cosine_sklearn(norm_texts: list[str]) -> list[tuple[int, int, float]]:
    vec = TfidfVectorizer(sublinear_tf=True, norm="l2")
    mat = vec.fit_transform(norm_texts)
    sim_mat = cosine_similarity(mat)
    pairs = []
    n = len(norm_texts)
    for i in range(n):
        row = sim_mat[i]
        for j in range(i + 1, n):
            score = float(row[j])
            if score >= 0.65:
                pairs.append((i, j, score))
    return pairs


def _pairwise_cosine_pure_python(norm_texts: list[str]) -> list[tuple[int, int, float]]:
    """Pure-Python sparse TF-IDF cosine similarity via inverted index."""
    n = len(norm_texts)
    toks = [t.split() for t in norm_texts]
    df = Counter()
    for t in toks:
        df.update(set(t))

    idf = {w: math.log((1 + n) / (1 + c)) + 1.0 for w, c in df.items()}
    vecs = []
    for t in toks:
        counts = Counter(t)
        raw_vec = {w: (1.0 + math.log(cnt)) * idf[w] for w, cnt in counts.items()}
        norm = math.sqrt(sum(v * v for v in raw_vec.values())) or 1.0
        vecs.append({w: v / norm for w, v in raw_vec.items()})

    inv = defaultdict(list)
    for idx, vec in enumerate(vecs):
        for w in vec:
            inv[w].append(idx)

    dots = defaultdict(float)
    for i, vec in enumerate(vecs):
        for w, val_i in vec.items():
            for j in inv[w]:
                if j > i:
                    dots[(i, j)] += val_i * vecs[j][w]

    return [(i, j, float(score)) for (i, j), score in dots.items() if score >= 0.65]


def find_near_duplicates(items: list[dict], exact_pair_keys: set) -> list[dict]:
    """Compute within-(unit, part) TF-IDF cosine similarity and flag >=0.80 and [0.65, 0.80)."""
    buckets = defaultdict(list)
    for item in items:
        buckets[(item["syllabus_unit"], item["part"])].append(item)

    method_label = "sklearn_tfidf_cosine" if HAS_SKLEARN else "pure_python_tfidf_cosine+shingle3+difflib"
    near_pairs = []

    for (unit, part) in sorted(buckets.keys(), key=lambda k: (unit_sort_key(k[0]), k[1])):
        bucket_items = buckets[(unit, part)]
        if len(bucket_items) < 2:
            continue
        texts = [it["norm_text"] for it in bucket_items]
        raw_pairs = (
            _pairwise_cosine_sklearn(texts)
            if HAS_SKLEARN
            else _pairwise_cosine_pure_python(texts)
        )
        for i, j, score in raw_pairs:
            a, b = bucket_items[i], bucket_items[j]
            key = tuple(sorted([a["uid"], b["uid"]]))
            if key in exact_pair_keys:
                continue
            tier = "high_confidence" if score >= 0.80 else "borderline"
            near_pairs.append(build_pair_record(a, b, score, tier, method_label))

    return near_pairs


def print_summary_and_top10(all_pairs: list[dict]):
    counts_by_unit = defaultdict(lambda: {"exact": 0, "high": 0, "borderline": 0})
    totals = {"exact": 0, "high": 0, "borderline": 0}

    for p in all_pairs:
        u = p["unit"]
        if p["tier"] == "exact_normalized":
            counts_by_unit[u]["exact"] += 1
            totals["exact"] += 1
        elif p["tier"] == "high_confidence":
            counts_by_unit[u]["high"] += 1
            totals["high"] += 1
        else:
            counts_by_unit[u]["borderline"] += 1
            totals["borderline"] += 1

    print("\n=== 4. Duplicate Summary by Unit & Tier ===")
    header = f"{'Syllabus Unit':<58} {'Exact':>7} {'High(>=.80)':>11} {'Mid(.65-.80)':>13} {'Total':>7}"
    print(header)
    print("-" * len(header))
    for u in sorted(counts_by_unit.keys(), key=unit_sort_key):
        c = counts_by_unit[u]
        tot = c["exact"] + c["high"] + c["borderline"]
        print(f"{u:<58} {c['exact']:>7} {c['high']:>11} {c['borderline']:>13} {tot:>7}")
    print("-" * len(header))
    grand = totals["exact"] + totals["high"] + totals["borderline"]
    print(f"{'Total Pairs':<58} {totals['exact']:>7} {totals['high']:>11} {totals['borderline']:>13} {grand:>7}")

    sorted_pairs = sorted(
        all_pairs,
        key=lambda p: (-p["similarity"], -p["shingle_jaccard_3"], -p["difflib_ratio"]),
    )

    print("\n=== 5. Top 10 Highest-Similarity Pairs ===")
    if not sorted_pairs:
        print("No duplicate or near-duplicate pairs found above 0.65.")
        return

    for idx, p in enumerate(sorted_pairs[:10], 1):
        a, b = p["a"], p["b"]
        print("-" * 80)
        print(
            f"#{idx} | {p['unit']} (Part {p['part']}) | Tier: {p['tier']} | "
            f"Sim: {p['similarity']:.4f} (3-shingle: {p['shingle_jaccard_3']:.4f}, difflib: {p['difflib_ratio']:.4f})"
        )
        print(f"  [A] ID #{a['id']} ({a['source']}, {a['gen_model']}): {a['preview']}")
        print(f"  [B] ID #{b['id']} ({b['source']}, {b['gen_model']}): {b['preview']}")


def winner_sort_key(entry: dict) -> tuple[int, int]:
    """Priority order: pyqs > gpt-6.2-sol / gpt-6-luna > other > gpt-5-mini, then lower ID."""
    if entry["source"] == "pyqs":
        rank = 0
    elif entry["gen_model"] in ("gpt-6.2-sol", "gpt-6-luna"):
        rank = 1
    elif entry["gen_model"] == "gpt-5-mini":
        rank = 3
    else:
        rank = 2
    return (rank, int(entry["id"]))


def apply_dedup(conn: sqlite3.Connection, all_pairs: list[dict], threshold: float):
    """Back up DB and retire weaker generated_questions duplicates (verified = 0) >= threshold."""
    candidates = sorted(
        [p for p in all_pairs if p["similarity"] >= threshold],
        key=lambda p: (-p["similarity"], winner_sort_key(p["a"]), winner_sort_key(p["b"])),
    )

    retired_uids = set()
    to_retire_gen_ids = []

    for p in candidates:
        a, b = p["a"], p["b"]
        uid_a = (a["source"], int(a["id"]))
        uid_b = (b["source"], int(b["id"]))
        if uid_a in retired_uids or uid_b in retired_uids:
            continue

        # Lower sort key wins (kept); higher sort key is retired if generated
        if winner_sort_key(a) <= winner_sort_key(b):
            loser = b
        else:
            loser = a

        loser_uid = (loser["source"], int(loser["id"]))
        if loser["source"] == "generated":
            retired_uids.add(loser_uid)
            to_retire_gen_ids.append(int(loser["id"]))

    print(f"\n=== Applying Deduplication (threshold >= {threshold:.2f}) ===")
    dst = sqlite3.connect(BACKUP_PATH)
    conn.backup(dst)
    dst.close()
    print(f"Backup created: {BACKUP_PATH}")

    for qid in to_retire_gen_ids:
        conn.execute("UPDATE generated_questions SET verified = 0 WHERE id = ?", (qid,))
    conn.commit()

    remaining = conn.execute("SELECT COUNT(*) FROM generated_questions WHERE verified = 1").fetchone()[0]
    print(f"Retired (set verified=0): {len(to_retire_gen_ids)} generated_questions rows")
    print(f"Remaining verified=1 rows: {remaining}")


def main():
    parser = argparse.ArgumentParser(description="CSIR NET Question Bank Deduplication Audit")
    parser.add_argument("--apply", action="store_true", help="Retire weaker duplicates >= threshold (sets verified=0)")
    parser.add_argument("--threshold", type=float, default=0.80, help="Similarity threshold for --apply (default 0.80)")
    args = parser.parse_args()

    conn = sqlite3.connect(DB_PATH)
    backend_check = check_backend_sampling_repeats(conn)
    items = load_active_questions(conn)
    exact_pairs, exact_keys = find_exact_normalized_matches(items)
    near_pairs = find_near_duplicates(items, exact_keys)

    all_pairs = exact_pairs + near_pairs
    all_pairs.sort(key=lambda p: (-p["similarity"], -p["shingle_jaccard_3"], p["unit"], p["part"]))

    print_summary_and_top10(all_pairs)

    report = {
        "similarity_backend": "scikit-learn" if HAS_SKLEARN else "pure-python-tfidf+shingle3+difflib",
        "backend_sampling_check": backend_check,
        "active_pool": {
            "pyqs_usable": sum(1 for x in items if x["source"] == "pyqs"),
            "generated_verified": sum(1 for x in items if x["source"] == "generated"),
            "total": len(items),
        },
        "counts": {
            "exact_normalized": len(exact_pairs),
            "high_confidence_ge_0_80": sum(1 for p in near_pairs if p["tier"] == "high_confidence"),
            "borderline_0_65_to_0_80": sum(1 for p in near_pairs if p["tier"] == "borderline"),
            "total_flagged": len(all_pairs),
        },
        "pairs": all_pairs,
    }
    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    print(f"\nSaved full duplicate report ({len(all_pairs)} pairs) to {REPORT_PATH}")

    if args.apply:
        apply_dedup(conn, all_pairs, threshold=args.threshold)

    conn.close()


if __name__ == "__main__":
    main()
