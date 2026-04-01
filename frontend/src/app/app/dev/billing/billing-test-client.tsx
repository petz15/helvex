"use client";

import { useState } from "react";
import { createSubscriptionCheckout, createTopupCheckout } from "@/lib/api";

const PLAN_TIERS = ["simple", "explorer", "researcher", "strategist"] as const;
const TOPUP_AMOUNTS = [10_000, 50_000, 100_000] as const;

type CheckoutKind =
  | { kind: "subscription"; value: string }
  | { kind: "topup"; value: number };

function buildReturnUrl(pathname: string, params: Record<string, string>): string {
  const url = new URL(pathname, window.location.origin);
  for (const [key, value] of Object.entries(params)) {
    url.searchParams.set(key, value);
  }
  return url.toString();
}

export function BillingTestClient() {
  const [yearly, setYearly] = useState(false);
  const [loading, setLoading] = useState<CheckoutKind | null>(null);
  const [banner, setBanner] = useState<{ kind: "success" | "error"; message: string } | null>(null);

  async function startPlanCheckout(tier: (typeof PLAN_TIERS)[number]) {
    setLoading({ kind: "subscription", value: tier });
    setBanner(null);
    try {
      const session = await createSubscriptionCheckout({
        tier,
        billing_cycle: yearly ? "yearly" : "monthly",
        success_url: buildReturnUrl("/app/dev/billing", {
          checkout: "success",
          tier,
          cycle: yearly ? "yearly" : "monthly",
        }),
        cancel_url: buildReturnUrl("/app/dev/billing", {
          checkout: "cancel",
          tier,
          cycle: yearly ? "yearly" : "monthly",
        }),
      });
      window.location.assign(session.checkout_url);
    } catch (err) {
      setBanner({
        kind: "error",
        message: err instanceof Error ? err.message : "Failed to start subscription checkout",
      });
    } finally {
      setLoading(null);
    }
  }

  async function startTopupCheckout(credits: number) {
    setLoading({ kind: "topup", value: credits });
    setBanner(null);
    try {
      const session = await createTopupCheckout({
        credits,
        success_url: buildReturnUrl("/app/dev/billing", {
          checkout: "success",
          credits: String(credits),
        }),
        cancel_url: buildReturnUrl("/app/dev/billing", {
          checkout: "cancel",
          credits: String(credits),
        }),
      });
      window.location.assign(session.checkout_url);
    } catch (err) {
      setBanner({
        kind: "error",
        message: err instanceof Error ? err.message : "Failed to start top-up checkout",
      });
    } finally {
      setLoading(null);
    }
  }

  return (
    <div className="min-h-screen bg-[radial-gradient(circle_at_top,_rgba(56,189,248,0.16),_transparent_34%),linear-gradient(180deg,#020617_0%,#0f172a_42%,#111827_100%)] text-slate-100">
      <div className="mx-auto flex min-h-screen max-w-5xl flex-col px-4 py-10 sm:px-6 lg:px-8">
        <div className="max-w-2xl space-y-4">
          <p className="text-xs font-semibold uppercase tracking-[0.3em] text-sky-300">Dev billing test</p>
          <h1 className="text-4xl font-bold tracking-tight sm:text-5xl">Checkout probes for the Saferpay flow</h1>
          <p className="max-w-2xl text-sm leading-6 text-slate-300">
            Use this page to launch a live subscription or top-up checkout directly, then verify the browser return URL and backend authorization path.
          </p>
        </div>

        <div className="mt-8 flex flex-wrap items-center gap-3 rounded-2xl border border-white/10 bg-white/5 p-4 backdrop-blur">
          <span className="text-xs font-medium text-slate-300">Billing cycle</span>
          <button
            onClick={() => setYearly((value) => !value)}
            className={`rounded-full px-3 py-1.5 text-xs font-semibold transition-colors ${
              yearly ? "bg-sky-400 text-slate-950" : "bg-white/10 text-slate-200 hover:bg-white/15"
            }`}
          >
            {yearly ? "Yearly" : "Monthly"}
          </button>
          <span className="text-xs text-slate-400">Yearly uses the same plan selector with a 10x price factor.</span>
        </div>

        {banner && (
          <div
            role="status"
            className={`mt-5 rounded-2xl border px-4 py-3 text-sm ${
              banner.kind === "error"
                ? "border-red-400/40 bg-red-500/10 text-red-200"
                : "border-emerald-400/40 bg-emerald-500/10 text-emerald-200"
            }`}
          >
            {banner.message}
          </div>
        )}

        <div className="mt-8 grid grid-cols-1 gap-4 lg:grid-cols-2">
          <section className="rounded-3xl border border-white/10 bg-white/5 p-5 shadow-2xl shadow-slate-950/30 backdrop-blur">
            <div className="flex items-center justify-between gap-3">
              <div>
                <h2 className="text-lg font-semibold text-white">Subscription checkout</h2>
                <p className="text-xs text-slate-400">Launch a live plan checkout for any paid tier.</p>
              </div>
              <span className="rounded-full border border-sky-400/30 bg-sky-400/10 px-2.5 py-1 text-[11px] font-semibold text-sky-200">
                {yearly ? "Yearly" : "Monthly"}
              </span>
            </div>

            <div className="mt-4 grid grid-cols-1 gap-3 sm:grid-cols-2">
              {PLAN_TIERS.map((tier) => (
                <button
                  key={tier}
                  onClick={() => startPlanCheckout(tier)}
                  disabled={loading !== null}
                  className="rounded-2xl border border-white/10 bg-slate-900/70 px-4 py-4 text-left transition-colors hover:border-sky-400/40 hover:bg-slate-900 disabled:opacity-60"
                >
                  <p className="text-xs font-semibold uppercase tracking-[0.2em] text-slate-400">Plan</p>
                  <p className="mt-1 text-lg font-semibold capitalize text-white">{tier}</p>
                  <p className="mt-2 text-xs text-slate-400">
                    {loading?.kind === "subscription" && loading.value === tier ? "Starting checkout…" : `Start ${tier} checkout`}
                  </p>
                </button>
              ))}
            </div>
          </section>

          <section className="rounded-3xl border border-white/10 bg-white/5 p-5 shadow-2xl shadow-slate-950/30 backdrop-blur">
            <div className="flex items-center justify-between gap-3">
              <div>
                <h2 className="text-lg font-semibold text-white">Top-up checkout</h2>
                <p className="text-xs text-slate-400">Exercise credit purchases with a known amount.</p>
              </div>
              <span className="rounded-full border border-emerald-400/30 bg-emerald-400/10 px-2.5 py-1 text-[11px] font-semibold text-emerald-200">
                Credits
              </span>
            </div>

            <div className="mt-4 grid grid-cols-1 gap-3 sm:grid-cols-2">
              {TOPUP_AMOUNTS.map((credits) => (
                <button
                  key={credits}
                  onClick={() => startTopupCheckout(credits)}
                  disabled={loading !== null}
                  className="rounded-2xl border border-white/10 bg-slate-900/70 px-4 py-4 text-left transition-colors hover:border-emerald-400/40 hover:bg-slate-900 disabled:opacity-60"
                >
                  <p className="text-xs font-semibold uppercase tracking-[0.2em] text-slate-400">Top-up</p>
                  <p className="mt-1 text-lg font-semibold text-white">{credits.toLocaleString()} credits</p>
                  <p className="mt-2 text-xs text-slate-400">
                    {loading?.kind === "topup" && loading.value === credits ? "Starting checkout…" : "Launch credit purchase"}
                  </p>
                </button>
              ))}
            </div>
          </section>
        </div>

        <div className="mt-8 rounded-2xl border border-white/10 bg-black/20 p-4 text-xs leading-6 text-slate-300">
          This page is intentionally unlinked in production. It exists to test the end-to-end checkout redirect and browser return path from a stable internal route.
        </div>
      </div>
    </div>
  );
}
