"use client";
import { useState, useCallback, useTransition, Suspense, useEffect, useRef } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import useSWR from "swr";
import {
  Compass, Search, X, Download, Settings, AlertTriangle, BarChart2,
  ArrowLeft, Wand2, ChevronRight, HelpCircle, Sparkles, Grid3x3,
  TrendingUp, Target, MapPin, Zap, Hash, Building2, Copy, Check,
} from "lucide-react";
import { CompanyTable } from "@/components/dashboard/company-table";
import { CompanyPreview } from "@/components/dashboard/company-preview";
import { FilterBar } from "@/components/dashboard/filter-bar";
import { BaloghAdCard } from "@/components/balogh-ad-card";
import { Pagination } from "@/components/dashboard/pagination";
import {
  fetchCompanies, fetchStats, fetchCantons, fetchTaxonomy,
  fetchSavedViews, saveView, deleteView, toggleViewAlert,
  bulkUpdateCompanies, bulkTagCompanies,
  fetchCurrentUser, fetchOrgEffectiveSettings, saveOrgWorkspaceSettings,
  fetchOrg, fetchNogaHierarchy, fetchCategoryStats,
  enqueueGenericJob, semanticSearch,
} from "@/lib/api";
import type { NogaNode, CategoryStats, OrgEffectiveSettings, SemanticSearchResponse } from "@/lib/api";
import type { Company, CompanyFilters, CompanyStats } from "@/lib/types";
import { cn, formatClusterLabel } from "@/lib/utils";
import { getExportLimit } from "@/lib/entitlements";

// ── Constants ─────────────────────────────────────────────────────────────────

const BROWSE_DEFAULTS: CompanyFilters = { sort: "-combined_score", page: 1, page_size: 50, status: "ACTIVE" };

type CategoryType = "ai_category" | "tfidf_cluster" | "keyword" | "noga_code";

const BUSINESS_MODEL_CHIPS = [
  { value: "b2b", label: "B2B", color: "bg-blue-100 text-blue-700 border-blue-200" },
  { value: "b2c", label: "B2C", color: "bg-violet-100 text-violet-700 border-violet-200" },
  { value: "b2g", label: "B2G", color: "bg-amber-100 text-amber-700 border-amber-200" },
  { value: "mixed", label: "Mixed", color: "bg-slate-100 text-slate-600 border-slate-200" },
];

const CAT_TYPE_LABELS: Record<CategoryType, string> = {
  ai_category: "AI Categories",
  tfidf_cluster: "Clusters",
  keyword: "Keywords",
  noga_code: "NOGA",
};

const CAT_TYPE_FILTER_KEY: Record<CategoryType, keyof CompanyFilters> = {
  ai_category: "ai_category",
  tfidf_cluster: "tfidf_cluster",
  keyword: "purpose_keywords",
  noga_code: "noga_code",
};

function buildExportUrl(filters: CompanyFilters): string {
  const params = new URLSearchParams();
  const rest: Record<string, unknown> = { ...filters };
  delete rest.page;
  delete rest.page_size;
  for (const [k, v] of Object.entries(rest)) {
    if (v !== undefined && v !== null && v !== "") params.set(k, String(v));
  }
  return `/api/v1/companies/export.csv?${params.toString()}`;
}

// ── SetupGateBanner ───────────────────────────────────────────────────────────

function SetupGateBanner({ hasApiKey, hasTargetDescription }: { hasApiKey: boolean; hasTargetDescription: boolean }) {
  if (hasApiKey && hasTargetDescription) return null;
  const missing: string[] = [];
  if (!hasApiKey) missing.push("Anthropic API key");
  if (!hasTargetDescription) missing.push("target company description");
  return (
    <div className="mx-4 mt-3 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 flex items-start gap-3">
      <AlertTriangle size={16} className="text-amber-500 shrink-0 mt-0.5" />
      <div className="flex-1 min-w-0">
        <p className="text-sm font-medium text-amber-800">AI scoring is not configured</p>
        <p className="text-xs text-amber-700 mt-0.5">
          Set your {missing.join(" and ")} to enable AI-powered lead scoring.
        </p>
      </div>
      <a
        href="/app/settings?tab=llm"
        className="shrink-0 flex items-center gap-1 text-xs font-medium text-amber-700 hover:text-amber-900 border border-amber-300 bg-white rounded-lg px-2.5 py-1.5 hover:bg-amber-50 transition-colors"
      >
        <Settings size={12} /> Set up
      </a>
    </div>
  );
}

// ── ScoreBreakdown popover ────────────────────────────────────────────────────

interface BreakdownData {
  clusters: number;
  keywords: number;
  distance: number;
  legal_form: number;
  data_quality: number;
  final_score: number;
  cancelled?: boolean;
}

