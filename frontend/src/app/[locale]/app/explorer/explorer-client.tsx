"use client";
import { useState, useCallback, useTransition, Suspense, useEffect, useRef } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import useSWR from "swr";
import {
  Search, X, Download, Wand2, ChevronRight, Sparkles, Target,
  TrendingUp, Building2, SlidersHorizontal, ChevronLeft, AlertTriangle, Loader2,
} from "lucide-react";
import { CompanyTable } from "@/components/dashboard/company-table";
import { CompanyPreview } from "@/components/dashboard/company-preview";
import { Pagination } from "@/components/dashboard/pagination";
import {
  fetchCompanies, fetchStats, fetchCantons,
  fetchCurrentUser, fetchOrgEffectiveSettings, saveOrgWorkspaceSettings,
  fetchOrg, fetchNogaHierarchy, fetchMarketSegments,
  enqueueGenericJob, semanticSearch, fetchPurposeSemanticSearch,
} from "@/lib/api";
import type { NogaNode, MarketSegment, OrgEffectiveSettings, SemanticSearchResponse, SemanticSearchResult, PurposeSearchHit } from "@/lib/api";
import type { Company, CompanyFilters, CompanyStats } from "@/lib/types";
import { cn, formatClusterLabel } from "@/lib/utils";
import { getExportLimit } from "@/lib/entitlements";

// ── Constants ─────────────────────────────────────────────────────────────────

const BROWSE_DEFAULTS: CompanyFilters = { sort: "-combined_score", page: 1, page_size: 50, status: "ACTIVE" };

const LANGUAGE_CHIPS = [
  { value: "de", label: "DE" },
  { value: "fr", label: "FR" },
  { value: "it", label: "IT" },
  { value: "en", label: "EN" },
];

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
      <Sparkles size={16} className="text-amber-500 shrink-0 mt-0.5" />
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <p className="text-sm font-medium text-amber-800">AI scoring is not configured</p>
          <span className="inline-flex items-center gap-1 text-[10px] uppercase tracking-wide text-amber-700 bg-amber-100 border border-amber-300 rounded px-1.5 py-0.5 font-semibold">Pro</span>
        </div>
        <p className="text-xs text-amber-700 mt-0.5">
          AI lead scoring is a paid feature that uses credits per company. Set your {missing.join(" and ")} in <Link href="/app/settings" className="underline">Settings → Scoring</Link> to enable it. Free-tier filtering by NOGA, canton, and score is unaffected.
        </p>
      </div>
    </div>
  );
}

// ── MarketMap ─────────────────────────────────────────────────────────────────

function sectionColor(avgRelevance: number | null): string {
  if (avgRelevance === null) return "bg-slate-100 border-slate-200";
  if (avgRelevance >= 70) return "bg-emerald-50 border-emerald-200";
  if (avgRelevance >= 50) return "bg-blue-50 border-blue-200";
  return "bg-slate-50 border-slate-200";
}

function MarketMap({
  activeSection,
  onSectionClick,
  locale,
}: {
  activeSection: string | null;
  onSectionClick: (section: string | null) => void;
  locale: string;
}) {
  const { data: segments = [], isLoading } = useSWR("market-segments", fetchMarketSegments, {
    dedupingInterval: 3_600_000,
  });

  if (isLoading) {
    return (
      <div className="flex gap-2 px-4 py-3 overflow-x-auto shrink-0">
        {Array.from({ length: 8 }).map((_, i) => (
          <div key={i} className="w-28 h-16 rounded-xl bg-slate-100 animate-pulse shrink-0" />
        ))}
      </div>
    );
  }

  if (segments.length === 0) return null;

  return (
    <div className="px-4 py-2 shrink-0">
      <div className="flex flex-wrap gap-1.5">
        {segments.map((seg) => {
          const label = seg.labels[locale] || seg.labels["de"] || seg.section;
          const isActive = activeSection === seg.section;
          return (
            <button
              key={seg.section}
              onClick={() => onSectionClick(isActive ? null : seg.section)}
              className={cn(
                "flex flex-col items-start px-2.5 py-1.5 rounded-xl border text-left transition-all min-w-0",
                sectionColor(seg.avg_relevance),
                isActive && "ring-2 ring-blue-500 ring-offset-1"
              )}
              title={label}
            >
              <div className="flex items-center gap-1">
                <span className="text-xs font-bold text-slate-700 font-mono">{seg.section}</span>
                <span className="text-xs text-slate-500">{seg.company_count.toLocaleString()}</span>
              </div>
              <span className="text-[10px] text-slate-500 leading-tight max-w-[80px] truncate">{label}</span>
            </button>
          );
        })}
        {activeSection && (
          <button
            onClick={() => onSectionClick(null)}
            className="px-2.5 py-1.5 rounded-xl border border-slate-200 bg-white text-xs text-slate-500 hover:text-slate-800 transition-colors flex items-center gap-1"
          >
            <X size={11} /> Clear
          </button>
        )}
      </div>
    </div>
  );
}

