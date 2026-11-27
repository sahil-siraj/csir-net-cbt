import json
import random
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.auth import get_current_user
from app.db import execute, execute_many, get_connection, query_all, query_one
from app.grading import (
    VALID_OPTIONS,
    format_source_label,
    grade_test_responses,
)
from app.question_pool import (
    fetch_eligible_pool,
    fetch_questions_by_keys,
    get_user_attempt_map,
    parse_options,
    unit_sort_key,
)

router = APIRouter(prefix="/api/tests", tags=["tests"])

FULL_MOCK_TARGETS = {"A": 20, "B": 50, "C": 75}
PART_ORDER = {"A": 0, "B": 1, "C": 2}


class CreateTestRequest(BaseModel):
    test_type: Literal["full_mock", "unit_practice", "mistake_drill", "bookmark_drill"]
    title: str | None = Field(default=None, max_length=160)
    source_filter: Literal["mixed", "ai_only", "pyq_only"] = "mixed"
    syllabus_units: list[str] | None = None
    parts: list[Literal["A", "B", "C"]] | None = None
    question_count: int | None = Field(default=None, ge=1, le=200)
    duration_minutes: int | None = Field(default=None, ge=1, le=360)
    instant_feedback: bool = False


class SaveResponseRequest(BaseModel):
    response_id: int | None = None
    question_order: int | None = None
    selected_option: Literal["A", "B", "C", "D"] | None = None
    status: Literal[
        "not_visited",
        "not_answered",
        "answered",
        "marked",
        "answered_marked",
    ]
    time_spent_sec: int = Field(default=0, ge=0)


class CheckAnswerRequest(BaseModel):
    response_id: int | None = None
    question_order: int | None = None
    selected_option: Literal["A", "B", "C", "D"] | None = None


def _prioritize_and_shuffle(
    candidates: list[dict[str, Any]],
    attempt_map: dict[tuple[str, int], int | None],
) -> list[dict[str, Any]]:
    """
    Prioritize:
      1. Questions this user has never attempted
      2. Previously missed questions (latest is_correct == 0)
      3. Other previously seen questions
    """
    never_attempted: list[dict[str, Any]] = []
    previously_missed: list[dict[str, Any]] = []
    other_seen: list[dict[str, Any]] = []

    for q in candidates:
        key = (q["question_source"], int(q["question_id"]))
        if key not in attempt_map:
            never_attempted.append(q)
        elif attempt_map[key] == 0:
            previously_missed.append(q)
        else:
            other_seen.append(q)

    random.shuffle(never_attempted)
    random.shuffle(previously_missed)
    random.shuffle(other_seen)

    return never_attempted + previously_missed + other_seen


def _smart_sample(
    candidates: list[dict[str, Any]],
    attempt_map: dict[tuple[str, int], int | None],
    count: int,
) -> list[dict[str, Any]]:
    return _prioritize_and_shuffle(candidates, attempt_map)[:count]


def _stratified_smart_sample(
    candidates: list[dict[str, Any]],
    attempt_map: dict[tuple[str, int], int | None],
    target: int,
) -> list[dict[str, Any]]:
    """
    Stratify sampling unit-by-unit across all syllabus units in `candidates`
    (e.g., 3-4 Part B or 5-6 Part C per unit across 14 units), prioritizing
    unseen questions first within each unit and filling any shortfall from leftovers.
    """
    if not candidates or target <= 0:
        return []

    by_unit: dict[str, list[dict[str, Any]]] = {}
    for q in candidates:
        unit = str(q.get("syllabus_unit") or "Unknown")
        by_unit.setdefault(unit, []).append(q)

    unit_names = sorted(by_unit.keys(), key=unit_sort_key)
    num_units = len(unit_names)
    if num_units == 0:
        return []

    base_quota = target // num_units
    remainder = target % num_units
    bonus_units = set(random.sample(unit_names, remainder)) if remainder else set()

    selected: list[dict[str, Any]] = []
    leftovers: list[dict[str, Any]] = []

    for unit in unit_names:
        ordered = _prioritize_and_shuffle(by_unit[unit], attempt_map)
        quota = base_quota + (1 if unit in bonus_units else 0)
        selected.extend(ordered[:quota])
        leftovers.extend(ordered[quota:])

    if len(selected) < target and leftovers:
        selected.extend(_smart_sample(leftovers, attempt_map, target - len(selected)))

    random.shuffle(selected)
    return selected[:target]


