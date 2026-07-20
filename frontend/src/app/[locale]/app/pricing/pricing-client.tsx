"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import useSWR from "swr";
import {
  createSubscriptionCheckout,
  fetchCurrentUser,
} from "@/lib/api";
import { CREDIT_ACTIONS, PRICING_TIERS, creditsToChf } from "@/lib/marketing-data";
import { PlanComparisonTable } from "@/components/plan-comparison-table";
import { useI18n } from "@/i18n/context";

// ── Data ──────────────────────────────────────────────────────────────────────

const TIERS = PRICING_TIERS;

type TierId = typeof TIERS[number]["id"];

const TIER_BONUS_RATE: Record<TierId, number> = {
  free: 0,
  simple: 0.05,
  explorer: 0.10,
  researcher: 0.15,
  strategist: 0.20,
};

// ── Main component ─────────────────────────────────────────────────────────────

export function PricingClient() {
  const { dict } = useI18n();
  const t = dict.app.pricing;
  const [yearly, setYearly] = useState(false);
  const [creditTier, setCreditTier] = useState<TierId>("explorer");
  const [checkoutLoading, setCheckoutLoading] = useState<string | null>(null);
  const [checkoutMessage, setCheckoutMessage] = useState<{ kind: "success" | "error"; message: string } | null>(null);
  const { data: me } = useSWR("me", fetchCurrentUser);
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const checkout = params.get("checkout");
    const tier = params.get("tier");
    const reason = params.get("reason");
    const alreadyProcessed = params.get("already_processed") === "true";
    if (!checkout && !alreadyProcessed && !reason) return;
    if (alreadyProcessed) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setCheckoutMessage({ kind: "success", message: t.alreadyProcessed });
    } else if (checkout === "cancel") {
      setCheckoutMessage({
        kind: "error",
        message: tier ? t.checkoutCancelledTier.replace("{tier}", tier) : reason === "payment_declined" ? t.paymentDeclined : t.checkoutCancelled,
      });
    } else if (checkout === "success") {
      setCheckoutMessage({ kind: "success", message: tier ? t.checkoutCompletedTier.replace("{tier}", tier) : t.checkoutCompleted });
    }
    window.history.replaceState({}, "", window.location.pathname);
  }, []);

  // Custom configurator state
  const [webMonths, setWebMonths] = useState(0);
  const [export100k, setExport100k] = useState(false);
  const [bonusSteps, setBonusSteps] = useState(0);
  const [priority, setPriority] = useState(0);
  const [immediateLlm, setImmediateLlm] = useState(false);
  const [byoKeys, setByoKeys] = useState(false);
  const [flexAuto, setFlexAuto] = useState(false);
  const [llmAuto, setLlmAuto] = useState(false);

  function handlePlanCheckout(tier: TierId) {
    const cycle = yearly ? "yearly" : "monthly";
    const params = new URLSearchParams({
      kind: "subscription",
      tier,
      billing_cycle: cycle,
      success_path: "/app/billing",
      cancel_path: "/app/pricing",
    });
    window.location.assign(`/app/payment?${params.toString()}`);
  }

  const customMonthly = useMemo(() => {
    let total = 1; // base
    total += webMonths * 1;
    total += export100k ? 2 : 0;
    total += bonusSteps * 2;
    total += priority * 2;
    total += immediateLlm ? 1 : 0;
    total += byoKeys ? 14 : 0;
    total += flexAuto ? 1 : 0;
    total += llmAuto ? 1 : 0;
    return total;
  }, [webMonths, export100k, bonusSteps, priority, immediateLlm, byoKeys, flexAuto, llmAuto]);

  const customBonus = bonusSteps * 0.05;

  return (
    <div className="min-h-screen bg-slate-50">
      <div className="mx-auto max-w-6xl px-4 py-10 space-y-16">

        {/* ── Header ── */}
        <div className="text-center space-y-3">
          <h1 className="text-4xl font-bold tracking-tight text-slate-900">
            {dict.app.pricing.title}
          </h1>
          <p className="text-slate-500 max-w-xl mx-auto">
            {t.subtitle}
          </p>

          {/* Billing toggle */}
          <div className="inline-flex items-center gap-3 mt-4 rounded-full border border-slate-200 bg-white px-4 py-2 shadow-sm">
            <span className={`text-sm font-medium ${!yearly ? "text-slate-900" : "text-slate-400"}`}>{t.monthly}</span>
            <button
              role="switch"
              aria-checked={yearly}
              onClick={() => setYearly((v) => !v)}
              className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors focus:outline-none ${yearly ? "bg-blue-600" : "bg-slate-200"}`}
            >
              <span className={`inline-block h-4 w-4 transform rounded-full bg-white shadow transition-transform ${yearly ? "translate-x-6" : "translate-x-1"}`} />
            </button>
            <span className={`text-sm font-medium ${yearly ? "text-slate-900" : "text-slate-400"}`}>
              {t.yearly} <span className="ml-1 rounded-full bg-emerald-100 px-2 py-0.5 text-[10px] font-semibold text-emerald-700">{t.twoMonthsFree}</span>
            </span>
          </div>
        </div>

        {/* ── Tier cards ── */}
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-5">
          {TIERS.map((tier) => {
            const price = yearly ? tier.yearly : tier.monthly;
            const isDark = "dark" in tier && tier.dark;
            const isPopular = tier.popular;
            return (
              <div
                key={tier.id}
                className={`relative flex flex-col rounded-2xl border p-5 shadow-sm ${
                  isDark
                    ? "border-slate-800 bg-slate-900 text-white"
                    : isPopular
                    ? "border-blue-400 bg-blue-50 ring-2 ring-blue-400"
                    : "border-slate-200 bg-white"
                }`}
              >
                {isPopular && (
                  <span className="absolute -top-3 left-1/2 -translate-x-1/2 rounded-full bg-blue-600 px-3 py-0.5 text-[11px] font-semibold text-white shadow">
                    {t.mostPopular}
                  </span>
                )}
                <p className={`text-xs font-semibold uppercase tracking-widest ${isDark ? "text-slate-400" : "text-slate-500"}`}>
                  {tier.name}
                </p>
                <div className="mt-2 flex items-end gap-1">
                  <span className={`text-3xl font-bold ${isDark ? "text-white" : "text-slate-900"}`}>
                    {price === 0 ? t.free : `CHF ${price}`}
                  </span>
                  {price > 0 && (
                    <span className={`mb-1 text-xs ${isDark ? "text-slate-400" : "text-slate-400"}`}>
                      {yearly ? t.perYear : t.perMonth}
                    </span>
                  )}
                </div>
                {price > 0 && (
                  <p className={`text-[10px] mt-0.5 ${isDark ? "text-slate-500" : "text-slate-400"}`}>
                    {dict.app.pricing.exklMwst}
                  </p>
                )}
                <p className={`mt-2 text-xs leading-relaxed ${isDark ? "text-slate-400" : "text-slate-500"}`}>
                  {tier.description}
                </p>
                <button
                  onClick={() => handlePlanCheckout(tier.id)}
                  disabled={checkoutLoading !== null}
                  className={`mt-4 inline-flex items-center justify-center rounded-lg px-3 py-2 text-xs font-semibold transition-colors disabled:opacity-60 ${
                    isDark
                      ? "bg-white text-slate-900 hover:bg-slate-100"
                      : isPopular
                      ? "bg-blue-600 text-white hover:bg-blue-700"
                      : "bg-slate-900 text-white hover:bg-slate-800"
                  }`}
                >
                  {checkoutLoading === tier.id ? t.starting : t.startCheckout.replace("{tier}", tier.name)}
                </button>
                {price > 0 && (
                  <p className={`mt-2 text-center text-[10px] ${isDark ? "text-slate-500" : "text-slate-400"}`}>
                    {t.renewsAutoCancelAnytime}
                  </p>
                )}
              </div>
            );
          })}
        </div>

        {checkoutMessage && (
          <div
            role="status"
            className={`rounded-xl border px-4 py-3 text-sm ${
              checkoutMessage.kind === "error"
                ? "border-red-200 bg-red-50 text-red-700"
                : "border-green-200 bg-green-50 text-green-700"
            }`}
          >
            {checkoutMessage.message}
          </div>
        )}

        {/* ── Feature comparison table ── */}
        <PlanComparisonTable comparison={dict.app.pricing.comparison} />

        {/* ── Consumption-based pricing ── */}
        <div className="space-y-5">
          <div>
            <h2 className="text-xl font-semibold text-slate-900">{t.consumptionCredits}</h2>
            <p className="mt-1 text-sm text-slate-500">
              {t.consumptionDesc}
            </p>
          </div>

          {/* Topup bonus preview — pick a tier to see how many bonus credits you'd receive */}
          <div className="rounded-xl border border-slate-200 bg-slate-50 px-5 py-4 space-y-3">
            <p className="text-sm font-medium text-slate-700">
              {t.topupBonusPreview}
            </p>
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-xs text-slate-500">{t.yourTier}</span>
              {TIERS.map((tr) => (
                <button
                  key={tr.id}
                  onClick={() => setCreditTier(tr.id)}
                  className={`rounded-full px-3 py-1 text-xs font-semibold transition-colors ${
                    creditTier === tr.id
                      ? "bg-blue-600 text-white"
                      : "bg-white border border-slate-200 text-slate-600 hover:bg-slate-100"
                  }`}
                >
                  {tr.name}
                </button>
              ))}
            </div>
            {TIER_BONUS_RATE[creditTier] > 0 ? (
              <p className="text-sm text-emerald-700 font-medium">
                {t.buyingCredits} <strong>{t.creditsChf}</strong> {t.youReceive}{" "}
                <strong>{(10_000 + Math.round(10_000 * TIER_BONUS_RATE[creditTier])).toLocaleString()} {t.creditsWord}</strong>{" "}
                {t.bonusPct.replace("{pct}", (TIER_BONUS_RATE[creditTier] * 100).toFixed(0)).replace("{extra}", Math.round(10_000 * TIER_BONUS_RATE[creditTier]).toLocaleString())}
              </p>
            ) : (
              <p className="text-sm text-slate-500">
                {t.freeNoBonusCredits}
              </p>
            )}
          </div>

          {/* Credit cost table — base cost is the same for all tiers */}
          <div className="overflow-x-auto rounded-xl border border-slate-200 shadow-sm">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-slate-200 bg-slate-100">
                  <th className="px-4 py-3 text-left font-semibold text-slate-600">{t.colAction}</th>
                  <th className="px-4 py-3 text-center font-semibold text-slate-600">{t.colUnit}</th>
                  <th className="px-4 py-3 text-center font-semibold text-slate-600">{t.colCredits}</th>
                  <th className="px-4 py-3 text-center font-semibold text-slate-600">{t.colChf}</th>
                </tr>
              </thead>
              <tbody>
                {CREDIT_ACTIONS.map((action, i) => (
                  <tr key={action.label} className={`border-b border-slate-100 ${i % 2 === 0 ? "bg-white" : "bg-slate-50/50"}`}>
                    <td className="px-4 py-3 text-slate-700 font-medium">{action.label}</td>
                    <td className="px-4 py-3 text-center text-slate-500 text-xs">{action.unit}</td>
                    <td className="px-4 py-3 text-center text-slate-700 font-semibold">{action.base.toLocaleString()}</td>
                    <td className="px-4 py-3 text-center text-slate-600">{creditsToChf(action.base)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>

        {/* ── Custom tier configurator ── */}
        <div className="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm space-y-6">
          <div>
            <h2 className="text-xl font-semibold text-slate-900">{t.customConfigurator}</h2>
            <p className="mt-1 text-sm text-slate-500">
              {t.customConfiguratorDesc}
            </p>
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 text-sm">
            {/* Number stepper */}
            {[
              { label: t.webPrivacyMonths, sublabel: t.webPrivacyMonthsSub, value: webMonths, min: 0, max: 24, set: setWebMonths },
              { label: t.topupBonusSteps, sublabel: t.topupBonusStepsSub, value: bonusSteps, min: 0, max: 8, set: setBonusSteps },
              { label: t.queuePriority, sublabel: t.queuePrioritySub, value: priority, min: 0, max: 4, set: setPriority },
            ].map(({ label, sublabel, value, min, max, set }) => (
              <div key={label} className="flex items-center justify-between gap-3 rounded-xl border border-slate-200 px-4 py-3">
                <div>
                  <p className="font-medium text-slate-700">{label}</p>
                  <p className="text-xs text-slate-400 mt-0.5">{sublabel}</p>
                </div>
                <div className="flex items-center gap-1 shrink-0">
                  <button
                    onClick={() => set(Math.max(min, value - 1))}
                    className="flex h-7 w-7 items-center justify-center rounded-lg border border-slate-200 text-slate-500 hover:bg-slate-50 active:bg-slate-100"
                  >
                    −
                  </button>
                  <span className="w-7 text-center font-semibold text-slate-800">{value}</span>
                  <button
                    onClick={() => set(Math.min(max, value + 1))}
                    className="flex h-7 w-7 items-center justify-center rounded-lg border border-slate-200 text-slate-500 hover:bg-slate-50 active:bg-slate-100"
                  >
                    +
                  </button>
                </div>
              </div>
            ))}

            {/* Checkboxes */}
            {[
              { label: t.export100k, sublabel: t.export100kSub, checked: export100k, set: setExport100k },
              { label: t.immediateLlm, sublabel: t.immediateLlmSub, checked: immediateLlm, set: setImmediateLlm },
              { label: t.byoKeys, sublabel: t.byoKeysSub, checked: byoKeys, set: setByoKeys },
              { label: t.flexAuto, sublabel: t.flexAutoSub, checked: flexAuto, set: setFlexAuto },
              { label: t.llmAuto, sublabel: t.llmAutoSub, checked: llmAuto, set: setLlmAuto },
            ].map(({ label, sublabel, checked, set }) => (
              <button
                key={label}
                onClick={() => set((v: boolean) => !v)}
                className={`flex items-center justify-between gap-3 rounded-xl border px-4 py-3 text-left transition-colors ${
                  checked
                    ? "border-blue-300 bg-blue-50"
                    : "border-slate-200 hover:border-slate-300 hover:bg-slate-50"
                }`}
              >
                <div>
                  <p className={`font-medium ${checked ? "text-blue-800" : "text-slate-700"}`}>{label}</p>
                  <p className={`text-xs mt-0.5 ${checked ? "text-blue-500" : "text-slate-400"}`}>{sublabel}</p>
                </div>
                <div className={`flex h-5 w-5 shrink-0 items-center justify-center rounded border-2 transition-colors ${
                  checked ? "border-blue-600 bg-blue-600" : "border-slate-300"
                }`}>
                  {checked && (
                    <svg className="h-3 w-3 text-white" viewBox="0 0 12 12" fill="none">
                      <path d="M2 6l3 3 5-5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
                    </svg>
                  )}
                </div>
              </button>
            ))}
          </div>

          {/* Price summary */}
          <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4 rounded-xl border border-slate-100 bg-slate-50 px-5 py-4">
            <div className="space-y-1">
              <p className="text-xs text-slate-500 uppercase tracking-wide font-semibold">{t.estimatedPrice}</p>
              <div className="flex items-end gap-2">
                <span className="text-3xl font-bold text-slate-900">
                  CHF {yearly ? customMonthly * 10 : customMonthly}
                </span>
                <span className="mb-1 text-sm text-slate-400">{yearly ? t.perYear : t.perMonth}</span>
              </div>
              {customBonus > 0 && (
                <p className="text-xs text-emerald-600 font-medium">
                  {t.bonusIncluded.replace("{pct}", (customBonus * 100).toFixed(0))}
                </p>
              )}
            </div>
            <div className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-xs text-amber-800 max-w-xs">
              {t.useButtonsHint}
            </div>
          </div>
          <div className="flex flex-wrap items-center justify-between gap-3">
            {process.env.NODE_ENV !== "production" && (
              <Link
                href="/app/dev/billing"
                className="inline-flex items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-xs font-medium text-slate-600 transition-colors hover:bg-slate-50"
              >
                {t.openDevBilling}
              </Link>
            )}
          </div>
        </div>

        {/* ── Footer note ── */}
        <p className="text-center text-xs text-slate-400 pb-4">
          {t.footerNote}
        </p>

      </div>
    </div>
  );
}
