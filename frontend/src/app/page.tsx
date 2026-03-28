import Link from "next/link";
import { HelvexMark } from "@/components/helvex-logo";

// ─── Post CH AG mock data ─────────────────────────────────────────────────────

const POST_CH = {
  name: "Post CH AG",
  uid: "CHE-116.317.415",
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
              {POST_CH.fitScore}
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

const FEATURES = [
  {
    icon: "◆",
    title: "Live register data",
    body: "Every Swiss company from the commercial register, with automated SHAB updates keeping records current.",
  },
  {
    icon: "◇",
    title: "AI classification",
    body: "Claude-powered categorisation and fit scoring to filter thousands of companies down to your short list.",
  },
  {
    icon: "◆",
    title: "Full company history",
    body: "Name changes, ownership shifts, signer changes — replayed from SHAB publications into a structured timeline.",
  },
];

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
              href="/demo"
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
      <section id="features" className="bg-slate-50 border-t border-slate-100 py-20">
        <div className="max-w-5xl mx-auto px-6">
          <h2 className="text-2xl font-bold text-slate-900 text-center mb-12">
            Everything you need to work the Swiss register
          </h2>
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-6">
            {FEATURES.map((f) => (
              <div key={f.title} className="bg-white rounded-xl border border-slate-200 p-6 shadow-sm">
                <span className="text-2xl text-blue-500 mb-3 block">{f.icon}</span>
                <h3 className="font-semibold text-slate-800 mb-2 text-sm">{f.title}</h3>
                <p className="text-sm text-slate-500 leading-relaxed">{f.body}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* ── Bottom CTA ── */}
      <section id="pricing" className="py-20 text-center px-6">
        <div className="max-w-lg mx-auto">
          <div className="flex justify-center mb-4 text-blue-600">
            <HelvexMark size={30} />
          </div>
          <h2 className="text-2xl font-bold text-slate-900 mb-3">Start for free</h2>
          <p className="text-slate-500 mb-6 text-sm">
            Access the full Swiss company register. Upgrade when you need more.
          </p>
          <Link
            href="/register"
            className="inline-block px-6 py-3 rounded-lg bg-blue-600 text-white font-semibold text-sm hover:bg-blue-700 transition-colors shadow-sm"
          >
            Create your free account →
          </Link>
        </div>
      </section>

      {/* ── Footer ── */}
      <footer className="border-t border-slate-100 py-8 text-center text-xs text-slate-400">
        © {new Date().getFullYear()} Helvex · Balogh Consulting ·{" "}
        <Link href="/login" className="hover:text-slate-600 underline-offset-2 hover:underline">
          Sign in
        </Link>
      </footer>
    </div>
  );
}
