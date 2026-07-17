# Deep Dive — Billing Webhooks & Worldline/Saferpay

For the Saferpay API request/response contract (what fields go where), see
the existing [`docs/payment-flows.md`](../payment-flows.md) — it's detailed
and accurate, no need to duplicate it here. This doc covers the **code
structure and risk** angle: what's fragile, what's untested, and a concrete
lead on the known "upgrade doesn't work" bug from `ROADMAP.md`.

## Stripe vs. Worldline webhooks are structured completely differently

`app/api/routes/billing/webhooks.py` has three handlers:

- `stripe_webhook` — ~45 lines. Verifies signature, extracts event data, then
  **delegates**: `payments.apply_subscription_update(...)` or
  `payments.apply_credit_topup(...)`. This matches the layering CLAUDE.md
  describes (routes thin, business logic in services).
- `worldline_return` — **295 lines** (lines 93–387) of inline business logic
  directly in the route handler: token/context extraction, a 15-minute
  pending-payment expiry check, calling `WorldlineProvider().authorize_transaction()`,
  parsing three different possible locations for the card alias ID in the
  response, VAT computation, manual `PaymentTransaction` upsert (two
  divergent code paths depending on whether a `pending_payment` row already
  exists), conditional auto-capture, and finally
  `payment_transactions.apply_successful_payment(...)`.
- `worldline_card_return` — a second, ~300-line handler for the
  card-registration-only flow, with its own near-duplicate implementation of
  token resolution, pending-payment lookup, and cancel/success redirect
  logic.

**Neither Worldline handler is covered by tests** (confirmed via the
knowledge-gaps scan). If you're reviewing a change to either, there's no
safety net today beyond reading it carefully and/or manual testing against
the Saferpay test environment.

**Simplification opportunity, not just a bug risk:** the token-resolution +
pending-payment-lookup logic (`_WORLDLINE_PLACEHOLDER_TOKENS` handling, "no
token but we have an order_reference, look up the pending row" fallback) is
near-identical between `worldline_return` and `worldline_card_return`. A
future refactor extracting that into a shared helper would shrink both and
remove a place where the two can silently drift apart.

## Concrete lead: `upgrade_proration_credits` is client-supplied, unvalidated

This is the most actionable finding from this pass — worth checking early in
your review regardless of whether it's the exact cause of the reported bug.

Trace:

1. `SubscriptionCheckoutRequest.upgrade_proration_credits` (`app/api/routes/billing/_shared.py:40`)
   is a plain field on the **incoming request body** — set by the frontend.
2. `create_subscription_checkout` (`app/api/routes/billing/checkout.py:101`)
   takes that value and stores it verbatim on the new `PaymentTransaction`
   row (`kind="subscription"`, `status="pending"`), with **no server-side
   recomputation or bound-checking** against the org's actual current tier,
   prior payments, or any proration formula.
3. Later, when the webhook confirms the payment,
   `apply_successful_payment` (`app/services/billing/payment_transactions.py:376`)
   reads that same field back off the row and **grants it as real credits**:
   ```python
   proration = payment_tx.upgrade_proration_credits
   if proration and proration > 0:
       credits.grant_credits(db, org_id=payment_tx.org_id, amount=proration, ...)
   ```

Two separate things worth your attention here:

- **Payment integrity risk**: nothing stops a client from sending an
  inflated `upgrade_proration_credits` value on *any* subscription checkout
  (not just a genuine upgrade) and receiving that many free credits the
  moment the (possibly minimum-amount) payment captures. Whether this is
  exploitable depends on frontend behavior you'd need to check — but the
  backend alone does not defend against it. This has nothing to do with
  Worldline specifically — the same trust applies to whichever provider's
  webhook eventually calls `apply_successful_payment`.
- **The reported "upgrade doesn't work" bug**: `apply_successful_payment`
  does correctly set `org.tier = payment_tx.subscription_tier` for both
  providers (Stripe via `apply_subscription_update`, Worldline via this
  function) — so the tier update itself isn't obviously skipped. The
  proration-credit path above is the one piece of upgrade-specific logic
  that has no Stripe equivalent at all, which makes it the natural place to
  start — but confirming the actual defect requires tracing where the
  frontend computes `upgrade_proration_credits` and decides which checkout
  fields to send for an org that already has a paid tier. That trace wasn't
  done in this pass (frontend, not covered by the graph tooling used here) —
  flagging it as the next step rather than a diagnosed root cause.

## Other things worth a look while you're in this area

- `worldline_return`'s exception handling special-cases `"TOKEN_INVALID"` /
  `"TRANSACTION_IN_WRONG_STATE"` substring-matching on a `RuntimeError`
  message (line ~378) to decide the payment already went through — string
  matching on an error message from an external provider is brittle if
  Saferpay ever changes their error text.
- The duplicate-payment guard (`get_payment_transaction_by_external_id`) and
  the 15-minute pending-expiry check are both good defensive patterns
  already in place — don't remove them without understanding why they're
  there (double-webhook-delivery and abandoned-checkout cases).
