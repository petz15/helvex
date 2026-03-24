"use client";
import { useCallback } from "react";
import { X, ChevronDown } from "lucide-react";
import { cn } from "@/lib/utils";
import type { CompanyFilters } from "@/lib/types";
import { REVIEW_STATUSES, PROPOSAL_STATUSES } from "@/lib/types";

interface FilterSidebarProps {
  filters: CompanyFilters;
  cantons: string[];
  taxonomy?: Record<string, [string, number][]>;
  onChange: (filters: CompanyFilters) => void;
  onClear: () => void;
  resultCount: number;
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-1">
      <label className="text-xs font-medium text-slate-500 uppercase tracking-wide">{label}</label>
      {children}
    </div>
  );
}

const inputCls =
  "w-full rounded border border-slate-200 bg-white px-2 py-1.5 text-sm text-slate-800 focus:outline-none focus:ring-2 focus:ring-blue-400 focus:border-transparent";

const selectCls = cn(inputCls, "appearance-none pr-6 bg-[right_0.4rem_center] bg-no-repeat");

export function FilterSidebar({ filters, cantons, taxonomy, onChange, onClear, resultCount }: FilterSidebarProps) {
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

  const activeCount = Object.entries(filters).filter(
    ([k, v]) => !["page", "page_size", "sort"].includes(k) && v !== undefined && v !== ""
  ).length;

  const activeEntries = Object.entries(filters).filter(
    ([k, v]) => !["page", "page_size", "sort"].includes(k) && v !== undefined && v !== ""
  ) as [keyof CompanyFilters, string | number][];

  function fmtChip(key: keyof CompanyFilters, value: string | number): string {
    const labels: Partial<Record<keyof CompanyFilters, string>> = {
      q: "Search",
      canton: "Canton",
      review_status: "Review",
      proposal_status: "Proposal",
      google_searched: "Google",
      tags: "Tags",
      claude_category: "Claude",
      tfidf_cluster: "Cluster",
      purpose_keywords: "Keyword",
      min_google_score: "Min Google",
      min_claude_score: "Min Claude",
      min_zefix_score: "Min Zefix",
      exclude_review_status: "Exclude review",
      exclude_proposal_status: "Exclude proposal",
      exclude_canton: "Exclude canton",
      exclude_tags: "Exclude tags",
      sort: "Sort",
    };

    const prettyValue = String(value)
      .replace(/^_none$/, "none")
      .replace(/^_any$/, "any")
      .replace(/_/g, " ");

    return `${labels[key] ?? String(key).replace(/_/g, " ")}: ${prettyValue}`;
  }

  return (
    <aside className="w-64 shrink-0 flex flex-col bg-white border-r border-slate-200 overflow-y-auto">
      <div className="flex items-center justify-between px-4 py-3 border-b border-slate-100">
        <span className="text-sm font-semibold text-slate-700">
          Filters {activeCount > 0 && <span className="ml-1 text-xs bg-blue-100 text-blue-700 rounded-full px-1.5 py-0.5">{activeCount}</span>}
        </span>
        {activeCount > 0 && (
          <button onClick={onClear} className="text-xs text-slate-400 hover:text-slate-600 flex items-center gap-0.5">
            <X size={12} /> Clear
          </button>
        )}
      </div>
      <div className="flex-1 px-4 py-3 flex flex-col gap-4 text-sm">

        {activeEntries.length > 0 && (
          <div>
            <div className="flex flex-wrap gap-1.5">
              {activeEntries.map(([k, v]) => (
                <span
                  key={String(k)}
                  className="inline-flex items-center gap-1 rounded-full bg-slate-100 text-slate-700 border border-slate-200 px-2 py-0.5 text-xs"
                >
                  {fmtChip(k, v)}
                  <button
                    type="button"
                    onClick={() => unset(k)}
                    className="text-slate-400 hover:text-slate-700"
                    aria-label={`Remove ${String(k)}`}
                  >
                    <X size={12} />
                  </button>
                </span>
              ))}
            </div>
          </div>
        )}

        <Field label="Search">
          <input
            type="text"
            className={inputCls}
            placeholder="Company name…"
            value={filters.q ?? ""}
            onChange={(e) => set("q", e.target.value)}
          />
        </Field>

        <Field label={`Canton (${cantons.length} options)`}>
          <div className="relative">
            <select className={selectCls} value={filters.canton ?? ""} onChange={(e) => set("canton", e.target.value)}>
              <option value="">All cantons</option>
              {cantons.map((c) => <option key={c} value={c}>{c}</option>)}
            </select>
            <ChevronDown size={13} className="pointer-events-none absolute right-2 top-2 text-slate-400" />
          </div>
        </Field>

        <Field label={`Review status (${REVIEW_STATUSES.length + 2} options)`}>
          <div className="relative">
            <select className={selectCls} value={filters.review_status ?? ""} onChange={(e) => set("review_status", e.target.value)}>
              <option value="">All</option>
              <option value="_none">Pending (none set)</option>
              {REVIEW_STATUSES.map((s) => <option key={s.value} value={s.value}>{s.label}</option>)}
            </select>
            <ChevronDown size={13} className="pointer-events-none absolute right-2 top-2 text-slate-400" />
          </div>
        </Field>

        <Field label={`Proposal status (${PROPOSAL_STATUSES.length + 2} options)`}>
          <div className="relative">
            <select className={selectCls} value={filters.proposal_status ?? ""} onChange={(e) => set("proposal_status", e.target.value)}>
              <option value="">All</option>
              <option value="_none">Not sent (none set)</option>
              {PROPOSAL_STATUSES.map((s) => <option key={s.value} value={s.value}>{s.label}</option>)}
            </select>
            <ChevronDown size={13} className="pointer-events-none absolute right-2 top-2 text-slate-400" />
          </div>
        </Field>

        <Field label="Google search">
          <div className="relative">
            <select className={selectCls} value={filters.google_searched ?? ""} onChange={(e) => set("google_searched", e.target.value)}>
              <option value="">All</option>
              <option value="yes">Searched</option>
              <option value="no_result">No result</option>
              <option value="no">Not searched</option>
            </select>
            <ChevronDown size={13} className="pointer-events-none absolute right-2 top-2 text-slate-400" />
          </div>
        </Field>

        <Field label="Tags">
          <input type="text" className={inputCls} placeholder="e.g. saas, warm-lead…" value={filters.tags ?? ""}
            onChange={(e) => set("tags", e.target.value)} />
        </Field>

        <Field label="Claude category">
          <input type="text" className={inputCls} placeholder="e.g. SaaS or _none" value={filters.claude_category ?? ""}
            onChange={(e) => set("claude_category", e.target.value)} />
        </Field>

        <Field label={`TF-IDF cluster (${clusters.length} options)`}>
          <div className="relative">
            <select className={selectCls} value={filters.tfidf_cluster ?? ""} onChange={(e) => set("tfidf_cluster", e.target.value)}>
              <option value="">All</option>
              <option value="_none">None (unset)</option>
              <option value="_any">Any (set)</option>
              {clusters.map(([label]) => <option key={label} value={label}>{label}</option>)}
            </select>
            <ChevronDown size={13} className="pointer-events-none absolute right-2 top-2 text-slate-400" />
          </div>
        </Field>

        <Field label={`Purpose keyword (${keywords.length} options)`}>
          <div className="relative">
            <select className={selectCls} value={filters.purpose_keywords ?? ""} onChange={(e) => set("purpose_keywords", e.target.value)}>
              <option value="">All</option>
              <option value="_none">None (unset)</option>
              {keywords.map(([kw]) => <option key={kw} value={kw}>{kw}</option>)}
            </select>
            <ChevronDown size={13} className="pointer-events-none absolute right-2 top-2 text-slate-400" />
          </div>
        </Field>

        <Field label="Min Google score">
          <input type="number" className={inputCls} min={0} max={100} placeholder="0–100" value={filters.min_google_score ?? ""}
            onChange={(e) => set("min_google_score", e.target.value ? Number(e.target.value) : undefined)} />
        </Field>

        <Field label="Min Claude score">
          <input type="number" className={inputCls} min={0} max={100} placeholder="0–100" value={filters.min_claude_score ?? ""}
            onChange={(e) => set("min_claude_score", e.target.value ? Number(e.target.value) : undefined)} />
        </Field>

        <Field label="Min Zefix score">
          <input type="number" className={inputCls} min={0} max={100} placeholder="0–100" value={filters.min_zefix_score ?? ""}
            onChange={(e) => set("min_zefix_score", e.target.value ? Number(e.target.value) : undefined)} />
        </Field>

        <Field label="Exclude review">
          <div className="relative">
            <select className={selectCls} value={filters.exclude_review_status ?? ""} onChange={(e) => set("exclude_review_status", e.target.value)}>
              <option value="">— none —</option>
              {REVIEW_STATUSES.map((s) => <option key={s.value} value={s.value}>{s.label}</option>)}
            </select>
            <ChevronDown size={13} className="pointer-events-none absolute right-2 top-2 text-slate-400" />
          </div>
        </Field>

        <Field label="Exclude proposal">
          <div className="relative">
            <select className={selectCls} value={filters.exclude_proposal_status ?? ""} onChange={(e) => set("exclude_proposal_status", e.target.value)}>
              <option value="">— none —</option>
              {PROPOSAL_STATUSES.map((s) => <option key={s.value} value={s.value}>{s.label}</option>)}
            </select>
            <ChevronDown size={13} className="pointer-events-none absolute right-2 top-2 text-slate-400" />
          </div>
        </Field>

        <Field label="Exclude canton">
          <div className="relative">
            <select className={selectCls} value={filters.exclude_canton ?? ""} onChange={(e) => set("exclude_canton", e.target.value)}>
              <option value="">— none —</option>
              {cantons.map((c) => <option key={c} value={c}>{c}</option>)}
            </select>
            <ChevronDown size={13} className="pointer-events-none absolute right-2 top-2 text-slate-400" />
          </div>
        </Field>

        <Field label="Exclude tags">
          <input type="text" className={inputCls} placeholder="comma-separated" value={filters.exclude_tags ?? ""}
            onChange={(e) => set("exclude_tags", e.target.value)} />
        </Field>

      </div>
      <div className="px-4 py-3 border-t border-slate-100 text-xs text-slate-400">
        {resultCount.toLocaleString()} result{resultCount !== 1 ? "s" : ""}
      </div>
    </aside>
  );
}
