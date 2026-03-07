"""
AgentIC Auth — Supabase JWT middleware + plan/build-count guard.

Env vars required:
    SUPABASE_URL          – e.g. https://xyz.supabase.co
    SUPABASE_SERVICE_KEY  – service-role key (server-side only, never expose)
    SUPABASE_JWT_SECRET   – JWT secret from Supabase dashboard → Settings → API
    ENCRYPTION_KEY        – symmetric key for encrypting BYOK API keys (32+ chars)
"""

import hashlib
import hmac
import json
import os
import time
from functools import lru_cache
from typing import Optional, Tuple

import httpx
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

# ─── Config ──────────────────────────────────────────────────────────
SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
SUPABASE_JWT_SECRET = os.environ.get("SUPABASE_JWT_SECRET", "")
ENCRYPTION_KEY = os.environ.get("ENCRYPTION_KEY", "change-me-in-production-32chars!")

AUTH_ENABLED = bool(SUPABASE_URL and SUPABASE_SERVICE_KEY and SUPABASE_JWT_SECRET)

# Plan limits: max successful builds allowed (None = unlimited)
PLAN_LIMITS = {
    "free": 2,
    "starter": 25,
    "pro": None,   # unlimited
    "byok": None,  # unlimited, uses own key
}

_bearer = HTTPBearer(auto_error=False)


# ─── JWT Decode (no pyjwt dependency — use Supabase /auth/v1/user) ──
def _decode_supabase_jwt(token: str) -> dict:
    """Validate JWT by calling Supabase auth endpoint.

    We call GET /auth/v1/user with the user's access_token.
    Supabase verifies the JWT signature and returns the user object.
    """
    resp = httpx.get(
        f"{SUPABASE_URL}/auth/v1/user",
        headers={
            "Authorization": f"Bearer {token}",
            "apikey": SUPABASE_SERVICE_KEY,
        },
        timeout=10,
    )
    if resp.status_code != 200:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    return resp.json()


