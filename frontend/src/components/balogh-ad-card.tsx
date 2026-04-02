"use client";

import useSWR from "swr";
import Link from "next/link";
import { fetchCurrentUser, fetchOrg } from "@/lib/api";
import { hasNoAds } from "@/lib/entitlements";

interface BaloghAdCardProps {
  className?: string;
}

export function BaloghAdCard({ className = "" }: BaloghAdCardProps) {
  const { data: me } = useSWR("me", fetchCurrentUser);
  const orgId = me?.org?.id ?? null;
  const { data: org } = useSWR(orgId ? ["org-detail", orgId] : null, () => fetchOrg(orgId!));

  if (!me || !org) return null;
  if (hasNoAds({ tier: org.tier, customFeatures: org.custom_features })) return null;

  return (
    <div className={`rounded-lg border border-slate-200 bg-white p-4 shadow-sm ${className}`}>
      <div className="flex flex-col gap-3">
        <div>
          <p className="text-xs text-slate-400 uppercase tracking-wide font-medium">Powered by</p>
          <h3 className="text-sm font-semibold text-slate-900 mt-0.5">Balogh Consulting</h3>
          <p className="mt-1 text-xs text-slate-600">Value first — you decide what it's worth.</p>
        </div>
        <Link
          href="https://balogh-consulting.ch"
          target="_blank"
          rel="noopener noreferrer"
          className="inline-flex items-center gap-1 text-xs font-medium text-blue-600 hover:text-blue-700 hover:underline"
        >
          Learn more →
        </Link>
      </div>
    </div>
  );
}
