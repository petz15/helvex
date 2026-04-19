import { Suspense } from "react";
import { AddressesClient } from "./addresses-client";

export default function AddressesPage() {
  return (
    <Suspense fallback={<div className="mx-auto max-w-3xl px-4 py-8 text-sm text-slate-500">Loading addresses...</div>}>
      <AddressesClient />
    </Suspense>
  );
}