function ScoreBreakdown({ company, onClose }: { company: Company; onClose: () => void }) {
  let breakdown: BreakdownData | null = null;
  try {
    if (company.flex_score_breakdown) breakdown = JSON.parse(company.flex_score_breakdown);
  } catch { /* ignore */ }

  const fmt = (n: number) => (n > 0 ? `+${n}` : String(n));
  const sign = (n: number) => n > 0 ? "text-emerald-600" : n < 0 ? "text-red-500" : "text-slate-400";

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30" onClick={onClose}>
      <div
        className="bg-white rounded-2xl shadow-xl border border-slate-200 w-80 p-5 space-y-4"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between">
          <h3 className="text-sm font-semibold text-slate-800 truncate">{company.name}</h3>
          <button onClick={onClose} className="text-slate-400 hover:text-slate-600"><X size={16} /></button>
        </div>

        <div className="space-y-3">
          {/* AI Score */}
          <div className="flex items-center justify-between text-sm">
            <span className="text-slate-600 flex items-center gap-1.5">
              <Sparkles size={13} className="text-violet-500" /> AI Score
              {company.ai_category && <span className="text-xs text-slate-400">({company.ai_category})</span>}
            </span>
            <span className="font-semibold text-slate-800">{company.ai_score ?? "—"}</span>
          </div>

          {/* Flex Score */}
          <div className="space-y-1.5">
            <div className="flex items-center justify-between text-sm">
              <span className="text-slate-600 flex items-center gap-1.5">
                <Target size={13} className="text-blue-500" /> Flex Score
              </span>
              <span className="font-semibold text-slate-800">{company.flex_score ?? "—"}</span>
            </div>
            {breakdown && !breakdown.cancelled && (
              <div className="pl-5 space-y-1">
                {[
                  { label: "Clusters", val: breakdown.clusters },
                  { label: "Keywords", val: breakdown.keywords },
                  { label: "Distance", val: breakdown.distance },
                  { label: "Legal form", val: breakdown.legal_form },
                  { label: "Data quality", val: breakdown.data_quality },
                ].map(({ label, val }) => (
                  <div key={label} className="flex items-center justify-between text-xs">
                    <span className="text-slate-500">{label}</span>
                    <span className={cn("font-mono font-medium", sign(val))}>{fmt(val)}</span>
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* Web Score */}
          <div className="flex items-center justify-between text-sm">
            <span className="text-slate-600 flex items-center gap-1.5">
              <TrendingUp size={13} className="text-emerald-500" /> Web Score
            </span>
            <span className="font-semibold text-slate-800">{company.web_score ?? "—"}</span>
          </div>

          {/* Combined */}
          <div className="flex items-center justify-between text-sm border-t border-slate-100 pt-2">
            <span className="font-medium text-slate-700">Combined</span>
            <span className="font-bold text-slate-900 text-base">{company.combined_score ?? "—"}</span>
          </div>
        </div>

        <a
          href="/app/settings?tab=flex"
          className="block text-center text-xs text-blue-600 hover:underline"
          onClick={onClose}
        >
          Edit flex scoring settings →
        </a>
      </div>
    </div>
  );
}

// ── ScoringWizard ─────────────────────────────────────────────────────────────

function ScoringWizard({
  taxonomy,
  settings,
  orgId,
  onClose,
  onSaved,
}: {
  taxonomy: Record<string, [string, number][]>;
  settings: OrgEffectiveSettings | undefined;
  orgId: number | null;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [step, setStep] = useState(1);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Step 1: categories + clusters
  const [selCategories, setSelCategories] = useState<string[]>(() =>
    (settings?.scoring_target_clusters || "").split("|").filter(Boolean)
  );
  const [targetClusters, setTargetClusters] = useState<string>(() =>
    settings?.scoring_target_clusters || ""
  );

  // Step 2: keywords
  const [targetKws, setTargetKws] = useState<string>(() =>
    settings?.scoring_target_keywords || ""
  );
  const [excludeKws, setExcludeKws] = useState<string>(() =>
    settings?.scoring_exclude_keywords || ""
  );
  const [kwHitPts, setKwHitPts] = useState<string>(() =>
    settings?.scoring_keyword_hit_points || "10"
  );
  const [kwExclPts, setKwExclPts] = useState<string>(() =>
    settings?.scoring_keyword_exclude_points || "10"
  );

  // Step 3: NOGA targeting
  const [nogaTargets, setNogaTargets] = useState<string>(() =>
    settings?.scoring_noga_targets || ""
  );

  // Step 4: scoring weights
  const [wAi, setWAi] = useState<string>(() => String(Math.round(parseFloat(settings?.scoring_weight_ai || "0.70") * 100)));
  const [wFlex, setWFlex] = useState<string>(() => String(Math.round(parseFloat(settings?.scoring_weight_flex || "0.10") * 100)));
  const [wWeb, setWWeb] = useState<string>(() => String(Math.round(parseFloat(settings?.scoring_weight_web || "0.20") * 100)));

  const aiCats = (taxonomy["categories"] as [string, number][] | undefined) ?? [];
  const clusters = (taxonomy["clusters"] as [string, number][] | undefined) ?? [];

  function toggleCat(cat: string) {
    setSelCategories(prev => prev.includes(cat) ? prev.filter(c => c !== cat) : [...prev, cat]);
  }

  async function handleSave() {
    if (!orgId) return;
    setSaving(true);
    setError(null);
    try {
      const totalW = (parseInt(wAi) || 0) + (parseInt(wFlex) || 0) + (parseInt(wWeb) || 0);
      await saveOrgWorkspaceSettings(orgId, {
        scoring_target_clusters: targetClusters,
        scoring_target_keywords: targetKws,
        scoring_exclude_keywords: excludeKws,
        scoring_keyword_hit_points: kwHitPts,
        scoring_keyword_exclude_points: kwExclPts,
        scoring_noga_targets: nogaTargets,
        scoring_weight_ai: totalW > 0 ? String((parseInt(wAi) || 0) / totalW) : "0.70",
        scoring_weight_flex: totalW > 0 ? String((parseInt(wFlex) || 0) / totalW) : "0.10",
        scoring_weight_web: totalW > 0 ? String((parseInt(wWeb) || 0) / totalW) : "0.20",
      });
      onSaved();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to save settings");
    } finally {
      setSaving(false);
    }
  }

  const totalW = (parseInt(wAi) || 0) + (parseInt(wFlex) || 0) + (parseInt(wWeb) || 0);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" onClick={onClose}>
      <div
        className="bg-white rounded-2xl shadow-2xl border border-slate-200 w-full max-w-lg max-h-[90vh] flex flex-col"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-6 pt-5 pb-4 border-b border-slate-100">
          <div className="flex items-center gap-2">
            <Wand2 size={18} className="text-blue-600" />
            <h2 className="text-base font-semibold text-slate-800">Scoring Wizard</h2>
          </div>
          <button onClick={onClose} className="text-slate-400 hover:text-slate-600"><X size={18} /></button>
        </div>

        {/* Step indicator */}
        <div className="flex items-center gap-1 px-6 py-3 bg-slate-50 border-b border-slate-100">
          {[1, 2, 3, 4, 5].map(s => (
            <div key={s} className="flex items-center gap-1">
              <div className={cn(
                "w-6 h-6 rounded-full flex items-center justify-center text-xs font-bold",
                step === s ? "bg-blue-600 text-white" : step > s ? "bg-emerald-500 text-white" : "bg-slate-200 text-slate-500"
              )}>{step > s ? "✓" : s}</div>
              {s < 5 && <div className={cn("w-6 h-0.5", step > s ? "bg-emerald-300" : "bg-slate-200")} />}
            </div>
          ))}
          <span className="ml-2 text-xs text-slate-500">
            {["Clusters", "Keywords", "NOGA targets", "Score weights", "Save"][step - 1]}
          </span>
        </div>

        {/* Content */}
        <div className="flex-1 overflow-y-auto px-6 py-5 space-y-4">

          {step === 1 && (
            <div className="space-y-4">
              <p className="text-sm text-slate-600">Which AI categories are your primary targets? Their cluster labels will be used in flex scoring.</p>

              <div>
                <p className="text-xs font-medium text-slate-500 uppercase tracking-wide mb-2">AI Categories</p>
                <div className="flex flex-wrap gap-2 max-h-40 overflow-y-auto">
                  {aiCats.slice(0, 30).map(([cat]) => (
                    <button
                      key={cat}
                      onClick={() => toggleCat(cat)}
                      className={cn(
                        "px-2.5 py-1 rounded-full text-xs border transition-colors",
                        selCategories.includes(cat)
                          ? "bg-blue-600 text-white border-blue-600"
                          : "bg-white text-slate-600 border-slate-200 hover:border-blue-300"
                      )}
                    >{cat}</button>
                  ))}
                  {aiCats.length === 0 && <p className="text-xs text-slate-400">No AI categories yet — run AI classification first.</p>}
                </div>
              </div>

              <div>
                <label className="text-xs font-medium text-slate-500 uppercase tracking-wide block mb-1">
                  Target cluster keywords (pipe-separated, e.g. <code className="font-mono text-xs">software|beratung|it</code>)
                </label>
                <textarea
                  value={targetClusters}
                  onChange={(e) => setTargetClusters(e.target.value)}
                  rows={2}
                  className="w-full text-xs border border-slate-200 rounded-lg px-3 py-2 focus:outline-none focus:ring-2 focus:ring-blue-300"
                  placeholder="e.g. software|beratung|entwicklung"
                />
                <p className="text-xs text-slate-400 mt-1">Companies whose cluster label contains any of these terms score higher.</p>
              </div>
            </div>
          )}

          {step === 2 && (
            <div className="space-y-4">
              <p className="text-sm text-slate-600">Set which purpose keywords boost or penalise a company&apos;s flex score.</p>
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="text-xs font-medium text-slateald-500 uppercase tracking-wide block mb-1 text-emerald-700">Target keywords (boost)</label>
                  <textarea
                    value={targetKws}
                    onChange={(e) => setTargetKws(e.target.value)}
                    rows={3}
                    className="w-full text-xs border border-slate-200 rounded-lg px-3 py-2 focus:outline-none focus:ring-2 focus:ring-blue-300"
                    placeholder="e.g. software|saas|entwicklung"
                  />
                  <div className="mt-1 flex items-center gap-1">
                    <span className="text-xs text-slate-400">Points per hit:</span>
                    <input type="number" value={kwHitPts} onChange={e => setKwHitPts(e.target.value)}
                      className="w-14 text-xs border border-slate-200 rounded px-2 py-0.5 text-center" />
                  </div>
                </div>
                <div>
                  <label className="text-xs font-medium uppercase tracking-wide block mb-1 text-red-600">Exclude keywords (penalty)</label>
                  <textarea
                    value={excludeKws}
                    onChange={(e) => setExcludeKws(e.target.value)}
                    rows={3}
                    className="w-full text-xs border border-slate-200 rounded-lg px-3 py-2 focus:outline-none focus:ring-2 focus:ring-blue-300"
                    placeholder="e.g. gastro|handel|bau"
                  />
                  <div className="mt-1 flex items-center gap-1">
                    <span className="text-xs text-slate-400">Points per hit:</span>
                    <input type="number" value={kwExclPts} onChange={e => setKwExclPts(e.target.value)}
                      className="w-14 text-xs border border-slate-200 rounded px-2 py-0.5 text-center" />
                  </div>
                </div>
              </div>
              <p className="text-xs text-slate-400">Use pipe-separated terms. Each matching keyword adds/subtracts the configured points.</p>
            </div>
          )}

          {step === 3 && (
            <div className="space-y-4">
              <p className="text-sm text-slate-600">
                Assign bonus points to companies based on their NOGA industry code. Use the format{" "}
                <code className="font-mono text-xs bg-slate-100 px-1 rounded">CODE:points</code>, pipe-separated.
                More specific codes override parent codes.
              </p>
              <div>
                <label className="text-xs font-medium text-slate-500 uppercase tracking-wide block mb-1">
                  NOGA targets (e.g. <code className="font-mono text-xs">J:20|62:35|620:40</code>)
                </label>
                <textarea
                  value={nogaTargets}
                  onChange={(e) => setNogaTargets(e.target.value)}
                  rows={3}
                  className="w-full text-xs border border-slate-200 rounded-lg px-3 py-2 focus:outline-none focus:ring-2 focus:ring-blue-300 font-mono"
                  placeholder="e.g. J:20|62:35|620:40|M:15"
                />
              </div>
              <div className="rounded-lg border border-slate-200 bg-slate-50 p-3 text-xs text-slate-600 space-y-1">
                <p className="font-medium text-slate-700">How it works</p>
                <p>NOGA codes follow a hierarchy: <strong>J</strong> (section) → <strong>62</strong> (division) → <strong>620</strong> (group) → <strong>6201</strong> (class). The most specific matching code wins.</p>
                <p>Example: <code className="font-mono bg-white px-1 rounded">J:20|62:35</code> gives 20 pts for any IT-sector company, but 35 pts for software companies (division 62 overrides J).</p>
              </div>
              <a href="/app/companies?noga_level=section" target="_blank" className="text-xs text-blue-600 hover:underline">
                Browse NOGA codes in company list →
              </a>
            </div>
          )}

          {step === 4 && (
            <div className="space-y-4">
              <p className="text-sm text-slate-600">How much should each score type contribute to the combined score?</p>
              <div className="space-y-4">
                {[
                  { label: "AI Score", icon: <Sparkles size={14} className="text-violet-500" />, val: wAi, set: setWAi },
                  { label: "Flex Score", icon: <Target size={14} className="text-blue-500" />, val: wFlex, set: setWFlex },
                  { label: "Web Score", icon: <TrendingUp size={14} className="text-emerald-500" />, val: wWeb, set: setWWeb },
                ].map(({ label, icon, val, set }) => (
                  <div key={label} className="flex items-center gap-3">
                    <div className="flex items-center gap-1.5 w-24 shrink-0">
                      {icon}<span className="text-sm text-slate-700">{label}</span>
                    </div>
                    <input
                      type="range" min={0} max={100} value={parseInt(val) || 0}
                      onChange={e => set(e.target.value)}
                      className="flex-1"
                    />
                    <div className="w-14 text-right">
                      <span className="text-sm font-semibold text-slate-800">
                        {totalW > 0 ? Math.round(((parseInt(val) || 0) / totalW) * 100) : 0}%
                      </span>
                    </div>
                  </div>
                ))}
              </div>
              <p className="text-xs text-slate-400">Values are normalised to 100%. For precise control visit Settings → Flex tab.</p>
            </div>
          )}

          {step === 5 && (
            <div className="space-y-4">
              <div className="bg-slate-50 rounded-xl border border-slate-200 p-4 space-y-3 text-sm">
                <p className="font-medium text-slate-700">Summary of changes</p>
                <div className="space-y-1.5 text-xs text-slate-600">
                  <div><span className="font-medium">Target clusters:</span> {targetClusters || "(none)"}</div>
                  <div><span className="font-medium">Target keywords:</span> {targetKws || "(none)"} (+{kwHitPts}pts each)</div>
                  <div><span className="font-medium">Exclude keywords:</span> {excludeKws || "(none)"} (-{kwExclPts}pts each)</div>
                  <div><span className="font-medium">NOGA targets:</span> {nogaTargets || "(none)"}</div>
                  <div><span className="font-medium">Score weights:</span> AI {totalW > 0 ? Math.round(((parseInt(wAi) || 0) / totalW) * 100) : 0}% · Flex {totalW > 0 ? Math.round(((parseInt(wFlex) || 0) / totalW) * 100) : 0}% · Web {totalW > 0 ? Math.round(((parseInt(wWeb) || 0) / totalW) * 100) : 0}%</div>
                </div>
              </div>
              <p className="text-xs text-slate-500">After saving, go to <strong>Settings → Admin → Recalculate Scores</strong> to apply new scoring to all companies.</p>
              {error && <p className="text-xs text-red-600 bg-red-50 border border-red-200 rounded-lg px-3 py-2">{error}</p>}
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="flex items-center justify-between px-6 py-4 border-t border-slate-100">
          <button
            onClick={() => step > 1 ? setStep(s => s - 1) : onClose()}
            className="text-sm text-slate-500 hover:text-slate-700"
          >
            {step > 1 ? "← Back" : "Cancel"}
          </button>
          {step < 5 ? (
            <button
              onClick={() => setStep(s => s + 1)}
              className="flex items-center gap-1.5 px-4 py-2 rounded-lg bg-blue-600 text-white text-sm font-medium hover:bg-blue-700 transition-colors"
            >
              Next <ChevronRight size={14} />
            </button>
          ) : (
            <button
              onClick={handleSave}
              disabled={saving || !orgId}
              className="flex items-center gap-1.5 px-4 py-2 rounded-lg bg-emerald-600 text-white text-sm font-medium hover:bg-emerald-700 disabled:opacity-50 transition-colors"
            >
              {saving ? "Saving…" : "Save settings"}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

// ── ScoreDistributionBar (kept from original) ─────────────────────────────────

function ScoreDistributionBar({ filters }: { filters: CompanyFilters }) {
  const makeF = (min: number, max?: number): CompanyFilters => ({
    ...filters, page: 1, page_size: 1,
    min_combined_score: min,
    ...(max !== undefined ? { max_combined_score: max } : {}),
  });
  const { data: top } = useSWR(["score-dist-top", filters], () => fetchCompanies(makeF(70)), { keepPreviousData: true });
  const { data: mid } = useSWR(["score-dist-mid", filters], () => fetchCompanies(makeF(40, 69)), { keepPreviousData: true });
  const { data: low } = useSWR(["score-dist-low", filters], () => fetchCompanies(makeF(1, 39)), { keepPreviousData: true });
  const { data: zero } = useSWR(["score-dist-zero", filters], () => fetchCompanies(makeF(0, 0)), { keepPreviousData: true });
  const t = top?.total ?? 0, m = mid?.total ?? 0, l = low?.total ?? 0, z = zero?.total ?? 0;
  const total = t + m + l + z;
  if (!total) return null;
  const pct = (n: number) => Math.round((n / total) * 100);
  return (
    <div className="flex items-center gap-2 text-[10px] text-slate-500">
      <span className="hidden sm:inline">Score dist:</span>
      <div className="flex h-2 w-20 rounded-full overflow-hidden gap-px">
        {t > 0 && <div className="bg-green-500" style={{ width: `${pct(t)}%` }} title={`Top ≥70: ${t}`} />}
        {m > 0 && <div className="bg-yellow-400" style={{ width: `${pct(m)}%` }} title={`Mid 40–69: ${m}`} />}
        {l > 0 && <div className="bg-orange-300" style={{ width: `${pct(l)}%` }} title={`Low 1–39: ${l}`} />}
        {z > 0 && <div className="bg-slate-200" style={{ width: `${pct(z)}%` }} title={`Unscored: ${z}`} />}
      </div>
      <span className="text-green-600">{pct(t)}%</span>
      <span className="text-yellow-600">{pct(m)}%</span>
      <span className="text-slate-400">{pct(z)}% unscored</span>
    </div>
  );
}

// ── CategoryScoreBar — mini bar for a single category ────────────────────────

function CategoryScoreBar({ bands }: { bands: CategoryStats["bands"] }) {
  const total = (bands["80plus"] ?? 0) + (bands["60to80"] ?? 0) + (bands["40to60"] ?? 0) + (bands["below40"] ?? 0) + (bands["unscored"] ?? 0);
  if (!total) return null;
  const pct = (n: number) => Math.round((n / total) * 100);
  return (
    <div className="flex h-1.5 w-full rounded-full overflow-hidden gap-px">
      {bands["80plus"] > 0 && <div className="bg-emerald-500" style={{ width: `${pct(bands["80plus"])}%` }} title={`≥80: ${bands["80plus"]}`} />}
      {bands["60to80"] > 0 && <div className="bg-yellow-400" style={{ width: `${pct(bands["60to80"])}%` }} title={`60–79: ${bands["60to80"]}`} />}
      {bands["40to60"] > 0 && <div className="bg-orange-300" style={{ width: `${pct(bands["40to60"])}%` }} title={`40–59: ${bands["40to60"]}`} />}
      {(bands["below40"] > 0 || bands["unscored"] > 0) && (
        <div className="bg-slate-200 flex-1" title={`<40 or unscored`} />
      )}
    </div>
  );
}

// ── ScoreAvgBadge ─────────────────────────────────────────────────────────────

function ScoreAvgBadge({ score }: { score: number | null | undefined }) {
  if (score == null) return null;
  const color = score >= 70 ? "bg-emerald-100 text-emerald-700" : score >= 50 ? "bg-yellow-100 text-yellow-700" : "bg-slate-100 text-slate-500";
  return <span className={cn("text-[10px] font-semibold px-1.5 py-0.5 rounded-full", color)}>{score}</span>;
}

// ── Type → accent color map ───────────────────────────────────────────────────

const CAT_TYPE_ACCENT: Record<CategoryType, string> = {
  tfidf_cluster: "border-blue-400",
  ai_category: "border-violet-400",
  keyword: "border-emerald-400",
  noga_code: "border-amber-400",
};

const CAT_TYPE_ICON: Record<CategoryType, React.ReactNode> = {
  tfidf_cluster: <Grid3x3 size={11} />,
  ai_category: <Sparkles size={11} />,
  keyword: <Hash size={11} />,
  noga_code: <Building2 size={11} />,
};

// ── SemanticSearchBar ─────────────────────────────────────────────────────────

interface SemanticSearchBarProps {
  onSelect: (type: CategoryType, value: string) => void;
}

function SemanticSearchBar({ onSelect }: SemanticSearchBarProps) {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<SemanticSearchResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, []);

  useEffect(() => {
    if (timerRef.current) clearTimeout(timerRef.current);
    if (!query.trim() || query.length < 2) { setResults(null); setOpen(false); return; }
    timerRef.current = setTimeout(async () => {
      setLoading(true);
      try {
        const r = await semanticSearch(query, 6);
        setResults(r);
        setOpen(true);
      } catch { setResults(null); }
      finally { setLoading(false); }
    }, 300);
    return () => { if (timerRef.current) clearTimeout(timerRef.current); };
  }, [query]);

  const makeSection = (
    label: string,
    type: CategoryType,
    items: SemanticSearchResponse["clusters"],
  ) => ({ label, type, items });

  const sections: Array<{ label: string; type: CategoryType; items: SemanticSearchResponse["clusters"] }> = results
    ? [
      makeSection("Clusters", "tfidf_cluster", results.clusters),
      makeSection("AI Categories", "ai_category", results.categories),
      makeSection("Keywords", "keyword", results.keywords),
      makeSection("NOGA codes", "noga_code", results.noga_codes),
    ].filter((s) => s.items.length > 0)
    : [];

  return (
    <div ref={containerRef} className="relative w-full">
      <div className="relative">
        <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400 pointer-events-none" />
        <input
          type="text"
          value={query}
          onChange={e => setQuery(e.target.value)}
          onFocus={() => results && setOpen(true)}
          onKeyDown={e => e.key === "Escape" && setOpen(false)}
          placeholder="Search clusters, categories, keywords, NOGA… (DE/FR/IT/EN)"
          className="w-full pl-9 pr-10 py-2.5 text-sm border border-slate-200 rounded-xl bg-slate-50 focus:bg-white focus:outline-none focus:ring-2 focus:ring-blue-300 focus:border-blue-300 transition-colors placeholder:text-slate-400"
        />
        {loading && (
          <div className="absolute right-3 top-1/2 -translate-y-1/2">
            <div className="w-4 h-4 border-2 border-blue-300 border-t-blue-600 rounded-full animate-spin" />
          </div>
        )}
        {query && !loading && (
          <button onClick={() => { setQuery(""); setResults(null); setOpen(false); }}
            className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-400 hover:text-slate-600">
            <X size={13} />
          </button>
        )}
      </div>

      {open && sections.length > 0 && (
        <div className="absolute z-50 top-full mt-1.5 left-0 right-0 bg-white border border-slate-200 rounded-xl shadow-lg overflow-hidden max-h-80 overflow-y-auto">
          {sections.map(({ label, type, items }) => (
            <div key={type}>
              <div className="flex items-center gap-1.5 px-3 pt-2.5 pb-1">
                <span className={cn("p-0.5 rounded text-white", {
                  "bg-blue-500": type === "tfidf_cluster",
                  "bg-violet-500": type === "ai_category",
                  "bg-emerald-500": type === "keyword",
                  "bg-amber-500": type === "noga_code",
                })}>{CAT_TYPE_ICON[type]}</span>
                <span className="text-[10px] font-semibold text-slate-500 uppercase tracking-wider">{label}</span>
              </div>
              {(items as Array<{ value: string; count: number; similarity: number }>).map(item => (
                <button
                  key={item.value}
                  onMouseDown={() => { onSelect(type, item.value); setQuery(""); setOpen(false); }}
                  className="w-full flex items-center gap-2 px-3 py-2 hover:bg-slate-50 text-left group"
                >
                  <span className="flex-1 text-sm text-slate-700 truncate group-hover:text-blue-700">
                    {type === "tfidf_cluster" ? formatClusterLabel(item.value.split("|")[0]) : item.value}
                  </span>
                  <span className="text-[10px] text-slate-400 shrink-0">{item.count.toLocaleString()}</span>
                  <div className="w-10 h-1 bg-slate-100 rounded-full overflow-hidden shrink-0">
                    <div className="h-full bg-blue-400 rounded-full" style={{ width: `${Math.round(item.similarity * 100)}%` }} />
                  </div>
                </button>
              ))}
            </div>
          ))}
          <div className="px-3 py-2 border-t border-slate-100 text-[10px] text-slate-400 text-center">
            Results ranked by semantic similarity · {results?.query}
          </div>
        </div>
      )}
    </div>
  );
}

// ── RecommendedStrip ──────────────────────────────────────────────────────────

interface RecommendedStripProps {
  taxonomy: Record<string, [string, number][]>;
  settings: OrgEffectiveSettings | undefined;
  onSelect: (type: CategoryType, value: string) => void;
}

function RecommendedStrip({ taxonomy, settings, onSelect }: RecommendedStripProps) {
  type Pick = { type: CategoryType; value: string; label: string; count: number; hint: string };
  const picks: Pick[] = [];

  // 1. Cluster with most companies
  const clusters = (taxonomy["clusters"] as [string, number][] | undefined) ?? [];
  if (clusters[0]) {
    picks.push({ type: "tfidf_cluster", value: clusters[0][0], label: formatClusterLabel(clusters[0][0].split("|")[0]), count: clusters[0][1], hint: "Largest cluster" });
  }

  // 2. AI category with highest avg_ai score
  const catsEnriched = (taxonomy["categories_enriched"] as unknown as [string, number, Record<string, number | null>][] | undefined) ?? [];
  const topAi = catsEnriched.sort((a, b) => (b[2]?.avg_ai_score ?? 0) - (a[2]?.avg_ai_score ?? 0))[0];
  if (topAi) {
    picks.push({ type: "ai_category", value: topAi[0], label: topAi[0], count: topAi[1], hint: `Avg AI ${topAi[2]?.avg_ai_score ?? "–"}` });
  }

  // 3. NOGA code with most companies
  const noga = (taxonomy["noga_codes"] as [string, number][] | undefined) ?? [];
  if (noga[0]) {
    const [code, label] = noga[0][0].split(" — ");
    picks.push({ type: "noga_code", value: code?.trim() ?? noga[0][0], label: label?.trim() ?? noga[0][0], count: noga[0][1], hint: "Top NOGA" });
  }

  // 4. Org scoring target if set
  const orgTarget = (settings?.scoring_target_clusters || "").split("|").filter(Boolean)[0];
  const orgMatch = orgTarget ? clusters.find(([l]) => l.toLowerCase().includes(orgTarget.toLowerCase())) : null;
  if (orgMatch) {
    picks.push({ type: "tfidf_cluster", value: orgMatch[0], label: formatClusterLabel(orgMatch[0].split("|")[0]), count: orgMatch[1], hint: "Your target" });
  } else {
    // Fall back to top keyword
    const kws = (taxonomy["keywords"] as [string, number][] | undefined) ?? [];
    if (kws[0]) picks.push({ type: "keyword", value: kws[0][0], label: kws[0][0], count: kws[0][1], hint: "Top keyword" });
  }

  if (picks.length === 0) return null;

  const accentBg: Record<CategoryType, string> = {
    tfidf_cluster: "bg-blue-50 border-blue-200 text-blue-700 hover:bg-blue-100",
    ai_category: "bg-violet-50 border-violet-200 text-violet-700 hover:bg-violet-100",
    keyword: "bg-emerald-50 border-emerald-200 text-emerald-700 hover:bg-emerald-100",
    noga_code: "bg-amber-50 border-amber-200 text-amber-700 hover:bg-amber-100",
  };

  return (
    <div className="px-4 py-2 border-b border-slate-100 bg-slate-50/50 shrink-0">
      <div className="flex items-center gap-2 flex-wrap">
        <span className="text-[10px] font-semibold text-slate-400 uppercase tracking-wider shrink-0 flex items-center gap-1">
          <Zap size={9} /> Suggested
        </span>
        {picks.slice(0, 4).map((pick, i) => (
          <button
            key={i}
            onClick={() => onSelect(pick.type, pick.value)}
            className={cn("flex items-center gap-1.5 px-2.5 py-1 rounded-full border text-xs font-medium transition-colors", accentBg[pick.type])}
            title={pick.hint}
          >
            <span className="opacity-70">{CAT_TYPE_ICON[pick.type]}</span>
            <span className="truncate max-w-[120px]">{pick.label}</span>
            <span className="opacity-60 text-[10px]">{pick.count.toLocaleString()}</span>
          </button>
        ))}
      </div>
    </div>
  );
}

// ── CategoryGrid (Layer 1) ────────────────────────────────────────────────────

interface CategoryGridProps {
  taxonomy: Record<string, [string, number][]>;
  onSelectCategory: (type: CategoryType, value: string) => void;
  onBrowseAll: () => void;
  hasApiKey: boolean;
  hasTargetDescription: boolean;
  orgId: number | null;
  settings: OrgEffectiveSettings | undefined;
  onOpenWizard: () => void;
}

function CategoryGrid({
  taxonomy, onSelectCategory, onBrowseAll, hasApiKey, hasTargetDescription, orgId, settings, onOpenWizard,
}: CategoryGridProps) {
  const [catType, setCatType] = useState<CategoryType>("ai_category");
  const [search, setSearch] = useState("");

  const { data: nogaTree } = useSWR(
    catType === "noga_code" ? "noga-hierarchy" : null,
    fetchNogaHierarchy,
    { revalidateOnFocus: false }
  );

  const aiCatsEnriched = (taxonomy["categories_enriched"] as unknown as [string, number, Record<string, number | null>][] | undefined) ?? [];
  const aiCats = (taxonomy["categories"] as [string, number][] | undefined) ?? [];
  const clusters = (taxonomy["clusters"] as [string, number][] | undefined) ?? [];
  const keywords = (taxonomy["keywords"] as [string, number][] | undefined) ?? [];
  const nogaCodes = (taxonomy["noga_codes"] as [string, number][] | undefined) ?? [];

  // Org target sets for ★ badge
  const orgTargetClusters = new Set(
    (settings?.scoring_target_clusters || "").split("|").map(s => s.trim().toLowerCase()).filter(Boolean)
  );
  const orgTargetKeywords = new Set(
    (settings?.scoring_target_keywords || "").split("|").map(s => s.trim().toLowerCase()).filter(Boolean)
  );
  const orgNogaTargetCodes = new Set(
    (settings?.scoring_noga_targets || "").split("|").map(s => s.split(":")[0].trim()).filter(Boolean)
  );

  function isOrgTarget(item: { name: string }): boolean {
    const nameLower = item.name.toLowerCase();
    if (catType === "tfidf_cluster") {
      return Array.from(orgTargetClusters).some(t => nameLower.includes(t));
    }
    if (catType === "keyword") {
      return orgTargetKeywords.has(nameLower);
    }
    if (catType === "noga_code") {
      return orgNogaTargetCodes.has(item.name);
    }
    return false;
  }

  function getItems() {
    const q = search.toLowerCase();
    if (catType === "ai_category") {
      const items = aiCatsEnriched.length > 0
        ? aiCatsEnriched.map(([name, count, scores]) => ({ name, count, avgAi: scores?.avg_ai_score ?? null }))
        : aiCats.map(([name, count]) => ({ name, count, avgAi: null }));
      return items.filter(i => !q || i.name.toLowerCase().includes(q));
    }
    if (catType === "tfidf_cluster") {
      return clusters
        .filter(([label]) => !q || label.toLowerCase().includes(q))
        .map(([label, count]) => ({ name: label, count, avgAi: null }));
    }
    if (catType === "keyword") {
      return keywords
        .filter(([kw]) => !q || kw.toLowerCase().includes(q))
        .map(([kw, count]) => ({ name: kw, count, avgAi: null }));
    }
    // noga_code
    return nogaCodes
      .filter(([code]) => !q || code.toLowerCase().includes(q))
      .map(([code, count]) => ({ name: code, count, avgAi: null }));
  }

  const items = getItems();

  function getDisplayName(item: { name: string }) {
    if (catType === "tfidf_cluster") {
      return formatClusterLabel(item.name.split("|")[0]);
    }
    return item.name;
  }

  const isEmpty = items.length === 0;

  return (
    <div className="flex flex-col h-full overflow-hidden">
      {/* Top bar */}
      <div className="flex items-center gap-3 px-4 py-3 bg-white border-b border-slate-100 shrink-0 flex-wrap">
        {/* Semantic search — spans full width, displaces tab-local search */}
        <div className="flex-1 min-w-64">
          <SemanticSearchBar onSelect={(type, value) => { onSelectCategory(type, value); }} />
        </div>

        {/* Scoring wizard button */}
        <button
          onClick={onOpenWizard}
          disabled={!orgId}
          className="flex items-center gap-1.5 px-3 py-2 rounded-lg text-sm font-medium border border-slate-200 bg-white text-slate-600 hover:border-blue-300 hover:text-blue-600 disabled:opacity-40 transition-colors shrink-0"
          title={!orgId ? "Requires an org workspace" : "Configure scoring"}
        >
          <Wand2 size={13} /> Scoring Wizard
        </button>

        {/* Browse all */}
        <button
          onClick={onBrowseAll}
          className="flex items-center gap-1.5 px-3 py-2 rounded-lg text-sm font-medium bg-blue-600 text-white hover:bg-blue-700 transition-colors shrink-0"
        >
          <Grid3x3 size={13} /> Browse all
        </button>
      </div>

      {/* Recommended starting points */}
      <RecommendedStrip taxonomy={taxonomy} settings={settings} onSelect={onSelectCategory} />

      {/* Category type tabs */}
      <div className="flex items-center gap-0 px-4 border-b border-slate-100 bg-white shrink-0">
        {(Object.keys(CAT_TYPE_LABELS) as CategoryType[]).map(type => (
          <button
            key={type}
            onClick={() => { setCatType(type); setSearch(""); }}
            className={cn(
              "px-4 py-2.5 text-sm font-medium border-b-2 transition-colors",
              catType === type
                ? "border-blue-600 text-blue-700"
                : "border-transparent text-slate-500 hover:text-slate-700"
            )}
          >
            {CAT_TYPE_LABELS[type]}
          </button>
        ))}
        {/* Per-tab filter search (kept for tab-specific filtering) */}
        <div className="ml-auto relative py-1">
          <Search size={12} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-400 pointer-events-none" />
          <input
            type="text"
            value={search}
            onChange={e => setSearch(e.target.value)}
            placeholder={`Filter ${CAT_TYPE_LABELS[catType].toLowerCase()}…`}
            className="pl-7 pr-7 py-1.5 text-xs border border-slate-200 rounded-lg focus:outline-none focus:ring-1 focus:ring-blue-300 bg-white w-44"
          />
          {search && (
            <button onClick={() => setSearch("")} className="absolute right-2.5 top-1/2 -translate-y-1/2 text-slate-400 hover:text-slate-600">
              <X size={11} />
            </button>
          )}
        </div>
      </div>

      {/* Setup banners */}
      {catType === "ai_category" && !hasApiKey && (
        <div className="mx-4 mt-3 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 flex items-center gap-3 shrink-0">
          <AlertTriangle size={14} className="text-amber-500 shrink-0" />
          <p className="text-xs text-amber-700 flex-1">Configure your Anthropic API key to enable AI classification.</p>
          <a href="/app/settings?tab=llm" className="text-xs font-medium text-amber-700 hover:underline shrink-0">Set up →</a>
        </div>
      )}

      {/* Cards or NOGA tree */}
      <div className="flex-1 overflow-y-auto px-4 py-4">
        {catType === "noga_code" ? (
          <div className="rounded-xl border border-slate-200 bg-white">
            {!nogaTree ? (
              <div className="h-40 flex items-center justify-center text-slate-400 text-sm">Loading NOGA hierarchy…</div>
            ) : nogaTree.length === 0 ? (
              <div className="h-40 flex items-center justify-center text-slate-400 text-sm">No NOGA data yet.</div>
            ) : (
              <div className="py-2">
                {nogaTree
                  .filter(node => !search || node.label.toLowerCase().includes(search.toLowerCase()) || node.code.toLowerCase().includes(search.toLowerCase()))
                  .map(node => (
                    <NogaTreeNode
                      key={node.code}
                      node={node}
                      depth={0}
                      orgNogaTargetCodes={orgNogaTargetCodes}
                      onSelect={(code) => onSelectCategory("noga_code", code)}
                    />
                  ))
                }
              </div>
            )}
          </div>
        ) : isEmpty ? (
          <div className="flex flex-col items-center justify-center h-40 text-center">
            <p className="text-slate-500 text-sm">
              {search ? `No ${CAT_TYPE_LABELS[catType].toLowerCase()} match "${search}"` : `No ${CAT_TYPE_LABELS[catType].toLowerCase()} yet.`}
            </p>
            {catType === "ai_category" && !search && (
              <a href="/app/jobs" className="text-xs text-blue-600 hover:underline mt-1">Run AI classification →</a>
            )}
            {catType === "tfidf_cluster" && !search && (
              <a href="/app/jobs" className="text-xs text-blue-600 hover:underline mt-1">Run clustering job →</a>
            )}
          </div>
        ) : (
          <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5 gap-3">
            {items.map((item) => {
              const isTarget = isOrgTarget(item);
              const accent = CAT_TYPE_ACCENT[catType];
              return (
                <button
                  key={item.name}
                  onClick={() => onSelectCategory(catType, item.name)}
                  className={cn(
                    "group flex flex-col items-start p-3 rounded-xl border-l-[3px] border border-slate-200 bg-white hover:border-blue-200 hover:shadow-sm transition-all text-left",
                    accent,
                    isTarget ? "bg-amber-50/40" : ""
                  )}
                >
                  <div className="flex items-start justify-between w-full gap-1 mb-2">
                    <span className="text-sm font-medium text-slate-700 group-hover:text-blue-700 leading-tight line-clamp-2 flex-1">
                      {getDisplayName(item)}
                    </span>
                    <div className="flex items-center gap-1 shrink-0">
                      {isTarget && (
                        <span className="text-amber-500 text-xs" title="Matches your org targets">★</span>
                      )}
                      {item.avgAi != null && <ScoreAvgBadge score={item.avgAi} />}
                    </div>
                  </div>
                  <span className="text-xs text-slate-400">{item.count.toLocaleString()} companies</span>
                  {item.avgAi != null && (
                    <div className="mt-1.5 w-full h-1 bg-slate-100 rounded-full overflow-hidden">
                      <div className={cn("h-full rounded-full transition-all", item.avgAi >= 70 ? "bg-emerald-400" : item.avgAi >= 50 ? "bg-yellow-400" : "bg-slate-300")}
                        style={{ width: `${item.avgAi}%` }} />
                    </div>
                  )}
                  <div className="mt-2 flex items-center gap-1 text-xs text-blue-600 opacity-0 group-hover:opacity-100 transition-opacity">
                    Explore <ChevronRight size={11} />
                  </div>
                </button>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}

// ── NogaTreeNode — collapsible NOGA hierarchy node ───────────────────────────
// (defined above CategoryGrid in the module so CategoryGrid can reference it)

function NogaTreeNode({
  node,
  depth,
  orgNogaTargetCodes,
  onSelect,
}: {
  node: NogaNode;
  depth: number;
  orgNogaTargetCodes: Set<string>;
  onSelect: (code: string) => void;
}) {
  const [expanded, setExpanded] = useState(depth === 0);
  const hasChildren = node.children && node.children.length > 0;
  const isTarget = orgNogaTargetCodes.has(node.code);

  return (
    <div>
      <div
        className={cn(
          "flex items-center gap-1 py-1 px-2 rounded-lg hover:bg-slate-50 cursor-pointer group",
          depth === 0 ? "font-semibold" : ""
        )}
        style={{ paddingLeft: `${8 + depth * 16}px` }}
      >
        {hasChildren ? (
          <button
            onClick={() => setExpanded(e => !e)}
            className="text-slate-400 hover:text-slate-600 shrink-0 w-4"
          >
            {expanded ? "▾" : "▸"}
          </button>
        ) : (
          <span className="w-4 shrink-0" />
        )}
        <button
          onClick={() => onSelect(node.code)}
          className="flex-1 flex items-center gap-1.5 text-left min-w-0"
        >
          <span className={cn(
            "font-mono text-xs shrink-0 px-1 rounded",
            isTarget ? "bg-amber-100 text-amber-700" : "bg-slate-100 text-slate-600"
          )}>
            {node.code}
            {isTarget && " ★"}
          </span>
          <span className="text-sm text-slate-700 truncate group-hover:text-blue-700">{node.label}</span>
          <span className="text-xs text-slate-400 shrink-0 ml-auto">{node.count.toLocaleString()}</span>
        </button>
      </div>
      {expanded && hasChildren && (
        <div>
          {node.children.map(child => (
            <NogaTreeNode
              key={child.code}
              node={child}
              depth={depth + 1}
              orgNogaTargetCodes={orgNogaTargetCodes}
              onSelect={onSelect}
            />
          ))}
        </div>
      )}
    </div>
  );
}

// ── CategoryDetail (Layer 2) ──────────────────────────────────────────────────

interface CategoryDetailProps {
  catType: CategoryType;
  catValue: string;
  initialTaxonomy: Record<string, [string, number][]>;
  initialCantons: string[];
  initialStats: CompanyStats;
  orgId: number | null;
  onBack: () => void;
  onOpenWizard: () => void;
}

function CategoryDetail({
  catType, catValue, initialTaxonomy, initialCantons, initialStats, orgId, onBack, onOpenWizard,
}: CategoryDetailProps) {
  const router = useRouter();
  const [selectedBand, setSelectedBand] = useState<string | null>(null);
  const [selectedBm, setSelectedBm] = useState<string | null>(null);
  const [breakdownCompany, setBreakdownCompany] = useState<Company | null>(null);
  const [copied, setCopied] = useState(false);

  function handleCopyFilterUrl() {
    const params = new URLSearchParams({ browse: "1", [CAT_TYPE_FILTER_KEY[catType]]: catValue });
    const url = `${window.location.origin}/app/explorer?${params.toString()}`;
    navigator.clipboard.writeText(url).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  }

  const { data: stats } = useSWR(
    ["cat-stats", catType, catValue, orgId],
    () => fetchCategoryStats(catType, catValue, orgId),
    { dedupingInterval: 300000 }
  );

  // Filters for the company table
  const baseFilters: CompanyFilters = {
    ...BROWSE_DEFAULTS,
    [CAT_TYPE_FILTER_KEY[catType]]: catValue,
    status: "ACTIVE",
  };

  const tableFilters: CompanyFilters = {
    ...baseFilters,
    ...(selectedBand === "80plus" ? { min_combined_score: 80 } : {}),
    ...(selectedBand === "60to80" ? { min_combined_score: 60, max_combined_score: 79 } : {}),
    ...(selectedBand === "40to60" ? { min_combined_score: 40, max_combined_score: 59 } : {}),
    ...(selectedBand === "below40" ? { max_combined_score: 39 } : {}),
    ...(selectedBm ? { business_model: selectedBm } : {}),
  };

  const [filters, setFilters] = useState<CompanyFilters>(tableFilters);
  const [selectedCompany, setSelectedCompany] = useState<Company | null>(null);
  const [, startTransition] = useTransition();

  const { data: page, isLoading, mutate: mutateCompanies } = useSWR(
    ["companies", filters],
    () => fetchCompanies(filters),
    { keepPreviousData: true }
  );

  const { data: me } = useSWR("me", fetchCurrentUser);
  const { data: org } = useSWR(me?.org?.id ? ["org-detail", me.org.id] : null, () => fetchOrg(me!.org!.id));

  const exportLimit = org ? getExportLimit({ tier: org.tier, customFeatures: org.custom_features }) : 100;
  const matchingCount = page?.total ?? 0;

  function handleBandClick(band: string) {
    const next = selectedBand === band ? null : band;
    setSelectedBand(next);
    setFilters({
      ...baseFilters,
      ...(next === "80plus" ? { min_combined_score: 80 } : {}),
      ...(next === "60to80" ? { min_combined_score: 60, max_combined_score: 79 } : {}),
      ...(next === "40to60" ? { min_combined_score: 40, max_combined_score: 59 } : {}),
      ...(next === "below40" ? { max_combined_score: 39 } : {}),
      ...(selectedBm ? { business_model: selectedBm } : {}),
      page: 1,
    });
  }

  function handleBmClick(bm: string) {
    const next = selectedBm === bm ? null : bm;
    setSelectedBm(next);
    setFilters({
      ...baseFilters,
      ...(selectedBand === "80plus" ? { min_combined_score: 80 } : {}),
      ...(selectedBand === "60to80" ? { min_combined_score: 60, max_combined_score: 79 } : {}),
      ...(selectedBand === "40to60" ? { min_combined_score: 40, max_combined_score: 59 } : {}),
      ...(selectedBand === "below40" ? { max_combined_score: 39 } : {}),
      ...(next ? { business_model: next } : {}),
      page: 1,
    });
  }

  function handleBrowseFull() {
    const params = new URLSearchParams({ browse: "1", [CAT_TYPE_FILTER_KEY[catType]]: catValue });
    router.push(`/app/explorer?${params.toString()}`);
  }

  const displayName = catType === "tfidf_cluster" ? formatClusterLabel(catValue.split("|")[0]) : catValue;
  const unocoredPct = stats ? Math.round((stats.unscored_count / (stats.count || 1)) * 100) : 0;

  return (
    <div className="flex flex-col h-full overflow-hidden">
      {/* Header */}
      <div className="flex items-center gap-3 px-4 py-3 bg-white border-b border-slate-100 shrink-0 flex-wrap">
        <button onClick={onBack} className="flex items-center gap-1.5 text-sm text-slate-500 hover:text-slate-700 shrink-0">
          <ArrowLeft size={15} /> Back
        </button>
        <div className="flex items-center gap-2 flex-1 min-w-0">
          <span className="text-xs font-medium text-slate-400 uppercase tracking-wide shrink-0">{CAT_TYPE_LABELS[catType]}</span>
          <h1 className="text-base font-semibold text-slate-800 truncate">{displayName}</h1>
          {stats && <span className="text-sm text-slate-400 shrink-0">{stats.count.toLocaleString()} companies</span>}
        </div>
        <div className="flex items-center gap-2 ml-auto shrink-0">
          {orgId && (
            <button
              onClick={onOpenWizard}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium border border-slate-200 bg-white text-slate-600 hover:border-blue-300 hover:text-blue-600 transition-colors"
            >
              <Wand2 size={12} /> Improve scoring
            </button>
          )}
          <button
            onClick={handleCopyFilterUrl}
            className={cn(
              "flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium border transition-colors",
              copied
                ? "border-emerald-300 bg-emerald-50 text-emerald-700"
                : "border-slate-200 bg-white text-slate-600 hover:border-slate-300 hover:text-slate-800"
            )}
            title="Copy filter URL to clipboard"
          >
            {copied ? <Check size={12} /> : <Copy size={12} />}
            {copied ? "Copied!" : "Copy URL"}
          </button>
          <button
            onClick={handleBrowseFull}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium bg-blue-600 text-white hover:bg-blue-700 transition-colors"
          >
            Full browse <ChevronRight size={12} />
          </button>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto">
        {/* ML coverage warning */}
        {stats && unocoredPct > 50 && (
          <div className="mx-4 mt-3 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 flex items-center gap-3">
            <AlertTriangle size={14} className="text-amber-500 shrink-0" />
            <p className="text-xs text-amber-700 flex-1">
              {stats.unscored_count.toLocaleString()} companies ({unocoredPct}%) in this category have not been AI-scored yet.
            </p>
            <a href="/app/jobs" className="text-xs font-medium text-amber-700 hover:underline shrink-0">Run AI scoring →</a>
          </div>
        )}

        {/* Score Landscape */}
        <div className="mx-4 mt-4 grid grid-cols-1 lg:grid-cols-2 gap-4">
          {/* Score distribution */}
          <div className="rounded-xl border border-slate-200 bg-white p-4">
            <h3 className="text-xs font-semibold text-slate-500 uppercase tracking-wide mb-3">Score Distribution</h3>
            {stats ? (
              <div className="space-y-2">
                {[
                  { key: "80plus", label: "Top (≥ 80)", color: "bg-emerald-500" },
                  { key: "60to80", label: "Strong (60–79)", color: "bg-yellow-400" },
                  { key: "40to60", label: "Good (40–59)", color: "bg-orange-300" },
                  { key: "below40", label: "Low (< 40)", color: "bg-slate-300" },
                  { key: "unscored", label: "Unscored", color: "bg-slate-100" },
                ].map(({ key, label, color }) => {
                  const val = stats.bands[key as keyof typeof stats.bands] ?? 0;
                  const pct = Math.round((val / (stats.count || 1)) * 100);
                  return (
                    <button
                      key={key}
                      onClick={() => key !== "unscored" && handleBandClick(key)}
                      className={cn(
                        "w-full flex items-center gap-2 text-xs rounded-lg px-2 py-1.5 transition-colors",
                        key !== "unscored" ? "hover:bg-slate-50 cursor-pointer" : "cursor-default",
                        selectedBand === key ? "bg-blue-50 ring-1 ring-blue-200" : ""
                      )}
                    >
                      <div className="w-24 text-right text-slate-500 shrink-0">{label}</div>
                      <div className="flex-1 h-4 bg-slate-100 rounded-full overflow-hidden">
                        <div className={cn("h-full rounded-full", color)} style={{ width: `${pct}%` }} />
                      </div>
                      <span className="w-12 text-right tabular-nums text-slate-600 shrink-0">{val.toLocaleString()}</span>
                      <span className="w-8 text-right text-slate-400 shrink-0">{pct}%</span>
                    </button>
                  );
                })}
                {selectedBand && (
                  <button onClick={() => { setSelectedBand(null); setFilters({ ...baseFilters, page: 1 }); }}
                    className="text-xs text-blue-600 hover:underline mt-1 block">
                    Clear band filter
                  </button>
                )}
              </div>
            ) : (
              <div className="h-32 flex items-center justify-center text-slate-400 text-sm">Loading…</div>
            )}
          </div>

          {/* Score components + cantons */}
          <div className="space-y-4">
            <div className="rounded-xl border border-slate-200 bg-white p-4">
              <h3 className="text-xs font-semibold text-slate-500 uppercase tracking-wide mb-3">Score Components (avg)</h3>
              {stats ? (
                <div className="space-y-2.5">
                  {[
                    { label: "AI Score", val: stats.avg_ai_score, icon: <Sparkles size={12} className="text-violet-500" /> },
                    { label: "Flex Score", val: stats.avg_flex_score, icon: <Target size={12} className="text-blue-500" /> },
                    { label: "Web Score", val: stats.avg_web_score, icon: <TrendingUp size={12} className="text-emerald-500" /> },
                    { label: "Combined", val: stats.avg_combined_score, icon: <BarChart2 size={12} className="text-slate-500" /> },
                  ].map(({ label, val, icon }) => (
                    <div key={label} className="flex items-center gap-2 text-xs">
                      {icon}
                      <span className="text-slate-600 w-20 shrink-0">{label}</span>
                      <div className="flex-1 h-3 bg-slate-100 rounded-full overflow-hidden">
                        {val != null && <div className="h-full bg-blue-400 rounded-full" style={{ width: `${val}%` }} />}
                      </div>
                      <span className="w-6 text-right tabular-nums text-slate-700 font-medium shrink-0">{val ?? "—"}</span>
                    </div>
                  ))}
                </div>
              ) : (
                <div className="h-20 flex items-center justify-center text-slate-400 text-sm">Loading…</div>
              )}
            </div>

            {/* Canton breakdown */}
            {stats && stats.canton_breakdown.length > 0 && (
              <div className="rounded-xl border border-slate-200 bg-white p-4">
                <h3 className="text-xs font-semibold text-slate-500 uppercase tracking-wide mb-3 flex items-center gap-1.5">
                  <MapPin size={12} /> Geographic distribution
                </h3>
                <div className="flex flex-wrap gap-1.5">
                  {stats.canton_breakdown.slice(0, 8).map(([canton, cnt]) => (
                    <span key={canton} className="flex items-center gap-1 text-xs bg-slate-100 rounded-full px-2 py-0.5">
                      <span className="font-medium">{canton}</span>
                      <span className="text-slate-400">{cnt.toLocaleString()}</span>
                    </span>
                  ))}
                </div>
              </div>
            )}
          </div>
        </div>

        {/* Top companies preview pills */}
        {page?.items && page.items.length > 0 && (
          <div className="mx-4 mt-4 shrink-0">
            <div className="flex items-center gap-2 flex-wrap">
              <span className="text-[10px] font-semibold text-slate-400 uppercase tracking-wider shrink-0 flex items-center gap-1">
                <Sparkles size={9} /> Top leads
              </span>
              {page.items.slice(0, 3).map(company => (
                <button
                  key={company.id}
                  onClick={() => setSelectedCompany(company)}
                  className="flex items-center gap-1.5 px-2.5 py-1 rounded-full border border-slate-200 bg-white hover:border-blue-300 hover:bg-blue-50 transition-colors group"
                >
                  <span className="text-xs text-slate-700 font-medium group-hover:text-blue-700 truncate max-w-[160px]">
                    {company.name}
                  </span>
                  {company.combined_score != null && (
                    <span className={cn(
                      "text-[10px] font-bold px-1.5 py-0.5 rounded-full shrink-0",
                      company.combined_score >= 70
                        ? "bg-emerald-100 text-emerald-700"
                        : company.combined_score >= 50
                        ? "bg-yellow-100 text-yellow-700"
                        : "bg-slate-100 text-slate-500"
                    )}>{company.combined_score}</span>
                  )}
                </button>
              ))}
            </div>
          </div>
        )}

        {/* Company table */}
        <div className="mx-4 mt-4 mb-4 rounded-xl border border-slate-200 bg-white overflow-hidden">
          <div className="flex items-center justify-between px-3 py-2 border-b border-slate-100 bg-slate-50 flex-wrap gap-2">
            <div className="flex items-center gap-2 flex-wrap">
              <h3 className="text-xs font-semibold text-slate-600">
                {matchingCount.toLocaleString()} companies
              </h3>
              {/* Business model chips */}
              <div className="flex items-center gap-1 flex-wrap">
                {BUSINESS_MODEL_CHIPS.map(chip => (
                  <button
                    key={chip.value}
                    onClick={() => handleBmClick(chip.value)}
                    className={cn(
                      "text-xs px-2 py-0.5 rounded-full border transition-colors",
                      selectedBm === chip.value
                        ? chip.color + " font-semibold"
                        : "bg-white text-slate-500 border-slate-200 hover:border-slate-300"
                    )}
                  >
                    {chip.label}
                  </button>
                ))}
                {selectedBm && (
                  <button onClick={() => handleBmClick(selectedBm)} className="text-xs text-blue-600 hover:underline">
                    ✕
                  </button>
                )}
              </div>
            </div>
            <div className="flex items-center gap-2">
              <a
                href={matchingCount <= exportLimit ? buildExportUrl(filters) : undefined}
                download={matchingCount <= exportLimit ? true : undefined}
                className={cn(
                  "flex items-center gap-1 text-xs px-2 py-1 rounded border transition-colors",
                  matchingCount > exportLimit
                    ? "text-slate-300 border-slate-200 bg-slate-100 cursor-not-allowed pointer-events-none"
                    : "text-slate-500 hover:text-slate-700 border-slate-200 hover:bg-white"
                )}
                title={matchingCount > exportLimit ? `Export limited to ${exportLimit.toLocaleString()} rows` : undefined}
              >
                <Download size={11} /> Export CSV
              </a>
            </div>
          </div>
          <div className="relative">
            <CompanyTable
              companies={page?.items ?? []}
              selectedId={selectedCompany?.id ?? null}
              onSelect={setSelectedCompany}
              filters={filters}
              onSort={(sort) => startTransition(() => setFilters(f => ({ ...f, sort, page: 1 })))}
              isLoading={isLoading}
              selectedIds={new Set()}
              onToggleSelect={() => {}}
              onSelectAll={() => {}}
              onUpdateReview={async (id, status) => { await bulkUpdateCompanies([id], "review_status", status); mutateCompanies(); }}
            />
            {/* Score breakdown button overlay — rendered as extra info next to each row's score */}
          </div>
          <Pagination
            page={filters.page ?? 1}
            pages={page?.pages ?? 1}
            total={page?.total ?? 0}
            pageSize={filters.page_size ?? 50}
            onChange={(p) => startTransition(() => setFilters(f => ({ ...f, page: p })))}
            onPageSizeChange={(s) => startTransition(() => setFilters(f => ({ ...f, page_size: s, page: 1 })))}
          />
        </div>

        {/* Score breakdown popover trigger — show a "why?" panel when a company row is clicked */}
        {selectedCompany && (
          <div className="fixed bottom-4 right-4 z-40 flex items-center gap-2">
            <button
              onClick={() => setBreakdownCompany(selectedCompany)}
              className="flex items-center gap-1.5 px-3 py-2 rounded-xl bg-slate-800 text-white text-xs font-medium shadow-lg hover:bg-slate-700 transition-colors"
            >
              <HelpCircle size={13} /> Why this score?
            </button>
            <button onClick={() => setSelectedCompany(null)} className="text-slate-400 hover:text-slate-600">
              <X size={16} />
            </button>
          </div>
        )}
      </div>

      {/* Company preview panel */}
      {selectedCompany && (
        <CompanyPreview
          company={selectedCompany}
          onClose={() => setSelectedCompany(null)}
          onUpdated={(updated) => { setSelectedCompany(updated); mutateCompanies(); }}
        />
      )}

      {/* Score breakdown modal */}
      {breakdownCompany && (
        <ScoreBreakdown company={breakdownCompany} onClose={() => setBreakdownCompany(null)} />
      )}
    </div>
  );
}

// ── BrowseView (original, kept for full-featured browsing) ────────────────────

interface BrowseViewProps {
  initialFilters: CompanyFilters;
  initialCantons: string[];
  initialStats: CompanyStats;
  initialTaxonomy: Record<string, [string, number][]>;
  onBack?: () => void;
}

function BrowseView({ initialFilters, initialCantons, initialStats, initialTaxonomy, onBack }: BrowseViewProps) {
  const router = useRouter();
  const [filters, setFiltersState] = useState<CompanyFilters>(initialFilters);
  const [, startTransition] = useTransition();

  const setFilters = useCallback((update: CompanyFilters | ((f: CompanyFilters) => CompanyFilters)) => {
    setFiltersState(prev => {
      const next = typeof update === "function" ? update(prev) : update;
      const params = new URLSearchParams({ browse: "1" });
      for (const [k, v] of Object.entries(next)) {
        const isDefault =
          (k === "sort" && v === "-combined_score") ||
          (k === "page" && v === 1) ||
          (k === "page_size" && v === 50) ||
          (k === "status" && v === "ACTIVE");
        if (v !== undefined && v !== null && v !== "" && !isDefault) params.set(k, String(v));
      }
      router.replace(`/app/explorer?${params.toString()}`, { scroll: false });
      return next;
    });
  }, [router]);

  const [selectedCompany, setSelectedCompany] = useState<Company | null>(null);
  const [breakdownCompany, setBreakdownCompany] = useState<Company | null>(null);
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set());
  const [batchLoading, setBatchLoading] = useState(false);
  const [tagInput, setTagInput] = useState("");
  const [tagLoading, setTagLoading] = useState(false);

  const { data: page, isLoading, mutate: mutateCompanies } = useSWR(
    ["companies", filters],
    () => fetchCompanies(filters),
    { keepPreviousData: true }
  );

  const { data: stats } = useSWR("stats", fetchStats, { fallbackData: initialStats });
  const { data: cantons = initialCantons } = useSWR("cantons", fetchCantons, { fallbackData: initialCantons });
  const { data: taxonomy = initialTaxonomy } = useSWR("taxonomy", fetchTaxonomy, { fallbackData: initialTaxonomy });
  const { data: savedViews = [], mutate: mutateSavedViews } = useSWR("saved-views", fetchSavedViews);
  const { data: me } = useSWR("me", fetchCurrentUser);
  const { data: org } = useSWR(me?.org?.id ? ["org-detail", me.org.id] : null, () => fetchOrg(me!.org!.id));

  function toggleSelect(id: number) {
    setSelectedIds(prev => { const n = new Set(prev); n.has(id) ? n.delete(id) : n.add(id); return n; });
  }
  function selectAll(ids: number[]) {
    const allSel = ids.every(id => selectedIds.has(id));
    setSelectedIds(prev => { const n = new Set(prev); allSel ? ids.forEach(id => n.delete(id)) : ids.forEach(id => n.add(id)); return n; });
  }
  async function batchUpdate(field: string, value: string | null) {
    if (!selectedIds.size) return;
    setBatchLoading(true);
    try { await bulkUpdateCompanies([...selectedIds], field, value); setSelectedIds(new Set()); mutateCompanies(); }
    finally { setBatchLoading(false); }
  }
  async function handleBulkTag(action: "add" | "remove") {
    const tag = tagInput.trim();
    if (!tag || !selectedIds.size) return;
    setTagLoading(true);
    try { await bulkTagCompanies([...selectedIds], tag, action); setTagInput(""); setSelectedIds(new Set()); mutateCompanies(); }
    finally { setTagLoading(false); }
  }

  const activeFilters: { label: string; key: string }[] = [];
  if (filters.ai_category) activeFilters.push({ label: `Industry: ${filters.ai_category}`, key: "ai_category" });
  if (filters.tfidf_cluster) activeFilters.push({ label: `Cluster: ${filters.tfidf_cluster.split(",").slice(0, 2).join(", ")}`, key: "tfidf_cluster" });
  if (filters.canton) activeFilters.push({ label: `Canton: ${filters.canton}`, key: "canton" });
  if (filters.min_combined_score) activeFilters.push({ label: `Score ≥ ${filters.min_combined_score}`, key: "min_combined_score" });
  if (filters.review_status) activeFilters.push({ label: `Review: ${filters.review_status.replace(/_/g, " ")}`, key: "review_status" });

  const exportLimit = org ? getExportLimit({ tier: org.tier, customFeatures: org.custom_features }) : 100;
  const matchingCount = page?.total ?? 0;
  const exportBlocked = matchingCount > exportLimit;

  return (
    <div className="flex flex-col h-[calc(100vh-3rem)] overflow-hidden">
      {/* Stats bar */}
      <div className="flex items-center gap-4 px-4 py-2 bg-slate-50 border-b border-slate-100 text-xs text-slate-500 shrink-0 flex-wrap">
        {onBack && (
          <button onClick={onBack} className="flex items-center gap-1 text-slate-500 hover:text-slate-700 shrink-0">
            <ArrowLeft size={12} /> Categories
          </button>
        )}
        <span><span className="font-semibold text-slate-700">{(stats?.total ?? 0).toLocaleString()}</span> total</span>
        <span>·</span>
        <span><span className="font-semibold text-amber-600">{(stats?.review?.interesting ?? 0).toLocaleString()}</span> interesting</span>
        <span>·</span>
        <span><span className="font-semibold text-emerald-600">{(stats?.review?.confirmed_proposal ?? 0).toLocaleString()}</span> confirmed</span>
        {page?.total !== undefined && (
          <><span>·</span><span><span className="font-semibold text-blue-600">{page.total.toLocaleString()}</span> matching</span></>
        )}
        {activeFilters.length > 0 && (
          <div className="flex items-center gap-1.5 ml-1">
            {activeFilters.map(({ label, key }) => (
              <span key={key} className="flex items-center gap-1 bg-blue-50 text-blue-700 border border-blue-200 rounded-full px-2 py-0.5">
                {label}
                <button onClick={() => setFilters(f => { const n = { ...f }; delete (n as Record<string, unknown>)[key]; return n; })} className="hover:text-blue-900"><X size={10} /></button>
              </span>
            ))}
          </div>
        )}
        <div className="ml-auto"><ScoreDistributionBar filters={filters} /></div>
      </div>

      {/* Filter bar */}
      <FilterBar
        filters={filters}
        cantons={cantons}
        taxonomy={taxonomy as Record<string, [string, number][]>}
        onChange={(f) => startTransition(() => setFilters(f))}
        onClear={() => setFilters(BROWSE_DEFAULTS)}
        resultCount={page?.total ?? 0}
        savedViews={savedViews}
        onSaveView={async (name) => { await saveView(name, filters); mutateSavedViews(); }}
        onLoadView={(f) => setFilters({ ...BROWSE_DEFAULTS, ...f })}
        onDeleteView={async (id) => { await deleteView(id); mutateSavedViews(); }}
        onToggleAlert={async (id, enabled) => { await toggleViewAlert(id, enabled); mutateSavedViews(); }}
      />

      {/* Ad card */}
      <div className="px-4 py-2 bg-slate-50 border-b border-slate-100 shrink-0">
        <BaloghAdCard className="max-w-xs" />
      </div>

      {/* Table + preview */}
      <div className="flex flex-1 overflow-hidden">
        <div className="flex-1 flex flex-col overflow-hidden bg-white">
          <div className="flex items-center justify-between px-3 py-1 border-b border-slate-100 bg-slate-50">
            {selectedIds.size > 0 ? (
              <div className="flex items-center gap-2 flex-wrap">
                <span className="text-xs font-medium text-blue-700">{selectedIds.size} selected</span>
                <span className="text-slate-300">|</span>
                <span className="text-xs text-slate-500">Review:</span>
                {["interesting", "potential_proposal", "confirmed_proposal"].map(v => (
                  <button key={v} disabled={batchLoading} onClick={() => batchUpdate("review_status", v)}
                    className="px-2 py-0.5 text-xs rounded border border-slate-200 bg-white hover:bg-blue-50 hover:border-blue-300 hover:text-blue-700 disabled:opacity-50 transition-colors">
                    {v.replace(/_/g, " ")}
                  </button>
                ))}
                <button disabled={batchLoading} onClick={() => batchUpdate("review_status", null)}
                  className="px-2 py-0.5 text-xs rounded border border-slate-200 bg-white hover:bg-red-50 hover:border-red-300 hover:text-red-700 disabled:opacity-50 transition-colors">Clear</button>
                <span className="text-slate-300">|</span>
                <span className="text-xs text-slate-500">Tag:</span>
                <input type="text" value={tagInput} onChange={e => setTagInput(e.target.value)} placeholder="tag name…"
                  className="px-2 py-0.5 text-xs border border-slate-200 rounded focus:outline-none focus:ring-1 focus:ring-blue-300 w-24"
                  onKeyDown={e => { if (e.key === "Enter") handleBulkTag("add"); }} />
                <button disabled={tagLoading || !tagInput.trim()} onClick={() => handleBulkTag("add")}
                  className="px-2 py-0.5 text-xs rounded border border-slate-200 bg-white hover:bg-emerald-50 hover:border-emerald-300 hover:text-emerald-700 disabled:opacity-50 transition-colors">+Tag</button>
                <button disabled={tagLoading || !tagInput.trim()} onClick={() => handleBulkTag("remove")}
                  className="px-2 py-0.5 text-xs rounded border border-slate-200 bg-white hover:bg-red-50 hover:border-red-300 hover:text-red-700 disabled:opacity-50 transition-colors">−Tag</button>
                <button onClick={() => setSelectedIds(new Set())} className="ml-1 text-xs text-slate-400 hover:text-slate-600"><X size={12} /></button>
              </div>
            ) : <div />}
            <div className="flex items-center gap-2">
              {selectedCompany && (
                <button
                  onClick={() => setBreakdownCompany(selectedCompany)}
                  className="flex items-center gap-1 text-xs px-2 py-1 rounded border border-slate-200 text-slate-500 hover:bg-white hover:text-slate-700 transition-colors"
                >
                  <HelpCircle size={11} /> Why this score?
                </button>
              )}
              <a
                href={exportBlocked ? undefined : buildExportUrl(filters)}
                download={exportBlocked ? undefined : true}
                className={cn(
                  "flex items-center gap-1.5 text-xs px-2.5 py-1 rounded border transition-colors",
                  exportBlocked ? "text-slate-300 border-slate-200 bg-slate-100 cursor-not-allowed pointer-events-none" : "text-slate-500 hover:text-slate-700 border-slate-200 hover:bg-white"
                )}
                title={exportBlocked ? `Export limited to ${exportLimit.toLocaleString()} rows` : undefined}
              >
                <Download size={12} /> Export CSV ({page?.total ?? 0})
              </a>
            </div>
          </div>
          {exportBlocked && (
            <div className="px-3 py-1 text-xs text-amber-700 bg-amber-50 border-b border-amber-100">
              Export limited to {exportLimit.toLocaleString()} rows for your current plan.
            </div>
          )}
          <CompanyTable
            companies={page?.items ?? []}
            selectedId={selectedCompany?.id ?? null}
            onSelect={setSelectedCompany}
            filters={filters}
            onSort={(sort) => setFilters(f => ({ ...f, sort, page: 1 }))}
            isLoading={isLoading}
            selectedIds={selectedIds}
            onToggleSelect={toggleSelect}
            onSelectAll={selectAll}
            onUpdateReview={async (id, status) => { await bulkUpdateCompanies([id], "review_status", status); mutateCompanies(); }}
          />
          <Pagination
            page={page?.page ?? 1}
            pages={page?.pages ?? 1}
            total={page?.total ?? 0}
            pageSize={filters.page_size ?? 50}
            onChange={(p) => setFilters(f => ({ ...f, page: p }))}
            onPageSizeChange={(s) => setFilters(f => ({ ...f, page_size: s, page: 1 }))}
          />
        </div>
        {selectedCompany && (
          <CompanyPreview
            company={selectedCompany}
            onClose={() => setSelectedCompany(null)}
            onUpdated={(updated) => { setSelectedCompany(updated); mutateCompanies(); }}
          />
        )}
      </div>

      {breakdownCompany && (
        <ScoreBreakdown company={breakdownCompany} onClose={() => setBreakdownCompany(null)} />
      )}
    </div>
  );
}

// ── ExplorerInner (reads URL params) ─────────────────────────────────────────

interface ExplorerInnerProps {
  initialCantons: string[];
  initialStats: CompanyStats;
  initialTaxonomy: Record<string, [string, number][]>;
}

function ExplorerInner({ initialCantons, initialStats, initialTaxonomy }: ExplorerInnerProps) {
  const router = useRouter();
  const searchParams = useSearchParams();
  const [wizardOpen, setWizardOpen] = useState(false);

  const catType = searchParams.get("cat_type") as CategoryType | null;
  const catValue = searchParams.get("cat_value");
  const isBrowse = searchParams.get("browse") === "1";

  const { data: me } = useSWR("me", fetchCurrentUser);
  const orgId = me?.org_id ?? null;

  const { data: taxonomy = initialTaxonomy } = useSWR("taxonomy", fetchTaxonomy, { fallbackData: initialTaxonomy });
  const { data: effectiveSettings } = useSWR(
    orgId ? ["org-effective-settings", orgId] : null,
    () => fetchOrgEffectiveSettings(orgId!),
  );

  const hasApiKey = effectiveSettings?.anthropic_api_key_set ?? false;
  const hasTargetDescription = !!(effectiveSettings?.claude_target_description?.trim());

  function navigateToCategory(type: CategoryType, value: string) {
    const params = new URLSearchParams({ cat_type: type, cat_value: value });
    router.push(`/app/explorer?${params.toString()}`);
  }

  function navigateToBrowse(preFilters?: Partial<CompanyFilters>) {
    const params = new URLSearchParams({ browse: "1" });
    if (preFilters) {
      for (const [k, v] of Object.entries(preFilters)) {
        if (v !== undefined && v !== null && v !== "") params.set(k, String(v));
      }
    }
    router.push(`/app/explorer?${params.toString()}`);
  }

  function navigateBack() {
    router.push("/app/explorer");
  }

  // Derive initial browse filters from URL params
  const browseFilters: CompanyFilters = { ...BROWSE_DEFAULTS };
  for (const [k, v] of Array.from(searchParams.entries())) {
    if (k !== "browse") (browseFilters as Record<string, unknown>)[k] = v;
  }

  return (
    <div className="flex flex-col h-[calc(100vh-3rem)] overflow-hidden">
      {/* Setup gate */}
      <SetupGateBanner hasApiKey={hasApiKey} hasTargetDescription={hasTargetDescription} />

      {/* Content */}
      <div className="flex-1 overflow-hidden">
        {isBrowse ? (
          <BrowseView
            initialFilters={browseFilters}
            initialCantons={initialCantons}
            initialStats={initialStats}
            initialTaxonomy={taxonomy}
            onBack={navigateBack}
          />
        ) : catType && catValue ? (
          <CategoryDetail
            catType={catType}
            catValue={catValue}
            initialTaxonomy={taxonomy}
            initialCantons={initialCantons}
            initialStats={initialStats}
            orgId={orgId}
            onBack={navigateBack}
            onOpenWizard={() => setWizardOpen(true)}
          />
        ) : (
          <CategoryGrid
            taxonomy={taxonomy}
            onSelectCategory={navigateToCategory}
            onBrowseAll={() => navigateToBrowse()}
            hasApiKey={hasApiKey}
            hasTargetDescription={hasTargetDescription}
            orgId={orgId}
            settings={effectiveSettings}
            onOpenWizard={() => setWizardOpen(true)}
          />
        )}
      </div>

      {/* Scoring Wizard modal */}
      {wizardOpen && (
        <ScoringWizard
          taxonomy={taxonomy}
          settings={effectiveSettings}
          orgId={orgId}
          onClose={() => setWizardOpen(false)}
          onSaved={() => {
            setWizardOpen(false);
          }}
        />
      )}
    </div>
  );
}

// ── ExplorerClient (exported, wraps in Suspense) ──────────────────────────────

export interface ExplorerClientProps {
  initialCantons: string[];
  initialStats: CompanyStats;
  initialTaxonomy: Record<string, [string, number][]>;
}

export function ExplorerClient({ initialCantons, initialStats, initialTaxonomy }: ExplorerClientProps) {
  return (
    <Suspense fallback={
      <div className="flex items-center justify-center h-[calc(100vh-3rem)] text-slate-400 text-sm">
        <Compass size={20} className="mr-2 animate-pulse" /> Loading…
      </div>
    }>
      <ExplorerInner
        initialCantons={initialCantons}
        initialStats={initialStats}
        initialTaxonomy={initialTaxonomy}
      />
    </Suspense>
  );
}
