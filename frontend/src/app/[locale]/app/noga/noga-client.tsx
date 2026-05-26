"use client";
import { useState, useMemo, useCallback } from "react";
import { useRouter } from "next/navigation";
import useSWR from "swr";
import {
  ChevronRight,
  ChevronDown,
  Search,
  X,
  Building2,
  ArrowRight,
} from "lucide-react";
import { fetchNogaHierarchy } from "@/lib/api";
import type { NogaNode } from "@/lib/api";
import { cn } from "@/lib/utils";

// ── Section colour palette (A–S) ─────────────────────────────────────────────

const SECTION_BADGE: Record<string, string> = {
  A: "bg-green-100 text-green-800 border-green-200",
  B: "bg-stone-100 text-stone-800 border-stone-200",
  C: "bg-blue-100 text-blue-800 border-blue-200",
  D: "bg-yellow-100 text-yellow-800 border-yellow-200",
  E: "bg-teal-100 text-teal-800 border-teal-200",
  F: "bg-orange-100 text-orange-800 border-orange-200",
  G: "bg-indigo-100 text-indigo-800 border-indigo-200",
  H: "bg-cyan-100 text-cyan-800 border-cyan-200",
  I: "bg-amber-100 text-amber-800 border-amber-200",
  J: "bg-violet-100 text-violet-800 border-violet-200",
  K: "bg-emerald-100 text-emerald-800 border-emerald-200",
  L: "bg-rose-100 text-rose-800 border-rose-200",
  M: "bg-purple-100 text-purple-800 border-purple-200",
  N: "bg-slate-100 text-slate-700 border-slate-200",
  O: "bg-red-100 text-red-800 border-red-200",
  P: "bg-lime-100 text-lime-800 border-lime-200",
  Q: "bg-pink-100 text-pink-800 border-pink-200",
  R: "bg-sky-100 text-sky-800 border-sky-200",
  S: "bg-gray-100 text-gray-800 border-gray-200",
};

const SECTION_ACCENT: Record<string, string> = {
  A: "border-l-green-400",   B: "border-l-stone-400",
  C: "border-l-blue-400",    D: "border-l-yellow-400",
  E: "border-l-teal-400",    F: "border-l-orange-400",
  G: "border-l-indigo-400",  H: "border-l-cyan-400",
  I: "border-l-amber-400",   J: "border-l-violet-400",
  K: "border-l-emerald-400", L: "border-l-rose-400",
  M: "border-l-purple-400",  N: "border-l-slate-400",
  O: "border-l-red-400",     P: "border-l-lime-400",
  Q: "border-l-pink-400",    R: "border-l-sky-400",
  S: "border-l-gray-400",
};

// ── Helpers ───────────────────────────────────────────────────────────────────

function getLabel(node: NogaNode, locale: string): string {
  if (node.labels) {
    return (
      node.labels[locale] ||
      node.labels.de ||
      node.labels.fr ||
      node.labels.it ||
      node.labels.en ||
      node.label
    );
  }
  return node.label;
}

function nodeContains(node: NogaNode, q: string, locale: string): boolean {
  const label = getLabel(node, locale).toLowerCase();
  if (label.includes(q) || node.code.toLowerCase().includes(q)) return true;
  return node.children.some((c) => nodeContains(c, q, locale));
}

// ── TreeNode ──────────────────────────────────────────────────────────────────

interface TreeNodeProps {
  node: NogaNode;
  depth: number;
  locale: string;
  searchQuery: string;
  expandedCodes: Set<string>;
  sectionCode: string;
  onToggle: (code: string) => void;
  onNavigate: (code: string) => void;
}

