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
from typing import Optional

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from server.auth import (
    AUTH_ENABLED,
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
async def create_order(req: CreateOrderRequest):
    """Create a Razorpay order for plan upgrade."""
    if not RAZORPAY_KEY_ID or not RAZORPAY_KEY_SECRET:
        raise HTTPException(status_code=503, detail="Payment system not configured")

    if req.plan not in PLAN_PRICES:
        raise HTTPException(status_code=400, detail=f"Invalid plan: {req.plan}. Choose 'starter' or 'pro'.")

    amount = PLAN_PRICES[req.plan]

    # Create order via Razorpay API
    resp = httpx.post(
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
        timeout=15,
    )
    if resp.status_code != 200:
        raise HTTPException(status_code=502, detail="Failed to create Razorpay order")

    order = resp.json()

    # Record pending payment
    if AUTH_ENABLED:
        _supabase_insert("payments", {
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
async def verify_payment(req: VerifyPaymentRequest):
    """Verify Razorpay payment signature and upgrade user plan."""
    if not RAZORPAY_KEY_SECRET:
        raise HTTPException(status_code=503, detail="Payment system not configured")

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
        _supabase_update(
            "payments",
            f"razorpay_order_id=eq.{req.razorpay_order_id}",
            {
                "razorpay_payment_id": req.razorpay_payment_id,
                "razorpay_signature": req.razorpay_signature,
                "status": "captured",
            },
        )

        # Upgrade user plan
        _supabase_update(
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

        if user_id and plan and AUTH_ENABLED:
            # Update payment status
            _supabase_update(
                "payments",
                f"razorpay_order_id=eq.{order_id}",
                {
                    "razorpay_payment_id": payment.get("id", ""),
                    "status": "captured",
                },
            )

            # Upgrade user plan and reset build count
            _supabase_update(
                "profiles",
                f"id=eq.{user_id}",
                {"plan": plan, "successful_builds": 0},
            )

    elif event == "payment.failed":
        payment = payload.get("payload", {}).get("payment", {}).get("entity", {})
        order_id = payment.get("order_id", "")
        if order_id and AUTH_ENABLED:
            _supabase_update(
                "payments",
                f"razorpay_order_id=eq.{order_id}",
                {"status": "failed"},
            )

    # Razorpay expects 200 OK to acknowledge receipt
    return {"status": "ok"}
