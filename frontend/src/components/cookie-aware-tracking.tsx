"use client";

import Script from "next/script";
import { useEffect, useState } from "react";

import { readCookieConsent, type CookieConsent } from "@/lib/cookie-consent";

function readConsentState(): CookieConsent | null {
  try {
    return readCookieConsent();
  } catch {
    return null;
  }
}

export function CookieAwareTracking() {
  const [consent, setConsent] = useState<CookieConsent | null>(null);

  useEffect(() => {
    const refresh = () => setConsent(readConsentState());
    refresh();

    window.addEventListener("helvex-cookie-consent-updated", refresh);
    window.addEventListener("storage", refresh);
    return () => {
      window.removeEventListener("helvex-cookie-consent-updated", refresh);
      window.removeEventListener("storage", refresh);
    };
  }, []);

  const gtmId = (process.env.NEXT_PUBLIC_GTM_ID || "").trim();

  return (
    <>
      {consent?.analytics && gtmId && (
        <>
          <Script id="gtm-bootstrap" strategy="afterInteractive">
            {`window.dataLayer = window.dataLayer || [];`}
          </Script>
          <Script
            id="gtm-loader"
            strategy="afterInteractive"
            src={`https://www.googletagmanager.com/gtm.js?id=${encodeURIComponent(gtmId)}`}
          />
        </>
      )}
    </>
  );
}
