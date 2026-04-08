"use client";
import { useState, useCallback, useTransition, useEffect, useRef } from "react";
import { useRouter } from "next/navigation";
import useSWR from "swr";
import { Compass, Search, ChevronRight, X, Download, Settings, AlertTriangle, Star, BarChart2 } from "lucide-react";
import { CompanyTable } from "@/components/dashboard/company-table";
import { CompanyPreview } from "@/components/dashboard/company-preview";
import { FilterBar } from "@/components/dashboard/filter-bar";
import { BaloghAdCard } from "@/components/balogh-ad-card";
import { Pagination } from "@/components/dashboard/pagination";
import {
  fetchCompanies, fetchStats, fetchCantons, fetchTaxonomy,
  fetchSavedViews, saveView, deleteView, bulkUpdateCompanies, bulkTagCompanies,
  fetchCurrentUser, fetchOrgEffectiveSettings,
  fetchOrg, fetchNogaHierarchy,
} from "@/lib/api";
import type { NogaNode } from "@/lib/api";
import type { Company, CompanyFilters, CompanyStats } from "@/lib/types";
import { cn } from "@/lib/utils";
import { getExportLimit } from "@/lib/entitlements";

// ── Swiss cantons for the guided picker ──────────────────────────────────────

const CANTON_LABELS: Record<string, string> = {
  AG: "Aargau", AI: "Appenzell I.Rh.", AR: "Appenzell A.Rh.", BE: "Bern",
  BL: "Basel-Land", BS: "Basel-Stadt", FR: "Fribourg", GE: "Genf",
  GL: "Glarus", GR: "Graubünden", JU: "Jura", LU: "Luzern",
  NE: "Neuenburg", NW: "Nidwalden", OW: "Obwalden", SG: "St. Gallen",
  SH: "Schaffhausen", SO: "Solothurn", SZ: "Schwyz", TG: "Thurgau",
  TI: "Tessin", UR: "Uri", VD: "Waadt", VS: "Wallis",
  ZG: "Zug", ZH: "Zürich",
};

// Score preset options for guided mode
const SCORE_PRESETS = [
  { label: "Any", value: 0, description: "Show all companies" },
  { label: "Good", value: 40, description: "Score ≥ 40" },
  { label: "Strong", value: 60, description: "Score ≥ 60" },
  { label: "Top", value: 80, description: "Score ≥ 80" },
];

// Review status quick filter options
const REVIEW_QUICK_FILTERS = [
  { label: "Not yet reviewed", value: "_none", description: "No review status set" },
  { label: "Interesting", value: "interesting", description: "Marked as interesting" },
  { label: "Potential proposal", value: "potential_proposal", description: "" },
  { label: "Confirmed", value: "confirmed_proposal", description: "" },
  { label: "Rejected", value: "rejected", description: "Marked as rejected" },
];

type Mode = "guided" | "browse";

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

const BROWSE_DEFAULTS: CompanyFilters = { sort: "-combined_score", page: 1, page_size: 50, status: "ACTIVE" };

// ── Setup gate banner ─────────────────────────────────────────────────────────

interface SetupGateProps {
  orgId: number | null;
  hasApiKey: boolean;
  hasTargetDescription: boolean;
}

function SetupGateBanner({ orgId, hasApiKey, hasTargetDescription }: SetupGateProps) {
  if (hasApiKey && hasTargetDescription) return null;

  const missing: string[] = [];
  if (!hasApiKey) missing.push("Anthropic API key");
  if (!hasTargetDescription) missing.push("target company description");

  return (
    <div className="mx-4 mt-3 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 flex items-start gap-3">
      <AlertTriangle size={16} className="text-amber-500 shrink-0 mt-0.5" />
      <div className="flex-1 min-w-0">
        <p className="text-sm font-medium text-amber-800">
          AI scoring is not configured
        </p>
        <p className="text-xs text-amber-700 mt-0.5">
          Set your {missing.join(" and ")} to enable AI-powered lead scoring.
        </p>
      </div>
      <a
        href="/app/settings?tab=llm"
        className="shrink-0 flex items-center gap-1 text-xs font-medium text-amber-700 hover:text-amber-900 border border-amber-300 bg-white rounded-lg px-2.5 py-1.5 hover:bg-amber-50 transition-colors"
      >
        <Settings size={12} />
        Set up
      </a>
    </div>
  );
}

// ── Score distribution mini-bar ───────────────────────────────────────────────

interface ScoreDistributionProps {
  filters: CompanyFilters;
}

