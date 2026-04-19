import Link from "next/link";
import { CookieSettingsButton } from "@/components/cookie-settings-button";
import { AdBanner } from "@/components/ad-banner";
import { PageViewTracker } from "@/components/page-view-tracker";
import { getDictionary, isLocale } from "@/i18n/dictionaries";
import { notFound } from "next/navigation";

export default async function AppLayout({
  children,
  params,
}: {
  children: React.ReactNode;
  params: Promise<{ locale: string }>;
}) {
  const { locale } = await params;
  if (!isLocale(locale)) notFound();
  const dict = await getDictionary(locale);
  const t = dict.app.footer;

  return (
    <div className="flex min-h-full flex-col">
      <PageViewTracker />
      <AdBanner />
      <div className="flex-1">{children}</div>
      <footer className="border-t border-slate-200 bg-white px-4 py-3 text-xs text-slate-500">
        <div className="mx-auto flex max-w-7xl flex-wrap items-center justify-center gap-3">
          <Link href={`/${locale}/impressum`} className="hover:text-slate-700 hover:underline">{t.impressum}</Link>
          <span>·</span>
          <Link href={`/${locale}/datenschutz`} className="hover:text-slate-700 hover:underline">{t.datenschutz}</Link>
          <span>·</span>
          <Link href={`/${locale}/agb`} className="hover:text-slate-700 hover:underline">{t.agb}</Link>
          <span>·</span>
          <CookieSettingsButton className="hover:text-slate-700 hover:underline" label={t.cookieSettings} />
        </div>
      </footer>
    </div>
  );
}
