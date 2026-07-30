"""
Auth routes — Phase 8.

POST /auth/login   — verify email+password, set httpOnly JWT cookie
POST /auth/logout  — clear cookie
GET  /auth/me      — return current user from cookie

JWT is stored in an httpOnly cookie named "edi_token".
Token expiry: 8 hours. Secret: settings.secret_key.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import bcrypt
import structlog
from fastapi import APIRouter, Depends, HTTPException, Response, status
from jose import JWTError, jwt

from app.api.deps import get_sync_db
from app.schemas.api import LoginRequest, LoginResponse, UserResponse

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/auth", tags=["Auth"])

_COOKIE_NAME = "edi_token"
_TOKEN_EXPIRE_HOURS = 8
_ALGORITHM = "HS256"


def _get_secret() -> str:
    from app.config import get_settings
    return get_settings().secret_key


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode(), hashed.encode())


def create_access_token(email: str) -> str:
    expire = datetime.now(UTC) + timedelta(hours=_TOKEN_EXPIRE_HOURS)
    return jwt.encode(
        {"sub": email, "exp": expire},
        _get_secret(),
        algorithm=_ALGORITHM,
    )


def get_current_user_email(request: object) -> str:
    """
    Extract and verify the JWT. Raises 401 on failure.

    Two transports, same token:
      - Authorization: Bearer <token>  — server-to-server callers (e.g. the SAP push).
      - edi_token cookie               — the browser SPA.
    The header wins when both are present.
    """
    req: Request = request  # type: ignore[assignment]

    token: str | None = None
    auth_header = req.headers.get("authorization")
    if auth_header and auth_header.lower().startswith("bearer "):
        token = auth_header[7:].strip()
    if not token:
        token = req.cookies.get(_COOKIE_NAME)

    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    try:
        payload = jwt.decode(token, _get_secret(), algorithms=[_ALGORITHM])
        email: str | None = payload.get("sub")
        if not email:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
        return email
    except JWTError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token") from exc


# ── Dependency ────────────────────────────────────────────────────────────────

from fastapi import Request  # noqa: E402


def get_current_user(
    request: Request,
    db: Session = Depends(get_sync_db),
) -> UserResponse:
    from sqlalchemy import select

    from app.models.users import User

    email = get_current_user_email(request)
    user = db.execute(select(User).where(User.email == email, User.is_active.is_(True))).scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found or inactive")
    return UserResponse.model_validate(user)


# ── Routes ────────────────────────────────────────────────────────────────────

@router.post("/login")
def login(
    body: LoginRequest,
    response: Response,
    db: Session = Depends(get_sync_db),
) -> LoginResponse:
    from sqlalchemy import select

    from app.models.users import User

    user = db.execute(
        select(User).where(User.email == body.email, User.is_active.is_(True))
    ).scalar_one_or_none()

    if not user or not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    token = create_access_token(user.email)
    response.set_cookie(
        key=_COOKIE_NAME,
        value=token,
        httponly=True,
        samesite="lax",
        max_age=_TOKEN_EXPIRE_HOURS * 3600,
    )
    log.info("auth.login", email=user.email)
    return LoginResponse(
        user=UserResponse.model_validate(user),
        access_token=token,
        token_type="bearer",
        expires_in=_TOKEN_EXPIRE_HOURS * 3600,
    )


@router.post("/logout")
def logout(response: Response) -> dict[str, str]:
    response.delete_cookie(_COOKIE_NAME)
    return {"status": "logged out"}


@router.get("/me")
def me(current_user: UserResponse = Depends(get_current_user)) -> UserResponse:
    return current_user