function ScoreDistributionBar({ filters }: ScoreDistributionProps) {
  // Fetch counts for each score band using page_size=1 tricks
  const makeFilters = (min: number, max?: number): CompanyFilters => ({
    ...filters, page: 1, page_size: 1,
    min_combined_score: min,
    ...(max !== undefined ? { max_combined_score: max } : {}),
  });

  const { data: top } = useSWR(
    ["score-dist-top", filters],
    () => fetchCompanies(makeFilters(70)),
    { keepPreviousData: true }
  );
  const { data: mid } = useSWR(
    ["score-dist-mid", filters],
    () => fetchCompanies(makeFilters(40, 69)),
    { keepPreviousData: true }
  );
  const { data: low } = useSWR(
    ["score-dist-low", filters],
    () => fetchCompanies(makeFilters(1, 39)),
    { keepPreviousData: true }
  );
  const { data: zero } = useSWR(
    ["score-dist-zero", filters],
    () => fetchCompanies(makeFilters(0, 0)),
    { keepPreviousData: true }
  );

  const t = top?.total ?? 0;
  const m = mid?.total ?? 0;
  const l = low?.total ?? 0;
  const z = zero?.total ?? 0;
  const total = t + m + l + z;
  if (!total) return null;

  const pct = (n: number) => Math.round((n / total) * 100);

  return (
    <div className="flex items-center gap-2 text-[10px] text-slate-500">
      <span className="hidden sm:inline">Score dist:</span>
      <div className="flex h-2 w-20 rounded-full overflow-hidden gap-px">
        {t > 0 && <div className="bg-green-500" style={{ width: `${pct(t)}%` }} title={`Top (≥70): ${t}`} />}
        {m > 0 && <div className="bg-yellow-400" style={{ width: `${pct(m)}%` }} title={`Mid (40–69): ${m}`} />}
        {l > 0 && <div className="bg-orange-300" style={{ width: `${pct(l)}%` }} title={`Low (1–39): ${l}`} />}
        {z > 0 && <div className="bg-slate-200" style={{ width: `${pct(z)}%` }} title={`Unscored: ${z}`} />}
      </div>
      <span className="text-green-600">{pct(t)}%</span>
      <span className="text-yellow-600">{pct(m)}%</span>
      <span className="text-slate-400">{pct(z)}% unscored</span>
    </div>
  );
}

// ── Pipeline funnel ───────────────────────────────────────────────────────────

function PipelineFunnel({ stats }: { stats: CompanyStats }) {
  const total = stats.total || 1;
  const withWebsite = stats.with_website ?? 0;
  const searched = stats.searched ?? 0;
  const interesting = stats.review?.interesting ?? 0;
  const confirmed = stats.review?.confirmed_proposal ?? 0;

  const stages = [
    { label: "Total", value: stats.total, color: "bg-slate-300" },
    { label: "Website", value: withWebsite, color: "bg-blue-300" },
    { label: "Searched", value: searched, color: "bg-indigo-400" },
    { label: "Interesting", value: interesting, color: "bg-amber-400" },
    { label: "Confirmed", value: confirmed, color: "bg-emerald-500" },
  ];

  return (
    <div className="space-y-2">
      {stages.map((s) => (
        <div key={s.label} className="flex items-center gap-2 text-xs">
          <span className="w-20 text-right text-slate-500 shrink-0">{s.label}</span>
          <div className="flex-1 h-4 bg-slate-100 rounded-full overflow-hidden">
            <div
              className={`h-full ${s.color} rounded-full transition-all`}
              style={{ width: `${Math.min(100, (s.value / total) * 100)}%` }}
            />
          </div>
          <span className="w-16 tabular-nums text-slate-600 shrink-0">{s.value.toLocaleString()}</span>
        </div>
      ))}
    </div>
  );
}

// ── Guided mode ───────────────────────────────────────────────────────────────

interface GuidedPickerProps {
  cantons: string[];
  taxonomy: Record<string, [string, number][]>;
  stats: CompanyStats;
  onBrowse: (filters: CompanyFilters) => void;
  orgId: number | null;
  hasApiKey: boolean;
  hasTargetDescription: boolean;
}

type IndustryMode = "category" | "cluster";

const LS_KEY = "firmiq_guided_picker";

