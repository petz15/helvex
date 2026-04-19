"use client";

import { useMemo } from "react";
import { useSearchParams } from "next/navigation";
import { AddressBookManager } from "@/components/billing/address-book-manager";
import { useI18n } from "@/i18n/context";

export function AddressesClient() {
  const searchParams = useSearchParams();
  const { dict } = useI18n();
  const t = dict.app.addresses;

  const returnTo = useMemo(() => {
    const raw = searchParams.get("return_to");
    if (!raw) return null;
    if (!raw.startsWith("/app/")) return null;
    return raw;
  }, [searchParams]);

  return (
    <div className="mx-auto max-w-3xl px-4 py-8 space-y-5">
      <div>
        <h1 className="text-2xl font-bold text-slate-900">{t.title}</h1>
        <p className="text-sm text-slate-500 mt-1">{t.subtitle}</p>
      </div>

      {returnTo && (
        <a href={returnTo} className="inline-flex items-center rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs font-medium text-slate-700 hover:bg-slate-50">
          {t.backToCheckout}
        </a>
      )}

      <AddressBookManager returnTo={returnTo} />
    </div>
  );
}
