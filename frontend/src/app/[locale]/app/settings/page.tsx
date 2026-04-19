import { Suspense } from "react";
import { SettingsClient } from "./settings-client";

export default function SettingsPage() {
  return (
    <Suspense fallback={<div className="p-6 text-slate-400 text-sm">Loading settings…</div>}>
      <SettingsClient />
    </Suspense>
  );
}
