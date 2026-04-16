"use client";
import { useState } from "react";
import useSWR from "swr";
import {
  Coins, RefreshCw, Loader2, ChevronLeft, ChevronRight, TrendingUp, TrendingDown, ArrowUpCircle,
} from "lucide-react";
import {
  fetchAdminCreditTransactions,
  type AdminCreditTransaction,
  type AdminPage,
} from "@/lib/api";

// ── Helpers ───────────────────────────────────────────────────────────────────

const TX_TYPE_META: Record<string, { label: string; colour: string; sign: string }> = {
  topup:     { label: "Top-up",     colour: "bg-emerald-100 text-emerald-800", sign: "+" },
  grant:     { label: "Grant",      colour: "bg-teal-100 text-teal-800",       sign: "+" },
  bonus:     { label: "Bonus",      colour: "bg-lime-100 text-lime-800",       sign: "+" },
  deduction: { label: "Deduction",  colour: "bg-red-100 text-red-800",         sign: "−" },
  refund:    { label: "Refund",     colour: "bg-sky-100 text-sky-800",         sign: "+" },
  expire:    { label: "Expired",    colour: "bg-slate-100 text-slate-500",     sign: "−" },
};

const ACTION_LABELS: Record<string, string> = {
  web_search:          "Web search",
  batch_llm:           "Batch AI",
  immediate_llm:       "Immediate AI",
  flex_rescore:        "Flex rescore",
  recluster:           "Recluster",
  bulk_export_basic:   "CSV export",
  bulk_export_detail:  "CSV export (detail)",
  admin_grant:         "Admin grant",
  admin_deduct:        "Admin deduct",
};

function TypeBadge({ type }: { type: string }) {
  const m = TX_TYPE_META[type];
  return (
    <span className={`inline-block px-1.5 py-0.5 rounded text-[11px] font-medium ${m?.colour ?? "bg-slate-100 text-slate-600"}`}>
      {m?.label ?? type}
    </span>
  );
}

function fmtCredits(n: number | null, sign: string) {
  if (n == null) return "—";
  return `${sign}${Math.abs(n).toLocaleString()}`;
}

function fmtDate(iso: string) {
  return new Date(iso).toLocaleString("de-CH", {
    day: "2-digit", month: "2-digit", year: "2-digit",
    hour: "2-digit", minute: "2-digit",
  });
}

// ── Filters ───────────────────────────────────────────────────────────────────

interface Filters {
  tx_type: string;
  action_type: string;
  page: number;
}

const PAGE_SIZE = 50;

// ── KPI card ──────────────────────────────────────────────────────────────────

function KpiCard({ label, value, icon: Icon, colour }: {
  label: string; value: string | number; icon: React.ElementType; colour: string;
}) {
  return (
    <div className="bg-white rounded-xl border border-slate-200 p-4 flex items-start gap-3">
      <div className={`mt-0.5 p-2 rounded-lg ${colour}`}>
        <Icon size={16} className="text-current" />
      </div>
      <div>
        <p className="text-xs text-slate-500">{label}</p>
        <p className="text-xl font-semibold text-slate-900 tabular-nums">{value}</p>
      </div>
    </div>
  );
}

// ── Main ──────────────────────────────────────────────────────────────────────

