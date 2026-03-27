"use client";
import Link from "next/link";
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  Cell,
} from "recharts";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

interface Props {
  taxonomy: Record<string, [string, number][]>;
}

// ── Colour helpers ────────────────────────────────────────────────────────────

const CATEGORY_COLORS = [
  "#3b82f6", "#8b5cf6", "#10b981", "#f59e0b",
  "#ef4444", "#06b6d4", "#f97316", "#84cc16",
  "#ec4899", "#6366f1",
];

function barColor(index: number) {
  return CATEGORY_COLORS[index % CATEGORY_COLORS.length];
}

// ── Custom tooltip for the bar chart ─────────────────────────────────────────

function CategoryTooltip({ active, payload }: { active?: boolean; payload?: { value: number; payload: { name: string } }[] }) {
  if (!active || !payload?.length) return null;
  return (
    <div className="bg-white border border-slate-200 rounded-lg px-3 py-2 shadow text-sm">
      <p className="font-medium text-slate-800">{payload[0].payload.name}</p>
      <p className="text-slate-500">{payload[0].value.toLocaleString()} companies</p>
    </div>
  );
}

// ── Section wrapper ───────────────────────────────────────────────────────────

function Section({ title, subtitle, children }: { title: string; subtitle?: string; children: React.ReactNode }) {
  return (
    <section className="space-y-4">
      <div>
        <h2 className="text-lg font-semibold text-slate-800">{title}</h2>
        {subtitle && <p className="text-sm text-slate-500 mt-0.5">{subtitle}</p>}
      </div>
      {children}
    </section>
  );
}

// ── Flex Cluster card ─────────────────────────────────────────────────────────

function ClusterCard({ label, count, maxCount }: { label: string; count: number; maxCount: number }) {
  const terms = label.split(",").map(t => t.trim()).filter(Boolean);
  const pct = Math.round((count / maxCount) * 100);
  return (
    <Link
      href={`/app/search?tfidf_cluster=${encodeURIComponent(label)}`}
      className="group block rounded-xl border border-slate-200 bg-white p-4 shadow-sm hover:border-purple-300 hover:shadow-md transition-all"
    >
      <div className="flex items-start justify-between gap-2 mb-3">
        <div className="flex flex-wrap gap-1">
          {terms.slice(0, 6).map(t => (
            <Badge key={t} className="bg-purple-50 text-purple-700 text-xs group-hover:bg-purple-100">
              {t}
            </Badge>
          ))}
          {terms.length > 6 && (
            <Badge className="bg-slate-100 text-slate-500 text-xs">+{terms.length - 6}</Badge>
          )}
        </div>
        <span className="shrink-0 text-sm font-semibold text-slate-700 tabular-nums">
          {count.toLocaleString()}
        </span>
      </div>
      {/* Proportion bar */}
      <div className="h-1 bg-slate-100 rounded-full overflow-hidden">
        <div
          className="h-full bg-purple-400 rounded-full transition-all group-hover:bg-purple-500"
          style={{ width: `${pct}%` }}
        />
      </div>
    </Link>
  );
}

// ── Main component ────────────────────────────────────────────────────────────

