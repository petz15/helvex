"use client";

import { useState } from "react";
import Link from "next/link";
import useSWR from "swr";
import { useParams } from "next/navigation";
import { Users, Building2, AlertCircle, CheckCircle, X } from "lucide-react";
import { fetchCompanyPersons, fetchCompanyAuditors, reportPersonFlag } from "@/lib/api";
import type { SogcPersonAppearance, SogcAuditor } from "@/lib/types";

const CONFIDENCE_BADGE: Record<string, string> = {
  high: "bg-emerald-50 text-emerald-700 border-emerald-200",
  medium: "bg-amber-50 text-amber-700 border-amber-200",
  low: "bg-red-50 text-red-700 border-red-200",
};

const CONFIDENCE_LABEL: Record<string, string> = {
  high: "High",
  medium: "Medium",
  low: "Low — approx.",
};

function ConfidenceBadge({ level }: { level: string }) {
  return (
    <span className={`text-[10px] px-1.5 py-0.5 rounded border font-medium ${CONFIDENCE_BADGE[level] ?? CONFIDENCE_BADGE.medium}`}>
      {CONFIDENCE_LABEL[level] ?? level}
    </span>
  );
}

interface FlagModalProps {
  entityId: number;
  personName: string;
  onClose: () => void;
}

function FlagModal({ entityId, personName, onClose }: FlagModalProps) {
  const [flagType, setFlagType] = useState<"should_merge" | "should_split">("should_merge");
  const [reason, setReason] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [done, setDone] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    try {
      await reportPersonFlag(entityId, { flag_type: flagType, reason: reason || null });
      setDone(true);
    } catch {
      // ignore
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
      <div className="bg-white rounded-xl shadow-xl w-full max-w-md p-6 mx-4">
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-sm font-semibold text-slate-800">Report identity issue — {personName}</h3>
          <button onClick={onClose} className="text-slate-400 hover:text-slate-600"><X size={16} /></button>
        </div>
        {done ? (
          <div className="flex items-center gap-2 text-emerald-700 text-sm">
            <CheckCircle size={16} /> Reported. Thank you.
          </div>
        ) : (
          <form onSubmit={handleSubmit} className="space-y-4">
            <div>
              <label className="block text-xs font-medium text-slate-600 mb-1">Issue type</label>
              <select
                value={flagType}
                onChange={e => setFlagType(e.target.value as "should_merge" | "should_split")}
                className="w-full rounded border border-slate-200 px-3 py-1.5 text-sm"
              >
                <option value="should_merge">These two entries are the same person (merge)</option>
                <option value="should_split">This entry is a different person (split)</option>
              </select>
            </div>
            <div>
              <label className="block text-xs font-medium text-slate-600 mb-1">Reason (optional)</label>
              <textarea
                value={reason}
                onChange={e => setReason(e.target.value)}
                placeholder="Describe the issue…"
                rows={3}
                className="w-full rounded border border-slate-200 px-3 py-1.5 text-sm resize-none focus:outline-none focus:ring-2 focus:ring-blue-200"
              />
            </div>
            <div className="flex gap-2 justify-end">
              <button type="button" onClick={onClose} className="px-3 py-1.5 text-sm text-slate-600 hover:text-slate-800">Cancel</button>
              <button type="submit" disabled={submitting} className="px-3 py-1.5 text-sm bg-blue-600 text-white rounded hover:bg-blue-700 disabled:opacity-50">
                {submitting ? "Sending…" : "Submit"}
              </button>
            </div>
          </form>
        )}
      </div>
    </div>
  );
}

interface PersonCardProps {
  appearance: SogcPersonAppearance;
  confidence?: string;
  activeCompanyCount?: number;
  locale: string;
}

