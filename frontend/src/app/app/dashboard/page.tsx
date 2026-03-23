import { fetchCantons, fetchStats } from "@/lib/api";
import { DashboardClient } from "./dashboard-client";

export const dynamic = "force-dynamic";

export default async function DashboardPage() {
  const [cantons, stats] = await Promise.all([
    fetchCantons().catch(() => [] as string[]),
    fetchStats().catch(() => ({
      total: 0, searched: 0, with_website: 0, searches_today: 0,
      review: {}, proposal: {},
    })),
  ]);

  return <DashboardClient cantons={cantons} initialStats={stats} />;
}
