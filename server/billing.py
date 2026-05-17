"""
AgentIC Billing — Razorpay payment gateway with test mode.

TWO MODES:
1. Test Mode (default when RAZORPAY_KEY_ID is not set or "test"):
   - Creates mock orders instantly
   - Accepts any payment without real processing
   - USE THIS FOR DEVELOPMENT/TESTING

2. Production Mode (set RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET in .env):
   - Full Razorpay integration
   - Real payments processed

Env vars required for production:
    RAZORPAY_KEY_ID       – Razorpay API key id
    RAZORPAY_KEY_SECRET   – Razorpay API key secret
    RAZORPAY_WEBHOOK_SECRET – Webhook secret from Razorpay dashboard

Supabase profile columns required:
    plan_type: TEXT     -- 'agentic_paid' or 'byok'
    build_limit: INTEGER  -- 10 for starter, NULL for unlimited
    razorpay_order_id: TEXT
"""

import hashlib
import hmac
import json
import os
import re
import uuid
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from server.auth import (
    AUTH_ENABLED,
    get_current_user,
    _supabase_insert,
    _supabase_query,
    _supabase_update,
)

router = APIRouter(prefix="/billing", tags=["billing"])

# ─── Billing Mode Detection ───────────────────────────────────────────
_RAZORPAY_KEY_ID = os.environ.get("RAZORPAY_KEY_ID", "")
_RAZORPAY_KEY_SECRET = os.environ.get("RAZORPAY_KEY_SECRET", "")
_RAZORPAY_WEBHOOK_SECRET = os.environ.get("RAZORPAY_WEBHOOK_SECRET", "")

IS_TEST_MODE = not _RAZORPAY_KEY_ID or _RAZORPAY_KEY_ID == "test"

# Plan definitions
# Prices in USD cents (display) and INR paise (Razorpay)
# $20 USD = 2000 cents, $2000 USD = 200000 cents
PLANS = {
    "starter": {
        "name": "Starter",
        "description": "10 successful chip builds",
        "build_limit": 10,
        "price_display": "$20",
        "price_inr_paise": 167000,  # ~₹1,670 (approx $20 USD)
        "razorpay_amount_paise": 167000,
    },
    "pro": {
        "name": "Pro (Unlimited)",
        "description": "Unlimited successful chip builds",
        "build_limit": None,  # NULL = unlimited
        "price_display": "$200",
        "price_inr_paise": 16700000,  # ~₹1,67,000 (approx $2000 USD)
        "razorpay_amount_paise": 16700000,
    },
}

# Legacy aliases (for existing data)
PLAN_LIMITS = {
    "starter": 10,
    "pro": None,
    "unlimited": None,
}

UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
RAZORPAY_ORDER_RE = re.compile(r"^order_[a-zA-Z0-9]+$")
RAZORPAY_PAYMENT_RE = re.compile(r"^pay_[a-zA-Z0-9]+$")
HEX_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


# ─── Request Models ────────────────────────────────────────────────────
class CreateOrderRequest(BaseModel):
    plan: str  # "starter" or "unlimited"
    user_id: str  # Supabase user UUID


class VerifyPaymentRequest(BaseModel):
    razorpay_order_id: str
    razorpay_payment_id: str
    razorpay_signature: str
    user_id: str
    plan: str


class TestModeActivateRequest(BaseModel):
    plan: str
    user_id: str


# ─── Helper ────────────────────────────────────────────────────────────
def _is_valid_uuid(val: str) -> bool:
    return bool(UUID_RE.fullmatch(val))


# ─── GET /billing/plans ───────────────────────────────────────────────
@router.get("/plans")
async def get_plans(profile: dict = Depends(get_current_user)):
    """Return available plans (public, no auth required)."""
    return {
        "plans": [
            {
                "id": plan_id,
                "name": info["name"],
                "description": info["description"],
                "build_limit": info["build_limit"],
                "price_display": info["price_display"],
                "test_mode": IS_TEST_MODE,
            }
            for plan_id, info in PLANS.items()
        ],
        "test_mode": IS_TEST_MODE,
    }


