"use client";
import { useState } from "react";
import { ChevronDown, ChevronUp, UserPlus, UserMinus, UserCog } from "lucide-react";

// ─── Types ────────────────────────────────────────────────────────────────────

interface MutationType {
  id: number;
  key: string;
}

interface SogcEntry {
  sogcDate: string;
  sogcId: number;
  registryOfCommerceCanton: string;
  message: string;
  mutationTypes: MutationType[];
}

export interface Person {
  raw: string;
  lastName: string;
  firstName: string;
  origin: string;
  city: string;
  role: string;
  signatureType: string;
  bisher?: string;
}

export interface OrganeChange {
  added: Person[];
  removed: Person[];
  mutated: Person[];
}

// ─── FT tag renderer ──────────────────────────────────────────────────────────

function renderFtTags(message: string): React.ReactNode[] {
  // Strip outer XML-like FT tags, keeping content with styling
  const parts: React.ReactNode[] = [];
  const regex = /<FT TYPE="([^"]+)">([^<]*)<\/FT>|([^<]+)/g;
  let match: RegExpExecArray | null;
  let key = 0;

  while ((match = regex.exec(message)) !== null) {
    if (match[3] !== undefined) {
      // plain text
      parts.push(<span key={key++}>{match[3]}</span>);
    } else {
      const type = match[1];
      const text = match[2];
      switch (type) {
        case "F": // old firm name (strikethrough)
          parts.push(
            <span key={key++} className="line-through text-slate-400" title="Bisheriger Name">
              {text}
            </span>
          );
          break;
        case "N": // new firm name (highlight)
          parts.push(
            <span key={key++} className="font-semibold text-blue-700" title="Neuer Name">
              {text}
            </span>
          );
          break;
        case "S": // seat / Sitz
          parts.push(
            <span key={key++} className="font-medium text-slate-700" title="Sitz">
              {text}
            </span>
          );
          break;
        case "A": // UID of this company
          parts.push(
            <span key={key++} className="font-mono text-xs bg-slate-100 px-1 py-0.5 rounded text-slate-600" title="UID">
              {text}
            </span>
          );
          break;
        case "B": // UID of related company
          parts.push(
            <span key={key++} className="font-mono text-xs bg-blue-50 px-1 py-0.5 rounded text-blue-600" title="Verbundene UID">
              {text}
            </span>
          );
          break;
        default:
          parts.push(<span key={key++}>{text}</span>);
      }
    }
  }
  return parts;
}

// ─── Person parser ────────────────────────────────────────────────────────────

function parsePerson(raw: string): Person {
  // Format (typical):
  //   Nachname, Vorname [Titel], von Heimatort, in Wohnort, Rolle, mit Einzelunterschrift [bisher: alte Rolle]
  const bisherMatch = raw.match(/\[bisher:\s*([^\]]+)\]/);
  const bisher = bisherMatch ? bisherMatch[1].trim() : undefined;
  const cleaned = raw.replace(/\[bisher:[^\]]*\]/g, "").trim();

  const parts = cleaned.split(/,\s*/);
  const lastName = parts[0]?.trim() ?? "";
  const firstName = parts[1]?.trim() ?? "";

  let origin = "";
  let city = "";
  let role = "";
  let signatureType = "";

  for (let i = 2; i < parts.length; i++) {
    const p = parts[i].trim();
    if (p.startsWith("von ")) {
      origin = p.slice(4).trim();
    } else if (p.startsWith("in ")) {
      city = p.slice(3).trim();
    } else if (p.startsWith("mit ")) {
      signatureType = p.slice(4).trim();
    } else if (role === "") {
      role = p;
    }
  }

  return { raw, lastName, firstName, origin, city, role, signatureType, bisher };
}

function parseOrganeSection(message: string): OrganeChange {
  // Strip FT tags first for text parsing
  const plain = message.replace(/<FT TYPE="[^"]+">([^<]*)<\/FT>/g, "$1");

  const removedMatch = plain.match(
    /Ausgeschiedene Personen und erloschene Unterschriften:\s*([\s\S]*?)(?=Eingetragene Personen neu oder mutierend:|$)/i
  );
  const addedMatch = plain.match(
    /Eingetragene Personen neu oder mutierend:\s*([\s\S]*?)(?=Ausgeschiedene Personen|$)/i
  );

  function splitPersons(block: string): string[] {
    // Persons are separated by "; " but semicolons can also appear inside brackets
    // Simple approach: split on "; " that is not inside brackets
    const result: string[] = [];
    let depth = 0;
    let current = "";
    for (let i = 0; i < block.length; i++) {
      const ch = block[i];
      if (ch === "[") depth++;
      else if (ch === "]") depth--;
      else if (ch === ";" && depth === 0 && block[i + 1] === " ") {
        const trimmed = current.trim();
        if (trimmed) result.push(trimmed);
        current = "";
        i++; // skip space
        continue;
      }
      current += ch;
    }
    const trimmed = current.trim();
    if (trimmed) result.push(trimmed);
    return result;
  }

  const removedRaw = removedMatch ? splitPersons(removedMatch[1].trim()) : [];
  const addedRaw = addedMatch ? splitPersons(addedMatch[1].trim()) : [];

  const removed = removedRaw.filter(Boolean).map(parsePerson);
  const allAdded = addedRaw.filter(Boolean).map(parsePerson);

  // Persons with [bisher: ...] are mutations, not net-new
  const added = allAdded.filter((p) => !p.bisher);
  const mutated = allAdded.filter((p) => !!p.bisher);

  return { added, removed, mutated };
}