export default function CreditTransactionsClient() {
  const [filters, setFilters] = useState<Filters>({ tx_type: "", action_type: "", page: 1 });

  const key = ["admin-credit-transactions", filters] as const;
  const { data, isLoading, mutate } = useSWR<AdminPage<AdminCreditTransaction>>(key, () =>
    fetchAdminCreditTransactions({
      tx_type: filters.tx_type || undefined,
      action_type: filters.action_type || undefined,
      page: filters.page,
      page_size: PAGE_SIZE,
    }),
  );

  const totalPages = data ? Math.ceil(data.total / PAGE_SIZE) : 1;

  // Simple KPIs derived from the current page
  const pageDeductions = data?.items.filter(r => r.type === "deduction").reduce((s, r) => s + Math.abs(r.amount), 0) ?? 0;
  const pageTopups     = data?.items.filter(r => r.type === "topup").reduce((s, r) => s + r.amount, 0) ?? 0;

  return (
    <div className="p-6 max-w-6xl space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-lg font-semibold text-slate-800">Credit Transactions</h1>
          <p className="text-xs text-slate-400 mt-0.5">All credit ledger entries · superadmin only</p>
        </div>
        <button
          onClick={() => void mutate()}
          className="flex items-center gap-1.5 text-xs text-slate-500 hover:text-slate-700 px-2.5 py-1.5 rounded-lg border border-slate-200 hover:bg-slate-50 transition-colors"
        >
          <RefreshCw size={12} /> Refresh
        </button>
      </div>

      {/* KPIs (page-level) */}
      {data && (
        <div className="grid grid-cols-2 md:grid-cols-3 gap-4">
          <KpiCard label="Total records" value={data.total.toLocaleString()} icon={Coins} colour="bg-purple-50 text-purple-600" />
          <KpiCard label="Deductions (this page)" value={pageDeductions.toLocaleString()} icon={TrendingDown} colour="bg-red-50 text-red-500" />
          <KpiCard label="Top-ups (this page)" value={pageTopups.toLocaleString()} icon={TrendingUp} colour="bg-emerald-50 text-emerald-600" />
        </div>
      )}

      {/* Filters */}
      <div className="flex flex-wrap gap-3 items-end">
        <div>
          <label className="block text-xs text-slate-500 mb-1">Type</label>
          <select
            value={filters.tx_type}
            onChange={(e) => setFilters({ ...filters, tx_type: e.target.value, page: 1 })}
            className="text-sm border border-slate-200 rounded-lg px-2 py-1.5 bg-white focus:outline-none focus:ring-2 focus:ring-purple-300"
          >
            <option value="">All types</option>
            {Object.entries(TX_TYPE_META).map(([slug, { label }]) => (
              <option key={slug} value={slug}>{label}</option>
            ))}
          </select>
        </div>
        <div>
          <label className="block text-xs text-slate-500 mb-1">Action</label>
          <select
            value={filters.action_type}
            onChange={(e) => setFilters({ ...filters, action_type: e.target.value, page: 1 })}
            className="text-sm border border-slate-200 rounded-lg px-2 py-1.5 bg-white focus:outline-none focus:ring-2 focus:ring-purple-300"
          >
            <option value="">All actions</option>
            {Object.entries(ACTION_LABELS).map(([slug, label]) => (
              <option key={slug} value={slug}>{label}</option>
            ))}
          </select>
        </div>
        {(filters.tx_type || filters.action_type) && (
          <button
            onClick={() => setFilters({ tx_type: "", action_type: "", page: 1 })}
            className="text-xs text-slate-500 hover:text-slate-700 underline pb-1.5"
          >
            Clear filters
          </button>
        )}
      </div>

      {/* Table */}
      <div className="bg-white rounded-xl border border-slate-200 overflow-hidden">
        <div className="px-4 py-3 border-b border-slate-100 flex items-center justify-between">
          <h2 className="text-sm font-semibold text-slate-700">
            Ledger
            {data && (
              <span className="ml-2 text-xs font-normal text-slate-400">
                ({data.total.toLocaleString()} total)
              </span>
            )}
          </h2>
          {data && totalPages > 1 && (
            <div className="flex items-center gap-2 text-xs text-slate-500">
              <button
                disabled={filters.page <= 1}
                onClick={() => setFilters({ ...filters, page: filters.page - 1 })}
                className="p-0.5 rounded hover:bg-slate-100 disabled:opacity-30"
              >
                <ChevronLeft size={14} />
              </button>
              <span>Page {filters.page} / {totalPages}</span>
              <button
                disabled={filters.page >= totalPages}
                onClick={() => setFilters({ ...filters, page: filters.page + 1 })}
                className="p-0.5 rounded hover:bg-slate-100 disabled:opacity-30"
              >
                <ChevronRight size={14} />
              </button>
            </div>
          )}
        </div>

        {isLoading ? (
          <div className="flex items-center justify-center h-32 text-slate-400">
            <Loader2 size={20} className="animate-spin" />
          </div>
        ) : !data?.items.length ? (
          <p className="text-xs text-slate-400 text-center py-10">No credit transactions found.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="text-left text-slate-500 border-b border-slate-100 bg-slate-50">
                  <th className="px-3 py-2 font-medium">When</th>
                  <th className="px-3 py-2 font-medium">Org</th>
                  <th className="px-3 py-2 font-medium">Type</th>
                  <th className="px-3 py-2 font-medium">Action</th>
                  <th className="px-3 py-2 font-medium text-right">Amount</th>
                  <th className="px-3 py-2 font-medium text-right">Before</th>
                  <th className="px-3 py-2 font-medium text-right">After</th>
                  <th className="px-3 py-2 font-medium">Reference</th>
                </tr>
              </thead>
              <tbody>
                {data.items.map((row) => {
                  const meta = TX_TYPE_META[row.type];
                  const isDebit = row.type === "deduction" || row.type === "expire";
                  return (
                    <tr key={row.id} className="border-b border-slate-50 last:border-0 hover:bg-slate-50/60 transition-colors">
                      <td className="px-3 py-2 text-slate-500 whitespace-nowrap font-mono">
                        {fmtDate(row.created_at)}
                      </td>
                      <td className="px-3 py-2 text-slate-700 max-w-[140px] truncate" title={row.org_name ?? undefined}>
                        {row.org_name ?? <span className="text-slate-400 italic">—</span>}
                        {row.org_id != null && (
                          <span className="ml-1 text-slate-400 font-mono">#{row.org_id}</span>
                        )}
                      </td>
                      <td className="px-3 py-2">
                        <TypeBadge type={row.type} />
                      </td>
                      <td className="px-3 py-2 text-slate-600">
                        {row.action_type ? (ACTION_LABELS[row.action_type] ?? row.action_type) : <span className="text-slate-300">—</span>}
                      </td>
                      <td className={`px-3 py-2 font-mono font-semibold text-right ${isDebit ? "text-red-600" : "text-emerald-600"}`}>
                        {fmtCredits(row.amount, meta?.sign ?? "")}
                      </td>
                      <td className="px-3 py-2 font-mono text-right text-slate-500">
                        {row.credits_before != null ? row.credits_before.toLocaleString() : "—"}
                      </td>
                      <td className="px-3 py-2 font-mono text-right text-slate-700 font-medium">
                        {row.credits_after != null ? row.credits_after.toLocaleString() : "—"}
                      </td>
                      <td className="px-3 py-2 text-slate-400 font-mono max-w-[180px] truncate" title={row.reference_id ?? undefined}>
                        {row.reference_id ?? "—"}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
