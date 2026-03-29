"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";

const CONSENT_KEY = "helvex_cookie_consent_v1";

type CookieConsent = {
  essential: true;
  analytics: boolean;
  updatedAt: string;
};

export function CookieBanner() {
  const [ready, setReady] = useState(false);
  const [visible, setVisible] = useState(false);
  const [analytics, setAnalytics] = useState(false);

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
      const existing = window.localStorage.getItem(CONSENT_KEY);
      setVisible(!existing);
    } catch {
      setVisible(true);
    } finally {
      setReady(true);
    }

    const onOpen = () => setVisible(true);
    window.addEventListener("helvex-open-cookie-banner", onOpen);
    return () => window.removeEventListener("helvex-open-cookie-banner", onOpen);
  }, []);

  if (!ready || !visible) return null;

  function saveAndClose(value: CookieConsent) {
    try {
      window.localStorage.setItem(CONSENT_KEY, JSON.stringify(value));
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
            <h2 className="text-sm font-semibold text-slate-900">Cookie-Hinweis</h2>
            <p className="text-xs sm:text-sm text-slate-600 max-w-2xl">
              Wir verwenden notwendige Cookies fuer Login, Sicherheit und den technischen Betrieb der Website.
              Optionale Analyse-Cookies setzen wir nur mit Ihrer Zustimmung.
              Details finden Sie in unserem <Link href="/datenschutz" className="text-blue-700 hover:text-blue-900 underline">Datenschutz</Link>.
            </p>
            <div className="flex items-center gap-2 text-xs text-slate-600">
              <input
                id="analytics-consent"
                type="checkbox"
                checked={analytics}
                onChange={(e) => setAnalytics(e.target.checked)}
                className="h-3.5 w-3.5 rounded border-slate-300"
              />
              <label htmlFor="analytics-consent">Optionale Analyse-Cookies erlauben</label>
            </div>
          </div>
          <div className="flex flex-wrap gap-2 sm:justify-end">
            <button
              type="button"
              onClick={() => saveAndClose({ essential: true, analytics: false, updatedAt: new Date().toISOString() })}
              className="px-3 py-1.5 rounded-lg border border-slate-200 text-xs sm:text-sm text-slate-700 hover:bg-slate-50 transition-colors"
            >
              Nur notwendige
            </button>
            <button
              type="button"
              onClick={() => saveAndClose(consent)}
              className="px-3 py-1.5 rounded-lg bg-blue-600 text-white text-xs sm:text-sm hover:bg-blue-700 transition-colors"
            >
              Auswahl speichern
            </button>
            <button
              type="button"
              onClick={() => saveAndClose({ essential: true, analytics: true, updatedAt: new Date().toISOString() })}
              className="px-3 py-1.5 rounded-lg bg-slate-900 text-white text-xs sm:text-sm hover:bg-slate-800 transition-colors"
            >
              Alle akzeptieren
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
