"use client";
import { useState, FormEvent } from "react";
import Link from "next/link";

export default function ForgotPasswordPage() {
  const [email, setEmail] = useState("");
  const [loading, setLoading] = useState(false);
  const [done, setDone] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      const body = new URLSearchParams({ email });
      const res = await fetch("/api/v1/auth/forgot-password", {
        method: "POST",
        headers: { "Content-Type": "application/x-www-form-urlencoded" },
        body: body.toString(),
      });
      if (!res.ok && res.status !== 204) {
        const data = await res.json().catch(() => ({}));
        setError(data.detail ?? "Something went wrong. Please try again.");
        return;
      }
      setDone(true);
    } finally {
      setLoading(false);
    }
  }

  if (done) {
    return (
      <div className="flex min-h-[60vh] items-center justify-center">
        <div className="w-full max-w-sm rounded-xl border border-slate-200 bg-white p-8 shadow-sm text-center">
          <div className="text-3xl mb-3">📬</div>
          <h1 className="text-lg font-semibold text-slate-800 mb-2">Check your inbox</h1>
          <p className="text-sm text-slate-500 mb-4">
            If an account with that email exists, we sent a password reset link. It expires in 1 hour.
          </p>
          <Link href="/login" className="text-sm text-blue-600 hover:underline">
            Back to login
          </Link>
        </div>
      </div>
    );
  }

  return (
    <div className="flex min-h-[60vh] items-center justify-center">
      <div className="w-full max-w-sm rounded-xl border border-slate-200 bg-white p-8 shadow-sm">
        <h1 className="text-xl font-semibold text-slate-800 mb-2">Forgot password</h1>
        <p className="text-sm text-slate-500 mb-6">
          Enter your email and we&apos;ll send you a reset link.
        </p>

        <form onSubmit={handleSubmit} className="flex flex-col gap-4">
          <div>
            <label className="text-xs font-medium text-slate-500 uppercase tracking-wide">Email</label>
            <input
              type="email"
              required
              autoComplete="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className="mt-1 w-full rounded border border-slate-200 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-400"
              placeholder="you@example.com"
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
            {loading ? "Sending…" : "Send reset link"}
          </button>
        </form>

        <p className="mt-4 text-center text-sm text-slate-500">
          Remembered it?{" "}
          <Link href="/login" className="text-blue-600 hover:underline">
            Sign in
          </Link>
        </p>
      </div>
    </div>
  );
}
