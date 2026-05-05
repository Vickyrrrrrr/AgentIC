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
from typing import Optional

import httpx
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

# ─── Config ──────────────────────────────────────────────────────────
SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
SUPABASE_JWT_SECRET = os.environ.get("SUPABASE_JWT_SECRET", "")
# ENCRYPTION_KEY must be set in production via env var — never rely on a default.
# If unset, BYOK key storage is disabled with a clear error rather than silently
# using a publicly-known default key that would let anyone decrypt stored keys.
ENCRYPTION_KEY = os.environ.get("ENCRYPTION_KEY", "")

AUTH_ENABLED = bool(SUPABASE_URL and SUPABASE_SERVICE_KEY and SUPABASE_JWT_SECRET)

# Plan limits: max successful builds allowed (None = unlimited)
PLAN_LIMITS = {
    "free": 2,
    "starter": 10,
    "unlimited": None,
    "pro": None,
    "byok": None,  # unlimited, uses own key
}

_bearer = HTTPBearer(auto_error=False)


# ─── JWT Decode (no pyjwt dependency — use Supabase /auth/v1/user) ──
async def _decode_supabase_jwt(token: str) -> dict:
    """Validate JWT by calling Supabase auth endpoint.

    We call GET /auth/v1/user with the user's access_token.
    Supabase verifies the JWT signature and returns the user object.
    """
    async with httpx.AsyncClient(timeout=10) as client:
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


# ─── Supabase DB helpers (use service-role key) ─────────────────────
async def _supabase_rpc(fn_name: str, params: dict) -> dict:
    """Call a Supabase RPC function."""
    async with httpx.AsyncClient(timeout=10) as client:
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
    """Simple REST query against Supabase PostgREST."""
    url = f"{SUPABASE_URL}/rest/v1/{table}?select={select}"
    if filters:
        url += f"&{filters}"
    async with httpx.AsyncClient(timeout=10) as client:
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
    async with httpx.AsyncClient(timeout=10) as client:
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
    async with httpx.AsyncClient(timeout=10) as client:
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


# Sync wrappers kept for background threads (Celery worker, build thread).
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
def _derive_fernet_key(secret: str) -> bytes:
    """Derive a Fernet-compatible key from the ENCRYPTION_KEY env var."""
    import base64
    key_bytes = hashlib.sha256(secret.encode()).digest()
    return base64.urlsafe_b64encode(key_bytes)


def encrypt_api_key(plaintext: str) -> str:
    """Encrypt a BYOK API key using Fernet (AES-128-CBC)."""
    if not ENCRYPTION_KEY:
        raise RuntimeError(
            "ENCRYPTION_KEY env var is not set. "
            "Set a secret 32+ character value in your environment before storing BYOK keys."
        )
    from cryptography.fernet import Fernet
    f = Fernet(_derive_fernet_key(ENCRYPTION_KEY))
    return "fernet:" + f.encrypt(plaintext.encode()).decode()


def _decrypt_legacy_xor(ciphertext: str) -> str:
    """Backward-compatible decryption for old XOR-encrypted keys."""
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


def decrypt_api_key(ciphertext: str) -> str:
    """Decrypt a BYOK API key. Supports both Fernet and legacy XOR format."""
    if not ENCRYPTION_KEY:
        raise RuntimeError(
            "ENCRYPTION_KEY env var is not set — cannot decrypt stored API key."
        )
    # New Fernet format: prefixed with "fernet:"
    if ciphertext.startswith("fernet:"):
        from cryptography.fernet import Fernet
        f = Fernet(_derive_fernet_key(ENCRYPTION_KEY))
        return f.decrypt(ciphertext[7:].encode()).decode()

    # Legacy XOR format: "base64.hmac"
    return _decrypt_legacy_xor(ciphertext)


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
    user = await _decode_supabase_jwt(token)
    uid = user.get("id")
    if not uid:
        raise HTTPException(status_code=401, detail="Invalid user")

    # Fetch profile from DB
    profiles = await _supabase_query("profiles", filters=f"id=eq.{uid}")
    if not profiles:
        raise HTTPException(status_code=404, detail="Profile not found. Sign up first.")

    return profiles[0]


