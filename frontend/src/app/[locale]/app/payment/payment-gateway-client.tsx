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
  fetchPaymentMethods,
  createSubscriptionCheckout,
  createTopupCheckout,
  createWorldlineCardRegistration,
  cancelSubscription,
  scheduleDowngrade,
  claimUpgradeProration,
  parseBillingAddressJson,
  type BillingAddressPayload,
  type UpgradeProration,
  type PaymentMethod,
} from "@/lib/api";
import { AddressBookManager } from "@/components/billing/address-book-manager";
import { creditsToChf } from "@/lib/entitlements";

// ── helpers ───────────────────────────────────────────────────────────────────

const TIER_RANK: Record<string, number> = {
  free: 0, simple: 1, explorer: 2, researcher: 3, strategist: 4, custom: 5,
};

function tierRank(t: string): number { return TIER_RANK[t] ?? 0; }

function chf(n: number) { return `CHF ${n.toFixed(2)}`; }

function addPeriod(date: Date, cycle: "monthly" | "yearly"): Date {
  const d = new Date(date);
  if (cycle === "yearly") { d.setFullYear(d.getFullYear() + 1); }
  else { d.setMonth(d.getMonth() + 1); }
  return d;
}

function fmtDate(d: Date): string {
  return d.toLocaleDateString("en-CH", { day: "numeric", month: "short", year: "numeric" });
}

// ── component ─────────────────────────────────────────────────────────────────