function GuidedPicker({ cantons, taxonomy, stats, onBrowse, orgId, hasApiKey, hasTargetDescription }: GuidedPickerProps) {
  const [industryMode, setIndustryMode] = useState<IndustryMode>("category");
  const [selectedCategory, setSelectedCategory] = useState<string | null>(null);
  const [selectedCluster, setSelectedCluster] = useState<string | null>(null);
  const [selectedCanton, setSelectedCanton] = useState<string | null>(null);
  const [selectedNoga, setSelectedNoga] = useState<string | null>(null);
  const [selectedLegalForm, setSelectedLegalForm] = useState<string | null>(null);
  const [scoreMin, setScoreMin] = useState(0);
  const [selectedReview, setSelectedReview] = useState<string | null>(null);
  const [bulkMarking, setBulkMarking] = useState(false);
  const [bulkBanner, setBulkBanner] = useState<string | null>(null);
  const [industrySearch, setIndustrySearch] = useState("");
  const [showFunnel, setShowFunnel] = useState(false);
  const didRestoreRef = useRef(false);

  const { data: nogaTree = [] } = useSWR("noga-hierarchy", fetchNogaHierarchy);

  // Restore last state from localStorage
  useEffect(() => {
    if (didRestoreRef.current) return;
    didRestoreRef.current = true;
    try {
      const saved = localStorage.getItem(LS_KEY);
      if (saved) {
        const s = JSON.parse(saved);
        if (s.industryMode) setIndustryMode(s.industryMode);
        if (s.selectedCategory) setSelectedCategory(s.selectedCategory);
        if (s.selectedCluster) setSelectedCluster(s.selectedCluster);
        if (s.selectedCanton) setSelectedCanton(s.selectedCanton);
        if (s.selectedNoga) setSelectedNoga(s.selectedNoga);
        if (s.selectedLegalForm) setSelectedLegalForm(s.selectedLegalForm);
        if (s.scoreMin !== undefined) setScoreMin(s.scoreMin);
        if (s.selectedReview) setSelectedReview(s.selectedReview);
      }
    } catch { /* ignore */ }
  }, []);

  // Persist state to localStorage on change
  useEffect(() => {
    if (!didRestoreRef.current) return;
    try {
      localStorage.setItem(LS_KEY, JSON.stringify({
        industryMode, selectedCategory, selectedCluster, selectedCanton,
        selectedNoga, selectedLegalForm, scoreMin, selectedReview,
      }));
    } catch { /* ignore */ }
  }, [industryMode, selectedCategory, selectedCluster, selectedCanton, selectedNoga, selectedLegalForm, scoreMin, selectedReview]);

  const aiCategories: [string, number][] = taxonomy["ai_category"] ?? [];
  const clusters: [string, number][] = taxonomy["clusters"] ?? [];
  const legalForms: [string, number][] = taxonomy["legal_forms"] ?? [];
  const cantonCounts: Record<string, number> = Object.fromEntries((taxonomy["cantons"] ?? []) as [string, number][]);

  // Live count based on current guided filters
  const guidedFilters: CompanyFilters = {
    sort: "-combined_score",
    page: 1,
    page_size: 1,
    status: "ACTIVE",
    ...(selectedCategory ? { ai_category: selectedCategory } : {}),
    ...(selectedCluster ? { tfidf_cluster: selectedCluster } : {}),
    ...(selectedCanton ? { canton: selectedCanton } : {}),
    ...(selectedNoga ? { noga_code: selectedNoga } : {}),
    ...(selectedLegalForm ? { legal_form: selectedLegalForm } : {}),
    ...(scoreMin > 0 ? { min_combined_score: scoreMin } : {}),
    ...(selectedReview === "_none" ? { exclude_review_status: "interesting,potential_proposal,confirmed_proposal,rejected" } : {}),
    ...(selectedReview && selectedReview !== "_none" ? { review_status: selectedReview } : {}),
  };
  const { data: preview } = useSWR(
    ["companies-preview", guidedFilters],
    () => fetchCompanies(guidedFilters),
    { keepPreviousData: true }
  );

  function handleBrowse() {
    const filters: CompanyFilters = {
      ...BROWSE_DEFAULTS,
      ...(selectedCategory ? { ai_category: selectedCategory } : {}),
      ...(selectedCluster ? { tfidf_cluster: selectedCluster } : {}),
      ...(selectedCanton ? { canton: selectedCanton } : {}),
      ...(selectedNoga ? { noga_code: selectedNoga } : {}),
      ...(selectedLegalForm ? { legal_form: selectedLegalForm } : {}),
      ...(scoreMin > 0 ? { min_combined_score: scoreMin } : {}),
      ...(selectedReview === "_none" ? { exclude_review_status: "interesting,potential_proposal,confirmed_proposal,rejected" } : {}),
      ...(selectedReview && selectedReview !== "_none" ? { review_status: selectedReview } : {}),
    };
    onBrowse(filters);
  }

  async function handleBulkMark() {
    const matchCount = preview?.total ?? 0;
    if (!matchCount) return;
    const limit = Math.min(matchCount, 200);
    setBulkMarking(true);
    setBulkBanner(null);
    try {
      // Fetch all matching IDs
      const result = await fetchCompanies({ ...guidedFilters, page: 1, page_size: limit });
      const ids = result.items.map((c) => c.id);
      if (ids.length) {
        await bulkUpdateCompanies(ids, "review_status", "interesting");
        setBulkBanner(`Marked ${ids.length} companies as interesting.`);
        setTimeout(() => setBulkBanner(null), 4000);
      }
    } catch {
      setBulkBanner("Failed to mark companies.");
    } finally {
      setBulkMarking(false);
    }
  }

  const matchCount = preview?.total ?? null;

  return (
    <div className="flex flex-col items-center justify-start py-10 px-4 min-h-0 overflow-y-auto">
      <div className="w-full max-w-2xl space-y-8">

        {/* Hero */}
        <div className="text-center space-y-2">
          <div className="flex items-center justify-center gap-2 text-blue-600 mb-3">
            <Compass size={28} />
          </div>
          <h1 className="text-2xl font-semibold text-slate-800">Explore Swiss Companies</h1>
          <p className="text-slate-500 text-sm">
            Narrow down by industry, canton, and quality — then browse matching companies.
          </p>
          <button
            onClick={() => setShowFunnel(v => !v)}
            className="inline-flex items-center gap-1.5 text-xs text-slate-400 hover:text-slate-600 mt-1"
          >
            <BarChart2 size={12} />
            {showFunnel ? "Hide pipeline stats" : "Show pipeline stats"}
          </button>
          {showFunnel && (
            <div className="mt-3 bg-slate-50 rounded-xl border border-slate-200 p-4 text-left">
              <p className="text-xs font-medium text-slate-600 mb-3">Pipeline Overview</p>
              <PipelineFunnel stats={stats} />
            </div>
          )}
        </div>

        {/* Step 1: Industry / Cluster */}
        <div className="space-y-3">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="flex items-center justify-center w-5 h-5 rounded-full bg-blue-600 text-white text-xs font-bold shrink-0">1</span>
            <h2 className="text-sm font-semibold text-slate-700 uppercase tracking-wide">Industry</h2>
            {/* Mode tabs */}
            <div className="flex items-center gap-0.5 bg-slate-100 rounded-md p-0.5 ml-2">
              <button
                onClick={() => { setIndustryMode("category"); setSelectedCluster(null); setIndustrySearch(""); }}
                className={cn(
                  "px-2.5 py-0.5 rounded text-xs font-medium transition-colors",
                  industryMode === "category" ? "bg-white text-slate-800 shadow-sm" : "text-slate-500 hover:text-slate-700"
                )}
              >
                AI Category
              </button>
              <button
                onClick={() => { setIndustryMode("cluster"); setSelectedCategory(null); setIndustrySearch(""); }}
                className={cn(
                  "px-2.5 py-0.5 rounded text-xs font-medium transition-colors",
                  industryMode === "cluster" ? "bg-white text-slate-800 shadow-sm" : "text-slate-500 hover:text-slate-700"
                )}
              >
                TF-IDF Cluster
              </button>
            </div>
            {/* Inline search */}
            <div className="relative ml-auto">
              <Search size={12} className="absolute left-2 top-1.5 text-slate-400 pointer-events-none" />
              <input
                type="text"
                value={industrySearch}
                onChange={(e) => setIndustrySearch(e.target.value)}
                placeholder="Search…"
                className="pl-6 pr-2 py-1 text-xs border border-slate-200 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-300 bg-white w-36"
              />
            </div>
            {(selectedCategory || selectedCluster) && (
              <button
                onClick={() => { setSelectedCategory(null); setSelectedCluster(null); }}
                className="text-xs text-slate-400 hover:text-slate-600 flex items-center gap-0.5"
              >
                <X size={11} /> Clear
              </button>
            )}
          </div>

          {industryMode === "category" ? (
            aiCategories.length > 0 ? (
              <div className="flex flex-wrap gap-2 max-h-64 overflow-y-auto">
                {aiCategories
                  .filter(([cat]) => !industrySearch || cat.toLowerCase().includes(industrySearch.toLowerCase()))
                  .map(([cat, count]) => (
                    <button
                      key={cat}
                      onClick={() => setSelectedCategory(selectedCategory === cat ? null : cat)}
                      className={cn(
                        "px-3 py-1.5 rounded-full text-sm border transition-colors",
                        selectedCategory === cat
                          ? "bg-blue-600 text-white border-blue-600 font-medium"
                          : "bg-white text-slate-600 border-slate-200 hover:border-blue-300 hover:text-blue-600"
                      )}
                    >
                      {cat}
                      <span className={cn("ml-1.5 text-xs", selectedCategory === cat ? "text-blue-200" : "text-slate-400")}>
                        {count.toLocaleString()}
                      </span>
                    </button>
                  ))}
              </div>
            ) : (
              <div className="rounded-lg border border-slate-200 bg-slate-50 px-4 py-5 text-center space-y-2">
                <p className="text-sm text-slate-500">No AI classifications yet.</p>
                <p className="text-xs text-slate-400">
                  {hasApiKey
                    ? "Run the AI classification job from the Pipeline page."
                    : "Configure your Anthropic API key in workspace settings first."}
                </p>
                <a
                  href={hasApiKey ? "/app/pipeline" : "/app/settings?tab=llm"}
                  className="inline-block text-xs text-blue-600 hover:underline"
                >
                  {hasApiKey ? "Go to Pipeline →" : "Go to Settings →"}
                </a>
              </div>
            )
          ) : (
            clusters.length > 0 ? (
              <div className="flex flex-wrap gap-2 max-h-64 overflow-y-auto">
                {clusters
                  .filter(([label]) => !industrySearch || label.toLowerCase().includes(industrySearch.toLowerCase()))
                  .map(([label, count]) => {
                    const terms = label.split(",").map(t => t.trim()).filter(Boolean).slice(0, 3).join(", ");
                    return (
                      <button
                        key={label}
                        onClick={() => setSelectedCluster(selectedCluster === label ? null : label)}
                        className={cn(
                          "px-3 py-1.5 rounded-full text-sm border transition-colors",
                          selectedCluster === label
                            ? "bg-purple-600 text-white border-purple-600 font-medium"
                            : "bg-white text-slate-600 border-slate-200 hover:border-purple-300 hover:text-purple-600"
                        )}
                      >
                        {terms}
                        <span className={cn("ml-1.5 text-xs", selectedCluster === label ? "text-purple-200" : "text-slate-400")}>
                          {count.toLocaleString()}
                        </span>
                      </button>
                    );
                  })}
              </div>
            ) : (
              <div className="rounded-lg border border-slate-200 bg-slate-50 px-4 py-5 text-center space-y-2">
                <p className="text-sm text-slate-500">No TF-IDF clusters yet.</p>
                <p className="text-xs text-slate-400">Run the clustering job from the Pipeline page.</p>
                <a href="/app/pipeline" className="inline-block text-xs text-blue-600 hover:underline">Go to Pipeline →</a>
              </div>
            )
          )}
        </div>

        {/* Step 2: Canton */}
        <div className="space-y-3">
          <div className="flex items-center gap-2">
            <span className="flex items-center justify-center w-5 h-5 rounded-full bg-blue-600 text-white text-xs font-bold shrink-0">2</span>
            <h2 className="text-sm font-semibold text-slate-700 uppercase tracking-wide">Canton</h2>
            {selectedCanton && (
              <button onClick={() => setSelectedCanton(null)} className="ml-auto text-xs text-slate-400 hover:text-slate-600 flex items-center gap-0.5">
                <X size={11} /> Clear
              </button>
            )}
          </div>
          {/* Canton heatmap: buttons sized by relative company count */}
          {(() => {
            const maxCount = Math.max(1, ...cantons.map(c => cantonCounts[c] ?? 0));
            return (
              <div className="flex flex-wrap gap-1.5">
                {cantons.map((c) => {
                  const cnt = cantonCounts[c] ?? 0;
                  const opacity = 0.2 + 0.8 * (cnt / maxCount);
                  return (
                    <button
                      key={c}
                      onClick={() => setSelectedCanton(selectedCanton === c ? null : c)}
                      title={`${CANTON_LABELS[c] ?? c}: ${cnt.toLocaleString()} companies`}
                      className={cn(
                        "px-2.5 py-1 rounded text-xs font-medium border transition-colors",
                        selectedCanton === c
                          ? "bg-blue-600 text-white border-blue-600"
                          : "bg-white text-slate-600 border-slate-200 hover:border-blue-300 hover:text-blue-600"
                      )}
                      style={selectedCanton !== c ? { backgroundColor: `rgba(59,130,246,${opacity * 0.15})` } : undefined}
                    >
                      {c}
                      {cnt > 0 && <span className={cn("ml-1 text-[10px]", selectedCanton === c ? "text-blue-200" : "text-slate-400")}>{cnt >= 1000 ? `${Math.round(cnt / 1000)}k` : cnt}</span>}
                    </button>
                  );
                })}
              </div>
            );
          })()}
        </div>

        {/* Step 3: NOGA Industry Section */}
        {nogaTree.length > 0 && (
          <div className="space-y-3">
            <div className="flex items-center gap-2">
              <span className="flex items-center justify-center w-5 h-5 rounded-full bg-blue-600 text-white text-xs font-bold shrink-0">3</span>
              <h2 className="text-sm font-semibold text-slate-700 uppercase tracking-wide">NOGA Section</h2>
              <span className="text-xs text-slate-400">Swiss industry classification</span>
              {selectedNoga && (
                <button onClick={() => setSelectedNoga(null)} className="ml-auto text-xs text-slate-400 hover:text-slate-600 flex items-center gap-0.5">
                  <X size={11} /> Clear
                </button>
              )}
            </div>
            <div className="flex flex-wrap gap-1.5">
              {nogaTree.map((node: NogaNode) => (
                <button
                  key={node.code}
                  onClick={() => setSelectedNoga(selectedNoga === node.code ? null : node.code)}
                  title={node.label}
                  className={cn(
                    "px-2.5 py-1 rounded text-xs font-medium border transition-colors text-left",
                    selectedNoga === node.code
                      ? "bg-blue-600 text-white border-blue-600"
                      : "bg-white text-slate-600 border-slate-200 hover:border-blue-300 hover:text-blue-600"
                  )}
                >
                  <span className="font-mono mr-1">{node.code}</span>
                  <span className="hidden sm:inline">{node.label.length > 24 ? node.label.slice(0, 22) + "…" : node.label}</span>
                  <span className={cn("ml-1 text-[10px]", selectedNoga === node.code ? "text-blue-200" : "text-slate-400")}>{node.count >= 1000 ? `${Math.round(node.count / 1000)}k` : node.count}</span>
                </button>
              ))}
            </div>
          </div>
        )}

        {/* Step 4: Legal form */}
        {legalForms.length > 0 && (
          <div className="space-y-3">
            <div className="flex items-center gap-2">
              <span className="flex items-center justify-center w-5 h-5 rounded-full bg-blue-600 text-white text-xs font-bold shrink-0">4</span>
              <h2 className="text-sm font-semibold text-slate-700 uppercase tracking-wide">Legal Form</h2>
              {selectedLegalForm && (
                <button onClick={() => setSelectedLegalForm(null)} className="ml-auto text-xs text-slate-400 hover:text-slate-600 flex items-center gap-0.5">
                  <X size={11} /> Clear
                </button>
              )}
            </div>
            <div className="flex flex-wrap gap-1.5">
              {legalForms.slice(0, 16).map(([form, count]) => (
                <button
                  key={form}
                  onClick={() => setSelectedLegalForm(selectedLegalForm === form ? null : form)}
                  className={cn(
                    "px-2.5 py-1 rounded text-xs font-medium border transition-colors",
                    selectedLegalForm === form
                      ? "bg-blue-600 text-white border-blue-600"
                      : "bg-white text-slate-600 border-slate-200 hover:border-blue-300 hover:text-blue-600"
                  )}
                >
                  {form}
                  <span className={cn("ml-1 text-[10px]", selectedLegalForm === form ? "text-blue-200" : "text-slate-400")}>
                    {count.toLocaleString()}
                  </span>
                </button>
              ))}
            </div>
          </div>
        )}

        {/* Step 5: Minimum score */}
        <div className="space-y-3">
          <div className="flex items-center gap-2">
            <span className="flex items-center justify-center w-5 h-5 rounded-full bg-blue-600 text-white text-xs font-bold shrink-0">5</span>
            <h2 className="text-sm font-semibold text-slate-700 uppercase tracking-wide">Minimum Quality Score</h2>
          </div>
          <div className="flex gap-2">
            {SCORE_PRESETS.map((preset) => (
              <button
                key={preset.value}
                onClick={() => setScoreMin(preset.value)}
                title={preset.description}
                className={cn(
                  "px-4 py-2 rounded-lg text-sm border transition-colors",
                  scoreMin === preset.value
                    ? "bg-blue-600 text-white border-blue-600 font-medium"
                    : "bg-white text-slate-600 border-slate-200 hover:border-blue-300 hover:text-blue-600"
                )}
              >
                {preset.label}
              </button>
            ))}
          </div>
        </div>

        {/* Step 6: Review status filter */}
        <div className="space-y-3">
          <div className="flex items-center gap-2">
            <span className="flex items-center justify-center w-5 h-5 rounded-full bg-blue-600 text-white text-xs font-bold shrink-0">6</span>
            <h2 className="text-sm font-semibold text-slate-700 uppercase tracking-wide">Review Status</h2>
            {selectedReview && (
              <button onClick={() => setSelectedReview(null)} className="ml-auto text-xs text-slate-400 hover:text-slate-600 flex items-center gap-0.5">
                <X size={11} /> Clear
              </button>
            )}
          </div>
          <div className="flex flex-wrap gap-2">
            {REVIEW_QUICK_FILTERS.map((f) => (
              <button
                key={f.value}
                onClick={() => setSelectedReview(selectedReview === f.value ? null : f.value)}
                title={f.description}
                className={cn(
                  "px-3 py-1.5 rounded-full text-sm border transition-colors",
                  selectedReview === f.value
                    ? "bg-slate-700 text-white border-slate-700 font-medium"
                    : "bg-white text-slate-600 border-slate-200 hover:border-slate-400 hover:text-slate-700"
                )}
              >
                {f.label}
              </button>
            ))}
          </div>
        </div>

        {/* Quick starts */}
        <div className="space-y-3">
          <div className="flex items-center gap-2">
            <span className="flex items-center justify-center w-5 h-5 rounded-full bg-slate-400 text-white text-xs font-bold shrink-0">✦</span>
            <h2 className="text-sm font-semibold text-slate-700 uppercase tracking-wide">Quick Starts</h2>
          </div>
          <div className="flex flex-wrap gap-2">
            {[
              {
                label: "New & Notable",
                description: "Active companies with a website, score ≥ 40, sorted by newest",
                filters: { status: "ACTIVE", has_website: true, min_combined_score: 40, sort: "-created" } as Partial<CompanyFilters>,
              },
              {
                label: "Top Scored",
                description: "Highest combined score across all active companies",
                filters: { status: "ACTIVE", min_combined_score: 60, sort: "-combined_score" } as Partial<CompanyFilters>,
              },
              {
                label: "Unscored",
                description: "Active companies not yet classified by AI",
                filters: { status: "ACTIVE", ai_category: "_none", sort: "-created" } as Partial<CompanyFilters>,
              },
              {
                label: "Not yet reviewed",
                description: "Active companies with no review status",
                filters: { status: "ACTIVE", exclude_review_status: "interesting,potential_proposal,confirmed_proposal,rejected", sort: "-combined_score" } as Partial<CompanyFilters>,
              },
            ].map((preset) => (
              <button
                key={preset.label}
                onClick={() => onBrowse({ ...BROWSE_DEFAULTS, ...preset.filters })}
                className="group flex flex-col items-start px-4 py-2.5 rounded-xl border border-slate-200 bg-white hover:border-blue-300 hover:shadow-sm transition-all text-left"
              >
                <span className="text-sm font-medium text-slate-700 group-hover:text-blue-700">{preset.label}</span>
                <span className="text-xs text-slate-400 mt-0.5">{preset.description}</span>
              </button>
            ))}
          </div>
        </div>

        {/* CTA */}
        <div className="flex flex-col items-center gap-2 pt-2">
          <p className="text-sm text-slate-500">
            {matchCount !== null ? (
              <span>
                <span className="font-semibold text-slate-800">{matchCount.toLocaleString()}</span> active companies match
              </span>
            ) : (
              <span className="text-slate-300">Loading…</span>
            )}
          </p>
          <div className="flex items-center gap-2">
            <button
              onClick={handleBrowse}
              disabled={matchCount === 0}
              className={cn(
                "flex items-center gap-2 px-6 py-2.5 rounded-lg text-sm font-medium transition-colors",
                matchCount === 0
                  ? "bg-slate-100 text-slate-400 cursor-not-allowed"
                  : "bg-blue-600 text-white hover:bg-blue-700"
              )}
            >
              Browse these companies
              <ChevronRight size={16} />
            </button>
            {matchCount !== null && matchCount > 0 && (
              <button
                onClick={handleBulkMark}
                disabled={bulkMarking}
                title={`Mark up to ${Math.min(matchCount, 200)} matches as interesting`}
                className="flex items-center gap-1.5 px-4 py-2.5 rounded-lg text-sm font-medium border border-yellow-300 bg-yellow-50 text-yellow-800 hover:bg-yellow-100 transition-colors disabled:opacity-50"
              >
                <Star size={14} />
                Mark interesting
              </button>
            )}
          </div>
          {bulkBanner && (
            <p className="text-xs text-green-700 bg-green-50 border border-green-200 rounded-lg px-3 py-1.5">{bulkBanner}</p>
          )}
          <button
            onClick={() => onBrowse(BROWSE_DEFAULTS)}
            className="text-xs text-slate-400 hover:text-slate-600 underline underline-offset-2"
          >
            Skip — show all companies
          </button>
        </div>
      </div>
    </div>
  );
}

