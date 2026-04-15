"use client";
import { useState, useMemo } from "react";
import Link from "next/link";
import useSWR from "swr";
import { ChevronRight, ChevronDown, Download, ThumbsDown, Search } from "lucide-react";
import { formatClusterLabel } from "@/lib/utils";
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  Cell,
} from "recharts";
import { cn } from "@/lib/utils";
import { fetchTaxonomy, fetchNogaHierarchy, fetchCurrentUser, fetchOrgSettings, saveOrgWorkspaceSettings, type NogaNode } from "@/lib/api";

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

function Section({ title, subtitle, children, searchValue, onSearchChange }: {
  title: string;
  subtitle?: string;
  children: React.ReactNode;
  searchValue?: string;
  onSearchChange?: (v: string) => void;
}) {
  return (
    <section className="space-y-4">
      <div className="flex items-center justify-between gap-4 flex-wrap">
        <div>
          <h2 className="text-lg font-semibold text-slate-800">{title}</h2>
          {subtitle && <p className="text-sm text-slate-500 mt-0.5">{subtitle}</p>}
        </div>
        {onSearchChange !== undefined && (
          <div className="relative">
            <Search size={13} className="absolute left-2.5 top-2 text-slate-400 pointer-events-none" />
            <input
              type="text"
              value={searchValue ?? ""}
              onChange={(e) => onSearchChange(e.target.value)}
              placeholder="Filter…"
              className="pl-7 pr-3 py-1.5 text-sm border border-slate-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-300 bg-white w-48"
            />
          </div>
        )}
      </div>
      {children}
    </section>
  );
}

// ── Export helper ─────────────────────────────────────────────────────────────

function ExportButton({ filterKey, filterValue, label }: { filterKey: string; filterValue: string; label: string }) {
  return (
    <a
      href={`/api/v1/companies/export.csv?${filterKey}=${encodeURIComponent(filterValue)}`}
      className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-xs text-slate-400 hover:text-emerald-600 hover:bg-emerald-50 transition-colors"
      title={`Export ${label} as CSV`}
    >
      <Download size={11} />
    </a>
  );
}

// ── NOGA tree node ────────────────────────────────────────────────────────────

