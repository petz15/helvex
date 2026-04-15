"use client";
import { useState, useCallback, useTransition } from "react";
import { useRouter } from "next/navigation";
import useSWR from "swr";
import { Download, Loader2, BrainCircuit, X, Star } from "lucide-react";
import { FilterBar } from "@/components/dashboard/filter-bar";
import { CompanyTable } from "@/components/dashboard/company-table";
import { CompanyPreview } from "@/components/dashboard/company-preview";
import { Pagination } from "@/components/dashboard/pagination";
import { BaloghAdCard } from "@/components/balogh-ad-card";
import { fetchCompanies, fetchStats, fetchCantons, fetchTaxonomy, fetchSavedViews, saveView, deleteView, enqueueCSVExport, fetchClaudePreview, toggleViewAlert } from "@/lib/api";
import type { ClaudePreviewOut } from "@/lib/api";
import type { Company, CompanyFilters, CompanyStats } from "@/lib/types";

interface SearchClientProps {
  initialCantons: string[];
  initialStats: CompanyStats;
  initialFilters?: CompanyFilters;
}

const DEFAULT_FILTERS: CompanyFilters = { sort: "-updated", page: 1, page_size: 50 };

function syncFiltersToUrl(filters: CompanyFilters, router: ReturnType<typeof useRouter>) {
  const params = new URLSearchParams();
  for (const [k, v] of Object.entries(filters)) {
    const isDefault = (k === "sort" && v === "-updated") || (k === "page" && v === 1) || (k === "page_size" && v === 50);
    if (v !== undefined && v !== null && v !== "" && !isDefault) params.set(k, String(v));
  }
  const qs = params.toString();
  router.replace(qs ? `/app/search?${qs}` : "/app/search", { scroll: false });
}

