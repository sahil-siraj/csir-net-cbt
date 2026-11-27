from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.auth import (
    create_access_token,
    get_current_user,
    hash_password,
    sanitize_user,
    verify_password,
)
from app.config import settings
from app.db import get_connection, query_one, utc_now_iso

router = APIRouter(prefix="/api/auth", tags=["auth"])

INVALID_INVITE_MSG = "Invalid, already used, or expired invite code"


class RegisterRequest(BaseModel):
    username: str = Field(..., min_length=2, max_length=64)
    display_name: str = Field(..., min_length=1, max_length=120)
    password: str = Field(..., min_length=6, max_length=128)
    invite_code: str = Field(..., min_length=1)


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1)
    password: str = Field(..., min_length=1)


@router.post("/register", status_code=status.HTTP_201_CREATED)
def register(payload: RegisterRequest):
    code = payload.invite_code.strip().upper()
    username = payload.username.strip()
    display_name = payload.display_name.strip()

    if len(username) < 2 or not display_name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Valid username and display_name are required",
        )
    if not code:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=INVALID_INVITE_MSG,
        )

    now_iso = utc_now_iso()
    password_hash = hash_password(payload.password)

    with get_connection() as conn:
        try:
            if not settings.use_turso:
                conn.execute("BEGIN IMMEDIATE")

            invite_row = conn.execute(
                """
                SELECT id, is_used, expires_at
                FROM invite_codes
                WHERE code = ?
                """,
                (code,),
            ).fetchone()

            if not invite_row:
                conn.rollback()
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=INVALID_INVITE_MSG,
                )

            _, is_used, expires_at = invite_row
            if int(is_used) != 0 or (expires_at is not None and expires_at <= now_iso):
                conn.rollback()
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=INVALID_INVITE_MSG,
                )

            existing = conn.execute(
                "SELECT id FROM users WHERE LOWER(username) = LOWER(?)",
                (username,),
            ).fetchone()
            if existing:
                conn.rollback()
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Username is already taken",
                )

            count_row = conn.execute("SELECT COUNT(*) FROM users").fetchone()
            is_first_user = (count_row[0] if count_row else 0) == 0
            role = "admin" if is_first_user else "student"

            insert_cur = conn.execute(
                """
                INSERT INTO users (username, display_name, password_hash, role)
                VALUES (?, ?, ?, ?)
                """,
                (username, display_name, password_hash, role),
            )
            user_id = int(insert_cur.lastrowid or 0)

            claim_cur = conn.execute(
                """
                UPDATE invite_codes
                SET is_used = 1, used_by_user_id = ?, used_at = ?
                WHERE code = ?
                  AND is_used = 0
                  AND (expires_at IS NULL OR expires_at > ?)
                """,
                (user_id, now_iso, code, now_iso),
            )
            if claim_cur.rowcount != 1:
                conn.rollback()
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=INVALID_INVITE_MSG,
                )

            conn.commit()
        except HTTPException:
            raise
        except Exception as exc:
            try:
                conn.rollback()
            except Exception:
                pass
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Registration failed",
            ) from exc

    user = query_one(
        "SELECT id, username, display_name, role, created_at FROM users WHERE id = ?",
        (user_id,),
    )
    if not user:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve newly created user",
        )

    token = create_access_token(user)
    return {
        "access_token": token,
        "token_type": "bearer",
        "user": sanitize_user(user),
    }


@router.post("/login")
def login(payload: LoginRequest):
    username = payload.username.strip()
    user = query_one(
        """
        SELECT id, username, display_name, password_hash, role, created_at
        FROM users
        WHERE LOWER(username) = LOWER(?)
        """,
        (username,),
    )
    if not user or not verify_password(payload.password, user["password_hash"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
        )

    safe_user = sanitize_user(user)
    token = create_access_token(safe_user)
    return {
        "access_token": token,
        "token_type": "bearer",
        "user": safe_user,
    }


@router.get("/me")
def get_me(current_user: dict = Depends(get_current_user)):
    return {"user": current_user}
