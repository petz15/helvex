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
import { paymentMethodIcon, paymentMethodLabel } from "@/lib/payment-method-display";
import {
  fetchCurrentUser,
  fetchBillingSummary,
  fetchPaymentMethods,
  fetchOrg,
  fetchBillingTiers,
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

// EU standard VAT rates (%) by ISO 3166-1 alpha-2 — mirrors app/data/eu_vat_rates.json
const EU_VAT_STANDARD: Record<string, number> = {
  AT:20, BE:21, BG:20, CY:19, CZ:21, DK:25, DE:19, EE:24, GR:24,
  ES:21, FI:25.5, FR:20, HR:25, HU:27, IE:23, IT:22, LV:21, LT:21,
  LU:17, MT:18, NL:21, PL:23, PT:23, RO:19, SI:22, SK:23, SE:25,
};

function computeVat(country: string | undefined): { rate: number; pct: number } {
  const c = (country ?? "").toUpperCase();
  if (c === "CH") return { rate: 0.081, pct: 8.1 };
  const euRate = EU_VAT_STANDARD[c];
  if (euRate !== undefined) return { rate: euRate / 100, pct: euRate };
  return { rate: 0.081, pct: 8.1 }; // unknown origin → CH fallback
}
import { AddressBookManager } from "@/components/billing/address-book-manager";
import { creditsToChf } from "@/lib/entitlements";
import { useI18n } from "@/i18n/context";

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
  const { dict } = useI18n();
  const t = dict.app.paymentGateway;

  const kind        = params?.get("kind") as "subscription" | "topup" | null;
  const tier        = params?.get("tier") ?? "";
  const billingCycle= (params?.get("billing_cycle") ?? "monthly") as "monthly" | "yearly";
  const credits     = parseInt(params?.get("credits") ?? "0");
  const cancelPath  = params?.get("cancel_path") ?? "/app/pricing";
  const successPath = params?.get("success_path") ?? "/app/billing";

  const { data: me, mutate: mutateMe } = useSWR("me", fetchCurrentUser);
  const { data: summary, mutate: mutateSummary } = useSWR("billing-summary", fetchBillingSummary);
  const { data: paymentMethodsData } = useSWR("payment-methods", fetchPaymentMethods);
  const { data: tiers } = useSWR("billing-tiers", fetchBillingTiers);
  const { data: org } = useSWR(
    me?.org_id ? `org-${me.org_id}` : null,
    () => fetchOrg(me!.org_id!),
  );

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

  const tierData = tiers?.find(t => t.slug === tier);

  const vat = computeVat(billingAddress?.country);
  const baseAmount = kind === "topup" ? creditsToChf(credits) : null;
  const vatAmount  = baseAmount !== null ? Math.round(baseAmount * vat.rate * 10000) / 10000 : null;
  const totalAmount = baseAmount !== null ? baseAmount + (vatAmount ?? 0) : null;

  const baseSubAmount = kind === "subscription" && tierData != null
    ? (billingCycle === "yearly" ? tierData.yearly_price_chf : tierData.monthly_price_chf)
    : null;
  const vatSubAmount = baseSubAmount !== null ? Math.round(baseSubAmount * vat.rate * 10000) / 10000 : null;
  const totalSubAmount = baseSubAmount !== null ? baseSubAmount + (vatSubAmount ?? 0) : null;

  // ── tier-change handlers ───────────────────────────────────────────────────

  async function handleConfirmDowngrade() {
    setDowngradeLoading(true);
    setError(null);
    try {
      await cancelSubscription();
      setShowDowngradeConfirm(false);
      await doCheckout();
    } catch (e) {
      setError(e instanceof Error ? e.message : t.downgradeFailed);
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
      setError(e instanceof Error ? e.message : t.scheduleFailed);
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
      setError(e instanceof Error ? e.message : t.prorationFailed);
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
    if (isSameTier) { setError(t.alreadySubscribed); return; }
    if (isDowngrade && !showDowngradeChoice && !showDowngradeConfirm) { setShowDowngradeChoice(true); return; }
    if (isUpgrade && !prorationClaimed) { void handleOpenUpgrade(); return; }
    await doCheckout();
  }

  async function doCheckout() {
    if (!billingAddress) { setError(t.addAddressFirst); return; }
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
          use_new_card: usingNewCard,
          selected_alias_id: usingNewCard ? null : selectedCard,
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
      setError(e instanceof Error ? e.message : t.checkoutFailed);
    } finally {
      setLoading(false);
    }
  }

  async function handleSaveCard() {
    if (!billingAddress) { setError(t.addAddressBeforeCard); return; }
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
      setError(e instanceof Error ? e.message : t.cardRegFailed);
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
            {t.securePayment}
          </div>
          <button
            onClick={() => { setIframeUrl(null); setLoading(false); }}
            className="text-xs text-slate-500 hover:text-slate-700 underline"
          >
            {t.cancel}
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
        <h1 className="text-2xl font-bold text-slate-900">{t.title}</h1>
        <p className="text-sm text-slate-500 mt-1">{t.subtitle}</p>
      </div>

      {/* Card saved banner */}
      {cardSaved && (
        <div className="rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-800 flex items-center gap-2">
          <CheckCircle2 size={15} className="shrink-0" />
          {t.cardSavedBanner}
        </div>
      )}

      {error && (
        <div className="rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
          {error}
        </div>
      )}

      {/* Order summary */}
      <div className="rounded-2xl border border-slate-200 bg-white p-5 space-y-3">
        <h2 className="text-sm font-semibold text-slate-700">{t.orderSummary}</h2>
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2 text-slate-800">
            {kind === "subscription"
              ? <CreditCard size={15} className="text-blue-500" />
              : <Zap size={15} className="text-amber-400" />
            }
            <span className="font-medium capitalize">{intentLabel}</span>
          </div>
          {kind === "topup" && baseAmount !== null && (
            <span className="text-sm text-slate-500">{chf(baseAmount)}</span>
          )}
          {kind === "subscription" && totalSubAmount !== null && (
            <span className="text-sm text-slate-500">{chf(baseSubAmount ?? 0)}</span>
          )}
        </div>

        {/* VAT breakdown */}
        {kind === "topup" && baseAmount !== null && (
          <div className="border-t border-slate-100 pt-2 space-y-1.5 text-xs">
            <div className="flex justify-between text-slate-500">
              <span>{dict.app.billing.payment.vatSubtotal}</span>
              <span>{chf(baseAmount)}</span>
            </div>
            <div className="flex justify-between text-slate-500">
              <span>{dict.app.billing.payment.vatLine.replace("{pct}", String(vat.pct))}</span>
              <span>{chf(vatAmount ?? 0)}</span>
            </div>
            <div className="flex justify-between font-semibold text-slate-900">
              <span>{dict.app.billing.payment.vatTotal}</span>
              <span>{totalAmount !== null ? chf(totalAmount) : "—"}</span>
            </div>
          </div>
        )}
        {kind === "subscription" && baseSubAmount !== null && (
          <div className="border-t border-slate-100 pt-2 space-y-1.5 text-xs">
            <div className="flex justify-between text-slate-500">
              <span>{dict.app.billing.payment.vatSubtotal}</span>
              <span>{chf(baseSubAmount)}</span>
            </div>
            <div className="flex justify-between text-slate-500">
              <span>{dict.app.billing.payment.vatLine.replace("{pct}", String(vat.pct))}</span>
              <span>{chf(vatSubAmount ?? 0)}</span>
            </div>
            <div className="flex justify-between font-semibold text-slate-900">
              <span>{dict.app.billing.payment.vatTotal}</span>
              <span>{totalSubAmount !== null ? chf(totalSubAmount) : "—"}</span>
            </div>
          </div>
        )}

        {/* Subscription notice */}
        {kind === "subscription" && (() => {
          const startDate = new Date();
          const nextDate = addPeriod(startDate, billingCycle);
          return (
            <div className="space-y-2">
              <div className="grid grid-cols-2 gap-2 text-xs">
                <div className="rounded-lg border border-slate-100 bg-slate-50 px-3 py-2">
                  <div className="text-slate-400 font-medium uppercase tracking-wide text-[10px]">{t.starts}</div>
                  <div className="font-semibold text-slate-700 mt-0.5">{fmtDate(startDate)}</div>
                </div>
                <div className="rounded-lg border border-slate-100 bg-slate-50 px-3 py-2">
                  <div className="text-slate-400 font-medium uppercase tracking-wide text-[10px]">{t.nextBilling}</div>
                  <div className="font-semibold text-slate-700 mt-0.5">{fmtDate(nextDate)}</div>
                </div>
              </div>
              <div className="flex items-start gap-2 rounded-lg border border-blue-100 bg-blue-50 px-3 py-2.5 text-xs text-blue-700">
                <Info size={13} className="shrink-0 mt-0.5" />
                <span>
                  {t.renewsNote.replace("{period}", billingCycle === "yearly" ? t.year : t.month)}
                </span>
              </div>
            </div>
          );
        })()}
      </div>

      {/* Billing address */}
      <div className="rounded-2xl border border-slate-200 bg-white p-5 space-y-3">
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-semibold text-slate-700">{t.billingAddress}</h2>
          <Link
            href={`/app/addresses?return_to=${encodeURIComponent(`/app/payment?${params?.toString() ?? ""}`)}`}
            className="text-xs text-blue-600 hover:underline"
          >
            {t.manageAddresses}
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
            <p className="text-sm text-amber-600">{t.noAddressSet}</p>
            <AddressBookManager
              returnTo={`/app/payment?${params?.toString() ?? ""}&resume_address=1`}
            />
          </div>
        )}
      </div>

      {/* Payment method */}
      <div className="rounded-2xl border border-slate-200 bg-white p-5 space-y-3">
        <h2 className="text-sm font-semibold text-slate-700">{t.paymentMethod}</h2>

        {/* Saved payment method selector */}
        <div className="space-y-2">
          {savedMethods.map(m => {
            const scopeLabel = m.scope === "org" ? (m.is_default ? t.orgDefault : t.org) : t.personal;
            const label = [paymentMethodLabel(m, t.savedPaymentMethod), scopeLabel].filter(Boolean).join(" · ");
            const Icon = paymentMethodIcon(m.method_type);
            const active = selectedCard === m.alias_id;
            return (
              <button
                key={m.id}
                onClick={() => setSelectedCard(m.alias_id)}
                className={`w-full flex items-center gap-2.5 rounded-xl border px-3 py-2.5 text-left transition-colors ${
                  active ? "border-blue-400 bg-blue-50" : "border-slate-200 hover:border-slate-300"
                }`}
              >
                <Icon size={14} className={active ? "text-blue-500" : "text-slate-400"} />
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
              {t.newPaymentMethod}
            </span>
          </button>
        </div>

        {/* Save checkbox — only when entering a new payment method */}
        {usingNewCard && (
          <label className="flex items-center gap-2 text-xs text-slate-600 cursor-pointer select-none">
            <input
              type="checkbox"
              checked={saveCard}
              onChange={e => setSaveCard(e.target.checked)}
              className="h-3.5 w-3.5 rounded border-slate-300 text-blue-600 accent-blue-600"
            />
            {t.savePaymentMethodOptional}
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
            {savedMethods.length > 0 ? t.addAnotherMethod : t.saveMethodNoPay}
          </button>
        </div>
      </div>

      {/* Security note */}
      <div className="flex items-center gap-2 text-xs text-slate-400">
        <ShieldCheck size={13} className="shrink-0" />
        {t.securityNote}
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
          {t.agbAccept}{" "}
          <Link href="/agb" target="_blank" className="text-blue-600 underline hover:text-blue-800">
            {t.agbLink}
          </Link>{" "}
          .
        </span>
      </label>

      {/* Same-tier block */}
      {isSameTier && (
        <div className="rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">
          {t.alreadyActiveSub.replace("{tier}", currentTier)}
        </div>
      )}

      {/* Downgrade: timing choice */}
      {isDowngrade && showDowngradeChoice && !showDowngradeConfirm && (
        <div className="rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 space-y-3">
          <p className="text-sm font-semibold text-amber-900">
            {t.downgradeTo.replace("{tier}", tier)}
          </p>
          <p className="text-xs text-amber-700">
            {t.planActiveUntil.replace("{tier}", currentTier)}{" "}
            <strong>{summary?.subscription_period_end ? fmtDate(new Date(summary.subscription_period_end)) : t.endOfBillingPeriod}</strong>.
          </p>
          {error && <p className="text-xs text-red-600">{error}</p>}
          <div className="flex flex-col sm:flex-row gap-2">
            <button
              onClick={() => { setShowDowngradeChoice(false); setShowDowngradeConfirm(true); }}
              disabled={!termsAccepted}
              className="flex-1 rounded-lg border border-amber-300 bg-white px-3 py-2.5 text-xs font-semibold text-amber-900 hover:bg-amber-100 disabled:opacity-60 text-left space-y-0.5"
            >
              <div>{t.downgradeImmediately}</div>
              <div className="font-normal text-amber-700">{t.downgradeImmediatelyDesc}</div>
            </button>
            <button
              onClick={() => void handleScheduleDowngrade()}
              disabled={scheduleLoading || !termsAccepted}
              className="flex-1 rounded-lg border border-amber-300 bg-white px-3 py-2.5 text-xs font-semibold text-amber-900 hover:bg-amber-100 disabled:opacity-60 text-left space-y-0.5"
            >
              <div className="flex items-center gap-1.5">
                {scheduleLoading && <Loader2 size={11} className="animate-spin shrink-0" />}
                {t.downgradeAtPeriodEnd}
              </div>
              <div className="font-normal text-amber-700">
                {t.keepUntil.replace("{tier}", currentTier)}{" "}
                {summary?.subscription_period_end ? fmtDate(new Date(summary.subscription_period_end)) : t.endOfBillingPeriod},{" "}
                {t.switchAutomatically.replace("{tier}", tier)}
              </div>
            </button>
          </div>
          <button
            onClick={() => setShowDowngradeChoice(false)}
            className="text-xs text-amber-700 hover:underline"
          >
            {t.keepCurrentPlan}
          </button>
        </div>
      )}

      {/* Downgrade: immediate confirmation */}
      {isDowngrade && showDowngradeConfirm && (
        <div className="rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 space-y-2">
          <p className="text-sm font-semibold text-amber-900">{t.downgradeImmediatelyQ.replace("{tier}", tier)}</p>
          <p className="text-xs text-amber-800">
            {t.planWillCancel.replace("{tier}", currentTier)}{" "}
            <strong>{summary?.subscription_period_end ? fmtDate(new Date(summary.subscription_period_end)) : t.endOfBillingPeriod}</strong>.
            {" "}{t.newPlanStarts.replace("{tier}", tier)}
          </p>
          {error && <p className="text-xs text-red-600">{error}</p>}
          <div className="flex gap-2">
            <button
              onClick={() => void handleConfirmDowngrade()}
              disabled={downgradeLoading || !termsAccepted}
              className="rounded-lg bg-amber-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-amber-700 disabled:opacity-60"
            >
              {downgradeLoading ? t.processing : t.yesSwitchNow.replace("{tier}", tier.charAt(0).toUpperCase() + tier.slice(1))}
            </button>
            <button
              onClick={() => { setShowDowngradeConfirm(false); setShowDowngradeChoice(true); }}
              className="rounded-lg border border-amber-200 px-3 py-1.5 text-xs text-amber-800 hover:bg-amber-100"
            >
              {t.back}
            </button>
          </div>
        </div>
      )}

      {/* Upgrade proration modal */}
      {isUpgrade && showUpgradeProration && proration && (
        <div className="rounded-xl border border-blue-200 bg-blue-50 px-4 py-3 space-y-2">
          <p className="text-sm font-semibold text-blue-900">{t.upgradeTo.replace("{tier}", tier.charAt(0).toUpperCase() + tier.slice(1))}</p>
          <p className="text-xs text-blue-800">
            {t.remainingDays.replace("{days}", String(proration.remaining_days)).replace("{tier}", currentTier).replace("{cost}", proration.plan_cost_chf.toFixed(2))}
            {" "}{t.thankYouCredit}{" "}
            <strong>{proration.credits_granted.toLocaleString()} {t.creditsApprox.replace("{chf}", proration.credits_chf.toFixed(4))}</strong>.
          </p>
          <p className="text-xs text-blue-700 font-medium">
            {t.creditsAlreadyAdded}
          </p>
          {error && <p className="text-xs text-red-600">{error}</p>}
          <div className="flex gap-2">
            <button
              onClick={() => void handleConfirmUpgrade()}
              disabled={loading || !termsAccepted}
              className="flex items-center gap-1.5 rounded-lg bg-blue-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-blue-700 disabled:opacity-60"
            >
              {loading ? <Loader2 size={11} className="animate-spin" /> : <ArrowRight size={11} />}
              {loading ? t.openingPayment : t.proceedToPayment}
            </button>
            <button
              onClick={() => setShowUpgradeProration(false)}
              className="rounded-lg border border-blue-200 px-3 py-1.5 text-xs text-blue-800 hover:bg-blue-100"
            >
              {t.cancel}
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
            {t.cancel}
          </Link>
          <button
            onClick={() => void handleProceed()}
            disabled={loading || prorationLoading || !billingAddress || !termsAccepted}
            className="flex items-center gap-2 rounded-xl bg-blue-600 px-6 py-3 text-sm font-semibold text-white shadow hover:bg-blue-700 disabled:opacity-60 transition-colors"
          >
            {(loading || prorationLoading) ? <Loader2 size={15} className="animate-spin" /> : <ArrowRight size={15} />}
            {loading ? t.openingPayment : prorationLoading ? t.calculatingCredits : t.proceedToPayment}
          </button>
        </div>
      )}

      {/* Cancel link when a modal is open */}
      {(showDowngradeChoice || showDowngradeConfirm || showUpgradeProration) && (
        <Link href={cancelPath} className="text-sm text-slate-500 hover:underline">
          {t.cancel}
        </Link>
      )}
    </div>
  );
}
