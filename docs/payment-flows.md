# Payment Flows — Saferpay/Worldline

This document covers every payment path, the exact Saferpay API calls made, the fields in each request/response, and what is stored in the database.

---

## Data model

| Location | Column | What is stored |
|---|---|---|
| `users.payment_customer_id` | TEXT | Saferpay **Alias ID** — UUID created by `Alias/Insert` or `RegisterAlias` |
| `users.payment_card_info_json` | TEXT (JSON) | Masked card metadata: `masked_number`, `brand`, `holder_name`, `exp_year`, `exp_month` |
| `organizations.default_payment_user_id` | FK → users | Which org member's alias is used by default for topups |
| `organizations.recurring_transaction_id` | TEXT | Saferpay **Transaction ID** from the last successful subscription charge — used for `AuthorizeReferenced` (recurring billing only) |

---

## Flow A — Standalone card registration (no payment)

**Trigger:** `POST /billing/payment-methods/worldline/register`
**Purpose:** Save a card without charging it.

```
Frontend
  └─► POST /billing/payment-methods/worldline/register
        │
        ├─► Saferpay  POST /Payment/v1/Alias/Insert
        │     Request fields:
        │       RequestHeader.CustomerId, RequestId, SpecVersion, RetryIndicator
        │       RegisterAlias.IdGenerator = "RANDOM"
        │       Type = "CARD"
        │       ReturnUrl.Url  (includes signed ctx + "&TOKEN={TOKEN}")
        │       Notification.Url
        │       LanguageCode = "en"
        │
        │     Response fields used:
        │       Token   → stored in _pending_card_alias_tokens[order_reference]
        │       RedirectUrl → returned to frontend as checkout_url
        │
        └─► Background thread: _poll_worldline_alias_registration()
              polls Saferpay  POST /Payment/v1/Alias/AssertInsert
                Request: Token
                Response fields used:
                  Alias.Id                              → users.payment_customer_id
                  PaymentMeans.Card.MaskedNumber        ┐
                  PaymentMeans.Card.Brand               │ → users.payment_card_info_json
                  PaymentMeans.Card.HolderName          │
                  PaymentMeans.Card.ExpYear / ExpMonth  ┘

Customer fills card in Saferpay-hosted form → redirected back to:
  GET /billing/webhooks/worldline/card/return?TOKEN=<token>&ctx=<signed>
    │
    ├─► Saferpay  POST /Payment/v1/Alias/AssertInsert   (same as poll above)
    │     saves alias + card info immediately
    │
    └─► If no org.default_payment_user_id set → sets it to this user
```

**What appears in Saferpay backoffice:** The alias appears in **Secure Alias Store** under the customer ID. This is the only flow that creates a standalone, visible alias.

---

## Flow B — Topup or subscription (no saved card / new card)

**Trigger:** `POST /billing/checkout/topup` or `POST /billing/checkout/subscription` with no saved alias, or `use_new_card=true`

```
Frontend
  └─► POST /billing/checkout/topup  { save_payment_method: true/false }
        │
        ├─► Saferpay  POST /Payment/v1/Transaction/Initialize
        │     Request fields:
        │       RequestHeader.*
        │       TerminalId
        │       Payment.Amount.Value (CHF * 100, integer string)
        │       Payment.Amount.CurrencyCode = "CHF"
        │       Payment.OrderId              ← order_reference e.g. wl_topup_3_11_10000_<nonce>
        │       Payment.Description
        │       Payment.Recurring.Initial = true   (subscriptions only)
        │       Payer.Language = "en"
        │       Payer.FirstName / LastName / Address.*   (from billing_address)
        │       ReturnUrl.Url               (signed ctx, no {TOKEN} placeholder)
        │       RedirectNotifyUrls.Success / Fail
        │       Styling.CssUrl              (optional)
        │       ── NO PaymentMeans field ──
        │
        │     Response fields used:
        │       Token       → stored as payment_transactions.external_id
        │       RedirectUrl → returned to frontend as checkout_url
        │
        └─► Customer pays in Saferpay-hosted form → redirected back to:
              GET /billing/webhooks/worldline/return?ctx=<signed>&source=return
                │
                ├─► Saferpay  POST /Payment/v1/Transaction/Authorize
                │     Request fields:
                │       Token  (from ctx or query param)
                │       RegisterAlias.IdGenerator = "RANDOM"   ← only if save_payment_method=true
                │       RegisterAlias.Type = "CARD"
                │
                │     Response fields used:
                │       Transaction.Id      → payment_transactions.provider_transaction_id
                │       Transaction.Status  → normalized to authorized/captured/declined
                │       PaymentMeans.Card.Alias.Id          ┐  alias, if RegisterAlias was sent
                │       RegistrationResult.Alias.Id         ┤  (checked in this order)
                │       Alias.Id (top-level fallback)        ┘  → users.payment_customer_id
                │       PaymentMeans.Card.*                 → users.payment_card_info_json
                │       PaymentMeans.Card.HolderName / DisplayText
                │
                ├─► If status = AUTHORIZED:
                │     Saferpay  POST /Payment/v1/Transaction/Capture
                │       Request: TransactionReference.TransactionId
                │       → payment_transactions.status = "captured"
                │
                ├─► If subscription + captured:
                │     org.recurring_transaction_id = Transaction.Id
                │
                └─► If alias saved + no org default:
                      org.default_payment_user_id = user.id
```

