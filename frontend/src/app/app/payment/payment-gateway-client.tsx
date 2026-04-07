"use client";
/**
 * Unified payment gateway page.
 *
 * All subscription and topup checkouts pass through here before being sent to
 * the payment provider.  The page lets the user:
 *   - Review and set their billing address
 *   - Save / replace a card so future checkouts use it automatically
 *   - See that plans renew until cancelled (no lock-in period)
 *   - Proceed to the Worldline / Stripe hosted form
 *
 * URL params (set by the pricing / billing page):
 *   kind          "subscription" | "topup"
 *   tier          plan slug (subscription only)
 *   billing_cycle "monthly" | "yearly"  (subscription only)
 *   credits       number of credits (topup only)
 *   cancel_path   where to return on cancel (default: /app/pricing)
 *   success_path  where to return on success (default: /app/billing)
 */

import { useState, useEffect } from "react";
import { useSearchParams, useRouter } from "next/navigation";
import useSWR from "swr";
import Link from "next/link";
import { CreditCard, Zap, ArrowRight, CheckCircle2, Loader2, Info, ShieldCheck } from "lucide-react";
import {
  fetchCurrentUser,
  fetchBillingSummary,
  createSubscriptionCheckout,
  createTopupCheckout,
  createWorldlineCardRegistration,
  parseBillingAddressJson,
  type BillingAddressPayload,
} from "@/lib/api";
import { AddressBookManager } from "@/components/billing/address-book-manager";
import { creditsToChf } from "@/lib/entitlements";

// ── helpers ───────────────────────────────────────────────────────────────────

function chf(n: number) { return `CHF ${n.toFixed(2)}`; }

// ── component ─────────────────────────────────────────────────────────────────

