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
          Wir verwenden notwendige Cookies für Login und Sicherheit. Optionale Cookies für Analyse (Google Tag Manager)
          und Werbung (Google AdSense) werden nur nach ausdrücklicher Zustimmung gesetzt.
        </p>
        <ul className="list-disc pl-5 space-y-1">
          <li>Notwendig: Session/Authentifizierung und technische Sicherheit.</li>
          <li>Analyse: Reichweiten- und Nutzungsanalyse über Google Tag Manager.</li>
          <li>Werbung: Auslieferung und Messung von Anzeigen über Google AdSense.</li>
        </ul>
        <p>
          Ihre Auswahl kann jederzeit über den Link &quot;Cookie-Einstellungen&quot; im Footer geändert werden.
        </p>
      </section>

      <section className="rounded-xl border border-slate-200 bg-white p-6 space-y-3 text-sm text-slate-700">
        <h2 className="font-semibold text-slate-900">5. Tracking und Werbung (Google-Dienste)</h2>
        <p>
          Sofern Sie zustimmen, setzen wir Google Tag Manager (Analyse) sowie Google AdSense (Werbung) ein.
          Dabei können technische Kennungen, Geräte-/Browserdaten, Nutzungsereignisse und ungefähre Standortdaten
          verarbeitet werden.
        </p>
        <p>
          Rechtsgrundlage ist Ihre Einwilligung. Sie können diese jederzeit mit Wirkung für die Zukunft über
          &quot;Cookie-Einstellungen&quot; widerrufen.
        </p>
      </section>

      <section className="rounded-xl border border-slate-200 bg-white p-6 space-y-3 text-sm text-slate-700">
        <h2 className="font-semibold text-slate-900">6. Empfänger und Auftragsbearbeiter</h2>
        <p>
          Wir setzen technische Dienstleister ein, insbesondere für Hosting, E-Mail-Zustellung,
          OAuth-Authentifizierung, externe Daten-/KI-Dienste sowie Analyse-/Werbedienste. Mit Auftragsbearbeitern werden erforderliche
          vertragliche Datenschutzvereinbarungen abgeschlossen.
        </p>
      </section>

      <section className="rounded-xl border border-slate-200 bg-white p-6 space-y-3 text-sm text-slate-700">
        <h2 className="font-semibold text-slate-900">7. Bekanntgabe ins Ausland</h2>
        <p>
          Eine Bearbeitung kann durch eingesetzte Dienstleister auch ausserhalb der Schweiz erfolgen.
          Dies kann insbesondere bei Google-Diensten (Tag Manager, AdSense) der Fall sein.
          Falls erforderlich, erfolgen geeignete Garantien (z. B. Standardvertragsklauseln).
        </p>
      </section>

      <section className="rounded-xl border border-slate-200 bg-white p-6 space-y-3 text-sm text-slate-700">
        <h2 className="font-semibold text-slate-900">8. Aufbewahrung</h2>
        <p>
          Wir speichern Personendaten nur so lange, wie es für die genannten Zwecke erforderlich ist oder
          gesetzliche Pflichten es verlangen.
        </p>
      </section>

      <section className="rounded-xl border border-slate-200 bg-white p-6 space-y-3 text-sm text-slate-700">
        <h2 className="font-semibold text-slate-900">9. Rechte betroffener Personen</h2>
        <p>
          Sie haben im Rahmen des anwendbaren Rechts Anspruch auf Auskunft, Berichtigung, Löschung,
          Herausgabe/Übertragung und Widerspruch.
        </p>
      </section>

      <section className="rounded-xl border border-slate-200 bg-white p-6 space-y-3 text-sm text-slate-700">
        <h2 className="font-semibold text-slate-900">10. Kontakt und Aufsicht</h2>
        <p>Datenschutzanfragen: kontakt@balogh-consulting.ch</p>
        <p>In der Schweiz zuständige Aufsichtsbehörde: EDÖB.</p>
      </section>

      <p className="text-xs text-slate-400">Stand: 02.04.2026</p>
    </div>
  );
}
