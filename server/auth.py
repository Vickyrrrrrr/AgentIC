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
from typing import Optional

import httpx
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

# ─── Config ──────────────────────────────────────────────────────────
SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
SUPABASE_JWT_SECRET = os.environ.get("SUPABASE_JWT_SECRET", "")
ENCRYPTION_KEY = os.environ.get("ENCRYPTION_KEY", "")

AUTH_ENABLED = bool(SUPABASE_URL and SUPABASE_SERVICE_KEY and SUPABASE_JWT_SECRET)

# Plan limits: max successful builds allowed (None = unlimited)
PLAN_LIMITS = {
    "free": 2,
    "starter": 25,
    "pro": None,
    "byok": None,
}

_bearer = HTTPBearer(auto_error=False)

# ─── Shared async HTTP client (reused across requests — much faster) ─
# A single persistent client with connection pooling instead of
# creating a new connection on every request.
_async_client: Optional[httpx.AsyncClient] = None

def _get_async_client() -> httpx.AsyncClient:
    global _async_client
    if _async_client is None or _async_client.is_closed:
        _async_client = httpx.AsyncClient(
            timeout=10,
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        )
    return _async_client


# ─── JWT Decode — fully async, non-blocking ──────────────────────────
async def _decode_supabase_jwt(token: str) -> dict:
    """Validate JWT via Supabase auth endpoint — async so it never blocks the event loop."""
    client = _get_async_client()
    resp = await client.get(
        f"{SUPABASE_URL}/auth/v1/user",
        headers={
            "Authorization": f"Bearer {token}",
            "apikey": SUPABASE_SERVICE_KEY,
        },
    )
    if resp.status_code != 200:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    return resp.json()


# ─── Supabase DB helpers — all async ────────────────────────────────
async def _supabase_rpc(fn_name: str, params: dict) -> dict:
    client = _get_async_client()
    resp = await client.post(
        f"{SUPABASE_URL}/rest/v1/rpc/{fn_name}",
        headers={
            "apikey": SUPABASE_SERVICE_KEY,
            "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
            "Content-Type": "application/json",
        },
        json=params,
    )
    resp.raise_for_status()
    return resp.json() if resp.text else {}


async def _supabase_query(table: str, select: str = "*", filters: str = "") -> list:
    client = _get_async_client()
    url = f"{SUPABASE_URL}/rest/v1/{table}?select={select}"
    if filters:
        url += f"&{filters}"
    resp = await client.get(
        url,
        headers={
            "apikey": SUPABASE_SERVICE_KEY,
            "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
        },
    )
    resp.raise_for_status()
    return resp.json()


async def _supabase_insert(table: str, data: dict) -> dict:
    client = _get_async_client()
    resp = await client.post(
        f"{SUPABASE_URL}/rest/v1/{table}",
        headers={
            "apikey": SUPABASE_SERVICE_KEY,
            "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
            "Content-Type": "application/json",
            "Prefer": "return=representation",
        },
        json=data,
    )
    resp.raise_for_status()
    rows = resp.json()
    return rows[0] if rows else {}


async def _supabase_update(table: str, filters: str, data: dict) -> dict:
    client = _get_async_client()
    resp = await client.patch(
        f"{SUPABASE_URL}/rest/v1/{table}?{filters}",
        headers={
            "apikey": SUPABASE_SERVICE_KEY,
            "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
            "Content-Type": "application/json",
            "Prefer": "return=representation",
        },
        json=data,
    )
    resp.raise_for_status()
    rows = resp.json()
    return rows[0] if rows else {}


# Sync wrappers kept for background threads (Celery worker, build thread)
# These MUST NOT be called from async FastAPI route handlers.
def _supabase_update_sync(table: str, filters: str, data: dict) -> dict:
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


def _supabase_insert_sync(table: str, data: dict) -> dict:
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


def _supabase_rpc_sync(fn_name: str, params: dict) -> dict:
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


