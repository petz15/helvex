"use client";

import { useMemo } from "react";
import { useSearchParams } from "next/navigation";
import { AddressBookManager } from "@/components/billing/address-book-manager";

export function AddressesClient() {
  const searchParams = useSearchParams();
  const returnTo = useMemo(() => {
    const raw = searchParams.get("return_to");
    if (!raw) return null;
    if (!raw.startsWith("/app/")) return null;
    return raw;
  }, [searchParams]);

  return (
    <div className="mx-auto max-w-3xl px-4 py-8 space-y-5">
      <div>
        <h1 className="text-2xl font-bold text-slate-900">Billing Addresses</h1>
        <p className="text-sm text-slate-500 mt-1">Save multiple addresses and choose which one is used by default in checkout.</p>
      </div>

      {returnTo && (
        <a href={returnTo} className="inline-flex items-center rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs font-medium text-slate-700 hover:bg-slate-50">
          Back to checkout
        </a>
      )}

      <AddressBookManager returnTo={returnTo} />
    </div>
  );
}
