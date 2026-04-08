"use client";
import { useState, useCallback } from "react";
import useSWR from "swr";
import {
  Loader2, ChevronLeft, ChevronRight, CheckCircle2, XCircle, Clock,
  AlertCircle, CreditCard, RefreshCw, RotateCcw,
} from "lucide-react";
import { fetchAdminPaymentTransactions, adminRefundTransaction, type AdminPaymentTransaction } from "@/lib/api";

const STATUS_META: Record<string, { label: string; cls: string; icon: React.ReactNode }> = {
  captured:   { label: "Captured",  cls: "bg-emerald-50 text-emerald-700 border-emerald-200",  icon: <CheckCircle2 size={12} /> },
  authorized: { label: "Authorized",cls: "bg-emerald-50 text-emerald-700 border-emerald-200",  icon: <CheckCircle2 size={12} /> },
  declined:   { label: "Declined",  cls: "bg-red-50 text-red-700 border-red-200",              icon: <XCircle size={12} /> },
  pending:    { label: "Pending",   cls: "bg-amber-50 text-amber-700 border-amber-200",         icon: <Clock size={12} /> },
  error:      { label: "Error",     cls: "bg-red-50 text-red-700 border-red-200",              icon: <AlertCircle size={12} /> },
};

const KIND_COLORS: Record<string, string> = {
  subscription: "bg-violet-50 text-violet-700 border-violet-200",
  topup:        "bg-sky-50 text-sky-700 border-sky-200",
};

function StatusBadge({ status }: { status: string }) {
  const meta = STATUS_META[status] ?? { label: status, cls: "bg-slate-50 text-slate-600 border-slate-200", icon: null };
  return (
    <span className={`inline-flex items-center gap-1 text-xs font-medium px-2 py-0.5 rounded-full border ${meta.cls}`}>
      {meta.icon}{meta.label}
    </span>
  );
}

function KindBadge({ kind }: { kind: string }) {
  return (
    <span className={`inline-flex text-xs font-medium px-2 py-0.5 rounded-full border ${KIND_COLORS[kind] ?? "bg-slate-50 text-slate-600 border-slate-200"}`}>
      {kind}
    </span>
  );
}

function fmtDate(iso: string | null) {
  if (!iso) return "—";
  return new Date(iso).toLocaleString("en-CH", { dateStyle: "short", timeStyle: "short" });
}

function fmtChf(v: number | null) {
  if (v == null) return "—";
  return `CHF ${v.toFixed(2)}`;
}

function RefundPanel({ tx, onRefunded }: { tx: AdminPaymentTransaction; onRefunded: (updated: AdminPaymentTransaction) => void }) {
  const [open, setOpen] = useState(false);
  const [amount, setAmount] = useState(tx.amount_chf.toFixed(2));
  const [reason, setReason] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const canRefund = (tx.status === "captured" || tx.status === "authorized")
    && tx.provider === "worldline"
    && !tx.refunded_at;

  if (!canRefund) return null;

  async function handleRefund() {
    const amt = parseFloat(amount);
    if (isNaN(amt) || amt <= 0) { setError("Enter a valid amount"); return; }
    if (amt > tx.amount_chf) { setError(`Cannot exceed CHF ${tx.amount_chf.toFixed(2)}`); return; }
    if (!reason.trim()) { setError("Reason is required"); return; }
    setLoading(true);
    setError(null);
    try {
      const updated = await adminRefundTransaction(tx.id, amt, reason.trim());
      onRefunded(updated);
      setOpen(false);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Refund failed");
    } finally {
      setLoading(false);
    }
  }

  if (!open) {
    return (
      <button
        onClick={() => setOpen(true)}
        className="mt-4 flex items-center gap-1.5 text-xs font-medium text-amber-700 border border-amber-300 bg-amber-50 rounded-lg px-3 py-2 hover:bg-amber-100 transition-colors"
      >
        <RotateCcw size={12} /> Issue Refund
      </button>
    );
  }

  return (
    <div className="mt-4 rounded-xl border border-amber-200 bg-amber-50 p-4 space-y-3">
      <div className="text-xs font-semibold text-amber-800 uppercase tracking-wide">Issue Refund</div>
      <div className="space-y-2">
        <label className="block">
          <span className="text-xs text-slate-500">Amount (CHF, max {tx.amount_chf.toFixed(2)})</span>
          <input
            type="number"
            step="0.01"
            min="0.01"
            max={tx.amount_chf}
            value={amount}
            onChange={(e) => setAmount(e.target.value)}
            className="mt-1 block w-full rounded-lg border border-slate-200 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-amber-300"
          />
        </label>
        <label className="block">
          <span className="text-xs text-slate-500">Reason (required)</span>
          <input
            type="text"
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            placeholder="e.g. Customer requested cancellation"
            className="mt-1 block w-full rounded-lg border border-slate-200 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-amber-300"
          />
        </label>
      </div>
      {error && <p className="text-xs text-red-600">{error}</p>}
      <div className="flex gap-2">
        <button
          onClick={handleRefund}
          disabled={loading}
          className="rounded-lg bg-amber-600 text-white px-3 py-1.5 text-xs font-medium hover:bg-amber-700 disabled:opacity-60"
        >
          {loading ? <Loader2 size={12} className="animate-spin inline" /> : "Confirm Refund"}
        </button>
        <button
          onClick={() => { setOpen(false); setError(null); }}
          className="rounded-lg border border-slate-200 px-3 py-1.5 text-xs text-slate-600 hover:bg-slate-100"
        >
          Cancel
        </button>
      </div>
    </div>
  );
}

