"""Billing routes package — assembles all sub-routers under /billing.

Implementation is split into focused modules:
  _shared.py      — Shared schemas, imports, and helper functions
  checkout.py     — Subscription + top-up checkout, card registration
  webhooks.py     — Stripe webhook, Worldline return/notify, Worldline card return
  info.py         — Tiers, summary, credits, payment history, payment methods CRUD
  subscription.py — Subscription lifecycle (cancel, reactivate, downgrade, upgrade, invoice)
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.routes.billing import checkout, info, subscription, webhooks

router = APIRouter(prefix="/billing", tags=["billing"])
router.include_router(checkout.router)
router.include_router(webhooks.router)
router.include_router(info.router)
router.include_router(subscription.router)
