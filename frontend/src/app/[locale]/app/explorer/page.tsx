import { fetchCantons, fetchStats, fetchTaxonomy } from "@/lib/api";
import ExplorerClient from "./explorer-client";

export const dynamic = "force-dynamic";

export default async function ExplorerPage({
  params,
}: {
  params: Promise<{ locale: string }>;
}) {
  const { locale } = await params;
  const [cantons, stats, taxonomy] = await Promise.all([
    fetchCantons().catch(() => [] as string[]),
    fetchStats().catch(() => ({
      total: 0, searched: 0, with_website: 0, searches_today: 0,
      review: {}, contact: {},
    })),
    fetchTaxonomy().catch(() => ({} as Record<string, [string, number][]>)),
  ]);

  return <ExplorerClient initialCantons={cantons} initialStats={stats} initialTaxonomy={taxonomy} locale={locale} />;
}
