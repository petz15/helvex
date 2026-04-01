"use client";

import { useMemo, useState } from "react";

const TIERS = [
  { name: "Free", monthly: 0, yearly: 0, highlights: ["Core search", "Single user", "CSV export up to 100 rows"] },
  { name: "Simple", monthly: 9, yearly: 90, highlights: ["No ads", "Multi-user", "1 free monthly flex rescore"] },
  { name: "Explorer", monthly: 29, yearly: 290, highlights: ["Immediate LLM scoring", "5k CSV export", "15% credit discount"] },
  { name: "Researcher", monthly: 79, yearly: 790, highlights: ["LLM auto scoring", "20k CSV export", "20% credit discount"] },
  { name: "Strategist", monthly: 199, yearly: 1990, highlights: ["API access", "100k CSV export", "30% credit discount"] },
] as const;

function cardClass(name: string): string {
  if (name === "Explorer") return "border-blue-300 bg-blue-50";
  if (name === "Strategist") return "border-slate-900 bg-slate-900 text-white";
  return "border-slate-200 bg-white";
}

export function PricingClient() {
  const [yearly, setYearly] = useState(false);

  const [webMonths, setWebMonths] = useState(0);
  const [export100k, setExport100k] = useState(false);
  const [discountSteps, setDiscountSteps] = useState(0);
  const [priority, setPriority] = useState(0);
  const [immediateLlm, setImmediateLlm] = useState(false);
  const [byoKeys, setByoKeys] = useState(false);
  const [flexAuto, setFlexAuto] = useState(false);
  const [llmAuto, setLlmAuto] = useState(false);

  const customMonthly = useMemo(() => {
    let total = 1;
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

  const customYearly = customMonthly * 10;

  return (
    <div className="p-6 max-w-6xl mx-auto space-y-6">
      <div className="flex items-end justify-between gap-4 flex-wrap">
        <div>
          <h1 className="text-2xl font-semibold text-slate-900">Pricing</h1>
          <p className="text-sm text-slate-500 mt-1">Choose a fixed tier or assemble your own custom bundle.</p>
        </div>
        <label className="inline-flex items-center gap-2 text-sm text-slate-600">
          <input type="checkbox" checked={yearly} onChange={(e) => setYearly(e.target.checked)} />
          Yearly billing (2 months free)
        </label>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-5 gap-4">
        {TIERS.map((tier) => (
          <div key={tier.name} className={`rounded-xl border p-4 ${cardClass(tier.name)}`}>
            <p className="text-sm font-semibold">{tier.name}</p>
            <p className="mt-2 text-2xl font-bold">CHF {yearly ? tier.yearly : tier.monthly}</p>
            <p className="text-xs opacity-80">{yearly ? "per year" : "per month"}</p>
            <ul className="mt-3 text-xs space-y-1 opacity-90">
              {tier.highlights.map((h) => (
                <li key={h}>• {h}</li>
              ))}
            </ul>
          </div>
        ))}
      </div>

      <div className="rounded-xl border border-slate-200 bg-white p-5 space-y-4">
        <h2 className="text-lg font-semibold text-slate-900">Custom Tier Configurator</h2>
        <p className="text-xs text-slate-500">Build a modular plan and estimate your monthly/yearly price.</p>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-4 text-sm">
          <label className="flex items-center justify-between gap-2 border border-slate-200 rounded-lg px-3 py-2">
            Additional web privacy months
            <input type="number" min={0} max={24} value={webMonths} onChange={(e) => setWebMonths(Number(e.target.value || 0))} className="w-20 border border-slate-200 rounded px-2 py-1" />
          </label>
          <label className="flex items-center justify-between gap-2 border border-slate-200 rounded-lg px-3 py-2">
            Export 100k rows
            <input type="checkbox" checked={export100k} onChange={(e) => setExport100k(e.target.checked)} />
          </label>
          <label className="flex items-center justify-between gap-2 border border-slate-200 rounded-lg px-3 py-2">
            Discount steps (5% each)
            <input type="number" min={0} max={8} value={discountSteps} onChange={(e) => setDiscountSteps(Number(e.target.value || 0))} className="w-20 border border-slate-200 rounded px-2 py-1" />
          </label>
          <label className="flex items-center justify-between gap-2 border border-slate-200 rounded-lg px-3 py-2">
            Queue priority level
            <input type="number" min={0} max={4} value={priority} onChange={(e) => setPriority(Number(e.target.value || 0))} className="w-20 border border-slate-200 rounded px-2 py-1" />
          </label>
          <label className="flex items-center justify-between gap-2 border border-slate-200 rounded-lg px-3 py-2">
            Immediate LLM
            <input type="checkbox" checked={immediateLlm} onChange={(e) => setImmediateLlm(e.target.checked)} />
          </label>
          <label className="flex items-center justify-between gap-2 border border-slate-200 rounded-lg px-3 py-2">
            Bring your own LLM keys
            <input type="checkbox" checked={byoKeys} onChange={(e) => setByoKeys(e.target.checked)} />
          </label>
          <label className="flex items-center justify-between gap-2 border border-slate-200 rounded-lg px-3 py-2">
            Flex auto score
            <input type="checkbox" checked={flexAuto} onChange={(e) => setFlexAuto(e.target.checked)} />
          </label>
          <label className="flex items-center justify-between gap-2 border border-slate-200 rounded-lg px-3 py-2">
            LLM auto score
            <input type="checkbox" checked={llmAuto} onChange={(e) => setLlmAuto(e.target.checked)} />
          </label>
        </div>

        <div className="flex items-center justify-between border-t border-slate-100 pt-4">
          <div>
            <p className="text-sm text-slate-500">Estimated custom price</p>
            <p className="text-2xl font-bold text-slate-900">CHF {yearly ? customYearly : customMonthly}</p>
            <p className="text-xs text-slate-500">{yearly ? "per year" : "per month"}</p>
          </div>
          <div className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800 max-w-xs">
            Checkout is not yet implemented. This page currently provides plan visibility and estimation only.
          </div>
        </div>
      </div>
    </div>
  );
}