def _get_user_bookmark_set(user_id: int) -> set[tuple[str, int]]:
    rows = query_all(
        "SELECT question_source, question_id FROM bookmarks WHERE user_id = ?",
        (user_id,),
    )
    return {(r["question_source"], int(r["question_id"])) for r in rows}


def _find_response_row(
    test_id: int,
    user_id: int,
    response_id: int | None,
    question_order: int | None,
) -> dict[str, Any]:
    if response_id is not None:
        row = query_one(
            """
            SELECT * FROM test_responses
            WHERE id = ? AND test_id = ? AND user_id = ?
            """,
            (response_id, test_id, user_id),
        )
    elif question_order is not None:
        row = query_one(
            """
            SELECT * FROM test_responses
            WHERE question_order = ? AND test_id = ? AND user_id = ?
            """,
            (question_order, test_id, user_id),
        )
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Either response_id or question_order must be provided",
        )

    if not row:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Question response not found for this test",
        )
    return row


def _build_test_payload(test: dict[str, Any], user_id: int) -> dict[str, Any]:
    test_id = int(test["id"])
    responses = query_all(
        """
        SELECT * FROM test_responses
        WHERE test_id = ? AND user_id = ?
        ORDER BY question_order ASC
        """,
        (test_id, user_id),
    )

    keys = [(r["question_source"], int(r["question_id"])) for r in responses]
    question_map = fetch_questions_by_keys(keys, include_answers=True)
    bookmark_set = _get_user_bookmark_set(user_id)

    is_completed = test["status"] == "completed"
    serialized_questions: list[dict[str, Any]] = []

    for resp in responses:
        src = resp["question_source"]
        qid = int(resp["question_id"])
        q = question_map.get((src, qid), {})

        item: dict[str, Any] = {
            "response_id": resp["id"],
            "question_order": resp["question_order"],
            "part": resp["part"],
            "syllabus_unit": resp["syllabus_unit"],
            "question_text": q.get("question_text", ""),
            "options": parse_options(q.get("options_json")),
            "selected_option": resp["selected_option"],
            "status": resp["status"],
            "time_spent_sec": resp["time_spent_sec"],
            "is_checked": bool(resp["is_checked"]),
            "is_bookmarked": (src, qid) in bookmark_set,
        }

        # CRITICAL: While status == 'in_progress', omit correct_option, explanation,
        # exam_year, and question_source from unanswered/unverified questions.
        if is_completed or resp["is_checked"]:
            item["question_source"] = src
            item["exam_year"] = q.get("exam_year")
            item["source_label"] = format_source_label(src, q.get("exam_year"))
            item["correct_option"] = q.get("correct_option")
            item["explanation"] = q.get("explanation")
            item["is_correct"] = (
                bool(resp["is_correct"]) if resp["is_correct"] is not None else None
            )
            if is_completed:
                item["is_evaluated"] = (
                    bool(resp["is_evaluated"])
                    if resp["is_evaluated"] is not None
                    else None
                )
                item["marks_awarded"] = resp["marks_awarded"]

        serialized_questions.append(item)

    test_meta = {
        "id": test["id"],
        "user_id": test["user_id"],
        "test_type": test["test_type"],
        "title": test["title"],
        "status": test["status"],
        "source_filter": test["source_filter"],
        "instant_feedback": bool(test["instant_feedback"]),
        "duration_minutes": test["duration_minutes"],
        "total_questions": test["total_questions"],
        "started_at": test["started_at"],
        "submitted_at": test["submitted_at"],
    }
    if is_completed:
        test_meta.update(
            {
                "total_score": test["total_score"],
                "max_score": test["max_score"],
                "negative_loss": test["negative_loss"],
                "correct_count": test["correct_count"],
                "incorrect_count": test["incorrect_count"],
                "unanswered_count": test["unanswered_count"],
            }
        )

    payload: dict[str, Any] = {
        "test": test_meta,
        "questions": serialized_questions,
    }

    if is_completed and test.get("summary_json"):
        try:
            payload["scorecard"] = json.loads(test["summary_json"])
        except (json.JSONDecodeError, TypeError):
            pass

    return payload


