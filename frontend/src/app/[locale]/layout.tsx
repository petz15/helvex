import { notFound } from "next/navigation";
import { NavBar } from "@/components/nav-bar";
import { CookieBanner } from "@/components/cookie-banner";
import { CookieAwareTracking } from "@/components/cookie-aware-tracking";
import { I18nProvider } from "@/i18n/context";
import { getDictionary, isLocale } from "@/i18n/dictionaries";

export default async function LocaleLayout({
  children,
  params,
}: {
  children: React.ReactNode;
  params: Promise<{ locale: string }>;
}) {
  const { locale } = await params;
  if (!isLocale(locale)) notFound();

  const dict = await getDictionary(locale);

  return (
    <I18nProvider dict={dict} locale={locale}>
      <CookieAwareTracking />
      <NavBar />
      <main className="flex-1">{children}</main>
      <CookieBanner />
    </I18nProvider>
  );
}
