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
  cancelPendingPayment,
  cancelSubscription,
  fetchOrg,
  fetchOrgMembers,
  setOrgDefaultPaymentUser,
  createWorldlineCardRegistration,
  parseBillingAddressJson,
  type BillingAddressPayload,
  type CreditTransaction,
  type PaymentRecord,
} from "@/lib/api";
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

function SummaryCards({ balance, tier, billingCycle, periodEnd, onCancelSubscription }: {
  balance: number; tier: string; billingCycle: string; periodEnd: string | null;
  onCancelSubscription: () => void;
}) {
  const [cancelling, setCancelling] = useState(false);
  const [cancelError, setCancelError] = useState<string | null>(null);
  const [showCancelConfirm, setShowCancelConfirm] = useState(false);
  const isPaid = tier !== "free";

  async function handleCancel() {
    setCancelling(true);
    setCancelError(null);
    try {
      await cancelSubscription();
      setShowCancelConfirm(false);
      onCancelSubscription();
    } catch (e) {
      setCancelError(e instanceof Error ? e.message : "Failed to cancel");
    } finally {
      setCancelling(false);
    }
  }

  return (
    <div className="space-y-3">
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
          <div className="mt-1 text-sm text-slate-400 capitalize">{billingCycle ? `${billingCycle} billing` : "—"}</div>
        </div>

        {/* Subscription */}
        <div className="rounded-2xl border border-slate-200 bg-white p-5">
          <div className="flex items-center gap-2 text-xs text-slate-400 font-medium uppercase tracking-wide">
            <CreditCard size={12} className="text-violet-400" />Subscription
          </div>
          <div className="mt-2 text-slate-800 font-medium">
            {periodEnd ? `Renews ${fmtDate(periodEnd)}` : isPaid ? "Active" : "—"}
          </div>
          {isPaid && (
            <p className="mt-0.5 text-xs text-slate-400">Renews automatically until cancelled</p>
          )}
          <div className="mt-2 flex items-center gap-3">
            <Link
              href="/app/pricing"
              className="inline-flex items-center gap-1 text-xs text-blue-600 hover:underline"
            >
              Change plan <ArrowUpRight size={11} />
            </Link>
            {isPaid && !showCancelConfirm && (
              <button
                onClick={() => setShowCancelConfirm(true)}
                className="text-xs text-red-500 hover:underline"
              >
                Cancel subscription
              </button>
            )}
          </div>
        </div>
      </div>

      {/* Cancel confirmation */}
      {showCancelConfirm && (
        <div className="rounded-xl border border-red-200 bg-red-50 px-4 py-3 space-y-2">
          <p className="text-sm font-medium text-red-800">Cancel subscription?</p>
          <p className="text-xs text-red-700">
            Your plan will be downgraded to <strong>Free</strong> immediately. Credits already in your balance are kept. No refund is issued.
          </p>
          {cancelError && <p className="text-xs text-red-600">{cancelError}</p>}
          <div className="flex gap-2">
            <button
              onClick={handleCancel}
              disabled={cancelling}
              className="rounded-lg bg-red-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-red-700 disabled:opacity-60"
            >
              {cancelling ? "Cancelling…" : "Yes, cancel subscription"}
            </button>
            <button
              onClick={() => { setShowCancelConfirm(false); setCancelError(null); }}
              className="rounded-lg border border-red-200 px-3 py-1.5 text-xs text-red-700 hover:bg-red-100"
            >
              Keep subscription
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

function TopupSection() {
  const [customCreditsInput, setCustomCreditsInput] = useState<string>("25000");

  const customCredits = Number.parseInt(customCreditsInput, 10);
  const customCreditsValid = Number.isInteger(customCredits) && customCredits >= 100;

  function goToPayment(credits: number) {
    const p = new URLSearchParams({
      kind: "topup",
      credits: String(credits),
      success_path: "/app/billing",
      cancel_path: "/app/billing",
    });
    window.location.assign(`/app/payment?${p.toString()}`);
  }

  return (
    <div className="rounded-2xl border border-slate-200 bg-white p-5">
      <h2 className="text-sm font-semibold text-slate-700">Top up credits</h2>
      <p className="text-xs text-slate-400 mt-0.5 mb-4">Credits never expire. Higher tiers receive bonus credits on every purchase.</p>

      <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
        {TOPUP_AMOUNTS.map(({ credits, label, chf }) => (
          <button
            key={credits}
            onClick={() => goToPayment(credits)}
            className="flex flex-col items-center rounded-xl border border-slate-200 px-3 py-3 text-center hover:border-blue-400 hover:bg-blue-50 transition-colors"
          >
            <Zap size={16} className="text-amber-400 mb-1" />
            <span className="text-sm font-semibold text-slate-800">{label}</span>
            <span className="text-xs text-slate-400">{chf}</span>
          </button>
        ))}
      </div>

      <div className="mt-4 rounded-xl border border-slate-200 bg-white p-4 space-y-3">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-end">
          <label className="flex-1 space-y-1">
            <span className="block text-xs uppercase tracking-wide text-slate-400 font-semibold">Custom amount</span>
            <input
              type="number"
              min={100}
              step={1}
              value={customCreditsInput}
              onChange={(e) => setCustomCreditsInput(e.target.value)}
              className="w-full rounded-lg border border-slate-200 px-3 py-2 text-sm text-slate-800 outline-none transition-colors focus:border-blue-400"
              placeholder="25000"
            />
          </label>
          <div className="text-xs text-slate-500 sm:min-w-40">
            {customCreditsValid ? `≈ CHF ${creditsToChf(customCredits).toFixed(2)}` : "Minimum is 100 credits."}
          </div>
          <button
            onClick={() => goToPayment(customCredits)}
            disabled={!customCreditsValid}
            className="rounded-lg bg-slate-900 px-3 py-2 text-xs font-medium text-white transition-colors hover:bg-slate-800 disabled:opacity-60"
          >
            Top up custom amount
          </button>
        </div>
      </div>
    </div>
  );
}

function SavedCardSection({ billingAddress, hasSavedPaymentMethod }: { billingAddress: BillingAddressPayload | null; hasSavedPaymentMethod: boolean }) {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleRegisterCard() {
    if (!billingAddress) {
      setError("Add a billing address first in Address Management before saving a card.");
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const origin = window.location.origin;
      const successUrl = new URL("/app/billing", origin);
      successUrl.searchParams.set("checkout", "success");
      successUrl.searchParams.set("kind", "card");
      successUrl.searchParams.set("saved", "1");
      const cancelUrl = new URL("/app/billing", origin);
      cancelUrl.searchParams.set("checkout", "cancel");
      cancelUrl.searchParams.set("kind", "card");

      const session = await createWorldlineCardRegistration({
        success_url: successUrl.toString(),
        cancel_url: cancelUrl.toString(),
        billing_address: billingAddress,
      });
      window.location.assign(session.checkout_url);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to start card registration");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="rounded-2xl border border-slate-200 bg-white p-5">
      <h2 className="text-sm font-semibold text-slate-700">Saved card</h2>
      <p className="mt-0.5 text-xs text-slate-400">
        Save a Saferpay alias on your user account so admins can pick it as the org's default card later.
      </p>
      {error && (
        <div className="mt-3 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700">{error}</div>
      )}
      <div className="mt-4 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="text-sm text-slate-700">
          {hasSavedPaymentMethod ? "Your account has a saved payment method." : "No saved payment method on your account yet."}
        </div>
        <button
          onClick={handleRegisterCard}
          disabled={loading}
          className="rounded-lg bg-slate-900 px-3 py-2 text-xs font-medium text-white transition-colors hover:bg-slate-800 disabled:opacity-60"
        >
          {loading ? "Opening Saferpay…" : hasSavedPaymentMethod ? "Replace saved card" : "Save a card"}
        </button>
      </div>
      {!billingAddress && (
        <p className="mt-3 text-xs text-amber-600">Add a billing address first to continue.</p>
      )}
    </div>
  );
}

function DefaultCardSection({
  orgId,
  currentUserId,
  currentDefaultUserId,
  members,
  canManage,
  onUpdated,
}: {
  orgId: number;
  currentUserId: number;
  currentDefaultUserId: number | null;
  members: Array<{ id: number; email: string; has_saved_payment_method: boolean }>;
  canManage: boolean;
  onUpdated: () => void;
}) {
  const [savingUserId, setSavingUserId] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);

  if (!canManage) return null;

  const eligibleMembers = members.filter((member) => member.has_saved_payment_method);

  async function selectDefault(userId: number) {
    setSavingUserId(userId);
    setError(null);
    try {
      await setOrgDefaultPaymentUser(orgId, userId);
      onUpdated();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to update default card");
    } finally {
      setSavingUserId(null);
    }
  }

  async function clearDefault() {
    setSavingUserId(0);
    setError(null);
    try {
      await setOrgDefaultPaymentUser(orgId, null);
      onUpdated();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to clear default card");
    } finally {
      setSavingUserId(null);
    }
  }

  return (
    <div className="rounded-2xl border border-slate-200 bg-white p-5">
      <h2 className="text-sm font-semibold text-slate-700">Org default card</h2>
      <p className="mt-0.5 text-xs text-slate-400">
        Admins can choose which member's saved card should be charged by default.
      </p>
      {error && (
        <div className="mt-3 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700">{error}</div>
      )}
      <div className="mt-4 space-y-2">
        {eligibleMembers.length === 0 ? (
          <div className="text-sm text-slate-500">No members with a saved card yet.</div>
        ) : (
          eligibleMembers.map((member) => {
            const active = member.id === currentDefaultUserId;
            return (
              <div key={member.id} className="flex items-center justify-between gap-3 rounded-xl border border-slate-200 px-3 py-2">
                <div>
                  <div className="text-sm font-medium text-slate-800">{member.email}</div>
                  <div className="text-xs text-slate-400">{active ? "Current default" : member.id === currentUserId ? "You" : "Saved card available"}</div>
                </div>
                <button
                  onClick={() => selectDefault(member.id)}
                  disabled={savingUserId !== null}
                  className={`rounded-lg px-3 py-2 text-xs font-medium transition-colors disabled:opacity-60 ${
                    active ? "bg-emerald-600 text-white" : "bg-slate-900 text-white hover:bg-slate-800"
                  }`}
                >
                  {savingUserId === member.id ? "Saving…" : active ? "Selected" : "Use by default"}
                </button>
              </div>
            );
          })
        )}
      </div>
      {currentDefaultUserId !== null && (
        <button
          onClick={clearDefault}
          disabled={savingUserId !== null}
          className="mt-3 text-xs text-slate-500 hover:underline disabled:opacity-60"
        >
          Clear default card
        </button>
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
  const { data, isLoading, mutate } = useSWR(
    `billing-payments-${page}`,
    () => fetchPaymentHistory(page, 20),
  );
  const [cancelingId, setCancelingId] = useState<number | null>(null);

  async function handleCancel(paymentId: number) {
    setCancelingId(paymentId);
    try {
      await cancelPendingPayment(paymentId);
      await mutate();
    } finally {
      setCancelingId(null);
    }
  }

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
              <th className="text-right px-4 py-2.5 text-xs text-slate-400 font-medium">Actions</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-50">
            {isLoading && (
              <tr><td colSpan={7} className="text-center py-8">
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
                  {tx.decline_reason && (
                    <div className="mt-1 inline-block rounded-md bg-amber-50 px-2 py-0.5 text-[11px] font-medium normal-case text-amber-700 border border-amber-200">
                      {tx.decline_reason}
                    </div>
                  )}
                </td>
                <td className="px-4 py-2.5 text-right text-sm font-semibold text-slate-800 tabular-nums">
                  CHF {tx.amount_chf.toFixed(2)}
                  {tx.refunded_amount_chf && (
                    <div className="text-xs text-amber-500 font-normal">−CHF {tx.refunded_amount_chf.toFixed(2)}</div>
                  )}
                </td>
                <td className="px-4 py-2.5 text-right">
                  {tx.status === "pending" && (
                    <button
                      onClick={() => handleCancel(tx.id)}
                      disabled={cancelingId !== null}
                      className="rounded-lg border border-slate-200 px-2 py-1 text-xs text-slate-700 hover:bg-slate-100 disabled:opacity-60"
                    >
                      {cancelingId === tx.id ? "Cancelling..." : "Cancel"}
                    </button>
                  )}
                </td>
              </tr>
            ))}
            {!isLoading && data?.items.length === 0 && (
              <tr><td colSpan={7} className="text-center py-8 text-xs text-slate-400">No payments yet</td></tr>
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
  const { data: summary, isLoading, mutate: mutateSummary } = useSWR("billing-summary", fetchBillingSummary);
  const { data: me } = useSWR("me", fetchCurrentUser);
  const { data: org } = useSWR(summary ? `org-${summary.org_id}` : null, () => fetchOrg(summary!.org_id));
  const { data: members, mutate: mutateMembers } = useSWR(summary ? `org-members-${summary.org_id}` : null, () => fetchOrgMembers(summary!.org_id));
  const { mutate: mutateOrg } = useSWR(summary ? `org-${summary.org_id}` : null, () => fetchOrg(summary!.org_id));
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
    const saved = params.get("saved") === "1";
    const alreadyProcessed = params.get("already_processed") === "true";

    if (!checkout && !alreadyProcessed && !reason && !saved) return;

    if (alreadyProcessed) {
      setReturnBanner({ kind: "success", message: "This payment was already processed. No changes were applied twice." });
    } else if (saved && checkout === "success") {
      setReturnBanner({ kind: "success", message: "Saved card registered successfully." });
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
          onCancelSubscription={() => void mutateSummary()}
        />
      )}

      <TopupSection />
      {summary && (
        <SavedCardSection billingAddress={billingAddress} hasSavedPaymentMethod={summary.has_saved_payment_method} />
      )}
      {summary && me && org && members && (
        <DefaultCardSection
          orgId={summary.org_id}
          currentUserId={me.id}
          currentDefaultUserId={org.default_payment_user_id}
          members={members}
          canManage={me.org_role === "admin" || me.org_role === "owner"}
          onUpdated={() => {
            void mutateMembers();
            void mutateOrg();
          }}
        />
      )}
      <CreditHistory />
      <PaymentHistory />
    </div>
  );
}
