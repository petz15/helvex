"use client";
import { useState, useMemo } from "react";
import Link from "next/link";
import {
  useReactTable, getCoreRowModel, flexRender,
  createColumnHelper, type VisibilityState,
} from "@tanstack/react-table";
import { ChevronUp, ChevronDown, ChevronsUpDown, Settings2, ThumbsUp, ThumbsDown, Minus } from "lucide-react";
import { cn, reviewBadgeClass, proposalBadgeClass, scoreColor, formatClusterLabel } from "@/lib/utils";
import { ScoreBar } from "@/components/ui/score-bar";
import { Badge } from "@/components/ui/badge";
import type { Company, CompanyFilters } from "@/lib/types";

const ch = createColumnHelper<Company>();

const SORT_MAP: Record<string, string> = {
  name: "name", canton: "canton", web_score: "web_score",
  flex_score: "flex_score", ai_score: "ai_score",
  combined_score: "combined_score", review_status: "review_status",
  contact_status: "contact_status", ai_category: "ai_category",
  updated_at: "updated",
};

interface CompanyTableProps {
  companies: Company[];
  selectedId: number | null;
  onSelect: (company: Company) => void;
  filters: CompanyFilters;
  onSort: (sort: string) => void;
  isLoading: boolean;
  // batch selection
  selectedIds?: Set<number>;
  onToggleSelect?: (id: number) => void;
  onSelectAll?: (ids: number[]) => void;
  // inline triage
  onUpdateReview?: (id: number, status: string | null) => void;
}

function SortIcon({ col, sort }: { col: string; sort: string }) {
  const key = SORT_MAP[col];
  if (!key) return null;
  if (sort === key) return <ChevronUp size={12} className="text-blue-600" />;
  if (sort === `-${key}`) return <ChevronDown size={12} className="text-blue-600" />;
  return <ChevronsUpDown size={12} className="text-slate-400" />;
}

function reviewLeftBorderClass(status: string | null): string {
  switch (status) {
    case "confirmed_proposal":
      return "border-l-green-500";
    case "potential_proposal":
      return "border-l-blue-500";
    case "interesting":
      return "border-l-yellow-400";
    case "rejected":
      return "border-l-red-400";
    default:
      return "border-l-transparent";
  }
}

function scoreTextClass(score: number | null): string {
  if (score == null) return "text-slate-500";
  if (score >= 70) return "text-green-700";
  if (score >= 40) return "text-yellow-700";
  return "text-red-700";
}

function ProTag() {
  return (
    <span
      className="inline-flex items-center text-[9px] uppercase tracking-wide font-semibold text-amber-700 bg-amber-50 border border-amber-200 rounded px-1 py-px"
      title="AI feature — uses credits per company"
    >
      Pro
    </span>
  );
}

interface FlexBreakdown {
  clusters: number;
  keywords: number;
  distance: number;
  legal_form: number;
  data_quality: number;
  raw_total: number;
  final_score: number;
  cancelled: boolean;
}

function FlexScoreCell({ score, breakdownJson }: { score: number | null; breakdownJson: string | null }) {
  let bd: FlexBreakdown | null = null;
  try { if (breakdownJson) bd = JSON.parse(breakdownJson); } catch { /* ignore */ }

  return (
    <div className="relative group">
      <ScoreBar score={score} />
      {bd && (
        <div className="absolute bottom-full left-1/2 -translate-x-1/2 mb-1.5 z-50 hidden group-hover:block pointer-events-none">
          <div className="bg-slate-800 text-white text-xs rounded-lg px-3 py-2 shadow-xl w-44 space-y-1">
            {bd.cancelled ? (
              <p className="text-slate-300 italic">Cancelled / dissolved</p>
            ) : (
              <>
                <p className="font-semibold text-slate-200 mb-1.5">Flex breakdown</p>
                {[
                  ["Clusters", bd.clusters],
                  ["Keywords", bd.keywords],
                  ["Distance", bd.distance],
                  ["Legal form", bd.legal_form],
                  ["Data quality", bd.data_quality],
                ].map(([label, val]) => (
                  <div key={label as string} className="flex justify-between gap-2">
                    <span className="text-slate-400">{label}</span>
                    <span className={cn("tabular-nums font-medium", (val as number) > 0 ? "text-green-400" : (val as number) < 0 ? "text-red-400" : "text-slate-400")}>
                      {(val as number) > 0 ? "+" : ""}{val as number}
                    </span>
                  </div>
                ))}
                <div className="border-t border-slate-600 pt-1 flex justify-between gap-2">
                  <span className="text-slate-300 font-medium">Score</span>
                  <span className="tabular-nums font-bold text-white">{bd.final_score}</span>
                </div>
              </>
            )}
            <div className="absolute top-full left-1/2 -translate-x-1/2 border-4 border-transparent border-t-slate-800" />
          </div>
        </div>
      )}
    </div>
  );
}

