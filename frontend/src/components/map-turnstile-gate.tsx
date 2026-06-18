"use client";

import { useEffect, useState } from "react";
import Turnstile from "react-turnstile";
import useSWR from "swr";
import { fetchCurrentUser, fetchOrg } from "@/lib/api";
import { hasNoAds } from "@/lib/entitlements";

const SESSION_KEY = "map_turnstile_passed";

export function MapTurnstileGate() {
  const { data: me } = useSWR("me", fetchCurrentUser);
  const orgId = me?.org?.id ?? null;
  const { data: org } = useSWR(orgId ? ["org-detail", orgId] : null, () => fetchOrg(orgId!));

  const siteKey = process.env.NEXT_PUBLIC_TURNSTILE_SITE_KEY || "";

  // Initialise from sessionStorage so the overlay never flashes for returning users.
  const [passed, setPassed] = useState(() => {
    if (typeof window === "undefined") return false;
    return sessionStorage.getItem(SESSION_KEY) === "1";
  });

  const isPaidUser = org ? hasNoAds({ tier: org.tier, customFeatures: org.custom_features }) : false;

  // Re-read sessionStorage after hydration (handles SSR mismatch).
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    if (sessionStorage.getItem(SESSION_KEY) === "1") setPassed(true);
  }, []);

  // Nothing to show: paid tier, already passed, data not yet loaded, or no site key.
  if (isPaidUser || passed || !me || !org || !siteKey) return null;

  const handleSuccess = () => {
    sessionStorage.setItem(SESSION_KEY, "1");
    setPassed(true);
  };

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
      <div className="bg-white rounded-lg shadow-xl p-8 max-w-md w-full mx-4">
        <div className="text-center mb-6">
          <h2 className="text-xl font-semibold text-slate-900 mb-2">🗺 Map Access</h2>
          <p className="text-sm text-slate-600">
            Please complete a quick verification to access the interactive map.
          </p>
        </div>

        <div className="flex justify-center mb-6">
          <Turnstile
            sitekey={siteKey}
            onSuccess={handleSuccess}
            theme="light"
            size="normal"
          />
        </div>

        <p className="text-xs text-slate-500 text-center">
          Upgrade to <strong>Simple</strong> or above to skip this step permanently.
        </p>
      </div>
    </div>
  );
}
