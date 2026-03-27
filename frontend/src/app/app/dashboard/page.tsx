import { fetchCantons, fetchStats } from "@/lib/api";
import { DashboardClient } from "./dashboard-client";
import type { CompanyFilters } from "@/lib/types";

export const dynamic = "force-dynamic";

export default async function DashboardPage({ searchParams }: { searchParams: Promise<Record<string, string>> }) {
  const sp = await searchParams;
  const [cantons, stats] = await Promise.all([
    fetchCantons().catch(() => [] as string[]),
    fetchStats().catch(() => ({
      total: 0, searched: 0, with_website: 0, searches_today: 0,
      review: {}, contact: {},
    })),
  ]);

  // Restore filters from URL query params (set by the client on every filter change)
  const numericKeys = new Set(["page", "page_size", "min_web_score", "max_web_score", "min_flex_score", "max_flex_score", "min_ai_score", "max_ai_score", "min_combined_score", "max_combined_score"]);
  const urlFilters: CompanyFilters = {};
  for (const [k, v] of Object.entries(sp)) {
    if (v) (urlFilters as Record<string, unknown>)[k] = numericKeys.has(k) ? Number(v) : v;
  }
  const initialFilters: CompanyFilters = Object.keys(urlFilters).length > 0
    ? { sort: "-updated", page: 1, page_size: 50, ...urlFilters }
    : { sort: "-updated", page: 1, page_size: 50 };

  return <DashboardClient initialCantons={cantons} initialStats={stats} initialFilters={initialFilters} />;
}
