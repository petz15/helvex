import { Suspense } from "react";
import { ResetPasswordClient } from "./reset-password-client";

export default function ResetPasswordPage() {
  return (
    <Suspense fallback={
      <div className="flex min-h-[60vh] items-center justify-center">
        <p className="text-sm text-slate-400">Loading…</p>
      </div>
    }>
      <ResetPasswordClient />
    </Suspense>
  );
}