// ─── Derive current signers ───────────────────────────────────────────────────

export function deriveCurrentSigners(entries: SogcEntry[]): Person[] {
  const sorted = [...entries].sort(
    (a, b) => new Date(a.sogcDate).getTime() - new Date(b.sogcDate).getTime()
  );

  const signers: Map<string, Person> = new Map();

  for (const entry of sorted) {
    const isOrgane = entry.mutationTypes.some((m) => m.key === "aenderungorgane");
    if (!isOrgane) continue;

    const { added, removed, mutated } = parseOrganeSection(entry.message);

    for (const p of removed) {
      const key = `${p.lastName.toLowerCase()}_${p.firstName.toLowerCase()}`;
      signers.delete(key);
    }
    for (const p of mutated) {
      const key = `${p.lastName.toLowerCase()}_${p.firstName.toLowerCase()}`;
      signers.set(key, p);
    }
    for (const p of added) {
      const key = `${p.lastName.toLowerCase()}_${p.firstName.toLowerCase()}`;
      signers.set(key, p);
    }
  }

  return Array.from(signers.values());
}

// ─── Badge helpers ────────────────────────────────────────────────────────────

const MUTATION_LABELS: Record<string, { label: string; className: string }> = {
  "status.neu": { label: "Neueintragung", className: "bg-green-100 text-green-700" },
  firmenaenderung: { label: "Firmenänderung", className: "bg-blue-100 text-blue-700" },
  zweckaenderung: { label: "Zweckänderung", className: "bg-purple-100 text-purple-700" },
  adressaenderung: { label: "Adressänderung", className: "bg-amber-100 text-amber-700" },
  aenderungorgane: { label: "Organe", className: "bg-rose-100 text-rose-700" },
  kapitalaenderung: { label: "Kapitaländerung", className: "bg-teal-100 text-teal-700" },
  vermoegenstransfer: { label: "Vermögenstransfer", className: "bg-orange-100 text-orange-700" },
  status: { label: "Status", className: "bg-slate-100 text-slate-600" },
};

function MutationBadge({ mutKey }: { mutKey: string }) {
  const cfg = MUTATION_LABELS[mutKey] ?? { label: mutKey, className: "bg-slate-100 text-slate-500" };
  return (
    <span className={`inline-block text-[11px] font-medium px-1.5 py-0.5 rounded ${cfg.className}`}>
      {cfg.label}
    </span>
  );
}

// ─── Person card ──────────────────────────────────────────────────────────────

function PersonCard({ person, variant = "current" }: { person: Person; variant?: "current" | "added" | "removed" | "mutated" }) {
  const iconMap = {
    current: null,
    added: <UserPlus size={12} className="text-green-600 shrink-0 mt-0.5" />,
    removed: <UserMinus size={12} className="text-red-500 shrink-0 mt-0.5" />,
    mutated: <UserCog size={12} className="text-amber-500 shrink-0 mt-0.5" />,
  };

  const borderMap = {
    current: "border-slate-200",
    added: "border-green-200 bg-green-50/40",
    removed: "border-red-200 bg-red-50/40",
    mutated: "border-amber-200 bg-amber-50/40",
  };

  return (
    <div className={`flex gap-2 rounded-lg border px-3 py-2 ${borderMap[variant]}`}>
      {iconMap[variant]}
      <div className="min-w-0">
        <p className="text-sm font-medium text-slate-800 leading-tight">
          {person.lastName}{person.firstName ? `, ${person.firstName}` : ""}
        </p>
        {person.role && (
          <p className="text-xs text-slate-500 leading-snug">{person.role}</p>
        )}
        {person.signatureType && (
          <p className="text-xs text-slate-400">mit {person.signatureType}</p>
        )}
        {person.bisher && (
          <p className="text-xs text-amber-600">bisher: {person.bisher}</p>
        )}
        {(person.origin || person.city) && (
          <p className="text-xs text-slate-400">
            {[person.origin ? `von ${person.origin}` : null, person.city ? `in ${person.city}` : null]
              .filter(Boolean)
              .join(", ")}
          </p>
        )}
      </div>
    </div>
  );
}

// ─── Timeline entry ───────────────────────────────────────────────────────────

const LONG_MSG_THRESHOLD = 400;

