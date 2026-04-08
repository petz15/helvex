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
    <div className={`overflow-hidden rounded-xl border border-blue-200 bg-gradient-to-br from-blue-50 to-indigo-50 shadow-sm ${className}`}>
      {/* Accent bar */}
      <div className="h-1 w-full bg-gradient-to-r from-blue-500 to-indigo-500" />
      <div className="p-5 flex flex-col gap-4">
        {/* Header */}
        <div className="flex items-center gap-3">
          <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-blue-600 text-white font-bold text-sm shadow-sm">
            BC
          </div>
          <div>
            <div className="flex items-center gap-1.5">
              <span className="font-semibold text-slate-900">Balogh Consulting</span>
              <span className="rounded-full bg-blue-100 px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-blue-700">
                Sponsor
              </span>
            </div>
            <p className="text-xs text-slate-500 mt-0.5">Business consulting · Zurich</p>
          </div>
        </div>

        {/* Body */}
        <p className="text-sm text-slate-700 leading-relaxed">
          Value first — you decide what it's worth.
        </p>

        {/* CTA */}
        <Link
          href="https://balogh-consulting.ch"
          target="_blank"
          rel="noopener noreferrer"
          className="inline-flex items-center justify-center gap-1.5 rounded-lg bg-blue-600 px-4 py-2 text-sm font-semibold text-white shadow-sm hover:bg-blue-700 transition-colors"
        >
          Visit balogh-consulting.ch →
        </Link>
      </div>
    </div>
  );
}
