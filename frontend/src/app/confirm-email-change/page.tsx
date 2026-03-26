"use client";
import { useEffect, useState, Suspense } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";

function ConfirmEmailChangeContent() {
  const searchParams = useSearchParams();
  const token = searchParams.get("token");
  const [status, setStatus] = useState<"loading" | "success" | "error">("loading");
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  useEffect(() => {
    if (!token) {
      setErrorMsg("No confirmation token found in the link.");
      setStatus("error");
      return;
    }

    fetch("/api/v1/auth/confirm-email-change", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token }),
    })
      .then(async (res) => {
        if (res.ok || res.status === 204) {
          setStatus("success");
        } else {
          const body = await res.json().catch(() => ({}));
          setErrorMsg(body.detail ?? `Failed (HTTP ${res.status})`);
          setStatus("error");
        }
      })
      .catch(() => {
        setErrorMsg("Network error. Please try again.");
        setStatus("error");
      });
  }, [token]);

  if (status === "loading") {
    return (
      <div className="flex min-h-[60vh] items-center justify-center">
        <div className="w-full max-w-sm rounded-xl border border-slate-200 bg-white p-8 shadow-sm text-center">
          <div className="w-8 h-8 border-4 border-blue-200 border-t-blue-600 rounded-full animate-spin mx-auto mb-4" />
          <p className="text-sm text-slate-500">Confirming your email address…</p>
        </div>
      </div>
    );
  }

  if (status === "success") {
    return (
      <div className="flex min-h-[60vh] items-center justify-center">
        <div className="w-full max-w-sm rounded-xl border border-slate-200 bg-white p-8 shadow-sm text-center">
          <div className="text-3xl mb-3">✓</div>
          <h1 className="text-lg font-semibold text-slate-800 mb-2">Email address updated</h1>
          <p className="text-sm text-slate-500 mb-4">Your email address has been changed successfully.</p>
          <Link
            href="/app/account"
            className="inline-block rounded bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 transition-colors"
          >
            Go to Account
          </Link>
        </div>
      </div>
    );
  }

  return (
    <div className="flex min-h-[60vh] items-center justify-center">
      <div className="w-full max-w-sm rounded-xl border border-red-200 bg-white p-8 shadow-sm text-center">
        <div className="text-3xl mb-3">✗</div>
        <h1 className="text-lg font-semibold text-red-700 mb-2">Confirmation failed</h1>
        <p className="text-sm text-red-600 mb-4">{errorMsg}</p>
        <Link href="/app/account" className="text-sm text-blue-600 hover:underline">
          Back to Account
        </Link>
      </div>
    </div>
  );
}

export default function ConfirmEmailChangePage() {
  return (
    <Suspense>
      <ConfirmEmailChangeContent />
    </Suspense>
  );
}
