"use client";

import { useEffect, useMemo, useState } from "react";
import useSWR from "swr";
import {
  createSubscriptionCheckout,
  fetchCurrentUser,
  parseBillingAddressJson,
} from "@/lib/api";
import {
  buildAddressReturnUrl,
  clearCheckoutIntent,
  readCheckoutIntent,
  saveCheckoutIntent,
} from "@/lib/checkout-resume";

// ── Data ──────────────────────────────────────────────────────────────────────

const TIERS = [
  {
    id: "free",
    name: "Free",
    monthly: 0,
    yearly: 0,
    description: "Try the core search and export features at no cost.",
    color: "slate",
    popular: false,
  },
  {
    id: "simple",
    name: "Simple",
    monthly: 6,
    yearly: 60,
    description: "For individuals who need a clean workspace without ads.",
    color: "slate",
    popular: false,
  },
  {
    id: "explorer",
    name: "Explorer",
    monthly: 12,
    yearly: 120,
    description: "Immediate LLM scoring and automated flex rescoring.",
    color: "blue",
    popular: true,
  },
  {
    id: "researcher",
    name: "Researcher",
    monthly: 17,
    yearly: 170,
    description: "Full LLM auto-scoring and custom ML stopwords.",
    color: "violet",
    popular: false,
  },
  {
    id: "strategist",
    name: "Strategist",
    monthly: 37,
    yearly: 370,
    description: "Highest priority, BYO keys, and future API access.",
    color: "slate",
    popular: false,
    dark: true,
  },
] as const;

type TierId = typeof TIERS[number]["id"];

// Feature rows: label, tooltip (optional), value per tier index [free,simple,explorer,researcher,strategist]
type CellValue = "yes" | "no" | string;

interface FeatureRow {
  label: string;
  tip?: string;
  values: [CellValue, CellValue, CellValue, CellValue, CellValue];
}

interface FeatureGroup {
  heading: string;
  rows: FeatureRow[];
}

const FEATURE_GROUPS: FeatureGroup[] = [
  {
    heading: "Usage limits",
    rows: [
      {
        label: "CSV export rows",
        values: ["100", "1,000", "5,000", "20,000", "100,000"],
      },
      {
        label: "Queue priority",
        tip: "Jobs from higher tiers are always processed first.",
        values: ["Lowest", "Low", "Medium", "High", "Highest"],
      },
      {
        label: "Web result privacy",
        tip: "How long your web-search results stay private before being visible to others.",
        values: ["None", "1 week", "1 month", "3 months", "Held + 6 mo"],
      },
      {
        label: "Topup credit bonus",
        tip: "Extra credits granted on top of every credit purchase. E.g. Explorer buying 10,000 credits receives 1,500 bonus credits.",
        values: ["None", "+10%", "+15%", "+20%", "+30%"],
      },
    ],
  },
  {
    heading: "Workspace",
    rows: [
      { label: "No ads", values: ["no", "yes", "yes", "yes", "yes"] },
      {
        label: "Multi-user org",
        tip: "Invite teammates to share the same workspace, collection, and credit balance.",
        values: ["no", "yes", "yes", "yes", "yes"],
      },
      {
        label: "Daily digest notifications",
        tip: "Receive a daily email summary of newly discovered companies matching your criteria.",
        values: ["no", "yes", "yes", "yes", "yes"],
      },
    ],
  },
  {
    heading: "Scoring & AI",
    rows: [
      {
        label: "Flex rescore",
        tip: "Explorer+ tiers get unlimited flex rescores at no credit cost. Free and Simple tiers pay per rescore from their credit balance.",
        values: ["no", "no", "Unlimited", "Unlimited", "Unlimited"],
      },
      {
        label: "Flex auto-score new companies",
        tip: "Automatically rescores newly discovered companies against your Flex criteria.",
        values: ["no", "no", "yes", "yes", "yes"],
      },
      {
        label: "Immediate LLM scoring",
        tip: "Uses the Anthropic API synchronously instead of the batch queue — results appear instantly.",
        values: ["no", "no", "yes", "yes", "yes"],
      },
      {
        label: "LLM auto-score new companies",
        tip: "Automatically runs LLM scoring on newly discovered companies. Uses credits from your balance.",
        values: ["no", "no", "no", "yes", "yes"],
      },
      {
        label: "Custom ML stopwords",
        tip: "Define words to exclude from ML-based company name clustering and matching.",
        values: ["no", "no", "no", "yes", "yes"],
      },
      {
        label: "Bring your own LLM keys",
        tip: "Use your own Anthropic API key so LLM scoring costs go directly to your account.",
        values: ["no", "no", "no", "no", "yes"],
      },
    ],
  },
  {
    heading: "Developer",
    rows: [
      {
        label: "API access",
        tip: "Programmatic access to your workspace (coming soon).",
        values: ["no", "no", "no", "no", "yes"],
      },
    ],
  },
];

