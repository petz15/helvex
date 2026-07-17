# Deep Dive — Billing Webhooks & Worldline/Saferpay

For the Saferpay API request/response contract (what fields go where), see
the existing [`docs/payment-flows.md`](../payment-flows.md) — it's detailed
and accurate, no need to duplicate it here. This doc covers the **code
structure and risk** angle: what's fragile, where test coverage is still thin
(Payment Page + proration), and the still-open `upgrade_proration_credits` lead.

> **2026-07 update — Stripe is gone, Payment Page is in.** This doc previously
> compared a `stripe_webhook` handler against the Worldline ones. There is no
> longer any Stripe code path: **Worldline is the only provider**, and the
> return handler now settles two different Worldline *interfaces* (Transaction
> vs Payment Page). See "[Payment Page interface](#payment-page-interface-newest)"
> below.

## The two Worldline handlers (no Stripe delegation anymore)

`app/api/routes/billing/webhooks.py` has **two** handlers, both with the
business logic inline in the route (not delegated to a service — this diverges
from the thin-route layering CLAUDE.md describes):

- `worldline_return` (`webhooks.py:38`) — ~375 lines of inline logic: token/
  context extraction, the 15-minute pending-payment expiry check, settling the
  transaction (`assert_payment_page` **or** `authorize_transaction` depending
  on interface — see below), parsing three different possible locations for the
  card alias ID in the response, VAT computation, a manual `PaymentTransaction`
  upsert (two divergent paths depending on whether a `pending_payment` row
  already exists), conditional auto-capture, and finally
  `payment_transactions.apply_successful_payment(...)`.
- `worldline_card_return` (`webhooks.py:417`) — a second, ~100-line handler for
  the card-registration-only (alias) flow, with its own near-duplicate
  implementation of token resolution, pending-token lookup, and cancel/success
  redirect logic.