// ── Browse mode ────────────────────────────────────────────────────────────────

interface BrowseViewProps {
  initialFilters: CompanyFilters;
  initialCantons: string[];
  initialStats: CompanyStats;
  initialTaxonomy: Record<string, [string, number][]>;
}

function BrowseView({ initialFilters, initialCantons, initialStats, initialTaxonomy }: BrowseViewProps) {
  const router = useRouter();
  const [filters, setFiltersState] = useState<CompanyFilters>(initialFilters);
  const [, startTransition] = useTransition();

  const setFilters = useCallback((update: CompanyFilters | ((f: CompanyFilters) => CompanyFilters)) => {
    setFiltersState(prev => {
      const next = typeof update === "function" ? update(prev) : update;
      // sync to URL so back-navigation works
      const params = new URLSearchParams();
      for (const [k, v] of Object.entries(next)) {
        const isDefault =
          (k === "sort" && v === "-combined_score") ||
          (k === "page" && v === 1) ||
          (k === "page_size" && v === 50) ||
          (k === "status" && v === "ACTIVE");
        if (v !== undefined && v !== null && v !== "" && !isDefault) params.set(k, String(v));
      }
      const qs = params.toString();
      router.replace(qs ? `/app/explorer?${qs}` : "/app/explorer", { scroll: false });
      return next;
    });
  }, [router]);

  const [selectedCompany, setSelectedCompany] = useState<Company | null>(null);
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
    setSelectedIds(prev => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  }
  function selectAll(ids: number[]) {
    const allSelected = ids.every(id => selectedIds.has(id));
    setSelectedIds(prev => {
      const next = new Set(prev);
      if (allSelected) ids.forEach(id => next.delete(id));
      else ids.forEach(id => next.add(id));
      return next;
    });
  }
  async function batchUpdate(field: string, value: string | null) {
    if (selectedIds.size === 0) return;
    setBatchLoading(true);
    try {
      await bulkUpdateCompanies([...selectedIds], field, value);
      setSelectedIds(new Set());
      mutateCompanies();
    } finally {
      setBatchLoading(false);
    }
  }

  async function handleBulkTag(action: "add" | "remove") {
    const tag = tagInput.trim();
    if (!tag || selectedIds.size === 0) return;
    setTagLoading(true);
    try {
      await bulkTagCompanies([...selectedIds], tag, action);
      setTagInput("");
      setSelectedIds(new Set());
      mutateCompanies();
    } finally {
      setTagLoading(false);
    }
  }

  async function handleInlineReview(id: number, status: string | null) {
    await bulkUpdateCompanies([id], "review_status", status);
    mutateCompanies();
  }

  // Active filter chips summary for the browse bar
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
        <span><span className="font-semibold text-slate-700">{(stats?.total ?? 0).toLocaleString()}</span> total</span>
        <span>·</span>
        <span><span className="font-semibold text-slate-700">{(stats?.with_website ?? 0).toLocaleString()}</span> with website</span>
        <span>·</span>
        <span title="Reviewed as interesting"><span className="font-semibold text-amber-600">{(stats?.review?.interesting ?? 0).toLocaleString()}</span> interesting</span>
        <span>·</span>
        <span title="Confirmed proposals"><span className="font-semibold text-emerald-600">{(stats?.review?.confirmed_proposal ?? 0).toLocaleString()}</span> confirmed</span>
        {page?.total !== undefined && (
          <>
            <span>·</span>
            <span>
              <span className="font-semibold text-blue-600">{page.total.toLocaleString()}</span> matching
            </span>
          </>
        )}
        {activeFilters.length > 0 && (
          <div className="flex items-center gap-1.5 ml-1">
            {activeFilters.map(({ label, key }) => (
              <span
                key={key}
                className="flex items-center gap-1 bg-blue-50 text-blue-700 border border-blue-200 rounded-full px-2 py-0.5 text-xs"
              >
                {label}
                <button
                  onClick={() => setFilters((f) => { const n = { ...f }; delete (n as Record<string, unknown>)[key]; return n; })}
                  className="hover:text-blue-900"
                >
                  <X size={10} />
                </button>
              </span>
            ))}
          </div>
        )}
        <div className="ml-auto">
          <ScoreDistributionBar filters={filters} />
        </div>
      </div>

      {/* Filter bar */}
      <FilterBar
        filters={filters}
        cantons={cantons}
        taxonomy={taxonomy}
        onChange={(f) => startTransition(() => setFilters(f))}
        onClear={() => setFilters(BROWSE_DEFAULTS)}
        resultCount={page?.total ?? 0}
        savedViews={savedViews}
        onSaveView={async (name) => { await saveView(name, filters); mutateSavedViews(); }}
        onLoadView={(f) => setFilters({ ...BROWSE_DEFAULTS, ...f })}
        onDeleteView={async (id) => { await deleteView(id); mutateSavedViews(); }}
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
                  <button
                    key={v}
                    disabled={batchLoading}
                    onClick={() => batchUpdate("review_status", v)}
                    className="px-2 py-0.5 text-xs rounded border border-slate-200 bg-white hover:bg-blue-50 hover:border-blue-300 hover:text-blue-700 disabled:opacity-50 transition-colors"
                  >
                    {v.replace(/_/g, " ")}
                  </button>
                ))}
                <button
                  disabled={batchLoading}
                  onClick={() => batchUpdate("review_status", null)}
                  className="px-2 py-0.5 text-xs rounded border border-slate-200 bg-white hover:bg-red-50 hover:border-red-300 hover:text-red-700 disabled:opacity-50 transition-colors"
                >
                  Clear
                </button>
                <span className="text-slate-300">|</span>
                <span className="text-xs text-slate-500">Tag:</span>
                <input
                  type="text"
                  value={tagInput}
                  onChange={(e) => setTagInput(e.target.value)}
                  placeholder="tag name…"
                  className="px-2 py-0.5 text-xs border border-slate-200 rounded focus:outline-none focus:ring-1 focus:ring-blue-300 w-24"
                  onKeyDown={(e) => { if (e.key === "Enter") handleBulkTag("add"); }}
                />
                <button
                  disabled={tagLoading || !tagInput.trim()}
                  onClick={() => handleBulkTag("add")}
                  className="px-2 py-0.5 text-xs rounded border border-slate-200 bg-white hover:bg-emerald-50 hover:border-emerald-300 hover:text-emerald-700 disabled:opacity-50 transition-colors"
                >
                  +Tag
                </button>
                <button
                  disabled={tagLoading || !tagInput.trim()}
                  onClick={() => handleBulkTag("remove")}
                  className="px-2 py-0.5 text-xs rounded border border-slate-200 bg-white hover:bg-red-50 hover:border-red-300 hover:text-red-700 disabled:opacity-50 transition-colors"
                >
                  −Tag
                </button>
                <button onClick={() => setSelectedIds(new Set())} className="ml-1 text-xs text-slate-400 hover:text-slate-600">
                  <X size={12} />
                </button>
              </div>
            ) : (
              <div />
            )}
            <a
              href={exportBlocked ? undefined : buildExportUrl(filters)}
              download={exportBlocked ? undefined : true}
              className={`flex items-center gap-1.5 text-xs px-2.5 py-1 rounded border transition-colors ${
                exportBlocked
                  ? "text-slate-300 border-slate-200 bg-slate-100 cursor-not-allowed pointer-events-none"
                  : "text-slate-500 hover:text-slate-700 border-slate-200 hover:bg-white"
              }`}
              title={exportBlocked ? `Your current plan allows up to ${exportLimit.toLocaleString()} rows per CSV export.` : undefined}
            >
              <Download size={12} /> Export CSV ({page?.total ?? 0})
            </a>
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
            onSort={(sort) => setFilters((f) => ({ ...f, sort, page: 1 }))}
            isLoading={isLoading}
            selectedIds={selectedIds}
            onToggleSelect={toggleSelect}
            onSelectAll={selectAll}
            onUpdateReview={handleInlineReview}
          />
          <Pagination
            page={page?.page ?? 1}
            pages={page?.pages ?? 1}
            total={page?.total ?? 0}
            pageSize={filters.page_size ?? 50}
            onChange={(p) => setFilters((f) => ({ ...f, page: p }))}
            onPageSizeChange={(s) => setFilters((f) => ({ ...f, page_size: s, page: 1 }))}
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
    </div>
  );
}

