"use client";
import { useState, FormEvent, useEffect, useRef, Suspense } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";

function OAuthButtons() {
  return (
    <div className="flex flex-col gap-2 mt-4">
      <div className="flex items-center gap-2 text-xs text-slate-400">
        <div className="flex-1 border-t border-slate-200" />
        <span>or sign up with</span>
        <div className="flex-1 border-t border-slate-200" />
      </div>
      <a
        href="/api/v1/auth/google/authorize"
        className="flex items-center justify-center gap-2 rounded border border-slate-200 px-4 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50 transition-colors"
      >
        <svg width="18" height="18" viewBox="0 0 18 18" aria-hidden="true">
          <path d="M17.64 9.2c0-.637-.057-1.251-.164-1.84H9v3.481h4.844c-.209 1.125-.843 2.078-1.796 2.717v2.258h2.908c1.702-1.567 2.684-3.875 2.684-6.615z" fill="#4285F4"/>
          <path d="M9 18c2.43 0 4.467-.806 5.956-2.184l-2.908-2.258c-.806.54-1.837.86-3.048.86-2.344 0-4.328-1.584-5.036-3.711H.957v2.332A8.997 8.997 0 0 0 9 18z" fill="#34A853"/>
          <path d="M3.964 10.707A5.41 5.41 0 0 1 3.682 9c0-.593.102-1.17.282-1.707V4.961H.957A8.996 8.996 0 0 0 0 9c0 1.452.348 2.827.957 4.039l3.007-2.332z" fill="#FBBC05"/>
          <path d="M9 3.58c1.321 0 2.508.454 3.44 1.345l2.582-2.58C13.463.891 11.426 0 9 0A8.997 8.997 0 0 0 .957 4.961L3.964 7.293C4.672 5.163 6.656 3.58 9 3.58z" fill="#EA4335"/>
        </svg>
        Google
      </a>
      <a
        href="/api/v1/auth/linkedin/authorize"
        className="flex items-center justify-center gap-2 rounded border border-slate-200 px-4 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50 transition-colors"
      >
        <svg width="18" height="18" viewBox="0 0 18 18" fill="#0A66C2" aria-hidden="true">
          <path d="M16.2 0H1.8C.81 0 0 .81 0 1.8v14.4C0 17.19.81 18 1.8 18h14.4c.99 0 1.8-.81 1.8-1.8V1.8C18 .81 17.19 0 16.2 0zM5.4 15.3H2.7V6.75h2.7V15.3zM4.05 5.58a1.575 1.575 0 1 1 0-3.15 1.575 1.575 0 0 1 0 3.15zM15.3 15.3h-2.7v-4.185c0-1.008-.018-2.304-1.404-2.304-1.404 0-1.62 1.098-1.62 2.232V15.3H6.876V6.75h2.592v1.188h.036c.36-.684 1.242-1.404 2.556-1.404 2.736 0 3.24 1.8 3.24 4.14V15.3z"/>
        </svg>
        LinkedIn
      </a>
    </div>
  );
}

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

        {!showResend && <OAuthButtons />}

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