function NogaTreeNode({ node, depth = 0, filterQ }: { node: NogaNode; depth?: number; filterQ: string }) {
  const [expanded, setExpanded] = useState(depth === 0 && node.children.length > 0 && node.count > 200);
  const hasChildren = node.children.length > 0;

  const matchesFilter = !filterQ ||
    node.code.toLowerCase().includes(filterQ.toLowerCase()) ||
    node.label.toLowerCase().includes(filterQ.toLowerCase());

  const childrenMatchFilter = filterQ
    ? node.children.some((c) => nodeMatchesFilter(c, filterQ))
    : true;

  if (!matchesFilter && !childrenMatchFilter) return null;

  return (
    <div className={cn("", depth > 0 && "ml-5 border-l border-slate-100 pl-3")}>
      <div className="flex items-center gap-1.5 py-1 group">
        {hasChildren ? (
          <button
            type="button"
            onClick={() => setExpanded((e) => !e)}
            className="shrink-0 text-slate-400 hover:text-slate-700 transition-colors"
          >
            {expanded ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
          </button>
        ) : (
          <span className="w-[13px] shrink-0" />
        )}
        <Link
          href={`/app/search?noga_code=${encodeURIComponent(node.code)}`}
          className="text-sm text-slate-700 hover:text-blue-600 hover:underline transition-colors"
        >
          <span className="font-mono text-xs text-slate-400 mr-1.5">{node.code}</span>
          {node.label}
        </Link>
        <span className="ml-1 text-xs text-slate-400 tabular-nums">{node.count.toLocaleString()}</span>
        <ExportButton filterKey="noga_code" filterValue={node.code} label={node.label} />
      </div>
      {expanded && hasChildren && (
        <div>
          {node.children.map((child) => (
            <NogaTreeNode key={child.code} node={child} depth={depth + 1} filterQ={filterQ} />
          ))}
        </div>
      )}
    </div>
  );
}

function nodeMatchesFilter(node: NogaNode, q: string): boolean {
  if (node.code.toLowerCase().includes(q.toLowerCase())) return true;
  if (node.label.toLowerCase().includes(q.toLowerCase())) return true;
  return node.children.some((c) => nodeMatchesFilter(c, q));
}

// ── Main component ────────────────────────────────────────────────────────────

export function CategoriesClient() {
  const { data: taxonomy, isLoading, error } = useSWR("taxonomy", fetchTaxonomy);
  const { data: nogaTree = [], isLoading: nogaLoading } = useSWR("noga-hierarchy", fetchNogaHierarchy);
  const { data: me } = useSWR("me", fetchCurrentUser);
  const orgId = me?.org?.id;
  const isAdmin = me?.org_role === "admin" || me?.org_role === "owner" || me?.is_superadmin;
  const { data: orgSettings = {}, mutate: reloadOrgSettings } = useSWR(
    orgId ? ["org-settings", orgId] : null,
    () => fetchOrgSettings(orgId!),
  );

  const categories: [string, number][] = taxonomy?.categories ?? [];
  const keywords: [string, number][] = taxonomy?.keywords ?? [];
  const clusters: [string, number][] = taxonomy?.clusters ?? [];

  const [categorySearch, setCategorySearch] = useState("");
  const [keywordSearch, setKeywordSearch] = useState("");
  const [clusterSearch, setClusterSearch] = useState("");
  const [showAllClusters, setShowAllClusters] = useState(false);
  const [nogaSearch, setNogaSearch] = useState("");
  const [showAllCategories, setShowAllCategories] = useState(false);
  const [markingIrrelevant, setMarkingIrrelevant] = useState<string | null>(null);

  if (isLoading) return <div className="max-w-7xl mx-auto px-4 py-6 text-sm text-slate-400">Loading…</div>;
  if (error) return <div className="max-w-7xl mx-auto px-4 py-6 text-sm text-red-500">Failed to load taxonomy data.</div>;

  const maxCategoryCount = categories[0]?.[1] ?? 1;
  const maxKeywordCount = keywords[0]?.[1] ?? 1;

  const filteredCategories = categorySearch
    ? categories.filter(([name]) => name.toLowerCase().includes(categorySearch.toLowerCase()))
    : categories;
  const displayedCategories = showAllCategories ? filteredCategories : filteredCategories.slice(0, 30);
  const categoryData = displayedCategories.slice(0, 50).map(([name, count]) => ({ name, count }));

  const filteredKeywords = keywordSearch
    ? keywords.filter(([kw]) => kw.toLowerCase().includes(keywordSearch.toLowerCase()))
    : keywords;

  const filteredClusters = clusterSearch
    ? clusters.filter(([cl]) => cl.toLowerCase().includes(clusterSearch.toLowerCase()) || formatClusterLabel(cl).toLowerCase().includes(clusterSearch.toLowerCase()))
    : clusters;
  const displayedClusters = showAllClusters ? filteredClusters : filteredClusters.slice(0, 60);
  const maxClusterCount = clusters[0]?.[1] ?? 1;

  function kwSize(count: number): string {
    const ratio = count / maxKeywordCount;
    if (ratio >= 0.6) return "text-base font-semibold";
    if (ratio >= 0.3) return "text-sm font-medium";
    return "text-xs";
  }

  async function markCategoryIrrelevant(category: string) {
    if (!orgId || !isAdmin) return;
    setMarkingIrrelevant(category);
    try {
      const current = (orgSettings as Record<string, string>).claude_classify_categories ?? "";
      const lines = current.split("\n").map((l) => l.trim()).filter(Boolean);
      const excl = `!${category}`;
      if (!lines.includes(excl)) {
        lines.push(excl);
        await saveOrgWorkspaceSettings(orgId, { claude_classify_categories: lines.join("\n") });
        await reloadOrgSettings();
      }
    } finally {
      setMarkingIrrelevant(null);
    }
  }

  return (
    <div className="max-w-7xl mx-auto px-4 py-6 space-y-12">
      <div>
        <h1 className="text-2xl font-bold text-slate-900">Category Overview</h1>
        <p className="text-sm text-slate-500 mt-1">
          Browse keywords and classifications — click any item to open the dashboard filtered to that value.
        </p>
      </div>

      {/* ── AI Categories ──────────────────────────────────────────────── */}
      <Section
        title="AI Categories"
        subtitle={`${categories.length} distinct categories — assigned by Claude Haiku (claude_classify job). Each company receives a short human-readable label and a confidence score. Categories and the classification prompt are customisable per org.`}
        searchValue={categorySearch}
        onSearchChange={setCategorySearch}
      >
        {categories.length === 0 ? (
          <p className="text-sm text-slate-400">No categories found. Run an AI classification job first.</p>
        ) : (
          <div className="bg-white rounded-xl border border-slate-200 p-5 shadow-sm space-y-3">
            <ResponsiveContainer width="100%" height={Math.max(300, categoryData.length * 32)}>
              <BarChart
                data={categoryData}
                layout="vertical"
                margin={{ top: 0, right: 60, left: 0, bottom: 0 }}
                barCategoryGap="20%"
              >
                <XAxis type="number" tick={{ fontSize: 11, fill: "#94a3b8" }} axisLine={false} tickLine={false} domain={[0, maxCategoryCount]} />
                <YAxis type="category" dataKey="name" width={180} tick={{ fontSize: 12, fill: "#475569" }} axisLine={false} tickLine={false} />
                <Tooltip content={<CategoryTooltip />} cursor={{ fill: "#f1f5f9" }} />
                <Bar dataKey="count" radius={[0, 4, 4, 0]} onClick={(d: { name?: string }) => {
                  if (d.name) window.location.href = `/app/search?ai_category=${encodeURIComponent(d.name)}`;
                }} style={{ cursor: "pointer" }}>
                  {categoryData.map((_, i) => <Cell key={i} fill={barColor(i)} fillOpacity={0.85} />)}
                </Bar>
              </BarChart>
            </ResponsiveContainer>

            {/* Per-category actions row */}
            {orgId && isAdmin && (
              <div className="border-t border-slate-100 pt-3 space-y-1">
                <p className="text-xs text-slate-400 mb-2">Mark a category as irrelevant for your org (appends to classification exclusion list):</p>
                <div className="flex flex-wrap gap-2">
                  {filteredCategories.slice(0, 50).map(([name, count]) => (
                    <div key={name} className="inline-flex items-center gap-1 border border-slate-200 rounded-full px-2 py-0.5 text-xs bg-slate-50">
                      <Link href={`/app/search?ai_category=${encodeURIComponent(name)}`} className="text-slate-700 hover:text-blue-600 hover:underline">{name}</Link>
                      <span className="text-slate-400">{count}</span>
                      <ExportButton filterKey="ai_category" filterValue={name} label={name} />
                      <button
                        type="button"
                        title="Mark irrelevant for this org"
                        disabled={markingIrrelevant === name}
                        onClick={() => markCategoryIrrelevant(name)}
                        className="text-slate-300 hover:text-red-500 transition-colors disabled:opacity-50"
                      >
                        <ThumbsDown size={10} />
                      </button>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {filteredCategories.length > 30 && (
              <button
                type="button"
                onClick={() => setShowAllCategories((v) => !v)}
                className="text-xs text-blue-600 hover:underline"
              >
                {showAllCategories ? "Show less" : `Show all ${filteredCategories.length} categories`}
              </button>
            )}
          </div>
        )}
      </Section>

      {/* ── Purpose Keywords ───────────────────────────────────────────── */}
      <Section
        title="Purpose Keywords"
        subtitle={`Top ${keywords.length} TF-IDF keywords extracted from company purpose texts (recompute_keywords job). These are distilled, lemmatized terms that represent each company's core activity — used as input for semantic clustering and NOGA classification.`}
        searchValue={keywordSearch}
        onSearchChange={setKeywordSearch}
      >
        {keywords.length === 0 ? (
          <p className="text-sm text-slate-400">No keywords found. Run the pipeline job to extract them.</p>
        ) : (
          <div className="bg-white rounded-xl border border-slate-200 p-5 shadow-sm">
            <div className="flex flex-wrap gap-2">
              {filteredKeywords.map(([kw, count]) => (
                <div key={kw} className="inline-flex items-center gap-1">
                  <Link
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
                  <ExportButton filterKey="purpose_keywords" filterValue={kw} label={kw} />
                </div>
              ))}
            </div>
          </div>
        )}
      </Section>

      {/* ── Clusters ───────────────────────────────────────────────────── */}
      <Section
        title="Clusters"
        subtitle={`${clusters.length} active clusters — assigned by semantic_kmeans_cluster or tfidf_kmeans_cluster. Each cluster groups companies with related business activities. Click any cluster to filter the dashboard.`}
        searchValue={clusterSearch}
        onSearchChange={setClusterSearch}
      >
        {clusters.length === 0 ? (
          <p className="text-sm text-slate-400">No clusters found. Run a clustering job first.</p>
        ) : (
          <div className="bg-white rounded-xl border border-slate-200 p-5 shadow-sm space-y-3">
            <div className="flex flex-wrap gap-2">
              {displayedClusters.map(([raw, count]) => {
                const ratio = count / maxClusterCount;
                const sizeCls = ratio >= 0.5 ? "text-sm font-semibold" : ratio >= 0.25 ? "text-sm font-medium" : "text-xs";
                return (
                  <div key={raw} className="inline-flex items-center gap-1">
                    <Link
                      href={`/app/search?tfidf_cluster=${encodeURIComponent(raw)}`}
                      className={`inline-flex items-center gap-1 px-2.5 py-1 rounded-full border transition-colors bg-purple-50 text-purple-700 border-purple-100 hover:bg-purple-100 hover:border-purple-300 ${sizeCls}`}
                    >
                      {formatClusterLabel(raw)}
                      <span className="text-purple-400 text-xs tabular-nums">{count}</span>
                    </Link>
                    <ExportButton filterKey="tfidf_cluster" filterValue={raw} label={formatClusterLabel(raw)} />
                  </div>
                );
              })}
            </div>
            {filteredClusters.length > 60 && (
              <button
                type="button"
                onClick={() => setShowAllClusters((v) => !v)}
                className="text-xs text-blue-600 hover:underline"
              >
                {showAllClusters ? "Show less" : `Show all ${filteredClusters.length} clusters`}
              </button>
            )}
          </div>
        )}
      </Section>

      {/* ── NOGA Hierarchy ─────────────────────────────────────────────── */}
      <Section
        title="NOGA Classification"
        subtitle="Official Swiss industry taxonomy (reclassify_noga job). Each company is assigned a NOGA code via hybrid token-matching + sentence-transformer embeddings. Counts include all sub-levels. Click any code to filter."
        searchValue={nogaSearch}
        onSearchChange={setNogaSearch}
      >
        {nogaLoading ? (
          <p className="text-sm text-slate-400">Loading NOGA hierarchy…</p>
        ) : nogaTree.length === 0 ? (
          <p className="text-sm text-slate-400">No NOGA classifications found yet.</p>
        ) : (
          <div className="bg-white rounded-xl border border-slate-200 p-5 shadow-sm">
            {nogaTree.map((root) => (
              <NogaTreeNode key={root.code} node={root} depth={0} filterQ={nogaSearch} />
            ))}
          </div>
        )}
      </Section>
    </div>
  );
}
