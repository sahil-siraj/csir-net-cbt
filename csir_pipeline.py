import argparse
import json
import os
import random
import re
import sqlite3
import time
from dotenv import load_dotenv
from openai import APIError, OpenAI, RateLimitError

load_dotenv()

DB_PATH = "csir_net.db"

REQUIRED_ENV_VARS = [
    "AZURE_FOUNDRY_BASE_URL",
    "AZURE_FOUNDRY_API_KEY",
    "TAGGER_MODEL",
    "AB_VERIFY_MODEL",
    "C_VERIFY_MODEL",
]

# Azure Global Standard short-context rates (USD per 1M tokens: input, output)
PRICING_PER_1M = {
    "gpt-6-luna": (0.10, 0.50),
    "gpt-6.1-terra": (1.00, 5.00),
    "gpt-6.2-sol": (2.00, 10.00),
    "gpt-oss-120b": (0.15, 0.60),
    "gpt-5-mini": (0.25, 2.00),
    "Phi-4-reasoning": (0.125, 0.50),
}

NO_JSON_MODE_MODELS = {"gpt-oss-120b", "Phi-4-reasoning"}

LATEX_UNIT_RULE = (
    r"Wrap all unit exponents, chemical formulas, and ion charges "
    r"(e.g., $10\ \mathrm{m}^3$, $\mathrm{L}^{-1}$, $\mathrm{Mn}^{2+}$) in $...$ delimiters."
)
OPTION_LENGTH_RULE = (
    "Keep all four options (A, B, C, D) similar in length, detail, and grammatical structure; "
    "never make the correct option noticeably longer than the distractors."
)
NO_LABEL_PREFIX_RULE = (
    "Do NOT include any question number or section label "
    "(such as Part A, Part B, Part C, or Q1.) at the start of question_text."
)

LEADING_LABEL_RE = re.compile(
    r"^\s*(?:\*{1,2}\s*)?(?:"
    r"Part\s+[ABC]\b(?:\s*\([^)]*\)\s*[:.\-–—]?|\s*[:.\-–—]|\s*(?=\*{1,2}|\n))"
    r"|Q(?:uestion)?\.?\s*\d+\s*[:.)\-–—]"
    r")\s*(?:\*{1,2})?\s*[:.\-–—]?\s*",
    flags=re.IGNORECASE,
)

SELF_REFERENTIAL_OPTION_RE = re.compile(
    r"(?:"
    r"\babove\b"
    r"|\b(?:all|none)\s+of\s+these\b"
    r"|\b(?:both|neither)\s+\(?[a-d]\)?\s*(?:and|&|or|nor)\s+\(?[a-d]\)?\b"
    r"|\boptions?\s+[a-d]\b"
    r"|\bonly\s+\(?[a-d]\)?(?:\s*(?:,|and|&|or)\s*\(?[a-d]\)?)+\b"
    r"|\([a-d]\)\s*(?:and|&|or|,)\s*\([a-d]\)"
    r")",
    flags=re.IGNORECASE,
)


def clean_question_prefix(text):
    """Strip stray leading Part A/B/C or Q1. labels from question_text."""
    cleaned = str(text or "")
    while True:
        updated = LEADING_LABEL_RE.sub("", cleaned, count=1)
        if updated == cleaned:
            break
        cleaned = updated
    return cleaned.lstrip()


def has_self_referential_option(options):
    """Return True if any option relies on positional or letter-referential phrasing."""
    if not isinstance(options, dict):
        return True
    for key in ("A", "B", "C", "D"):
        val = str(options.get(key, ""))
        if SELF_REFERENTIAL_OPTION_RE.search(val):
            return True
    return False


def shuffle_options(options, correct_option, verifier_answer):
    """Randomly permute A/B/C/D options and remap correct_option and verifier_answer together."""
    if (
        not isinstance(options, dict)
        or set(options.keys()) != {"A", "B", "C", "D"}
        or correct_option not in {"A", "B", "C", "D"}
        or has_self_referential_option(options)
    ):
        return options, correct_option, verifier_answer

    orig_keys = ["A", "B", "C", "D"]
    perm_keys = orig_keys.copy()
    random.shuffle(perm_keys)
    old_to_new = {old_k: new_k for new_k, old_k in zip(orig_keys, perm_keys)}
    new_options = {new_k: options[old_k] for new_k, old_k in zip(orig_keys, perm_keys)}
    new_correct = old_to_new.get(correct_option, correct_option)
    new_verifier = old_to_new.get(verifier_answer, verifier_answer)
    return new_options, new_correct, new_verifier


def get_client_and_models():
    missing = [var for var in REQUIRED_ENV_VARS if not os.getenv(var)]
    ab_gen = os.getenv("AB_GEN_MODEL") or os.getenv("GEN_MODEL")
    c_gen = os.getenv("C_GEN_MODEL") or os.getenv("GEN_MODEL")
    if not ab_gen:
        missing.append("AB_GEN_MODEL (or GEN_MODEL)")
    if not c_gen:
        missing.append("C_GEN_MODEL (or GEN_MODEL)")
    if missing:
        raise RuntimeError(
            f"Missing required entries in .env: {', '.join(missing)}"
        )

    base_url = os.environ["AZURE_FOUNDRY_BASE_URL"].rstrip("/")
    if base_url.endswith("/responses"):
        base_url = base_url[:-len("/responses")]

    client = OpenAI(
        base_url=base_url,
        api_key=os.environ["AZURE_FOUNDRY_API_KEY"],
    )
    models = {
        "tagger": os.environ["TAGGER_MODEL"],
        "ab_gen": ab_gen,
        "c_gen": c_gen,
        "ab_verify": os.environ["AB_VERIFY_MODEL"],
        "c_verify": os.environ["C_VERIFY_MODEL"],
    }
    return client, models


