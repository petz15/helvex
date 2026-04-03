import Link from "next/link";
import { HelvexMark } from "@/components/helvex-logo";
import { CookieSettingsButton } from "../components/cookie-settings-button";
import { LANDING_FEATURE_CARDS, PRICING_TIERS } from "@/lib/marketing-data";

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

// ─── Fit-score ring ───────────────────────────────────────────────────────────

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

// ─── Company detail mock ──────────────────────────────────────────────────────

function CompanyMock() {
  return (
    <div className="rounded-xl border border-slate-200 bg-white shadow-lg overflow-hidden text-left select-none w-72">
      {/* Browser chrome */}
      <div className="bg-slate-100 border-b border-slate-200 px-3 py-2 flex items-center gap-2">
        <span className="h-2.5 w-2.5 rounded-full bg-red-400" />
        <span className="h-2.5 w-2.5 rounded-full bg-amber-400" />
        <span className="h-2.5 w-2.5 rounded-full bg-green-400" />
        <div className="flex-1 mx-2 bg-white rounded-md px-2 py-0.5 text-[10px] text-slate-400 border border-slate-200 truncate">
          helvex.dicy.ch/app/companies/…
        </div>
      </div>

      <div className="p-4 space-y-3">
        {/* Back link */}
        <div className="text-[10px] text-slate-400">← Search</div>

        {/* Header row */}
        <div className="flex items-start justify-between gap-2">
          <div>
            <p className="text-sm font-bold text-slate-900">{POST_CH.name}</p>
            <div className="flex flex-wrap items-center gap-1 mt-1">
              <span className="font-mono text-[9px] bg-slate-100 text-slate-500 px-1.5 py-0.5 rounded">
                {POST_CH.uid}
              </span>
              <span className="text-[9px] font-medium px-1.5 py-0.5 rounded bg-emerald-100 text-emerald-700">
                ACTIVE
              </span>
            </div>
          </div>
          <div className="relative shrink-0">
            <ScoreRing value={POST_CH.fitScore} />
            <span className="absolute inset-0 flex items-center justify-center text-[11px] font-bold text-blue-600 rotate-90">
              <p className="text-[11px] font-bold text-blue-600 transform -rotate-90">{POST_CH.fitScore}</p>
            </span>
          </div>
        </div>

        {/* Info grid */}
        <div className="grid grid-cols-2 gap-x-3 gap-y-1 text-[10px]">
          <div>
            <p className="text-slate-400">Seat</p>
            <p className="font-medium text-slate-700">{POST_CH.seat}, {POST_CH.canton}</p>
          </div>
          <div>
            <p className="text-slate-400">Legal form</p>
            <p className="font-medium text-slate-700">{POST_CH.legalForm}</p>
          </div>
          <div>
            <p className="text-slate-400">Capital</p>
            <p className="font-medium text-slate-700">{POST_CH.capital}</p>
          </div>
        </div>

        {/* Category tags */}
        <div className="flex flex-wrap gap-1">
          {POST_CH.categories.map((c) => (
            <span key={c} className="text-[9px] px-1.5 py-0.5 rounded-full bg-blue-50 text-blue-600 font-medium">
              {c}
            </span>
          ))}
        </div>

        {/* Purpose snippet */}
        <p className="text-[10px] text-slate-400 leading-relaxed line-clamp-2">{POST_CH.purpose}</p>

        {/* SHAB timeline snippet */}
        <div className="border-t border-slate-100 pt-2">
          <p className="text-[10px] font-semibold text-slate-600 mb-1.5">SHAB History</p>
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
  );
}

// ─── Features ─────────────────────────────────────────────────────────────────

function isWhiteCrossCell(index: number): boolean {
  const row = Math.floor(index / 5);
  const col = index % 5;
  return row === 2 || col === 2;
}

// ─── Page ─────────────────────────────────────────────────────────────────────

