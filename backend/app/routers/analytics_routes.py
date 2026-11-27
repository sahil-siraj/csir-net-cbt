from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.auth import get_current_user
from app.config import settings
from app.db import execute, query_all, query_one
from app.grading import format_source_label
from app.question_pool import fetch_questions_by_keys, parse_options

router = APIRouter(prefix="/api", tags=["analytics"])


class BookmarkToggleRequest(BaseModel):
    question_source: Literal["pyq", "generated"] | None = None
    question_id: int | None = None
    response_id: int | None = None


class FlagQuestionRequest(BaseModel):
    question_source: Literal["pyq", "generated"] | None = None
    question_id: int | None = None
    response_id: int | None = None
    reason: Literal[
        "typo",
        "wrong_answer",
        "broken_latex",
        "missing_diagram",
        "other",
    ]
    details: str | None = Field(default=None, max_length=1000)


def _resolve_question_target(
    user_id: int,
    question_source: str | None,
    question_id: int | None,
    response_id: int | None,
) -> tuple[str, int, bool]:
    """
    Resolve `(question_source, question_id, via_response_id)`.
    Supporting `response_id` lets clients bookmark or flag during an `in_progress`
    test without needing `question_source` exposed in the test payload.
    """
    if response_id is not None:
        resp = query_one(
            """
            SELECT question_source, question_id
            FROM test_responses
            WHERE id = ? AND user_id = ?
            """,
            (response_id, user_id),
        )
        if not resp:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Test response not found",
            )
        return resp["question_source"], int(resp["question_id"]), True

    if question_source is None or question_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Provide either response_id or both question_source and question_id",
        )

    table = "pyqs" if question_source == "pyq" else "generated_questions"
    exists = query_one(f"SELECT id FROM {table} WHERE id = ?", (question_id,))
    if not exists:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Question not found",
        )
    return question_source, int(question_id), False


@router.get("/health")
def health_check():
    query_one("SELECT 1 AS ok")
    return {
        "status": "ok",
        "db_mode": settings.db_mode,
    }


@router.get("/analytics/overview")
def get_analytics_overview(current_user: dict[str, Any] = Depends(get_current_user)):
    user_id = int(current_user["id"])

    completed_agg = query_one(
        """
        SELECT
            COUNT(*) AS total_tests,
            AVG(total_score) AS avg_score,
            SUM(COALESCE(correct_count, 0)) AS total_correct,
            SUM(COALESCE(incorrect_count, 0)) AS total_incorrect
        FROM mock_tests
        WHERE user_id = ? AND status = 'completed'
        """,
        (user_id,),
    )

    in_progress_row = query_one(
        """
        SELECT COUNT(*) AS cnt
        FROM mock_tests
        WHERE user_id = ? AND status = 'in_progress'
        """,
        (user_id,),
    )

    total_tests = int((completed_agg or {}).get("total_tests") or 0)
    avg_score_raw = (completed_agg or {}).get("avg_score")
    average_score = round(float(avg_score_raw), 2) if avg_score_raw is not None else 0.0

    total_correct = int((completed_agg or {}).get("total_correct") or 0)
    total_incorrect = int((completed_agg or {}).get("total_incorrect") or 0)
    total_evaluated = total_correct + total_incorrect
    overall_accuracy = (
        round((total_correct / total_evaluated) * 100.0, 2)
        if total_evaluated > 0
        else 0.0
    )

    unit_rows = query_all(
        """
        SELECT
            tr.syllabus_unit,
            COUNT(*) AS evaluated_count,
            SUM(CASE WHEN tr.is_correct = 1 THEN 1 ELSE 0 END) AS correct_count,
            SUM(CASE WHEN tr.is_correct = 0 THEN 1 ELSE 0 END) AS incorrect_count
        FROM test_responses tr
        JOIN mock_tests mt ON mt.id = tr.test_id
        WHERE tr.user_id = ?
          AND mt.user_id = ?
          AND mt.status = 'completed'
          AND tr.is_evaluated = 1
        GROUP BY tr.syllabus_unit
        """,
        (user_id, user_id),
    )

    weak_units: list[dict[str, Any]] = []
    for row in unit_rows:
        correct = int(row["correct_count"] or 0)
        incorrect = int(row["incorrect_count"] or 0)
        evaluated = correct + incorrect
        accuracy = round((correct / evaluated) * 100.0, 2) if evaluated > 0 else 0.0
        weak_units.append(
            {
                "syllabus_unit": row["syllabus_unit"],
                "evaluated": evaluated,
                "correct": correct,
                "incorrect": incorrect,
                "accuracy": accuracy,
            }
        )

    weak_units.sort(key=lambda u: (u["accuracy"], -u["incorrect"], u["syllabus_unit"]))

    recent_rows = query_all(
        """
        SELECT
            id, test_type, title, status, source_filter,
            instant_feedback, duration_minutes, total_questions,
            total_score, max_score, negative_loss,
            correct_count, incorrect_count, unanswered_count,
            started_at, submitted_at
        FROM mock_tests
        WHERE user_id = ?
        ORDER BY started_at DESC, id DESC
        LIMIT 20
        """,
        (user_id,),
    )

    recent_tests = []
    for r in recent_rows:
        c = int(r["correct_count"] or 0)
        inc = int(r["incorrect_count"] or 0)
        acc = round((c / (c + inc)) * 100.0, 2) if (c + inc) > 0 else 0.0
        recent_tests.append(
            {
                "id": r["id"],
                "test_type": r["test_type"],
                "title": r["title"],
                "status": r["status"],
                "source_filter": r["source_filter"],
                "instant_feedback": bool(r["instant_feedback"]),
                "duration_minutes": r["duration_minutes"],
                "total_questions": r["total_questions"],
                "total_score": r["total_score"],
                "max_score": r["max_score"],
                "negative_loss": r["negative_loss"],
                "correct_count": r["correct_count"],
                "incorrect_count": r["incorrect_count"],
                "unanswered_count": r["unanswered_count"],
                "accuracy": acc if r["status"] == "completed" else None,
                "started_at": r["started_at"],
                "submitted_at": r["submitted_at"],
            }
        )

    return {
        "total_tests": total_tests,
        "in_progress_tests": int((in_progress_row or {}).get("cnt") or 0),
        "average_score": average_score,
        "overall_accuracy": overall_accuracy,
        "total_correct": total_correct,
        "total_incorrect": total_incorrect,
        "weak_units": weak_units,
        "recent_tests": recent_tests,
    }


