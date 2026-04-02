"use client";
import { useState, useEffect } from "react";
import useSWR from "swr";
import { ChevronLeft, ChevronRight, CreditCard, Zap, TrendingUp, ArrowUpRight, Loader2 } from "lucide-react";
import Link from "next/link";
import {
  fetchCurrentUser,
  fetchBillingSummary,
  fetchCreditTransactions,
  fetchPaymentHistory,
  createTopupCheckout,
  parseBillingAddressJson,
  type BillingAddressPayload,
  type CreditTransaction,
  type PaymentRecord,
} from "@/lib/api";
import {
  buildAddressReturnUrl,
  clearCheckoutIntent,
  readCheckoutIntent,
  saveCheckoutIntent,
} from "@/lib/checkout-resume";
import { creditsToChf } from "@/lib/entitlements";

// ── helpers ───────────────────────────────────────────────────────────────────

const TIER_COLORS: Record<string, string> = {
  free:        "bg-slate-100 text-slate-600",
  simple:      "bg-slate-100 text-slate-700",
  explorer:    "bg-blue-100 text-blue-700",
  researcher:  "bg-violet-100 text-violet-700",
  strategist:  "bg-slate-800 text-white",
};

const CREDIT_TYPE_META: Record<string, { label: string; cls: string }> = {
  topup:     { label: "Top-up",     cls: "text-emerald-600" },
  bonus:     { label: "Bonus",      cls: "text-emerald-500" },
  grant:     { label: "Grant",      cls: "text-blue-600" },
  deduction: { label: "Used",       cls: "text-slate-500" },
  refund:    { label: "Refund",     cls: "text-amber-600" },
  expire:    { label: "Expired",    cls: "text-red-400" },
};

const STATUS_CLS: Record<string, string> = {
  captured:   "text-emerald-600",
  authorized: "text-emerald-600",
  declined:   "text-red-500",
  error:      "text-red-500",
  pending:    "text-amber-500",
};

const TOPUP_AMOUNTS = [
  { credits: 10_000, label: "10,000", chf: "CHF 1.00" },
  { credits: 50_000, label: "50,000", chf: "CHF 5.00" },
  { credits: 100_000, label: "100,000", chf: "CHF 10.00" },
  { credits: 500_000, label: "500,000", chf: "CHF 50.00" },
];

function fmtDate(iso: string) {
  return new Date(iso).toLocaleDateString("en-CH", { day: "2-digit", month: "short", year: "numeric" });
}

function fmtCredits(n: number) {
  const abs = Math.abs(n);
  const sign = n > 0 ? "+" : n < 0 ? "−" : "";
  return `${sign}${abs.toLocaleString()}`;
}

// ── sub-components ─────────────────────────────────────────────────────────────

function SummaryCards({ balance, tier, billingCycle, periodEnd }: {
  balance: number; tier: string; billingCycle: string; periodEnd: string | null;
}) {
  return (
    <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
      {/* Credit balance */}
      <div className="rounded-2xl border border-slate-200 bg-white p-5">
        <div className="flex items-center gap-2 text-xs text-slate-400 font-medium uppercase tracking-wide">
          <Zap size={12} className="text-amber-400" />Credit balance
        </div>
        <div className="mt-2 text-3xl font-bold text-slate-900">{balance.toLocaleString()}</div>
        <div className="mt-0.5 text-sm text-slate-400">
          ≈ CHF {creditsToChf(balance).toFixed(2)}
        </div>
      </div>

      {/* Plan */}
      <div className="rounded-2xl border border-slate-200 bg-white p-5">
        <div className="flex items-center gap-2 text-xs text-slate-400 font-medium uppercase tracking-wide">
          <TrendingUp size={12} className="text-blue-400" />Current plan
        </div>
        <div className="mt-2 flex items-center gap-2">
          <span className={`text-lg font-bold capitalize px-2.5 py-0.5 rounded-lg ${TIER_COLORS[tier] ?? "bg-slate-100 text-slate-700"}`}>
            {tier}
          </span>
        </div>
        <div className="mt-1 text-sm text-slate-400 capitalize">{billingCycle} billing</div>
      </div>

      {/* Next renewal */}
      <div className="rounded-2xl border border-slate-200 bg-white p-5">
        <div className="flex items-center gap-2 text-xs text-slate-400 font-medium uppercase tracking-wide">
          <CreditCard size={12} className="text-violet-400" />Subscription
        </div>
        <div className="mt-2 text-slate-800 font-medium">
          {periodEnd ? `Renews ${fmtDate(periodEnd)}` : "—"}
        </div>
        <Link
          href="/app/pricing"
          className="mt-1 inline-flex items-center gap-1 text-xs text-blue-600 hover:underline"
        >
          Change plan <ArrowUpRight size={11} />
        </Link>
      </div>
    </div>
  );
}

