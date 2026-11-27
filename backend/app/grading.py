from collections import defaultdict
from typing import Any

PART_RULES: dict[str, dict[str, Any]] = {
    "A": {"positive": 2.0, "negative": 0.5, "full_mock_limit": 15},
    "B": {"positive": 2.0, "negative": 0.5, "full_mock_limit": 35},
    "C": {"positive": 4.0, "negative": 1.0, "full_mock_limit": 25},
}

ANSWERED_STATUSES = {"answered", "answered_marked"}
VALID_OPTIONS = {"A", "B", "C", "D"}


def get_part_rule(part: str) -> dict[str, Any]:
    key = (part or "").strip().upper()
    if key not in PART_RULES:
        raise ValueError(f"Unsupported CSIR NET part: {part!r}")
    return PART_RULES[key]


def is_response_answered(resp: dict[str, Any]) -> bool:
    status = resp.get("status")
    opt = (resp.get("selected_option") or "").strip().upper()
    return status in ANSWERED_STATUSES and opt in VALID_OPTIONS


def format_source_label(question_source: str, exam_year: str | None) -> str:
    if question_source == "pyq":
        year = (exam_year or "").strip()
        return f"PYQ {year}" if year else "PYQ"
    return "AI Verified"


def grade_test_responses(
    test_type: str,
    responses: list[dict[str, Any]],
    correct_answers: dict[tuple[str, int], str],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """
    Grade all responses for a test according to CSIR NET rules.

    For `full_mock`, enforces NTA First-N grading per part:
      - Part A: first 15 answered (+2 / -0.5)
      - Part B: first 35 answered (+2 / -0.5)
      - Part C: first 25 answered (+4 / -1.0)
    Any answered questions beyond N in a part are marked `is_evaluated = 0`, `marks_awarded = 0.0`.

    For `unit_practice`, `mistake_drill`, and `bookmark_drill`, all answered questions
    are evaluated using their part's marking scheme.
    """
    is_full_mock = test_type == "full_mock"

    # Sort all responses by question_order ascending
    ordered = sorted(responses, key=lambda r: int(r["question_order"]))

    # Determine which answered response IDs are evaluated under First-N rules
    evaluated_ids: set[int] = set()
    by_part: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for resp in ordered:
        by_part[resp["part"]].append(resp)

    for part, part_rows in by_part.items():
        rule = get_part_rule(part)
        answered_rows = [r for r in part_rows if is_response_answered(r)]
        if is_full_mock:
            limit = int(rule["full_mock_limit"])
            for r in answered_rows[:limit]:
                evaluated_ids.add(int(r["id"]))
        else:
            for r in answered_rows:
                evaluated_ids.add(int(r["id"]))

    graded_rows: list[dict[str, Any]] = []

    part_stats: dict[str, dict[str, Any]] = {}
    unit_stats: dict[str, dict[str, Any]] = {}

    total_score = 0.0
    max_score = 0.0
    negative_loss = 0.0
    correct_count = 0
    incorrect_count = 0
    unanswered_count = 0
    over_limit_count = 0

    for part in ("A", "B", "C"):
        if part in by_part:
            rule = get_part_rule(part)
            total_in_part = len(by_part[part])
            allowed = min(total_in_part, int(rule["full_mock_limit"])) if is_full_mock else total_in_part
            part_max = round(allowed * float(rule["positive"]), 2)
            max_score += part_max
            part_stats[part] = {
                "part": part,
                "total_questions": total_in_part,
                "max_evaluated_allowed": allowed,
                "attempted": 0,
                "evaluated": 0,
                "over_limit_ignored": 0,
                "correct": 0,
                "incorrect": 0,
                "unanswered": 0,
                "score": 0.0,
                "max_score": part_max,
                "negative_loss": 0.0,
                "accuracy": 0.0,
            }

    for resp in ordered:
        resp_id = int(resp["id"])
        part = resp["part"]
        unit = resp["syllabus_unit"]
        rule = get_part_rule(part)
        pos_marks = float(rule["positive"])
        neg_marks = float(rule["negative"])

        if unit not in unit_stats:
            unit_stats[unit] = {
                "syllabus_unit": unit,
                "total_questions": 0,
                "attempted": 0,
                "evaluated": 0,
                "correct": 0,
                "incorrect": 0,
                "unanswered": 0,
                "score": 0.0,
                "negative_loss": 0.0,
                "accuracy": 0.0,
            }

        p_stat = part_stats[part]
        u_stat = unit_stats[unit]
        u_stat["total_questions"] += 1

        answered = is_response_answered(resp)
        key = (resp["question_source"], int(resp["question_id"]))
        correct_opt = (correct_answers.get(key) or "").strip().upper()
        selected_opt = (resp.get("selected_option") or "").strip().upper() or None

        if not answered:
            is_evaluated = 0
            is_correct = None
            marks_awarded = 0.0
            unanswered_count += 1
            p_stat["unanswered"] += 1
            u_stat["unanswered"] += 1
        elif resp_id not in evaluated_ids:
            # Answered beyond NTA First-N limit in full_mock
            is_evaluated = 0
            is_correct = None
            marks_awarded = 0.0
            over_limit_count += 1
            p_stat["attempted"] += 1
            p_stat["over_limit_ignored"] += 1
            u_stat["attempted"] += 1
        else:
            is_evaluated = 1
            p_stat["attempted"] += 1
            p_stat["evaluated"] += 1
            u_stat["attempted"] += 1
            u_stat["evaluated"] += 1

            if selected_opt == correct_opt:
                is_correct = 1
                marks_awarded = pos_marks
                correct_count += 1
                p_stat["correct"] += 1
                u_stat["correct"] += 1
            else:
                is_correct = 0
                marks_awarded = -neg_marks
                incorrect_count += 1
                negative_loss += neg_marks
                p_stat["incorrect"] += 1
                p_stat["negative_loss"] += neg_marks
                u_stat["incorrect"] += 1
                u_stat["negative_loss"] += neg_marks

            total_score += marks_awarded
            p_stat["score"] += marks_awarded
            u_stat["score"] += marks_awarded

        graded_rows.append(
            {
                "id": resp_id,
                "is_evaluated": is_evaluated,
                "is_correct": is_correct,
                "marks_awarded": round(marks_awarded, 2),
            }
        )

    for p_stat in part_stats.values():
        p_eval = p_stat["correct"] + p_stat["incorrect"]
        p_stat["score"] = round(p_stat["score"], 2)
        p_stat["negative_loss"] = round(p_stat["negative_loss"], 2)
        p_stat["accuracy"] = round((p_stat["correct"] / p_eval * 100.0), 2) if p_eval > 0 else 0.0

    for u_stat in unit_stats.values():
        u_eval = u_stat["correct"] + u_stat["incorrect"]
        u_stat["score"] = round(u_stat["score"], 2)
        u_stat["negative_loss"] = round(u_stat["negative_loss"], 2)
        u_stat["accuracy"] = round((u_stat["correct"] / u_eval * 100.0), 2) if u_eval > 0 else 0.0

    total_evaluated = correct_count + incorrect_count
    overall_accuracy = round((correct_count / total_evaluated * 100.0), 2) if total_evaluated > 0 else 0.0

    summary = {
        "total_questions": len(ordered),
        "total_score": round(total_score, 2),
        "max_score": round(max_score, 2),
        "negative_loss": round(negative_loss, 2),
        "correct_count": correct_count,
        "incorrect_count": incorrect_count,
        "unanswered_count": unanswered_count,
        "over_limit_count": over_limit_count,
        "accuracy": overall_accuracy,
        "part_summary": [part_stats[p] for p in ("A", "B", "C") if p in part_stats],
        "unit_summary": sorted(unit_stats.values(), key=lambda x: x["syllabus_unit"]),
    }

    return graded_rows, summary