function DetailDrawer({ tx: initialTx, onClose, onMutate }: { tx: AdminPaymentTransaction; onClose: () => void; onMutate: () => void }) {
  const [tx, setTx] = useState(initialTx);

  const rows: [string, React.ReactNode][] = [
    ["ID",              tx.id],
    ["Org ID",          tx.org_id],
    ["Provider",        <span className="font-mono">{tx.provider}</span>],
    ["External ID",     <span className="font-mono text-xs break-all">{tx.external_id}</span>],
    ["Order ref",       <span className="font-mono text-xs break-all">{tx.order_reference}</span>],
    ["Provider tx ID",  tx.provider_transaction_id ?? "—"],
    ["Amount",          fmtChf(tx.amount_chf)],
    ["Kind",            <KindBadge kind={tx.kind} />],
    ["Status",          <StatusBadge status={tx.status} />],
    ["Payment method",  tx.payment_method ?? "—"],
    ["Cardholder name", tx.cardholder_name ?? "—"],
    [
      "Billing address",
      tx.billing_address
        ? (() => {
            try {
              const addr = JSON.parse(tx.billing_address);
              return `${addr.street} ${addr.number}, ${addr.postal_code} ${addr.city}, ${addr.country}`;
            } catch {
              return tx.billing_address;
            }
          })()
        : "—"
    ],
    ["Tier",            tx.subscription_tier ?? "—"],
    ["Billing cycle",   tx.subscription_billing_cycle ?? "—"],
    ["Credits purchased", tx.credits_purchased?.toLocaleString() ?? "—"],
    ["Credits bonus",   tx.credits_bonus?.toLocaleString() ?? "—"],
    ["Credits granted", tx.credits_total_granted?.toLocaleString() ?? "—"],
    ["Error code",      tx.error_code ?? "—"],
    ["Error message",   tx.error_message ?? "—"],
    ["Refund amount",   fmtChf(tx.refunded_amount_chf)],
    ["Created",         fmtDate(tx.created_at)],
    ["Authorized",      fmtDate(tx.authorized_at)],
    ["Webhook processed", fmtDate(tx.webhook_processed_at)],
    ["Refunded at",     fmtDate(tx.refunded_at)],
  ];

  return (
    <div className="fixed inset-0 z-50 flex justify-end">
      <div className="absolute inset-0 bg-black/20" onClick={onClose} />
      <div className="relative w-full max-w-md bg-white shadow-2xl flex flex-col overflow-hidden">
        <div className="flex items-center justify-between px-5 py-4 border-b border-slate-200">
          <h2 className="font-semibold text-slate-800">Transaction #{tx.id}</h2>
          <button onClick={onClose} className="text-slate-400 hover:text-slate-600 text-lg leading-none">×</button>
        </div>
        <div className="overflow-y-auto flex-1 px-5 py-4">
          <dl className="space-y-2">
            {rows.map(([label, value]) => (
              <div key={label} className="flex gap-3 text-sm">
                <dt className="w-36 shrink-0 text-slate-500">{label}</dt>
                <dd className="text-slate-800 font-medium min-w-0">{value}</dd>
              </div>
            ))}
          </dl>
          <RefundPanel
            tx={tx}
            onRefunded={(updated) => {
              setTx(updated);
              onMutate();
            }}
          />
        </div>
      </div>
    </div>
  );
}