**Both handlers are plain `def`, not `async def` — and that matters (2026-07
pod-restart fix).** They do fully synchronous work (sync `httpx.Client` calls
plus `wait_for_alias_registration`'s `time.sleep`-based retry loop, up to ~10s).
They were previously `async def` with **zero `await`s**, so that blocking work
ran directly on the event loop and froze *every* concurrent request — including
`/health`. A duplicate card-return replayed the retry storm (~20s), queued
requests hit `duration_ms=45035`, the liveness (5s) / readiness (3s) probes
timed out, and Kubernetes killed the pod. FastAPI runs a plain `def` route in
its threadpool, so the blocking work no longer stalls the loop. **Do not add
`async` back to either handler** unless you also convert every blocking call to
`await`. A companion fix clears the pending alias token on *failure* too
(`webhooks.py:513`), so a retried/cancelled registration takes the fast
`missing_token → cancel_url` path instead of re-running the 10s retry storm.

**Test coverage (corrected).** Both handlers *are* covered — the earlier
"untested" note was a false negative from the static call-graph, which doesn't
see tests that drive the routes over HTTP. `tests/test_billing_routes.py`
exercises them via `TestClient`: `test_worldline_return_authorizes_and_redirects`,
`_ignores_forged_order_reference`, `_rejects_amount_mismatch`,
`_blocks_grant_without_trusted_context`, `_persists_alias_from_checkout_authorize`,
`_persists_alias_from_registration_result`, plus
`test_worldline_card_registration_saves_alias` and
`test_worldline_card_return_saves_alias` (20 tests, all passing). The gaps that
remain are the **Payment Page (`assert_payment_page`) branch** and the
proration path — the existing tests drive the Transaction interface.

**Simplification opportunity, not just a bug risk:** the token-resolution +
pending-lookup logic (`_WORLDLINE_PLACEHOLDER_TOKENS` handling, "no token but
we have an order_reference, look up the pending row" fallback) is near-identical
between the two handlers. A future refactor extracting it into a shared helper
would shrink both and remove a place where they can silently drift apart.

## Payment Page interface (newest)

Worldline is now driven through **two interfaces**, selected per-checkout:

| Interface | When | Settled in webhook via | Why |
|---|---|---|---|
| **Payment Page** (`paymentpage`) | Fresh payment — no saved card alias to charge | `assert_payment_page(token)` | Hosted page offers alternative methods (TWINT / PayPal / Apple Pay), not just cards |
| **Transaction** (`transaction`) | Charging a saved alias (one-click) | `authorize_transaction()` + conditional capture | No method picker; direct alias charge |

**Selection logic** lives in the checkout routes, not the provider. In both
`create_subscription_checkout` (`checkout.py:74`) and `create_topup_checkout`
(`checkout.py:184`):

```python
use_payment_page = (
    bool(payments.settings.worldline_payment_page_enabled)   # feature flag, default off
    and alias is None                                        # no saved card to charge
    and body.provider in {None, "worldline"}
)
```

So Payment Page only kicks in when the global flag is on **and** there is no
resolved alias. A saved-alias charge always stays on the Transaction interface.

**How the interface is threaded through the redirect:** the provider sets
`interface = "paymentpage" if use_payment_page else "transaction"`
(`worldline_provider.py:850` / `:899`) and bakes it into the signed callback
URL. On return, `worldline_return` reads `interface` back out of the callback
context and branches (`webhooks.py:180`):

```python
if interface == "paymentpage":
    result = payments.WorldlineProvider().assert_payment_page(token=token)
else:
    result = payments.WorldlineProvider().authorize_transaction(token=token, ...)
```

`assert_payment_page` returns the **same result shape**
(`Transaction` / `PaymentMeans` / `RegistrationResult`) as
`authorize_transaction`, and Assert yields an already-authorized (often
captured) transaction — so everything downstream (alias save, amount
verification, capture, `apply_successful_payment`) is identical regardless of
interface. That shared shape is the reason the ~375-line handler didn't have to
fork. Worth preserving if either provider method changes.

## Entitlement is now re-derived from the server-trusted pending row

The webhook no longer trusts the return query params for *what was bought*.
When a pending transaction exists, tier / credits / kind are **re-derived from
that row's stored `order_reference`**, not from the (unsigned) callback params
(`webhooks.py:148-173`). If there is neither a pending tx nor a validly signed
context, the handler **refuses to grant any entitlement**
(`billing.worldline_untrusted_grant_blocked`). This closes the earlier hole
where anyone holding a valid token could forge a higher tier or a large credit
grant via crafted query params.

Defense in depth also present: after settlement the handler verifies the amount
Worldline actually authorized covers the expected amount (±1%, currency must be
CHF) before applying, and voids the transaction on a clear mismatch
(`webhooks.py:308-349`).

**Important caveat — this re-derivation does NOT cover proration** (next
section). `upgrade_proration_credits` is read straight off the pending row as
the client set it at checkout; it is not encoded in `order_reference` and is
not recomputed.

## Still open: `upgrade_proration_credits` is client-supplied, unvalidated

This remains the most actionable finding. Trace:

1. `SubscriptionCheckoutRequest.upgrade_proration_credits` (`_shared.py:40`) is
   a plain field on the **incoming request body** — set by the frontend.
2. `create_subscription_checkout` (`checkout.py:110`) stores that value verbatim
   on the new pending `PaymentTransaction` row, with **no server-side
   recomputation or bound-checking** against the org's actual current tier,
   remaining period, or any proration formula.
3. When the webhook confirms the payment, `apply_successful_payment`
   (`payment_transactions.py:376`) reads it back off the row and **grants it as
   real credits**:
   ```python
   proration = payment_tx.upgrade_proration_credits
   if proration and proration > 0:
       credits.grant_credits(db, org_id=payment_tx.org_id, amount=proration, ...)
   ```

Note there **is** a server-side proration calculator —
`POST /subscription/upgrade-proration` (`subscription.py:109`) — but it is
advisory: the frontend calls it to display a number, then sends its own
`upgrade_proration_credits` on the checkout. Nothing forces the granted amount
to equal what that endpoint would compute.

**Payment integrity risk:** nothing stops a client from sending an inflated
`upgrade_proration_credits` on *any* subscription checkout (not just a genuine
upgrade) and receiving that many free credits the moment the (possibly
minimum-amount) payment captures. Whether it's exploitable end-to-end depends
on frontend behavior — but the backend alone does not defend against it. The
fix is to recompute proration server-side at checkout (reuse the
`upgrade-proration` calculator) and ignore the client value, or to bind it into
the signed `order_reference` the way tier/credits already are.

## Other things worth a look while you're in this area

- `worldline_return`'s exception handling special-cases `"TOKEN_INVALID"` /
  `"TRANSACTION_IN_WRONG_STATE"` substring-matching on a `RuntimeError` message
  (`webhooks.py:410`) to decide the payment already went through — string
  matching on an external provider's error text is brittle if Saferpay ever
  changes the wording.
- The duplicate-payment guard (`get_payment_transaction_by_external_id`,
  `webhooks.py:122`) and the 15-minute pending-expiry check (`webhooks.py:127`)
  are good defensive patterns already in place — don't remove them without
  understanding why they're there (double-webhook-delivery and
  abandoned-checkout cases).