function TreeNode({
  node,
  depth,
  locale,
  searchQuery,
  expandedCodes,
  sectionCode,
  onToggle,
  onNavigate,
}: TreeNodeProps) {
  const q = searchQuery.toLowerCase();
  if (q && !nodeContains(node, q, locale)) return null;

  const label = getLabel(node, locale);
  const hasChildren = node.children.length > 0;
  const isExpanded = q ? nodeContains(node, q, locale) : expandedCodes.has(node.code);
  const isSection = depth === 0;
  const badgeClass = SECTION_BADGE[sectionCode] ?? "bg-slate-100 text-slate-700 border-slate-200";
  const accentClass = SECTION_ACCENT[sectionCode] ?? "border-l-slate-400";

  if (isSection) {
    return (
      <div className="mb-0">
        {/* Section header row */}
        <div
          className={cn(
            "flex items-center gap-3 px-4 py-3 border-b border-slate-100 hover:bg-slate-50",
            "group cursor-pointer transition-colors select-none",
            "border-l-4",
            accentClass,
            isExpanded && "bg-slate-50/70"
          )}
          onClick={() => hasChildren && onToggle(node.code)}
        >
          {/* Letter badge */}
          <div
            className={cn(
              "w-9 h-9 rounded-xl flex items-center justify-center",
              "font-mono font-bold text-sm shrink-0 border",
              badgeClass
            )}
          >
            {node.code}
          </div>

          {/* Name + level */}
          <div className="flex-1 min-w-0">
            <span className="font-semibold text-slate-800 text-sm leading-tight block truncate">
              {label}
            </span>
            <span className="text-[10px] text-slate-400 uppercase tracking-wide">
              {node.level}
            </span>
          </div>

          {/* Count + browse CTA */}
          <div className="flex items-center gap-3 shrink-0">
            {node.count > 0 && (
              <span className="text-xs font-mono font-medium text-slate-500 tabular-nums">
                {node.count.toLocaleString()}
              </span>
            )}
            <button
              onClick={(e) => {
                e.stopPropagation();
                onNavigate(node.code);
              }}
              className={cn(
                "opacity-0 group-hover:opacity-100 transition-opacity",
                "flex items-center gap-1 text-xs font-medium text-blue-600 hover:text-blue-800",
                "px-2 py-1 rounded-lg hover:bg-blue-50"
              )}
            >
              <Building2 size={12} />
              Browse
            </button>
            {hasChildren && (
              <ChevronDown
                size={15}
                className={cn(
                  "text-slate-400 transition-transform duration-150 shrink-0",
                  !isExpanded && "-rotate-90"
                )}
              />
            )}
          </div>
        </div>

        {/* Children */}
        {isExpanded && hasChildren && (
          <div className={cn("border-l-4", accentClass, "border-opacity-30 ml-0")}>
            {node.children.map((child) => (
              <TreeNode
                key={child.code}
                node={child}
                depth={1}
                locale={locale}
                searchQuery={searchQuery}
                expandedCodes={expandedCodes}
                sectionCode={sectionCode}
                onToggle={onToggle}
                onNavigate={onNavigate}
              />
            ))}
          </div>
        )}
      </div>
    );
  }

  // L2–L5 rows — compact, indented
  const indent = 16 + (depth - 1) * 20;
  const codeWidth = depth === 1 ? "w-10" : depth === 2 ? "w-12" : "w-14";

  return (
    <div>
      <div
        className="flex items-center gap-2 py-1.5 pr-4 hover:bg-blue-50 group cursor-pointer transition-colors"
        style={{ paddingLeft: `${indent}px` }}
        onClick={() => hasChildren && onToggle(node.code)}
      >
        {hasChildren ? (
          <ChevronRight
            size={11}
            className={cn(
              "text-slate-300 shrink-0 transition-transform duration-150",
              isExpanded && "rotate-90"
            )}
          />
        ) : (
          <span className="w-3 shrink-0" />
        )}

        <span
          className={cn(
            "font-mono text-[11px] text-slate-400 shrink-0 tabular-nums",
            codeWidth
          )}
        >
          {node.code}
        </span>

        <span className="text-sm text-slate-700 flex-1 truncate leading-snug">
          {label}
        </span>

        <div className="flex items-center gap-2 shrink-0">
          {node.count > 0 && (
            <span className="text-xs font-mono text-slate-400 tabular-nums">
              {node.count.toLocaleString()}
            </span>
          )}
          <button
            onClick={(e) => {
              e.stopPropagation();
              onNavigate(node.code);
            }}
            className="opacity-0 group-hover:opacity-100 transition-opacity text-blue-500 hover:text-blue-700 p-0.5 rounded"
            title="Browse companies"
          >
            <ArrowRight size={12} />
          </button>
        </div>
      </div>

      {isExpanded && hasChildren && (
        <div>
          {node.children.map((child) => (
            <TreeNode
              key={child.code}
              node={child}
              depth={depth + 1}
              locale={locale}
              searchQuery={searchQuery}
              expandedCodes={expandedCodes}
              sectionCode={sectionCode}
              onToggle={onToggle}
              onNavigate={onNavigate}
            />
          ))}
        </div>
      )}
    </div>
  );
}

// ── Main component ────────────────────────────────────────────────────────────

