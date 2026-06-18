"use client";

import useSWR from "swr";
import Link from "next/link";
import { fetchCurrentUser, fetchOrg } from "@/lib/api";
import { hasNoAds } from "@/lib/entitlements";

export function AdBanner() {
  const { data: me } = useSWR("me", fetchCurrentUser);
  const orgId = me?.org?.id ?? null;
  const { data: org } = useSWR(orgId ? ["org-detail", orgId] : null, () => fetchOrg(orgId!));

  if (!me || !org) return null;
  if (hasNoAds({ tier: org.tier, customFeatures: org.custom_features })) return null;

  return (
    <div className="mx-auto w-full max-w-7xl px-4 py-2">
      <div className="flex items-center justify-between rounded-xl border border-blue-200 bg-gradient-to-r from-blue-50 via-white to-indigo-50 px-5 py-3 shadow-sm">
        <div className="flex items-center gap-3">
          <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-blue-600 text-white text-xs font-bold shadow-sm">
            B
          </div>
          <div>
            <div className="flex items-center gap-2">
              <span className="font-semibold text-slate-900 text-sm">Balogh Consulting</span>
              <span className="rounded-full bg-blue-100 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-blue-700">Sponsor</span>
            </div>
            <p className="text-xs text-slate-600 mt-0.5">Value first — you decide what it&apos;s worth.</p>
          </div>
        </div>
        <Link
          href="https://balogh-consulting.ch"
          target="_blank"
          rel="noopener noreferrer"
          className="ml-6 shrink-0 rounded-lg bg-blue-600 px-4 py-1.5 text-xs font-semibold text-white shadow-sm hover:bg-blue-700 transition-colors"
        >
          Learn more →
        </Link>
      </div>
    </div>
  );
}