function TimelineEntry({ entry }: { entry: SogcEntry }) {
  const [expanded, setExpanded] = useState(false);
  const isOrgane = entry.mutationTypes.some((m) => m.key === "aenderungorgane");
  const plain = entry.message.replace(/<FT TYPE="[^"]+">([^<]*)<\/FT>/g, "$1");
  const isLong = plain.length > LONG_MSG_THRESHOLD;

  let organe: OrganeChange | null = null;
  if (isOrgane) {
    organe = parseOrganeSection(entry.message);
  }

  // For non-organe entries, show the styled FT message
  // For organe entries, show person cards + rest of message
  const hasPersonChanges = organe && (organe.added.length + organe.removed.length + organe.mutated.length) > 0;

  return (
    <div className="relative pl-6">
      {/* Dot on timeline */}
      <span className="absolute left-0 top-1.5 h-2.5 w-2.5 rounded-full border-2 border-white ring-1 ring-slate-300 bg-white" />

      <div className="mb-1 flex flex-wrap items-center gap-1.5">
        <span className="text-xs font-semibold text-slate-600">
          {new Date(entry.sogcDate).toLocaleDateString("de-CH", { day: "2-digit", month: "2-digit", year: "numeric" })}
        </span>
        <span className="text-xs text-slate-400">{entry.registryOfCommerceCanton}</span>
        {entry.mutationTypes.map((m) => (
          <MutationBadge key={m.id} mutKey={m.key} />
        ))}
      </div>

      {/* Person changes */}
      {hasPersonChanges && organe && (
        <div className="mb-2 grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-1.5">
          {organe.removed.map((p, i) => (
            <PersonCard key={`r-${i}`} person={p} variant="removed" />
          ))}
          {organe.mutated.map((p, i) => (
            <PersonCard key={`m-${i}`} person={p} variant="mutated" />
          ))}
          {organe.added.map((p, i) => (
            <PersonCard key={`a-${i}`} person={p} variant="added" />
          ))}
        </div>
      )}

      {/* Message text */}
      <div className="text-xs text-slate-600 leading-relaxed">
        {isLong && !expanded ? (
          <>
            <span>{renderFtTags(plain.slice(0, LONG_MSG_THRESHOLD))}…</span>
            <button
              type="button"
              onClick={() => setExpanded(true)}
              className="ml-1 inline-flex items-center gap-0.5 text-blue-600 hover:underline"
            >
              mehr <ChevronDown size={11} />
            </button>
          </>
        ) : (
          <>
            {renderFtTags(entry.message)}
            {isLong && (
              <button
                type="button"
                onClick={() => setExpanded(false)}
                className="ml-1 inline-flex items-center gap-0.5 text-blue-600 hover:underline"
              >
                weniger <ChevronUp size={11} />
              </button>
            )}
          </>
        )}
      </div>
    </div>
  );
}

// ─── Public components ────────────────────────────────────────────────────────

export function SogcTimeline({ sogcPubJson }: { sogcPubJson: string | null }) {
  const entries: SogcEntry[] = (() => {
    if (!sogcPubJson) return [];
    try {
      const parsed = JSON.parse(sogcPubJson);
      return Array.isArray(parsed) ? parsed : [];
    } catch {
      return [];
    }
  })();

  if (entries.length === 0) return null;

  // Newest first
  const sorted = [...entries].sort(
    (a, b) => new Date(b.sogcDate).getTime() - new Date(a.sogcDate).getTime()
  );

  return (
    <div className="bg-white rounded-xl border border-slate-200 p-5 shadow-sm">
      <h2 className="text-sm font-semibold text-slate-700 mb-4">SHAB-Verlauf ({entries.length})</h2>
      <div className="relative border-l border-slate-200 ml-1.5 space-y-5">
        {sorted.map((entry) => (
          <TimelineEntry key={entry.sogcId} entry={entry} />
        ))}
      </div>
    </div>
  );
}

export function SignersPanel({ sogcPubJson }: { sogcPubJson: string | null }) {
  const entries: SogcEntry[] = (() => {
    if (!sogcPubJson) return [];
    try {
      const parsed = JSON.parse(sogcPubJson);
      return Array.isArray(parsed) ? parsed : [];
    } catch {
      return [];
    }
  })();

  const hasAnyOrgane = entries.some((e) => e.mutationTypes.some((m) => m.key === "aenderungorgane"));
  if (!hasAnyOrgane) return null;

  const currentSigners = deriveCurrentSigners(entries);

  if (currentSigners.length === 0) return null;

  return (
    <div className="bg-white rounded-xl border border-slate-200 p-5 shadow-sm">
      <h2 className="text-sm font-semibold text-slate-700 mb-4">Aktuelle Zeichnungsberechtigte</h2>
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2">
        {currentSigners.map((p, i) => (
          <PersonCard key={i} person={p} variant="current" />
        ))}
      </div>
    </div>
  );
}
