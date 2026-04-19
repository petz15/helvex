import Link from "next/link";
import { Database, Sparkles, Search, Building2, CheckCircle2 } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { notFound } from "next/navigation";
import { HelvexMark } from "@/components/helvex-logo";
import { CookieSettingsButton } from "@/components/cookie-settings-button";
import { PRICING_TIERS, creditsToChf } from "@/lib/marketing-data";
import { getDictionary, isLocale } from "@/i18n/dictionaries";

// ─── Post CH AG mock data ─────────────────────────────────────────────────────

const POST_CH = {
  name: "Post CH AG",
  uid: "CHE-435.551.225",
  legalForm: "Aktiengesellschaft",
  seat: "Bern",
  canton: "BE",
  capital: "CHF 1'300'000'000",
  purpose:
    "Die Gesellschaft bezweckt die Erbringung von Postdienstleistungen für die Allgemeinheit in der Schweiz sowie die Erbringung von weiteren Dienstleistungen im Post-, Logistik- und Kommunikationsbereich…",
  fitScore: 92,
  categories: ["Logistics", "Public Services", "Telecoms"],
};

function ScoreRing({ value }: { value: number }) {
  const r = 20;
  const circ = 2 * Math.PI * r;
  return (
    <svg width="52" height="52" viewBox="0 0 52 52" className="-rotate-90">
      <circle cx="26" cy="26" r={r} fill="none" stroke="#e0e7ff" strokeWidth="5" />
      <circle
        cx="26"
        cy="26"
        r={r}
        fill="none"
        stroke="#2563eb"
        strokeWidth="5"
        strokeDasharray={`${(value / 100) * circ} ${circ}`}
        strokeLinecap="round"
      />
    </svg>
  );
}

type FeatureBlock = {
  id: string;
  Icon: LucideIcon;
  accent: string;
  iconBg: string;
  border: string;
  title: string;
  tagline: string;
  features: string[];
};