export default function LandingPage() {
  return (
    <div className="min-h-screen bg-white">
      {/* ── Hero ── */}
      <section className="max-w-6xl mx-auto px-6 pt-20 pb-24 flex flex-col lg:flex-row items-center gap-16">
        {/* Copy */}
        <div className="flex-1 max-w-xl">
          <div className="flex items-center gap-2.5 mb-8">
            <span className="text-blue-600">
              <HelvexMark size={34} />
            </span>
            <span className="text-xl font-bold tracking-tight text-slate-900">Helvex</span>
          </div>

          <h1 className="text-4xl sm:text-5xl font-extrabold text-slate-900 leading-tight tracking-tight mb-5">
            Swiss company{" "}
            <span className="text-blue-600">intelligence</span>
          </h1>
          <p className="text-lg text-slate-500 leading-relaxed mb-8">
            Search, qualify, and track companies from the Swiss commercial register.
            Powered by live SHAB data and AI classification — so your pipeline stays relevant.
          </p>

          <div className="flex flex-wrap items-center gap-3">
            <Link
              href="/register"
              className="px-5 py-2.5 rounded-lg bg-blue-600 text-white font-semibold text-sm hover:bg-blue-700 transition-colors shadow-sm"
            >
              Sign up free →
            </Link>
            <Link
              href="/demo/company"
              className="px-5 py-2.5 rounded-lg border border-slate-200 text-slate-700 font-medium text-sm hover:bg-slate-50 transition-colors"
            >
              View live demo
            </Link>
          </div>
          <p className="mt-3 text-xs text-slate-400">No credit card required.</p>
        </div>

        {/* Product preview */}
        <div className="flex-1 flex justify-center lg:justify-end">
          <div className="relative">
            <div className="absolute -inset-6 bg-blue-100 rounded-3xl blur-2xl opacity-60" />
            <div className="relative">
              <CompanyMock />
            </div>
          </div>
        </div>
      </section>

      {/* ── Features ── */}
      <section
        id="features"
        className="relative overflow-hidden border-t border-slate-200 bg-[radial-gradient(circle_at_top,_rgba(59,130,246,0.08),_transparent_42%),linear-gradient(180deg,#f8fafc_0%,#eff6ff_100%)] py-24"
      >
        <div className="absolute left-1/2 top-0 h-56 w-56 -translate-x-1/2 rounded-full bg-blue-300/20 blur-3xl" />
        <div className="max-w-6xl mx-auto px-6 relative">
          <div className="mx-auto max-w-3xl text-center mb-10">
            <span className="inline-flex items-center rounded-full border border-blue-200 bg-white/90 px-3 py-1 text-[11px] font-semibold uppercase tracking-[0.25em] text-blue-700 shadow-sm">
              Feature board
            </span>
            <h2 className="mt-4 text-3xl sm:text-4xl font-black tracking-tight text-slate-900">
              A visual map of what Helvex does
            </h2>
            <p className="mt-4 text-sm sm:text-base text-slate-600 leading-relaxed">
              The blue tiles are the product surface. The white cross highlights the core areas where the platform is most differentiated.
            </p>
          </div>

          <div className="rounded-[2rem] border border-white/70 bg-white/75 p-4 sm:p-5 shadow-[0_20px_60px_rgba(15,23,42,0.08)] backdrop-blur">
            <div className="mb-4 flex flex-wrap items-center justify-between gap-3 px-1 text-[11px] font-medium text-slate-500">
              <span>Hover a tile to reveal details</span>
              <span>Use keyboard focus too</span>
            </div>

            <div className="overflow-x-auto pb-2">
              <div className="grid min-w-[920px] grid-cols-5 gap-4">
                {LANDING_FEATURE_CARDS.map((feature, index) => {
                  const isWhite = isWhiteCrossCell(index);
                  return (
                    <article
                      key={feature.title}
                      tabIndex={0}
                      className={`group relative aspect-square overflow-hidden rounded-2xl border p-4 outline-none transition-all duration-300 hover:-translate-y-1 hover:shadow-xl focus-visible:ring-2 focus-visible:ring-blue-500 ${
                        isWhite
                          ? "border-blue-200 bg-white text-blue-700 shadow-[0_10px_30px_rgba(37,99,235,0.08)]"
                          : "border-blue-400/20 bg-gradient-to-br from-blue-600 via-blue-600 to-blue-700 text-white shadow-[0_14px_40px_rgba(37,99,235,0.26)]"
                      }`}
                    >
                      <div className={`absolute inset-0 transition-opacity duration-300 ${isWhite ? "opacity-0" : "bg-[radial-gradient(circle_at_top_right,rgba(255,255,255,0.18),transparent_45%)] opacity-100 group-hover:opacity-70"}`} />
                      <div className="relative z-10 flex h-full flex-col">
                        <div className="flex items-start justify-between gap-3">
                          <span
                            className={`inline-flex h-7 items-center rounded-full px-2.5 text-[10px] font-semibold tracking-[0.24em] uppercase ${
                              isWhite ? "bg-blue-50 text-blue-600" : "bg-white/15 text-white/80"
                            }`}
                          >
                            {String(index + 1).padStart(2, "0")}
                          </span>
                          <span className={`mt-1 h-2.5 w-2.5 rounded-full ${isWhite ? "bg-blue-500" : "bg-white/70"}`} />
                        </div>

                        <div className="mt-4 flex-1">
                          <h3 className="text-[1.05rem] font-semibold leading-tight">
                            {feature.title}
                          </h3>
                        </div>

                        <div className="mt-3">
                          <p
                            className={`text-[11px] font-medium uppercase tracking-[0.2em] transition-opacity duration-300 group-hover:opacity-0 group-focus-visible:opacity-0 ${
                              isWhite ? "text-blue-500/70" : "text-white/70"
                            }`}
                          >
                            Hover for detail
                          </p>
                          <p
                            className={`mt-2 text-sm leading-relaxed transition-all duration-300 opacity-0 translate-y-2 group-hover:opacity-100 group-hover:translate-y-0 group-focus-visible:opacity-100 group-focus-visible:translate-y-0 ${
                              isWhite ? "text-slate-600" : "text-white/90"
                            }`}
                          >
                            {feature.detail}
                          </p>
                        </div>
                      </div>
                    </article>
                  );
                })}
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* ── Bottom CTA ── */}
      <section id="pricing" className="py-20 px-6">
        <div className="max-w-6xl mx-auto">
          <div className="flex justify-center mb-4 text-blue-600">
            <HelvexMark size={30} />
          </div>
          <h2 className="text-2xl font-bold text-slate-900 mb-3 text-center">Current plans and pricing</h2>
          <p className="text-slate-500 mb-8 text-sm text-center max-w-2xl mx-auto">
            Start for free, then scale up as your needs grow. No hidden fees — only pay for what you use.
          </p>

          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-5">
            {PRICING_TIERS.map((tier) => {
              const isDark = Boolean(tier.dark);
              return (
                <article
                  key={tier.id}
                  className={`rounded-2xl border p-5 shadow-sm ${
                    isDark
                      ? "border-slate-800 bg-slate-900 text-white"
                      : tier.popular
                      ? "border-blue-400 bg-blue-50 ring-2 ring-blue-300"
                      : "border-slate-200 bg-white"
                  }`}
                >
                  <p className={`text-xs font-semibold uppercase tracking-widest ${isDark ? "text-slate-300" : "text-slate-500"}`}>
                    {tier.name}
                  </p>
                  <div className="mt-2">
                    <p className={`text-2xl font-bold ${isDark ? "text-white" : "text-slate-900"}`}>
                      {tier.monthly === 0 ? "Free" : `CHF ${tier.monthly}/mo`}
                    </p>
                    {tier.monthly > 0 && (
                      <p className={`text-xs mt-1 ${isDark ? "text-slate-300" : "text-slate-500"}`}>
                        CHF {tier.yearly}/yr billed yearly
                      </p>
                    )}
                  </div>
                </article>
              );
            })}
          </div>

          <div className="mt-10 text-center">
            <Link
              href="/register"
              className="inline-block px-6 py-3 rounded-lg bg-blue-600 text-white font-semibold text-sm hover:bg-blue-700 transition-colors shadow-sm"
            >
              Create your free account →
            </Link>
          </div>
        </div>
      </section>

      {/* ── Footer ── */}
      <footer className="border-t border-slate-100 py-8 text-center text-xs text-slate-400">
        <div className="flex items-center justify-center gap-3 flex-wrap px-4">
          <span>© {new Date().getFullYear()} Helvex · Balogh Consulting</span>
          <span>·</span>
          <Link href="/impressum" className="hover:text-slate-600 underline-offset-2 hover:underline">Impressum</Link>
          <span>·</span>
          <Link href="/datenschutz" className="hover:text-slate-600 underline-offset-2 hover:underline">Datenschutz</Link>
          <span>·</span>
          <Link href="/agb" className="hover:text-slate-600 underline-offset-2 hover:underline">AGB</Link>
          <span>·</span>
          <CookieSettingsButton className="hover:text-slate-600 underline-offset-2 hover:underline" label="Cookie-Einstellungen" />
          <span>·</span>
          <Link href="/login" className="hover:text-slate-600 underline-offset-2 hover:underline">Sign in</Link>
        </div>
      </footer>
    </div>
  );
}
