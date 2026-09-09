import secrets
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select, delete
from app.db.session import session
from app.models.entities import User, LoginSession, APIToken
from app.core.security import passwords, token_hash
from app.core.settings import settings

router = APIRouter(prefix="/auth", tags=["Authentication"])


def origin_allowed(origin: str) -> bool:
    from urllib.parse import urlsplit

    def normalized(value):
        try:
            parsed = urlsplit(value)
            port = parsed.port
        except ValueError:
            return None
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            return None
        return (
            parsed.scheme,
            parsed.hostname.lower(),
            port or (443 if parsed.scheme == "https" else 80),
        )

    candidate = normalized(origin)
    if not candidate:
        return False
    configured = [settings.supervisor_public_url, *settings.allowed_origins]
    if candidate in [normalized(value) for value in configured]:
        return True
    # Local development aliases only, with the same configured scheme and port.
    public = normalized(settings.supervisor_public_url)
    loopback = {"localhost", "127.0.0.1", "::1"}
    return bool(
        settings.environment == "development"
        and public
        and candidate[1] in loopback
        and public[1] in loopback
        and candidate[0] == public[0]
        and candidate[2] == public[2]
    )


class Login(BaseModel):
    username: str
    password: str


class PasswordChange(BaseModel):
    old_password: str
    new_password: str = Field(min_length=12, max_length=128)


async def current_user(request: Request, db=Depends(session)):
    authorization = request.headers.get("Authorization", "")
    if authorization.startswith("Bearer "):
        token = await db.scalar(
            select(APIToken).where(
                APIToken.token_hash == token_hash(authorization[7:]),
                APIToken.revoked.is_(False),
                APIToken.expires_at > datetime.now(timezone.utc),
            )
        )
        if not token:
            raise HTTPException(401, "Invalid or expired API token")
        user = await db.get(User, token.user_id)
        if not user or not user.active or user.must_change_password:
            raise HTTPException(403, "Account is inactive or requires a password change")
        if token.customer_id:
            allowed = request.url.path == f"/api/v1/customers/{token.customer_id}/usage"
            if request.method != "GET" or not allowed:
                raise HTTPException(403, "This token is restricted to its customer usage API")
        request.state.user = user
        request.state.api_token = token
        return user
    token = request.cookies.get("cdn_session", "")
    row = await db.scalar(
        select(LoginSession).where(
            LoginSession.token_hash == token_hash(token), LoginSession.expires_at > datetime.now(timezone.utc)
        )
    )
    if not row:
        raise HTTPException(401, "Authentication required")
    user = await db.get(User, row.user_id)
    if not user or not user.active:
        raise HTTPException(401, "Account inactive")
    if request.method not in {"GET", "HEAD", "OPTIONS"} and not secrets.compare_digest(
        request.headers.get("X-CSRF-Token", ""), row.csrf
    ):
        raise HTTPException(403, "CSRF check failed")
    if user.must_change_password and request.url.path not in {
        "/api/v1/auth/me",
        "/api/v1/auth/password",
        "/api/v1/auth/logout",
    }:
        raise HTTPException(403, "BOOTSTRAP_PASSWORD_CHANGE_REQUIRED")
    request.state.user = user
    return user


def role(*roles):
    async def check(user=Depends(current_user)):
        if user.role not in roles:
            raise HTTPException(403, "Insufficient role")
        return user

    return check


@router.post("/login")
async def login(body: Login, request: Request, response: Response, db=Depends(session)):
    if request.headers.get("origin") and not origin_allowed(request.headers["origin"]):
        raise HTTPException(
            403, "Origin rejected: add this browser origin to ALLOWED_ORIGINS or use SUPERVISOR_PUBLIC_URL"
        )
    user = await db.scalar(select(User).where(User.username == body.username))
    try:
        if not user or not user.active:
            raise ValueError()
        passwords.verify(user.password_hash, body.password)
    except Exception:
        raise HTTPException(401, "Invalid credentials") from None
    token = secrets.token_urlsafe(48)
    csrf = secrets.token_urlsafe(32)
    db.add(
        LoginSession(
            user_id=user.id,
            token_hash=token_hash(token),
            csrf=csrf,
            expires_at=datetime.now(timezone.utc) + timedelta(hours=12),
        )
    )
    response.set_cookie(
        "cdn_session",
        token,
        httponly=True,
        secure=settings.environment != "development",
        samesite="strict",
        max_age=43200,
        path="/",
    )
    return {
        "csrf": csrf,
        "id": str(user.id),
        "preferences": user.preferences,
        "username": user.username,
        "role": user.role,
        "must_change_password": user.must_change_password,
    }


@router.get("/me")
async def me(request: Request, user=Depends(current_user), db=Depends(session)):
    row = (
        None
        if getattr(request.state, "api_token", None)
        else await db.scalar(
            select(LoginSession).where(LoginSession.token_hash == token_hash(request.cookies["cdn_session"]))
        )
    )
    return {
        "username": user.username,
        "role": user.role,
        "csrf": row.csrf if row else None,
        "id": str(user.id),
        "preferences": user.preferences,
        "must_change_password": user.must_change_password,
    }


@router.post("/password")
async def change(body: PasswordChange, user=Depends(current_user), db=Depends(session)):
    try:
        passwords.verify(user.password_hash, body.old_password)
    except Exception:
        raise HTTPException(400, "Incorrect password") from None
    user.password_hash = passwords.hash(body.new_password)
    user.must_change_password = False
    await db.execute(delete(LoginSession).where(LoginSession.user_id == user.id))
    return {"success": True, "login_required": True}


@router.post("/logout")
async def logout(request: Request, response: Response, user=Depends(current_user), db=Depends(session)):
    await db.execute(
        delete(LoginSession).where(LoginSession.token_hash == token_hash(request.cookies["cdn_session"]))
    )
    response.delete_cookie("cdn_session")
    return {"success": True}