def get_pricing_map(models):
    pricing = dict(PRICING_PER_1M)
    for m in models.values():
        if m not in pricing:
            pricing[m] = (0.25, 2.00)
    return pricing


def init_db(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS generated_questions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            syllabus_unit TEXT,
            subtopic TEXT,
            part TEXT,
            question_text TEXT UNIQUE,
            options_json TEXT,
            correct_option TEXT,
            explanation TEXT,
            gen_model TEXT,
            verify_model TEXT,
            verifier_answer TEXT,
            verified INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(generated_questions)").fetchall()]
    if "subtopic" not in cols:
        conn.execute("ALTER TABLE generated_questions ADD COLUMN subtopic TEXT")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS cost_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stage TEXT,
            model TEXT,
            input_tokens INTEGER,
            output_tokens INTEGER,
            cost_usd REAL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()


def get_total_spend(conn):
    row = conn.execute(
        "SELECT COALESCE(SUM(cost_usd), 0.0) FROM cost_log"
    ).fetchone()
    return float(row[0])


def record_cost(conn, pricing_map, stage, model, usage):
    in_tok = getattr(usage, "prompt_tokens", 0) or 0
    out_tok = getattr(usage, "completion_tokens", 0) or 0
    in_rate, out_rate = pricing_map.get(model, (0.25, 2.00))
    cost = (in_tok / 1_000_000) * in_rate + (out_tok / 1_000_000) * out_rate
    conn.execute(
        "INSERT INTO cost_log (stage, model, input_tokens, output_tokens, cost_usd) "
        "VALUES (?, ?, ?, ?, ?)",
        (stage, model, in_tok, out_tok, cost),
    )
    conn.commit()
    return cost


def extract_json(raw_text, expected_key=None):
    """Safely extracts JSON even if reasoning traces or LaTeX braces precede it."""
    if not raw_text:
        raise json.JSONDecodeError("Empty response from model", "", 0)

    cleaned = re.sub(r"<think>.*?</think>", "", raw_text, flags=re.DOTALL).strip()

    # 1. Try fenced ```json ... ``` blocks from last to first
    fences = re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", cleaned, flags=re.DOTALL)
    for block in reversed(fences):
        try:
            parsed = json.loads(block)
            if expected_key is None or expected_key in parsed:
                return parsed
        except json.JSONDecodeError:
            continue

    # 2. Try direct parse of the full cleaned text
    try:
        parsed = json.loads(cleaned)
        if expected_key is None or expected_key in parsed:
            return parsed
    except json.JSONDecodeError:
        pass

    # 3. Fallback: scan for JSON objects using JSONDecoder (avoids LaTeX brace collisions)
    decoder = json.JSONDecoder()
    candidates = []
    idx = 0
    while idx < len(cleaned):
        brace_pos = cleaned.find("{", idx)
        if brace_pos == -1:
            break
        try:
            obj, end_pos = decoder.raw_decode(cleaned, brace_pos)
            if isinstance(obj, dict):
                if expected_key is None or expected_key in obj:
                    candidates.append(obj)
            idx = end_pos
        except json.JSONDecodeError:
            idx = brace_pos + 1

    if candidates:
        return candidates[-1]

    raise json.JSONDecodeError("Could not locate valid JSON object in output", cleaned, 0)


def call_llm(
    client,
    conn,
    models,
    pricing_map,
    stage,
    model,
    prompt,
    expected_key,
    temperature=0.3,
    max_retries=3,
):
    is_no_sampling = model.startswith(("gpt-6", "gpt-5"))

    kwargs = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
    }

    if model == "Phi-4-reasoning":
        # Recommended Microsoft sampling settings for Phi-4-reasoning to prevent loops
        kwargs["temperature"] = 0.8
        kwargs["top_p"] = 0.95
        kwargs["max_tokens"] = 4096
    elif not is_no_sampling:
        kwargs["temperature"] = temperature

    if model.startswith("gpt-6"):
        if model == models["ab_gen"] and model == models["c_gen"]:
            kwargs["reasoning_effort"] = "medium" if stage.endswith("_C") else "low"
        elif model in (models["c_gen"], models["c_verify"]):
            kwargs["reasoning_effort"] = "medium"
        elif model == models["ab_gen"]:
            kwargs["reasoning_effort"] = "low"

    if model not in NO_JSON_MODE_MODELS:
        kwargs["response_format"] = {"type": "json_object"}

    for attempt in range(1, max_retries + 1):
        try:
            resp = client.chat.completions.create(**kwargs)
            cost = record_cost(conn, pricing_map, stage, model, resp.usage)
            content = resp.choices[0].message.content
            data = extract_json(content, expected_key=expected_key)
            return data, cost
        except (RateLimitError, APIError, json.JSONDecodeError, KeyError, TypeError) as e:
            wait = 10 * attempt
            print(
                f"  [Retry {attempt}/{max_retries}] {model} error: "
                f"{type(e).__name__}: {e}. Waiting {wait}s..."
            )
            time.sleep(wait)

    raise RuntimeError(f"Failed after {max_retries} attempts on {model}")


