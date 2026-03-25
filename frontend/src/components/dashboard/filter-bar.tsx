"use client";
import { useState, useCallback } from "react";
import { X, SlidersHorizontal, ChevronDown } from "lucide-react";
import { cn } from "@/lib/utils";
import { Combobox } from "./combobox";
import type { CompanyFilters } from "@/lib/types";
import { REVIEW_STATUSES, PROPOSAL_STATUSES } from "@/lib/types";

interface FilterBarProps {
  filters: CompanyFilters;
  cantons: string[];
  taxonomy?: Record<string, [string, number][]>;
  onChange: (filters: CompanyFilters) => void;
  onClear: () => void;
  resultCount: number;
}

const inputCls =
  "w-full rounded border border-slate-200 bg-white px-2 py-1.5 text-sm text-slate-800 focus:outline-none focus:ring-2 focus:ring-blue-400 focus:border-transparent";

const selectCls = cn(inputCls, "appearance-none pr-6");

function Label({ children }: { children: React.ReactNode }) {
  return <div className="text-xs font-medium text-slate-500 uppercase tracking-wide mb-1">{children}</div>;
}

const CHIP_LABELS: Partial<Record<keyof CompanyFilters, string>> = {
  q: "Search", canton: "Canton", review_status: "Review", proposal_status: "Proposal",
  google_searched: "Google", tags: "Tags", claude_category: "Claude",
  tfidf_cluster: "Cluster", purpose_keywords: "Keyword",
  min_google_score: "Min Google", min_claude_score: "Min Claude", min_zefix_score: "Min Zefix",
  exclude_review_status: "Excl. review", exclude_proposal_status: "Excl. proposal",
  exclude_canton: "Excl. canton", exclude_tags: "Excl. tags",
};

