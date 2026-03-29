import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Impressum | Helvex",
  description: "Impressum der Helvex Webanwendung.",
};

export default function ImpressumPage() {
  return (
    <div className="mx-auto max-w-3xl px-6 py-10 space-y-6">
      <header>
        <h1 className="text-3xl font-bold text-slate-900">Impressum</h1>
        <p className="mt-2 text-sm text-slate-500">Angaben gemäss schweizerischem Recht.</p>
      </header>

      <section className="rounded-xl border border-slate-200 bg-white p-6 space-y-4 text-sm text-slate-700">
        <div>
          <h2 className="font-semibold text-slate-900">Anbieterin</h2>
          <p>Balogh Consulting</p>
          <p>Einzelunternehmen</p>
          <p>Dorfstrasse 43</p>
          <p>3073 Gümligen, Schweiz</p>
        </div>

        <div>
          <h2 className="font-semibold text-slate-900">Kontakt</h2>
          <p>E-Mail: kontakt@balogh-consulting.ch</p>
          <p>Telefon: +41 78 242 8584</p>
        </div>

        <div>
          <h2 className="font-semibold text-slate-900">Vertretungsberechtigte Person</h2>
          <p>Peter Balogh</p>
        </div>

        <div>
          <h2 className="font-semibold text-slate-900">Handelsregister</h2>
          <p>Bern, CHE-457.771.278</p>
        </div>

        <div>
          <h2 className="font-semibold text-slate-900">Mehrwertsteuer</h2>
          <p>CHE-457.771.278 MWST</p>
        </div>
      </section>

      <section className="rounded-xl border border-slate-200 bg-white p-6 space-y-3 text-sm text-slate-700">
        <h2 className="font-semibold text-slate-900">Haftungsausschluss</h2>
        <p>
          Trotz sorgfältiger inhaltlicher Kontrolle übernehmen wir keine Gewähr für Richtigkeit, Vollständigkeit
          und Aktualität der bereitgestellten Informationen.
        </p>
        <p>
          Wir haften nicht für Inhalte externer Links. Für deren Inhalte sind ausschliesslich die jeweiligen
          Betreiber verantwortlich.
        </p>
      </section>

      <section className="rounded-xl border border-slate-200 bg-white p-6 space-y-3 text-sm text-slate-700">
        <h2 className="font-semibold text-slate-900">Urheberrecht</h2>
        <p>
          Inhalte, Werke und Strukturen auf dieser Website sind urheberrechtlich geschützt. Jede Verwertung ausserhalb
          der gesetzlichen Grenzen bedarf der vorherigen schriftlichen Zustimmung der Rechteinhaber.
        </p>
      </section>

      <p className="text-xs text-slate-400">Stand: 29.03.2026</p>
    </div>
  );
}
