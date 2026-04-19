import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

const SUPPORTED_LOCALES = ["de", "fr", "it", "en"] as const;
type Locale = (typeof SUPPORTED_LOCALES)[number];
const DEFAULT_LOCALE: Locale = "de";

// Paths that bypass locale handling entirely (served outside [locale] segment)
const BYPASS_PREFIXES = [
  "/api/",
  "/_next/",
  "/health",
  "/static/",
  "/apple-icon",
  "/icon",
  "/manifest.webmanifest",
  "/favicon.ico",
];

// Page paths (relative to locale) that are publicly accessible without auth
const PUBLIC_PAGE_PATHS = [
  "/",
  "/demo",
  "/login",
  "/logout",
  "/register",
  "/verify-email",
  "/forgot-password",
  "/reset-password",
  "/accept-invite",
  "/confirm-email-change",
  "/impressum",
  "/datenschutz",
  "/agb",
];

function extractLocale(pathname: string): Locale | null {
  const segment = pathname.split("/")[1];
  if (SUPPORTED_LOCALES.includes(segment as Locale)) return segment as Locale;
  return null;
}

export function proxy(request: NextRequest) {
  const { pathname } = request.nextUrl;

  // Skip paths that live outside the [locale] routing tree
  if (BYPASS_PREFIXES.some((p) => pathname.startsWith(p) || pathname === p.slice(0, -1))) {
    return NextResponse.next();
  }

  const locale = extractLocale(pathname);

  // No locale prefix → redirect to default locale
  if (!locale) {
    const target = new URL(`/${DEFAULT_LOCALE}${pathname}`, request.url);
    target.search = request.nextUrl.search;
    return NextResponse.redirect(target);
  }

  // Strip locale prefix for the remaining checks (e.g. "/de/login" → "/login")
  const stripped = pathname.slice(locale.length + 1) || "/";

  // Propagate locale to root layout via request header
  const requestHeaders = new Headers(request.headers);
  requestHeaders.set("x-locale", locale);

  // Public pages — no auth required
  const isPublic = PUBLIC_PAGE_PATHS.some(
    (p) => stripped === p || stripped.startsWith(p === "/" ? "//" : p + "/")
  );
  if (isPublic) {
    return NextResponse.next({ request: { headers: requestHeaders } });
  }

  // Auth check
  const session = request.cookies.get("session");
  if (!session) {
    const loginUrl = new URL(`/${locale}/login`, request.url);
    loginUrl.searchParams.set("next", pathname);
    return NextResponse.redirect(loginUrl);
  }

  return NextResponse.next({ request: { headers: requestHeaders } });
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico|apple-icon|icon).*)"],
};
