"use client";
import { useEffect, useState, Suspense } from "react";
import Link from "next/link";
import { useSearchParams, usePathname } from "next/navigation";
import { useI18n } from "@/i18n/context";

function ConfirmEmailChangeContent() {
  const searchParams = useSearchParams();
  const token = searchParams.get("token");
  const pathname = usePathname();
  const locale = pathname.split("/")[1] ?? "de";
  const { dict } = useI18n();
  const t = dict.auth.confirmEmailChange;

  const [status, setStatus] = useState<"loading" | "success" | "error">("loading");
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  useEffect(() => {
    if (!token) {
      setErrorMsg(t.noToken);
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
        setErrorMsg(t.networkError);
        setStatus("error");
      });
  }, [token]);

  if (status === "loading") {
    return (
      <div className="flex min-h-[60vh] items-center justify-center">
        <div className="w-full max-w-sm rounded-xl border border-slate-200 bg-white p-8 shadow-sm text-center">
          <div className="w-8 h-8 border-4 border-blue-200 border-t-blue-600 rounded-full animate-spin mx-auto mb-4" />
          <p className="text-sm text-slate-500">{t.confirming}</p>
        </div>
      </div>
    );
  }

  if (status === "success") {
    return (
      <div className="flex min-h-[60vh] items-center justify-center">
        <div className="w-full max-w-sm rounded-xl border border-slate-200 bg-white p-8 shadow-sm text-center">
          <div className="text-3xl mb-3">✓</div>
          <h1 className="text-lg font-semibold text-slate-800 mb-2">{t.successTitle}</h1>
          <p className="text-sm text-slate-500 mb-4">{t.successMessage}</p>
          <Link
            href={`/${locale}/app/account`}
            className="inline-block rounded bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 transition-colors"
          >
            {t.goToAccount}
          </Link>
        </div>
      </div>
    );
  }

  return (
    <div className="flex min-h-[60vh] items-center justify-center">
      <div className="w-full max-w-sm rounded-xl border border-red-200 bg-white p-8 shadow-sm text-center">
        <div className="text-3xl mb-3">✗</div>
        <h1 className="text-lg font-semibold text-red-700 mb-2">{t.failTitle}</h1>
        <p className="text-sm text-red-600 mb-4">{errorMsg}</p>
        <Link href={`/${locale}/app/account`} className="text-sm text-blue-600 hover:underline">
          {t.backToAccount}
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