@router.post("/bookmarks/toggle")
def toggle_bookmark(
    payload: BookmarkToggleRequest,
    current_user: dict[str, Any] = Depends(get_current_user),
):
    user_id = int(current_user["id"])
    src, qid, via_response = _resolve_question_target(
        user_id,
        payload.question_source,
        payload.question_id,
        payload.response_id,
    )

    existing = query_one(
        """
        SELECT id FROM bookmarks
        WHERE user_id = ? AND question_source = ? AND question_id = ?
        """,
        (user_id, src, qid),
    )

    if existing:
        execute("DELETE FROM bookmarks WHERE id = ? AND user_id = ?", (existing["id"], user_id))
        bookmarked = False
    else:
        execute(
            """
            INSERT INTO bookmarks (user_id, question_source, question_id)
            VALUES (?, ?, ?)
            """,
            (user_id, src, qid),
        )
        bookmarked = True

    if via_response:
        return {"bookmarked": bookmarked, "response_id": payload.response_id}
    return {"bookmarked": bookmarked, "question_source": src, "question_id": qid}


@router.get("/bookmarks")
def list_bookmarks(current_user: dict[str, Any] = Depends(get_current_user)):
    user_id = int(current_user["id"])
    rows = query_all(
        """
        SELECT id, question_source, question_id, created_at
        FROM bookmarks
        WHERE user_id = ?
        ORDER BY created_at DESC, id DESC
        """,
        (user_id,),
    )
    if not rows:
        return {"bookmarks": []}

    # Hide answers for any bookmarked question currently in an unverified in_progress test
    active_rows = query_all(
        """
        SELECT tr.question_source, tr.question_id
        FROM test_responses tr
        JOIN mock_tests mt ON mt.id = tr.test_id
        WHERE tr.user_id = ?
          AND mt.user_id = ?
          AND mt.status = 'in_progress'
          AND tr.is_checked = 0
        """,
        (user_id, user_id),
    )
    active_hidden = {(r["question_source"], int(r["question_id"])) for r in active_rows}

    keys = [(r["question_source"], int(r["question_id"])) for r in rows]
    q_lookup = fetch_questions_by_keys(keys, include_answers=True)

    items: list[dict[str, Any]] = []
    for r in rows:
        src = r["question_source"]
        qid = int(r["question_id"])
        q = q_lookup.get((src, qid))
        if not q:
            continue
        entry: dict[str, Any] = {
            "bookmark_id": r["id"],
            "question_id": qid,
            "part": q["part"],
            "syllabus_unit": q["syllabus_unit"],
            "question_text": q["question_text"],
            "options": parse_options(q.get("options_json")),
            "created_at": r["created_at"],
        }
        if (src, qid) not in active_hidden:
            entry["question_source"] = src
            entry["exam_year"] = q.get("exam_year")
            entry["source_label"] = format_source_label(src, q.get("exam_year"))
            entry["correct_option"] = q.get("correct_option")
            entry["explanation"] = q.get("explanation")
        items.append(entry)

    return {"bookmarks": items}


@router.post("/flags", status_code=status.HTTP_201_CREATED)
def flag_question(
    payload: FlagQuestionRequest,
    current_user: dict[str, Any] = Depends(get_current_user),
):
    user_id = int(current_user["id"])
    src, qid, via_response = _resolve_question_target(
        user_id,
        payload.question_source,
        payload.question_id,
        payload.response_id,
    )

    flag_id = execute(
        """
        INSERT INTO question_flags (
            user_id, question_source, question_id, reason, details, resolved
        )
        VALUES (?, ?, ?, ?, ?, 0)
        """,
        (user_id, src, qid, payload.reason, (payload.details or "").strip() or None),
    )

    result: dict[str, Any] = {
        "flag_id": flag_id,
        "reason": payload.reason,
        "resolved": False,
    }
    if via_response:
        result["response_id"] = payload.response_id
    else:
        result["question_source"] = src
        result["question_id"] = qid
    return result
