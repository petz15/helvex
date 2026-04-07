export const COOKIE_CONSENT_KEY = "helvex_cookie_consent_v3";
const COOKIE_CONSENT_V2_KEY = "helvex_cookie_consent_v2";
const COOKIE_CONSENT_V1_KEY = "helvex_cookie_consent_v1";

export type CookieConsent = {
  essential: true;
  analytics: boolean;
  updatedAt: string;
};

export function defaultCookieConsent(): CookieConsent {
  return {
    essential: true,
    analytics: true,
    updatedAt: new Date().toISOString(),
  };
}

export function parseCookieConsent(raw: string | null): CookieConsent | null {
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw) as Partial<CookieConsent> & { analytics?: unknown };
    return {
      essential: true,
      analytics: Boolean(parsed.analytics),
      updatedAt: typeof parsed.updatedAt === "string" ? parsed.updatedAt : new Date().toISOString(),
    };
  } catch {
    return null;
  }
}

export function readCookieConsent(): CookieConsent | null {
  if (typeof window === "undefined") return null;
  const current = parseCookieConsent(window.localStorage.getItem(COOKIE_CONSENT_KEY));
  if (current) return current;

  // Migrate from v2 (had ads field) or v1.
  const legacyRaw =
    window.localStorage.getItem(COOKIE_CONSENT_V2_KEY) ||
    window.localStorage.getItem(COOKIE_CONSENT_V1_KEY);
  if (!legacyRaw) return null;
  const legacy = parseCookieConsent(legacyRaw);
  if (!legacy) return null;
  window.localStorage.setItem(COOKIE_CONSENT_KEY, JSON.stringify(legacy));
  return legacy;
}

export function writeCookieConsent(consent: CookieConsent): void {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(COOKIE_CONSENT_KEY, JSON.stringify(consent));
  window.dispatchEvent(new Event("helvex-cookie-consent-updated"));
}