export function PaymentGatewayClient() {
  const params = useSearchParams();
  const router = useRouter();

  const kind        = params?.get("kind") as "subscription" | "topup" | null;
  const tier        = params?.get("tier") ?? "";
  const billingCycle= (params?.get("billing_cycle") ?? "monthly") as "monthly" | "yearly";
  const credits     = parseInt(params?.get("credits") ?? "0");
  const cancelPath  = params?.get("cancel_path") ?? "/app/pricing";
  const successPath = params?.get("success_path") ?? "/app/billing";

  const { data: me, mutate: mutateMe } = useSWR("me", fetchCurrentUser);
  const { data: summary, mutate: mutateSummary } = useSWR("billing-summary", fetchBillingSummary);
  const { data: paymentMethodsData } = useSWR("payment-methods", fetchPaymentMethods);

  const billingAddress: BillingAddressPayload | null = parseBillingAddressJson(me?.billing_address_json ?? null);
  const savedMethods: PaymentMethod[] = paymentMethodsData?.items ?? [];

  // "new" = use a new card; any other value = alias_id of a saved card
  const [selectedCard, setSelectedCard] = useState<string>("new");
  const [saveCard, setSaveCard] = useState(true);
  const [termsAccepted, setTermsAccepted] = useState(false);

  // Auto-select the org default card once methods load
  const hasLoaded = paymentMethodsData !== undefined;
  useEffect(() => {
    if (!hasLoaded) return;
    const def = savedMethods.find(m => m.scope === "org" && m.is_default) ?? savedMethods[0];
    if (def) setSelectedCard(def.alias_id);
  }, [hasLoaded]); // eslint-disable-line react-hooks/exhaustive-deps

  const usingNewCard = selectedCard === "new";

  const [loading, setLoading] = useState(false);
  const [cardLoading, setCardLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [iframeUrl, setIframeUrl] = useState<string | null>(null);

  // Tier-change flow state
  const [showDowngradeChoice, setShowDowngradeChoice] = useState(false);
  const [showDowngradeConfirm, setShowDowngradeConfirm] = useState(false);
  const [downgradeLoading, setDowngradeLoading] = useState(false);
  const [scheduleLoading, setScheduleLoading] = useState(false);
  const [showUpgradeProration, setShowUpgradeProration] = useState(false);
  const [proration, setProration] = useState<UpgradeProration | null>(null);
  const [prorationLoading, setProrationLoading] = useState(false);
  const [prorationClaimed, setProrationClaimed] = useState(false);

  // Derive current tier comparison (only relevant for subscription kind)
  const currentTier = summary?.tier ?? "free";
  const currentTierIsPaid = currentTier !== "free" && !summary?.subscription_cancel_at_period_end;
  const requestedRank = tierRank(tier);
  const currentRank = tierRank(currentTier);
  const isSameTier = kind === "subscription" && currentTierIsPaid && tier === currentTier;
  const isDowngrade = kind === "subscription" && currentTierIsPaid && requestedRank < currentRank;
  const isUpgrade   = kind === "subscription" && currentTierIsPaid && requestedRank > currentRank;

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

  // ── tier-change handlers ───────────────────────────────────────────────────

  async function handleConfirmDowngrade() {
    setDowngradeLoading(true);
    setError(null);
    try {
      await cancelSubscription();
      setShowDowngradeConfirm(false);
      await doCheckout();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to process downgrade");
    } finally {
      setDowngradeLoading(false);
    }
  }

  async function handleScheduleDowngrade() {
    setScheduleLoading(true);
    setError(null);
    try {
      await scheduleDowngrade(tier);
      router.replace(`${successPath}?downgrade_scheduled=1`);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to schedule downgrade");
      setScheduleLoading(false);
    }
  }

  async function handleOpenUpgrade() {
    setProrationLoading(true);
    setError(null);
    try {
      const data = await claimUpgradeProration();
      setProration(data);
      setProrationClaimed(true);
      setShowUpgradeProration(true);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to calculate upgrade credits");
    } finally {
      setProrationLoading(false);
    }
  }

  async function handleConfirmUpgrade() {
    setShowUpgradeProration(false);
    await doCheckout();
  }

  // ── handlers ──────────────────────────────────────────────────────────────

  async function handleProceed() {
    if (isSameTier) { setError("You already have an active subscription to this plan."); return; }
    if (isDowngrade && !showDowngradeChoice && !showDowngradeConfirm) { setShowDowngradeChoice(true); return; }
    if (isUpgrade && !prorationClaimed) { void handleOpenUpgrade(); return; }
    await doCheckout();
  }

  async function doCheckout() {
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
          save_payment_method: usingNewCard ? saveCard : false,
          selected_alias_id: usingNewCard ? null : selectedCard,
          upgrade_proration_credits: proration?.credits_granted ?? null,
        });
        setIframeUrl(session.checkout_url);
      } else if (kind === "topup") {
        const session = await createTopupCheckout({
          credits,
          success_url: successUrl.toString(),
          cancel_url: cancelUrl.toString(),
          billing_address: billingAddress,
          save_payment_method: usingNewCard ? saveCard : false,
          use_new_card: usingNewCard,
          selected_alias_id: usingNewCard ? null : selectedCard,
        });
        setIframeUrl(session.checkout_url);
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
      params?.forEach((v, k) => successUrl.searchParams.set(k, v));
      successUrl.searchParams.set("card_saved", "1");
      const cancelUrl = new URL("/app/payment", origin);
      params?.forEach((v, k) => cancelUrl.searchParams.set(k, v));

      const session = await createWorldlineCardRegistration({
        success_url: successUrl.toString(),
        cancel_url: cancelUrl.toString(),
        billing_address: billingAddress,
      });
      setIframeUrl(session.checkout_url);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Card registration failed");
    } finally {
      setCardLoading(false);
    }
  }

  // Show success banner after card registration redirect
  const cardSaved = params?.get("card_saved") === "1";
  useEffect(() => {
    if (cardSaved) {
      void mutateMe();
      void mutateSummary();
    }
  }, [cardSaved, mutateMe, mutateSummary]);

  if (!kind) return null;

  // Saferpay iframe overlay: shown after checkout session is created.
  // The return URL handler (backend) breaks out of the iframe via JavaScript,
  // so the top-level page navigates to success_url / cancel_url automatically.
  if (iframeUrl) {
    return (
      <div className="fixed inset-0 z-50 flex flex-col bg-white">
        {/* Header bar — your own CSS surrounding the Saferpay iframe */}
        <div className="flex items-center justify-between px-5 py-3 border-b border-slate-200 bg-white shrink-0">
          <div className="flex items-center gap-2 text-sm font-semibold text-slate-800">
            <ShieldCheck size={15} className="text-blue-500 shrink-0" />
            Secure payment — Worldline Saferpay
          </div>
          <button
            onClick={() => { setIframeUrl(null); setLoading(false); }}
            className="text-xs text-slate-500 hover:text-slate-700 underline"
          >
            Cancel
          </button>
        </div>
        <iframe
          src={iframeUrl}
          className="flex-1 w-full border-none"
          title="Saferpay secure payment"
          sandbox="allow-forms allow-scripts allow-same-origin allow-top-navigation allow-popups"
        />
      </div>
    );
  }

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
          Payment method saved successfully. You can now proceed with your saved payment method.
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
        {kind === "subscription" && (() => {
          const startDate = new Date();
          const nextDate = addPeriod(startDate, billingCycle);
          return (
            <div className="space-y-2">
              <div className="grid grid-cols-2 gap-2 text-xs">
                <div className="rounded-lg border border-slate-100 bg-slate-50 px-3 py-2">
                  <div className="text-slate-400 font-medium uppercase tracking-wide text-[10px]">Starts</div>
                  <div className="font-semibold text-slate-700 mt-0.5">{fmtDate(startDate)}</div>
                </div>
                <div className="rounded-lg border border-slate-100 bg-slate-50 px-3 py-2">
                  <div className="text-slate-400 font-medium uppercase tracking-wide text-[10px]">Next billing</div>
                  <div className="font-semibold text-slate-700 mt-0.5">{fmtDate(nextDate)}</div>
                </div>
              </div>
              <div className="flex items-start gap-2 rounded-lg border border-blue-100 bg-blue-50 px-3 py-2.5 text-xs text-blue-700">
                <Info size={13} className="shrink-0 mt-0.5" />
                <span>
                  Renews automatically every {billingCycle === "yearly" ? "year" : "month"} until you cancel.
                  No minimum term — cancel any time from your billing page.
                </span>
              </div>
            </div>
          );
        })()}
      </div>

      {/* Billing address */}
      <div className="rounded-2xl border border-slate-200 bg-white p-5 space-y-3">
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-semibold text-slate-700">Billing address</h2>
          <Link
            href={`/app/addresses?return_to=${encodeURIComponent(`/app/payment?${params?.toString() ?? ""}`)}`}
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
              returnTo={`/app/payment?${params?.toString() ?? ""}&resume_address=1`}
            />
          </div>
        )}
      </div>

      {/* Payment method */}
      <div className="rounded-2xl border border-slate-200 bg-white p-5 space-y-3">
        <h2 className="text-sm font-semibold text-slate-700">Payment method</h2>

        {/* Saved card selector */}
        <div className="space-y-2">
          {savedMethods.map(m => {
            const last4 = m.masked_number ? m.masked_number.slice(-4) : null;
            const brand = m.brand ? m.brand.charAt(0).toUpperCase() + m.brand.slice(1).toLowerCase() : null;
            const scopeLabel = m.scope === "org" ? (m.is_default ? "Org · Default" : "Org") : "Personal";
            const label = [brand, last4 ? `•••• ${last4}` : null, scopeLabel].filter(Boolean).join(" · ");
            const active = selectedCard === m.alias_id;
            return (
              <button
                key={m.id}
                onClick={() => setSelectedCard(m.alias_id)}
                className={`w-full flex items-center gap-2.5 rounded-xl border px-3 py-2.5 text-left transition-colors ${
                  active ? "border-blue-400 bg-blue-50" : "border-slate-200 hover:border-slate-300"
                }`}
              >
                <CreditCard size={14} className={active ? "text-blue-500" : "text-slate-400"} />
                <span className={`text-sm ${active ? "font-medium text-blue-800" : "text-slate-700"}`}>{label}</span>
              </button>
            );
          })}
          <button
            onClick={() => setSelectedCard("new")}
            className={`w-full flex items-center gap-2.5 rounded-xl border px-3 py-2.5 text-left transition-colors ${
              usingNewCard ? "border-blue-400 bg-blue-50" : "border-slate-200 hover:border-slate-300"
            }`}
          >
            <CreditCard size={14} className={usingNewCard ? "text-blue-500" : "text-slate-400"} />
            <span className={`text-sm ${usingNewCard ? "font-medium text-blue-800" : "text-slate-500"}`}>
              Enter new card
            </span>
          </button>
        </div>

        {/* Save checkbox — only when entering a new card */}
        {usingNewCard && (
          <label className="flex items-center gap-2 text-xs text-slate-600 cursor-pointer select-none">
            <input
              type="checkbox"
              checked={saveCard}
              onChange={e => setSaveCard(e.target.checked)}
              className="h-3.5 w-3.5 rounded border-slate-300 text-blue-600 accent-blue-600"
            />
            Save this card for future transactions (optional)
          </label>
        )}

        {/* Standalone save card link */}
        <div className="pt-1 border-t border-slate-100">
          <button
            onClick={() => void handleSaveCard()}
            disabled={cardLoading || !billingAddress}
            className="text-xs text-slate-500 hover:text-slate-700 disabled:opacity-50 flex items-center gap-1"
          >
            {cardLoading ? <Loader2 size={11} className="animate-spin" /> : <CreditCard size={11} />}
            {savedMethods.length > 0 ? "Add another payment method" : "Save a payment method without paying now"}
          </button>
        </div>
      </div>

      {/* Security note */}
      <div className="flex items-center gap-2 text-xs text-slate-400">
        <ShieldCheck size={13} className="shrink-0" />
        Payments are processed securely by Worldline Saferpay. Your card details never reaches our servers.
      </div>

      {/* AGB acceptance */}
      <label className="flex items-start gap-3 cursor-pointer select-none">
        <input
          type="checkbox"
          checked={termsAccepted}
          onChange={e => setTermsAccepted(e.target.checked)}
          className="mt-0.5 h-4 w-4 shrink-0 rounded border-slate-300 text-blue-600 accent-blue-600"
        />
        <span className="text-sm text-slate-700">
          I have read and accepted the{" "}
          <Link href="/agb" target="_blank" className="text-blue-600 underline hover:text-blue-800">
            General Terms and Conditions (AGB)
          </Link>{" "}
          .
        </span>
      </label>

      {/* Same-tier block */}
      {isSameTier && (
        <div className="rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">
          You already have an active <strong className="capitalize">{currentTier}</strong> subscription. To change your plan, cancel your current subscription first or choose a different tier.
        </div>
      )}

      {/* Downgrade: timing choice */}
      {isDowngrade && showDowngradeChoice && !showDowngradeConfirm && (
        <div className="rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 space-y-3">
          <p className="text-sm font-semibold text-amber-900">
            Downgrade to <span className="capitalize">{tier}</span> — when?
          </p>
          <p className="text-xs text-amber-700">
            Your current <strong className="capitalize">{currentTier}</strong> plan is active until{" "}
            <strong>{summary?.subscription_period_end ? fmtDate(new Date(summary.subscription_period_end)) : "the end of your billing period"}</strong>.
          </p>
          {error && <p className="text-xs text-red-600">{error}</p>}
          <div className="flex flex-col sm:flex-row gap-2">
            <button
              onClick={() => { setShowDowngradeChoice(false); setShowDowngradeConfirm(true); }}
              disabled={!termsAccepted}
              className="flex-1 rounded-lg border border-amber-300 bg-white px-3 py-2.5 text-xs font-semibold text-amber-900 hover:bg-amber-100 disabled:opacity-60 text-left space-y-0.5"
            >
              <div>Downgrade immediately</div>
              <div className="font-normal text-amber-700">Cancel now and start the new plan today. No refund for remaining time.</div>
            </button>
            <button
              onClick={() => void handleScheduleDowngrade()}
              disabled={scheduleLoading || !termsAccepted}
              className="flex-1 rounded-lg border border-amber-300 bg-white px-3 py-2.5 text-xs font-semibold text-amber-900 hover:bg-amber-100 disabled:opacity-60 text-left space-y-0.5"
            >
              <div className="flex items-center gap-1.5">
                {scheduleLoading && <Loader2 size={11} className="animate-spin shrink-0" />}
                Downgrade at end of period
              </div>
              <div className="font-normal text-amber-700">
                Keep <span className="capitalize">{currentTier}</span> until{" "}
                {summary?.subscription_period_end ? fmtDate(new Date(summary.subscription_period_end)) : "period end"},
                then switch to <span className="capitalize">{tier}</span> automatically.
              </div>
            </button>
          </div>
          <button
            onClick={() => setShowDowngradeChoice(false)}
            className="text-xs text-amber-700 hover:underline"
          >
            Keep current plan
          </button>
        </div>
      )}

      {/* Downgrade: immediate confirmation */}
      {isDowngrade && showDowngradeConfirm && (
        <div className="rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 space-y-2">
          <p className="text-sm font-semibold text-amber-900">Downgrade to <span className="capitalize">{tier}</span> immediately?</p>
          <p className="text-xs text-amber-800">
            Your current <strong className="capitalize">{currentTier}</strong> plan will be cancelled and stays active until{" "}
            <strong>{summary?.subscription_period_end ? fmtDate(new Date(summary.subscription_period_end)) : "the end of your billing period"}</strong>.
            The new <strong className="capitalize">{tier}</strong> plan starts immediately. No refund is issued for remaining time.
          </p>
          {error && <p className="text-xs text-red-600">{error}</p>}
          <div className="flex gap-2">
            <button
              onClick={() => void handleConfirmDowngrade()}
              disabled={downgradeLoading || !termsAccepted}
              className="rounded-lg bg-amber-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-amber-700 disabled:opacity-60"
            >
              {downgradeLoading ? "Processing…" : `Yes, switch to ${tier.charAt(0).toUpperCase() + tier.slice(1)} now`}
            </button>
            <button
              onClick={() => { setShowDowngradeConfirm(false); setShowDowngradeChoice(true); }}
              className="rounded-lg border border-amber-200 px-3 py-1.5 text-xs text-amber-800 hover:bg-amber-100"
            >
              Back
            </button>
          </div>
        </div>
      )}

      {/* Upgrade proration modal */}
      {isUpgrade && showUpgradeProration && proration && (
        <div className="rounded-xl border border-blue-200 bg-blue-50 px-4 py-3 space-y-2">
          <p className="text-sm font-semibold text-blue-900">Upgrade to {tier.charAt(0).toUpperCase() + tier.slice(1)}?</p>
          <p className="text-xs text-blue-800">
            You have <strong>{proration.remaining_days} days</strong> remaining on your current{" "}
            <strong className="capitalize">{currentTier}</strong> plan (CHF {proration.plan_cost_chf.toFixed(2)}/cycle).
            As a thank-you for upgrading, we&apos;ll credit your account with{" "}
            <strong>{proration.credits_granted.toLocaleString()} credits (≈ CHF {proration.credits_chf.toFixed(4)})</strong>.
          </p>
          <p className="text-xs text-blue-700 font-medium">
            These credits have already been added to your balance. Proceed to pay for your new plan.
          </p>
          {error && <p className="text-xs text-red-600">{error}</p>}
          <div className="flex gap-2">
            <button
              onClick={() => void handleConfirmUpgrade()}
              disabled={loading || !termsAccepted}
              className="flex items-center gap-1.5 rounded-lg bg-blue-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-blue-700 disabled:opacity-60"
            >
              {loading ? <Loader2 size={11} className="animate-spin" /> : <ArrowRight size={11} />}
              {loading ? "Opening payment…" : "Proceed to payment"}
            </button>
            <button
              onClick={() => setShowUpgradeProration(false)}
              className="rounded-lg border border-blue-200 px-3 py-1.5 text-xs text-blue-800 hover:bg-blue-100"
            >
              Cancel
            </button>
          </div>
        </div>
      )}

      {/* Proceed */}
      {!isSameTier && !showDowngradeChoice && !showDowngradeConfirm && !showUpgradeProration && (
        <div className="flex items-center justify-between gap-4">
          <Link
            href={cancelPath}
            className="text-sm text-slate-500 hover:underline"
          >
            Cancel
          </Link>
          <button
            onClick={() => void handleProceed()}
            disabled={loading || prorationLoading || !billingAddress || !termsAccepted}
            className="flex items-center gap-2 rounded-xl bg-blue-600 px-6 py-3 text-sm font-semibold text-white shadow hover:bg-blue-700 disabled:opacity-60 transition-colors"
          >
            {(loading || prorationLoading) ? <Loader2 size={15} className="animate-spin" /> : <ArrowRight size={15} />}
            {loading ? "Opening payment…" : prorationLoading ? "Calculating credits…" : "Proceed to payment"}
          </button>
        </div>
      )}

      {/* Cancel link when a modal is open */}
      {(showDowngradeChoice || showDowngradeConfirm || showUpgradeProration) && (
        <Link href={cancelPath} className="text-sm text-slate-500 hover:underline">
          Cancel
        </Link>
      )}
    </div>
  );
}
