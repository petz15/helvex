"use client";

import { useMemo, useState } from "react";

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
        label: "Credit discount",
        tip: "Applied automatically on every credit deduction.",
        values: ["0%", "10%", "15%", "20%", "30%"],
      },
    ],
  },
  {
    heading: "Workspace",
    rows: [
      { label: "No ads", values: ["no", "yes", "yes", "yes", "yes"] },
      { label: "Multi-user org", values: ["no", "yes", "yes", "yes", "yes"] },
      {
        label: "Daily digest notifications",
        values: ["no", "yes", "yes", "yes", "yes"],
      },
    ],
  },
  {
    heading: "Scoring & AI",
    rows: [
      {
        label: "Flex rescore",
        tip: "Simple gets 1 free rescore per month; Explorer+ unlimited at no credit cost.",
        values: ["no", "1 / month", "Unlimited", "Unlimited", "Unlimited"],
      },
      {
        label: "Flex auto-score new companies",
        values: ["no", "no", "yes", "yes", "yes"],
      },
      {
        label: "Immediate LLM scoring",
        tip: "Uses the standard Anthropic API synchronously instead of the batch queue.",
        values: ["no", "no", "yes", "yes", "yes"],
      },
      {
        label: "LLM auto-score new companies",
        tip: "Uses credits from your balance.",
        values: ["no", "no", "no", "yes", "yes"],
      },
      {
        label: "Custom ML stopwords",
        values: ["no", "no", "no", "yes", "yes"],
      },
      {
        label: "Bring your own LLM keys",
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

const TIER_DISCOUNTS: Record<TierId, number> = {
  free: 0,
  simple: 0.1,
  explorer: 0.15,
  researcher: 0.2,
  strategist: 0.3,
};

function effectiveCredits(base: number, discount: number) {
  return Math.round(base * (1 - discount));
}

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

  // Custom configurator state
  const [webMonths, setWebMonths] = useState(0);
  const [export100k, setExport100k] = useState(false);
  const [discountSteps, setDiscountSteps] = useState(0);
  const [priority, setPriority] = useState(0);
  const [immediateLlm, setImmediateLlm] = useState(false);
  const [byoKeys, setByoKeys] = useState(false);
  const [flexAuto, setFlexAuto] = useState(false);
  const [llmAuto, setLlmAuto] = useState(false);

  const customMonthly = useMemo(() => {
    let total = 1; // base
    total += webMonths * 1;
    total += export100k ? 2 : 0;
    total += discountSteps * 2;
    total += priority * 2;
    total += immediateLlm ? 1 : 0;
    total += byoKeys ? 14 : 0;
    total += flexAuto ? 1 : 0;
    total += llmAuto ? 1 : 0;
    return total;
  }, [webMonths, export100k, discountSteps, priority, immediateLlm, byoKeys, flexAuto, llmAuto]);

  const customDiscount = discountSteps * 0.05;

  return (
    <div className="min-h-screen bg-slate-50">
      <div className="mx-auto max-w-6xl px-4 py-10 space-y-16">

        {/* ── Header ── */}
        <div className="text-center space-y-3">
          <h1 className="text-4xl font-bold tracking-tight text-slate-900">
            Simple, transparent pricing
          </h1>
          <p className="text-slate-500 max-w-xl mx-auto">
            A flat monthly plan plus consumption-based credits. Higher tiers get discounts on every credit spent.
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
              </div>
            );
          })}
        </div>

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
              1 credit = CHF 0.0001. Credits are deducted per action. Your tier discount is applied automatically at the time of deduction.
              Unused subscription-granted credits expire after 12 months; top-up purchases never expire.
            </p>
          </div>

          {/* Tier selector for effective price preview */}
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-sm text-slate-500 font-medium">Show prices for:</span>
            {TIERS.map((t) => (
              <button
                key={t.id}
                onClick={() => setCreditTier(t.id)}
                className={`rounded-full px-3 py-1 text-xs font-semibold transition-colors ${
                  creditTier === t.id
                    ? "bg-blue-600 text-white"
                    : "bg-slate-100 text-slate-600 hover:bg-slate-200"
                }`}
              >
                {t.name}
              </button>
            ))}
          </div>

          <div className="overflow-x-auto rounded-xl border border-slate-200 shadow-sm">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-slate-200 bg-slate-100">
                  <th className="px-4 py-3 text-left font-semibold text-slate-600">Action</th>
                  <th className="px-4 py-3 text-center font-semibold text-slate-600">Unit</th>
                  <th className="px-4 py-3 text-center font-semibold text-slate-600">Base credits</th>
                  <th className="px-4 py-3 text-center font-semibold text-blue-700 bg-blue-50">
                    Effective credits
                    <span className="ml-1 text-[10px] font-normal text-blue-500">
                      ({(TIER_DISCOUNTS[creditTier] * 100).toFixed(0)}% off)
                    </span>
                  </th>
                  <th className="px-4 py-3 text-center font-semibold text-blue-700 bg-blue-50">CHF</th>
                </tr>
              </thead>
              <tbody>
                {CREDIT_ACTIONS.map((action, i) => {
                  const discount = TIER_DISCOUNTS[creditTier];
                  const effective = effectiveCredits(action.base, discount);
                  return (
                    <tr key={action.label} className={`border-b border-slate-100 ${i % 2 === 0 ? "bg-white" : "bg-slate-50/50"}`}>
                      <td className="px-4 py-3 text-slate-700 font-medium">{action.label}</td>
                      <td className="px-4 py-3 text-center text-slate-500 text-xs">{action.unit}</td>
                      <td className="px-4 py-3 text-center text-slate-600">{action.base.toLocaleString()}</td>
                      <td className="px-4 py-3 text-center font-semibold text-blue-700 bg-blue-50/40">
                        {effective.toLocaleString()}
                      </td>
                      <td className="px-4 py-3 text-center text-blue-700 bg-blue-50/40">
                        {creditsToChf(effective)}
                      </td>
                    </tr>
                  );
                })}
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
              { label: "Discount steps (5% each, max 40%)", sublabel: "+CHF 2/mo per step", value: discountSteps, min: 0, max: 8, set: setDiscountSteps },
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
              {customDiscount > 0 && (
                <p className="text-xs text-emerald-600 font-medium">
                  {(customDiscount * 100).toFixed(0)}% credit discount included
                </p>
              )}
            </div>
            <div className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-xs text-amber-800 max-w-xs">
              Checkout is not yet implemented. This configurator provides plan visibility and price estimation only.
            </div>
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