export function CategoriesClient({ taxonomy }: Props) {
  const clusters: [string, number][] = taxonomy.clusters ?? [];
  const categories: [string, number][] = taxonomy.categories ?? [];
  const keywords: [string, number][] = taxonomy.keywords ?? [];

  const maxClusterCount = clusters[0]?.[1] ?? 1;
  const maxCategoryCount = categories[0]?.[1] ?? 1;
  const maxKeywordCount = keywords[0]?.[1] ?? 1;

  // Prepare recharts data
  const categoryData = categories.slice(0, 30).map(([name, count]) => ({ name, count }));

  // Keyword font-size tiers based on relative frequency
  function kwSize(count: number): string {
    const ratio = count / maxKeywordCount;
    if (ratio >= 0.6) return "text-base font-semibold";
    if (ratio >= 0.3) return "text-sm font-medium";
    return "text-xs";
  }

  return (
    <div className="max-w-7xl mx-auto px-4 py-6 space-y-12">
      <div>
        <h1 className="text-2xl font-bold text-slate-900">Category Overview</h1>
        <p className="text-sm text-slate-500 mt-1">
          Browse clusters, keywords, and classifications — click any item to open the dashboard filtered to that value.
        </p>
      </div>

      {/* ── Flex Clusters ──────────────────────────────────────────────── */}
      <Section
        title="Flex Clusters"
        subtitle={`${clusters.length} clusters from TF-IDF analysis — each cluster groups companies with similar purpose language`}
      >
        {clusters.length === 0 ? (
          <p className="text-sm text-slate-400">No clusters found. Run the pipeline job to generate them.</p>
        ) : (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-3">
            {clusters.map(([label, count]) => (
              <ClusterCard key={label} label={label} count={count} maxCount={maxClusterCount} />
            ))}
          </div>
        )}
      </Section>

      {/* ── AI Categories ──────────────────────────────────────────────── */}
      <Section
        title="AI Categories"
        subtitle={`${categories.length} distinct categories assigned by Claude — top ${Math.min(categories.length, 30)} shown`}
      >
        {categories.length === 0 ? (
          <p className="text-sm text-slate-400">No categories found. Run an AI classification job first.</p>
        ) : (
          <div className="bg-white rounded-xl border border-slate-200 p-5 shadow-sm">
            <ResponsiveContainer width="100%" height={Math.max(300, categoryData.length * 32)}>
              <BarChart
                data={categoryData}
                layout="vertical"
                margin={{ top: 0, right: 60, left: 0, bottom: 0 }}
                barCategoryGap="20%"
              >
                <XAxis
                  type="number"
                  tick={{ fontSize: 11, fill: "#94a3b8" }}
                  axisLine={false}
                  tickLine={false}
                  domain={[0, maxCategoryCount]}
                />
                <YAxis
                  type="category"
                  dataKey="name"
                  width={180}
                  tick={{ fontSize: 12, fill: "#475569" }}
                  axisLine={false}
                  tickLine={false}
                />
                <Tooltip content={<CategoryTooltip />} cursor={{ fill: "#f1f5f9" }} />
                <Bar dataKey="count" radius={[0, 4, 4, 0]} onClick={(d: { name?: string }) => {
                  if (d.name) window.location.href = `/app/search?ai_category=${encodeURIComponent(d.name)}`;
                }} style={{ cursor: "pointer" }}>
                  {categoryData.map((_, i) => (
                    <Cell key={i} fill={barColor(i)} fillOpacity={0.85} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
        )}
      </Section>

      {/* ── Purpose Keywords ───────────────────────────────────────────── */}
      <Section
        title="Purpose Keywords"
        subtitle={`Top ${keywords.length} TF-IDF keywords extracted from company purpose texts`}
      >
        {keywords.length === 0 ? (
          <p className="text-sm text-slate-400">No keywords found. Run the pipeline job to extract them.</p>
        ) : (
          <div className="bg-white rounded-xl border border-slate-200 p-5 shadow-sm">
            <div className="flex flex-wrap gap-2">
              {keywords.map(([kw, count]) => (
                <Link
                  key={kw}
                  href={`/app/search?purpose_keywords=${encodeURIComponent(kw)}`}
                  className={cn(
                    "inline-flex items-center gap-1 px-2.5 py-1 rounded-full border transition-colors",
                    "bg-blue-50 text-blue-700 border-blue-100 hover:bg-blue-100 hover:border-blue-300",
                    kwSize(count),
                  )}
                >
                  {kw}
                  <span className="text-blue-400 text-xs tabular-nums">{count}</span>
                </Link>
              ))}
            </div>
          </div>
        )}
      </Section>
    </div>
  );
}
