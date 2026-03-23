"use client";
import { useState, useMemo } from "react";
import {
  useReactTable, getCoreRowModel, flexRender,
  createColumnHelper, type VisibilityState,
} from "@tanstack/react-table";
import { ChevronUp, ChevronDown, ChevronsUpDown, Settings2 } from "lucide-react";
import { cn, reviewBadgeClass, proposalBadgeClass } from "@/lib/utils";
import { ScoreBar } from "@/components/ui/score-bar";
import { Badge } from "@/components/ui/badge";
import type { Company, CompanyFilters } from "@/lib/types";

const ch = createColumnHelper<Company>();

const SORT_MAP: Record<string, string> = {
  name: "name", canton: "canton", website_match_score: "google_score",
  zefix_score: "zefix_score", claude_score: "claude_score",
  combined_score: "combined_score", review_status: "review_status",
  proposal_status: "proposal_status", updated_at: "updated",
};

interface CompanyTableProps {
  companies: Company[];
  selectedId: number | null;
  onSelect: (company: Company) => void;
  filters: CompanyFilters;
  onSort: (sort: string) => void;
  isLoading: boolean;
}

function SortIcon({ col, sort }: { col: string; sort: string }) {
  const key = SORT_MAP[col];
  if (!key) return null;
  if (sort === key) return <ChevronUp size={12} className="text-blue-600" />;
  if (sort === `-${key}`) return <ChevronDown size={12} className="text-blue-600" />;
  return <ChevronsUpDown size={12} className="text-slate-300" />;
}

export function CompanyTable({ companies, selectedId, onSelect, filters, onSort, isLoading }: CompanyTableProps) {
  const sort = filters.sort ?? "-updated";

  const [columnVisibility, setColumnVisibility] = useState<VisibilityState>({
    tfidf_cluster: false,
    claude_category: false,
    website_checked_at: false,
    zefix_scored_at: false,
    claude_scored_at: false,
  });
  const [showColPicker, setShowColPicker] = useState(false);

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const columns = useMemo<any[]>(
    () => [
      ch.accessor("name", {
        header: "Company",
        cell: (info) => (
          <div className="font-medium text-slate-800 max-w-[200px] truncate" title={info.getValue() as string}>
            {info.getValue() as string}
          </div>
        ),
      }),
      ch.accessor("canton", {
        header: "Canton",
        cell: (info) => <span className="text-slate-600 text-xs">{info.getValue() as string ?? "—"}</span>,
      }),
      ch.accessor("status", {
        header: "Status",
        cell: (info) => <span className="text-slate-500 text-xs">{info.getValue() as string ?? "—"}</span>,
      }),
      ch.accessor("tfidf_cluster", {
        header: "Cluster",
        cell: (info) => <span className="text-slate-500 text-xs truncate max-w-[120px] block">{(info.getValue() as string)?.split("|")[0] ?? "—"}</span>,
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
      ch.accessor("website_match_score", {
        header: "Google",
        cell: (info) => <ScoreBar score={info.getValue() as number | null} />,
      }),
      ch.accessor("zefix_score", {
        header: "Zefix",
        cell: (info) => <ScoreBar score={info.getValue() as number | null} />,
      }),
      ch.accessor("claude_score", {
        header: "Claude",
        cell: (info) => <ScoreBar score={info.getValue() as number | null} />,
      }),
      ch.accessor("claude_category", {
        header: "Category",
        cell: (info) => <span className="text-slate-500 text-xs truncate max-w-[120px] block">{info.getValue() as string ?? "—"}</span>,
      }),
      ch.accessor("combined_score", {
        header: "Combined",
        cell: (info) => <ScoreBar score={info.getValue() as number | null} />,
      }),
      ch.accessor("review_status", {
        header: "Review",
        cell: (info) => (
          <Badge className={cn("text-xs", reviewBadgeClass(info.getValue() as string | null))}>
            {(info.getValue() as string)?.replace(/_/g, " ") ?? "pending"}
          </Badge>
        ),
      }),
      ch.accessor("proposal_status", {
        header: "Proposal",
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
          return <span className="text-slate-400 text-xs">{v ? new Date(v).toLocaleDateString("de-CH") : "—"}</span>;
        },
      }),
      ch.accessor("zefix_scored_at", {
        header: "Last Zefix",
        cell: (info) => {
          const v = info.getValue() as string | null;
          return <span className="text-slate-400 text-xs">{v ? new Date(v).toLocaleDateString("de-CH") : "—"}</span>;
        },
      }),
      ch.accessor("claude_scored_at", {
        header: "Last Claude",
        cell: (info) => {
          const v = info.getValue() as string | null;
          return <span className="text-slate-400 text-xs">{v ? new Date(v).toLocaleDateString("de-CH") : "—"}</span>;
        },
      }),
      ch.accessor("updated_at", {
        header: "Updated",
        cell: (info) => (
          <span className="text-slate-400 text-xs">{new Date(info.getValue() as string).toLocaleDateString("de-CH")}</span>
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
                {hg.headers.map((header) => {
                  const sortable = !!SORT_MAP[header.id];
                  return (
                    <th
                      key={header.id}
                      onClick={sortable ? () => handleHeaderClick(header.id) : undefined}
                      className={cn(
                        "px-3 py-2 text-left text-xs font-medium text-slate-500 border-b border-slate-200 whitespace-nowrap",
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
              </tr>
            ))}
          </thead>
          <tbody>
            {isLoading ? (
              <tr>
                <td colSpan={columns.length} className="text-center py-12 text-slate-400 text-sm">Loading…</td>
              </tr>
            ) : companies.length === 0 ? (
              <tr>
                <td colSpan={columns.length} className="text-center py-12 text-slate-400 text-sm">No companies found</td>
              </tr>
            ) : (
              table.getRowModel().rows.map((row) => (
                <tr
                  key={row.id}
                  onClick={() => onSelect(row.original)}
                  className={cn(
                    "border-b border-slate-100 cursor-pointer transition-colors",
                    row.original.id === selectedId
                      ? "bg-blue-50"
                      : "hover:bg-slate-50"
                  )}
                >
                  {row.getVisibleCells().map((cell) => (
                    <td key={cell.id} className="px-3 py-2 align-middle">
                      {flexRender(cell.column.columnDef.cell, cell.getContext())}
                    </td>
                  ))}
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