// Credit cost table
const CREDIT_ACTIONS = [
  {
    label: "Batch LLM classify",
    unit: "per company",
    base: 8,
  },
  {
    label: "Immediate LLM classify",
    unit: "per company",
    base: 12,
  },
  {
    label: "Web search",
    unit: "per company",
    base: 20,
  },
  {
    label: "Flex rescore",
    unit: "per company",
    base: 1,
  },
  {
    label: "Full reclustering",
    unit: "flat",
    base: 100_000,
  },
  {
    label: "Bulk export – basic (UID/Name/Canton)",
    unit: "per 10k rows",
    base: 6_000,
  },
  {
    label: "Bulk export – detailed",
    unit: "per 10k rows",
    base: 13_000,
  },
];

const TIER_BONUS_RATE: Record<TierId, number> = {
  free: 0,
  simple: 0.1,
  explorer: 0.15,
  researcher: 0.2,
  strategist: 0.3,
};

function creditsToChf(credits: number) {
  return (credits * 0.0001).toFixed(credits >= 1000 ? 2 : 4);
}

// ── Sub-components ─────────────────────────────────────────────────────────────

function Check() {
  return (
    <svg className="mx-auto h-5 w-5 text-emerald-500" viewBox="0 0 20 20" fill="currentColor">
      <path fillRule="evenodd" d="M16.704 4.153a.75.75 0 0 1 .143 1.052l-8 10.5a.75.75 0 0 1-1.127.075l-4.5-4.5a.75.75 0 0 1 1.06-1.06l3.894 3.893 7.48-9.817a.75.75 0 0 1 1.05-.143Z" clipRule="evenodd" />
    </svg>
  );
}

function Cross() {
  return (
    <svg className="mx-auto h-4 w-4 text-slate-300" viewBox="0 0 20 20" fill="currentColor">
      <path d="M6.28 5.22a.75.75 0 0 0-1.06 1.06L8.94 10l-3.72 3.72a.75.75 0 1 0 1.06 1.06L10 11.06l3.72 3.72a.75.75 0 1 0 1.06-1.06L11.06 10l3.72-3.72a.75.75 0 0 0-1.06-1.06L10 8.94 6.28 5.22Z" />
    </svg>
  );
}

function Cell({ value, dark }: { value: CellValue; dark?: boolean }) {
  if (value === "yes") return <Check />;
  if (value === "no") return <Cross />;
  return (
    <span className={`text-xs font-medium ${dark ? "text-slate-300" : "text-slate-600"}`}>
      {value}
    </span>
  );
}

function Tooltip({ text }: { text: string }) {
  return (
    <span
      title={text}
      className="ml-1 inline-flex h-3.5 w-3.5 cursor-help items-center justify-center rounded-full bg-slate-200 text-[9px] font-bold text-slate-500 leading-none"
    >
      ?
    </span>
  );
}

// ── Main component ─────────────────────────────────────────────────────────────

