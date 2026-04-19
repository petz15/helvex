"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { usePathname } from "next/navigation";
import { useI18n } from "@/i18n/context";

import {
  defaultCookieConsent,
  readCookieConsent,
  writeCookieConsent,
  type CookieConsent,
} from "@/lib/cookie-consent";

export function CookieBanner() {
  const [ready, setReady] = useState(false);
  const [visible, setVisible] = useState(false);
  const [analytics, setAnalytics] = useState(defaultCookieConsent().analytics);
  const { dict } = useI18n();
  const t = dict.cookie;

  const pathname = usePathname();
  const locale = pathname.split("/")[1] ?? "de";

  const consent = useMemo<CookieConsent>(
    () => ({
      essential: true,
      analytics,
      updatedAt: new Date().toISOString(),
    }),
    [analytics],
  );

  useEffect(() => {
    try {
      const existing = readCookieConsent();
      if (existing) {
        setAnalytics(existing.analytics);
      }
      setVisible(!existing);
    } catch {
      setVisible(true);
    } finally {
      setReady(true);
    }

    const onOpen = () => {
      const existing = readCookieConsent() ?? defaultCookieConsent();
      setAnalytics(existing.analytics);
      setVisible(true);
    };
    window.addEventListener("helvex-open-cookie-banner", onOpen);
    return () => window.removeEventListener("helvex-open-cookie-banner", onOpen);
  }, []);

  if (!ready || !visible) return null;

  function saveAndClose(value: CookieConsent) {
    try {
      writeCookieConsent(value);
    } catch {
      // Keep banner dismiss behavior even if storage is unavailable.
    }
    setVisible(false);
  }

  return (
    <div className="fixed bottom-4 left-1/2 z-[100] w-[min(960px,calc(100vw-2rem))] -translate-x-1/2 rounded-xl border border-slate-200 bg-white/95 backdrop-blur shadow-xl">
      <div className="p-4 sm:p-5">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
          <div className="space-y-2">
            <h2 className="text-sm font-semibold text-slate-900">{t.title}</h2>
            <p className="text-xs sm:text-sm text-slate-600 max-w-2xl">
              {t.description}{" "}
              <Link href={`/${locale}/datenschutz`} className="text-blue-700 hover:text-blue-900 underline">
                {t.privacyLink}
              </Link>
              .
            </p>
            <div className="space-y-1">
              <div className="flex items-center gap-2 text-xs text-slate-600">
                <input
                  id="analytics-consent"
                  type="checkbox"
                  checked={analytics}
                  onChange={(e) => setAnalytics(e.target.checked)}
                  className="h-3.5 w-3.5 rounded border-slate-300"
                />
                <label htmlFor="analytics-consent">{t.analyticsLabel}</label>
              </div>
            </div>
          </div>
          <div className="flex flex-wrap gap-2 sm:justify-end">
            <button
              type="button"
              onClick={() => saveAndClose(consent)}
              className="px-3 py-1.5 rounded-lg bg-blue-600 text-white text-xs sm:text-sm hover:bg-blue-700 transition-colors"
            >
              {t.saveChoice}
            </button>
            <button
              type="button"
              onClick={() => saveAndClose({ essential: true, analytics: true, updatedAt: new Date().toISOString() })}
              className="px-3 py-1.5 rounded-lg bg-slate-900 text-white text-xs sm:text-sm hover:bg-slate-800 transition-colors"
            >
              {t.acceptAll}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
