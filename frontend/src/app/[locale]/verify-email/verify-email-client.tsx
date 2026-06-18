"use client";
import { useEffect, useState } from "react";
import { useSearchParams, usePathname } from "next/navigation";
import Link from "next/link";
import { useI18n } from "@/i18n/context";

export function VerifyEmailClient() {
  const params = useSearchParams();
  const token = params?.get("token");
  const pathname = usePathname();
  const locale = pathname?.split("/")[1] ?? "de";
  const { dict } = useI18n();
  const t = dict.auth.verifyEmail;

  const [state, setState] = useState<"loading" | "success" | "error">("loading");
  const [message, setMessage] = useState("");

  useEffect(() => {
    if (!token) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setState("error");
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setMessage(t.noToken);
      return;
    }
    fetch(`/api/v1/auth/verify-email?token=${encodeURIComponent(token)}`)
      .then(async (res) => {
        if (res.ok) {
          setState("success");
        } else {
          const body = await res.json().catch(() => ({}));
          setState("error");
          setMessage(body.detail ?? t.failed);
        }
      })
      .catch(() => {
        setState("error");
        setMessage(t.networkError);
      });
  }, [token]);

  return (
    <div className="flex min-h-[60vh] items-center justify-center">
      <div className="w-full max-w-sm rounded-xl border border-slate-200 bg-white p-8 shadow-sm text-center">
        {state === "loading" && (
          <>
            <div className="text-3xl mb-3 animate-pulse">✉️</div>
            <p className="text-sm text-slate-500">{t.verifying}</p>
          </>
        )}
        {state === "success" && (
          <>
            <div className="text-3xl mb-3">✅</div>
            <h1 className="text-lg font-semibold text-slate-800 mb-2">{t.successTitle}</h1>
            <p className="text-sm text-slate-500 mb-4">{t.successMessage}</p>
            <Link
              href={`/${locale}/app/search`}
              className="inline-block rounded bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 transition-colors"
            >
              {t.openSearch}
            </Link>
          </>
        )}
        {state === "error" && (
          <>
            <div className="text-3xl mb-3">❌</div>
            <h1 className="text-lg font-semibold text-slate-800 mb-2">{t.failTitle}</h1>
            <p className="text-sm text-slate-500 mb-4">{message}</p>
            <Link href={`/${locale}/login`} className="text-sm text-blue-600 hover:underline">
              {t.backToLogin}
            </Link>
          </>
        )}
      </div>
    </div>
  );
}
