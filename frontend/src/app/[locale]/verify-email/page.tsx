import { Suspense } from "react";
import { VerifyEmailClient } from "./verify-email-client";

export default function VerifyEmailPage() {
  return (
    <Suspense fallback={
      <div className="flex min-h-[60vh] items-center justify-center">
        <p className="text-sm text-slate-500 animate-pulse">Loading…</p>
      </div>
    }>
      <VerifyEmailClient />
    </Suspense>
  );
}