export default async function LandingPage({ params }: { params: Promise<{ locale: string }> }) {
  const { locale } = await params;
  if (!isLocale(locale)) notFound();
  const dict = await getDictionary(locale);
  const t = dict.landing;

  const FEATURE_BLOCKS: FeatureBlock[] = [
    {
      id: "data",
      Icon: Database,
      accent: "text-blue-700",
      iconBg: "bg-blue-100 text-blue-600",
      border: "border-blue-100 hover:border-blue-300",
      title: t.features.data.title,
      tagline: t.features.data.tagline,
      features: [t.features.data.f1, t.features.data.f2, t.features.data.f3, t.features.data.f4, t.features.data.f5],
    },
    {
      id: "ai",
      Icon: Sparkles,
      accent: "text-violet-700",
      iconBg: "bg-violet-100 text-violet-600",
      border: "border-violet-100 hover:border-violet-300",
      title: t.features.ai.title,
      tagline: t.features.ai.tagline,
      features: [t.features.ai.f1, t.features.ai.f2, t.features.ai.f3, t.features.ai.f4, t.features.ai.f5],
    },
    {
      id: "search",
      Icon: Search,
      accent: "text-emerald-700",
      iconBg: "bg-emerald-100 text-emerald-600",
      border: "border-emerald-100 hover:border-emerald-300",
      title: t.features.search.title,
      tagline: t.features.search.tagline,
      features: [t.features.search.f1, t.features.search.f2, t.features.search.f3, t.features.search.f4, t.features.search.f5],
    },
    {
      id: "workspace",
      Icon: Building2,
      accent: "text-slate-700",
      iconBg: "bg-slate-100 text-slate-600",
      border: "border-slate-200 hover:border-slate-400",
      title: t.features.workspace.title,
      tagline: t.features.workspace.tagline,
      features: [t.features.workspace.f1, t.features.workspace.f2, t.features.workspace.f3, t.features.workspace.f4, t.features.workspace.f5],
    },
  ];

  const CREDIT_ACTIONS_I18N = [
    { ...t.pricing.actions.batchClassify, base: 8 },
    { ...t.pricing.actions.immediateClassify, base: 12 },
    { ...t.pricing.actions.webSearch, base: 20 },
    { ...t.pricing.actions.flexRescore, base: 1 },
    { ...t.pricing.actions.fullRecluster, base: 100_000 },
    { ...t.pricing.actions.exportBasic, base: 6_000 },
    { ...t.pricing.actions.exportDetailed, base: 13_000 },
  ];

  return (
    <div className="min-h-screen bg-white">
      {/* ── Hero ── */}
      <section className="max-w-6xl mx-auto px-6 pt-20 pb-24 flex flex-col lg:flex-row items-center gap-16">
        <div className="flex-1 max-w-xl">
          <div className="flex items-center gap-2.5 mb-8">
            <span className="text-blue-600"><HelvexMark size={34} /></span>
            <span className="text-xl font-bold tracking-tight text-slate-900">Helvex</span>
          </div>
          <h1 className="text-4xl sm:text-5xl font-extrabold text-slate-900 leading-tight tracking-tight mb-5">
            {t.hero.title}{" "}
            <span className="text-blue-600">{t.hero.titleAccent}</span>
          </h1>
          <p className="text-lg text-slate-500 leading-relaxed mb-8">{t.hero.subtitle}</p>
          <div className="flex flex-wrap items-center gap-3">
            <Link href={`/${locale}/register`} className="px-5 py-2.5 rounded-lg bg-blue-600 text-white font-semibold text-sm hover:bg-blue-700 transition-colors shadow-sm">
              {t.hero.cta}
            </Link>
            <Link href={`/${locale}/demo/company`} className="px-5 py-2.5 rounded-lg border border-slate-200 text-slate-700 font-medium text-sm hover:bg-slate-50 transition-colors">
              {t.hero.demo}
            </Link>
          </div>
          <p className="mt-3 text-xs text-slate-400 font-bold">{t.hero.noCreditCard}</p>
        </div>

        {/* Product preview */}
        <div className="flex-1 flex justify-center lg:justify-end">
          <div className="relative">
            <div className="absolute -inset-6 bg-blue-100 rounded-3xl blur-2xl opacity-60" />
            <div className="relative">
              <div className="rounded-xl border border-slate-200 bg-white shadow-lg overflow-hidden text-left select-none w-72">
                <div className="bg-slate-100 border-b border-slate-200 px-3 py-2 flex items-center gap-2">
                  <span className="h-2.5 w-2.5 rounded-full bg-red-400" />
                  <span className="h-2.5 w-2.5 rounded-full bg-amber-400" />
                  <span className="h-2.5 w-2.5 rounded-full bg-green-400" />
                  <div className="flex-1 mx-2 bg-white rounded-md px-2 py-0.5 text-[10px] text-slate-400 border border-slate-200 truncate">
                    helvex.dicy.ch/app/companies/…
                  </div>
                </div>
                <div className="p-4 space-y-3">
                  <div className="text-[10px] text-slate-400">{t.mock.back}</div>
                  <div className="flex items-start justify-between gap-2">
                    <div>
                      <p className="text-sm font-bold text-slate-900">{POST_CH.name}</p>
                      <div className="flex flex-wrap items-center gap-1 mt-1">
                        <span className="font-mono text-[9px] bg-slate-100 text-slate-500 px-1.5 py-0.5 rounded">{POST_CH.uid}</span>
                        <span className="text-[9px] font-medium px-1.5 py-0.5 rounded bg-emerald-100 text-emerald-700">{t.mock.active}</span>
                      </div>
                    </div>
                    <div className="relative shrink-0">
                      <ScoreRing value={POST_CH.fitScore} />
                      <span className="absolute inset-0 flex items-center justify-center text-[11px] font-bold text-blue-600 rotate-90">
                        <p className="text-[11px] font-bold text-blue-600 transform -rotate-90">{POST_CH.fitScore}</p>
                      </span>
                    </div>
                  </div>
                  <div className="grid grid-cols-2 gap-x-3 gap-y-1 text-[10px]">
                    <div><p className="text-slate-400">{t.mock.seat}</p><p className="font-medium text-slate-700">{POST_CH.seat}, {POST_CH.canton}</p></div>
                    <div><p className="text-slate-400">{t.mock.legalForm}</p><p className="font-medium text-slate-700">{POST_CH.legalForm}</p></div>
                    <div><p className="text-slate-400">{t.mock.capital}</p><p className="font-medium text-slate-700">{POST_CH.capital}</p></div>
                  </div>
                  <div className="flex flex-wrap gap-1">
                    {POST_CH.categories.map((c) => (
                      <span key={c} className="text-[9px] px-1.5 py-0.5 rounded-full bg-blue-50 text-blue-600 font-medium">{c}</span>
                    ))}
                  </div>
                  <p className="text-[10px] text-slate-400 leading-relaxed line-clamp-2">{POST_CH.purpose}</p>
                  <div className="border-t border-slate-100 pt-2">
                    <p className="text-[10px] font-semibold text-slate-600 mb-1.5">{t.mock.shabHistory}</p>
                    <div className="space-y-1.5">
                      {[
                        { date: "2013-09-27", label: "Neueintragung", cls: "bg-emerald-100 text-emerald-700" },
                        { date: "2017-04-20", label: "Firmenänderung", cls: "bg-blue-100 text-indigo-700" },
                        { date: "2024-11-08", label: "Adressänderung", cls: "bg-amber-100 text-amber-700" },
                      ].map((e) => (
                        <div key={e.date} className="flex items-center gap-2">
                          <span className="text-[9px] text-slate-400 w-16 shrink-0">{e.date}</span>
                          <span className={`text-[9px] font-medium px-1.5 py-0.5 rounded ${e.cls}`}>{e.label}</span>
                        </div>
                      ))}
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* ── Features ── */}
      <section id="features" className="border-t border-slate-100 bg-gradient-to-b from-slate-50 to-white py-24">
        <div className="max-w-6xl mx-auto px-6">
          <div className="mx-auto max-w-2xl text-center mb-14">
            <span className="inline-flex items-center rounded-full border border-blue-200 bg-white px-3 py-1 text-[11px] font-semibold uppercase tracking-[0.25em] text-blue-700 shadow-sm">
              {t.features.badge}
            </span>
            <h2 className="mt-4 text-3xl sm:text-4xl font-black tracking-tight text-slate-900">{t.features.title}</h2>
            <p className="mt-3 text-base text-slate-500 leading-relaxed">{t.features.subtitle}</p>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
            {FEATURE_BLOCKS.map((cat) => (
              <div key={cat.id} className={`group rounded-2xl border bg-white p-7 shadow-sm transition-all duration-200 hover:shadow-md ${cat.border}`}>
                <div className="flex items-start gap-4 mb-4">
                  <div className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-xl ${cat.iconBg}`}>
                    <cat.Icon size={20} />
                  </div>
                  <div>
                    <h3 className={`text-lg font-bold ${cat.accent}`}>{cat.title}</h3>
                    <p className="mt-0.5 text-sm text-slate-500 leading-snug">{cat.tagline}</p>
                  </div>
                </div>
                <ul className="space-y-2.5 mt-5">
                  {cat.features.map((f) => (
                    <li key={f} className="flex items-start gap-2.5 text-sm text-slate-600">
                      <CheckCircle2 size={15} className="mt-0.5 shrink-0 text-slate-400 group-hover:text-emerald-500 transition-colors" />
                      <span>{f}</span>
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          </div>

          <div className="mt-10 text-center">
            <Link href={`/${locale}/register`} className="inline-block px-6 py-3 rounded-lg bg-blue-600 text-white font-semibold text-sm hover:bg-blue-700 transition-colors shadow-sm">
              {t.features.cta}
            </Link>
          </div>
        </div>
      </section>

      {/* ── Pricing ── */}
      <section id="pricing" className="py-20 px-6 border-t border-slate-100">
        <div className="max-w-6xl mx-auto">
          <div className="flex justify-center mb-4 text-blue-600"><HelvexMark size={30} /></div>
          <h2 className="text-2xl font-bold text-slate-900 mb-3 text-center">{t.pricing.title}</h2>
          <p className="text-slate-500 mb-8 text-sm text-center max-w-2xl mx-auto">{t.pricing.subtitle}</p>

          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-5">
            {PRICING_TIERS.map((tier) => {
              const isDark = Boolean(tier.dark);
              return (
                <article
                  key={tier.id}
                  className={`rounded-2xl border p-5 shadow-sm ${
                    isDark ? "border-slate-800 bg-slate-900 text-white"
                    : tier.popular ? "border-blue-400 bg-blue-50 ring-2 ring-blue-300"
                    : "border-slate-200 bg-white"
                  }`}
                >
                  <p className={`text-xs font-semibold uppercase tracking-widest ${isDark ? "text-slate-300" : "text-slate-500"}`}>
                    {tier.name}
                  </p>
                  <div className="mt-2">
                    <p className={`text-2xl font-bold ${isDark ? "text-white" : "text-slate-900"}`}>
                      {tier.monthly === 0 ? t.pricing.free : `CHF ${tier.monthly}${t.pricing.perMonth}`}
                    </p>
                    {tier.monthly > 0 && (
                      <p className={`text-xs mt-1 ${isDark ? "text-slate-300" : "text-slate-500"}`}>
                        {t.pricing.perYearBilled.replace("{yearly}", String(tier.yearly))}
                      </p>
                    )}
                  </div>
                </article>
              );
            })}
          </div>

          <div className="mt-10 space-y-3">
            <div className="flex items-end justify-between gap-4 flex-wrap">
              <div>
                <h3 className="text-lg font-semibold text-slate-900">{t.pricing.consumption.title}</h3>
                <p className="mt-1 text-sm text-slate-500 max-w-3xl">{t.pricing.consumption.subtitle}</p>
              </div>
              <p className="text-xs font-medium text-slate-500">
                {t.pricing.consumption.creditRate.replace("{rate}", creditsToChf(1))}
              </p>
            </div>
            <div className="overflow-x-auto rounded-2xl border border-slate-200 bg-white shadow-sm">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-slate-200 bg-slate-50">
                    <th className="px-4 py-3 text-left font-semibold text-slate-600">{t.pricing.consumption.colAction}</th>
                    <th className="px-4 py-3 text-center font-semibold text-slate-600">{t.pricing.consumption.colUnit}</th>
                    <th className="px-4 py-3 text-center font-semibold text-slate-600">{t.pricing.consumption.colCredits}</th>
                    <th className="px-4 py-3 text-center font-semibold text-slate-600">{t.pricing.consumption.colChf}</th>
                  </tr>
                </thead>
                <tbody>
                  {CREDIT_ACTIONS_I18N.map((action, index) => (
                    <tr key={action.label} className={`border-b border-slate-100 ${index % 2 === 0 ? "bg-white" : "bg-slate-50/50"}`}>
                      <td className="px-4 py-3 text-slate-700 font-medium">{action.label}</td>
                      <td className="px-4 py-3 text-center text-slate-500 text-xs">{action.unit}</td>
                      <td className="px-4 py-3 text-center text-slate-700 font-semibold">{action.base.toLocaleString()}</td>
                      <td className="px-4 py-3 text-center text-slate-600">{creditsToChf(action.base)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          <div className="mt-10 text-center">
            <Link href={`/${locale}/register`} className="inline-block px-6 py-3 rounded-lg bg-blue-600 text-white font-semibold text-sm hover:bg-blue-700 transition-colors shadow-sm">
              {t.pricing.cta}
            </Link>
          </div>
        </div>
      </section>

      {/* ── Footer ── */}
      <footer className="border-t border-slate-100 py-8 text-center text-xs text-slate-400">
        <div className="flex items-center justify-center gap-3 flex-wrap px-4">
          <span>{t.footer.copyright.replace("{year}", String(new Date().getFullYear()))}</span>
          <span>·</span>
          <Link href={`/${locale}/impressum`} className="hover:text-slate-600 underline-offset-2 hover:underline">{t.footer.impressum}</Link>
          <span>·</span>
          <Link href={`/${locale}/datenschutz`} className="hover:text-slate-600 underline-offset-2 hover:underline">{t.footer.datenschutz}</Link>
          <span>·</span>
          <Link href={`/${locale}/agb`} className="hover:text-slate-600 underline-offset-2 hover:underline">{t.footer.agb}</Link>
          <span>·</span>
          <CookieSettingsButton className="hover:text-slate-600 underline-offset-2 hover:underline" label={t.footer.cookieSettings} />
          <span>·</span>
          <Link href={`/${locale}/login`} className="hover:text-slate-600 underline-offset-2 hover:underline">{t.footer.signIn}</Link>
        </div>
      </footer>
    </div>
  );
}
