"use client";
import { useRouter, useSearchParams, usePathname } from "next/navigation";
import { useCallback } from "react";
import { cn } from "@/lib/utils";
import { SearchClient } from "@/app/[locale]/app/search/search-client";
import { MapClient } from "@/app/[locale]/app/map/map-client";
import type { CompanyFilters, CompanyStats } from "@/lib/types";
import dynamic from "next/dynamic";

const NogaClient = dynamic(() => import("@/app/[locale]/app/noga/noga-client"), { ssr: false });

type View = "list" | "noga" | "map";

const VIEWS: { id: View; label: string }[] = [
  { id: "list", label: "List" },
  { id: "noga", label: "NOGA tree" },
  { id: "map", label: "Map" },
];

interface CompaniesClientProps {
  initialCantons: string[];
  initialStats: CompanyStats;
  initialFilters: CompanyFilters;
  initialView: View;
  locale: string;
}

export function CompaniesClient({ initialCantons, initialStats, initialFilters, initialView, locale }: CompaniesClientProps) {
  const router = useRouter();
  const searchParams = useSearchParams();
  const pathname = usePathname();

  const view: View = ((searchParams?.get("view")) as View) || initialView;

  const switchView = useCallback((v: View) => {
    const params = new URLSearchParams();
    params.set("view", v);
    router.push(`${pathname}?${params.toString()}`, { scroll: false });
  }, [router, pathname]);

  return (
    <div className="flex flex-col h-[calc(100vh-5rem)] overflow-hidden">
      {/* View tab strip */}
      <div className="flex items-center gap-1 px-4 py-2 border-b border-slate-200 bg-white shrink-0">
        {VIEWS.map(({ id, label }) => (
          <button
            key={id}
            onClick={() => switchView(id)}
            className={cn(
              "px-3 py-1.5 rounded-md text-sm font-medium transition-colors",
              view === id
                ? "bg-blue-600 text-white"
                : "text-slate-600 hover:bg-slate-100 hover:text-slate-900"
            )}
          >
            {label}
          </button>
        ))}
      </div>

      {/* View content */}
      <div className="flex-1 min-h-0 overflow-hidden">
        {view === "list" && (
          <SearchClient
            initialCantons={initialCantons}
            initialStats={initialStats}
            initialFilters={initialFilters}
            basePath="/app/companies?view=list"
            className="flex flex-col h-full overflow-hidden"
          />
        )}
        {view === "noga" && <NogaClient locale={locale} />}
        {view === "map" && <MapClient />}
      </div>
    </div>
  );
}
