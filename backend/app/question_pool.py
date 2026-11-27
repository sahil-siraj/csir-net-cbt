import json
import re
from typing import Any

from app.db import query_all

IMAGE_KEYWORDS = (
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


def _pyq_image_exclusion_clause() -> tuple[str, list[str]]:
    clauses = ["LOWER(p.question_text) LIKE ?" for _ in IMAGE_KEYWORDS]
    params = [f"%{kw}%" for kw in IMAGE_KEYWORDS]
    return f"NOT ({' OR '.join(clauses)})", params


def parse_options(options_json: str | None) -> dict[str, str]:
    if not options_json:
        return {}
    try:
        parsed = json.loads(options_json)
        if isinstance(parsed, dict):
            return {str(k): str(v) for k, v in parsed.items()}
    except (json.JSONDecodeError, TypeError):
        pass
    return {}


def unit_sort_key(unit_name: str) -> tuple[int, int, str]:
    if unit_name == "General Aptitude":
        return (0, 0, unit_name)
    m = re.match(r"Unit\s+(\d+)\b", unit_name, re.IGNORECASE)
    if m:
        return (1, int(m.group(1)), unit_name)
    return (2, 0, unit_name)


def fetch_eligible_pool(
    source_filter: str = "mixed",
    syllabus_units: list[str] | None = None,
    parts: list[str] | None = None,
    include_answers: bool = False,
) -> list[dict[str, Any]]:
    """
    Return the unified eligible question pool across `pyqs` and `generated_questions`:
      - Excludes PYQs containing case-insensitive image keywords
      - Includes only `verified = 1` from `generated_questions`
      - Always excludes questions with unresolved entries in `question_flags`
    """
    results: list[dict[str, Any]] = []
    answer_cols = "correct_option, " if include_answers else ""

    unit_clause = ""
    unit_params: list[str] = []
    if syllabus_units:
        placeholders = ", ".join(["?"] * len(syllabus_units))
        unit_clause = f" AND syllabus_unit IN ({placeholders})"
        unit_params = list(syllabus_units)

    part_clause = ""
    part_params: list[str] = []
    if parts:
        norm_parts = [p.strip().upper() for p in parts if p.strip()]
        if norm_parts:
            placeholders = ", ".join(["?"] * len(norm_parts))
            part_clause = f" AND part IN ({placeholders})"
            part_params = norm_parts

    if source_filter in ("mixed", "pyq_only"):
        img_clause, img_params = _pyq_image_exclusion_clause()
        sql_pyq = f"""
            SELECT
                'pyq' AS question_source,
                p.id AS question_id,
                p.question_text,
                p.options_json,
                {('p.correct_option, ' if include_answers else '')}
                NULL AS explanation,
                p.syllabus_unit,
                p.part,
                p.exam_year
            FROM pyqs p
            WHERE {img_clause}
              AND NOT EXISTS (
                  SELECT 1 FROM question_flags qf
                  WHERE qf.question_source = 'pyq'
                    AND qf.question_id = p.id
                    AND qf.resolved = 0
              )
              {unit_clause.replace('syllabus_unit', 'p.syllabus_unit')}
              {part_clause.replace('part', 'p.part')}
        """
        results.extend(query_all(sql_pyq, img_params + unit_params + part_params))

    if source_filter in ("mixed", "ai_only"):
        sql_gen = f"""
            SELECT
                'generated' AS question_source,
                g.id AS question_id,
                g.question_text,
                g.options_json,
                {('g.correct_option, ' if include_answers else '')}
                {('g.explanation, ' if include_answers else 'NULL AS explanation, ')}
                g.syllabus_unit,
                g.part,
                NULL AS exam_year
            FROM generated_questions g
            WHERE g.verified = 1
              AND NOT EXISTS (
                  SELECT 1 FROM question_flags qf
                  WHERE qf.question_source = 'generated'
                    AND qf.question_id = g.id
                    AND qf.resolved = 0
              )
              {unit_clause.replace('syllabus_unit', 'g.syllabus_unit')}
              {part_clause.replace('part', 'g.part')}
        """
        results.extend(query_all(sql_gen, unit_params + part_params))

    return results


def fetch_questions_by_keys(
    keys: list[tuple[str, int]],
    include_answers: bool = True,
) -> dict[tuple[str, int], dict[str, Any]]:
    """Fetch question records by `(question_source, question_id)` pairs."""
    pyq_ids = sorted({qid for src, qid in keys if src == "pyq"})
    gen_ids = sorted({qid for src, qid in keys if src == "generated"})
    lookup: dict[tuple[str, int], dict[str, Any]] = {}

    if pyq_ids:
        placeholders = ", ".join(["?"] * len(pyq_ids))
        ans = "correct_option, " if include_answers else ""
        rows = query_all(
            f"""
            SELECT
                'pyq' AS question_source,
                id AS question_id,
                question_text,
                options_json,
                {ans}
                NULL AS explanation,
                syllabus_unit,
                part,
                exam_year
            FROM pyqs
            WHERE id IN ({placeholders})
            """,
            pyq_ids,
        )
        for row in rows:
            lookup[("pyq", int(row["question_id"]))] = row

    if gen_ids:
        placeholders = ", ".join(["?"] * len(gen_ids))
        ans = "correct_option, explanation, " if include_answers else "NULL AS explanation, "
        rows = query_all(
            f"""
            SELECT
                'generated' AS question_source,
                id AS question_id,
                question_text,
                options_json,
                {ans}
                syllabus_unit,
                part,
                NULL AS exam_year
            FROM generated_questions
            WHERE id IN ({placeholders})
            """,
            gen_ids,
        )
        for row in rows:
            lookup[("generated", int(row["question_id"]))] = row

    return lookup


def get_user_attempt_map(user_id: int) -> dict[tuple[str, int], int | None]:
    """
    Return {(question_source, question_id): latest_is_correct_or_None} for every
    question the user has previously attempted (selected_option IS NOT NULL or is_correct IS NOT NULL).
    """
    rows = query_all(
        """
        SELECT question_source, question_id, is_correct
        FROM test_responses
        WHERE user_id = ?
          AND (selected_option IS NOT NULL OR is_correct IS NOT NULL)
        ORDER BY updated_at DESC, id DESC
        """,
        (user_id,),
    )
    attempt_map: dict[tuple[str, int], int | None] = {}
    for row in rows:
        key = (row["question_source"], int(row["question_id"]))
        if key not in attempt_map:
            attempt_map[key] = row["is_correct"]
        elif attempt_map[key] is None and row["is_correct"] is not None:
            attempt_map[key] = row["is_correct"]
    return attempt_map
