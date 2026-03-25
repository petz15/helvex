"use client";
import { useState, FormEvent } from "react";
import { useSearchParams } from "next/navigation";
import Link from "next/link";

export function ResetPasswordClient() {
  const params = useSearchParams();
  const token = params.get("token");

  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [loading, setLoading] = useState(false);
  const [done, setDone] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    if (password !== confirm) {
      setError("Passwords do not match.");
      return;
    }
    setError(null);
    setLoading(true);
    try {
      const res = await fetch("/api/v1/auth/reset-password", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token, new_password: password }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        setError(body.detail ?? "Reset failed. The link may have expired.");
        return;
      }
      setDone(true);
    } finally {
      setLoading(false);
    }
  }

  if (!token) {
    return (
      <div className="flex min-h-[60vh] items-center justify-center">
        <div className="w-full max-w-sm rounded-xl border border-slate-200 bg-white p-8 shadow-sm text-center">
          <div className="text-3xl mb-3">❌</div>
          <h1 className="text-lg font-semibold text-slate-800 mb-2">Invalid link</h1>
          <p className="text-sm text-slate-500 mb-4">No reset token found in the URL.</p>
          <Link href="/forgot-password" className="text-sm text-blue-600 hover:underline">
            Request a new link
          </Link>
        </div>
      </div>
    );
  }

  if (done) {
    return (
      <div className="flex min-h-[60vh] items-center justify-center">
        <div className="w-full max-w-sm rounded-xl border border-slate-200 bg-white p-8 shadow-sm text-center">
          <div className="text-3xl mb-3">✅</div>
          <h1 className="text-lg font-semibold text-slate-800 mb-2">Password updated</h1>
          <p className="text-sm text-slate-500 mb-4">You can now sign in with your new password.</p>
          <Link
            href="/login"
            className="inline-block rounded bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 transition-colors"
          >
            Sign in →
          </Link>
        </div>
      </div>
    );
  }

  return (
    <div className="flex min-h-[60vh] items-center justify-center">
      <div className="w-full max-w-sm rounded-xl border border-slate-200 bg-white p-8 shadow-sm">
        <h1 className="text-xl font-semibold text-slate-800 mb-2">Set new password</h1>
        <p className="text-sm text-slate-500 mb-6">Choose a password with at least 8 characters.</p>

        <form onSubmit={handleSubmit} className="flex flex-col gap-4">
          <div>
            <label className="text-xs font-medium text-slate-500 uppercase tracking-wide">New password</label>
            <input
              type="password"
              required
              minLength={8}
              autoComplete="new-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="mt-1 w-full rounded border border-slate-200 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-400"
              placeholder="Min. 8 characters"
            />
          </div>
          <div>
            <label className="text-xs font-medium text-slate-500 uppercase tracking-wide">Confirm password</label>
            <input
              type="password"
              required
              minLength={8}
              autoComplete="new-password"
              value={confirm}
              onChange={(e) => setConfirm(e.target.value)}
              className="mt-1 w-full rounded border border-slate-200 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-400"
              placeholder="Repeat your password"
            />
          </div>

          {error && (
            <p className="rounded bg-red-50 border border-red-200 px-3 py-2 text-sm text-red-700">{error}</p>
          )}

          <button
            type="submit"
            disabled={loading}
            className="rounded bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50 transition-colors"
          >
            {loading ? "Saving…" : "Set new password"}
          </button>
        </form>
      </div>
    </div>
  );
}