# ─── BYOK Encryption ────────────────────────────────────────────────
def encrypt_api_key(plaintext: str) -> str:
    """XOR-based encryption with HMAC integrity check."""
    if not ENCRYPTION_KEY:
        raise RuntimeError(
            "ENCRYPTION_KEY env var is not set. "
            "Set a secret 32+ character value in secrets before storing BYOK keys."
        )
    key_bytes = hashlib.sha256(ENCRYPTION_KEY.encode()).digest()
    pt_bytes = plaintext.encode()
    key_stream = (key_bytes * ((len(pt_bytes) // 32) + 1))[:len(pt_bytes)]
    ct = bytes(a ^ b for a, b in zip(pt_bytes, key_stream))
    mac = hmac.new(key_bytes, ct, hashlib.sha256).hexdigest()
    import base64
    return base64.urlsafe_b64encode(ct).decode() + "." + mac


def decrypt_api_key(ciphertext: str) -> str:
    import base64
    if not ENCRYPTION_KEY:
        raise RuntimeError("ENCRYPTION_KEY env var is not set — cannot decrypt stored API key.")
    parts = ciphertext.split(".", 1)
    if len(parts) != 2:
        raise ValueError("Malformed encrypted key")
    ct = base64.urlsafe_b64decode(parts[0])
    mac = parts[1]
    key_bytes = hashlib.sha256(ENCRYPTION_KEY.encode()).digest()
    expected_mac = hmac.new(key_bytes, ct, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(mac, expected_mac):
        raise ValueError("Integrity check failed — key may have been tampered with")
    key_stream = (key_bytes * ((len(ct) // 32) + 1))[:len(ct)]
    pt = bytes(a ^ b for a, b in zip(ct, key_stream))
    return pt.decode()


# ─── FastAPI Dependency: get current user — fully async ──────────────
async def get_current_user(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
) -> Optional[dict]:
    """Extract and validate the Supabase JWT — async so it never blocks the event loop."""
    if not AUTH_ENABLED:
        return None

    if not credentials:
        raise HTTPException(status_code=401, detail="Missing Authorization header")

    token = credentials.credentials
    user = await _decode_supabase_jwt(token)
    uid = user.get("id")
    if not uid:
        raise HTTPException(status_code=401, detail="Invalid user")

    profiles = await _supabase_query("profiles", filters=f"id=eq.{uid}")
    if not profiles:
        raise HTTPException(status_code=404, detail="Profile not found. Sign up first.")

    return profiles[0]


# ─── Build Guard ─────────────────────────────────────────────────────
def check_build_allowed(profile: Optional[dict]) -> None:
    if profile is None:
        return

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
                "message": f"You've used all {limit} builds on the {plan} plan. Upgrade to continue.",
                "upgrade_url": "/pricing",
            },
        )


def get_llm_key_for_user(profile: Optional[dict]) -> Optional[str]:
    if profile is None:
        return None
    if profile.get("plan") != "byok":
        return None
    encrypted_key = profile.get("llm_api_key")
    if not encrypted_key:
        raise HTTPException(
            status_code=400,
            detail="BYOK plan requires an API key setup. Set it in your profile settings.",
        )
    try:
        return decrypt_api_key(encrypted_key)
    except ValueError:
        raise HTTPException(status_code=500, detail="Failed to decrypt stored API key")


def get_byok_config_for_user(profile: Optional[dict]) -> Optional[dict]:
    val = get_llm_key_for_user(profile)
    if not val:
        return None
    try:
        return json.loads(val)
    except json.JSONDecodeError:
        return {
            "group1": {"api_key": val},
            "group2": {"api_key": val},
            "group3": {"api_key": val},
        }


# ─── Build lifecycle — use sync versions (called from threads) ───────
def record_build_start(profile: Optional[dict], job_id: str, design_name: str) -> None:
    if profile is None or not AUTH_ENABLED:
        return
    try:
        _supabase_insert_sync("builds", {
            "user_id": profile["id"],
            "job_id": job_id,
            "design_name": design_name,
            "status": "queued",
        })
    except Exception:
        pass  # Don't let Supabase hiccups break the build start


def record_build_success(profile: Optional[dict], job_id: str) -> None:
    if profile is None or not AUTH_ENABLED:
        return
    uid = profile["id"]
    try:
        _supabase_update_sync("builds", f"job_id=eq.{job_id}", {
            "status": "done",
            "finished_at": "now()",
        })
        _supabase_rpc_sync("increment_successful_builds", {"uid": uid})
    except Exception:
        pass


def record_build_failure(job_id: str) -> None:
    if not AUTH_ENABLED:
        return
    try:
        _supabase_update_sync("builds", f"job_id=eq.{job_id}", {
            "status": "failed",
            "finished_at": "now()",
        })
    except Exception:
        pass
