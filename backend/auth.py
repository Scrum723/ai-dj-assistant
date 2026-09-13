"""DJ Bot Botty viewer profiles. Email signup now; social OAuth when keys exist."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import time
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlencode

from fastapi import APIRouter, Cookie, HTTPException, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, Field

DB_PATH = Path(__file__).resolve().parent / "users.db"
COOKIE = "djbb_session"
router = APIRouter(prefix="/auth", tags=["auth"])

PROVIDERS = ("google", "apple", "facebook", "tiktok")


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
    return {
        "id": row["id"],
        "email": row["email"],
        "display_name": row["display_name"],
        "provider": row["provider"] or "email",
        "perks": perks,
        "product": "DJ Bot Botty",
        "buddy": "Halo",
    }


def _default_perks() -> dict:
    return {
        "stream_badge": True,
        "room_pass": True,
        "request_priority": False,
        "early_chat": True,
        "notes": "Live-stream perks expand after your first show.",
    }


def _set_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        COOKIE,
        token,
        httponly=True,
        samesite="lax",
        max_age=60 * 60 * 24 * 30,
        secure=os.getenv("COOKIE_SECURE", "").lower() in {"1", "true"},
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


@router.get("/{provider}/start")
def oauth_start(provider: str, request: Request):
    if provider not in PROVIDERS:
        raise HTTPException(404, "Unknown provider")
    origin = str(request.base_url).rstrip("/")
    callback = f"{origin}/auth/{provider}/callback"
    if provider == "google" and os.getenv("GOOGLE_OAUTH_CLIENT_ID"):
        qs = urlencode(
            {
                "client_id": os.getenv("GOOGLE_OAUTH_CLIENT_ID"),
                "redirect_uri": callback,
                "response_type": "code",
                "scope": "openid email profile",
                "access_type": "online",
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
            }
        )
        return RedirectResponse(f"https://www.tiktok.com/v2/auth/authorize/?{qs}")
    raise HTTPException(
        501,
        f"{provider.title()} login is wired. Set {provider.upper()}_OAUTH_CLIENT_ID (TikTok: TIKTOK_OAUTH_CLIENT_KEY) to go live.",
    )
