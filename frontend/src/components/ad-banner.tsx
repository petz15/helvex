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
      <div className="flex items-center justify-between rounded-lg border border-slate-200 bg-white px-4 py-2.5 text-xs">
        <div>
          <span className="text-slate-400 text-xs uppercase tracking-wide font-medium">Powered by</span>
          <span className="mx-2 font-semibold text-slate-900">Balogh Consulting</span>
          <span className="mx-2 text-slate-400">·</span>
          <span className="text-slate-600">Value first — you decide what it's worth.</span>
        </div>
        <Link
          href="https://balogh-consulting.ch"
          target="_blank"
          rel="noopener noreferrer"
          className="ml-4 whitespace-nowrap text-blue-600 hover:text-blue-700 hover:underline"
        >
          Learn more →
        </Link>
      </div>
    </div>
  );
}
