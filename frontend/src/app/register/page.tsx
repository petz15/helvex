"use client";
import { useState, FormEvent, useEffect, useRef, Suspense } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";

const RESEND_COOLDOWN_SECONDS = 60;

function RegisterForm() {
  const searchParams = useSearchParams();
  const inviteToken = searchParams.get("invite");
  const prefillEmail = searchParams.get("email") ?? "";

  const [email, setEmail] = useState(prefillEmail);
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);
  const [loading, setLoading] = useState(false);
  // unverified-duplicate state
  const [showResend, setShowResend] = useState(false);
  const [resendCooldown, setResendCooldown] = useState(0);
  const [resendLoading, setResendLoading] = useState(false);
  const [resendDone, setResendDone] = useState(false);
  const cooldownRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    if (prefillEmail) setEmail(prefillEmail);
  }, [prefillEmail]);

  // Countdown timer for resend cooldown
  useEffect(() => {
    if (resendCooldown <= 0) {
      if (cooldownRef.current) clearInterval(cooldownRef.current);
      return;
    }
    cooldownRef.current = setInterval(() => {
      setResendCooldown(s => {
        if (s <= 1) {
          clearInterval(cooldownRef.current!);
          return 0;
        }
        return s - 1;
      });
    }, 1000);
    return () => { if (cooldownRef.current) clearInterval(cooldownRef.current); };
  }, [resendCooldown]);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setShowResend(false);
    setResendDone(false);
    setLoading(true);
    try {
      const res = await fetch("/api/v1/auth/register", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, password }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        if (body.detail === "email_unverified") {
          setShowResend(true);
          setError(null);
          return;
        }
        setError(body.detail ?? `Registration failed (HTTP ${res.status})`);
        return;
      }
      if (inviteToken) {
        sessionStorage.setItem("pendingInviteToken", inviteToken);
      }
      setDone(true);
    } finally {
      setLoading(false);
    }
  }

  async function handleResend() {
    setResendLoading(true);
    setResendDone(false);
    try {
      await fetch("/api/v1/auth/resend-verification-public", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email }),
      });
      // Always show success (endpoint never reveals whether it actually sent)
      setResendDone(true);
      setResendCooldown(RESEND_COOLDOWN_SECONDS);
    } finally {
      setResendLoading(false);
    }
  }

  if (done) {
    return (
      <div className="flex min-h-[60vh] items-center justify-center">
        <div className="w-full max-w-sm rounded-xl border border-slate-200 bg-white p-8 shadow-sm text-center">
          <div className="text-3xl mb-3">📬</div>
          <h1 className="text-lg font-semibold text-slate-800 mb-2">Check your inbox</h1>
          <p className="text-sm text-slate-500 mb-2">
            We sent a verification link to <strong>{email}</strong>. Click it to activate your account.
          </p>
          <p className="text-xs text-slate-400 mb-4">
            Don&apos;t see it? Check your spam or junk folder.
          </p>
          {inviteToken && (
            <p className="text-xs text-blue-600 mb-4">
              After verifying your email, visit the invite link again to join the organization.
            </p>
          )}
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
        <h1 className="text-xl font-semibold text-slate-800 mb-2">Create an account</h1>
        {inviteToken && (
          <p className="text-sm text-blue-600 mb-4 rounded bg-blue-50 px-3 py-2">
            You were invited to join an organization. Create an account to accept.
          </p>
        )}

        {showResend ? (
          <div className="space-y-3">
            <div className="rounded bg-yellow-50 border border-yellow-200 px-3 py-2 text-sm text-yellow-800">
              This email is registered but not yet verified.
            </div>
            {resendDone ? (
              <div className="rounded bg-green-50 border border-green-200 px-3 py-2 text-sm text-green-800">
                Verification email sent! Check your inbox — and your spam or junk folder.
              </div>
            ) : (
              <button
                type="button"
                onClick={handleResend}
                disabled={resendLoading || resendCooldown > 0}
                className="w-full rounded bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50 transition-colors"
              >
                {resendLoading
                  ? "Sending…"
                  : resendCooldown > 0
                  ? `Resend in ${resendCooldown}s`
                  : "Resend verification email"}
              </button>
            )}
            {resendDone && resendCooldown > 0 && (
              <p className="text-xs text-slate-400 text-center">
                You can request another in {resendCooldown}s
              </p>
            )}
            <button
              type="button"
              onClick={() => { setShowResend(false); setResendDone(false); }}
              className="w-full text-sm text-slate-500 hover:text-slate-700"
            >
              ← Back to registration
            </button>
          </div>
        ) : (
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
            <div>
              <label className="text-xs font-medium text-slate-500 uppercase tracking-wide">Password</label>
              <input
                type="password"
                required
                autoComplete="new-password"
                minLength={8}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                className="mt-1 w-full rounded border border-slate-200 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-400"
                placeholder="Min. 8 characters"
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
              {loading ? "Creating account…" : "Create account"}
            </button>
          </form>
        )}

        <p className="mt-4 text-center text-sm text-slate-500">
          Already have an account?{" "}
          <Link
            href={inviteToken ? `/accept-invite?token=${inviteToken}` : "/login"}
            className="text-blue-600 hover:underline"
          >
            Sign in
          </Link>
        </p>
      </div>
    </div>
  );
}

export default function RegisterPage() {
  return (
    <Suspense>
      <RegisterForm />
    </Suspense>
  );
}