**What appears in Saferpay backoffice:**
- The transaction appears in **Transactions**.
- If `RegisterAlias` was sent and the user completed the flow, an alias *should* appear in **Secure Alias Store**. In the test environment this often does NOT appear because Saferpay test cards do not always trigger alias creation via `RegisterAlias` — use Flow A (standalone registration) for reliable alias creation.

---

## Flow C — Topup using a saved alias

**Trigger:** `POST /billing/checkout/topup` when `users.payment_customer_id` is set and `use_new_card=false`

```
Frontend
  └─► POST /billing/checkout/topup  { save_payment_method: true/false, use_new_card: false }
        │
        ├─► _resolve_worldline_payment_alias()
        │     checks current_user.payment_customer_id first
        │     falls back to org.default_payment_user_id → that user's payment_customer_id
        │
        └─► Saferpay  POST /Payment/v1/Transaction/Initialize
              Request fields (same as Flow B, PLUS):
                PaymentMeans.Alias.Id = <alias_id>   ← pre-selects the saved card
                ── NOT PaymentMeans.Card.Alias.Id ──  (that field is output-only)

              The customer still sees the Saferpay payment form, but with the
              card pre-filled. They may need to re-enter CVV depending on
              Saferpay configuration.

              → remainder of flow identical to Flow B
```

**What appears in Saferpay backoffice:** Same as Flow B — a transaction. The alias is consumed (not modified) but remains in the Alias Store for future use.

---

## Flow D — Recurring subscription billing (no customer interaction)

**Trigger:** Nightly `billing_renewal` cron job

```
billing_renewal job
  └─► Saferpay  POST /Payment/v1/Transaction/AuthorizeReferenced
        Request fields:
          RequestHeader.*
          TerminalId
          Payment.Amount.Value / CurrencyCode / OrderId / Description
          TransactionReference.TransactionId  ← org.recurring_transaction_id
                                                (the captured tx from the original subscription payment)

        Response fields used:
          Transaction.Id     → new provider_transaction_id
          Transaction.Status → must be AUTHORIZED for capture to proceed

        If AUTHORIZED:
          Saferpay  POST /Payment/v1/Transaction/Capture
            → new transaction captured, credited to org
```

**What appears in Saferpay backoffice:** A new transaction each billing cycle, linked to the original by the reference. No alias involved at all — this uses a **transaction reference**, not a card alias.

---

## Flow E — Manual transaction cancel

**Trigger:** `POST /billing/payments/{id}/cancel`

```
  └─► Saferpay  POST /Payment/v1/Transaction/Cancel
        Request: TransactionReference.TransactionId  ← provider_transaction_id
        → payment_transactions.status = "declined", error_code = "MANUAL_CANCELLED"
```

---

## Why the alias may not appear in the Saferpay backoffice

| Scenario | Cause |
|---|---|
| Used Flow B with `save_payment_method=true` | `RegisterAlias` in test environment is unreliable — test cards often don't create an alias entry. Use Flow A instead. |
| Used Flow D (recurring) | No alias is used or created — it uses `TransactionReference`, not an alias. |
| Alias was stored in DB but Saferpay rejected it | The prior `Transaction/Authorize` may have failed silently before the alias was created on Saferpay's side, but the code still found an alias ID in the response. |
| Old alias format bug | Before the fix in commit `5d67583`, `PaymentMeans.Card.Alias.Id` was sent (wrong); Saferpay rejected the Initialize call before the alias was ever used. The alias ID stored in `users.payment_customer_id` may still be valid — it just could never be used. |

## Recommendation for reliable saved-card flow

1. Use **Flow A** (`POST /billing/payment-methods/worldline/register`) for standalone card registration — this is the only flow that reliably creates a visible alias in the Saferpay Secure Alias Store.
2. Clear any existing `users.payment_customer_id` that was stored via `RegisterAlias` in test mode and re-register via Flow A.
3. Once a real alias exists, **Flow C** (topup with saved card) works correctly with the `PaymentMeans.Alias.Id` fix now in place.