function PersonCard({ appearance, confidence = "medium", activeCompanyCount = 1, locale }: PersonCardProps) {
  const [showFlag, setShowFlag] = useState(false);
  const name = [appearance.title, appearance.role].filter(Boolean).join(" ");
  const displayName = appearance.raw_excerpt
    ? appearance.raw_excerpt.split(",").slice(0, 2).join(",").trim()
    : String(appearance.person_entity_id);

  return (
    <>
      <div className="flex flex-col gap-1 rounded-lg border border-slate-200 px-3 py-2 bg-slate-50/50 group">
        <div className="flex items-start justify-between gap-1">
          <Link
            href={`/${locale}/app/people?entity=${appearance.person_entity_id}`}
            className="text-sm font-medium text-slate-800 hover:text-blue-600 leading-tight truncate"
          >
            {displayName}
          </Link>
          <button
            onClick={() => setShowFlag(true)}
            className="opacity-0 group-hover:opacity-100 transition-opacity text-slate-300 hover:text-amber-500 shrink-0"
            title="Report identity issue"
          >
            <AlertCircle size={13} />
          </button>
        </div>
        {appearance.role && (
          <p className="text-xs text-slate-500 leading-tight truncate">{appearance.role}</p>
        )}
        <div className="flex flex-wrap items-center gap-1.5 mt-0.5">
          {confidence && <ConfidenceBadge level={confidence} />}
          {activeCompanyCount > 1 && (
            <Link
              href={`/${locale}/app/people?entity=${appearance.person_entity_id}`}
              className="text-[10px] text-blue-600 hover:underline"
            >
              +{activeCompanyCount - 1} other {activeCompanyCount - 1 === 1 ? "company" : "companies"}
            </Link>
          )}
        </div>
      </div>
      {showFlag && (
        <FlagModal
          entityId={appearance.person_entity_id}
          personName={displayName}
          onClose={() => setShowFlag(false)}
        />
      )}
    </>
  );
}

function AuditorCard({ auditor }: { auditor: SogcAuditor }) {
  return (
    <div className="flex flex-col gap-1 rounded-lg border border-slate-200 px-3 py-2 bg-slate-50/50">
      <p className="text-sm font-medium text-slate-800 leading-tight">{auditor.auditor_name}</p>
      <div className="flex flex-wrap items-center gap-2 text-[11px] text-slate-500">
        {auditor.auditor_location && <span>{auditor.auditor_location}</span>}
        {auditor.auditor_uid && (
          <span className="font-mono text-[10px] text-slate-400">{auditor.auditor_uid}</span>
        )}
        {auditor.auditor_legal_form && <span>{auditor.auditor_legal_form}</span>}
      </div>
    </div>
  );
}

export function BoardPanel({ companyUid }: { companyUid: string }) {
  const params = useParams();
  const locale = (params?.locale as string) ?? "de";

  const { data: persons = [] } = useSWR(
    `company-persons-${companyUid}`,
    () => fetchCompanyPersons(companyUid, true),
    { revalidateOnFocus: false },
  );
  const { data: auditors = [] } = useSWR(
    `company-auditors-${companyUid}`,
    () => fetchCompanyAuditors(companyUid, true),
    { revalidateOnFocus: false },
  );

  if (persons.length === 0 && auditors.length === 0) return null;

  const directors = persons.filter(p => p.role_category === "director");
  const officers = persons.filter(p => p.role_category === "officer");
  const other = persons.filter(p => p.role_category === "other" || !p.role_category);

  return (
    <div className="bg-white rounded-xl border border-slate-200 p-5 shadow-sm">
      <h2 className="text-sm font-semibold text-slate-700 mb-4 flex items-center gap-1.5">
        <Users size={15} className="text-slate-400" />
        Board &amp; Officers
      </h2>

      {directors.length > 0 && (
        <div className="mb-4">
          <p className="text-[11px] font-semibold text-slate-400 uppercase tracking-wide mb-2">Board of Directors</p>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2">
            {directors.map(p => (
              <PersonCard key={p.id} appearance={p} locale={locale} />
            ))}
          </div>
        </div>
      )}

      {officers.length > 0 && (
        <div className="mb-4">
          <p className="text-[11px] font-semibold text-slate-400 uppercase tracking-wide mb-2">Management</p>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2">
            {officers.map(p => (
              <PersonCard key={p.id} appearance={p} locale={locale} />
            ))}
          </div>
        </div>
      )}

      {other.length > 0 && (
        <div className="mb-4">
          <p className="text-[11px] font-semibold text-slate-400 uppercase tracking-wide mb-2">Other</p>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
            {other.map(p => (
              <PersonCard key={p.id} appearance={p} locale={locale} />
            ))}
          </div>
        </div>
      )}

      {auditors.length > 0 && (
        <div>
          <p className="text-[11px] font-semibold text-slate-400 uppercase tracking-wide mb-2 flex items-center gap-1">
            <Building2 size={11} />
            Auditors
          </p>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
            {auditors.map(a => (
              <AuditorCard key={a.id} auditor={a} />
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
