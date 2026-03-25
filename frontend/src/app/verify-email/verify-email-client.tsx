"use client";
import { useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import Link from "next/link";

export function VerifyEmailClient() {
  const params = useSearchParams();
  const token = params.get("token");
  const [state, setState] = useState<"loading" | "success" | "error">("loading");
  const [message, setMessage] = useState("");

  useEffect(() => {
    if (!token) {
      setState("error");
      setMessage("No verification token provided.");
      return;
    }
    fetch(`/api/v1/auth/verify-email?token=${encodeURIComponent(token)}`)
      .then(async (res) => {
        if (res.ok) {
          setState("success");
        } else {
          const body = await res.json().catch(() => ({}));
          setState("error");
          setMessage(body.detail ?? "Verification failed.");
        }
      })
      .catch(() => {
        setState("error");
        setMessage("Network error. Please try again.");
      });
  }, [token]);

  return (
    <div className="flex min-h-[60vh] items-center justify-center">
      <div className="w-full max-w-sm rounded-xl border border-slate-200 bg-white p-8 shadow-sm text-center">
        {state === "loading" && (
          <>
            <div className="text-3xl mb-3 animate-pulse">✉️</div>
            <p className="text-sm text-slate-500">Verifying your email…</p>
          </>
        )}
        {state === "success" && (
          <>
            <div className="text-3xl mb-3">✅</div>
            <h1 className="text-lg font-semibold text-slate-800 mb-2">Email verified!</h1>
            <p className="text-sm text-slate-500 mb-4">Your account is now active.</p>
            <Link
              href="/app/dashboard"
              className="inline-block rounded bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 transition-colors"
            >
              Open dashboard →
            </Link>
          </>
        )}
        {state === "error" && (
          <>
            <div className="text-3xl mb-3">❌</div>
            <h1 className="text-lg font-semibold text-slate-800 mb-2">Verification failed</h1>
            <p className="text-sm text-slate-500 mb-4">{message}</p>
            <Link href="/login" className="text-sm text-blue-600 hover:underline">
              Back to login
            </Link>
          </>
        )}
      </div>
    </div>
  );
}
