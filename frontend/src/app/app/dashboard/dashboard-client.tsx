"use client";
import { useState, useCallback, useTransition } from "react";
import { useRouter } from "next/navigation";
import useSWR from "swr";
import { Download, Loader2 } from "lucide-react";
import { FilterBar } from "@/components/dashboard/filter-bar";
import { CompanyTable } from "@/components/dashboard/company-table";
import { CompanyPreview } from "@/components/dashboard/company-preview";
import { Pagination } from "@/components/dashboard/pagination";
import { fetchCompanies, fetchStats, fetchCantons, fetchTaxonomy, fetchSavedViews, saveView, deleteView, enqueueCSVExport, fetchCurrentUser, fetchOrg, toggleViewAlert } from "@/lib/api";
import { getExportLimit } from "@/lib/entitlements";
import type { Company, CompanyFilters, CompanyStats } from "@/lib/types";

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

interface DashboardClientProps {
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
  router.replace(qs ? `/app/dashboard?${qs}` : "/app/dashboard", { scroll: false });
}

export function DashboardClient({ initialCantons, initialStats, initialFilters }: DashboardClientProps) {
  const router = useRouter();
  const [filters, setFiltersState] = useState<CompanyFilters>(initialFilters ?? DEFAULT_FILTERS);
  const [queueingExport, setQueueingExport] = useState(false);
  const [exportBanner, setExportBanner] = useState<{ kind: "success" | "error"; message: string } | null>(null);

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
  const { data: me } = useSWR("me", fetchCurrentUser);
  const { data: org } = useSWR(me?.org?.id ? ["org-detail", me.org.id] : null, () => fetchOrg(me!.org!.id));

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

  const exportLimit = org ? getExportLimit({ tier: org.tier, customFeatures: org.custom_features }) : 100;
  const matchingCount = page?.total ?? 0;
  const exportBlocked = matchingCount > exportLimit;

  // suppress unused-variable warnings for pre-existing vars used by parent components
  void stats;
  void activeStat;
  void handleStatFilter;

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
            <div className="flex items-center gap-2">
              <button
                onClick={handleQueueExport}
                disabled={queueingExport}
                title="Queue an unlimited background export — download from Jobs page when ready"
                className="flex items-center gap-1.5 text-xs text-slate-500 hover:text-slate-700 px-2.5 py-1 rounded border border-slate-200 hover:bg-white transition-colors disabled:opacity-50"
              >
                {queueingExport ? <Loader2 size={12} className="animate-spin" /> : <Download size={12} />}
                Queue full export
              </button>
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

        {/* Preview panel */}
        {selectedCompany && (
          <CompanyPreview
            company={selectedCompany}
            onClose={() => setSelectedCompany(null)}
            onUpdated={(updated) => {
              setSelectedCompany(updated);
              mutateCompanies();
            }}
          />
        )}
      </div>
    </div>
  );
}