export function FilterBar({ filters, cantons, taxonomy, onChange, onClear, resultCount }: FilterBarProps) {
  const [expanded, setExpanded] = useState(false);

  const clusters = taxonomy?.clusters ?? [];
  const keywords = taxonomy?.keywords ?? [];

  const set = useCallback(
    (key: keyof CompanyFilters, value: string | number | undefined) =>
      onChange({ ...filters, [key]: value || undefined, page: 1 }),
    [filters, onChange]
  );
  const unset = useCallback(
    (key: keyof CompanyFilters) => onChange({ ...filters, [key]: undefined, page: 1 }),
    [filters, onChange]
  );

  const activeEntries = Object.entries(filters).filter(
    ([k, v]) => !["page", "page_size", "sort"].includes(k) && v !== undefined && v !== ""
  ) as [keyof CompanyFilters, string | number][];

  const activeCount = activeEntries.length;

  return (
    <div className="border-b border-slate-200 bg-slate-50 text-sm">
      {/* Toggle row */}
      <div className="flex items-center gap-2 px-3 py-2">
        <button
          type="button"
          onClick={() => setExpanded((e) => !e)}
          className={cn(
            "flex items-center gap-1.5 rounded px-2.5 py-1 text-sm font-medium transition-colors",
            expanded
              ? "bg-blue-600 text-white"
              : "bg-white border border-slate-200 text-slate-600 hover:bg-slate-100"
          )}
        >
          <SlidersHorizontal size={13} />
          Filters
          {activeCount > 0 && (
            <span className={cn(
              "rounded-full px-1.5 py-0.5 text-xs font-semibold",
              expanded ? "bg-blue-500 text-white" : "bg-blue-100 text-blue-700"
            )}>
              {activeCount}
            </span>
          )}
          <ChevronDown size={12} className={cn("transition-transform", expanded && "rotate-180")} />
        </button>

        {/* Active filter chips */}
        <div className="flex flex-wrap gap-1.5 flex-1 min-w-0">
          {activeEntries.map(([k, v]) => (
            <span
              key={String(k)}
              className="inline-flex items-center gap-1 rounded-full bg-white border border-slate-200 text-slate-700 px-2 py-0.5 text-xs"
            >
              <span className="text-slate-400">{CHIP_LABELS[k] ?? String(k).replace(/_/g, " ")}:</span>
              <span>{String(v).replace(/^_none$/, "none").replace(/^_any$/, "any")}</span>
              <button type="button" onClick={() => unset(k)} className="text-slate-400 hover:text-slate-700">
                <X size={11} />
              </button>
            </span>
          ))}
          {activeCount > 0 && !expanded && (
            <button onClick={onClear} className="text-xs text-slate-400 hover:text-slate-600 flex items-center gap-0.5">
              <X size={11} /> Clear all
            </button>
          )}
        </div>

        <span className="ml-auto shrink-0 text-xs text-slate-400">
          {resultCount.toLocaleString()} result{resultCount !== 1 ? "s" : ""}
        </span>
      </div>

      {/* Expanded filter grid */}
      {expanded && (
        <div className="px-3 pb-3 border-t border-slate-200 bg-white">
          <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-6 gap-x-4 gap-y-3 pt-3">

            {/* Search */}
            <div>
              <Label>Search</Label>
              <input type="text" className={inputCls} placeholder="Company name…"
                value={filters.q ?? ""} onChange={(e) => set("q", e.target.value)} />
            </div>

            {/* Canton */}
            <div>
              <Label>Canton</Label>
              <div className="relative">
                <select className={cn(selectCls, "bg-[right_0.4rem_center] bg-no-repeat")}
                  value={filters.canton ?? ""} onChange={(e) => set("canton", e.target.value)}>
                  <option value="">All cantons</option>
                  {cantons.map((c) => <option key={c} value={c}>{c}</option>)}
                </select>
                <ChevronDown size={13} className="pointer-events-none absolute right-2 top-2 text-slate-400" />
              </div>
            </div>

            {/* TF-IDF cluster — combobox */}
            <div>
              <Label>TF-IDF cluster</Label>
              <Combobox
                options={clusters}
                value={filters.tfidf_cluster === "_none" || filters.tfidf_cluster === "_any" ? undefined : filters.tfidf_cluster}
                onChange={(v) => set("tfidf_cluster", v)}
                placeholder={`Search ${clusters.length} clusters…`}
                extraOptions={[
                  { value: "_none", label: "None (unset)" },
                  { value: "_any", label: "Any (set)" },
                ]}
              />
            </div>

            {/* Purpose keyword — combobox */}
            <div>
              <Label>Purpose keyword (top 100)</Label>
              <Combobox
                options={keywords}
                value={filters.purpose_keywords === "_none" ? undefined : filters.purpose_keywords}
                onChange={(v) => set("purpose_keywords", v)}
                placeholder={`Search ${keywords.length} keywords…`}
                extraOptions={[{ value: "_none", label: "None (unset)" }]}
              />
            </div>

            {/* Review status */}
            <div>
              <Label>Review status</Label>
              <div className="relative">
                <select className={cn(selectCls, "bg-[right_0.4rem_center] bg-no-repeat")}
                  value={filters.review_status ?? ""} onChange={(e) => set("review_status", e.target.value)}>
                  <option value="">All</option>
                  <option value="_none">Pending (none set)</option>
                  {REVIEW_STATUSES.map((s) => <option key={s.value} value={s.value}>{s.label}</option>)}
                </select>
                <ChevronDown size={13} className="pointer-events-none absolute right-2 top-2 text-slate-400" />
              </div>
            </div>

            {/* Proposal status */}
            <div>
              <Label>Proposal status</Label>
              <div className="relative">
                <select className={cn(selectCls, "bg-[right_0.4rem_center] bg-no-repeat")}
                  value={filters.proposal_status ?? ""} onChange={(e) => set("proposal_status", e.target.value)}>
                  <option value="">All</option>
                  <option value="_none">Not sent (none set)</option>
                  {PROPOSAL_STATUSES.map((s) => <option key={s.value} value={s.value}>{s.label}</option>)}
                </select>
                <ChevronDown size={13} className="pointer-events-none absolute right-2 top-2 text-slate-400" />
              </div>
            </div>

            {/* Tags */}
            <div>
              <Label>Tags</Label>
              <input type="text" className={inputCls} placeholder="e.g. saas, warm-lead…"
                value={filters.tags ?? ""} onChange={(e) => set("tags", e.target.value)} />
            </div>

            {/* Claude category */}
            <div>
              <Label>Claude category</Label>
              <input type="text" className={inputCls} placeholder="e.g. SaaS or _none"
                value={filters.claude_category ?? ""} onChange={(e) => set("claude_category", e.target.value)} />
            </div>

            {/* Google search */}
            <div>
              <Label>Google search</Label>
              <div className="relative">
                <select className={cn(selectCls, "bg-[right_0.4rem_center] bg-no-repeat")}
                  value={filters.google_searched ?? ""} onChange={(e) => set("google_searched", e.target.value)}>
                  <option value="">All</option>
                  <option value="yes">Searched</option>
                  <option value="no_result">No result</option>
                  <option value="no">Not searched</option>
                </select>
                <ChevronDown size={13} className="pointer-events-none absolute right-2 top-2 text-slate-400" />
              </div>
            </div>

            {/* Min scores */}
            <div>
              <Label>Min Google score</Label>
              <input type="number" className={inputCls} min={0} max={100} placeholder="0–100"
                value={filters.min_google_score ?? ""}
                onChange={(e) => set("min_google_score", e.target.value ? Number(e.target.value) : undefined)} />
            </div>
            <div>
              <Label>Min Claude score</Label>
              <input type="number" className={inputCls} min={0} max={100} placeholder="0–100"
                value={filters.min_claude_score ?? ""}
                onChange={(e) => set("min_claude_score", e.target.value ? Number(e.target.value) : undefined)} />
            </div>
            <div>
              <Label>Min Zefix score</Label>
              <input type="number" className={inputCls} min={0} max={100} placeholder="0–100"
                value={filters.min_zefix_score ?? ""}
                onChange={(e) => set("min_zefix_score", e.target.value ? Number(e.target.value) : undefined)} />
            </div>

            {/* Exclude row */}
            <div>
              <Label>Excl. canton</Label>
              <div className="relative">
                <select className={cn(selectCls, "bg-[right_0.4rem_center] bg-no-repeat")}
                  value={filters.exclude_canton ?? ""} onChange={(e) => set("exclude_canton", e.target.value)}>
                  <option value="">— none —</option>
                  {cantons.map((c) => <option key={c} value={c}>{c}</option>)}
                </select>
                <ChevronDown size={13} className="pointer-events-none absolute right-2 top-2 text-slate-400" />
              </div>
            </div>
            <div>
              <Label>Excl. review</Label>
              <div className="relative">
                <select className={cn(selectCls, "bg-[right_0.4rem_center] bg-no-repeat")}
                  value={filters.exclude_review_status ?? ""} onChange={(e) => set("exclude_review_status", e.target.value)}>
                  <option value="">— none —</option>
                  {REVIEW_STATUSES.map((s) => <option key={s.value} value={s.value}>{s.label}</option>)}
                </select>
                <ChevronDown size={13} className="pointer-events-none absolute right-2 top-2 text-slate-400" />
              </div>
            </div>
            <div>
              <Label>Excl. proposal</Label>
              <div className="relative">
                <select className={cn(selectCls, "bg-[right_0.4rem_center] bg-no-repeat")}
                  value={filters.exclude_proposal_status ?? ""} onChange={(e) => set("exclude_proposal_status", e.target.value)}>
                  <option value="">— none —</option>
                  {PROPOSAL_STATUSES.map((s) => <option key={s.value} value={s.value}>{s.label}</option>)}
                </select>
                <ChevronDown size={13} className="pointer-events-none absolute right-2 top-2 text-slate-400" />
              </div>
            </div>
            <div>
              <Label>Excl. tags</Label>
              <input type="text" className={inputCls} placeholder="comma-separated"
                value={filters.exclude_tags ?? ""} onChange={(e) => set("exclude_tags", e.target.value)} />
            </div>

          </div>

          {/* Clear button inside expanded panel */}
          {activeCount > 0 && (
            <div className="mt-3 pt-2 border-t border-slate-100 flex justify-end">
              <button onClick={onClear} className="text-xs text-slate-500 hover:text-slate-700 flex items-center gap-1">
                <X size={12} /> Clear all filters
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