export function SearchClient({ initialCantons, initialStats, initialFilters }: SearchClientProps) {
  const router = useRouter();
  const [filters, setFiltersState] = useState<CompanyFilters>(initialFilters ?? DEFAULT_FILTERS);
  const [queueingExport, setQueueingExport] = useState(false);
  const [exportBanner, setExportBanner] = useState<{ kind: "success" | "error"; message: string } | null>(null);
  const [previewOpen, setPreviewOpen] = useState(false);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewResult, setPreviewResult] = useState<ClaudePreviewOut | null>(null);
  const [previewError, setPreviewError] = useState<string | null>(null);

  const setFilters = useCallback((update: CompanyFilters | ((f: CompanyFilters) => CompanyFilters)) => {
    setFiltersState(prev => {
      const next = typeof update === "function" ? update(prev) : update;
      syncFiltersToUrl(next, router);
      return next;
    });
  }, [router]);
  const [selectedCompany, setSelectedCompany] = useState<Company | null>(null);
  const [, startTransition] = useTransition();

  const { data: page, isLoading, mutate: mutateCompanies } = useSWR(
    ["companies", filters],
    () => fetchCompanies(filters),
    { keepPreviousData: true }
  );

  const { data: stats } = useSWR("stats", fetchStats, { fallbackData: initialStats });
  const { data: cantons = initialCantons } = useSWR("cantons", fetchCantons, { fallbackData: initialCantons });
  const { data: taxonomy = {} } = useSWR("taxonomy", fetchTaxonomy);
  const { data: savedViews = [], mutate: mutateSavedViews } = useSWR("saved-views", fetchSavedViews);

  async function handleQueueExport() {
    setQueueingExport(true);
    setExportBanner(null);
    try {
      await enqueueCSVExport(filters);
      setExportBanner({ kind: "success", message: "Export queued — check Jobs page to download when ready." });
    } catch (err) {
      setExportBanner({ kind: "error", message: err instanceof Error ? err.message : "Failed to queue export" });
    } finally {
      setQueueingExport(false);
      setTimeout(() => setExportBanner(null), 6000);
    }
  }

  async function handleAIPreview() {
    setPreviewOpen(true);
    setPreviewLoading(true);
    setPreviewError(null);
    setPreviewResult(null);
    try {
      const result = await fetchClaudePreview({
        canton: filters.canton ?? null,
        min_zefix_score: filters.min_flex_score ?? null,
        max_zefix_score: filters.max_flex_score ?? null,
        purpose_keywords: filters.purpose_keywords ?? null,
      });
      setPreviewResult(result);
    } catch (err) {
      setPreviewError(err instanceof Error ? err.message : "Preview failed");
    } finally {
      setPreviewLoading(false);
    }
  }

  const handleFilterChange = useCallback((newFilters: CompanyFilters) => {
    startTransition(() => setFilters(newFilters));
  }, [setFilters]);

  const handleClear = useCallback(() => {
    setFilters(DEFAULT_FILTERS);
  }, [setFilters]);

  const handleSort = useCallback((sort: string) => {
    setFilters((f) => ({ ...f, sort, page: 1 }));
  }, [setFilters]);

  const handleStatFilter = useCallback((key: string, value: string) => {
    if (!key) { setFilters(DEFAULT_FILTERS); return; }
    setFilters({ ...DEFAULT_FILTERS, [key]: value });
  }, [setFilters]);

  const handleSaveView = useCallback(async (name: string) => {
    await saveView(name, filters);
    mutateSavedViews();
  }, [filters, mutateSavedViews]);

  const handleDeleteView = useCallback(async (id: number) => {
    await deleteView(id);
    mutateSavedViews();
  }, [mutateSavedViews]);

  const handleToggleAlert = useCallback(async (id: number, enabled: boolean) => {
    await toggleViewAlert(id, enabled);
    mutateSavedViews();
  }, [mutateSavedViews]);

  const handleLoadView = useCallback((viewFilters: CompanyFilters) => {
    setFilters({ ...DEFAULT_FILTERS, ...viewFilters });
  }, [setFilters]);

  const activeStat = (() => {
    if (filters.review_status) return { key: "review_status", value: String(filters.review_status) };
    if (filters.contact_status) return { key: "contact_status", value: String(filters.contact_status) };
    if (filters.google_searched) return { key: "google_searched", value: String(filters.google_searched) };
    return { key: "", value: "" };
  })();

  return (
    <div className="flex flex-col h-[calc(100vh-3rem)] overflow-hidden">

      {/* Filter bar (top, collapsible) */}
      <FilterBar
        filters={filters}
        cantons={cantons}
        taxonomy={taxonomy}
        onChange={handleFilterChange}
        onClear={handleClear}
        resultCount={page?.total ?? 0}
        savedViews={savedViews}
        onSaveView={handleSaveView}
        onLoadView={handleLoadView}
        onDeleteView={handleDeleteView}
        onToggleAlert={handleToggleAlert}
      />

      {/* Ad card */}
      <div className="px-4 py-2 bg-slate-50 border-b border-slate-100 shrink-0">
        <BaloghAdCard className="max-w-xs" />
      </div>

      {/* Table + preview (horizontal split) */}
      <div className="flex flex-1 overflow-hidden">
        {/* Table + pagination */}
        <div className="flex-1 flex flex-col overflow-hidden bg-white">
          <div className="flex items-center justify-between px-3 py-1 border-b border-slate-100 bg-slate-50">
            {exportBanner ? (
              <span className={`text-xs px-2 py-0.5 rounded ${
                exportBanner.kind === "success" ? "text-green-700 bg-green-50" : "text-red-700 bg-red-50"
              }`}>
                {exportBanner.message}
              </span>
            ) : <span />}
            <div className="flex items-center gap-1.5">
              <button
                onClick={handleAIPreview}
                disabled={previewLoading}
                title="Run AI scoring on up to 5 companies matching current filters (3 previews/day)"
                className="flex items-center gap-1.5 text-xs text-violet-600 hover:text-violet-700 px-2.5 py-1 rounded border border-violet-200 hover:bg-violet-50 transition-colors disabled:opacity-50"
              >
                {previewLoading ? <Loader2 size={12} className="animate-spin" /> : <BrainCircuit size={12} />}
                AI preview
              </button>
              <button
                onClick={handleQueueExport}
                disabled={queueingExport}
                title="Queue an unlimited background export — download from Jobs page when ready"
                className="flex items-center gap-1.5 text-xs text-slate-500 hover:text-slate-700 px-2.5 py-1 rounded border border-slate-200 hover:bg-white transition-colors disabled:opacity-50"
              >
                {queueingExport ? <Loader2 size={12} className="animate-spin" /> : <Download size={12} />}
                Queue full export
              </button>
            </div>
          </div>
          <CompanyTable
            companies={page?.items ?? []}
            selectedId={selectedCompany?.id ?? null}
            onSelect={setSelectedCompany}
            filters={filters}
            onSort={handleSort}
            isLoading={isLoading}
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

        {/* Preview panel — full-screen overlay on mobile, inline panel on desktop */}
        {selectedCompany && (
          <>
            {/* Backdrop (mobile only) */}
            <div
              className="fixed inset-0 top-12 z-40 bg-black/30 md:hidden"
              onClick={() => setSelectedCompany(null)}
            />
            <div className="fixed inset-0 top-12 z-50 overflow-hidden md:static md:inset-auto md:z-auto md:w-80 md:shrink-0 md:overflow-hidden">
              <CompanyPreview
                company={selectedCompany}
                onClose={() => setSelectedCompany(null)}
                onUpdated={(updated) => {
                  setSelectedCompany(updated);
                  mutateCompanies();
                }}
                className="w-full h-full"
              />
            </div>
          </>
        )}
      </div>

      {/* AI Preview modal */}
      {previewOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
          <div className="bg-white rounded-xl shadow-xl w-full max-w-lg overflow-hidden">
            <div className="flex items-center justify-between px-4 py-3 border-b border-slate-100">
              <p className="text-sm font-semibold text-slate-800 flex items-center gap-1.5">
                <BrainCircuit size={14} className="text-violet-500" /> AI Preview
              </p>
              <button onClick={() => setPreviewOpen(false)} className="text-slate-400 hover:text-slate-600">
                <X size={16} />
              </button>
            </div>

            <div className="p-4">
              {previewLoading && (
                <div className="flex items-center gap-2 text-sm text-slate-500 py-6 justify-center">
                  <Loader2 size={16} className="animate-spin" /> Scoring up to 5 companies…
                </div>
              )}
              {previewError && (
                <p className="text-sm text-red-600 py-4 text-center">{previewError}</p>
              )}
              {previewResult && (
                <>
                  <p className="text-xs text-slate-400 mb-3">
                    {previewResult.results.length} companies scored · {previewResult.previews_used}/{previewResult.previews_used + previewResult.previews_remaining} previews used today
                  </p>
                  <div className="space-y-2">
                    {previewResult.results.map(r => (
                      <div key={r.company_id} className="rounded-lg border border-slate-100 bg-slate-50 px-3 py-2.5">
                        <div className="flex items-center justify-between gap-2">
                          <p className="text-sm font-medium text-slate-800 truncate">{r.name}</p>
                          {r.ai_score != null && (
                            <span className={`shrink-0 flex items-center gap-1 text-xs font-semibold px-2 py-0.5 rounded-full ${
                              r.ai_score >= 70 ? "bg-emerald-100 text-emerald-700" :
                              r.ai_score >= 40 ? "bg-amber-100 text-amber-700" :
                              "bg-slate-100 text-slate-500"
                            }`}>
                              <Star size={10} /> {r.ai_score}
                            </span>
                          )}
                        </div>
                        {r.ai_category && <p className="text-xs text-slate-500 mt-0.5">{r.ai_category}</p>}
                        {r.ai_freeform && <p className="text-xs text-slate-400 mt-1 line-clamp-2">{r.ai_freeform}</p>}
                      </div>
                    ))}
                  </div>
                  {previewResult.previews_remaining === 0 && (
                    <p className="text-xs text-amber-600 mt-3 text-center">Daily preview limit reached. Resets tomorrow.</p>
                  )}
                </>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