// ── Root component ─────────────────────────────────────────────────────────────

interface ExplorerClientProps {
  initialCantons: string[];
  initialStats: CompanyStats;
  initialTaxonomy: Record<string, [string, number][]>;
}

export function ExplorerClient({ initialCantons, initialStats, initialTaxonomy }: ExplorerClientProps) {
  const [mode, setMode] = useState<Mode>("guided");
  const [browseFilters, setBrowseFilters] = useState<CompanyFilters>(BROWSE_DEFAULTS);

  const { data: me } = useSWR("me", fetchCurrentUser);
  const orgId = me?.org_id ?? null;

  const { data: effectiveSettings } = useSWR(
    orgId ? ["org-effective-settings", orgId] : null,
    () => fetchOrgEffectiveSettings(orgId!),
  );

  const hasApiKey = effectiveSettings?.anthropic_api_key_set ?? false;
  const hasTargetDescription = !!(effectiveSettings?.claude_target_description?.trim());

  function handleBrowse(filters: CompanyFilters) {
    setBrowseFilters(filters);
    setMode("browse");
  }

  return (
    <div className="flex flex-col h-[calc(100vh-3rem)] overflow-hidden">
      {/* Mode toggle header */}
      <div className="flex items-center justify-between px-4 py-2 bg-white border-b border-slate-200 shrink-0">
        <div className="flex items-center gap-1 bg-slate-100 rounded-lg p-0.5">
          <button
            onClick={() => setMode("guided")}
            className={cn(
              "flex items-center gap-1.5 px-3 py-1.5 rounded-md text-sm font-medium transition-colors",
              mode === "guided" ? "bg-white text-slate-800 shadow-sm" : "text-slate-500 hover:text-slate-700"
            )}
          >
            <Compass size={14} />
            Guided
          </button>
          <button
            onClick={() => setMode("browse")}
            className={cn(
              "flex items-center gap-1.5 px-3 py-1.5 rounded-md text-sm font-medium transition-colors",
              mode === "browse" ? "bg-white text-slate-800 shadow-sm" : "text-slate-500 hover:text-slate-700"
            )}
          >
            <Search size={14} />
            Browse
          </button>
        </div>
        {mode === "guided" && (
          <p className="text-xs text-slate-400 hidden sm:block">Pick your criteria, then click Browse</p>
        )}
        {mode === "browse" && (
          <button
            onClick={() => setMode("guided")}
            className="text-xs text-blue-600 hover:underline hidden sm:block"
          >
            ← Back to guided selection
          </button>
        )}
      </div>

      {/* Setup gate — shown in both modes */}
      <SetupGateBanner orgId={orgId} hasApiKey={hasApiKey} hasTargetDescription={hasTargetDescription} />

      {/* Content */}
      {mode === "guided" ? (
        <GuidedPicker
          cantons={initialCantons}
          taxonomy={initialTaxonomy}
          stats={initialStats}
          onBrowse={handleBrowse}
          orgId={orgId}
          hasApiKey={hasApiKey}
          hasTargetDescription={hasTargetDescription}
        />
      ) : (
        <BrowseView
          initialFilters={browseFilters}
          initialCantons={initialCantons}
          initialStats={initialStats}
          initialTaxonomy={initialTaxonomy}
        />
      )}
    </div>
  );
}