@router.get("/meta")
def get_tests_meta(current_user: dict[str, Any] = Depends(get_current_user)):
    user_id = int(current_user["id"])
    pool = fetch_eligible_pool(source_filter="mixed", include_answers=False)

    by_unit: dict[str, dict[str, Any]] = {}
    by_part: dict[str, dict[str, int]] = {
        "A": {"pyq": 0, "ai": 0, "total": 0},
        "B": {"pyq": 0, "ai": 0, "total": 0},
        "C": {"pyq": 0, "ai": 0, "total": 0},
    }
    totals = {"pyq": 0, "ai": 0, "total": 0}
    eligible_keys: set[tuple[str, int]] = set()

    for q in pool:
        key = (q["question_source"], int(q["question_id"]))
        eligible_keys.add(key)

        unit = q["syllabus_unit"]
        part = q["part"]
        src_bucket = "pyq" if q["question_source"] == "pyq" else "ai"

        if unit not in by_unit:
            by_unit[unit] = {
                "syllabus_unit": unit,
                "pyq_count": 0,
                "ai_count": 0,
                "total_count": 0,
                "parts": {
                    "A": {"pyq": 0, "ai": 0, "total": 0},
                    "B": {"pyq": 0, "ai": 0, "total": 0},
                    "C": {"pyq": 0, "ai": 0, "total": 0},
                },
            }

        u_entry = by_unit[unit]
        u_entry[f"{src_bucket}_count"] += 1
        u_entry["total_count"] += 1
        if part in u_entry["parts"]:
            u_entry["parts"][part][src_bucket] += 1
            u_entry["parts"][part]["total"] += 1

        if part in by_part:
            by_part[part][src_bucket] += 1
            by_part[part]["total"] += 1

        totals[src_bucket] += 1
        totals["total"] += 1

    sorted_units = sorted(by_unit.keys(), key=unit_sort_key)

    attempt_map = get_user_attempt_map(user_id)
    mistake_count = sum(
        1 for key, val in attempt_map.items() if val == 0 and key in eligible_keys
    )
    bookmark_set = _get_user_bookmark_set(user_id)
    bookmark_count = sum(1 for key in bookmark_set if key in eligible_keys)

    return {
        "syllabus_units": sorted_units,
        "counts": {
            "by_unit": [by_unit[u] for u in sorted_units],
            "by_part": by_part,
            "totals": totals,
        },
        "user_stats": {
            "mistake_count": mistake_count,
            "bookmark_count": bookmark_count,
        },
    }


