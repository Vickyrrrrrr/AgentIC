"""
AgentIC Billing — Razorpay webhook handler + order creation.

Env vars required:
    RAZORPAY_KEY_ID       – Razorpay API key id
    RAZORPAY_KEY_SECRET   – Razorpay API key secret
    RAZORPAY_WEBHOOK_SECRET – Webhook secret from Razorpay dashboard
"""

import hashlib
import hmac
import json
import os
import re
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

RAZORPAY_KEY_ID = os.environ.get("RAZORPAY_KEY_ID", "")
RAZORPAY_KEY_SECRET = os.environ.get("RAZORPAY_KEY_SECRET", "")
RAZORPAY_WEBHOOK_SECRET = os.environ.get("RAZORPAY_WEBHOOK_SECRET", "")

# Plan prices in paise (₹1 = 100 paise)
PLAN_PRICES = {
    "starter": 49900,    # ₹499
    "pro": 149900,       # ₹1,499
}

UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
RAZORPAY_ORDER_RE = re.compile(r"^order_[a-zA-Z0-9]+$")
RAZORPAY_PAYMENT_RE = re.compile(r"^pay_[a-zA-Z0-9]+$")
HEX_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class CreateOrderRequest(BaseModel):
    plan: str    # "starter" or "pro"
    user_id: str  # Supabase user UUID


class VerifyPaymentRequest(BaseModel):
    razorpay_order_id: str
    razorpay_payment_id: str
    razorpay_signature: str
    user_id: str
    plan: str


# ─── Create Razorpay Order ──────────────────────────────────────────
@router.post("/create-order")
async def create_order(req: CreateOrderRequest, profile: dict = Depends(get_current_user)):
    """Create a Razorpay order for plan upgrade."""
    if not RAZORPAY_KEY_ID or not RAZORPAY_KEY_SECRET:
        raise HTTPException(status_code=503, detail="Payment system not configured")

    if req.plan not in PLAN_PRICES:
        raise HTTPException(status_code=400, detail=f"Invalid plan: {req.plan}. Choose 'starter' or 'pro'.")

    if not UUID_RE.fullmatch(req.user_id):
        raise HTTPException(status_code=400, detail="Invalid user_id")

    if profile is not None and profile.get("id") != req.user_id:
        raise HTTPException(status_code=403, detail="Cannot create order for another user")

    amount = PLAN_PRICES[req.plan]

    # Create order via Razorpay API
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            "https://api.razorpay.com/v1/orders",
            auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET),
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

    # Record pending payment
    if AUTH_ENABLED:
        await _supabase_insert("payments", {
            "user_id": req.user_id,
            "razorpay_order_id": order["id"],
            "amount_paise": amount,
            "plan": req.plan,
            "status": "pending",
        })

    return {
        "order_id": order["id"],
        "amount": amount,
        "currency": "INR",
        "key_id": RAZORPAY_KEY_ID,
        "plan": req.plan,
    }


# ─── Verify Payment (client-side callback) ─────────────────────────
@router.post("/verify-payment")
async def verify_payment(req: VerifyPaymentRequest, profile: dict = Depends(get_current_user)):
    """Verify Razorpay payment signature and upgrade user plan."""
    if not RAZORPAY_KEY_SECRET:
        raise HTTPException(status_code=503, detail="Payment system not configured")

    # Validate plan to prevent arbitrary plan escalation
    if req.plan not in ("starter", "pro"):
        raise HTTPException(status_code=400, detail="Invalid plan")

    # Validate user_id is a well-formed UUID to prevent PostgREST filter injection
    if not UUID_RE.fullmatch(req.user_id):
        raise HTTPException(status_code=400, detail="Invalid user_id")

    if not RAZORPAY_ORDER_RE.fullmatch(req.razorpay_order_id):
        raise HTTPException(status_code=400, detail="Invalid razorpay_order_id")

    if not RAZORPAY_PAYMENT_RE.fullmatch(req.razorpay_payment_id):
        raise HTTPException(status_code=400, detail="Invalid razorpay_payment_id")

    if not HEX_SHA256_RE.fullmatch(req.razorpay_signature):
        raise HTTPException(status_code=400, detail="Invalid razorpay_signature")

    # Enforce that the authenticated user can only upgrade their own account
    if profile is not None and profile.get("id") != req.user_id:
        raise HTTPException(status_code=403, detail="Cannot upgrade another user's plan")

    # Verify signature: SHA256 HMAC of order_id|payment_id
    message = f"{req.razorpay_order_id}|{req.razorpay_payment_id}"
    expected = hmac.new(
        RAZORPAY_KEY_SECRET.encode(),
        message.encode(),
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(expected, req.razorpay_signature):
        raise HTTPException(status_code=400, detail="Payment verification failed — signature mismatch")

    if AUTH_ENABLED:
        # Update payment record
        await _supabase_update(
            "payments",
            f"razorpay_order_id=eq.{req.razorpay_order_id}",
            {
                "razorpay_payment_id": req.razorpay_payment_id,
                "razorpay_signature": req.razorpay_signature,
                "status": "captured",
            },
        )

        # Upgrade user plan
        await _supabase_update(
            "profiles",
            f"id=eq.{req.user_id}",
            {"plan": req.plan, "successful_builds": 0},
        )

    return {"success": True, "plan": req.plan, "message": f"Upgraded to {req.plan} plan!"}


# ─── Razorpay Webhook (server-to-server) ───────────────────────────
@router.post("/webhook/razorpay")
async def razorpay_webhook(request: Request):
    """Handle Razorpay webhook events (payment.captured, payment.failed).

    Razorpay sends a POST with a JSON body and X-Razorpay-Signature header.
    We verify the HMAC-SHA256 signature before processing.
    """
    if not RAZORPAY_WEBHOOK_SECRET:
        raise HTTPException(status_code=503, detail="Webhook secret not configured")

    body = await request.body()
    signature = request.headers.get("X-Razorpay-Signature", "")

    # Verify webhook signature
    expected = hmac.new(
        RAZORPAY_WEBHOOK_SECRET.encode(),
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

        # Sanitize inputs to prevent PostgREST filter injection
        if not (user_id and UUID_RE.match(user_id)):
            user_id = ""
        if not (order_id and RAZORPAY_ORDER_RE.match(order_id)):
            order_id = ""
        if user_id and plan and order_id and AUTH_ENABLED:
            # Update payment status
            await _supabase_update(
                "payments",
                f"razorpay_order_id=eq.{order_id}",
                {
                    "razorpay_payment_id": payment.get("id", ""),
                    "status": "captured",
                },
            )

            # Upgrade user plan and reset build count
            await _supabase_update(
                "profiles",
                f"id=eq.{user_id}",
                {"plan": plan, "successful_builds": 0},
            )

    elif event == "payment.failed":
        payment = payload.get("payload", {}).get("payment", {}).get("entity", {})
        order_id = payment.get("order_id", "")
        if order_id and AUTH_ENABLED:
            await _supabase_update(
                "payments",
                f"razorpay_order_id=eq.{order_id}",
                {"status": "failed"},
            )

    # Razorpay expects 200 OK to acknowledge receipt
    return {"status": "ok"}
