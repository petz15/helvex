"use client";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import useSWR from "swr";
import { cn } from "@/lib/utils";
import { Search, Cog, Database, Activity, UserCircle, Shield, CreditCard, Menu, X, Tag, Globe, ChevronDown, Building2, Users } from "lucide-react";
import { fetchCurrentUser } from "@/lib/api";
import { HelvexMark } from "@/components/helvex-logo";
import { useI18n } from "@/i18n/context";
import { SUPPORTED_LOCALES } from "@/i18n/locales";
import { useState, useRef, useEffect } from "react";

const AUTH_PATHS = ["/login", "/register", "/verify-email", "/forgot-password", "/reset-password", "/accept-invite"];

function useLocale() {
  const pathname = usePathname();
  const segment = pathname?.split("/")[1] ?? "";
  return SUPPORTED_LOCALES.includes(segment as (typeof SUPPORTED_LOCALES)[number])
    ? (segment as (typeof SUPPORTED_LOCALES)[number])
    : "de" as const;
}

function useLocalePath(path: string) {
  const locale = useLocale();
  return `/${locale}${path}`;
}

const LOCALE_LABELS: Record<string, string> = { de: "DE", fr: "FR", it: "IT", en: "EN" };

export function NavBar() {
  const pathname = usePathname();
  const router = useRouter();
  const [mobileOpen, setMobileOpen] = useState(false);
  const [accountMenuOpen, setAccountMenuOpen] = useState(false);
  const accountMenuRef = useRef<HTMLDivElement>(null);
  const locale = useLocale();
  const { dict } = useI18n();
  const t = dict.nav;

  useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      if (accountMenuRef.current && !accountMenuRef.current.contains(event.target as Node)) {
        setAccountMenuOpen(false);
      }
    }
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  const { data: me, isLoading } = useSWR("me", fetchCurrentUser, {
    shouldRetryOnError: false,
    revalidateOnMount: true,
    revalidateOnFocus: false,
  });

  // Strip locale prefix for AUTH_PATHS check
  const strippedPath = (pathname ?? "").replace(/^\/(de|fr|it|en)/, "");
  if (AUTH_PATHS.some((p) => strippedPath.startsWith(p))) return null;

  const loggedIn = !isLoading && !!me;
  const loggedOut = !isLoading && !me;

  const NAV_MAIN = [
    { href: `/${locale}/app/search`, label: t.search ?? "Search", icon: Search },
    { href: `/${locale}/app/companies`, label: t.companies ?? "Companies", icon: Building2 },
    { href: `/${locale}/app/people`, label: "People", icon: Users },
    { href: `/${locale}/app/jobs`, label: t.jobs, icon: Activity },
  ];

  const NAV_ADMIN = [
    { href: `/${locale}/app/collection`, label: t.collection, icon: Database },
    { href: `/${locale}/app/settings`, label: t.settings, icon: Cog },
  ];

  const MARKETING_NAV = [
    { href: `/${locale}/#features`, label: t.features },
    { href: `/${locale}/#pricing`, label: t.pricing },
  ];

  function switchLocale(newLocale: string) {
    // Replace the locale segment in the current path
    const newPath = (pathname ?? "").replace(/^\/(de|fr|it|en)/, `/${newLocale}`);
    router.push(newPath || `/${newLocale}`);
  }

  const closeMobile = () => setMobileOpen(false);

  return (
    <>
      <header className="h-20 bg-white border-b border-slate-200 flex items-center px-6 shrink-0 z-40 shadow-sm">
        {/* Logo */}
        <Link
          href={loggedIn ? `/${locale}/app/search` : `/${locale}`}
          className="flex items-center gap-2 font-bold text-blue-600 mr-4 tracking-tight shrink-0"
          onClick={closeMobile}
        >
          <HelvexMark size={28} />
          <span className="text-2xl">Helvex</span>
        </Link>


        {/* Logged-in: app nav — desktop only */}
        {loggedIn && (
          <nav className="hidden md:flex items-center gap-0.5">
            {NAV_MAIN.map(({ href, label, icon: Icon }) => {
              const active = pathname === href || pathname?.startsWith(href + "/");
              return (
                <Link
                  key={href}
                  href={href}
                  className={cn(
                    "flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm transition-colors",
                    active
                      ? "bg-blue-600 text-white font-medium"
                      : "text-slate-600 hover:bg-slate-100 hover:text-slate-900"
                  )}
                >
                  <Icon size={14} />
                  {label}
                </Link>
              );
            })}
            {me?.org?.tier === "free" && (
              <Link
                href={`/${locale}/app/pricing`}
                className={cn(
                  "flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm transition-colors",
                  pathname === `/${locale}/app/pricing`
                    ? "bg-blue-600 text-white font-medium"
                    : "text-slate-600 hover:bg-slate-100 hover:text-slate-900"
                )}
              >
                <Tag size={14} />
                {t.pricing}
              </Link>
            )}
            {me?.is_superadmin && (
              <>
                <div className="w-px h-4 bg-slate-200 mx-1" />
                {NAV_ADMIN.map(({ href, label, icon: Icon }) => {
                  const active = pathname === href || pathname?.startsWith(href + "/");
                  return (
                    <Link
                      key={href}
                      href={href}
                      className={cn(
                        "flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm transition-colors",
                        active
                          ? "bg-blue-600 text-white font-medium"
                          : "text-slate-600 hover:bg-slate-100 hover:text-slate-900"
                      )}
                    >
                      <Icon size={14} />
                      {label}
                    </Link>
                  );
                })}
              </>
            )}
          </nav>
        )}

        {/* Logged-out: marketing nav — desktop only */}
        {loggedOut && (
          <nav className="hidden md:flex items-center gap-1">
            {MARKETING_NAV.map(({ href, label }) => (
              <Link
                key={href}
                href={href}
                className="px-3 py-1.5 rounded-lg text-sm text-slate-600 hover:bg-slate-100 hover:text-slate-900 transition-colors"
              >
                {label}
              </Link>
            ))}
          </nav>
        )}

        <div className="ml-auto flex items-center gap-3">
          {/* Language switcher — symbol only, dropdown on hover */}
          <div className="hidden md:block relative group">
            <button className="p-2 text-slate-600 hover:text-slate-900 rounded-lg hover:bg-slate-100 transition-colors">
              <Globe size={18} />
            </button>
            {/* No gap between button and panel — pt-1 (not mt-1) keeps the hoverable
                area contiguous so the dropdown doesn't vanish while crossing into it. */}
            <div className="absolute right-0 top-full hidden group-hover:block pt-1 z-50">
              <div className="flex flex-col bg-white border border-slate-200 rounded-lg shadow-lg">
                {SUPPORTED_LOCALES.map((loc) => (
                  <button
                    key={loc}
                    onClick={() => switchLocale(loc)}
                    className={cn(
                      "px-4 py-2 text-sm text-left transition-colors whitespace-nowrap",
                      loc === locale
                        ? "bg-blue-100 text-blue-700 font-medium"
                        : "text-slate-700 hover:bg-slate-100"
                    )}
                  >
                    {LOCALE_LABELS[loc]}
                  </button>
                ))}
              </div>
            </div>
          </div>

          {/* Logged-in: admin + account menu — desktop only */}
          {loggedIn && (
            <>
              {me?.is_superadmin && (
                <Link
                  href={`/${locale}/app/admin`}
                  className={cn(
                    "hidden md:flex items-center gap-1.5 px-3 py-2 rounded-lg text-sm transition-colors",
                    pathname?.startsWith(`/${locale}/app/admin`)
                      ? "bg-purple-600 text-white font-medium"
                      : "text-purple-600 hover:bg-purple-50"
                  )}
                >
                  <Shield size={16} />
                  {t.admin}
                </Link>
              )}
              {/* Account dropdown */}
              <div className="hidden md:block relative" ref={accountMenuRef}>
                <button
                  onClick={() => setAccountMenuOpen(!accountMenuOpen)}
                  className="flex items-center gap-2 px-3 py-2 text-slate-600 hover:text-slate-900 rounded-lg hover:bg-slate-100 transition-colors"
                >
                  <UserCircle size={18} />
                  <span className="text-sm font-medium truncate max-w-[140px]">{me?.email}</span>
                  <ChevronDown size={14} className={cn("transition-transform", accountMenuOpen && "rotate-180")} />
                </button>
                {accountMenuOpen && (
                  <div className="absolute right-0 top-full mt-1 bg-white border border-slate-200 rounded-lg shadow-lg z-50 min-w-[220px]">
                    <Link
                      href={`/${locale}/app/account`}
                      onClick={() => setAccountMenuOpen(false)}
                      className="flex items-center gap-2 px-4 py-2.5 text-sm text-slate-700 hover:bg-slate-50 border-b border-slate-100 transition-colors"
                    >
                      <Cog size={16} />
                      {t.general || "General"}
                    </Link>
                    <Link
                      href={`/${locale}/app/organizations`}
                      onClick={() => setAccountMenuOpen(false)}
                      className="flex items-center gap-2 px-4 py-2.5 text-sm text-slate-700 hover:bg-slate-50 border-b border-slate-100 transition-colors"
                    >
                      <Building2 size={16} />
                      {t.organizations || "Organizations"}
                    </Link>
                    <Link
                      href={`/${locale}/app/billing`}
                      onClick={() => setAccountMenuOpen(false)}
                      className="flex items-center gap-2 px-4 py-2.5 text-sm text-slate-700 hover:bg-slate-50 border-b border-slate-100 transition-colors"
                    >
                      <CreditCard size={16} />
                      {t.billing}
                    </Link>
                    <a
                      href="/logout"
                      onClick={() => setAccountMenuOpen(false)}
                      className="flex items-center gap-2 px-4 py-2.5 text-sm text-slate-700 hover:bg-slate-50 transition-colors"
                    >
                      <span>→</span>
                      {t.signOut}
                    </a>
                  </div>
                )}
              </div>
            </>
          )}

          {/* Logged-out: sign in + sign up — desktop only */}
          {loggedOut && (
            <div className="hidden md:flex items-center gap-1">
              <Link
                href={`/${locale}/login`}
                className="px-3 py-1.5 rounded-lg text-sm text-slate-600 hover:bg-slate-100 hover:text-slate-900 transition-colors"
              >
                {t.signIn}
              </Link>
              <Link
                href={`/${locale}/register`}
                className="px-3 py-1.5 rounded-lg text-sm font-medium bg-blue-600 text-white hover:bg-blue-700 transition-colors"
              >
                {t.signUpFree}
              </Link>
            </div>
          )}

          {/* Hamburger — mobile only */}
          {!isLoading && (
            <button
              className="md:hidden p-2 -mr-1 text-slate-600 hover:text-slate-900 rounded-lg hover:bg-slate-100 transition-colors"
              onClick={() => setMobileOpen((v) => !v)}
              aria-label="Toggle menu"
            >
              {mobileOpen ? <X size={20} /> : <Menu size={20} />}
            </button>
          )}
        </div>
      </header>

      {/* Mobile menu overlay */}
      {mobileOpen && (
        <div className="md:hidden fixed inset-0 top-12 z-30 bg-white overflow-y-auto border-t border-slate-200 shadow-lg">
          {loggedIn && (
            <div className="py-1">
              {NAV_MAIN.map(({ href, label, icon: Icon }) => {
                const active = pathname === href || pathname?.startsWith(href + "/");
                return (
                  <Link
                    key={href}
                    href={href}
                    onClick={closeMobile}
                    className={cn(
                      "flex items-center gap-3 px-5 py-3.5 text-sm border-b border-slate-50 transition-colors",
                      active ? "bg-blue-50 text-blue-700 font-medium" : "text-slate-700 hover:bg-slate-50"
                    )}
                  >
                    <Icon size={18} />
                    {label}
                  </Link>
                );
              })}
              {me?.org?.tier === "free" && (
                <Link
                  href={`/${locale}/app/pricing`}
                  onClick={closeMobile}
                  className={cn(
                    "flex items-center gap-3 px-5 py-3.5 text-sm border-b border-slate-50 transition-colors",
                    pathname === `/${locale}/app/pricing` ? "bg-blue-50 text-blue-700 font-medium" : "text-slate-700 hover:bg-slate-50"
                  )}
                >
                  <Tag size={18} />
                  {t.pricing}
                </Link>
              )}

              {me?.is_superadmin && (
                <>
                  <div className="px-5 py-2 text-xs font-semibold text-slate-400 uppercase tracking-widest bg-slate-50 border-b border-slate-100">
                    {t.admin}
                  </div>
                  {NAV_ADMIN.map(({ href, label, icon: Icon }) => (
                    <Link
                      key={href}
                      href={href}
                      onClick={closeMobile}
                      className={cn(
                        "flex items-center gap-3 px-5 py-3.5 text-sm border-b border-slate-50 transition-colors",
                        pathname?.startsWith(href) ? "bg-blue-50 text-blue-700 font-medium" : "text-slate-700 hover:bg-slate-50"
                      )}
                    >
                      <Icon size={18} />
                      {label}
                    </Link>
                  ))}
                  <Link
                    href={`/${locale}/app/admin`}
                    onClick={closeMobile}
                    className={cn(
                      "flex items-center gap-3 px-5 py-3.5 text-sm border-b border-slate-50 transition-colors",
                      pathname?.startsWith(`/${locale}/app/admin`) ? "bg-purple-50 text-purple-700 font-medium" : "text-purple-600 hover:bg-purple-50"
                    )}
                  >
                    <Shield size={18} />
                    {t.adminPanel}
                  </Link>
                </>
              )}

              <div className="mt-2 border-t border-slate-200" />

              {me?.email && (
                <div className="px-5 py-3 text-sm text-slate-500 border-b border-slate-50">{me.email}</div>
              )}
              <Link
                href={`/${locale}/app/account`}
                onClick={closeMobile}
                className="flex items-center gap-3 px-5 py-3.5 text-sm text-slate-700 hover:bg-slate-50 border-b border-slate-50 transition-colors"
              >
                <UserCircle size={18} />
                {t.account}
              </Link>
              <Link
                href={`/${locale}/app/organizations`}
                onClick={closeMobile}
                className="flex items-center gap-3 px-5 py-3.5 text-sm text-slate-700 hover:bg-slate-50 border-b border-slate-50 transition-colors"
              >
                <Building2 size={18} />
                {t.organizations || "Organizations"}
              </Link>
              <Link
                href={`/${locale}/app/billing`}
                onClick={closeMobile}
                className="flex items-center gap-3 px-5 py-3.5 text-sm text-slate-700 hover:bg-slate-50 border-b border-slate-50 transition-colors"
              >
                <CreditCard size={18} />
                {t.billing}
              </Link>
              <a
                href="/logout"
                className="flex items-center gap-3 px-5 py-3.5 text-sm text-slate-500 hover:bg-slate-50 transition-colors"
              >
                {t.signOut}
              </a>
            </div>
          )}

          {loggedOut && (
            <div className="py-1">
              {MARKETING_NAV.map(({ href, label }) => (
                <Link
                  key={href}
                  href={href}
                  onClick={closeMobile}
                  className="flex items-center px-5 py-3.5 text-sm text-slate-700 hover:bg-slate-50 border-b border-slate-50 transition-colors"
                >
                  {label}
                </Link>
              ))}
              <div className="px-5 py-4 flex flex-col gap-2">
                <Link
                  href={`/${locale}/login`}
                  onClick={closeMobile}
                  className="w-full text-center py-2.5 rounded-lg border border-slate-200 text-sm text-slate-700 hover:bg-slate-50 transition-colors"
                >
                  {t.signIn}
                </Link>
                <Link
                  href={`/${locale}/register`}
                  onClick={closeMobile}
                  className="w-full text-center py-2.5 rounded-lg bg-blue-600 text-white text-sm font-medium hover:bg-blue-700 transition-colors"
                >
                  {t.signUpFree}
                </Link>
              </div>
              {/* Mobile locale switcher */}
              <div className="px-5 py-3 border-t border-slate-100 flex items-center gap-2">
                <Globe size={14} className="text-slate-400" />
                {SUPPORTED_LOCALES.map((loc) => (
                  <button
                    key={loc}
                    onClick={() => { switchLocale(loc); closeMobile(); }}
                    className={cn(
                      "px-2 py-1 rounded text-xs font-medium transition-colors",
                      loc === locale ? "bg-slate-100 text-slate-800" : "text-slate-400 hover:text-slate-700"
                    )}
                  >
                    {LOCALE_LABELS[loc]}
                  </button>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </>
  );
}