export function BillingAdminClient() {
  const [orgIdFilter, setOrgIdFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [kindFilter, setKindFilter] = useState("");
  const [providerFilter, setProviderFilter] = useState("");
  const [page, setPage] = useState(1);
  const [selected, setSelected] = useState<AdminPaymentTransaction | null>(null);

  const PAGE_SIZE = 50;

  const swrKey = `admin-billing-${orgIdFilter}-${statusFilter}-${kindFilter}-${providerFilter}-${page}`;
  const { data, isLoading, mutate } = useSWR(swrKey, () =>
    fetchAdminPaymentTransactions({
      org_id: orgIdFilter ? parseInt(orgIdFilter) : undefined,
      status: statusFilter || undefined,
      kind: kindFilter || undefined,
      provider: providerFilter || undefined,
      page,
      page_size: PAGE_SIZE,
    })
  );

  const resetFilters = useCallback(() => {
    setOrgIdFilter(""); setStatusFilter(""); setKindFilter("");
    setProviderFilter(""); setPage(1);
  }, []);

  const total = data?.total ?? 0;
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  // Revenue summary from current page
  const successItems = data?.items.filter(t => t.status === "captured" || t.status === "authorized") ?? [];
  const successRevenue = successItems.reduce((s, t) => s + t.amount_chf, 0);

  return (
    <div className="p-6 space-y-5">
      {selected && <DetailDrawer tx={selected} onClose={() => setSelected(null)} onMutate={() => mutate()} />}

      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-xl font-semibold text-slate-900">Payment Transactions</h1>
          <p className="text-sm text-slate-500 mt-0.5">{total} total transactions</p>
        </div>
        <button
          onClick={() => mutate()}
          className="flex items-center gap-1.5 text-xs text-slate-500 hover:text-slate-700 border border-slate-200 rounded-lg px-3 py-1.5 hover:bg-slate-50 transition-colors"
        >
          <RefreshCw size={12} />Refresh
        </button>
      </div>

      {/* Summary cards */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        {[
          { label: "Total",        value: total,                         sub: "all time" },
          { label: "Successful",   value: successItems.length,           sub: "this page" },
          { label: "Revenue",      value: `CHF ${successRevenue.toFixed(2)}`, sub: "this page" },
          { label: "Failed",
            value: (data?.items.filter(t => t.status === "declined" || t.status === "error").length ?? 0),
            sub: "this page" },
        ].map(({ label, value, sub }) => (
          <div key={label} className="rounded-xl border border-slate-200 bg-white px-4 py-3">
            <div className="text-xs text-slate-400">{label}</div>
            <div className="text-xl font-semibold text-slate-800 mt-0.5">{value}</div>
            <div className="text-xs text-slate-400">{sub}</div>
          </div>
        ))}
      </div>

      {/* Filters */}
      <div className="flex gap-2 flex-wrap items-center">
        <input
          type="number"
          placeholder="Org ID"
          value={orgIdFilter}
          onChange={(e) => { setOrgIdFilter(e.target.value); setPage(1); }}
          className="w-24 px-3 py-1.5 border border-slate-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-purple-300"
        />
        <select
          value={statusFilter}
          onChange={(e) => { setStatusFilter(e.target.value); setPage(1); }}
          className="border border-slate-200 rounded-lg text-sm px-3 py-1.5 focus:outline-none focus:ring-2 focus:ring-purple-300"
        >
          <option value="">All statuses</option>
          {["captured", "authorized", "declined", "pending", "error"].map(s => (
            <option key={s} value={s}>{s}</option>
          ))}
        </select>
        <select
          value={kindFilter}
          onChange={(e) => { setKindFilter(e.target.value); setPage(1); }}
          className="border border-slate-200 rounded-lg text-sm px-3 py-1.5 focus:outline-none focus:ring-2 focus:ring-purple-300"
        >
          <option value="">All kinds</option>
          <option value="subscription">Subscription</option>
          <option value="topup">Top-up</option>
        </select>
        <select
          value={providerFilter}
          onChange={(e) => { setProviderFilter(e.target.value); setPage(1); }}
          className="border border-slate-200 rounded-lg text-sm px-3 py-1.5 focus:outline-none focus:ring-2 focus:ring-purple-300"
        >
          <option value="">All providers</option>
          <option value="worldline">Worldline</option>
          <option value="stripe">Stripe</option>
        </select>
        {(orgIdFilter || statusFilter || kindFilter || providerFilter) && (
          <button onClick={resetFilters} className="text-xs text-slate-400 hover:text-slate-600 underline">
            Clear filters
          </button>
        )}
      </div>

      {/* Table */}
      <div className="rounded-xl border border-slate-200 overflow-x-auto">
        <table className="w-full text-sm min-w-[700px]">
          <thead className="bg-slate-50 border-b border-slate-200">
            <tr>
              <th className="text-left px-4 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wide">ID</th>
              <th className="text-left px-4 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wide">Org</th>
              <th className="text-left px-4 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wide">Kind</th>
              <th className="text-left px-4 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wide">Status</th>
              <th className="text-left px-4 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wide">Amount</th>
              <th className="text-left px-4 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wide">Method</th>
              <th className="text-left px-4 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wide">Details</th>
              <th className="text-left px-4 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wide">Date</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {isLoading && (
              <tr>
                <td colSpan={8} className="text-center py-10 text-slate-400">
                  <Loader2 size={20} className="animate-spin mx-auto" />
                </td>
              </tr>
            )}
            {!isLoading && data?.items.map((tx) => (
              <tr
                key={tx.id}
                onClick={() => setSelected(tx)}
                className="hover:bg-slate-50 cursor-pointer transition-colors"
              >
                <td className="px-4 py-3 font-mono text-xs text-slate-400">#{tx.id}</td>
                <td className="px-4 py-3 text-slate-600 font-mono text-xs">{tx.org_id}</td>
                <td className="px-4 py-3"><KindBadge kind={tx.kind} /></td>
                <td className="px-4 py-3"><StatusBadge status={tx.status} /></td>
                <td className="px-4 py-3 font-medium text-slate-800">{fmtChf(tx.amount_chf)}</td>
                <td className="px-4 py-3 text-slate-500 text-xs">
                  <span className="flex items-center gap-1">
                    <CreditCard size={11} />
                    {tx.payment_method ?? "—"}
                  </span>
                </td>
                <td className="px-4 py-3 text-xs text-slate-500">
                  {tx.kind === "subscription"
                    ? `${tx.subscription_tier ?? "?"} · ${tx.subscription_billing_cycle ?? "?"}`
                    : tx.credits_total_granted
                      ? `${tx.credits_total_granted.toLocaleString()} credits`
                      : "—"}
                </td>
                <td className="px-4 py-3 text-xs text-slate-400">{fmtDate(tx.created_at)}</td>
              </tr>
            ))}
            {!isLoading && data?.items.length === 0 && (
              <tr>
                <td colSpan={8} className="text-center py-10 text-slate-400 text-sm">
                  No transactions found
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {totalPages > 1 && (
        <div className="flex items-center justify-between text-sm text-slate-500">
          <span>{total} transactions · page {page}/{totalPages}</span>
          <div className="flex gap-1">
            <button
              onClick={() => setPage((p) => Math.max(1, p - 1))}
              disabled={page === 1}
              className="p-1.5 rounded hover:bg-slate-100 disabled:opacity-40"
            >
              <ChevronLeft size={16} />
            </button>
            <button
              onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
              disabled={page === totalPages}
              className="p-1.5 rounded hover:bg-slate-100 disabled:opacity-40"
            >
              <ChevronRight size={16} />
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
