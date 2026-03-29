import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "AGB | Helvex",
  description: "Allgemeine Geschaeftsbedingungen der Helvex Webanwendung.",
};

export default function AgbPage() {
  return (
    <div className="mx-auto max-w-3xl px-6 py-10 space-y-6">
      <header>
        <h1 className="text-3xl font-bold text-slate-900">Allgemeine Geschaeftsbedingungen (AGB)</h1>
      </header>

      <section className="rounded-xl border border-slate-200 bg-white p-6 space-y-3 text-sm text-slate-700">
        <h2 className="font-semibold text-slate-900">1. Geltungsbereich</h2>
        <p>
          Diese AGB regeln die Nutzung der Plattform durch Unternehmen und beruflich handelnde Personen.
        </p>

        <h2 className="font-semibold text-slate-900">2. Anbieterin</h2>
        <p>Balogh Consulting, Einzelunternehmen, Dorfstrasse 43 3073 Gümligen, Schweiz</p>

        <h2 className="font-semibold text-slate-900">3. Leistungen</h2>
        <p>
          Helvex bietet eine webbasierte Plattform für Recherche, Analyse und Bearbeitung von Unternehmensdaten,
          inklusive anderweitigen Funktionen gemäss gewählten Tarifen.
        </p>

        <h2 className="font-semibold text-slate-900">4. Konto und Nutzung</h2>
        <p>
          Der Kunde ist für die sichere Verwahrung der Zugangsdaten verantwortlich und stellt sicher,
          dass nur berechtigte Personen Zugriff erhalten.
        </p>

        <h2 className="font-semibold text-slate-900">5. Preise und Laufzeit</h2>
        <p>
          Preise, Leistungsumfang und Laufzeiten richten sich nach dem gewählten Plan.
          Alle Preise verstehen sich exklusive gesetzlicher MWST, sofern nicht anders angegeben.
        </p>

        <h2 className="font-semibold text-slate-900">6. Pflichten des Kunden</h2>
        <p>
          Der Kunde nutzt die Plattform rechtmässig, unterlässt missbräuchliche Nutzung und ist für eigene Inhalte
          und deren Rechtmässigkeit verantwortlich.
        </p>

        <h2 className="font-semibold text-slate-900">7. Verfügbarkeit und Drittleistungen</h2>
        <p>
          Wir bemühen uns um hohe Verfügbarkeit, können aber keine unterbruchsfreie Verfügbarkeit garantieren.
          Leistungen können von externen Datenquellen und Drittanbietern abhängen.
        </p>

        <h2 className="font-semibold text-slate-900">8. Haftung</h2>
        <p>
          Wir haften nach den zwingenden gesetzlichen Bestimmungen. Im Übrigen wird die Haftung,
          soweit gesetzlich zulässig, ausgeschlossen oder vertraglich begrenzt.
        </p>

        <h2 className="font-semibold text-slate-900">9. Datenschutz</h2>
        <p>Die Bearbeitung von Personendaten richtet sich nach der Datenschutzerklärung.</p>

        <h2 className="font-semibold text-slate-900">10. Schlussbestimmungen</h2>
        <p>
          Es gilt ausschliesslich materielles Schweizer Recht unter Ausschluss des UN-Kaufrechts (CISG).
          Gerichtsstand ist Bern, soweit gesetzlich zulässig.
        </p>
      </section>

      <p className="text-xs text-slate-400">Stand: 29.03.2026</p>
    </div>
  );
}
