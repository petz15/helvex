import { NextRequest, NextResponse } from "next/server";

export async function GET(request: NextRequest) {
  // Call FastAPI to clear the session cookie server-side
  try {
    await fetch(`${process.env.FASTAPI_URL ?? "http://localhost:8000"}/api/v1/auth/logout`, {
      method: "POST",
      headers: { cookie: request.headers.get("cookie") ?? "" },
    });
  } catch {
    // Ignore errors — we clear the cookie client-side regardless
  }

  const response = NextResponse.redirect(new URL("/login", request.url));
  response.cookies.delete("session");
  return response;
}
