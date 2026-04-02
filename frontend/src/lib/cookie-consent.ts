export const COOKIE_CONSENT_KEY = "helvex_cookie_consent_v2";
const COOKIE_CONSENT_LEGACY_KEY = "helvex_cookie_consent_v1";

export type CookieConsent = {
  essential: true;
  analytics: boolean;
  ads: boolean;
  updatedAt: string;
};

export function defaultCookieConsent(): CookieConsent {
  return {
    essential: true,
    analytics: false,
    ads: false,
    updatedAt: new Date().toISOString(),
  };
}

export function parseCookieConsent(raw: string | null): CookieConsent | null {
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw) as Partial<CookieConsent> & { analytics?: unknown; ads?: unknown };
    return {
      essential: true,
      analytics: Boolean(parsed.analytics),
      ads: Boolean(parsed.ads),
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

  // Backward-compatible migration from v1 (analytics-only) consent.
  const legacyRaw = window.localStorage.getItem(COOKIE_CONSENT_LEGACY_KEY);
  if (!legacyRaw) return null;
  const legacy = parseCookieConsent(legacyRaw);
  if (!legacy) return null;
  const migrated: CookieConsent = {
    essential: true,
    analytics: legacy.analytics,
    ads: false,
    updatedAt: legacy.updatedAt,
  };
  window.localStorage.setItem(COOKIE_CONSENT_KEY, JSON.stringify(migrated));
  return migrated;
}

export function writeCookieConsent(consent: CookieConsent): void {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(COOKIE_CONSENT_KEY, JSON.stringify(consent));
  window.dispatchEvent(new Event("helvex-cookie-consent-updated"));
}