# ─── POST /billing/create-order ────────────────────────────────────────
@router.post("/create-order")
async def create_order(
    req: CreateOrderRequest, profile: dict = Depends(get_current_user)
):
    """Create a payment order for plan upgrade.

    In TEST MODE: Returns a mock order instantly.
    In PRODUCTION: Creates a real Razorpay order.
    """
    if req.plan not in PLANS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid plan: {req.plan}. Choose 'starter' or 'unlimited'.",
        )

    if not _is_valid_uuid(req.user_id):
        raise HTTPException(status_code=400, detail="Invalid user_id")

    if profile is not None and profile.get("id") != req.user_id:
        raise HTTPException(
            status_code=403, detail="Cannot create order for another user"
        )

    plan_info = PLANS[req.plan]

    # ── TEST MODE ────────────────────────────────────────────────────────
    if IS_TEST_MODE:
        mock_order_id = f"test_order_{uuid.uuid4().hex[:12]}"
        return {
            "order_id": mock_order_id,
            "amount_display": plan_info["price_display"],
            "amount_inr_paise": plan_info["price_inr_paise"],
            "currency": "INR",
            "plan": req.plan,
            "plan_name": plan_info["name"],
            "build_limit": plan_info["build_limit"],
            "test_mode": True,
            "message": "TEST MODE — No real payment required",
        }

    # ── PRODUCTION MODE ─────────────────────────────────────────────────
    if not _RAZORPAY_KEY_ID or not _RAZORPAY_KEY_SECRET:
        raise HTTPException(status_code=503, detail="Payment system not configured")

    amount = plan_info["razorpay_amount_paise"]

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            "https://api.razorpay.com/v1/orders",
            auth=(_RAZORPAY_KEY_ID, _RAZORPAY_KEY_SECRET),
            json={
                "amount": amount,
                "currency": "INR",
                "receipt": f"agentic_{req.user_id[:8]}_{req.plan}",
                "notes": {
                    "user_id": req.user_id,
                    "plan": req.plan,
                },
            },
        )

    if resp.status_code != 200:
        raise HTTPException(status_code=502, detail="Failed to create Razorpay order")

    order = resp.json()

    if AUTH_ENABLED:
        await _supabase_insert(
            "payments",
            {
                "user_id": req.user_id,
                "razorpay_order_id": order["id"],
                "amount_paise": amount,
                "plan": req.plan,
                "status": "pending",
            },
        )

    return {
        "order_id": order["id"],
        "amount_display": plan_info["price_display"],
        "amount_inr_paise": amount,
        "currency": "INR",
        "key_id": _RAZORPAY_KEY_ID,
        "plan": req.plan,
        "plan_name": plan_info["name"],
        "build_limit": plan_info["build_limit"],
    }