def run_preflight_check(conn):
    print("\n=== Pre-Flight Check ===")

    # 1. Check pyqs table & columns
    tables = {
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    if "pyqs" not in tables:
        print("[FAIL] Table 'pyqs' not found in csir_net.db.")
        return

    cols = {
        r[1] for r in conn.execute("PRAGMA table_info(pyqs)").fetchall()
    }
    required_cols = {
        "id",
        "question_text",
        "options_json",
        "correct_option",
        "syllabus_unit",
        "part",
    }
    missing_cols = required_cols - cols
    if missing_cols:
        print(f"[FAIL] 'pyqs' table is missing columns: {sorted(missing_cols)}")
        return

    total_pyqs = conn.execute("SELECT COUNT(*) FROM pyqs").fetchone()[0]
    untagged_pyqs = conn.execute(
        "SELECT COUNT(*) FROM pyqs "
        "WHERE syllabus_unit IS NULL OR syllabus_unit = '' OR part IS NULL OR part = ''"
    ).fetchone()[0]
    total_gen = conn.execute("SELECT COUNT(*) FROM generated_questions").fetchone()[0]
    total_verified = conn.execute(
        "SELECT COUNT(*) FROM generated_questions WHERE verified = 1"
    ).fetchone()[0]
    spend = get_total_spend(conn)

    print(f"[OK] Database '{DB_PATH}' verified.")
    print(f"     PYQs total:      {total_pyqs} ({total_pyqs - untagged_pyqs} tagged, {untagged_pyqs} untagged)")
    print(f"     Generated total: {total_gen} ({total_verified} verified)")
    print(f"     Logged spend:    ${spend:.4f}")

    # 2. Check .env, pricing rates & live ping to unique deployed models
    try:
        client, models = get_client_and_models()
        pricing_map = get_pricing_map(models)
        print("[OK] .env loaded successfully.")
    except RuntimeError as e:
        print(f"[FAIL] {e}")
        return

    print("\nConfigured Model Routing & Pricing (USD per 1M tokens):")
    for role, model_name in models.items():
        in_rate, out_rate = pricing_map.get(model_name, (0.25, 2.00))
        print(f"  {role:<10} -> {model_name:<18} (${in_rate:.3f} in / ${out_rate:.3f} out)")

    unique_models = list(dict.fromkeys(models.values()))
    print("\nTesting live Azure Foundry deployments:")
    for m in unique_models:
        try:
            kwargs = {
                "model": m,
                "messages": [{"role": "user", "content": "Reply with the single word: OK"}],
            }
            if m.startswith(("gpt-6", "gpt-5", "o1", "o3")):
                kwargs["max_completion_tokens"] = 256
                if m.startswith("gpt-6"):
                    if m in (models["c_gen"], models["c_verify"]):
                        kwargs["reasoning_effort"] = "medium"
                    elif m == models["ab_gen"]:
                        kwargs["reasoning_effort"] = "low"
            elif m == "Phi-4-reasoning":
                kwargs["temperature"] = 0.8
                kwargs["top_p"] = 0.95
                kwargs["max_tokens"] = 256
            else:
                kwargs["max_tokens"] = 64
            resp = client.chat.completions.create(**kwargs)
            reply = (resp.choices[0].message.content or "").strip().replace("\n", " ")[:60]
            in_rate, out_rate = pricing_map.get(m, (0.25, 2.00))
            print(f"  [OK] {m} (${in_rate:.3f}/${out_rate:.3f} per 1M) -> '{reply}'")
        except Exception as e:
            print(f"  [FAIL] {m} -> {type(e).__name__}: {e}")


def inspect_verified(conn, limit=5, unit_filter=None):
    if unit_filter:
        conditions = []
        params = []
        for u in str(unit_filter).split(","):
            u = u.strip()
            if u:
                conditions.append("syllabus_unit LIKE ?")
                params.append(f"%{u}%")
        where_clause = " OR ".join(conditions) if conditions else "1=1"
        query = f"""SELECT id, syllabus_unit, part, question_text, options_json,
                           correct_option, explanation, gen_model, verify_model, subtopic
                    FROM generated_questions
                    WHERE verified = 1 AND ({where_clause})
                    ORDER BY RANDOM()
                    LIMIT ?"""
        params.append(limit)
        rows = conn.execute(query, params).fetchall()
    else:
        rows = conn.execute(
            """SELECT id, syllabus_unit, part, question_text, options_json,
                      correct_option, explanation, gen_model, verify_model, subtopic
               FROM generated_questions
               WHERE verified = 1
               ORDER BY (subtopic IS NOT NULL) DESC, RANDOM()
               LIMIT ?""",
            (limit,),
        ).fetchall()

    if not rows:
        print("No verified generated questions found yet. Run `--mode pilot` or `--mode generate-syllabus` first.")
        return

    print(f"\n=== Showing {len(rows)} Random Verified Questions ===")
    for r in rows:
        qid, unit, part, qtext, opts_json, ans, exp, gmod, vmod, subtopic = r
        try:
            opts = json.loads(opts_json)
        except Exception:
            opts = {}
        subtopic_str = f" | Subtopic: {subtopic}" if subtopic else ""
        print("-" * 70)
        print(f"ID #{qid} | {unit}{subtopic_str} | Part {part} | Gen: {gmod} | Ver: {vmod}")
        print(f"\nQ: {qtext}\n")
        for k in ["A", "B", "C", "D"]:
            if k in opts:
                print(f"  ({k}) {opts[k]}")
        print(f"\nAnswer: {ans}")
        print(f"Explanation: {exp}\n")


def bulk_tag_pyqs(client, conn, models, pricing_map, max_budget, batch_size=10):
    print("\n--- Step 1: Bulk PYQ Tagging (gpt-oss-120b) ---")
    while True:
        if get_total_spend(conn) >= max_budget:
            print(f"Budget cap (${max_budget:.2f}) reached. Stopping.")
            break

        rows = conn.execute(
            "SELECT id, question_text FROM pyqs "
            "WHERE syllabus_unit IS NULL OR syllabus_unit = '' OR part IS NULL OR part = '' "
            "LIMIT ?",
            (batch_size,),
        ).fetchall()

        if not rows:
            print("All PYQs are tagged!")
            break

        payload = [{"id": r[0], "question": r[1][:600]} for r in rows]
        prompt = f"""Classify each CSIR NET PYQ into its official syllabus unit (e.g., "Unit 1", "Unit 2", ..., or "General Aptitude") and exam part ("A", "B", or "C").
Return ONLY valid JSON wrapped in a ```json code block:
```json
{{
  "tags": [
    {{"id": 123, "syllabus_unit": "Unit 1", "part": "C"}}
  ]
}}
```

Questions:
{json.dumps(payload, indent=2)}"""

        data, cost = call_llm(
            client,
            conn,
            models,
            pricing_map,
            "tag",
            models["tagger"],
            prompt,
            expected_key="tags",
            temperature=0.1,
        )
        updated = 0
        for item in data.get("tags", []):
            unit_val = str(item.get("syllabus_unit", "")).strip()
            part_val = str(item.get("part", "")).strip().upper()
            if unit_val and part_val in {"A", "B", "C"}:
                conn.execute(
                    "UPDATE pyqs SET syllabus_unit = ?, part = ? WHERE id = ?",
                    (unit_val, part_val, item["id"]),
                )
                updated += 1
        conn.commit()
        print(
            f"Tagged {updated}/{len(rows)} PYQs | Batch: ${cost:.4f} | "
            f"Total spend: ${get_total_spend(conn):.4f}"
        )


def generate_and_verify_batch(
    client,
    conn,
    models,
    pricing_map,
    unit,
    part,
    cycle_idx=0,
    distinct_angle_mode=False,
):
    is_part_c = part == "C"
    batch_size = 3 if is_part_c else 5
    gen_model = models["c_gen"] if is_part_c else models["ab_gen"]
    ver_model = models["c_verify"] if is_part_c else models["ab_verify"]

    candidate_rows = conn.execute(
        "SELECT question_text, options_json, correct_option FROM pyqs "
        "WHERE syllabus_unit = ? AND part = ? ORDER BY id",
        (unit, part),
    ).fetchall()

    image_indicators = [
        "![",
        "<img",
        "figure",
        "diagram",
        "graph",
        "shown below",
        "given below",
        "plot shown",
        "image",
    ]

    non_image_rows = [
        r for r in candidate_rows
        if not any(indicator in r[0].lower() for indicator in image_indicators)
    ]

    if not non_image_rows:
        return 0, 0.0, batch_size

    if distinct_angle_mode or len(non_image_rows) <= 4:
        num_anchors = min(4, len(non_image_rows))
        start = (cycle_idx * num_anchors) % len(non_image_rows)
        anchors_rows = [
            non_image_rows[(start + k) % len(non_image_rows)]
            for k in range(num_anchors)
        ]
    else:
        anchors_rows = random.sample(non_image_rows, 4)

    anchors = [
        {"question": r[0], "options": json.loads(r[1]), "answer": r[2]}
        for r in anchors_rows
    ]

    extra_angle_block = ""
    if distinct_angle_mode:
        cognitive_style = COGNITIVE_STYLES[cycle_idx % len(COGNITIVE_STYLES)]
        style_guide = STYLE_GUIDELINES.get(cognitive_style, "")
        recent_rows = conn.execute(
            """SELECT question_text FROM generated_questions
               WHERE syllabus_unit = ? AND part = ?
               ORDER BY id DESC LIMIT 10""",
            (unit, part),
        ).fetchall()
        covered_lines = "\n".join(
            f"- {r[0][:120].strip().replace(chr(10), ' ')}..."
            for r in recent_rows if r[0]
        ) or "- None yet."
        extra_angle_block = (
            f"\nTarget Cognitive Style for this batch: {cognitive_style} ({style_guide})\n"
            "Generate a distinct subtopic/angle from the anchors and do NOT repeat these already covered concepts:\n"
            f"{covered_lines}\n"
        )

    gen_prompt = f"""You are a senior CSIR NET exam author. Using the rigor, style, and complexity of these real PYQs from '{unit}' (Part {part}), write {batch_size} brand-new, original MCQs testing DIFFERENT conceptual angles.
For Part C, include multi-statement, experimental, or analytical reasoning matching real CSIR NET difficulty.
Generate strictly self-contained text/LaTeX questions that never reference an external figure, graph, or diagram.
{LATEX_UNIT_RULE}
{OPTION_LENGTH_RULE}
{NO_LABEL_PREFIX_RULE}
{extra_angle_block}
Real PYQ Style Anchors:
{json.dumps(anchors, indent=2)}

Return ONLY valid JSON:
{{
  "questions": [
    {{
      "question_text": "Full question in Markdown (use $...$ for LaTeX)",
      "options": {{"A": "...", "B": "...", "C": "...", "D": "..."}},
      "correct_option": "A",
      "explanation": "Step-by-step scientific explanation"
    }}
  ]
}}"""

    gen_data, gen_cost = call_llm(
        client,
        conn,
        models,
        pricing_map,
        f"gen_{part}",
        gen_model,
        gen_prompt,
        expected_key="questions",
    )
    questions = gen_data.get("questions", [])
    if not questions:
        return 0, gen_cost, batch_size

    blind_payload = [
        {"idx": i, "question_text": q["question_text"], "options": q["options"]}
        for i, q in enumerate(questions)
    ]

    ver_prompt = f"""Solve each CSIR NET Part {part} question carefully step-by-step without guessing.
After reasoning, return ONLY valid JSON wrapped in a ```json code block:
```json
{{
  "answers": [
    {{"idx": 0, "selected_option": "A"}}
  ]
}}
```

Questions:
{json.dumps(blind_payload, indent=2)}"""

    ver_data, ver_cost = call_llm(
        client,
        conn,
        models,
        pricing_map,
        f"verify_{part}",
        ver_model,
        ver_prompt,
        expected_key="answers",
        temperature=0.1,
    )
    ver_map = {
        int(item["idx"]): str(item["selected_option"]).strip().upper()
        for item in ver_data.get("answers", [])
        if "idx" in item and "selected_option" in item
    }

    accepted = 0
    for i, q in enumerate(questions):
        gen_ans = str(q.get("correct_option", "")).strip().upper()
        ver_ans = ver_map.get(i, "")
        is_verified = (
            1 if (gen_ans in {"A", "B", "C", "D"} and gen_ans == ver_ans) else 0
        )
        accepted += is_verified

        q_text = clean_question_prefix(q["question_text"])
        opts, gen_ans, ver_ans = shuffle_options(q["options"], gen_ans, ver_ans)

        try:
            conn.execute(
                """INSERT INTO generated_questions
                   (syllabus_unit, part, question_text, options_json, correct_option, explanation,
                    gen_model, verify_model, verifier_answer, verified)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    unit,
                    part,
                    q_text,
                    json.dumps(opts),
                    gen_ans,
                    q.get("explanation", ""),
                    gen_model,
                    ver_model,
                    ver_ans,
                    is_verified,
                ),
            )
        except sqlite3.IntegrityError:
            pass  # Skip exact duplicate

    conn.commit()

    # Smooth pacing: 15s only for Phi-4-reasoning's 20K TPM ceiling; 1s for faster verifiers (e.g., gpt-6.1-terra)
    if is_part_c:
        if ver_model == "Phi-4-reasoning":
            time.sleep(15)
        else:
            time.sleep(1)

    return accepted, (gen_cost + ver_cost), len(questions)


def run_generation_loop(client, conn, models, pricing_map, target_verified, max_budget):
    buckets = conn.execute(
        "SELECT syllabus_unit, part, COUNT(*) as cnt FROM pyqs "
        "WHERE syllabus_unit IS NOT NULL AND syllabus_unit != '' "
        "AND part IN ('A', 'B', 'C') "
        "GROUP BY syllabus_unit, part ORDER BY cnt DESC"
    ).fetchall()

    if not buckets:
        print("No tagged PYQs found. Run `--mode tag` first.")
        return

    weights = [max(5, b[2]) for b in buckets]

    while True:
        verified_so_far = conn.execute(
            "SELECT COUNT(*) FROM generated_questions WHERE verified = 1"
        ).fetchone()[0]
        spend = get_total_spend(conn)

        if verified_so_far >= target_verified:
            print(
                f"\nTarget reached! {verified_so_far} verified Qs | "
                f"Total spend: ${spend:.4f}"
            )
            break
        if spend >= max_budget:
            print(
                f"\nBudget cap (${max_budget:.2f}) reached at {verified_so_far} verified Qs. "
                "Stopping safely."
            )
            break

        unit, part, _ = random.choices(buckets, weights=weights, k=1)[0]

        try:
            accepted, batch_cost, attempted = generate_and_verify_batch(
                client, conn, models, pricing_map, unit, part
            )
            print(
                f"[{unit} | Part {part}] +{accepted}/{attempted} verified | "
                f"Batch: ${batch_cost:.4f} | "
                f"Total: {verified_so_far + accepted}/{target_verified} Qs "
                f"(${spend + batch_cost:.4f} spent)"
            )
        except KeyboardInterrupt:
            print("\nPaused safely by user. Re-run the same command anytime to resume.")
            break
        except Exception as e:
            print(f"[{unit} | Part {part}] Skipped batch due to error: {e}")
            time.sleep(10)


COGNITIVE_STYLES = [
    "Multi-statement analytical",
    "Quantitative / numerical calculation",
    "Experimental troubleshooting / case study",
    "Comparative / match-the-following",
]

STYLE_GUIDELINES = {
    "Multi-statement analytical": (
        "Structure the question with 4-5 numbered statements (e.g. A, B, C, D or I, II, III, IV) "
        "describing mechanisms, regulatory steps, or experimental observations. "
        "The options must test combinations of statements (e.g., 'A and C only', 'B, C and D only')."
    ),
    "Quantitative / numerical calculation": (
        "Formulate a quantitative problem with explicit numerical values requiring mathematical deduction "
        "(e.g., enzyme/microbial kinetics, dilution rates, bioenergetics, stoichiometry, dynamic programming scores). "
        "Options must be distinct numerical answers with appropriate units."
    ),
    "Experimental troubleshooting / case study": (
        "Describe a real-world experimental protocol, laboratory assay, or bioprocess run with an unexpected result, "
        "artifact, or failure mode. Ask for the most probable root cause, corrective adjustment, or biological deduction."
    ),
    "Comparative / match-the-following": (
        "Present a two-column matching problem (e.g. Column I: Enzymes/Algorithms/Techniques vs Column II: Roles/Mechanisms/Outputs) "
        "or a direct comparative analysis between two biological systems/models. Options must be clear 4-combination mappings."
    ),
}


def fetch_style_anchors(conn, part, limit=3):
    image_indicators = [
        "![",
        "<img",
        "figure",
        "diagram",
        "graph",
        "shown below",
        "given below",
        "plot shown",
        "image",
    ]
    rows = conn.execute(
        """SELECT question_text, options_json, correct_option FROM pyqs
           WHERE syllabus_unit IN ('Unit 14: Methods in Biology', 'Unit 1: Structure and Function of Biomolecules')
             AND part = ?
           ORDER BY RANDOM()""",
        (part,),
    ).fetchall()

    anchors = []
    for r in rows:
        q_lower = r[0].lower()
        if not any(ind in q_lower for ind in image_indicators):
            try:
                opts = json.loads(r[1])
            except Exception:
                continue
            anchors.append({"question": r[0], "options": opts, "answer": r[2]})
            if len(anchors) == limit:
                break
    return anchors


def fetch_anti_repetition_angles(conn, subtopic, limit=10):
    rows = conn.execute(
        """SELECT question_text FROM generated_questions
           WHERE subtopic = ?
           ORDER BY id DESC LIMIT ?""",
        (subtopic, limit),
    ).fetchall()
    return [r[0][:120].strip().replace("\n", " ") for r in rows if r[0]]


def generate_and_verify_syllabus_batch(
    client, conn, models, pricing_map, topic_item, part, cognitive_style
):
    is_part_c = part == "C"
    batch_size = 3 if is_part_c else 5
    gen_model = models["c_gen"] if is_part_c else models["ab_gen"]
    ver_model = models["c_verify"] if is_part_c else models["ab_verify"]

    unit = topic_item.get("syllabus_unit", "")
    subtopic = topic_item.get("subtopic", "")
    key_concepts = topic_item.get("key_concepts", [])
    key_concepts_text = "\n".join(f"- {c}" for c in key_concepts)

    anchors = fetch_style_anchors(conn, part, limit=3)
    covered_angles = fetch_anti_repetition_angles(conn, subtopic, limit=10)
    if covered_angles:
        anti_rep_text = "Already covered angles — DO NOT repeat these concepts:\n" + "\n".join(
            f"- {a}..." for a in covered_angles
        )
    else:
        anti_rep_text = "Already covered angles — DO NOT repeat these concepts:\n- None yet (cover foundational principles)."

    style_guide = STYLE_GUIDELINES.get(cognitive_style, "")

    gen_prompt = f"""You are a senior CSIR NET exam author and scientific subject matter expert.
Using the style, difficulty, and structural depth of real CSIR NET questions, write {batch_size} brand-new, original MCQs for Part {part} strictly covering the target syllabus subtopic below.

=== TARGET SYLLABUS TOPIC ===
Unit: {unit}
Subtopic: {subtopic}
Key Concepts to Cover:
{key_concepts_text}

=== REQUIRED QUESTION STYLE / COGNITIVE ANGLE ===
Style: {cognitive_style}
Guideline: {style_guide}

=== ANTI-REPETITION CONSTRAINTS ===
{anti_rep_text}

=== DECOUPLED CSIR NET STYLE & DIFFICULTY ANCHORS ===
(CRITICAL: Use these real PYQs ONLY to calibrate the structural rigor, question depth, and formatting of Part {part}. DO NOT copy their biological topic or subject matter. Your questions must strictly test the TARGET SYLLABUS TOPIC above.)
{json.dumps(anchors, indent=2)}

=== STRICT GENERATION RULES ===
1. Generate strictly self-contained text/LaTeX questions that NEVER reference an external figure, graph, image, or diagram.
2. Embed all scientific variables, formulas, and math expressions in LaTeX ($...$ for inline, $$...$$ for display). {LATEX_UNIT_RULE}
3. Provide exactly 4 mutually exclusive options (A, B, C, D) and a single definitively correct option. {OPTION_LENGTH_RULE}
4. {NO_LABEL_PREFIX_RULE}
5. Part B questions must be 2-mark conceptual/factual deductions with tight scientific precision.
6. Part C questions must be 4-mark higher-order analytical problems matching CSIR NET Part C depth.
7. Provide a thorough, step-by-step scientific explanation proving why the chosen option is correct and why the alternatives are incorrect.

Return ONLY valid JSON:
{{
  "questions": [
    {{
      "question_text": "Full question in Markdown (use $...$ for LaTeX)",
      "options": {{"A": "...", "B": "...", "C": "...", "D": "..."}},
      "correct_option": "A",
      "explanation": "Step-by-step scientific explanation"
    }}
  ]
}}"""

    gen_data, gen_cost = call_llm(
        client,
        conn,
        models,
        pricing_map,
        f"gen_syllabus_{part}",
        gen_model,
        gen_prompt,
        expected_key="questions",
    )
    raw_questions = gen_data.get("questions", [])
    questions = [
        q for q in raw_questions
        if isinstance(q, dict) and "question_text" in q and isinstance(q.get("options"), dict)
    ]
    if not questions:
        return 0, gen_cost, batch_size

    blind_payload = [
        {"idx": i, "question_text": q["question_text"], "options": q["options"]}
        for i, q in enumerate(questions)
    ]

    ver_prompt = f"""Solve each CSIR NET Part {part} question carefully step-by-step without guessing.
After reasoning, return ONLY valid JSON wrapped in a ```json code block:
```json
{{
  "answers": [
    {{"idx": 0, "selected_option": "A"}}
  ]
}}
```

Questions:
{json.dumps(blind_payload, indent=2)}"""

    ver_data, ver_cost = call_llm(
        client,
        conn,
        models,
        pricing_map,
        f"verify_syllabus_{part}",
        ver_model,
        ver_prompt,
        expected_key="answers",
        temperature=0.1,
    )
    ver_map = {
        int(item["idx"]): str(item["selected_option"]).strip().upper()
        for item in ver_data.get("answers", [])
        if "idx" in item and "selected_option" in item
    }

    accepted = 0
    for i, q in enumerate(questions):
        gen_ans = str(q.get("correct_option", "")).strip().upper()
        ver_ans = ver_map.get(i, "")
        is_verified = (
            1 if (gen_ans in {"A", "B", "C", "D"} and gen_ans == ver_ans) else 0
        )
        accepted += is_verified

        q_text = clean_question_prefix(q["question_text"])
        opts, gen_ans, ver_ans = shuffle_options(q["options"], gen_ans, ver_ans)

        try:
            conn.execute(
                """INSERT INTO generated_questions
                   (syllabus_unit, subtopic, part, question_text, options_json, correct_option, explanation,
                    gen_model, verify_model, verifier_answer, verified)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    unit,
                    subtopic,
                    part,
                    q_text,
                    json.dumps(opts),
                    gen_ans,
                    q.get("explanation", ""),
                    gen_model,
                    ver_model,
                    ver_ans,
                    is_verified,
                ),
            )
        except sqlite3.IntegrityError:
            pass  # Skip exact duplicate question text

    conn.commit()

    # Smooth pacing: 15s only for Phi-4-reasoning's 20K TPM ceiling; 1s for faster verifiers (e.g., gpt-6.1-terra)
    if is_part_c:
        if ver_model == "Phi-4-reasoning":
            time.sleep(15)
        else:
            time.sleep(1)

    return accepted, (gen_cost + ver_cost), len(questions)


def run_syllabus_generation_loop(
    client,
    conn,
    models,
    pricing_map,
    target_verified=5000,
    max_budget=15.0,
    topics_path="dbt_topics.json",
):
    if not os.path.exists(topics_path):
        print(f"Error: Topics file '{topics_path}' not found.")
        return

    with open(topics_path, "r", encoding="utf-8") as f:
        topics = json.load(f)

    if not isinstance(topics, list) or not topics:
        print(f"Error: No valid topics found in '{topics_path}'.")
        return

    print(f"\n--- Decoupled Anchoring Syllabus Generation ---")
    print(f"Loaded {len(topics)} topics from {topics_path}")
    print(f"Global Budget Cap: ${max_budget:.2f} | Overall Target: {target_verified} verified Qs")

    style_idx = 0
    interrupted = False

    for t_idx, topic_item in enumerate(topics, 1):
        if interrupted:
            break

        unit = topic_item.get("syllabus_unit", "")
        subtopic = topic_item.get("subtopic", "")
        target_b = int(topic_item.get("target_part_b", 10))
        target_c = int(topic_item.get("target_part_c", 15))

        print(f"\n[{t_idx}/{len(topics)}] {unit}")
        print(f"  Subtopic: {subtopic}")

        for part, target_for_part in [("B", target_b), ("C", target_c)]:
            curr_verified = conn.execute(
                """SELECT COUNT(*) FROM generated_questions
                   WHERE syllabus_unit = ? AND subtopic = ? AND part = ? AND verified = 1""",
                (unit, subtopic, part),
            ).fetchone()[0]

            if curr_verified >= target_for_part:
                print(f"  Part {part}: Target already satisfied ({curr_verified}/{target_for_part} verified). Skipping.")
                continue

            consecutive_zero_batches = 0

            while curr_verified < target_for_part:
                total_spend = get_total_spend(conn)
                if total_spend >= max_budget:
                    print(
                        f"\n[STOP] Hard budget cap (${max_budget:.2f}) reached. "
                        f"Current spend: ${total_spend:.4f}."
                    )
                    return

                total_all_verified = conn.execute(
                    "SELECT COUNT(*) FROM generated_questions WHERE verified = 1"
                ).fetchone()[0]
                if total_all_verified >= target_verified:
                    print(
                        f"\n[DONE] Overall target ({target_verified} verified Qs) reached! "
                        f"Current spend: ${total_spend:.4f}."
                    )
                    return

                cognitive_style = COGNITIVE_STYLES[style_idx % len(COGNITIVE_STYLES)]
                style_idx += 1

                try:
                    accepted, batch_cost, attempted = generate_and_verify_syllabus_batch(
                        client,
                        conn,
                        models,
                        pricing_map,
                        topic_item,
                        part,
                        cognitive_style,
                    )
                    curr_verified += accepted
                    total_spend_now = get_total_spend(conn)
                    total_all_verified_now = conn.execute(
                        "SELECT COUNT(*) FROM generated_questions WHERE verified = 1"
                    ).fetchone()[0]

                    print(
                        f"  [Part {part} | {cognitive_style}] "
                        f"+{accepted}/{attempted} verified | "
                        f"Subtopic: {curr_verified}/{target_for_part} | "
                        f"Batch: ${batch_cost:.4f} | "
                        f"Total Spend: ${total_spend_now:.4f} | "
                        f"Overall: {total_all_verified_now}/{target_verified}"
                    )

                    if accepted == 0:
                        consecutive_zero_batches += 1
                        if consecutive_zero_batches >= 3:
                            print(
                                f"  [WARN] 3 consecutive batches with 0 verified for Part {part}. "
                                "Advancing to next target to prevent stalling."
                            )
                            break
                    else:
                        consecutive_zero_batches = 0

                except KeyboardInterrupt:
                    print("\n[PAUSE] Execution paused by user. Progress saved in database.")
                    interrupted = True
                    break
                except Exception as e:
                    print(f"  [ERROR] Batch failed: {e}. Retrying after 10s...")
                    time.sleep(10)

    total_final = conn.execute("SELECT COUNT(*) FROM generated_questions WHERE verified = 1").fetchone()[0]
    spend_final = get_total_spend(conn)
    print(f"\n=== Syllabus Generation Run Complete ===")
    print(f"Total Verified Questions in DB: {total_final}")
    print(f"Total Spend Logged: ${spend_final:.4f}")


def unit_sort_key(unit_name):
    m = re.match(r"Unit\s+(\d+)\b", str(unit_name), re.IGNORECASE)
    return (0, int(m.group(1)), str(unit_name)) if m else (1, 0, str(unit_name))


def get_unit_part_counts(conn, units):
    counts = {(u, p): 0 for u in units for p in ("B", "C")}
    rows = conn.execute(
        """SELECT syllabus_unit, part, COUNT(*)
           FROM generated_questions
           WHERE verified = 1 AND part IN ('B', 'C') AND syllabus_unit LIKE 'Unit %'
           GROUP BY syllabus_unit, part"""
    ).fetchall()
    for u, p, cnt in rows:
        if (u, p) in counts:
            counts[(u, p)] = int(cnt)
    return counts


def print_unit_summary_table(conn, units, min_b=None, min_c=None):
    counts = get_unit_part_counts(conn, units)
    print("\n=== Per-Unit Verified Summary (Units 1-14) ===")
    header = f"{'Syllabus Unit':<58} {'Part B':>10} {'Part C':>10} {'Total':>8}"
    print(header)
    print("-" * len(header))
    tot_b = tot_c = 0
    for u in units:
        b_cnt = counts[(u, "B")]
        c_cnt = counts[(u, "C")]
        tot_b += b_cnt
        tot_c += c_cnt
        b_str = f"{b_cnt}/{min_b}" if min_b is not None else str(b_cnt)
        c_str = f"{c_cnt}/{min_c}" if min_c is not None else str(c_cnt)
        print(f"{u:<58} {b_str:>10} {c_str:>10} {b_cnt + c_cnt:>8}")
    print("-" * len(header))
    print(f"{'Total (Units 1-14)':<58} {tot_b:>10} {tot_c:>10} {tot_b + tot_c:>8}")


def run_balance_loop(
    client,
    conn,
    models,
    pricing_map,
    min_b=160,
    min_c=180,
    max_budget=30.0,
):
    unit_rows = conn.execute(
        "SELECT DISTINCT syllabus_unit FROM pyqs WHERE syllabus_unit LIKE 'Unit %'"
    ).fetchall()
    units = sorted({r[0] for r in unit_rows if r[0]}, key=unit_sort_key)
    if not units:
        print("No Units 1-14 found in `pyqs`. Run `--mode tag` first.")
        return

    print(f"\n--- Unit Balance Mode (Targets: Part B >= {min_b}, Part C >= {min_c}, Budget Cap: ${max_budget:.2f}) ---")
    print_unit_summary_table(conn, units, min_b=min_b, min_c=min_c)

    cycle_counts = {}
    zero_streak = {}
    stalled_buckets = set()

    while True:
        spend = get_total_spend(conn)
        if spend >= max_budget:
            print(f"\n[STOP] Hard budget cap (${max_budget:.2f}) reached. Current spend: ${spend:.4f}.")
            break

        counts = get_unit_part_counts(conn, units)
        deficits = []
        for u in units:
            for p, target in (("B", min_b), ("C", min_c)):
                if (u, p) in stalled_buckets:
                    continue
                cur = counts[(u, p)]
                if cur < target:
                    deficits.append((target - cur, u, p, cur, target))

        if not deficits:
            print(f"\n[DONE] All {len(units)} units reached Part B >= {min_b} and Part C >= {min_c}!")
            break

        # Pick bucket with largest remaining deficit (tie-break by unit number, then Part)
        deficits.sort(key=lambda item: (-item[0], unit_sort_key(item[1]), item[2]))
        deficit, unit, part, cur, target = deficits[0]
        c_idx = cycle_counts.get((unit, part), 0)
        cycle_counts[(unit, part)] = c_idx + 1

        try:
            accepted, batch_cost, attempted = generate_and_verify_batch(
                client,
                conn,
                models,
                pricing_map,
                unit,
                part,
                cycle_idx=c_idx,
                distinct_angle_mode=True,
            )
            new_cur = cur + accepted
            new_spend = spend + batch_cost
            print(
                f"[{unit} | Part {part}] +{accepted}/{attempted} verified | "
                f"Unit Part: {new_cur}/{target} (deficit {max(0, target - new_cur)}) | "
                f"Batch: ${batch_cost:.4f} | Total Spend: ${new_spend:.4f}"
            )
            if accepted == 0:
                streak = zero_streak.get((unit, part), 0) + 1
                zero_streak[(unit, part)] = streak
                if streak >= 3:
                    print(f"  [WARN] 3 consecutive 0-verified batches for ({unit}, Part {part}); skipping bucket.")
                    stalled_buckets.add((unit, part))
            else:
                zero_streak[(unit, part)] = 0
        except KeyboardInterrupt:
            print("\nPaused safely by user. Re-run the same command anytime to resume.")
            break
        except Exception as e:
            print(f"[{unit} | Part {part}] Batch failed: {e}. Waiting 10s...")
            time.sleep(10)

    print_unit_summary_table(conn, units, min_b=min_b, min_c=min_c)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="CSIR NET Azure Foundry Question Generation Pipeline"
    )
    parser.add_argument(
        "--mode",
        choices=["check", "tag", "pilot", "inspect", "generate", "generate-syllabus", "balance"],
        required=True,
    )
    parser.add_argument("--target", type=int, default=5000, help="Target verified Qs")
    parser.add_argument("--max-budget", type=float, default=15.0, help="Hard USD cap")
    parser.add_argument("--limit", type=int, default=5, help="Rows to show in inspect mode")
    parser.add_argument("--unit", default=None, help="Filter by unit in inspect mode (e.g. 'Unit 11', '11,12,13')")
    parser.add_argument("--topics", default="dbt_topics.json", help="Path to DBT topics JSON file")
    parser.add_argument("--min-b", type=int, default=160, help="Min verified Part B per unit in balance mode")
    parser.add_argument("--min-c", type=int, default=180, help="Min verified Part C per unit in balance mode")
    args = parser.parse_args()

    conn = sqlite3.connect(DB_PATH)
    init_db(conn)

    if args.mode == "check":
        run_preflight_check(conn)
    elif args.mode == "inspect":
        inspect_verified(conn, limit=args.limit, unit_filter=args.unit)
    else:
        client, models = get_client_and_models()
        pricing_map = get_pricing_map(models)
        if args.mode == "tag":
            bulk_tag_pyqs(
                client, conn, models, pricing_map, max_budget=args.max_budget
            )
        elif args.mode == "pilot":
            print("\n--- Step 2: Running 50-Question Pilot (capped at $1.00) ---")
            run_generation_loop(
                client,
                conn,
                models,
                pricing_map,
                target_verified=50,
                max_budget=min(args.max_budget, 1.00),
            )
        elif args.mode == "generate":
            run_generation_loop(
                client,
                conn,
                models,
                pricing_map,
                target_verified=args.target,
                max_budget=args.max_budget,
            )
        elif args.mode == "generate-syllabus":
            run_syllabus_generation_loop(
                client,
                conn,
                models,
                pricing_map,
                target_verified=args.target,
                max_budget=args.max_budget,
                topics_path=args.topics,
            )
        elif args.mode == "balance":
            run_balance_loop(
                client,
                conn,
                models,
                pricing_map,
                min_b=args.min_b,
                min_c=args.min_c,
                max_budget=args.max_budget,
            )

    conn.close()