export default function NogaClient({ locale }: { locale: string }) {
  const router = useRouter();
  const [searchQuery, setSearchQuery] = useState("");
  const [expandedCodes, setExpandedCodes] = useState<Set<string>>(new Set());

  const { data: hierarchy = [], isLoading } = useSWR(
    "noga-hierarchy",
    fetchNogaHierarchy,
    { dedupingInterval: 3_600_000 }
  );

  const totalClassified = useMemo(
    () => hierarchy.reduce((sum, n) => sum + n.count, 0),
    [hierarchy]
  );

  const toggleExpand = useCallback((code: string) => {
    setExpandedCodes((prev) => {
      const next = new Set(prev);
      if (next.has(code)) next.delete(code);
      else next.add(code);
      return next;
    });
  }, []);

  function handleNavigate(code: string) {
    router.push(`/${locale}/app/explorer?noga_code=${encodeURIComponent(code)}`);
  }

  const LEVEL_LABELS: Record<string, string> = {
    de: "Abschnitt · Abteilung · Gruppe · Klasse · Art",
    fr: "Section · Division · Groupe · Classe · Type",
    it: "Sezione · Divisione · Gruppo · Classe · Tipo",
    en: "Section · Division · Group · Class · Type",
  };

  return (
    <div className="flex flex-col h-[calc(100vh-5rem)] bg-white">
      {/* ── Page header ───────────────────────────────────────────────── */}
      <div className="px-6 py-4 border-b border-slate-200 bg-white shrink-0">
        <div className="flex items-start justify-between gap-4 flex-wrap">
          <div>
            <h1 className="text-xl font-semibold text-slate-900 tracking-tight">
              NOGA Industry Classification
            </h1>
            <p className="text-sm text-slate-500 mt-0.5">
              Swiss federal taxonomy ·{" "}
              <span className="font-mono">{hierarchy.length}</span> sections ·{" "}
              <span className="font-mono font-medium text-slate-700">
                {totalClassified.toLocaleString()}
              </span>{" "}
              classified companies
            </p>
            <p className="text-[11px] text-slate-400 mt-1 font-mono">
              {LEVEL_LABELS[locale] ?? LEVEL_LABELS.en}
            </p>
          </div>
        </div>

        {/* Search bar */}
        <div className="relative mt-3 max-w-sm">
          <Search
            size={14}
            className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400 pointer-events-none"
          />
          <input
            type="text"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            placeholder="Search by code or name…"
            className="w-full pl-8 pr-8 py-2 text-sm border border-slate-200 rounded-xl focus:outline-none focus:ring-2 focus:ring-blue-300 bg-slate-50 focus:bg-white transition-colors"
          />
          {searchQuery && (
            <button
              onClick={() => setSearchQuery("")}
              className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-400 hover:text-slate-600"
            >
              <X size={13} />
            </button>
          )}
        </div>
      </div>

      {/* ── Tree ──────────────────────────────────────────────────────── */}
      <div className="flex-1 overflow-y-auto">
        {isLoading && (
          <div className="flex flex-col">
            {Array.from({ length: 12 }).map((_, i) => (
              <div
                key={i}
                className="flex items-center gap-3 px-4 py-3 border-b border-slate-100 border-l-4 border-l-slate-200"
              >
                <div className="w-9 h-9 rounded-xl bg-slate-100 animate-pulse shrink-0" />
                <div className="flex-1 space-y-1.5">
                  <div
                    className="h-3.5 bg-slate-100 rounded animate-pulse"
                    style={{ width: `${40 + (i % 5) * 12}%` }}
                  />
                  <div className="h-2.5 bg-slate-50 rounded animate-pulse w-16" />
                </div>
                <div className="w-12 h-3 bg-slate-100 rounded animate-pulse" />
              </div>
            ))}
          </div>
        )}

        {!isLoading &&
          hierarchy.map((section) => (
            <TreeNode
              key={section.code}
              node={section}
              depth={0}
              locale={locale}
              searchQuery={searchQuery}
              expandedCodes={expandedCodes}
              sectionCode={section.code}
              onToggle={toggleExpand}
              onNavigate={handleNavigate}
            />
          ))}

        {!isLoading && hierarchy.length === 0 && (
          <div className="flex flex-col items-center justify-center h-64 text-slate-400 gap-3">
            <Building2 size={36} className="opacity-30" />
            <p className="text-sm text-center max-w-xs">
              No NOGA classification data available yet.
              <br />
              Run the NOGA classification job to populate this view.
            </p>
          </div>
        )}

        {/* Bottom padding so last row isn't clipped */}
        <div className="h-8" />
      </div>
    </div>
  );
}