@router.post("/create", status_code=status.HTTP_201_CREATED)
def create_test(
    payload: CreateTestRequest,
    current_user: dict[str, Any] = Depends(get_current_user),
):
    user_id = int(current_user["id"])
    attempt_map = get_user_attempt_map(user_id)

    test_type = payload.test_type
    source_filter = payload.source_filter
    instant_feedback = False if test_type == "full_mock" else bool(payload.instant_feedback)
    duration_minutes = 180 if test_type == "full_mock" else payload.duration_minutes

    selected_questions: list[dict[str, Any]] = []

    if test_type == "full_mock":
        for part in ("A", "B", "C"):
            target = FULL_MOCK_TARGETS[part]
            part_pool = fetch_eligible_pool(
                source_filter=source_filter,
                parts=[part],
                include_answers=False,
            )
            if part in ("B", "C"):
                selected_questions.extend(
                    _stratified_smart_sample(part_pool, attempt_map, target)
                )
            else:
                selected_questions.extend(_smart_sample(part_pool, attempt_map, target))

        if not selected_questions:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No eligible questions available for the requested full mock configuration",
            )
        default_title = f"Full Mock Test ({source_filter.replace('_', ' ').title()})"

    elif test_type == "unit_practice":
        parts = payload.parts or ["A", "B", "C"]
        target_count = payload.question_count or 20
        pool = fetch_eligible_pool(
            source_filter=source_filter,
            syllabus_units=payload.syllabus_units,
            parts=parts,
            include_answers=False,
        )
        if not pool:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No eligible questions match the selected units, parts, and source filter",
            )
        selected_questions = _smart_sample(pool, attempt_map, target_count)
        selected_questions.sort(key=lambda q: PART_ORDER.get(q["part"], 99))
        default_title = "Unit Practice"

    elif test_type == "mistake_drill":
        pool = fetch_eligible_pool(
            source_filter=source_filter,
            syllabus_units=payload.syllabus_units,
            parts=payload.parts,
            include_answers=False,
        )
        mistake_pool = [
            q
            for q in pool
            if attempt_map.get((q["question_source"], int(q["question_id"]))) == 0
        ]
        if not mistake_pool:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No previously missed questions available for mistake drill",
            )
        random.shuffle(mistake_pool)
        if payload.question_count:
            mistake_pool = mistake_pool[: payload.question_count]
        mistake_pool.sort(key=lambda q: PART_ORDER.get(q["part"], 99))
        selected_questions = mistake_pool
        default_title = "Mistake Drill"

    elif test_type == "bookmark_drill":
        bookmark_set = _get_user_bookmark_set(user_id)
        if not bookmark_set:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No bookmarked questions available for bookmark drill",
            )
        pool = fetch_eligible_pool(
            source_filter=source_filter,
            syllabus_units=payload.syllabus_units,
            parts=payload.parts,
            include_answers=False,
        )
        bookmark_pool = [
            q
            for q in pool
            if (q["question_source"], int(q["question_id"])) in bookmark_set
        ]
        if not bookmark_pool:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No eligible bookmarked questions match the selected filters",
            )
        target_count = payload.question_count or len(bookmark_pool)
        selected_questions = _smart_sample(bookmark_pool, attempt_map, target_count)
        selected_questions.sort(key=lambda q: PART_ORDER.get(q["part"], 99))
        default_title = "Bookmark Drill"

    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported test_type: {test_type}",
        )

    title = (payload.title or "").strip() or default_title
    config_json = json.dumps(
        {
            "syllabus_units": payload.syllabus_units,
            "parts": payload.parts,
            "question_count": payload.question_count,
            "source_filter": source_filter,
        }
    )

    test_id = execute(
        """
        INSERT INTO mock_tests (
            user_id, test_type, title, status, source_filter,
            instant_feedback, duration_minutes, config_json, total_questions
        )
        VALUES (?, ?, ?, 'in_progress', ?, ?, ?, ?, ?)
        """,
        (
            user_id,
            test_type,
            title,
            source_filter,
            1 if instant_feedback else 0,
            duration_minutes,
            config_json,
            len(selected_questions),
        ),
    )

    response_rows = [
        (
            test_id,
            user_id,
            q["question_source"],
            int(q["question_id"]),
            q["part"],
            q["syllabus_unit"],
            idx,
        )
        for idx, q in enumerate(selected_questions, start=1)
    ]
    execute_many(
        """
        INSERT INTO test_responses (
            test_id, user_id, question_source, question_id,
            part, syllabus_unit, question_order
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        response_rows,
    )

    created_test = query_one(
        "SELECT * FROM mock_tests WHERE id = ? AND user_id = ?",
        (test_id, user_id),
    )
    if not created_test:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to load newly created test",
        )
    return _build_test_payload(created_test, user_id)


@router.get("/{test_id}")
def get_test(
    test_id: int,
    current_user: dict[str, Any] = Depends(get_current_user),
):
    user_id = int(current_user["id"])
    test = query_one(
        "SELECT * FROM mock_tests WHERE id = ? AND user_id = ?",
        (test_id, user_id),
    )
    if not test:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Test not found",
        )
    return _build_test_payload(test, user_id)


@router.patch("/{test_id}/response")
def save_response(
    test_id: int,
    payload: SaveResponseRequest,
    current_user: dict[str, Any] = Depends(get_current_user),
):
    user_id = int(current_user["id"])
    test = query_one(
        "SELECT * FROM mock_tests WHERE id = ? AND user_id = ?",
        (test_id, user_id),
    )
    if not test:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Test not found",
        )
    if test["status"] != "in_progress":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot modify responses after test submission",
        )

    resp = _find_response_row(
        test_id, user_id, payload.response_id, payload.question_order
    )

    if payload.status in ("answered", "answered_marked") and not payload.selected_option:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="selected_option is required when status is answered or answered_marked",
        )

    selected_opt = payload.selected_option
    if payload.status in ("not_visited", "not_answered"):
        selected_opt = None

    execute(
        """
        UPDATE test_responses
        SET selected_option = ?,
            status = ?,
            time_spent_sec = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ? AND test_id = ? AND user_id = ?
        """,
        (selected_opt, payload.status, payload.time_spent_sec, resp["id"], test_id, user_id),
    )

    updated = query_one(
        "SELECT * FROM test_responses WHERE id = ? AND user_id = ?",
        (resp["id"], user_id),
    )
    return {
        "response_id": updated["id"],
        "question_order": updated["question_order"],
        "selected_option": updated["selected_option"],
        "status": updated["status"],
        "time_spent_sec": updated["time_spent_sec"],
        "is_checked": bool(updated["is_checked"]),
    }


@router.post("/{test_id}/check-answer")
def check_answer(
    test_id: int,
    payload: CheckAnswerRequest,
    current_user: dict[str, Any] = Depends(get_current_user),
):
    user_id = int(current_user["id"])
    test = query_one(
        "SELECT * FROM mock_tests WHERE id = ? AND user_id = ?",
        (test_id, user_id),
    )
    if not test:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Test not found",
        )
    if test["status"] != "in_progress":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Test is already completed",
        )
    if not test["instant_feedback"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Instant feedback is not enabled for this test",
        )

    resp = _find_response_row(
        test_id, user_id, payload.response_id, payload.question_order
    )
    selected_opt = (payload.selected_option or resp.get("selected_option") or "").strip().upper()
    if selected_opt not in VALID_OPTIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A valid selected_option (A, B, C, or D) is required to check answer",
        )

    key = (resp["question_source"], int(resp["question_id"]))
    q_lookup = fetch_questions_by_keys([key], include_answers=True)
    q = q_lookup.get(key)
    if not q:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Question record not found",
        )

    correct_opt = (q.get("correct_option") or "").strip().upper()
    is_correct = 1 if selected_opt == correct_opt else 0
    new_status = "answered_marked" if resp["status"] == "answered_marked" else "answered"

    execute(
        """
        UPDATE test_responses
        SET selected_option = ?,
            status = ?,
            is_checked = 1,
            is_correct = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ? AND test_id = ? AND user_id = ?
        """,
        (selected_opt, new_status, is_correct, resp["id"], test_id, user_id),
    )

    return {
        "response_id": resp["id"],
        "question_order": resp["question_order"],
        "selected_option": selected_opt,
        "correct_option": correct_opt,
        "explanation": q.get("explanation"),
        "is_correct": bool(is_correct),
        "question_source": resp["question_source"],
        "exam_year": q.get("exam_year"),
        "source_label": format_source_label(resp["question_source"], q.get("exam_year")),
    }


@router.post("/{test_id}/submit")
def submit_test(
    test_id: int,
    current_user: dict[str, Any] = Depends(get_current_user),
):
    user_id = int(current_user["id"])
    test = query_one(
        "SELECT * FROM mock_tests WHERE id = ? AND user_id = ?",
        (test_id, user_id),
    )
    if not test:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Test not found",
        )
    if test["status"] != "in_progress":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Test has already been submitted",
        )

    responses = query_all(
        """
        SELECT * FROM test_responses
        WHERE test_id = ? AND user_id = ?
        ORDER BY question_order ASC
        """,
        (test_id, user_id),
    )

    keys = [(r["question_source"], int(r["question_id"])) for r in responses]
    q_lookup = fetch_questions_by_keys(keys, include_answers=True)
    correct_answers = {
        key: (q.get("correct_option") or "").strip().upper()
        for key, q in q_lookup.items()
    }

    graded_rows, summary = grade_test_responses(
        test_type=test["test_type"],
        responses=responses,
        correct_answers=correct_answers,
    )

    with get_connection() as conn:
        for g in graded_rows:
            conn.execute(
                """
                UPDATE test_responses
                SET is_evaluated = ?,
                    is_correct = ?,
                    marks_awarded = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND test_id = ? AND user_id = ?
                """,
                (
                    g["is_evaluated"],
                    g["is_correct"],
                    g["marks_awarded"],
                    g["id"],
                    test_id,
                    user_id,
                ),
            )
        conn.execute(
            """
            UPDATE mock_tests
            SET status = 'completed',
                total_score = ?,
                max_score = ?,
                negative_loss = ?,
                correct_count = ?,
                incorrect_count = ?,
                unanswered_count = ?,
                summary_json = ?,
                submitted_at = CURRENT_TIMESTAMP
            WHERE id = ? AND user_id = ?
            """,
            (
                summary["total_score"],
                summary["max_score"],
                summary["negative_loss"],
                summary["correct_count"],
                summary["incorrect_count"],
                summary["unanswered_count"],
                json.dumps(summary),
                test_id,
                user_id,
            ),
        )
        conn.commit()

    completed_test = query_one(
        "SELECT * FROM mock_tests WHERE id = ? AND user_id = ?",
        (test_id, user_id),
    )
    return _build_test_payload(completed_test, user_id)