// ── SubSegmentStrip ───────────────────────────────────────────────────────────

function SubSegmentStrip({
  section,
  activeDivision,
  onDivisionClick,
}: {
  section: string;
  activeDivision: string | null;
  onDivisionClick: (division: string | null) => void;
}) {
  const { data: hierarchy = [] } = useSWR("noga-hierarchy", fetchNogaHierarchy, {
    dedupingInterval: 3_600_000,
  });

  const sectionNode = (hierarchy as NogaNode[]).find(
    (n) => n.code.toUpperCase() === section.toUpperCase()
  );
  if (!sectionNode?.children?.length) return null;

  return (
    <div className="px-4 py-2 border-b border-slate-100 bg-slate-50 shrink-0">
      <div className="flex items-center gap-2 overflow-x-auto">
        <span className="text-[10px] font-medium text-slate-400 uppercase tracking-wide shrink-0">Division</span>
        {sectionNode.children.map((div) => {
          const isActive = activeDivision === div.code;
          return (
            <button
              key={div.code}
              onClick={() => onDivisionClick(isActive ? null : div.code)}
              className={cn(
                "flex items-center gap-1 px-2 py-1 rounded-lg text-xs border shrink-0 transition-colors",
                isActive
                  ? "bg-blue-600 text-white border-blue-600"
                  : "bg-white text-slate-600 border-slate-200 hover:border-blue-300"
              )}
            >
              <span className="font-mono font-medium">{div.code}</span>
              <span className="text-slate-500 truncate max-w-[100px]">{div.label?.split(" ")[0]}</span>
              {div.count != null && <span className="opacity-60">({div.count.toLocaleString()})</span>}
            </button>
          );
        })}
      </div>
    </div>
  );
}

// ── AutocompleteInput ─────────────────────────────────────────────────────────