function TopupSection({ billingAddress }: { billingAddress: BillingAddressPayload | null }) {
  const [loading, setLoading] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pendingCredits, setPendingCredits] = useState<number | null>(null);

  async function handleTopup(credits: number) {
    if (!billingAddress) {
      const sourcePath = `${window.location.pathname}${window.location.search}`;
      saveCheckoutIntent({
        kind: "topup",
        sourcePath,
        credits,
        success_path: "/app/billing",
        cancel_path: "/app/billing",
      });
      window.location.assign(buildAddressReturnUrl(sourcePath));
      return;
    }
    setPendingCredits(credits);
    setError(null);
  }

  useEffect(() => {
    if (!billingAddress) return;
    const params = new URLSearchParams(window.location.search);
    if (params.get("resume_checkout") !== "1") return;

    const intent = readCheckoutIntent();
    if (!intent || intent.kind !== "topup") return;
    if (!intent.sourcePath.startsWith("/app/billing")) return;

    clearCheckoutIntent();
    setPendingCredits(intent.credits);
    setError(null);
  }, [billingAddress]);

  async function confirmTopup() {
    if (!pendingCredits || !billingAddress) return;
    setLoading(pendingCredits);
    setError(null);
    try {
      const origin = window.location.origin;
      const successUrl = new URL("/app/billing", origin);
      successUrl.searchParams.set("checkout", "success");
      successUrl.searchParams.set("kind", "topup");
      successUrl.searchParams.set("credits", String(pendingCredits));
      const cancelUrl = new URL("/app/billing", origin);
      cancelUrl.searchParams.set("checkout", "cancel");
      cancelUrl.searchParams.set("kind", "topup");
      cancelUrl.searchParams.set("credits", String(pendingCredits));

      const session = await createTopupCheckout({
        credits: pendingCredits,
        success_url: successUrl.toString(),
        cancel_url: cancelUrl.toString(),
        billing_address: billingAddress,
      });
      window.location.assign(session.checkout_url);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to start checkout");
    } finally {
      setLoading(null);
      setPendingCredits(null);
    }
  }

  return (
    <div className="rounded-2xl border border-slate-200 bg-white p-5">
      <h2 className="text-sm font-semibold text-slate-700">Top up credits</h2>
      <p className="text-xs text-slate-400 mt-0.5 mb-4">Credits never expire. Higher tiers receive bonus credits on every purchase.</p>
      {error && (
        <div className="mb-3 text-xs text-red-600 bg-red-50 border border-red-200 rounded-lg px-3 py-2">{error}</div>
      )}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
        {TOPUP_AMOUNTS.map(({ credits, label, chf }) => (
          <button
            key={credits}
            onClick={() => handleTopup(credits)}
            disabled={loading !== null}
            className="flex flex-col items-center rounded-xl border border-slate-200 px-3 py-3 text-center hover:border-blue-400 hover:bg-blue-50 transition-colors disabled:opacity-60"
          >
            {loading === credits
              ? <Loader2 size={16} className="animate-spin text-blue-500 mb-1" />
              : <Zap size={16} className="text-amber-400 mb-1" />
            }
            <span className="text-sm font-semibold text-slate-800">{label}</span>
            <span className="text-xs text-slate-400">{chf}</span>
          </button>
        ))}
      </div>
      {pendingCredits && billingAddress && (
        <div className="mt-4 rounded-xl border border-slate-200 bg-slate-50 p-4 space-y-3">
          <div className="text-xs uppercase tracking-wide text-slate-400 font-semibold">Confirm billing address</div>
          <div className="text-sm text-slate-700">
            {billingAddress.first_name} {billingAddress.last_name}, {billingAddress.street} {billingAddress.number}, {billingAddress.postal_code} {billingAddress.city}, {billingAddress.country}
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={confirmTopup}
              disabled={loading !== null}
              className="rounded-lg bg-blue-600 px-3 py-2 text-xs font-medium text-white transition-colors hover:bg-blue-700 disabled:opacity-60"
            >
              {loading === pendingCredits ? "Starting…" : `Continue with ${pendingCredits.toLocaleString()} credits`}
            </button>
            <a href="/app/addresses?return_to=/app/billing" className="text-xs text-blue-600 hover:underline">Manage addresses</a>
          </div>
        </div>
      )}
    </div>
  );
}

