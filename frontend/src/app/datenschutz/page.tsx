import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Datenschutz | Helvex",
  description: "Datenschutzerklärung der Helvex Webanwendung.",
};

export default function DatenschutzPage() {
  return (
    <div className="mx-auto max-w-3xl px-6 py-10 space-y-6">
      <header>
        <h1 className="text-3xl font-bold text-slate-900">Datenschutz</h1>
        <p className="mt-2 text-sm text-slate-500">
          Diese Datenschutzerklärung richtet sich nach dem schweizerischen Datenschutzgesetz (DSG) und der DSV.
        </p>
      </header>

      <section className="rounded-xl border border-slate-200 bg-white p-6 space-y-4 text-sm text-slate-700">
        <h2 className="font-semibold text-slate-900">1. Verantwortliche Stelle</h2>
        <p>Balogh Consulting, Schweiz</p>
        <p>E-Mail für Datenschutzanfragen: kontakt@balogh-consulting.ch</p>
      </section>

      <section className="rounded-xl border border-slate-200 bg-white p-6 space-y-3 text-sm text-slate-700">
        <h2 className="font-semibold text-slate-900">2. Welche Daten wir bearbeiten</h2>
        <ul className="list-disc pl-5 space-y-1">
          <li>Kontodaten wie E-Mail-Adresse, Rollen und Organisationszuordnung.</li>
          <li>Authentifizierungsdaten wie Passwort-Hash, Session-Informationen und OAuth-Anmeldung.</li>
          <li>Sicherheits- und Protokolldaten wie IP-Adresse, Login-Versuche, Zeitstempel und Audit-Logs.</li>
          <li>Kommunikationsdaten für Verifikations-, Einladungs- und Passwort-Reset-E-Mails.</li>
          <li>Nutzungs- und Arbeitsdaten innerhalb der Plattform (z. B. Notizen und Kontaktdaten).</li>
        </ul>
      </section>

      <section className="rounded-xl border border-slate-200 bg-white p-6 space-y-3 text-sm text-slate-700">
        <h2 className="font-semibold text-slate-900">3. Zwecke und Rechtsgrundlagen</h2>
        <p>
          Wir bearbeiten Personendaten zur Vertragserfüllung, zur sicheren Bereitstellung der Plattform,
          zur Missbrauchsprävention, zur Kommunikation mit Nutzern und zur Erfüllung gesetzlicher Pflichten.
        </p>
      </section>

      <section className="rounded-xl border border-slate-200 bg-white p-6 space-y-3 text-sm text-slate-700">
        <h2 className="font-semibold text-slate-900">4. Cookies</h2>
        <p>
          Wir verwenden notwendige Cookies für Login und Sicherheit. Optionale Analyse-Cookies werden nur nach
          ausdrücklicher Zustimmung gesetzt.
        </p>
        <p>
          Ihre Auswahl kann jederzeit über den Link "Cookie-Einstellungen" im Footer geändert werden.
        </p>
      </section>

      <section className="rounded-xl border border-slate-200 bg-white p-6 space-y-3 text-sm text-slate-700">
        <h2 className="font-semibold text-slate-900">5. Empfänger und Auftragsbearbeiter</h2>
        <p>
          Wir setzen technische Dienstleister ein, insbesondere für Hosting, E-Mail-Zustellung,
          OAuth-Authentifizierung und externe Daten-/KI-Dienste. Mit Auftragsbearbeitern werden erforderliche
          vertragliche Datenschutzvereinbarungen abgeschlossen.
        </p>
      </section>

      <section className="rounded-xl border border-slate-200 bg-white p-6 space-y-3 text-sm text-slate-700">
        <h2 className="font-semibold text-slate-900">6. Bekanntgabe ins Ausland</h2>
        <p>
          Eine Bearbeitung kann durch eingesetzte Dienstleister auch ausserhalb der Schweiz erfolgen.
          Falls erforderlich, erfolgen geeignete Garantien (z. B. Standardvertragsklauseln).
        </p>
      </section>

      <section className="rounded-xl border border-slate-200 bg-white p-6 space-y-3 text-sm text-slate-700">
        <h2 className="font-semibold text-slate-900">7. Aufbewahrung</h2>
        <p>
          Wir speichern Personendaten nur so lange, wie es für die genannten Zwecke erforderlich ist oder
          gesetzliche Pflichten es verlangen.
        </p>
      </section>

      <section className="rounded-xl border border-slate-200 bg-white p-6 space-y-3 text-sm text-slate-700">
        <h2 className="font-semibold text-slate-900">8. Rechte betroffener Personen</h2>
        <p>
          Sie haben im Rahmen des anwendbaren Rechts Anspruch auf Auskunft, Berichtigung, Löschung,
          Herausgabe/Übertragung und Widerspruch.
        </p>
      </section>

      <section className="rounded-xl border border-slate-200 bg-white p-6 space-y-3 text-sm text-slate-700">
        <h2 className="font-semibold text-slate-900">9. Kontakt und Aufsicht</h2>
        <p>Datenschutzanfragen: kontakt@balogh-consulting.ch</p>
        <p>In der Schweiz zuständige Aufsichtsbehörde: EDÖB.</p>
      </section>

      <p className="text-xs text-slate-400">Stand: 29.03.2026</p>
    </div>
  );
}