export function CompanyTable({ companies, selectedId, onSelect, filters, onSort, isLoading, selectedIds, onToggleSelect, onSelectAll, onUpdateReview }: CompanyTableProps) {
  const sort = filters.sort ?? "-updated";

  const [columnVisibility, setColumnVisibility] = useState<VisibilityState>({
    web_score: false,
    flex_score: false,
    ai_score: false,
    combined_score: false,
    review_status: false,
    contact_status: false,
    tfidf_cluster: false,
    ai_category: false,
    website_checked_at: false,
    flex_scored_at: false,
    ai_scored_at: false,
  });
  const [showColPicker, setShowColPicker] = useState(false);

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const columns = useMemo<any[]>(
    () => [
      ch.accessor("name", {
        header: "Company",
        cell: (info) => (
          <div
            className={cn(
              "border-l-[3px] pl-2 -ml-2",
              reviewLeftBorderClass(info.row.original.review_status),
            )}
          >
            <Link
              href={`/app/companies/${info.row.original.id}`}
              onClick={(e) => e.stopPropagation()}
              className="font-medium text-blue-700 hover:underline max-w-[200px] truncate block"
              title={info.getValue() as string}
            >
              {info.getValue() as string}
            </Link>
          </div>
        ),
      }),
      ch.accessor("canton", {
        header: "Canton",
        cell: (info) => <span className="text-slate-600 text-xs">{info.getValue() as string ?? "—"}</span>,
      }),
      ch.accessor("status", {
        header: "Status",
        cell: (info) => <span className="text-slate-700 text-xs">{info.getValue() as string ?? "—"}</span>,
      }),
      ch.accessor("tfidf_cluster", {
        header: "Cluster",
        cell: (info) => <span className="text-slate-600 text-xs truncate max-w-[140px] block" title={(info.getValue() as string) ?? undefined}>{formatClusterLabel((info.getValue() as string)?.split("|")[0] ?? "") || "—"}</span>,
      }),
      ch.accessor("noga_label", {
        id: "noga",
        header: "NOGA",
        cell: (info) => {
          const row = info.row.original;
          const codes = (row.noga_path ?? "").split("|").filter(Boolean);
          const labels = (row.noga_path_labels ?? "").split("|").filter(Boolean);
          const leafLabel = (info.getValue() as string | null) ?? labels[labels.length - 1] ?? row.noga_code ?? null;
          if (!leafLabel) return <span className="text-slate-300 text-xs">—</span>;
          const tooltip = labels.length > 0
            ? labels.map((l, i) => `${codes[i] ?? ""} ${l}`.trim()).join(" › ")
            : (row.noga_code ? `${row.noga_code} ${leafLabel}` : leafLabel);
          const lowConf = row.noga_confidence != null && row.noga_confidence < 0.5;
          return (
            <span
              className={cn("text-slate-700 text-xs truncate max-w-[180px] block", lowConf && "text-slate-400 italic")}
              title={tooltip}
            >
              {row.noga_code && <span className="font-mono opacity-60 mr-1">{row.noga_code}</span>}
              {leafLabel}
            </span>
          );
        },
      }),
      ch.accessor("website_url", {
        id: "website",
        header: "Website",
        cell: (info) => {
          const url = info.getValue() as string | null;
          return url
            ? <a href={url} target="_blank" rel="noopener noreferrer" onClick={(e) => e.stopPropagation()}
                className="text-blue-600 hover:underline text-xs truncate max-w-[140px] block">{url.replace(/^https?:\/\//, "")}</a>
            : <span className="text-slate-300 text-xs">—</span>;
        },
      }),
      ch.accessor("web_score", {
        header: "Web",
        cell: (info) => <ScoreBar score={info.getValue() as number | null} />,
      }),
      ch.accessor("flex_score", {
        header: "Flex",
        cell: (info) => (
          <FlexScoreCell
            score={info.getValue() as number | null}
            breakdownJson={info.row.original.flex_score_breakdown}
          />
        ),
      }),
      ch.accessor("ai_score", {
        header: () => <span className="inline-flex items-center gap-1">AI <ProTag /></span>,
        cell: (info) => <ScoreBar score={info.getValue() as number | null} />,
      }),
      ch.accessor("ai_category", {
        header: () => <span className="inline-flex items-center gap-1">Category <ProTag /></span>,
        cell: (info) => <span className="text-slate-600 text-xs truncate max-w-[120px] block">{info.getValue() as string ?? "—"}</span>,
      }),
      ch.accessor("combined_score", {
        header: "Combined",
        cell: (info) => {
          const v = info.getValue() as number | null;
          const row = info.row.original;
          const hasComponents = row.ai_score != null || row.web_score != null || row.flex_score != null;
          return (
            <div className="relative group min-w-[6rem]">
              <div className={cn("text-xs font-bold tabular-nums", scoreTextClass(v))}>
                {v == null ? "—" : Math.round(v)}
              </div>
              <div className="mt-1 h-1.5 bg-slate-200 rounded-full overflow-hidden">
                <div className={cn("h-full rounded-full", scoreColor(v))} style={{ width: `${v ?? 0}%` }} />
              </div>
              {hasComponents && (
                <div className="absolute bottom-full left-1/2 -translate-x-1/2 mb-1.5 z-50 hidden group-hover:block pointer-events-none">
                  <div className="bg-slate-800 text-white text-xs rounded-lg px-3 py-2 shadow-xl w-40 space-y-1">
                    <p className="font-semibold text-slate-200 mb-1.5">Score breakdown</p>
                    {[
                      ["AI", row.ai_score, "70%"],
                      ["Web", row.web_score, "20%"],
                      ["FLEX", row.flex_score, "10%"],
                    ].map(([label, val]) => (
                      <div key={label as string} className="flex justify-between gap-2">
                        <span className="text-slate-400">{label}</span>
                        <span className={cn("tabular-nums font-medium", val == null ? "text-slate-500" : "text-white")}>
                          {val == null ? "—" : Math.round(val as number)}
                        </span>
                      </div>
                    ))}
                    <div className="border-t border-slate-600 pt-1 flex justify-between gap-2">
                      <span className="text-slate-300 font-medium">Combined</span>
                      <span className="tabular-nums font-bold text-white">{v == null ? "—" : Math.round(v)}</span>
                    </div>
                    <div className="absolute top-full left-1/2 -translate-x-1/2 border-4 border-transparent border-t-slate-800" />
                  </div>
                </div>
              )}
            </div>
          );
        },
      }),
      ch.accessor("review_status", {
        header: "Review",
        cell: (info) => (
          <Badge className={cn("text-xs", reviewBadgeClass(info.getValue() as string | null))}>
            {(info.getValue() as string)?.replace(/_/g, " ") ?? "pending"}
          </Badge>
        ),
      }),
      ch.accessor("contact_status", {
        header: "Contact",
        cell: (info) => {
          const v = info.getValue() as string | null;
          if (!v || v === "not_sent") return <span className="text-slate-300 text-xs">—</span>;
          return <Badge className={cn("text-xs", proposalBadgeClass(v))}>{v}</Badge>;
        },
      }),
      ch.accessor("website_checked_at", {
        header: "Last Google",
        cell: (info) => {
          const v = info.getValue() as string | null;
          return <span className="text-slate-600 text-xs">{v ? new Date(v).toLocaleDateString("de-CH") : "—"}</span>;
        },
      }),
      ch.accessor("flex_scored_at", {
        header: "Last Flex",
        cell: (info) => {
          const v = info.getValue() as string | null;
          return <span className="text-slate-600 text-xs">{v ? new Date(v).toLocaleDateString("de-CH") : "—"}</span>;
        },
      }),
      ch.accessor("ai_scored_at", {
        header: "Last AI",
        cell: (info) => {
          const v = info.getValue() as string | null;
          return <span className="text-slate-600 text-xs">{v ? new Date(v).toLocaleDateString("de-CH") : "—"}</span>;
        },
      }),
      ch.accessor("updated_at", {
        header: "Updated",
        cell: (info) => (
          <span className="text-slate-600 text-xs">{new Date(info.getValue() as string).toLocaleDateString("de-CH")}</span>
        ),
      }),
    ],
    []
  );

  const table = useReactTable({
    data: companies,
    columns,
    state: { columnVisibility },
    onColumnVisibilityChange: setColumnVisibility,
    getCoreRowModel: getCoreRowModel(),
  });

  const handleHeaderClick = (colId: string) => {
    const key = SORT_MAP[colId];
    if (!key) return;
    const current = sort;
    if (current === `-${key}`) onSort(key);
    else onSort(`-${key}`);
  };

  return (
    <div className="flex-1 flex flex-col overflow-hidden">
      {/* Column picker */}
      <div className="flex items-center justify-end px-3 py-1.5 border-b border-slate-100 bg-white">
        <div className="relative">
          <button
            onClick={() => setShowColPicker((v) => !v)}
            className="flex items-center gap-1.5 text-xs text-slate-500 hover:text-slate-700 px-2 py-1 rounded border border-slate-200 hover:bg-slate-50"
          >
            <Settings2 size={13} /> Columns
          </button>
          {showColPicker && (
            <div className="absolute right-0 top-full mt-1 z-50 bg-white border border-slate-200 rounded-lg shadow-lg p-3 w-48 grid grid-cols-1 gap-1">
              {table.getAllLeafColumns().map((col) => (
                <label key={col.id} className="flex items-center gap-2 text-xs text-slate-700 cursor-pointer hover:text-slate-900">
                  <input
                    type="checkbox"
                    checked={col.getIsVisible()}
                    onChange={col.getToggleVisibilityHandler()}
                    className="rounded border-slate-300 text-blue-600"
                  />
                  {col.id.replace(/_/g, " ")}
                </label>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* Table */}
      <div className="flex-1 overflow-auto">
        <table className="w-full text-sm border-collapse">
          <thead className="sticky top-0 bg-white z-10 shadow-sm">
            {table.getHeaderGroups().map((hg) => (
              <tr key={hg.id}>
                {onToggleSelect && (
                  <th className="px-3 py-2 border-b border-slate-200 w-8">
                    <input
                      type="checkbox"
                      className="rounded border-slate-300 text-blue-600"
                      checked={companies.length > 0 && companies.every(c => selectedIds?.has(c.id))}
                      onChange={() => onSelectAll?.(companies.map(c => c.id))}
                    />
                  </th>
                )}
                {hg.headers.map((header) => {
                  const sortable = !!SORT_MAP[header.id];
                  return (
                    <th
                      key={header.id}
                      onClick={sortable ? () => handleHeaderClick(header.id) : undefined}
                      className={cn(
                        "px-3 py-2 text-left text-xs font-semibold text-slate-700 border-b border-slate-200 whitespace-nowrap",
                        sortable && "cursor-pointer select-none hover:text-slate-700 hover:bg-slate-50"
                      )}
                    >
                      <span className="flex items-center gap-1">
                        {flexRender(header.column.columnDef.header, header.getContext())}
                        <SortIcon col={header.id} sort={sort} />
                      </span>
                    </th>
                  );
                })}
                {onUpdateReview && <th className="w-24 border-b border-slate-200" />}
              </tr>
            ))}
          </thead>
          <tbody>
            {isLoading ? (
              <tr>
                <td colSpan={columns.length} className="text-center py-12 text-slate-500 text-sm">Loading…</td>
              </tr>
            ) : companies.length === 0 ? (
              <tr>
                <td colSpan={columns.length} className="text-center py-12 text-slate-500 text-sm">No companies found</td>
              </tr>
            ) : (
              table.getRowModel().rows.map((row) => (
                <tr
                  key={row.id}
                  onClick={() => onSelect(row.original)}
                  className={cn(
                    "group border-b border-slate-100 cursor-pointer transition-colors",
                    String(row.original.status ?? "").toLowerCase() === "cancelled" && "opacity-60",
                    selectedIds?.has(row.original.id)
                      ? "bg-blue-50"
                      : row.original.id === selectedId
                        ? "bg-blue-50"
                        : "hover:bg-slate-50"
                  )}
                >
                  {onToggleSelect && (
                    <td className="px-3 py-2 align-middle w-8" onClick={(e) => { e.stopPropagation(); onToggleSelect(row.original.id); }}>
                      <input
                        type="checkbox"
                        className="rounded border-slate-300 text-blue-600"
                        checked={selectedIds?.has(row.original.id) ?? false}
                        onChange={() => onToggleSelect(row.original.id)}
                      />
                    </td>
                  )}
                  {row.getVisibleCells().map((cell) => (
                    <td key={cell.id} className="px-3 py-2 align-middle">
                      {flexRender(cell.column.columnDef.cell, cell.getContext())}
                    </td>
                  ))}
                  {onUpdateReview && (
                    <td className="px-2 py-2 align-middle w-24" onClick={(e) => e.stopPropagation()}>
                      <div className="invisible group-hover:visible flex items-center gap-0.5">
                        <button
                          title="Mark interesting"
                          onClick={() => onUpdateReview(row.original.id, row.original.review_status === "interesting" ? null : "interesting")}
                          className={cn(
                            "p-1 rounded transition-colors",
                            row.original.review_status === "interesting"
                              ? "text-amber-600 bg-amber-50"
                              : "text-slate-300 hover:text-amber-600 hover:bg-amber-50"
                          )}
                        >
                          <ThumbsUp size={12} />
                        </button>
                        <button
                          title="Skip (clear review)"
                          onClick={() => onUpdateReview(row.original.id, null)}
                          className="p-1 rounded text-slate-300 hover:text-slate-600 hover:bg-slate-100 transition-colors"
                        >
                          <Minus size={12} />
                        </button>
                        <button
                          title="Reject"
                          onClick={() => onUpdateReview(row.original.id, row.original.review_status === "rejected" ? null : "rejected")}
                          className={cn(
                            "p-1 rounded transition-colors",
                            row.original.review_status === "rejected"
                              ? "text-red-500 bg-red-50"
                              : "text-slate-300 hover:text-red-500 hover:bg-red-50"
                          )}
                        >
                          <ThumbsDown size={12} />
                        </button>
                      </div>
                    </td>
                  )}
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