function CreditHistory() {
  const [page, setPage] = useState(1);
  const { data, isLoading } = useSWR(
    `billing-credits-${page}`,
    () => fetchCreditTransactions(page, 20),
  );

  const totalPages = Math.max(1, Math.ceil((data?.total ?? 0) / 20));

  return (
    <div className="rounded-2xl border border-slate-200 bg-white overflow-hidden">
      <div className="px-5 py-4 border-b border-slate-100 flex items-center justify-between">
        <div>
          <h2 className="text-sm font-semibold text-slate-700">Credit ledger</h2>
          <p className="text-xs text-slate-400 mt-0.5">{data?.total ?? 0} transactions</p>
        </div>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="bg-slate-50">
            <tr>
              <th className="text-left px-4 py-2.5 text-xs text-slate-400 font-medium">Date</th>
              <th className="text-left px-4 py-2.5 text-xs text-slate-400 font-medium">Type</th>
              <th className="text-left px-4 py-2.5 text-xs text-slate-400 font-medium">Action</th>
              <th className="text-right px-4 py-2.5 text-xs text-slate-400 font-medium">Credits</th>
              <th className="text-right px-4 py-2.5 text-xs text-slate-400 font-medium">Balance after</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-50">
            {isLoading && (
              <tr><td colSpan={5} className="text-center py-8">
                <Loader2 size={16} className="animate-spin mx-auto text-slate-300" />
              </td></tr>
            )}
            {data?.items.map((tx: CreditTransaction) => {
              const meta = CREDIT_TYPE_META[tx.type] ?? { label: tx.type, cls: "text-slate-500" };
              return (
                <tr key={tx.id} className="hover:bg-slate-50 transition-colors">
                  <td className="px-4 py-2.5 text-xs text-slate-400">{fmtDate(tx.created_at)}</td>
                  <td className={`px-4 py-2.5 text-xs font-medium ${meta.cls}`}>{meta.label}</td>
                  <td className="px-4 py-2.5 text-xs text-slate-500">{tx.action_type ?? "—"}</td>
                  <td className={`px-4 py-2.5 text-right text-sm font-semibold tabular-nums ${tx.amount >= 0 ? "text-emerald-600" : "text-slate-500"}`}>
                    {fmtCredits(tx.amount)}
                  </td>
                  <td className="px-4 py-2.5 text-right text-xs text-slate-400 tabular-nums">
                    {tx.credits_after.toLocaleString()}
                  </td>
                </tr>
              );
            })}
            {!isLoading && data?.items.length === 0 && (
              <tr><td colSpan={5} className="text-center py-8 text-xs text-slate-400">No transactions yet</td></tr>
            )}
          </tbody>
        </table>
      </div>
      {totalPages > 1 && (
        <div className="px-4 py-3 border-t border-slate-100 flex items-center justify-between text-xs text-slate-400">
          <span>Page {page} of {totalPages}</span>
          <div className="flex gap-1">
            <button onClick={() => setPage(p => Math.max(1, p - 1))} disabled={page === 1} className="p-1 rounded hover:bg-slate-100 disabled:opacity-40">
              <ChevronLeft size={14} />
            </button>
            <button onClick={() => setPage(p => Math.min(totalPages, p + 1))} disabled={page === totalPages} className="p-1 rounded hover:bg-slate-100 disabled:opacity-40">
              <ChevronRight size={14} />
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

function PaymentHistory() {
  const [page, setPage] = useState(1);
  const { data, isLoading } = useSWR(
    `billing-payments-${page}`,
    () => fetchPaymentHistory(page, 20),
  );

  const totalPages = Math.max(1, Math.ceil((data?.total ?? 0) / 20));

  return (
    <div className="rounded-2xl border border-slate-200 bg-white overflow-hidden">
      <div className="px-5 py-4 border-b border-slate-100">
        <h2 className="text-sm font-semibold text-slate-700">Payment history</h2>
        <p className="text-xs text-slate-400 mt-0.5">{data?.total ?? 0} payments</p>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="bg-slate-50">
            <tr>
              <th className="text-left px-4 py-2.5 text-xs text-slate-400 font-medium">Date</th>
              <th className="text-left px-4 py-2.5 text-xs text-slate-400 font-medium">Type</th>
              <th className="text-left px-4 py-2.5 text-xs text-slate-400 font-medium">Details</th>
              <th className="text-left px-4 py-2.5 text-xs text-slate-400 font-medium">Method</th>
              <th className="text-left px-4 py-2.5 text-xs text-slate-400 font-medium">Status</th>
              <th className="text-right px-4 py-2.5 text-xs text-slate-400 font-medium">Amount</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-50">
            {isLoading && (
              <tr><td colSpan={6} className="text-center py-8">
                <Loader2 size={16} className="animate-spin mx-auto text-slate-300" />
              </td></tr>
            )}
            {data?.items.map((tx: PaymentRecord) => (
              <tr key={tx.id} className="hover:bg-slate-50 transition-colors">
                <td className="px-4 py-2.5 text-xs text-slate-400">{fmtDate(tx.created_at)}</td>
                <td className="px-4 py-2.5 text-xs font-medium text-slate-600 capitalize">{tx.kind}</td>
                <td className="px-4 py-2.5 text-xs text-slate-500">
                  {tx.kind === "subscription"
                    ? <span className="capitalize">{tx.subscription_tier ?? "?"} · {tx.subscription_billing_cycle ?? "?"}</span>
                    : tx.credits_total_granted
                      ? <span>{tx.credits_total_granted.toLocaleString()} credits{tx.credits_bonus ? <span className="text-emerald-500 ml-1">(+{tx.credits_bonus.toLocaleString()} bonus)</span> : null}</span>
                      : "—"
                  }
                </td>
                <td className="px-4 py-2.5 text-xs text-slate-400 capitalize">{tx.payment_method ?? "—"}</td>
                <td className={`px-4 py-2.5 text-xs font-medium capitalize ${STATUS_CLS[tx.status] ?? "text-slate-500"}`}>
                  {tx.status}
                  {tx.refunded_at && <span className="text-amber-500 ml-1">(refunded)</span>}
                </td>
                <td className="px-4 py-2.5 text-right text-sm font-semibold text-slate-800 tabular-nums">
                  CHF {tx.amount_chf.toFixed(2)}
                  {tx.refunded_amount_chf && (
                    <div className="text-xs text-amber-500 font-normal">−CHF {tx.refunded_amount_chf.toFixed(2)}</div>
                  )}
                </td>
              </tr>
            ))}
            {!isLoading && data?.items.length === 0 && (
              <tr><td colSpan={6} className="text-center py-8 text-xs text-slate-400">No payments yet</td></tr>
            )}
          </tbody>
        </table>
      </div>
      {totalPages > 1 && (
        <div className="px-4 py-3 border-t border-slate-100 flex items-center justify-between text-xs text-slate-400">
          <span>Page {page} of {totalPages}</span>
          <div className="flex gap-1">
            <button onClick={() => setPage(p => Math.max(1, p - 1))} disabled={page === 1} className="p-1 rounded hover:bg-slate-100 disabled:opacity-40">
              <ChevronLeft size={14} />
            </button>
            <button onClick={() => setPage(p => Math.min(totalPages, p + 1))} disabled={page === totalPages} className="p-1 rounded hover:bg-slate-100 disabled:opacity-40">
              <ChevronRight size={14} />
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

// ── main component ────────────────────────────────────────────────────────────

export function BillingClient() {
  const { data: summary, isLoading } = useSWR("billing-summary", fetchBillingSummary);
  const { data: me } = useSWR("me", fetchCurrentUser);
  const [returnBanner, setReturnBanner] = useState<{ kind: "success" | "error"; message: string } | null>(null);
  const billingAddress = parseBillingAddressJson(me?.billing_address_json);

  // Show banner when returning from payment provider
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const checkout = params.get("checkout");
    const kind = params.get("kind");
    const credits = params.get("credits");
    const tier = params.get("tier");
    const reason = params.get("reason");
    const alreadyProcessed = params.get("already_processed") === "true";

    if (!checkout && !alreadyProcessed && !reason) return;

    if (alreadyProcessed) {
      setReturnBanner({ kind: "success", message: "This payment was already processed. No changes were applied twice." });
    } else if (checkout === "success") {
      if (kind === "topup") {
        setReturnBanner({
          kind: "success",
          message: `Top-up successful! ${credits ? `${Number(credits).toLocaleString()} credits` : "Credits"} added to your balance.`,
        });
      } else if (kind === "subscription") {
        setReturnBanner({
          kind: "success",
          message: `${tier ? `${tier} plan` : "Subscription"} activated successfully.`,
        });
      } else {
        setReturnBanner({ kind: "success", message: "Payment completed successfully." });
      }
    } else {
      setReturnBanner({
        kind: "error",
        message:
          reason === "invalid_reference"
            ? "Payment could not be matched to an organization."
            : reason === "payment_declined"
              ? "Payment was declined or cancelled."
              : "Payment cancelled or declined.",
      });
    }

    window.history.replaceState({}, "", window.location.pathname);
  }, []);

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-64">
        <Loader2 size={20} className="animate-spin text-slate-300" />
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-4xl px-4 py-8 space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-slate-900">Billing</h1>
        <p className="text-sm text-slate-500 mt-1">Manage your plan, credits, and payment history.</p>
      </div>

      {returnBanner && (
        <div className={`rounded-xl border px-4 py-3 text-sm ${
          returnBanner.kind === "success"
            ? "border-emerald-200 bg-emerald-50 text-emerald-800"
            : "border-red-200 bg-red-50 text-red-800"
        }`}>
          {returnBanner.message}
        </div>
      )}

      {summary && (
        <SummaryCards
          balance={summary.credits_balance}
          tier={summary.tier}
          billingCycle={summary.billing_cycle}
          periodEnd={summary.subscription_period_end}
        />
      )}

      <TopupSection billingAddress={billingAddress} />
      <CreditHistory />
      <PaymentHistory />
    </div>
  );
}