export function PricingClient() {
  const [yearly, setYearly] = useState(false);
  const [creditTier, setCreditTier] = useState<TierId>("explorer");
  const [checkoutLoading, setCheckoutLoading] = useState<string | null>(null);
  const [checkoutMessage, setCheckoutMessage] = useState<{ kind: "success" | "error"; message: string } | null>(null);
  const { data: me } = useSWR("me", fetchCurrentUser);
  const billingAddress = parseBillingAddressJson(me?.billing_address_json);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const checkout = params.get("checkout");
    const tier = params.get("tier");
    const reason = params.get("reason");
    const alreadyProcessed = params.get("already_processed") === "true";
    if (!checkout && !alreadyProcessed && !reason) return;
    if (alreadyProcessed) {
      setCheckoutMessage({ kind: "success", message: "This checkout was already processed." });
    } else if (checkout === "cancel") {
      setCheckoutMessage({
        kind: "error",
        message: tier ? `${tier} checkout was cancelled.` : reason === "payment_declined" ? "Payment was declined or cancelled." : "Checkout cancelled.",
      });
    } else if (checkout === "success") {
      setCheckoutMessage({ kind: "success", message: tier ? `${tier} checkout completed.` : "Checkout completed." });
    }
    window.history.replaceState({}, "", window.location.pathname);
  }, []);

  useEffect(() => {
    if (!billingAddress) return;
    const params = new URLSearchParams(window.location.search);
    if (params.get("resume_checkout") !== "1") return;

    const intent = readCheckoutIntent();
    if (!intent || intent.kind !== "subscription") return;
    if (!intent.sourcePath.startsWith("/app/pricing")) return;

    clearCheckoutIntent();
    void confirmPlanCheckout(intent.tier as TierId, intent.billing_cycle);
  }, [billingAddress]);

  // Custom configurator state
  const [webMonths, setWebMonths] = useState(0);
  const [export100k, setExport100k] = useState(false);
  const [bonusSteps, setBonusSteps] = useState(0);
  const [priority, setPriority] = useState(0);
  const [immediateLlm, setImmediateLlm] = useState(false);
  const [byoKeys, setByoKeys] = useState(false);
  const [flexAuto, setFlexAuto] = useState(false);
  const [llmAuto, setLlmAuto] = useState(false);

  async function handlePlanCheckout(tier: TierId) {
    if (!billingAddress) {
      const sourcePath = `${window.location.pathname}${window.location.search}`;
      saveCheckoutIntent({
        kind: "subscription",
        sourcePath,
        tier,
        billing_cycle: yearly ? "yearly" : "monthly",
        success_path: "/app/account",
        cancel_path: "/app/pricing",
      });
      window.location.assign(buildAddressReturnUrl(sourcePath));
      return;
    }
    void confirmPlanCheckout(tier);
  }

  async function confirmPlanCheckout(tier: TierId, cycleOverride?: "monthly" | "yearly") {
    if (!billingAddress) {
      setCheckoutMessage({ kind: "error", message: "Add a billing address first in Address Management before starting checkout." });
      return;
    }
    setCheckoutLoading(tier);
    setCheckoutMessage(null);
    try {
      const origin = window.location.origin;
      const successUrl = new URL("/app/account", origin);
      successUrl.searchParams.set("checkout", "success");
      successUrl.searchParams.set("tier", tier);
      const cancelUrl = new URL("/app/pricing", origin);
      cancelUrl.searchParams.set("checkout", "cancel");
      cancelUrl.searchParams.set("tier", tier);
      const session = await createSubscriptionCheckout({
        tier,
        billing_cycle: cycleOverride ?? (yearly ? "yearly" : "monthly"),
        success_url: successUrl.toString(),
        cancel_url: cancelUrl.toString(),
        billing_address: billingAddress,
      });
      window.location.assign(session.checkout_url);
    } catch (err) {
      setCheckoutMessage({
        kind: "error",
        message: err instanceof Error ? err.message : "Failed to start checkout",
      });
    } finally {
      setCheckoutLoading(null);
    }
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
            Simple, transparent pricing
          </h1>
          <p className="text-slate-500 max-w-xl mx-auto">
            A flat monthly plan plus consumption-based credits. Higher tiers receive bonus credits on every top-up purchase.
          </p>

          {/* Billing toggle */}
          <div className="inline-flex items-center gap-3 mt-4 rounded-full border border-slate-200 bg-white px-4 py-2 shadow-sm">
            <span className={`text-sm font-medium ${!yearly ? "text-slate-900" : "text-slate-400"}`}>Monthly</span>
            <button
              role="switch"
              aria-checked={yearly}
              onClick={() => setYearly((v) => !v)}
              className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors focus:outline-none ${yearly ? "bg-blue-600" : "bg-slate-200"}`}
            >
              <span className={`inline-block h-4 w-4 transform rounded-full bg-white shadow transition-transform ${yearly ? "translate-x-6" : "translate-x-1"}`} />
            </button>
            <span className={`text-sm font-medium ${yearly ? "text-slate-900" : "text-slate-400"}`}>
              Yearly <span className="ml-1 rounded-full bg-emerald-100 px-2 py-0.5 text-[10px] font-semibold text-emerald-700">2 months free</span>
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
                    Most popular
                  </span>
                )}
                <p className={`text-xs font-semibold uppercase tracking-widest ${isDark ? "text-slate-400" : "text-slate-500"}`}>
                  {tier.name}
                </p>
                <div className="mt-2 flex items-end gap-1">
                  <span className={`text-3xl font-bold ${isDark ? "text-white" : "text-slate-900"}`}>
                    {price === 0 ? "Free" : `CHF ${price}`}
                  </span>
                  {price > 0 && (
                    <span className={`mb-1 text-xs ${isDark ? "text-slate-400" : "text-slate-400"}`}>
                      /{yearly ? "yr" : "mo"}
                    </span>
                  )}
                </div>
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
                  {checkoutLoading === tier.id ? "Starting…" : `Start ${tier.name} checkout`}
                </button>
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
        <div className="space-y-3">
          <h2 className="text-xl font-semibold text-slate-900">Compare plans</h2>
          <div className="overflow-x-auto rounded-xl border border-slate-200 shadow-sm">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-slate-200 bg-slate-100">
                  <th className="px-4 py-3 text-left font-semibold text-slate-600 w-52">Feature</th>
                  {TIERS.map((t) => (
                    <th
                      key={t.id}
                      className={`px-3 py-3 text-center font-semibold text-sm w-28 ${
                        "dark" in t && t.dark
                          ? "bg-slate-900 text-white"
                          : t.popular
                          ? "bg-blue-50 text-blue-700"
                          : "text-slate-700"
                      }`}
                    >
                      {t.name}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {FEATURE_GROUPS.map((group) => (
                  <>
                    <tr key={`group-${group.heading}`} className="bg-slate-50 border-y border-slate-200">
                      <td
                        colSpan={6}
                        className="px-4 py-2 text-xs font-semibold uppercase tracking-wider text-slate-400"
                      >
                        {group.heading}
                      </td>
                    </tr>
                    {group.rows.map((row, rowIdx) => (
                      <tr
                        key={row.label}
                        className={`border-b border-slate-100 ${rowIdx % 2 === 0 ? "bg-white" : "bg-slate-50/50"}`}
                      >
                        <td className="px-4 py-3 text-slate-700 font-medium">
                          {row.label}
                          {row.tip && <Tooltip text={row.tip} />}
                        </td>
                        {row.values.map((val, colIdx) => {
                          const tier = TIERS[colIdx];
                          const isDark = "dark" in tier && tier.dark;
                          return (
                            <td
                              key={colIdx}
                              className={`px-3 py-3 text-center ${
                                isDark ? "bg-slate-900" : tier.popular ? "bg-blue-50/40" : ""
                              }`}
                            >
                              <Cell value={val} dark={isDark} />
                            </td>
                          );
                        })}
                      </tr>
                    ))}
                  </>
                ))}
              </tbody>
            </table>
          </div>
        </div>

        {/* ── Consumption-based pricing ── */}
        <div className="space-y-5">
          <div>
            <h2 className="text-xl font-semibold text-slate-900">Consumption credits</h2>
            <p className="mt-1 text-sm text-slate-500">
              1 credit = CHF 0.0001. Credits are deducted at full base cost per action.
              Higher tiers receive bonus credits on top of every top-up purchase — the bonus is applied automatically at checkout.
              Unused subscription-granted credits expire after 12 months; purchased credits never expire.
            </p>
          </div>

          {/* Topup bonus preview — pick a tier to see how many bonus credits you'd receive */}
          <div className="rounded-xl border border-slate-200 bg-slate-50 px-5 py-4 space-y-3">
            <p className="text-sm font-medium text-slate-700">
              Topup bonus preview — credits are always deducted at full base cost. Bonuses are added when you buy credits.
            </p>
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-xs text-slate-500">Your tier:</span>
              {TIERS.map((t) => (
                <button
                  key={t.id}
                  onClick={() => setCreditTier(t.id)}
                  className={`rounded-full px-3 py-1 text-xs font-semibold transition-colors ${
                    creditTier === t.id
                      ? "bg-blue-600 text-white"
                      : "bg-white border border-slate-200 text-slate-600 hover:bg-slate-100"
                  }`}
                >
                  {t.name}
                </button>
              ))}
            </div>
            {TIER_BONUS_RATE[creditTier] > 0 ? (
              <p className="text-sm text-emerald-700 font-medium">
                Buying <strong>10,000 credits (CHF 1.00)</strong> → you receive{" "}
                <strong>{(10_000 + Math.round(10_000 * TIER_BONUS_RATE[creditTier])).toLocaleString()} credits</strong>{" "}
                ({(TIER_BONUS_RATE[creditTier] * 100).toFixed(0)}% bonus,{" "}
                {Math.round(10_000 * TIER_BONUS_RATE[creditTier]).toLocaleString()} extra)
              </p>
            ) : (
              <p className="text-sm text-slate-500">
                Free tier receives no bonus credits. Upgrade for a topup bonus.
              </p>
            )}
          </div>

          {/* Credit cost table — base cost is the same for all tiers */}
          <div className="overflow-x-auto rounded-xl border border-slate-200 shadow-sm">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-slate-200 bg-slate-100">
                  <th className="px-4 py-3 text-left font-semibold text-slate-600">Action</th>
                  <th className="px-4 py-3 text-center font-semibold text-slate-600">Unit</th>
                  <th className="px-4 py-3 text-center font-semibold text-slate-600">Credits</th>
                  <th className="px-4 py-3 text-center font-semibold text-slate-600">CHF</th>
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
            <h2 className="text-xl font-semibold text-slate-900">Custom tier configurator</h2>
            <p className="mt-1 text-sm text-slate-500">
              Build a modular plan with exactly the features you need. The base plan (CHF 1/mo) includes no-ads and multi-user.
            </p>
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 text-sm">
            {/* Number stepper */}
            {[
              { label: "Web result privacy months", sublabel: "+CHF 1/mo per month", value: webMonths, min: 0, max: 24, set: setWebMonths },
              { label: "Topup bonus steps (5% each, max 40%)", sublabel: "+CHF 2/mo per step", value: bonusSteps, min: 0, max: 8, set: setBonusSteps },
              { label: "Queue priority level (max 4)", sublabel: "+CHF 2/mo per level", value: priority, min: 0, max: 4, set: setPriority },
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
              { label: "Export 100k rows", sublabel: "+CHF 2/mo", checked: export100k, set: setExport100k },
              { label: "Immediate LLM scoring", sublabel: "+CHF 1/mo", checked: immediateLlm, set: setImmediateLlm },
              { label: "Bring your own LLM keys", sublabel: "+CHF 14/mo", checked: byoKeys, set: setByoKeys },
              { label: "Flex auto-score new companies", sublabel: "+CHF 1/mo", checked: flexAuto, set: setFlexAuto },
              { label: "LLM auto-score new companies", sublabel: "+CHF 1/mo", checked: llmAuto, set: setLlmAuto },
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
              <p className="text-xs text-slate-500 uppercase tracking-wide font-semibold">Estimated price</p>
              <div className="flex items-end gap-2">
                <span className="text-3xl font-bold text-slate-900">
                  CHF {yearly ? customMonthly * 10 : customMonthly}
                </span>
                <span className="mb-1 text-sm text-slate-400">/{yearly ? "yr" : "mo"}</span>
              </div>
              {customBonus > 0 && (
                <p className="text-xs text-emerald-600 font-medium">
                  {(customBonus * 100).toFixed(0)}% topup bonus included
                </p>
              )}
            </div>
            <div className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-xs text-amber-800 max-w-xs">
              Use the plan buttons above to start a live checkout, or open the dev billing page for direct subscription and top-up probes.
            </div>
          </div>
          <div className="flex flex-wrap items-center justify-between gap-3">
            <p className="text-xs text-slate-400">
              The buttons above start a live checkout for the selected tier so you can verify the browser return flow.
            </p>
            {process.env.NODE_ENV !== "production" && (
              <a
                href="/app/dev/billing"
                className="inline-flex items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-xs font-medium text-slate-600 transition-colors hover:bg-slate-50"
              >
                Open dev billing test page
              </a>
            )}
          </div>
        </div>

        {/* ── Footer note ── */}
        <p className="text-center text-xs text-slate-400 pb-4">
          All prices in CHF (Swiss francs) excluding VAT. Yearly billing charged as a single annual payment. Credits are shared across your organization.
        </p>

      </div>
    </div>
  );
}