function AutocompleteInput({
  placeholder,
  fetchFn,
  valueKey,
  labelKey = valueKey,
  value,
  onChange,
}: {
  placeholder: string;
  fetchFn: (q: string) => Promise<Record<string, unknown>[]>;
  valueKey: string;
  labelKey?: string;
  value: string;
  onChange: (v: string) => void;
}) {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<Record<string, unknown>[]>([]);
  const [open, setOpen] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    if (!query) { setResults([]); return; }
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(async () => {
      try {
        const r = await fetchFn(query);
        setResults(r.slice(0, 20));
        setOpen(true);
      } catch { setResults([]); }
    }, 300);
  }, [query, fetchFn]);

  const selectedItems = value.split("|").filter(Boolean);

  function addItem(item: string) {
    if (!selectedItems.includes(item)) {
      onChange([...selectedItems, item].join("|"));
    }
    setQuery("");
    setResults([]);
    setOpen(false);
  }

  function removeItem(item: string) {
    onChange(selectedItems.filter(i => i !== item).join("|"));
  }

  return (
    <div className="relative">
      {selectedItems.length > 0 && (
        <div className="flex flex-wrap gap-1 mb-1.5">
          {selectedItems.map(item => (
            <span key={item} className="flex items-center gap-1 px-2 py-0.5 rounded-full bg-blue-100 text-blue-700 text-xs">
              {item}
              <button onClick={() => removeItem(item)}><X size={10} /></button>
            </span>
          ))}
        </div>
      )}
      <input
        type="text"
        value={query}
        onChange={e => setQuery(e.target.value)}
        onFocus={() => results.length > 0 && setOpen(true)}
        onBlur={() => setTimeout(() => setOpen(false), 150)}
        placeholder={placeholder}
        className="w-full text-xs border border-slate-200 rounded-lg px-2.5 py-1.5 focus:outline-none focus:ring-1 focus:ring-blue-300"
      />
      {open && results.length > 0 && (
        <div className="absolute z-20 top-full left-0 right-0 mt-1 bg-white border border-slate-200 rounded-lg shadow-lg max-h-40 overflow-y-auto">
          {results.map((r) => {
            const v = String(r[valueKey]);
            const l = String(r[labelKey]);
            const cnt = r["count"] as number | undefined;
            return (
              <button
                key={v}
                onMouseDown={() => addItem(v)}
                className="w-full text-left px-3 py-1.5 text-xs hover:bg-blue-50 flex items-center justify-between"
              >
                <span className="truncate">{l}</span>
                {cnt != null && <span className="text-slate-400 ml-2 shrink-0">{cnt.toLocaleString()}</span>}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}

// ── FilterSidebar ─────────────────────────────────────────────────────────────

interface FilterSidebarProps {
  filters: CompanyFilters;
  cantons: string[];
  onChange: (patch: Partial<CompanyFilters>) => void;
  collapsed: boolean;
  onToggle: () => void;
}

function FilterSidebar({ filters, cantons, onChange, collapsed, onToggle }: FilterSidebarProps) {
  if (collapsed) {
    return (
      <div className="w-8 border-r border-slate-100 bg-white flex flex-col items-center pt-3 shrink-0">
        <button onClick={onToggle} className="text-slate-400 hover:text-slate-700" title="Expand filters">
          <SlidersHorizontal size={15} />
        </button>
      </div>
    );
  }

  return (
    <div className="w-56 border-r border-slate-100 bg-white flex flex-col shrink-0 overflow-y-auto">
      {/* Header */}
      <div className="flex items-center justify-between px-3 py-2.5 border-b border-slate-100">
        <span className="text-xs font-semibold text-slate-500 uppercase tracking-wide flex items-center gap-1.5">
          <SlidersHorizontal size={12} /> Filters
        </span>
        <button onClick={onToggle} className="text-slate-400 hover:text-slate-600">
          <ChevronLeft size={14} />
        </button>
      </div>

      <div className="px-3 py-3 space-y-4 text-xs">

        {/* Score range */}
        <div>
          <label className="block font-medium text-slate-500 mb-1.5">Score range</label>
          <div className="flex items-center gap-1">
            <input
              type="number" min={0} max={100} placeholder="Min"
              value={filters.min_combined_score ?? ""}
              onChange={e => onChange({ min_combined_score: e.target.value ? Number(e.target.value) : undefined, page: 1 })}
              className="w-full border border-slate-200 rounded px-2 py-1 text-xs focus:outline-none focus:ring-1 focus:ring-blue-300"
            />
            <span className="text-slate-400">–</span>
            <input
              type="number" min={0} max={100} placeholder="Max"
              value={filters.max_combined_score ?? ""}
              onChange={e => onChange({ max_combined_score: e.target.value ? Number(e.target.value) : undefined, page: 1 })}
              className="w-full border border-slate-200 rounded px-2 py-1 text-xs focus:outline-none focus:ring-1 focus:ring-blue-300"
            />
          </div>
        </div>

        {/* Canton */}
        <div>
          <label className="block font-medium text-slate-500 mb-1.5">Canton</label>
          <select
            value={filters.canton ?? ""}
            onChange={e => onChange({ canton: e.target.value || undefined, page: 1 })}
            className="w-full border border-slate-200 rounded px-2 py-1 text-xs focus:outline-none focus:ring-1 focus:ring-blue-300 bg-white"
          >
            <option value="">All cantons</option>
            {cantons.map(c => <option key={c} value={c}>{c}</option>)}
          </select>
        </div>

        {/* Language */}
        <div>
          <label className="block font-medium text-slate-500 mb-1.5">Language</label>
          <div className="flex gap-1 flex-wrap">
            {LANGUAGE_CHIPS.map(({ value, label }) => (
              <button
                key={value}
                onClick={() => onChange({ purpose_language: filters.purpose_language === value ? undefined : value, page: 1 })}
                className={cn(
                  "px-2 py-0.5 rounded border text-xs font-medium transition-colors",
                  filters.purpose_language === value
                    ? "bg-blue-600 text-white border-blue-600"
                    : "bg-white text-slate-600 border-slate-200 hover:border-blue-300"
                )}
              >
                {label}
              </button>
            ))}
          </div>
        </div>

        {/* Legal form */}
        <div>
          <label className="block font-medium text-slate-500 mb-1.5">Legal form</label>
          <input
            type="text" placeholder="e.g. GmbH, AG"
            value={filters.legal_form ?? ""}
            onChange={e => onChange({ legal_form: e.target.value || undefined, page: 1 })}
            className="w-full border border-slate-200 rounded px-2 py-1 text-xs focus:outline-none focus:ring-1 focus:ring-blue-300"
          />
        </div>

        {/* Review status */}
        <div>
          <label className="block font-medium text-slate-500 mb-1.5">Review status</label>
          <select
            value={filters.review_status ?? ""}
            onChange={e => onChange({ review_status: e.target.value || undefined, page: 1 })}
            className="w-full border border-slate-200 rounded px-2 py-1 text-xs focus:outline-none focus:ring-1 focus:ring-blue-300 bg-white"
          >
            <option value="">Any</option>
            <option value="_none">Not set</option>
            <option value="potential_proposal">Potential proposal</option>
            <option value="confirmed_proposal">Confirmed proposal</option>
            <option value="interesting">Interesting</option>
            <option value="rejected">Rejected</option>
          </select>
        </div>

        {/* Contact status */}
        <div>
          <label className="block font-medium text-slate-500 mb-1.5">Contact status</label>
          <select
            value={filters.contact_status ?? ""}
            onChange={e => onChange({ contact_status: e.target.value || undefined, page: 1 })}
            className="w-full border border-slate-200 rounded px-2 py-1 text-xs focus:outline-none focus:ring-1 focus:ring-blue-300 bg-white"
          >
            <option value="">Any</option>
            <option value="_none">Not set</option>
            <option value="sent">Sent</option>
            <option value="responded">Responded</option>
            <option value="converted">Converted</option>
            <option value="rejected">Rejected</option>
          </select>
        </div>

        {/* Tags */}
        <div>
          <label className="block font-medium text-slate-500 mb-1.5">Tags</label>
          <input
            type="text" placeholder="Comma-separated"
            value={filters.tags ?? ""}
            onChange={e => onChange({ tags: e.target.value || undefined, page: 1 })}
            className="w-full border border-slate-200 rounded px-2 py-1 text-xs focus:outline-none focus:ring-1 focus:ring-blue-300"
          />
        </div>

        {/* Date range */}
        <div>
          <label className="block font-medium text-slate-500 mb-1.5">Registered after</label>
          <input
            type="date"
            value={filters.registered_after ?? ""}
            onChange={e => onChange({ registered_after: e.target.value || undefined, page: 1 })}
            className="w-full border border-slate-200 rounded px-2 py-1 text-xs focus:outline-none focus:ring-1 focus:ring-blue-300"
          />
        </div>

        {/* Clear all */}
        <button
          onClick={() => onChange({ ...BROWSE_DEFAULTS, sort: filters.sort })}
          className="w-full text-center text-xs text-slate-400 hover:text-slate-600 pt-1"
        >
          Clear all filters
        </button>
      </div>
    </div>
  );
}

// ── ScoringWizard ─────────────────────────────────────────────────────────────

function ScoringWizard({
  orgId,
  settings,
  onClose,
  onSaved,
}: {
  orgId: number;
  settings: OrgEffectiveSettings | null | undefined;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [step, setStep] = useState(1);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [targetClusters, setTargetClusters] = useState(() => settings?.scoring_target_clusters || "");
  const [targetKws, setTargetKws] = useState(() => settings?.scoring_target_keywords || "");
  const [excludeKws, setExcludeKws] = useState(() => settings?.scoring_exclude_keywords || "");
  const [kwHitPts, setKwHitPts] = useState(() => settings?.scoring_keyword_hit_points || "10");
  const [kwExclPts, setKwExclPts] = useState(() => settings?.scoring_keyword_exclude_points || "10");
  const [nogaTargets, setNogaTargets] = useState(() => settings?.scoring_noga_targets || "");

  async function clusterSearch(q: string) {
    const res = await fetch(`/api/v1/companies/clusters/search?q=${encodeURIComponent(q)}&limit=20`, { credentials: "include" });
    return res.ok ? res.json() : [];
  }

  async function keywordSearch(q: string) {
    const res = await fetch(`/api/v1/companies/keywords/search?q=${encodeURIComponent(q)}&limit=20`, { credentials: "include" });
    return res.ok ? res.json() : [];
  }

  async function handleSave() {
    setSaving(true);
    setError(null);
    try {
      await saveOrgWorkspaceSettings(orgId, {
        scoring_target_clusters: targetClusters,
        scoring_target_keywords: targetKws,
        scoring_exclude_keywords: excludeKws,
        scoring_keyword_hit_points: kwHitPts,
        scoring_keyword_exclude_points: kwExclPts,
        scoring_noga_targets: nogaTargets,
      });
      onSaved();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to save settings");
    } finally {
      setSaving(false);
    }
  }

  const STEPS = ["Clusters", "Keywords", "NOGA targets", "Save"];

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" onClick={onClose}>
      <div
        className="bg-white rounded-2xl shadow-2xl border border-slate-200 w-full max-w-lg max-h-[90vh] flex flex-col"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between px-6 pt-5 pb-4 border-b border-slate-100">
          <div className="flex items-center gap-2">
            <Wand2 size={18} className="text-blue-600" />
            <h2 className="text-base font-semibold text-slate-800">Scoring Wizard</h2>
          </div>
          <button onClick={onClose} className="text-slate-400 hover:text-slate-600"><X size={18} /></button>
        </div>

        <div className="flex items-center gap-1 px-6 py-3 bg-slate-50 border-b border-slate-100">
          {STEPS.map((label, idx) => {
            const s = idx + 1;
            return (
              <div key={s} className="flex items-center gap-1">
                <div className={cn(
                  "w-6 h-6 rounded-full flex items-center justify-center text-xs font-bold",
                  step === s ? "bg-blue-600 text-white" : step > s ? "bg-emerald-500 text-white" : "bg-slate-200 text-slate-500"
                )}>{step > s ? "✓" : s}</div>
                {s < STEPS.length && <div className={cn("w-6 h-0.5", step > s ? "bg-emerald-300" : "bg-slate-200")} />}
              </div>
            );
          })}
          <span className="ml-2 text-xs text-slate-500">{STEPS[step - 1]}</span>
        </div>

        <div className="flex-1 overflow-y-auto px-6 py-5 space-y-4">
          {step === 1 && (
            <div className="space-y-4">
              <p className="text-sm text-slate-600">Search and select clusters to target in flex scoring.</p>
              <AutocompleteInput
                placeholder="Type to search clusters…"
                fetchFn={clusterSearch}
                valueKey="cluster"
                value={targetClusters}
                onChange={setTargetClusters}
              />
              <p className="text-xs text-slate-400">Companies whose cluster label matches any selected term score higher.</p>
            </div>
          )}

          {step === 2 && (
            <div className="space-y-4">
              <p className="text-sm text-slate-600">Keywords that boost or penalise the flex score.</p>
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="text-xs font-medium text-emerald-700 block mb-1">Target keywords (boost)</label>
                  <AutocompleteInput
                    placeholder="Search…"
                    fetchFn={keywordSearch}
                    valueKey="keyword"
                    value={targetKws}
                    onChange={setTargetKws}
                  />
                  <div className="mt-1 flex items-center gap-1">
                    <span className="text-xs text-slate-400">Points per hit:</span>
                    <input type="number" value={kwHitPts} onChange={e => setKwHitPts(e.target.value)}
                      className="w-14 text-xs border border-slate-200 rounded px-2 py-0.5 text-center" />
                  </div>
                </div>
                <div>
                  <label className="text-xs font-medium text-red-600 block mb-1">Exclude keywords (penalty)</label>
                  <AutocompleteInput
                    placeholder="Search…"
                    fetchFn={keywordSearch}
                    valueKey="keyword"
                    value={excludeKws}
                    onChange={setExcludeKws}
                  />
                  <div className="mt-1 flex items-center gap-1">
                    <span className="text-xs text-slate-400">Points per hit:</span>
                    <input type="number" value={kwExclPts} onChange={e => setKwExclPts(e.target.value)}
                      className="w-14 text-xs border border-slate-200 rounded px-2 py-0.5 text-center" />
                  </div>
                </div>
              </div>
            </div>
          )}

          {step === 3 && (
            <div className="space-y-4">
              <p className="text-sm text-slate-600">
                Bonus points by NOGA code. Format:{" "}
                <code className="font-mono text-xs bg-slate-100 px-1 rounded">CODE:points</code>, pipe-separated.
              </p>
              <textarea
                value={nogaTargets}
                onChange={(e) => setNogaTargets(e.target.value)}
                rows={3}
                className="w-full text-xs border border-slate-200 rounded-lg px-3 py-2 focus:outline-none focus:ring-2 focus:ring-blue-300 font-mono"
                placeholder="e.g. J:20|62:35|620:40|M:15"
              />
              <div className="rounded-lg border border-slate-200 bg-slate-50 p-3 text-xs text-slate-600 space-y-1">
                <p><strong>J</strong> (section) → <strong>62</strong> (division) → <strong>620</strong> (group). Most specific match wins.</p>
              </div>
            </div>
          )}

          {step === 4 && (
            <div className="space-y-4">
              <div className="bg-slate-50 rounded-xl border border-slate-200 p-4 space-y-2 text-sm">
                <p className="font-medium text-slate-700">Summary</p>
                <div className="space-y-1 text-xs text-slate-600">
                  <div><span className="font-medium">Target clusters:</span> {targetClusters || "(none)"}</div>
                  <div><span className="font-medium">Target keywords:</span> {targetKws || "(none)"} (+{kwHitPts}pts)</div>
                  <div><span className="font-medium">Exclude keywords:</span> {excludeKws || "(none)"} (-{kwExclPts}pts)</div>
                  <div><span className="font-medium">NOGA targets:</span> {nogaTargets || "(none)"}</div>
                </div>
              </div>
              <p className="text-xs text-slate-500">After saving, go to <strong>Settings → Admin → Recalculate Scores</strong> to apply new scoring.</p>
              {error && <p className="text-xs text-red-600 bg-red-50 border border-red-200 rounded-lg px-3 py-2">{error}</p>}
            </div>
          )}
        </div>

        <div className="flex items-center justify-between px-6 py-4 border-t border-slate-100">
          <button
            onClick={() => step > 1 ? setStep(s => s - 1) : onClose()}
            className="text-sm text-slate-500 hover:text-slate-700"
          >
            {step > 1 ? "← Back" : "Cancel"}
          </button>
          {step < STEPS.length ? (
            <button
              onClick={() => setStep(s => s + 1)}
              className="px-4 py-2 rounded-lg bg-blue-600 text-white text-sm font-medium hover:bg-blue-700"
            >
              Next →
            </button>
          ) : (
            <button
              onClick={handleSave}
              disabled={saving}
              className="px-4 py-2 rounded-lg bg-emerald-600 text-white text-sm font-medium hover:bg-emerald-700 disabled:opacity-50"
            >
              {saving ? "Saving…" : "Save settings"}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

// ── ExplorerPage (main) ───────────────────────────────────────────────────────

interface ExplorerPageProps {
  initialCantons: string[];
  initialStats: CompanyStats;
  locale: string;
}

function ExplorerPage({ initialCantons, initialStats, locale }: ExplorerPageProps) {
  const searchParams = useSearchParams();
  const urlNogaCode = searchParams?.get("noga_code") ?? null;

  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [wizardOpen, setWizardOpen] = useState(false);
  const [selectedCompany, setSelectedCompany] = useState<Company | null>(null);
  const [activeSection, setActiveSection] = useState<string | null>(
    urlNogaCode && /^[A-Za-z]$/.test(urlNogaCode) ? urlNogaCode.toUpperCase() : null
  );
  const [activeDivision, setActiveDivision] = useState<string | null>(
    urlNogaCode && !/^[A-Za-z]$/.test(urlNogaCode) ? urlNogaCode : null
  );
  const [semanticQuery, setSemanticQuery] = useState("");
  const [semanticResults, setSemanticResults] = useState<SemanticSearchResponse | null>(null);
  const [semanticLoading, setSemanticLoading] = useState(false);
  const [purposeHits, setPurposeHits] = useState<PurposeSearchHit[] | null>(null);
  const [purposeLoading, setPurposeLoading] = useState(false);
  const searchTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [, startTransition] = useTransition();

  const { data: me } = useSWR("me", fetchCurrentUser);
  const orgId = me?.org_id ?? null;
  const { data: org } = useSWR(
    me?.org?.id ? ["org-detail", me.org.id] : null,
    () => fetchOrg(me!.org!.id)
  );
  const { data: effectiveSettings, mutate: mutateSettings } = useSWR(
    orgId ? ["org-effective-settings", orgId] : null,
    () => fetchOrgEffectiveSettings(orgId!),
  );
  const { data: cantons = initialCantons } = useSWR("cantons", fetchCantons, { fallbackData: initialCantons });

  const hasApiKey = effectiveSettings?.anthropic_api_key_set ?? false;
  const hasTargetDescription = !!(effectiveSettings?.claude_target_description?.trim());
  const exportLimit = org ? getExportLimit({ tier: org.tier, customFeatures: org.custom_features }) : 100;

  // Build NOGA filter from section/division
  const nogaCodeFilter: string | undefined = activeDivision
    ? activeDivision
    : activeSection
    ? `${activeSection}` // section letter prefix match
    : undefined;

  const [filters, setFilters] = useState<CompanyFilters>({
    ...BROWSE_DEFAULTS,
    ...(nogaCodeFilter ? { noga_code: nogaCodeFilter } : {}),
  });

  // Keep noga_code in sync when section/division changes
  useEffect(() => {
    setFilters(prev => ({
      ...prev,
      noga_code: nogaCodeFilter || undefined,
      page: 1,
    }));
  }, [nogaCodeFilter]);

  const { data: page, isLoading: companiesLoading, mutate: mutateCompanies } = useSWR(
    ["companies", filters],
    () => fetchCompanies(filters),
    { keepPreviousData: true }
  );

  function patchFilters(patch: Partial<CompanyFilters>) {
    startTransition(() => {
      setFilters(prev => ({ ...prev, ...patch }));
    });
  }

  function handleSectionClick(section: string | null) {
    setActiveSection(section);
    setActiveDivision(null);
  }

  // Semantic search
  useEffect(() => {
    if (!semanticQuery.trim()) { setSemanticResults(null); return; }
    if (searchTimer.current) clearTimeout(searchTimer.current);
    searchTimer.current = setTimeout(async () => {
      setSemanticLoading(true);
      try {
        const r = await semanticSearch(semanticQuery, 5);
        setSemanticResults(r);
      } catch { /* ignore */ }
      finally { setSemanticLoading(false); }
    }, 400);
  }, [semanticQuery]);

  function applySemanticResult(type: string, value: string) {
    const key = type === "clusters" ? "tfidf_cluster"
      : type === "keywords" ? "purpose_keywords"
      : type === "noga_codes" ? "noga_code"
      : "ai_category";
    patchFilters({ [key]: value, page: 1 });
    setSemanticQuery("");
    setSemanticResults(null);
  }

  async function runPurposeSearch(q: string) {
    if (!q.trim()) return;
    setSemanticResults(null);
    setPurposeHits(null);
    setPurposeLoading(true);
    try {
      const res = await fetchPurposeSemanticSearch(q, 50);
      setPurposeHits(res.results);
    } catch { /* ignore */ }
    finally { setPurposeLoading(false); }
  }

  function clearPurposeSearch() {
    setPurposeHits(null);
    setSemanticQuery("");
    setSemanticResults(null);
  }

  const total = page?.total ?? 0;
  const avgScore = page?.items
    ? Math.round(page.items.filter(c => c.combined_score != null).reduce((s, c) => s + (c.combined_score ?? 0), 0) / Math.max(1, page.items.filter(c => c.combined_score != null).length))
    : null;

  return (
    <div className="flex flex-col h-[calc(100vh-3rem)] overflow-hidden bg-white">
      {/* Setup gate */}
      <SetupGateBanner hasApiKey={hasApiKey} hasTargetDescription={hasTargetDescription} />

      {/* Header */}
      <div className="px-4 py-2 border-b border-slate-100 bg-white shrink-0">
        <div className="flex items-center gap-3">
          {/* Search */}
          <div className="relative flex-1 max-w-xl">
            {purposeLoading
              ? <Loader2 size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-blue-400 animate-spin" />
              : <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
            }
            <input
              type="text"
              value={semanticQuery}
              onChange={e => { setSemanticQuery(e.target.value); if (purposeHits) setPurposeHits(null); }}
              onKeyDown={e => { if (e.key === "Enter" && semanticQuery.trim()) runPurposeSearch(semanticQuery); }}
              placeholder="Describe what you're looking for… (Enter for company search)"
              className={cn(
                "w-full pl-8 pr-3 py-2 text-sm border rounded-xl focus:outline-none focus:ring-2 focus:ring-blue-300",
                purposeHits ? "border-blue-400 bg-blue-50" : "border-slate-200",
              )}
            />
            {(semanticQuery || purposeHits) && (
              <button onClick={clearPurposeSearch} className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-400 hover:text-slate-600">
                <X size={13} />
              </button>
            )}

            {/* Semantic dropdown */}
            {(semanticLoading || semanticResults) && semanticQuery && (
              <div className="absolute top-full left-0 right-0 mt-1 bg-white border border-slate-200 rounded-xl shadow-xl z-30 max-h-80 overflow-y-auto">
                {semanticLoading && <div className="px-4 py-3 text-sm text-slate-500">Searching…</div>}
                {semanticResults && (() => {
                  const sections: Array<{ label: string; type: string; items: SemanticSearchResult[] }> = [
                    { label: "Clusters", type: "clusters", items: semanticResults.clusters ?? [] },
                    { label: "Keywords", type: "keywords", items: semanticResults.keywords ?? [] },
                    { label: "NOGA", type: "noga_codes", items: semanticResults.noga_codes ?? [] },
                    { label: "Categories", type: "categories", items: semanticResults.categories ?? [] },
                  ].filter(s => s.items.length > 0);
                  return sections.map(sec => (
                    <div key={sec.type}>
                      <div className="px-3 py-1.5 text-[10px] font-semibold text-slate-400 uppercase tracking-wide bg-slate-50">{sec.label}</div>
                      {sec.items.slice(0, 5).map(item => (
                        <button
                          key={item.value}
                          onMouseDown={() => applySemanticResult(sec.type, item.value)}
                          className="w-full text-left px-3 py-2 text-sm hover:bg-blue-50 flex items-center justify-between"
                        >
                          <span className="truncate text-slate-700">{item.value}</span>
                          <span className="text-xs text-slate-400 ml-2 shrink-0">{item.count?.toLocaleString()}</span>
                        </button>
                      ))}
                    </div>
                  ));
                })()}
              </div>
            )}
          </div>

          {/* Stats strip */}
          <div className="hidden md:flex items-center gap-3 text-xs text-slate-500">
            <span><span className="font-semibold text-slate-700">{total.toLocaleString()}</span> companies</span>
            {avgScore != null && <span>avg score <span className="font-semibold text-slate-700">{avgScore}</span></span>}
          </div>

          {/* Wizard button */}
          {orgId && (
            <button
              onClick={() => setWizardOpen(true)}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium border border-slate-200 bg-white text-slate-600 hover:border-blue-300 hover:text-blue-600 transition-colors shrink-0"
            >
              <Wand2 size={12} /> Configure Scoring
            </button>
          )}
        </div>
      </div>

      {/* Market map */}
      <MarketMap
        activeSection={activeSection}
        onSectionClick={handleSectionClick}
        locale={locale}
      />

      {/* Sub-segment strip */}
      {activeSection && (
        <SubSegmentStrip
          section={activeSection}
          activeDivision={activeDivision}
          onDivisionClick={(div) => {
            setActiveDivision(div);
          }}
        />
      )}

      {/* Body: sidebar + company panel */}
      <div className="flex flex-1 min-h-0">
        <FilterSidebar
          filters={filters}
          cantons={cantons}
          onChange={patchFilters}
          collapsed={sidebarCollapsed}
          onToggle={() => setSidebarCollapsed(v => !v)}
        />

        {/* Company panel */}
        <div className="flex flex-col flex-1 min-w-0 overflow-hidden">
          {/* Panel header */}
          <div className="flex items-center justify-between px-4 py-2 border-b border-slate-100 bg-white shrink-0">
            <div className="flex items-center gap-3 text-xs text-slate-500">
              {purposeHits ? (
                <>
                  <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-blue-100 text-blue-700 font-medium">
                    <Search size={10} /> Semantic
                  </span>
                  <span><span className="font-semibold text-slate-700">{purposeHits.length}</span> matches for &ldquo;{semanticQuery}&rdquo;</span>
                  <button onClick={clearPurposeSearch} className="text-slate-400 hover:text-slate-600 underline">clear</button>
                </>
              ) : (
                <>
                  <span className="font-semibold text-slate-700">{total.toLocaleString()}</span>
                  <span>companies</span>
                  {activeSection && (
                    <span className="text-blue-600 font-medium">· Section {activeSection}{activeDivision ? ` / ${activeDivision}` : ""}</span>
                  )}
                </>
              )}
            </div>
            <div className="flex items-center gap-2">
              {!purposeHits && (
                <a
                  href={buildExportUrl(filters)}
                  target="_blank"
                  className="flex items-center gap-1.5 px-3 py-1 rounded-lg text-xs border border-slate-200 bg-white text-slate-600 hover:border-slate-300 transition-colors"
                >
                  <Download size={11} /> Export CSV
                </a>
              )}
            </div>
          </div>

          {/* Table */}
          <div className="flex-1 overflow-auto">
            {purposeHits ? (
              <CompanyTable
                companies={purposeHits.map(h => ({
                  id: h.company_id, uid: h.uid, name: h.name,
                  canton: h.canton, purpose: h.purpose,
                  combined_score: Math.round(h.similarity * 100),
                  legal_form: null, status: null, municipality: null,
                  address: null, website_url: null, website_checked_at: null,
                  google_search_results_raw: null, web_score: null,
                  social_media_only: null, website_status: null,
                  website_count: null, flex_score: null,
                  flex_score_breakdown: null, flex_scored_at: null,
                  ai_score: null, ai_scored_at: null, ai_category: null,
                  ai_freeform: null, noga_code: null, noga_label: null,
                  noga_level: null, noga_confidence: null,
                  noga_classified_at: null, noga_path: null,
                  noga_path_labels: null, review_status: null,
                  contact_status: null, contact_name: null,
                  contact_email: null, contact_phone: null,
                  tags: null, purpose_keywords: null, tfidf_cluster: null,
                  capital_nominal: null, capital_currency: null,
                  cantonal_excerpt_web: null, translations: null,
                  zefix_detail_web: null, address_city: null,
                  address_zip: null, old_names: null, head_offices: null,
                  further_head_offices: null, branch_offices: null,
                  has_taken_over: null, was_taken_over_by: null,
                  audit_companies: null, sogc_pub: null, sogc_date: null,
                  first_sogc_date: null, deletion_date: null,
                  ehraid: null, chid: null, lat: null, lon: null,
                  business_model: null, purpose_language: h.lang ?? null,
                  created_at: "", updated_at: "", notes: [],
                } as import("@/lib/types").Company))}
                isLoading={purposeLoading}
                filters={{}}
                onSort={() => {}}
                onSelect={setSelectedCompany}
                selectedId={selectedCompany?.id ?? null}
              />
            ) : (
              <CompanyTable
                companies={page?.items ?? []}
                isLoading={companiesLoading}
                filters={filters}
                onSort={(sort) => patchFilters({ sort, page: 1 })}
                onSelect={setSelectedCompany}
                selectedId={selectedCompany?.id ?? null}
              />
            )}
          </div>

          {/* Pagination — hidden in semantic mode (all results loaded at once) */}
          {!purposeHits && page && page.pages > 1 && (
            <div className="border-t border-slate-100 bg-white shrink-0 px-4 py-2">
              <Pagination
                page={page.page}
                pages={page.pages}
                total={page.total}
                pageSize={filters.page_size ?? 50}
                onChange={(p) => patchFilters({ page: p })}
                onPageSizeChange={(size) => patchFilters({ page_size: size, page: 1 })}
              />
            </div>
          )}
        </div>
      </div>

      {/* Company preview drawer */}
      {selectedCompany && (
        <CompanyPreview
          company={selectedCompany}
          onClose={() => setSelectedCompany(null)}
          onUpdated={(updated) => { setSelectedCompany(updated); mutateCompanies(); }}
        />
      )}

      {/* Scoring wizard modal */}
      {wizardOpen && orgId && (
        <ScoringWizard
          orgId={orgId}
          settings={effectiveSettings}
          onClose={() => setWizardOpen(false)}
          onSaved={() => { setWizardOpen(false); mutateSettings(); }}
        />
      )}
    </div>
  );
}

// ── ExplorerInner (reads URL params) ─────────────────────────────────────────

interface ExplorerClientProps {
  initialCantons: string[];
  initialStats: CompanyStats;
  initialTaxonomy?: Record<string, [string, number][]>;
  locale?: string;
}

function ExplorerInner({ initialCantons, initialStats, locale = "de" }: ExplorerClientProps) {
  return (
    <ExplorerPage
      initialCantons={initialCantons}
      initialStats={initialStats}
      locale={locale}
    />
  );
}

// ── Public export ─────────────────────────────────────────────────────────────

export default function ExplorerClient(props: ExplorerClientProps) {
  return (
    <Suspense fallback={<div className="flex items-center justify-center h-96 text-slate-400">Loading…</div>}>
      <ExplorerInner {...props} />
    </Suspense>
  );
}
