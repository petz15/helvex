"use client";
import { ChevronLeft, ChevronRight, ChevronsLeft, ChevronsRight } from "lucide-react";
import { cn } from "@/lib/utils";

interface PaginationProps {
  page: number;
  pages: number;
  total: number;
  pageSize: number;
  onChange: (page: number) => void;
  onPageSizeChange: (size: number) => void;
}

export function Pagination({ page, pages, total, pageSize, onChange, onPageSizeChange }: PaginationProps) {
  const from = Math.min((page - 1) * pageSize + 1, total);
  const to = Math.min(page * pageSize, total);

  const btn = (onClick: () => void, disabled: boolean, children: React.ReactNode) => (
    <button onClick={onClick} disabled={disabled}
      className={cn("p-1 rounded border border-slate-200 text-slate-600 hover:bg-slate-100 disabled:opacity-40 disabled:cursor-not-allowed")}>
      {children}
    </button>
  );

  return (
    <div className="flex items-center justify-between px-4 py-2 bg-white border-t border-slate-200 text-xs text-slate-500 shrink-0">
      <span>{from}–{to} of {total.toLocaleString()}</span>
      <div className="flex items-center gap-1">
        {btn(() => onChange(1), page <= 1, <ChevronsLeft size={14} />)}
        {btn(() => onChange(page - 1), page <= 1, <ChevronLeft size={14} />)}
        <span className="px-2">{page} / {pages}</span>
        {btn(() => onChange(page + 1), page >= pages, <ChevronRight size={14} />)}
        {btn(() => onChange(pages), page >= pages, <ChevronsRight size={14} />)}
      </div>
      <select
        value={pageSize}
        onChange={(e) => onPageSizeChange(Number(e.target.value))}
        className="border border-slate-200 rounded px-2 py-1 text-xs text-slate-600"
      >
        {[25, 50, 100, 200].map((s) => <option key={s} value={s}>{s} / page</option>)}
      </select>
    </div>
  );
}