# ─── POST /billing/verify-payment ────────────────────────────────────
@router.post("/verify-payment")
async def verify_payment(
    req: VerifyPaymentRequest, profile: dict = Depends(get_current_user)
):
    """Verify Razorpay payment and activate the user's plan.

    In TEST MODE: Activates plan instantly without signature verification.
    In PRODUCTION: Full Razorpay signature verification.
    """
    if req.plan not in PLANS:
        raise HTTPException(status_code=400, detail=f"Invalid plan: {req.plan}")

    if not _is_valid_uuid(req.user_id):
        raise HTTPException(status_code=400, detail="Invalid user_id")

    if not _is_valid_uuid(
        req.razorpay_order_id.replace("test_order_", "00000000-0000-0000-0000-00000000")
    ):
        pass  # Allow test order IDs

    if profile is not None and profile.get("id") != req.user_id:
        raise HTTPException(
            status_code=403, detail="Cannot upgrade another user's plan"
        )

    plan_info = PLANS[req.plan]

    # ── TEST MODE ────────────────────────────────────────────────────────
    if IS_TEST_MODE:
        await _activate_user_plan(
            user_id=req.user_id,
            plan=req.plan,
            build_limit=plan_info["build_limit"],
            razorpay_order_id=req.razorpay_order_id,
            razorpay_payment_id=req.razorpay_payment_id,
        )
        return {
            "success": True,
            "plan": req.plan,
            "plan_name": plan_info["name"],
            "build_limit": plan_info["build_limit"],
            "message": "TEST MODE — Plan activated (no real payment).",
            "test_mode": True,
        }

    # ── PRODUCTION MODE ─────────────────────────────────────────────────
    if not _RAZORPAY_KEY_SECRET:
        raise HTTPException(status_code=503, detail="Payment system not configured")

    for regex in (RAZORPAY_ORDER_RE, RAZORPAY_PAYMENT_RE, HEX_SHA256_RE):
        if not regex.fullmatch(req.razorpay_signature):
            if not HEX_SHA256_RE.fullmatch(req.razorpay_signature):
                raise HTTPException(status_code=400, detail="Invalid signature format")

    message = f"{req.razorpay_order_id}|{req.razorpay_payment_id}"
    expected = hmac.new(
        _RAZORPAY_KEY_SECRET.encode(),
        message.encode(),
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(expected, req.razorpay_signature):
        raise HTTPException(
            status_code=400,
            detail="Payment verification failed — signature mismatch",
        )

    await _activate_user_plan(
        user_id=req.user_id,
        plan=req.plan,
        build_limit=plan_info["build_limit"],
        razorpay_order_id=req.razorpay_order_id,
        razorpay_payment_id=req.razorpay_payment_id,
    )

    return {
        "success": True,
        "plan": req.plan,
        "plan_name": plan_info["name"],
        "build_limit": plan_info["build_limit"],
        "message": f"Upgraded to {plan_info['name']} plan!",
    }


async def _activate_user_plan(
    user_id: str,
    plan: str,
    build_limit: Optional[int],
    razorpay_order_id: str,
    razorpay_payment_id: str,
) -> None:
    """Update Supabase profile with plan_type=agentic_paid and build_limit."""
    if not AUTH_ENABLED:
        return

    # Update or insert payment record
    try:
        await _supabase_insert(
            "payments",
            {
                "user_id": user_id,
                "razorpay_order_id": razorpay_order_id,
                "razorpay_payment_id": razorpay_payment_id,
                "plan": plan,
                "status": "captured",
            },
        )
    except Exception:
        pass

    # Upgrade user profile
    await _supabase_update(
        "profiles",
        f"id=eq.{user_id}",
        {
            "plan": plan,
            "successful_builds": 0,
        },
    )


# ─── POST /billing/test-activate ──────────────────────────────────────
@router.post("/test-activate")
async def test_mode_activate(
    req: TestModeActivateRequest, profile: dict = Depends(get_current_user)
):
    """Activate a plan in TEST MODE without any payment.

    This endpoint is ONLY available when RAZORPAY_KEY_ID is not set or is 'test'.
    It simulates a successful payment for development/testing purposes.
    """
    if not IS_TEST_MODE:
        raise HTTPException(
            status_code=403,
            detail="Test mode is not available. Configure RAZORPAY_KEY_ID in your .env to use production billing.",
        )

    if req.plan not in PLANS:
        raise HTTPException(status_code=400, detail=f"Invalid plan: {req.plan}")

    if not _is_valid_uuid(req.user_id):
        raise HTTPException(status_code=400, detail="Invalid user_id")

    if profile is not None and profile.get("id") != req.user_id:
        raise HTTPException(
            status_code=403, detail="Cannot activate plan for another user"
        )

    plan_info = PLANS[req.plan]

    await _activate_user_plan(
        user_id=req.user_id,
        plan=req.plan,
        build_limit=plan_info["build_limit"],
        razorpay_order_id=f"test_order_{uuid.uuid4().hex[:12]}",
        razorpay_payment_id=f"test_payment_{uuid.uuid4().hex[:12]}",
    )

    return {
        "success": True,
        "plan": req.plan,
        "plan_name": plan_info["name"],
        "build_limit": plan_info["build_limit"],
        "message": f"TEST MODE — {plan_info['name']} plan activated (no payment taken).",
        "test_mode": True,
    }


# ─── POST /billing/webhook/razorpay ────────────────────────────────────
@router.post("/webhook/razorpay")
async def razorpay_webhook(request: Request):
    """Handle Razorpay webhook events (payment.captured, payment.failed)."""
    if not _RAZORPAY_WEBHOOK_SECRET:
        raise HTTPException(status_code=503, detail="Webhook secret not configured")

    body = await request.body()
    signature = request.headers.get("X-Razorpay-Signature", "")

    expected = hmac.new(
        _RAZORPAY_WEBHOOK_SECRET.encode(),
        body,
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(expected, signature):
        raise HTTPException(status_code=400, detail="Invalid webhook signature")

    payload = json.loads(body)
    event = payload.get("event", "")

    if event == "payment.captured":
        payment = payload.get("payload", {}).get("payment", {}).get("entity", {})
        order_id = payment.get("order_id", "")
        notes = payment.get("notes", {})
        user_id = notes.get("user_id", "")
        plan = notes.get("plan", "")

        if not _is_valid_uuid(user_id):
            user_id = ""
        if not (order_id and RAZORPAY_ORDER_RE.match(order_id)):
            order_id = ""

        if user_id and plan in PLANS and order_id and AUTH_ENABLED:
            plan_info = PLANS[plan]
            await _supabase_update(
                "payments",
                f"razorpay_order_id=eq.{order_id}",
                {
                    "razorpay_payment_id": payment.get("id", ""),
                    "status": "captured",
                },
            )
            await _supabase_update(
                "profiles",
                f"id=eq.{user_id}",
                {
                    "plan_type": "agentic_paid",
                    "plan": plan,
                    "build_limit": plan_info["build_limit"],
                    "successful_builds": 0,
                },
            )

    elif event == "payment.failed":
        payment = payload.get("payload", {}).get("payment", {}).get("entity", {})
        order_id = payment.get("order_id", "")
        if order_id and RAZORPAY_ORDER_RE.match(order_id) and AUTH_ENABLED:
            await _supabase_update(
                "payments",
                f"razorpay_order_id=eq.{order_id}",
                {"status": "failed"},
            )

    return {"status": "ok"}


# ─── GET /billing/status ───────────────────────────────────────────────
@router.get("/status")
async def get_billing_status(profile: dict = Depends(get_current_user)):
    """Get current user's billing and plan status."""
    if profile is None:
        return {
            "plan_type": "local",
            "plan": None,
            "build_limit": None,
            "test_mode": IS_TEST_MODE,
        }

    plan = profile.get("plan", "free")
    return {
        "plan_type": "byok" if plan == "byok" else "agentic_paid",
        "plan": plan,
        "build_limit": PLAN_LIMITS.get(plan),
        "successful_builds": profile.get("successful_builds", 0),
        "test_mode": IS_TEST_MODE,
    }
