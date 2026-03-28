import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

/**
 * Auth routing middleware.
 *
 * Public paths (listed below) are always served without authentication.
 * Any path under /app/* requires the session cookie; missing → redirect to
 * /login?next=<path>.
 *
 * Note: this only checks cookie *presence*, not validity. Expired/invalid
 * cookies are rejected server-side by FastAPI when the client-side SWR
 * calls /api/v1/auth/me.
 */

const PUBLIC_PATHS = [
  "/",
  "/login",
  "/register",
  "/verify-email",
  "/forgot-password",
  "/reset-password",
  "/accept-invite",
  "/confirm-email-change",
  "/logout",
];

export function middleware(request: NextRequest) {
  const { pathname } = request.nextUrl;

  // Explicitly allow public paths — never redirect these to login
  if (PUBLIC_PATHS.some((p) => pathname === p || pathname.startsWith(p + "/"))) {
    return NextResponse.next();
  }

  // Protect /app/* routes via session cookie presence
  if (pathname.startsWith("/app/")) {
    const session = request.cookies.get("session");
    if (!session) {
      const url = request.nextUrl.clone();
      url.pathname = "/login";
      url.searchParams.set("next", pathname);
      return NextResponse.redirect(url);
    }
  }

  return NextResponse.next();
}

export const config = {
  // Run on all routes except Next.js internals and static assets
  matcher: ["/((?!_next/static|_next/image|favicon.ico|api/|static/).*)"],
};