# ─── Build Guard: check plan + build count ───────────────────────────
def check_build_allowed(profile: Optional[dict]) -> None:
    """Raise 402 if the user has exhausted their plan's build quota.

    Called before every /build request when auth is enabled.
    Uses build_limit from profile (set by billing flow).
    Falls back to PLAN_LIMITS lookup for legacy profiles.
    """
    if profile is None:
        return  # Auth disabled — no restrictions

    plan_type = profile.get("plan_type", "byok")

    # BYOK users have unlimited builds
    if plan_type == "byok":
        return

    # AgentIC-paid users: check build_limit (set by billing)
    # Falling back to PLAN_LIMITS for legacy users without build_limit set
    build_limit = profile.get("build_limit")
    if build_limit is None:
        build_limit = PLAN_LIMITS.get(plan_type)

    if build_limit is not None:
        builds = profile.get("successful_builds", 0)
        if builds >= build_limit:
            plan_name = profile.get("plan", plan_type)
            raise HTTPException(
                status_code=402,
                detail={
                    "error": "build_limit_reached",
                    "plan": plan_name,
                    "used": builds,
                    "limit": build_limit,
                    "message": f"You've used all {build_limit} builds on your {plan_name} plan. Upgrade to continue building chips.",
                    "upgrade_url": "/pricing",
                },
            )


def get_llm_key_for_user(profile: Optional[dict]) -> Optional[str]:
    """Return the user's decrypted LLM API key if they have one saved.

    Works for ALL plans — any user who has saved a BYOK key can use it.
    Returns None if no key is saved (backend fallback will be used if allowed).
    """
    if profile is None:
        return None

    # FIX: removed plan != 'byok' restriction — BYOK works for all plans
    encrypted_key = profile.get("llm_api_key")
    if not encrypted_key:
        return None

    try:
        return decrypt_api_key(encrypted_key)
    except ValueError:
        raise HTTPException(status_code=500, detail="Failed to decrypt stored API key")


def get_byok_config_for_user(profile: Optional[dict]) -> Optional[dict]:
    """Return normalized multi-group BYOK config stored in llm_api_key.

    Works for ALL plans — any user who has saved a BYOK key can use it.
    """
    if profile is None:
        return None
    encrypted_value = profile.get("llm_api_key")
    if not encrypted_value:
        return None
    try:
        decrypted = decrypt_api_key(encrypted_value)
    except (ValueError, RuntimeError):
        raise HTTPException(status_code=500, detail="Failed to decrypt stored API key")

    try:
        parsed = json.loads(decrypted)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    # Backward compatibility: previously a single key was stored.
    return {
        "group1": {"api_key": decrypted},
        "group2": {"api_key": decrypted},
        "group3": {"api_key": decrypted},
    }


async def save_byok_config_for_user(profile: dict, byok_config: dict) -> None:
    """Encrypt and persist full multi-group BYOK config in the profile row."""
    payload = json.dumps(byok_config)
    encrypted = encrypt_api_key(payload)
    await _supabase_update(
        "profiles", f"id=eq.{profile['id']}", {"llm_api_key": encrypted}
    )


def record_build_start(profile: Optional[dict], job_id: str, design_name: str) -> None:
    """Insert a build record into the builds table."""
    if profile is None or not AUTH_ENABLED:
        return
    _supabase_insert_sync(
        "builds",
        {
            "user_id": profile["id"],
            "job_id": job_id,
            "design_name": design_name,
            "status": "queued",
        },
    )


def record_build_success(profile: Optional[dict], job_id: str) -> None:
    """Mark build as done and increment the user's successful_builds count."""
    if profile is None or not AUTH_ENABLED:
        return
    uid = profile["id"]
    # Update build row
    _supabase_update_sync(
        "builds",
        f"job_id=eq.{job_id}",
        {
            "status": "done",
            "finished_at": "now()",
        },
    )
    # Increment counter
    _supabase_rpc_sync("increment_successful_builds", {"uid": uid})


def record_build_failure(job_id: str) -> None:
    """Mark build as failed."""
    if not AUTH_ENABLED:
        return
    _supabase_update_sync(
        "builds",
        f"job_id=eq.{job_id}",
        {
            "status": "failed",
            "finished_at": "now()",
        },
    )