# ─── Supabase DB helpers (use service-role key) ─────────────────────
def _supabase_rpc(fn_name: str, params: dict) -> dict:
    """Call a Supabase RPC function."""
    resp = httpx.post(
        f"{SUPABASE_URL}/rest/v1/rpc/{fn_name}",
        headers={
            "apikey": SUPABASE_SERVICE_KEY,
            "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
            "Content-Type": "application/json",
        },
        json=params,
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json() if resp.text else {}


def _supabase_query(table: str, select: str = "*", filters: str = "") -> list:
    """Simple REST query against Supabase PostgREST."""
    url = f"{SUPABASE_URL}/rest/v1/{table}?select={select}"
    if filters:
        url += f"&{filters}"
    resp = httpx.get(
        url,
        headers={
            "apikey": SUPABASE_SERVICE_KEY,
            "Authorization": f"Bearer {SUPABASE_SERVICE_KEY},",
        },
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()


def _supabase_insert(table: str, data: dict) -> dict:
    resp = httpx.post(
        f"{SUPABASE_URL}/rest/v1/{table}",
        headers={
            "apikey": SUPABASE_SERVICE_KEY,
            "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
            "Content-Type": "application/json",
            "Prefer": "return=representation",
        },
        json=data,
        timeout=10,
    )
    resp.raise_for_status()
    rows = resp.json()
    return rows[0] if rows else {}


def _supabase_update(table: str, filters: str, data: dict) -> dict:
    resp = httpx.patch(
        f"{SUPABASE_URL}/rest/v1/{table}?{filters}",
        headers={
            "apikey": SUPABASE_SERVICE_KEY,
            "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
            "Content-Type": "application/json",
            "Prefer": "return=representation",
        },
        json=data,
        timeout=10,
    )
    resp.raise_for_status()
    rows = resp.json()
    return rows[0] if rows else {}


# ─── BYOK Encryption ────────────────────────────────────────────────
def encrypt_api_key(plaintext: str) -> str:
    """XOR-based encryption with HMAC integrity check. Not AES-grade,
    but avoids requiring `cryptography` in Docker. Good enough for
    API keys at rest that are already scoped to a single user."""
    key_bytes = hashlib.sha256(ENCRYPTION_KEY.encode()).digest()
    ct = bytes(a ^ b for a, b in zip(plaintext.encode(), (key_bytes * ((len(plaintext) // 32) + 1))))
    mac = hmac.new(key_bytes, ct, hashlib.sha256).hexdigest()
    import base64
    return base64.urlsafe_b64encode(ct).decode() + "." + mac


def decrypt_api_key(ciphertext: str) -> str:
    import base64
    parts = ciphertext.split(".", 1)
    if len(parts) != 2:
        raise ValueError("Malformed encrypted key")
    ct = base64.urlsafe_b64decode(parts[0])
    mac = parts[1]
    key_bytes = hashlib.sha256(ENCRYPTION_KEY.encode()).digest()
    expected_mac = hmac.new(key_bytes, ct, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(mac, expected_mac):
        raise ValueError("Integrity check failed — key may have been tampered with")
    pt = bytes(a ^ b for a, b in zip(ct, (key_bytes * ((len(ct) // 32) + 1))))
    return pt.decode()


# ─── FastAPI Dependency: get current user ────────────────────────────
async def get_current_user(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
) -> Optional[dict]:
    """Extract and validate the Supabase JWT from the Authorization header.

    Returns the user profile dict or None if auth is disabled.
    When auth is enabled but no valid token is provided, raises 401.
    """
    if not AUTH_ENABLED:
        return None  # Auth not configured — allow anonymous access

    if not credentials:
        raise HTTPException(status_code=401, detail="Missing Authorization header")

    token = credentials.credentials
    user = _decode_supabase_jwt(token)
    uid = user.get("id")
    if not uid:
        raise HTTPException(status_code=401, detail="Invalid user")

    # Fetch profile from DB
    profiles = _supabase_query("profiles", filters=f"id=eq.{uid}")
    if not profiles:
        raise HTTPException(status_code=404, detail="Profile not found. Sign up first.")

    return profiles[0]


# ─── Build Guard: check plan + build count ───────────────────────────
def check_build_allowed(profile: Optional[dict]) -> None:
    """Raise 402 if the user has exhausted their plan's build quota.

    Called before every /build request when auth is enabled.
    """
    if profile is None:
        return  # Auth disabled — no restrictions

    plan = profile.get("plan", "free")
    builds = profile.get("successful_builds", 0)
    limit = PLAN_LIMITS.get(plan)

    if limit is not None and builds >= limit:
        raise HTTPException(
            status_code=402,
            detail={
                "error": "build_limit_reached",
                "plan": plan,
                "used": builds,
                "limit": limit,
                "message": f"You've used all {limit} builds on the {plan} plan. Upgrade to continue building chips.",
                "upgrade_url": "/pricing",
            },
        )


def get_llm_key_for_user(profile: Optional[dict]) -> Optional[str]:
    """Return the user's own LLM API key if they're on the BYOK plan.

    Returns None for all other plans (server uses global NVIDIA_API_KEY).
    """
    if profile is None:
        return None

    if profile.get("plan") != "byok":
        return None

    encrypted_key = profile.get("llm_api_key")
    if not encrypted_key:
        raise HTTPException(
            status_code=400,
            detail="BYOK plan requires an API key. Set it in your profile settings.",
        )

    try:
        return decrypt_api_key(encrypted_key)
    except ValueError:
        raise HTTPException(status_code=500, detail="Failed to decrypt stored API key")


def record_build_start(profile: Optional[dict], job_id: str, design_name: str) -> None:
    """Insert a build record into the builds table."""
    if profile is None or not AUTH_ENABLED:
        return
    _supabase_insert("builds", {
        "user_id": profile["id"],
        "job_id": job_id,
        "design_name": design_name,
        "status": "queued",
    })


def record_build_success(profile: Optional[dict], job_id: str) -> None:
    """Mark build as done and increment the user's successful_builds count."""
    if profile is None or not AUTH_ENABLED:
        return
    uid = profile["id"]
    # Update build row
    _supabase_update("builds", f"job_id=eq.{job_id}", {
        "status": "done",
        "finished_at": "now()",
    })
    # Increment counter
    _supabase_rpc("increment_successful_builds", {"uid": uid})


def record_build_failure(job_id: str) -> None:
    """Mark build as failed."""
    if not AUTH_ENABLED:
        return
    _supabase_update("builds", f"job_id=eq.{job_id}", {
        "status": "failed",
        "finished_at": "now()",
    })
