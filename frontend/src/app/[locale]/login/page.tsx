"use client";
import { useState, FormEvent, Suspense } from "react";
import Link from "next/link";
import { useSearchParams, useRouter, usePathname } from "next/navigation";
import { mutate } from "swr";
import { useI18n } from "@/i18n/context";

function OAuthButtons({ orContinueWith, next }: { orContinueWith: string; next: string }) {
  const googleHref = `/api/v1/auth/google/authorize?next=${encodeURIComponent(next)}`;
  const linkedinHref = `/api/v1/auth/linkedin/authorize?next=${encodeURIComponent(next)}`;
  return (
    <div className="flex flex-col gap-2 mt-4">
      <div className="flex items-center gap-2 text-xs text-slate-400">
        <div className="flex-1 border-t border-slate-200" />
        <span>{orContinueWith}</span>
        <div className="flex-1 border-t border-slate-200" />
      </div>
      <a
        href={googleHref}
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
        href={linkedinHref}
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

function LoginForm() {
  const searchParams = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();
  const locale = pathname?.split("/")[1] ?? "de";
  const { dict } = useI18n();
  const t = dict.auth.login;

  const rawNext = searchParams?.get("next");
  const next = rawNext && rawNext.startsWith("/") ? rawNext : `/${locale}/app/search`;
  const oauthError = searchParams?.get("oauth_error");

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(oauthError ? t.oauthError : null);
  const [loading, setLoading] = useState(false);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      const res = await fetch("/api/v1/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, password }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        setError(body.detail ?? `Login failed (HTTP ${res.status})`);
        return;
      }
      await mutate("me");
      window.location.href = next;
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="flex min-h-[60vh] items-center justify-center">
      <div className="w-full max-w-sm rounded-xl border border-slate-200 bg-white p-8 shadow-sm">
        <h1 className="text-xl font-semibold text-slate-800 mb-6">{t.title}</h1>

        <form onSubmit={handleSubmit} className="flex flex-col gap-4">
          <div>
            <label className="text-xs font-medium text-slate-500 uppercase tracking-wide">{t.email}</label>
            <input
              type="email"
              required
              autoComplete="email"
              autoFocus
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className="mt-1 w-full rounded border border-slate-200 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-400"
              placeholder="you@example.com"
            />
          </div>
          <div>
            <div className="flex items-center justify-between">
              <label className="text-xs font-medium text-slate-500 uppercase tracking-wide">{t.password}</label>
              <Link href={`/${locale}/forgot-password`} className="text-xs text-blue-600 hover:underline">
                {t.forgotPassword}
              </Link>
            </div>
            <input
              type="password"
              required
              autoComplete="current-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="mt-1 w-full rounded border border-slate-200 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-400"
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
            {loading ? t.signingIn : t.signIn}
          </button>
        </form>

        <OAuthButtons orContinueWith={t.orContinueWith} next={next} />

        <p className="mt-4 text-center text-sm text-slate-500">
          {t.noAccount}{" "}
          <Link href={`/${locale}/register`} className="text-blue-600 hover:underline">
            {t.createOne}
          </Link>
        </p>
      </div>
    </div>
  );
}

export default function LoginPage() {
  return (
    <Suspense>
      <LoginForm />
    </Suspense>
  );
}
