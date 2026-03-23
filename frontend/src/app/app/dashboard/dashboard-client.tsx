"use client";
import { useState, useCallback, useTransition } from "react";
import useSWR from "swr";
import { Download } from "lucide-react";
import { FilterSidebar } from "@/components/dashboard/filter-sidebar";
import { StatsBar } from "@/components/dashboard/stats-bar";
import { CompanyTable } from "@/components/dashboard/company-table";
import { CompanyPreview } from "@/components/dashboard/company-preview";
import { Pagination } from "@/components/dashboard/pagination";
import { fetchCompanies, fetchStats } from "@/lib/api";
import type { Company, CompanyFilters, CompanyStats } from "@/lib/types";

function buildExportUrl(filters: CompanyFilters): string {
  const params = new URLSearchParams();
  const { page: _p, page_size: _ps, ...rest } = filters;
  for (const [k, v] of Object.entries(rest)) {
    if (v !== undefined && v !== null && v !== "") params.set(k, String(v));
  }
  return `/api/v1/companies/export.csv?${params.toString()}`;
}

interface DashboardClientProps {
  cantons: string[];
  initialStats: CompanyStats;
}

const DEFAULT_FILTERS: CompanyFilters = { sort: "-updated", page: 1, page_size: 50 };

export function DashboardClient({ cantons, initialStats }: DashboardClientProps) {
  const [filters, setFilters] = useState<CompanyFilters>(DEFAULT_FILTERS);
  const [selectedCompany, setSelectedCompany] = useState<Company | null>(null);
  const [, startTransition] = useTransition();

  const { data: page, isLoading } = useSWR(
    ["companies", filters],
    () => fetchCompanies(filters),
    { keepPreviousData: true }
  );

  const { data: stats } = useSWR("stats", fetchStats, { fallbackData: initialStats });

  const handleFilterChange = useCallback((newFilters: CompanyFilters) => {
    startTransition(() => setFilters(newFilters));
  }, []);

  const handleClear = useCallback(() => {
    setFilters(DEFAULT_FILTERS);
  }, []);

  const handleSort = useCallback((sort: string) => {
    setFilters((f) => ({ ...f, sort, page: 1 }));
  }, []);

  const handleStatFilter = useCallback((key: string, value: string) => {
    if (!key) { setFilters(DEFAULT_FILTERS); return; }
    setFilters({ ...DEFAULT_FILTERS, [key]: value });
  }, []);

  return (
    <div className="flex flex-col h-[calc(100vh-3rem)] overflow-hidden">
      {/* Stats bar */}
      <StatsBar stats={stats ?? initialStats} onFilter={handleStatFilter} />

      {/* Three-panel layout */}
      <div className="flex flex-1 overflow-hidden">
        {/* Filter sidebar */}
        <FilterSidebar
          filters={filters}
          cantons={cantons}
          onChange={handleFilterChange}
          onClear={handleClear}
          resultCount={page?.total ?? 0}
        />

        {/* Table + pagination */}
        <div className="flex-1 flex flex-col overflow-hidden bg-white">
          <div className="flex items-center justify-end px-3 py-1 border-b border-slate-100 bg-slate-50">
            <a
              href={buildExportUrl(filters)}
              download
              className="flex items-center gap-1.5 text-xs text-slate-500 hover:text-slate-700 px-2.5 py-1 rounded border border-slate-200 hover:bg-white transition-colors"
            >
              <Download size={12} /> Export CSV ({page?.total ?? 0})
            </a>
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

        {/* Preview panel */}
        {selectedCompany && (
          <CompanyPreview
            company={selectedCompany}
            onClose={() => setSelectedCompany(null)}
          />
        )}
      </div>
    </div>
  );
}