export function PaymentGatewayClient() {
  const params = useSearchParams();
  const router = useRouter();

  const kind        = params.get("kind") as "subscription" | "topup" | null;
  const tier        = params.get("tier") ?? "";
  const billingCycle= (params.get("billing_cycle") ?? "monthly") as "monthly" | "yearly";
  const credits     = parseInt(params.get("credits") ?? "0");
  const cancelPath  = params.get("cancel_path") ?? "/app/pricing";
  const successPath = params.get("success_path") ?? "/app/billing";

  const { data: me, mutate: mutateMe } = useSWR("me", fetchCurrentUser);
  const { data: summary, mutate: mutateSummary } = useSWR("billing-summary", fetchBillingSummary);

  const billingAddress: BillingAddressPayload | null = parseBillingAddressJson(me?.billing_address_json ?? null);
  const hasSavedCard = Boolean(summary?.has_saved_payment_method);

  const [useNewCard, setUseNewCard] = useState(false);
  const [saveCard, setSaveCard] = useState(true);

  const willUseSavedCard = hasSavedCard && !useNewCard;

  const [loading, setLoading] = useState(false);
  const [cardLoading, setCardLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // If no valid checkout intent, redirect away.
  useEffect(() => {
    if (kind !== "subscription" && kind !== "topup") {
      router.replace("/app/billing");
    }
  }, [kind, router]);

  // ── computed label ────────────────────────────────────────────────────────

  const intentLabel = kind === "subscription"
    ? `${tier ? tier.charAt(0).toUpperCase() + tier.slice(1) : "?"} plan · ${billingCycle}`
    : `${credits.toLocaleString()} credits`;

  const estimatedAmount = kind === "subscription"
    ? null  // fetched server-side
    : kind === "topup"
      ? chf(creditsToChf(credits))
      : null;

  // ── handlers ──────────────────────────────────────────────────────────────

  async function handleProceed() {
    if (!billingAddress) { setError("Add a billing address before proceeding."); return; }
    setLoading(true);
    setError(null);
    try {
      const origin = window.location.origin;
      const successUrl = new URL(successPath, origin);
      successUrl.searchParams.set("checkout", "success");
      if (kind === "subscription") { successUrl.searchParams.set("tier", tier); successUrl.searchParams.set("kind", "subscription"); }
      if (kind === "topup")        { successUrl.searchParams.set("kind", "topup"); successUrl.searchParams.set("credits", String(credits)); }
      const cancelUrl = new URL(cancelPath, origin);
      cancelUrl.searchParams.set("checkout", "cancel");

      if (kind === "subscription") {
        const session = await createSubscriptionCheckout({
          tier,
          billing_cycle: billingCycle,
          success_url: successUrl.toString(),
          cancel_url: cancelUrl.toString(),
          billing_address: billingAddress,
          save_payment_method: willUseSavedCard ? false : saveCard,
        });
        window.location.assign(session.checkout_url);
      } else if (kind === "topup") {
        const session = await createTopupCheckout({
          credits,
          success_url: successUrl.toString(),
          cancel_url: cancelUrl.toString(),
          billing_address: billingAddress,
          save_payment_method: willUseSavedCard ? false : saveCard,
          use_new_card: useNewCard,
        });
        window.location.assign(session.checkout_url);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Checkout failed");
    } finally {
      setLoading(false);
    }
  }

  async function handleSaveCard() {
    if (!billingAddress) { setError("Add a billing address before saving a card."); return; }
    setCardLoading(true);
    setError(null);
    try {
      const origin = window.location.origin;
      // Return back to the same payment page after card registration.
      const successUrl = new URL("/app/payment", origin);
      // Preserve all current params
      params.forEach((v, k) => successUrl.searchParams.set(k, v));
      successUrl.searchParams.set("card_saved", "1");
      const cancelUrl = new URL("/app/payment", origin);
      params.forEach((v, k) => cancelUrl.searchParams.set(k, v));

      const session = await createWorldlineCardRegistration({
        success_url: successUrl.toString(),
        cancel_url: cancelUrl.toString(),
        billing_address: billingAddress,
      });
      window.location.assign(session.checkout_url);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Card registration failed");
    } finally {
      setCardLoading(false);
    }
  }

  // Show success banner after card registration redirect
  const cardSaved = params.get("card_saved") === "1";
  useEffect(() => {
    if (cardSaved) {
      void mutateMe();
      void mutateSummary();
    }
  }, [cardSaved, mutateMe, mutateSummary]);

  if (!kind) return null;

  return (
    <div className="mx-auto max-w-2xl px-4 py-10 space-y-6">

      {/* Header */}
      <div>
        <h1 className="text-2xl font-bold text-slate-900">Payment</h1>
        <p className="text-sm text-slate-500 mt-1">Review your details before proceeding to the payment provider.</p>
      </div>

      {/* Card saved banner */}
      {cardSaved && (
        <div className="rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-800 flex items-center gap-2">
          <CheckCircle2 size={15} className="shrink-0" />
          Card saved successfully. You can now proceed with your saved card.
        </div>
      )}

      {error && (
        <div className="rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
          {error}
        </div>
      )}

      {/* Order summary */}
      <div className="rounded-2xl border border-slate-200 bg-white p-5 space-y-3">
        <h2 className="text-sm font-semibold text-slate-700">Order summary</h2>
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2 text-slate-800">
            {kind === "subscription"
              ? <CreditCard size={15} className="text-blue-500" />
              : <Zap size={15} className="text-amber-400" />
            }
            <span className="font-medium capitalize">{intentLabel}</span>
          </div>
          {estimatedAmount && (
            <span className="text-sm font-semibold text-slate-900">{estimatedAmount}</span>
          )}
        </div>

        {/* Subscription notice */}
        {kind === "subscription" && (
          <div className="flex items-start gap-2 rounded-lg border border-blue-100 bg-blue-50 px-3 py-2.5 text-xs text-blue-700">
            <Info size={13} className="shrink-0 mt-0.5" />
            <span>
              This subscription <strong>renews automatically</strong> each {billingCycle === "yearly" ? "year" : "month"} until you cancel.
              No minimum term — you can cancel at any time from your billing page.
            </span>
          </div>
        )}
      </div>

      {/* Billing address */}
      <div className="rounded-2xl border border-slate-200 bg-white p-5 space-y-3">
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-semibold text-slate-700">Billing address</h2>
          <Link
            href={`/app/addresses?return_to=${encodeURIComponent(`/app/payment?${params.toString()}`)}`}
            className="text-xs text-blue-600 hover:underline"
          >
            Manage addresses
          </Link>
        </div>

        {billingAddress ? (
          <div className="text-sm text-slate-700 space-y-0.5">
            <p className="font-medium">{billingAddress.first_name} {billingAddress.last_name}</p>
            {billingAddress.company_name && <p className="text-slate-500">{billingAddress.company_name}</p>}
            <p>{billingAddress.street} {billingAddress.number}</p>
            <p>{billingAddress.postal_code} {billingAddress.city}</p>
            <p>{billingAddress.country}</p>
          </div>
        ) : (
          <div className="space-y-3">
            <p className="text-sm text-amber-600">No billing address set. Add one to continue.</p>
            <AddressBookManager
              returnTo={`/app/payment?${params.toString()}&resume_address=1`}
            />
          </div>
        )}
      </div>

      {/* Payment method */}
      <div className="rounded-2xl border border-slate-200 bg-white p-5 space-y-3">
        <h2 className="text-sm font-semibold text-slate-700">Payment method</h2>

        {hasSavedCard && !useNewCard ? (
          <div className="flex items-center justify-between rounded-xl border border-emerald-200 bg-emerald-50 px-3 py-2.5">
            <div className="flex items-center gap-2 text-sm text-emerald-700">
              <CreditCard size={14} className="shrink-0" />
              <span className="font-medium">Saved card on file</span>
            </div>
            <button
              onClick={() => setUseNewCard(true)}
              className="text-xs text-blue-600 hover:underline"
            >
              Use a different card
            </button>
          </div>
        ) : (
          <div className="space-y-2">
            {hasSavedCard && (
              <div className="flex items-center justify-between">
                <span className="text-xs text-slate-500">Entering a new card</span>
                <button onClick={() => setUseNewCard(false)} className="text-xs text-blue-600 hover:underline">
                  Use saved card instead
                </button>
              </div>
            )}
            <label className="flex items-center gap-2 text-xs text-slate-600 cursor-pointer select-none">
              <input
                type="checkbox"
                checked={saveCard}
                onChange={e => setSaveCard(e.target.checked)}
                className="h-3.5 w-3.5 rounded border-slate-300 text-blue-600 accent-blue-600"
              />
              Save card for future payments
            </label>
          </div>
        )}

        {/* Save card button (standalone, no payment) */}
        <div className="pt-1 border-t border-slate-100">
          <button
            onClick={() => void handleSaveCard()}
            disabled={cardLoading || !billingAddress}
            className="text-xs text-slate-500 hover:text-slate-700 disabled:opacity-50 flex items-center gap-1"
          >
            {cardLoading ? <Loader2 size={11} className="animate-spin" /> : <CreditCard size={11} />}
            {hasSavedCard ? "Replace saved card" : "Save a card without paying now"}
          </button>
        </div>
      </div>

      {/* Security note */}
      <div className="flex items-center gap-2 text-xs text-slate-400">
        <ShieldCheck size={13} className="shrink-0" />
        Payments are processed securely by Worldline Saferpay. Your card details never touch our servers.
      </div>

      {/* Proceed */}
      <div className="flex items-center justify-between gap-4">
        <Link
          href={cancelPath}
          className="text-sm text-slate-500 hover:underline"
        >
          Cancel
        </Link>
        <button
          onClick={() => void handleProceed()}
          disabled={loading || !billingAddress}
          className="flex items-center gap-2 rounded-xl bg-blue-600 px-6 py-3 text-sm font-semibold text-white shadow hover:bg-blue-700 disabled:opacity-60 transition-colors"
        >
          {loading ? <Loader2 size={15} className="animate-spin" /> : <ArrowRight size={15} />}
          {loading ? "Opening payment…" : "Proceed to payment"}
        </button>
      </div>
    </div>
  );
}
