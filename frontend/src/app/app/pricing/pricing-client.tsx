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
    <div className="p-6 max-w-6xl mx-auto space-y-8">
      <div>
        <h1 className="text-3xl font-bold text-slate-900">Pricing</h1>
        <p className="text-slate-600 mt-2">Pay for what you use. Get discounts on consumption based on your tier.</p>
      </div>

      <div className="rounded-lg border border-slate-200 bg-slate-50 p-6 space-y-4">
        <h2 className="text-lg font-semibold text-slate-900">Consumption-Based Pricing</h2>
        <p className="text-sm text-slate-700">
          Each search consumes credits based on the number of results returned. Your tier determines:
        </p>
        <ul className="space-y-2 text-sm text-slate-700">
          <li className="flex items-start gap-3">
            <span className="text-blue-600 font-semibold mt-0.5">•</span>
            <span><strong>Credit Discount:</strong> Higher tiers pay less per credit consumed (Free: 0%, Strategist: 30%)</span>
          </li>
          <li className="flex items-start gap-3">
            <span className="text-blue-600 font-semibold mt-0.5">•</span>
            <span><strong>Export Limits:</strong> CSV exports capped by tier (Free: 100 rows, Strategist: 100k rows)</span>
          </li>
          <li className="flex items-start gap-3">
            <span className="text-blue-600 font-semibold mt-0.5">•</span>
            <span><strong>Queue Priority:</strong> Higher tiers get faster job processing</span>
          </li>
          <li className="flex items-start gap-3">
            <span className="text-blue-600 font-semibold mt-0.5">•</span>
            <span><strong>Features:</strong> Tier determines access to advanced features like LLM scoring, API access, and more</span>
          </li>
        </ul>
      </div>

      <div className="space-y-4">
        <div className="flex items-center justify-between">
          <h2 className="text-lg font-semibold text-slate-900">Plans & Benefits</h2>
          <label className="inline-flex items-center gap-2 text-sm text-slate-600">
            <input type="checkbox" checked={yearly} onChange={(e) => setYearly(e.target.checked)} />
            Yearly billing (2 months free)
          </label>
        </div>
        
        <div className="overflow-x-auto">
          <table className="w-full text-sm border border-slate-200 rounded-lg overflow-hidden">
            <thead>
              <tr className="bg-slate-100 border-b border-slate-200">
                <th className="px-4 py-3 text-left font-semibold text-slate-900">Feature</th>
                {TIERS.map((tier) => (
                  <th key={tier.name} className="px-4 py-3 text-center font-semibold text-slate-900">{tier.name}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              <tr className="border-b border-slate-200 bg-white">
                <td className="px-4 py-3 font-semibold text-slate-900">Price</td>
                {TIERS.map((tier) => (
                  <td key={`price-${tier.name}`} className="px-4 py-3 text-center">
                    <p className="font-bold text-lg">CHF {yearly ? tier.yearly : tier.monthly}</p>
                    <p className="text-xs text-slate-500">{yearly ? "per year" : "per month"}</p>
                  </td>
                ))}
              </tr>
              <tr className="border-b border-slate-200 bg-white">
                <td className="px-4 py-3 font-semibold text-slate-900">CSV Export Limit</td>
                {TIERS.map((tier, idx) => {
                  const limits = ["100", "1,000", "5,000", "20,000", "100,000"];
                  return (
                    <td key={`export-${tier.name}`} className="px-4 py-3 text-center text-slate-700">{limits[idx]} rows</td>
                  );
                })}
              </tr>
              <tr className="border-b border-slate-200 bg-white">
                <td className="px-4 py-3 font-semibold text-slate-900">Credit Discount</td>
                {TIERS.map((tier, idx) => {
                  const discounts = ["0%", "10%", "15%", "20%", "30%"];
                  return (
                    <td key={`discount-${tier.name}`} className="px-4 py-3 text-center text-slate-700">{discounts[idx]}</td>
                  );
                })}
              </tr>
              <tr className="border-b border-slate-200 bg-white">
                <td className="px-4 py-3 font-semibold text-slate-900">Multi-user</td>
                {TIERS.map((tier, idx) => (
                  <td key={`multiuser-${tier.name}`} className="px-4 py-3 text-center text-slate-700">{idx > 0 ? "✓" : "–"}</td>
                ))}
              </tr>
              <tr className="border-b border-slate-200 bg-white">
                <td className="px-4 py-3 font-semibold text-slate-900">No Ads</td>
                {TIERS.map((tier, idx) => (
                  <td key={`noads-${tier.name}`} className="px-4 py-3 text-center text-slate-700">{idx > 0 ? "✓" : "–"}</td>
                ))}
              </tr>
              <tr className="border-b border-slate-200 bg-white">
                <td className="px-4 py-3 font-semibold text-slate-900">Immediate LLM Scoring</td>
                {TIERS.map((tier, idx) => (
                  <td key={`immediatellm-${tier.name}`} className="px-4 py-3 text-center text-slate-700">{idx > 1 ? "✓" : "–"}</td>
                ))}
              </tr>
              <tr className="border-b border-slate-200 bg-white">
                <td className="px-4 py-3 font-semibold text-slate-900">LLM Auto Scoring</td>
                {TIERS.map((tier, idx) => (
                  <td key={`llmauto-${tier.name}`} className="px-4 py-3 text-center text-slate-700">{idx > 2 ? "✓" : "–"}</td>
                ))}
              </tr>
              <tr className="border-b border-slate-200 bg-white">
                <td className="px-4 py-3 font-semibold text-slate-900">API Access</td>
                {TIERS.map((tier, idx) => (
                  <td key={`api-${tier.name}`} className="px-4 py-3 text-center text-slate-700">{idx > 3 ? "✓" : "–"}</td>
                ))}
              </tr>
              <tr className="bg-white">
                <td className="px-4 py-3 font-semibold text-slate-900">Custom Features</td>
                {TIERS.map((tier) => (
                  <td key={`custom-${tier.name}`} className="px-4 py-3 text-center text-slate-700">–</td>
                ))}
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      <div className="mt-8 rounded-xl border border-slate-200 bg-white p-6 space-y-4">
        <h2 className="text-lg font-semibold text-slate-900">Custom Tier Configurator</h2>
        <p className="text-sm text-slate-600">Not sure which plan is right for you? Build a modular plan tailored to your needs and see your estimated price.</p>

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
            <p className="text-sm text-slate-600">Estimated custom price</p>
            <p className="text-2xl font-bold text-slate-900">CHF {yearly ? customYearly : customMonthly}</p>
            <p className="text-xs text-slate-600">{yearly ? "per year" : "per month"}</p>
          </div>
          <div className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">
            Checkout is not yet implemented. This page currently provides plan visibility and estimation only.
          </div>
        </div>
      </div>
    </div>
  );
}
