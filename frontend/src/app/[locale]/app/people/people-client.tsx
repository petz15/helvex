"use client";

import { useState, useCallback } from "react";
import Link from "next/link";
import { useParams, useSearchParams } from "next/navigation";
import useSWR from "swr";
import { AlertCircle, CheckCircle, ChevronDown, ChevronRight, Search, Users, Building2, X } from "lucide-react";
import { searchPersonEntities, fetchPersonAppearances, searchAuditors, reportPersonFlag } from "@/lib/api";
import type { SogcPersonEntity, SogcPersonAppearance, SogcAuditor } from "@/lib/types";

const CONFIDENCE_STYLE: Record<string, string> = {
  high: "bg-emerald-50 text-emerald-700 border border-emerald-200",
  medium: "bg-amber-50 text-amber-700 border border-amber-200",
  low: "bg-red-50 text-red-700 border border-red-200",
};

const LIMIT = 50;

// ── Flag modal ─────────────────────────────────────────────────────────────────

function FlagModal({ entity, onClose }: { entity: SogcPersonEntity; onClose: () => void }) {
  const [flagType, setFlagType] = useState<"should_merge" | "should_split">("should_merge");
  const [reason, setReason] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [done, setDone] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    try {
      await reportPersonFlag(entity.id, { flag_type: flagType, reason: reason || null });
      setDone(true);
    } catch {
      // ignore
    } finally {
      setSubmitting(false);
    }
  }

  const name = [entity.firstname, entity.lastname].filter(Boolean).join(" ") || entity.normalized_key;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
      <div className="bg-white rounded-xl shadow-xl w-full max-w-md p-6 mx-4">
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-sm font-semibold text-slate-800">Report issue — {name}</h3>
          <button onClick={onClose} className="text-slate-400 hover:text-slate-600"><X size={16} /></button>
        </div>
        {done ? (
          <div className="flex items-center gap-2 text-emerald-700 text-sm">
            <CheckCircle size={16} /> Reported. Thank you.
          </div>
        ) : (
          <form onSubmit={submit} className="space-y-4">
            <div>
              <label className="block text-xs font-medium text-slate-600 mb-1">Issue type</label>
              <select
                value={flagType}
                onChange={e => setFlagType(e.target.value as "should_merge" | "should_split")}
                className="w-full rounded border border-slate-200 px-3 py-1.5 text-sm"
              >
                <option value="should_merge">Two entries are the same person (should merge)</option>
                <option value="should_split">This entry contains different people (should split)</option>
              </select>
            </div>
            <div>
              <label className="block text-xs font-medium text-slate-600 mb-1">Reason (optional)</label>
              <textarea
                value={reason}
                onChange={e => setReason(e.target.value)}
                rows={3}
                placeholder="Describe the issue…"
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

// ── Person entity card ─────────────────────────────────────────────────────────

function PersonEntityCard({ entity, locale }: { entity: SogcPersonEntity; locale: string }) {
  const [expanded, setExpanded] = useState(false);
  const [showFlag, setShowFlag] = useState(false);

  const { data: appearances = [] } = useSWR(
    expanded ? `person-appearances-${entity.id}` : null,
    () => fetchPersonAppearances(entity.id),
    { revalidateOnFocus: false },
  );

  const name = [entity.firstname, entity.lastname].filter(Boolean).join(" ") || entity.normalized_key;

  return (
    <>
      <div className="bg-white rounded-xl border border-slate-200 shadow-sm overflow-hidden">
        <div className="px-4 py-3 flex items-start gap-3">
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2 flex-wrap">
              <span className="text-sm font-semibold text-slate-800">{name}</span>
              {entity.is_verified && (
                <CheckCircle size={13} className="text-emerald-500 shrink-0" aria-label="Verified identity" />
              )}
              <span className={`text-[10px] px-1.5 py-0.5 rounded font-medium ${CONFIDENCE_STYLE[entity.confidence_level] ?? CONFIDENCE_STYLE.medium}`}>
                {entity.confidence_level}
              </span>
              {entity.is_foreign && entity.nationality && (
                <span className="text-[10px] text-slate-400 italic">{entity.nationality} national</span>
              )}
            </div>

            {entity.hometown_municipality && (
              <p className="text-xs text-slate-500 mt-0.5">von {entity.hometown_municipality}</p>
            )}

            {entity.confidence_level === "low" && (
              <p className="text-[10px] text-amber-600 mt-1 flex items-center gap-1">
                <AlertCircle size={10} />
                Foreign national — identity match is approximate
              </p>
            )}

            <div className="flex flex-wrap gap-1.5 mt-2">
              {entity.active_company_count > 0 && (
                <span className="text-[11px] bg-blue-50 text-blue-700 px-2 py-0.5 rounded-full border border-blue-100">
                  {entity.active_company_count} active {entity.active_company_count === 1 ? "company" : "companies"}
                </span>
              )}
              {entity.appearance_count > entity.active_company_count && (
                <span className="text-[11px] bg-slate-100 text-slate-500 px-2 py-0.5 rounded-full">
                  {entity.appearance_count} total appearances
                </span>
              )}
            </div>
          </div>

          <div className="flex items-center gap-1 shrink-0">
            <button
              onClick={() => setShowFlag(true)}
              className="text-slate-300 hover:text-amber-500 transition-colors p-1"
              title="Report identity issue"
            >
              <AlertCircle size={14} />
            </button>
            <button
              onClick={() => setExpanded(v => !v)}
              className="text-slate-400 hover:text-slate-600 p-1"
              title={expanded ? "Collapse" : "Show appearances"}
            >
              {expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
            </button>
          </div>
        </div>

        {expanded && (
          <div className="border-t border-slate-100 bg-slate-50/50 px-4 py-3 space-y-1.5">
            {appearances.length === 0 ? (
              <p className="text-xs text-slate-400">No appearances loaded.</p>
            ) : (
              appearances.map(a => (
                <div key={a.id} className="flex items-center gap-2 text-xs">
                  <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${a.is_current ? "bg-emerald-400" : "bg-slate-300"}`} />
                  <Link
                    href={`/${locale}/app/companies/${a.company_uid}`}
                    className="text-blue-600 hover:underline font-mono text-[10px]"
                  >
                    {a.company_uid}
                  </Link>
                  {a.role && <span className="text-slate-600 truncate">{a.role}</span>}
                  {a.pub_date && <span className="text-slate-400 shrink-0">{a.pub_date.slice(0, 7)}</span>}
                  {!a.is_current && a.bisher_role && (
                    <span className="text-slate-400 text-[10px] italic">→ {a.bisher_role}</span>
                  )}
                </div>
              ))
            )}
          </div>
        )}
      </div>

      {showFlag && <FlagModal entity={entity} onClose={() => setShowFlag(false)} />}
    </>
  );
}

// ── Auditor card ───────────────────────────────────────────────────────────────

function AuditorCard({ auditor, locale }: { auditor: SogcAuditor; locale: string }) {
  return (
    <div className="bg-white rounded-xl border border-slate-200 shadow-sm px-4 py-3">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="text-sm font-semibold text-slate-800">{auditor.auditor_name}</p>
          <div className="flex flex-wrap gap-2 mt-1 text-[11px] text-slate-500">
            {auditor.auditor_location && <span>{auditor.auditor_location}</span>}
            {auditor.auditor_uid && (
              <span className="font-mono text-[10px]">{auditor.auditor_uid}</span>
            )}
            {auditor.auditor_legal_form && <span>{auditor.auditor_legal_form}</span>}
          </div>
        </div>
        {auditor.company_uid && (
          <Link
            href={`/${locale}/app/companies/${auditor.company_uid}`}
            className="text-[11px] text-blue-600 hover:underline shrink-0"
          >
            {auditor.company_uid}
          </Link>
        )}
      </div>
    </div>
  );
}

// ── Main component ─────────────────────────────────────────────────────────────

export function PeopleClient() {
  const params = useParams();
  const searchParams = useSearchParams();
  const locale = (params?.locale as string) ?? "de";

  const [tab, setTab] = useState<"persons" | "auditors">("persons");
  const [q, setQ] = useState(searchParams?.get("q") ?? "");
  const [hometown, setHometown] = useState("");
  const [confidenceFilter, setConfidenceFilter] = useState("");
  const [currentOnly, setCurrentOnly] = useState(true);
  const [offset, setOffset] = useState(0);

  const [auditorQ, setAuditorQ] = useState("");
  const [auditorCurrentOnly, setAuditorCurrentOnly] = useState(true);
  const [auditorOffset, setAuditorOffset] = useState(0);

  const { data: persons = [], isLoading: personsLoading } = useSWR(
    tab === "persons" ? ["people-search", q, hometown, confidenceFilter, currentOnly, offset] : null,
    () => searchPersonEntities({
      q: q || undefined,
      hometown: hometown || undefined,
      confidence_level: confidenceFilter || undefined,
      is_current: currentOnly ? true : undefined,
      limit: LIMIT,
      offset,
    }),
    { keepPreviousData: true },
  );

  const { data: auditors = [], isLoading: auditorsLoading } = useSWR(
    tab === "auditors" ? ["auditors-search", auditorQ, auditorCurrentOnly, auditorOffset] : null,
    () => searchAuditors({
      q: auditorQ || undefined,
      is_current: auditorCurrentOnly ? true : undefined,
      limit: LIMIT,
      offset: auditorOffset,
    }),
    { keepPreviousData: true },
  );

  const resetOffset = useCallback(() => setOffset(0), []);
  const resetAuditorOffset = useCallback(() => setAuditorOffset(0), []);

  return (
    <div className="max-w-4xl mx-auto p-6 space-y-5">
      <div className="flex items-center gap-3">
        <Users size={20} className="text-slate-400" />
        <h1 className="text-xl font-semibold text-slate-800">People</h1>
      </div>

      {/* Tabs */}
      <div className="flex border-b border-slate-200">
        {(["persons", "auditors"] as const).map(t => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors ${
              tab === t
                ? "border-blue-500 text-blue-700"
                : "border-transparent text-slate-500 hover:text-slate-700"
            }`}
          >
            {t === "persons" ? <><Users size={13} className="inline mr-1.5" />Persons</> : <><Building2 size={13} className="inline mr-1.5" />Auditors</>}
          </button>
        ))}
      </div>

      {tab === "persons" && (
        <>
          {/* Filter bar */}
          <div className="flex flex-wrap gap-2 items-center">
            <div className="relative">
              <Search size={13} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-400" />
              <input
                type="search"
                placeholder="Search by name…"
                value={q}
                onChange={e => { setQ(e.target.value); resetOffset(); }}
                className="pl-8 pr-3 py-1.5 rounded-lg border border-slate-200 text-sm focus:outline-none focus:ring-2 focus:ring-blue-200 w-56"
              />
            </div>
            <input
              type="text"
              placeholder="Hometown (von X)…"
              value={hometown}
              onChange={e => { setHometown(e.target.value); resetOffset(); }}
              className="px-3 py-1.5 rounded-lg border border-slate-200 text-sm focus:outline-none focus:ring-2 focus:ring-blue-200 w-44"
            />
            <select
              value={confidenceFilter}
              onChange={e => { setConfidenceFilter(e.target.value); resetOffset(); }}
              className="px-3 py-1.5 rounded-lg border border-slate-200 text-sm text-slate-600 bg-white"
            >
              <option value="">All confidence</option>
              <option value="high">High</option>
              <option value="medium">Medium</option>
              <option value="low">Low (foreign)</option>
            </select>
            <label className="flex items-center gap-1.5 text-sm text-slate-600 cursor-pointer select-none">
              <input
                type="checkbox"
                checked={currentOnly}
                onChange={e => { setCurrentOnly(e.target.checked); resetOffset(); }}
                className="rounded border-slate-300"
              />
              Current only
            </label>
          </div>

          {/* Results */}
          {personsLoading ? (
            <p className="text-sm text-slate-400">Loading…</p>
          ) : persons.length === 0 ? (
            <p className="text-sm text-slate-400">No results.</p>
          ) : (
            <div className="space-y-2">
              {persons.map(entity => (
                <PersonEntityCard key={entity.id} entity={entity} locale={locale} />
              ))}
            </div>
          )}

          {/* Pagination */}
          <div className="flex gap-2">
            <button
              disabled={offset === 0}
              onClick={() => setOffset(Math.max(0, offset - LIMIT))}
              className="text-sm px-3 py-1.5 rounded border border-slate-200 disabled:opacity-40 hover:bg-slate-50"
            >
              Previous
            </button>
            <button
              disabled={persons.length < LIMIT}
              onClick={() => setOffset(offset + LIMIT)}
              className="text-sm px-3 py-1.5 rounded border border-slate-200 disabled:opacity-40 hover:bg-slate-50"
            >
              Next
            </button>
          </div>
        </>
      )}

      {tab === "auditors" && (
        <>
          <div className="flex flex-wrap gap-2 items-center">
            <div className="relative">
              <Search size={13} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-400" />
              <input
                type="search"
                placeholder="Search auditor name…"
                value={auditorQ}
                onChange={e => { setAuditorQ(e.target.value); resetAuditorOffset(); }}
                className="pl-8 pr-3 py-1.5 rounded-lg border border-slate-200 text-sm focus:outline-none focus:ring-2 focus:ring-blue-200 w-64"
              />
            </div>
            <label className="flex items-center gap-1.5 text-sm text-slate-600 cursor-pointer select-none">
              <input
                type="checkbox"
                checked={auditorCurrentOnly}
                onChange={e => { setAuditorCurrentOnly(e.target.checked); resetAuditorOffset(); }}
                className="rounded border-slate-300"
              />
              Current only
            </label>
          </div>

          {auditorsLoading ? (
            <p className="text-sm text-slate-400">Loading…</p>
          ) : auditors.length === 0 ? (
            <p className="text-sm text-slate-400">No results.</p>
          ) : (
            <div className="space-y-2">
              {auditors.map(a => (
                <AuditorCard key={a.id} auditor={a} locale={locale} />
              ))}
            </div>
          )}

          <div className="flex gap-2">
            <button
              disabled={auditorOffset === 0}
              onClick={() => setAuditorOffset(Math.max(0, auditorOffset - LIMIT))}
              className="text-sm px-3 py-1.5 rounded border border-slate-200 disabled:opacity-40 hover:bg-slate-50"
            >
              Previous
            </button>
            <button
              disabled={auditors.length < LIMIT}
              onClick={() => setAuditorOffset(auditorOffset + LIMIT)}
              className="text-sm px-3 py-1.5 rounded border border-slate-200 disabled:opacity-40 hover:bg-slate-50"
            >
              Next
            </button>
          </div>
        </>
      )}
    </div>
  );
}
