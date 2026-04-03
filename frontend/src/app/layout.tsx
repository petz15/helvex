import type { Metadata } from "next";
import "./globals.css";
import { NavBar } from "@/components/nav-bar";
import { CookieBanner } from "../components/cookie-banner";
import { CookieAwareTracking } from "@/components/cookie-aware-tracking";

const googleSiteVerification = (process.env.NEXT_PUBLIC_GOOGLE_SITE_VERIFICATION || "").trim();

export const metadata: Metadata = {
  title: "Helvex — Swiss Company Intelligence",
  description: "Search, qualify, and track Swiss companies from the commercial register. Powered by live SHAB data and AI classification.",
  verification: googleSiteVerification ? { google: [googleSiteVerification] } : undefined,
  icons: {
    icon: [
      { url: "/favicon.svg", type: "image/svg+xml" },
      { url: "/icon", type: "image/png", sizes: "32x32" },
    ],
    apple: "/apple-icon",
  },
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className="h-full antialiased">
      <body className="min-h-full flex flex-col bg-slate-50 text-slate-900">
        <CookieAwareTracking />
        <NavBar />
        <main className="flex-1">{children}</main>
        <CookieBanner />
      </body>
    </html>
  );
}
