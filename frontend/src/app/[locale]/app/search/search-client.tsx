"use client";
import { useState, useCallback, useTransition } from "react";
import { useRouter } from "next/navigation";
import useSWR from "swr";
import { useI18n } from "@/i18n/context";
import { Download, Loader2 } from "lucide-react";
import { FilterBar } from "@/components/dashboard/filter-bar";
import { CompanyTable } from "@/components/dashboard/company-table";
import { CompanyPreview } from "@/components/dashboard/company-preview";
import { Pagination } from "@/components/dashboard/pagination";
import { BaloghAdCard } from "@/components/balogh-ad-card";
import { fetchCompanies, fetchStats, fetchCantons, fetchTaxonomy, fetchSavedViews, saveView, deleteView, enqueueCSVExport, toggleViewAlert } from "@/lib/api";
import type { Company, CompanyFilters, CompanyStats } from "@/lib/types";

interface SearchClientProps {
  initialCantons: string[];
  initialStats: CompanyStats;
  initialFilters?: CompanyFilters;
  basePath?: string;
  className?: string;
}

const DEFAULT_FILTERS: CompanyFilters = { sort: "-updated", page: 1, page_size: 50 };

function syncFiltersToUrl(filters: CompanyFilters, router: ReturnType<typeof useRouter>, basePath: string) {
  const params = new URLSearchParams();
  for (const [k, v] of Object.entries(filters)) {
    const isDefault = (k === "sort" && v === "-updated") || (k === "page" && v === 1) || (k === "page_size" && v === 50);
    if (v !== undefined && v !== null && v !== "" && !isDefault) params.set(k, String(v));
  }
  const qs = params.toString();
  const sep = basePath.includes("?") ? "&" : "?";
  router.replace(qs ? `${basePath}${sep}${qs}` : basePath, { scroll: false });
}

export function SearchClient({ initialCantons, initialStats, initialFilters, basePath = "/app/search", className }: SearchClientProps) {
  const router = useRouter();
  const { dict } = useI18n();
  const t = dict.app.search;
  const [filters, setFiltersState] = useState<CompanyFilters>(initialFilters ?? DEFAULT_FILTERS);
  const [queueingExport, setQueueingExport] = useState(false);
  const [exportBanner, setExportBanner] = useState<{ kind: "success" | "error"; message: string } | null>(null);

  const setFilters = useCallback((update: CompanyFilters | ((f: CompanyFilters) => CompanyFilters)) => {
    setFiltersState(prev => {
      const next = typeof update === "function" ? update(prev) : update;
      syncFiltersToUrl(next, router, basePath);
      return next;
    });
  }, [router, basePath]);
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
      setExportBanner({ kind: "success", message: t.exportQueued });
    } catch (err) {
      setExportBanner({ kind: "error", message: err instanceof Error ? err.message : "Failed to queue export" });
    } finally {
      setQueueingExport(false);
      setTimeout(() => setExportBanner(null), 6000);
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
    <div className={className ?? "flex flex-col h-[calc(100vh-3rem)] overflow-hidden"}>

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
                onClick={handleQueueExport}
                disabled={queueingExport}
                title="Queue an unlimited background export — download from Jobs page when ready"
                className="flex items-center gap-1.5 text-xs text-slate-500 hover:text-slate-700 px-2.5 py-1 rounded border border-slate-200 hover:bg-white transition-colors disabled:opacity-50"
              >
                {queueingExport ? <Loader2 size={12} className="animate-spin" /> : <Download size={12} />}
                {t.queueExport}
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

    </div>
  );
}
