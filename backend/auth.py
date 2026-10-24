"""DJ Bot Botty viewer profiles. Email signup now; social OAuth when keys exist."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import secrets
import sqlite3
import time
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Cookie, Depends, Header, HTTPException, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, Field

def _users_db_path() -> Path:
    raw = os.getenv("DATA_DIR")
    if raw:
        p = Path(raw)
        p.mkdir(parents=True, exist_ok=True)
        return p / "users.db"
    return Path(__file__).resolve().parent / "users.db"


DB_PATH = _users_db_path()
COOKIE = "djbb_session"
router = APIRouter(prefix="/auth", tags=["auth"])
logger = logging.getLogger(__name__)

PROVIDERS = ("google", "apple", "facebook", "tiktok")
STATE_TTL_SEC = 600


def _state_secret() -> bytes:
    raw = (
        os.getenv("OAUTH_STATE_SECRET")
        or os.getenv("SESSION_SECRET")
        or os.getenv("OPERATOR_KEY")
        or "djbb-dev-oauth-state"
    )
    return raw.encode("utf-8")


def sign_oauth_state(provider: str, *, nonce: Optional[str] = None, ts: Optional[int] = None) -> str:
    """HMAC-signed CSRF state: provider.ts.nonce.sig"""
    nonce = nonce or secrets.token_urlsafe(12)
    ts = int(ts if ts is not None else time.time())
    payload = f"{provider}.{ts}.{nonce}"
    sig = hmac.new(_state_secret(), payload.encode(), hashlib.sha256).hexdigest()[:32]
    return f"{payload}.{sig}"


def verify_oauth_state(state: str, provider: str, *, max_age: int = STATE_TTL_SEC) -> bool:
    if not state or state.count(".") < 3:
        return False
    provider_part, ts_s, nonce, sig = state.split(".", 3)
    if provider_part != provider:
        return False
    try:
        ts = int(ts_s)
    except ValueError:
        return False
    if abs(time.time() - ts) > max_age:
        return False
    if not nonce:
        return False
    payload = f"{provider_part}.{ts_s}.{nonce}"
    expect = hmac.new(_state_secret(), payload.encode(), hashlib.sha256).hexdigest()[:32]
    return hmac.compare_digest(expect, sig)


def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE,
            display_name TEXT NOT NULL,
            password_hash TEXT,
            provider TEXT,
            provider_id TEXT,
            perks TEXT NOT NULL DEFAULT '{}',
            created_at INTEGER NOT NULL
        )
        """
    )
    cols = {r[1] for r in conn.execute("PRAGMA table_info(users)").fetchall()}
    if "email_opt_in" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN email_opt_in INTEGER NOT NULL DEFAULT 0")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS sessions (
            token TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            created_at INTEGER NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
        """
    )
    conn.commit()
    return conn


def _hash_pw(password: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 120_000)
    return f"{salt}${dk.hex()}"


def _check_pw(password: str, stored: str) -> bool:
    try:
        salt, hexed = stored.split("$", 1)
    except ValueError:
        return False
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 120_000)
    return hmac.compare_digest(dk.hex(), hexed)


def _public(row: sqlite3.Row) -> dict[str, Any]:
    perks = json.loads(row["perks"] or "{}")
    keys = row.keys()
    opt_in = bool(row["email_opt_in"]) if "email_opt_in" in keys else False
    return {
        "id": row["id"],
        "email": row["email"],
        "display_name": row["display_name"],
        "provider": row["provider"] or "email",
        "perks": perks,
        "email_opt_in": opt_in,
        "product": "DJ Bot Botty",
        "buddy": "Halo",
    }


def set_email_opt_in(user_id: int, opt_in: bool) -> Optional[dict[str, Any]]:
    """Store going-live email opt-in only — no sending in B8."""
    conn = _db()
    conn.execute(
        "UPDATE users SET email_opt_in = ? WHERE id = ?",
        (1 if opt_in else 0, int(user_id)),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM users WHERE id = ?", (int(user_id),)).fetchone()
    conn.close()
    return _public(row) if row else None


def _default_perks() -> dict:
    return {
        "stream_badge": True,
        "room_pass": True,
        "request_priority": False,
        "early_chat": True,
        "room_mod": False,
        "notes": "Live-stream perks expand after your first show.",
    }


def _set_cookie(response: Response, token: str) -> None:
    from ops import cookie_secure

    response.set_cookie(
        COOKIE,
        token,
        httponly=True,
        samesite="lax",
        max_age=60 * 60 * 24 * 30,
        secure=cookie_secure(),
    )


def user_from_cookie(token: Optional[str]) -> Optional[dict[str, Any]]:
    if not token:
        return None
    conn = _db()
    row = conn.execute(
        "SELECT u.* FROM users u JOIN sessions s ON s.user_id = u.id WHERE s.token = ?",
        (token,),
    ).fetchone()
    conn.close()
    return _public(row) if row else None


def user_by_id(user_id: Any) -> Optional[dict[str, Any]]:
    """Lookup public user dict by booth-pass profile id (for chat-path perks)."""
    try:
        uid = int(user_id)
    except (TypeError, ValueError):
        return None
    conn = _db()
    row = conn.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
    conn.close()
    return _public(row) if row else None


def _client_ip(request: Request) -> str:
    """Client IP. X-Forwarded-For is trusted only behind Railway / TRUSTED_PROXY=1."""
    from ops import trusted_proxy

    if trusted_proxy():
        forwarded = request.headers.get("x-forwarded-for", "")
        if forwarded:
            return forwarded.split(",")[0].strip()
    if request.client:
        return request.client.host or ""
    return ""


def require_operator(
    request: Request,
    x_operator_key: Optional[str] = Header(default=None, alias="X-Operator-Key"),
    djbb_session: Optional[str] = Cookie(default=None, alias=COOKIE),
) -> None:
    """Allow booth control when operator key, operator perk, or local unsigned booth."""
    expected = (os.getenv("OPERATOR_KEY") or "").strip()
    if expected and x_operator_key and secrets.compare_digest(x_operator_key, expected):
        return

    user = user_from_cookie(djbb_session)
    if user and bool((user.get("perks") or {}).get("operator")):
        return

    if not expected and _client_ip(request) in {"127.0.0.1", "::1", "testclient"}:
        return

    raise HTTPException(status_code=401, detail="operator auth required")


def current_mod_actor(
    request: Request,
    x_operator_key: Optional[str] = Header(default=None, alias="X-Operator-Key"),
    djbb_session: Optional[str] = Cookie(default=None, alias=COOKIE),
) -> dict[str, Any]:
    """Return actor dict if operator or room_mod; else raise 401/403."""
    expected = (os.getenv("OPERATOR_KEY") or "").strip()
    if expected and x_operator_key and secrets.compare_digest(x_operator_key, expected):
        return {
            "user_id": None,
            "display_name": "operator-key",
            "perks": {"operator": True, "room_mod": True},
            "via": "operator_key",
        }

    user = user_from_cookie(djbb_session)
    if user:
        perks = user.get("perks") or {}
        if bool(perks.get("operator")) or bool(perks.get("room_mod")):
            return {
                "user_id": user.get("id"),
                "display_name": user.get("display_name") or user.get("email") or "mod",
                "perks": perks,
                "via": "session",
            }
        raise HTTPException(status_code=403, detail="room mod required")

    if not expected and _client_ip(request) in {"127.0.0.1", "::1", "testclient"}:
        return {
            "user_id": None,
            "display_name": "local-operator",
            "perks": {"operator": True, "room_mod": True},
            "via": "local",
        }

    raise HTTPException(status_code=401, detail="mod auth required")


def require_room_mod(actor: dict[str, Any] = Depends(current_mod_actor)) -> dict[str, Any]:
    """FastAPI dependency — room_mod or operator."""
    return actor


class SignupIn(BaseModel):
    display_name: str = Field(min_length=2, max_length=40)
    email: str = Field(min_length=5, max_length=120)
    password: str = Field(min_length=8, max_length=128)


class LoginIn(BaseModel):
    email: str
    password: str


def _issue(user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    conn = _db()
    conn.execute(
        "INSERT INTO sessions(token, user_id, created_at) VALUES (?,?,?)",
        (token, user_id, int(time.time())),
    )
    conn.commit()
    conn.close()
    return token


@router.get("/providers")
def providers():
    ready = {
        "google": bool(os.getenv("GOOGLE_OAUTH_CLIENT_ID")),
        "apple": bool(os.getenv("APPLE_OAUTH_CLIENT_ID")),
        "facebook": bool(os.getenv("FACEBOOK_OAUTH_CLIENT_ID")),
        "tiktok": bool(os.getenv("TIKTOK_OAUTH_CLIENT_KEY")),
    }
    return {"providers": ready, "product": "DJ Bot Botty"}


@router.get("/me")
def me(djbb_session: Optional[str] = Cookie(default=None, alias=COOKIE)):
    user = user_from_cookie(djbb_session)
    if not user:
        return JSONResponse({"user": None}, status_code=200)
    return {"user": user}


@router.post("/signup")
def signup(body: SignupIn, response: Response):
    conn = _db()
    existing = conn.execute("SELECT id FROM users WHERE email = ?", (body.email.lower(),)).fetchone()
    if existing:
        conn.close()
        raise HTTPException(409, "That email already has a DJ Bot Botty pass.")
    conn.execute(
        "INSERT INTO users(email, display_name, password_hash, provider, perks, created_at) VALUES (?,?,?,?,?,?)",
        (
            body.email.lower(),
            body.display_name.strip(),
            _hash_pw(body.password),
            "email",
            json.dumps(_default_perks()),
            int(time.time()),
        ),
    )
    conn.commit()
    user_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    conn.close()
    token = _issue(user_id)
    _set_cookie(response, token)
    return {"user": _public(row), "created": True}


@router.post("/login")
def login(body: LoginIn, response: Response):
    conn = _db()
    row = conn.execute("SELECT * FROM users WHERE email = ?", (body.email.lower(),)).fetchone()
    conn.close()
    if not row or not row["password_hash"] or not _check_pw(body.password, row["password_hash"]):
        raise HTTPException(401, "Email or password did not match.")
    token = _issue(row["id"])
    _set_cookie(response, token)
    return {"user": _public(row), "created": False}


@router.post("/logout")
def logout(response: Response, djbb_session: Optional[str] = Cookie(default=None, alias=COOKIE)):
    if djbb_session:
        conn = _db()
        conn.execute("DELETE FROM sessions WHERE token = ?", (djbb_session,))
        conn.commit()
        conn.close()
    response.delete_cookie(COOKIE)
    return {"ok": True}


def _upsert_oauth_user(
    *,
    provider: str,
    provider_id: str,
    email: Optional[str],
    display_name: str,
) -> int:
    provider_id = (provider_id or "").strip()
    if not provider_id:
        raise HTTPException(400, "OAuth provider did not return a user id")
    display_name = (display_name or "Fan").strip()[:40] or "Fan"
    email_norm = (email or "").strip().lower() or None
    conn = _db()
    row = conn.execute(
        "SELECT * FROM users WHERE provider = ? AND provider_id = ?",
        (provider, provider_id),
    ).fetchone()
    if row:
        conn.execute(
            "UPDATE users SET display_name = ?, email = COALESCE(?, email) WHERE id = ?",
            (display_name, email_norm, row["id"]),
        )
        conn.commit()
        user_id = int(row["id"])
        conn.close()
        return user_id
    if email_norm:
        by_email = conn.execute(
            "SELECT * FROM users WHERE email = ?",
            (email_norm,),
        ).fetchone()
        if by_email:
            conn.execute(
                """
                UPDATE users SET provider = ?, provider_id = ?, display_name = ?
                WHERE id = ?
                """,
                (provider, provider_id, display_name, by_email["id"]),
            )
            conn.commit()
            user_id = int(by_email["id"])
            conn.close()
            return user_id
    conn.execute(
        """
        INSERT INTO users(email, display_name, password_hash, provider, provider_id, perks, created_at)
        VALUES (?,?,NULL,?,?,?,?)
        """,
        (
            email_norm,
            display_name,
            provider,
            provider_id,
            json.dumps(_default_perks()),
            int(time.time()),
        ),
    )
    conn.commit()
    user_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
    conn.close()
    return user_id


def _finish_oauth_login(user_id: int) -> RedirectResponse:
    token = _issue(user_id)
    redirect = RedirectResponse("/room", status_code=302)
    _set_cookie(redirect, token)
    return redirect


def _exchange_google(code: str, redirect_uri: str) -> dict[str, Any]:
    client_id = os.getenv("GOOGLE_OAUTH_CLIENT_ID") or ""
    client_secret = os.getenv("GOOGLE_OAUTH_CLIENT_SECRET") or ""
    with httpx.Client(timeout=20.0) as client:
        token_res = client.post(
            "https://oauth2.googleapis.com/token",
            data={
                "code": code,
                "client_id": client_id,
                "client_secret": client_secret,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
            },
        )
        token_res.raise_for_status()
        tokens = token_res.json()
        access = tokens.get("access_token")
        if not access:
            raise HTTPException(400, "Google token exchange failed")
        info_res = client.get(
            "https://www.googleapis.com/oauth2/v3/userinfo",
            headers={"Authorization": f"Bearer {access}"},
        )
        info_res.raise_for_status()
        return info_res.json()


def _exchange_facebook(code: str, redirect_uri: str) -> dict[str, Any]:
    client_id = os.getenv("FACEBOOK_OAUTH_CLIENT_ID") or ""
    client_secret = os.getenv("FACEBOOK_OAUTH_CLIENT_SECRET") or ""
    with httpx.Client(timeout=20.0) as client:
        token_res = client.get(
            "https://graph.facebook.com/v19.0/oauth/access_token",
            params={
                "client_id": client_id,
                "client_secret": client_secret,
                "redirect_uri": redirect_uri,
                "code": code,
            },
        )
        token_res.raise_for_status()
        tokens = token_res.json()
        access = tokens.get("access_token")
        if not access:
            raise HTTPException(400, "Facebook token exchange failed")
        info_res = client.get(
            "https://graph.facebook.com/me",
            params={"fields": "id,name,email", "access_token": access},
        )
        info_res.raise_for_status()
        return info_res.json()


@router.get("/{provider}/start")
def oauth_start(provider: str, request: Request):
    if provider not in PROVIDERS:
        raise HTTPException(404, "Unknown provider")
    origin = str(request.base_url).rstrip("/")
    callback = f"{origin}/auth/{provider}/callback"
    state = sign_oauth_state(provider)
    if provider == "google" and os.getenv("GOOGLE_OAUTH_CLIENT_ID"):
        qs = urlencode(
            {
                "client_id": os.getenv("GOOGLE_OAUTH_CLIENT_ID"),
                "redirect_uri": callback,
                "response_type": "code",
                "scope": "openid email profile",
                "access_type": "online",
                "state": state,
            }
        )
        return RedirectResponse(f"https://accounts.google.com/o/oauth2/v2/auth?{qs}")
    if provider == "facebook" and os.getenv("FACEBOOK_OAUTH_CLIENT_ID"):
        qs = urlencode(
            {
                "client_id": os.getenv("FACEBOOK_OAUTH_CLIENT_ID"),
                "redirect_uri": callback,
                "response_type": "code",
                "scope": "email,public_profile",
                "state": state,
            }
        )
        return RedirectResponse(f"https://www.facebook.com/v19.0/dialog/oauth?{qs}")
    if provider == "apple" and os.getenv("APPLE_OAUTH_CLIENT_ID"):
        qs = urlencode(
            {
                "client_id": os.getenv("APPLE_OAUTH_CLIENT_ID"),
                "redirect_uri": callback,
                "response_type": "code",
                "scope": "name email",
                "response_mode": "form_post",
                "state": state,
            }
        )
        return RedirectResponse(f"https://appleid.apple.com/auth/authorize?{qs}")
    if provider == "tiktok" and os.getenv("TIKTOK_OAUTH_CLIENT_KEY"):
        qs = urlencode(
            {
                "client_key": os.getenv("TIKTOK_OAUTH_CLIENT_KEY"),
                "redirect_uri": callback,
                "response_type": "code",
                "scope": "user.info.basic",
                "state": state,
            }
        )
        return RedirectResponse(f"https://www.tiktok.com/v2/auth/authorize/?{qs}")
    raise HTTPException(
        501,
        f"{provider.title()} login is wired. Set {provider.upper()}_OAUTH_CLIENT_ID (TikTok: TIKTOK_OAUTH_CLIENT_KEY) to go live.",
    )


@router.get("/{provider}/callback")
def oauth_callback(
    provider: str,
    request: Request,
    code: Optional[str] = None,
    state: Optional[str] = None,
    error: Optional[str] = None,
):
    if provider not in PROVIDERS:
        raise HTTPException(404, "Unknown provider")
    if provider in ("apple", "tiktok"):
        raise HTTPException(
            501,
            f"{provider.title()} OAuth callback is not implemented yet. Use email signup or Google/Facebook.",
        )
    if error:
        raise HTTPException(400, f"OAuth error: {error}")
    if not code:
        raise HTTPException(400, "Missing OAuth code")
    if not state or not verify_oauth_state(state, provider):
        raise HTTPException(400, "Invalid or expired OAuth state")

    origin = str(request.base_url).rstrip("/")
    redirect_uri = f"{origin}/auth/{provider}/callback"
    try:
        if provider == "google":
            if not os.getenv("GOOGLE_OAUTH_CLIENT_ID"):
                raise HTTPException(501, "Google OAuth client is not configured")
            info = _exchange_google(code, redirect_uri)
            provider_id = str(info.get("sub") or "")
            email = info.get("email")
            display_name = str(info.get("name") or info.get("given_name") or email or "Fan")
        elif provider == "facebook":
            if not os.getenv("FACEBOOK_OAUTH_CLIENT_ID"):
                raise HTTPException(501, "Facebook OAuth client is not configured")
            info = _exchange_facebook(code, redirect_uri)
            provider_id = str(info.get("id") or "")
            email = info.get("email")
            display_name = str(info.get("name") or email or "Fan")
        else:
            raise HTTPException(501, f"{provider} callback not implemented")
    except HTTPException:
        raise
    except httpx.HTTPError as exc:
        logger.exception("oauth token exchange failed provider=%s", provider)
        raise HTTPException(502, f"OAuth provider error: {exc}") from exc

    user_id = _upsert_oauth_user(
        provider=provider,
        provider_id=provider_id,
        email=email if isinstance(email, str) else None,
        display_name=display_name,
    )
    return _finish_oauth_login(user_id)
