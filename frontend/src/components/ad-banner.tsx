"use client";

import useSWR from "swr";
import { fetchCurrentUser, fetchOrg } from "@/lib/api";
import { hasNoAds } from "@/lib/entitlements";

export function AdBanner() {
  const { data: me } = useSWR("me", fetchCurrentUser);
  const orgId = me?.org?.id ?? null;
  const { data: org } = useSWR(orgId ? ["org-detail", orgId] : null, () => fetchOrg(orgId!));

  if (!me || !org) return null;
  if (hasNoAds({ tier: org.tier, customFeatures: org.custom_features })) return null;

  return (
    <div className="mx-auto mt-3 w-full max-w-7xl px-4">
      <div className="rounded-xl border border-amber-200 bg-gradient-to-r from-amber-50 to-orange-50 px-4 py-3 text-xs text-amber-900">
        Sponsored placement: Upgrade to Simple or above to remove ads from your workspace.
      </div>
    </div>
  );
}
