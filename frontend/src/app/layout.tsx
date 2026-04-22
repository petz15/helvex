import type { Metadata } from "next";
import "./globals.css";
import { headers } from "next/headers";

const googleSiteVerification = (process.env.NEXT_PUBLIC_GOOGLE_SITE_VERIFICATION || "").trim();

export const metadata: Metadata = {
  title: "Helvex — Swiss Company Intelligence",
  description: "Search, qualify, and track Swiss companies from the commercial register. Powered by live SHAB data and AI classification.",
  verification: googleSiteVerification ? { google: [googleSiteVerification] } : undefined,
  icons: {
    icon: [
      { url: "/favicon.ico", sizes: "any" },
      { url: "/favicon.svg", type: "image/svg+xml" },
    ],
  },
};

export default async function RootLayout({ children }: { children: React.ReactNode }) {
  const headersList = await headers();
  const locale = headersList.get("x-locale") ?? "de";

  return (
    <html lang={locale} className="h-full antialiased">
      <body className="min-h-full flex flex-col bg-slate-50 text-slate-900">
        {children}
      </body>
    </html>
  );
}
