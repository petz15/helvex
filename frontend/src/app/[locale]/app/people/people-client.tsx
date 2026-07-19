"use client";

import { useState, useMemo } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import useSWR from "swr";
import {
  AlertCircle,
  CheckCircle,
  ChevronDown,
  ChevronRight,
  Search,
  Users,
  Building2,
  X,
} from "lucide-react";
import {
  searchPersonEntities,
  searchAuditors,
  reportPersonFlag,
  fetchCurrentUser,
} from "@/lib/api";
import type {
  SogcPersonEntity,
  SogcAuditor,
} from "@/lib/types";
import { useI18n } from "@/i18n/context";

// ── Constants ──────────────────────────────────────────────────────────────────

const CONFIDENCE_STYLE: Record<string, string> = {
  high:   "bg-emerald-50 text-emerald-700 border border-emerald-200",
  medium: "bg-amber-50 text-amber-700 border border-amber-200",
  low:    "bg-red-50 text-red-700 border border-red-200",
};

const LIMIT = 50;
const COLLAPSE_THRESHOLD = 10;

// ── Utilities (used by AuditorClientsTimeline) ─────────────────────────────────

function dateToDecimal(dateStr: string): number {
  const d = new Date(dateStr);
  return d.getFullYear() + d.getMonth() / 12;
}

function clamp(v: number, lo: number, hi: number): number {
  return Math.max(lo, Math.min(hi, v));
}

// ── Flag modal ─────────────────────────────────────────────────────────────────

function FlagModal({ entity, onClose }: { entity: SogcPersonEntity; onClose: () => void }) {
  const { dict } = useI18n();
  const t = dict.app.people;
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
    } catch { /* ignore */ }
    finally { setSubmitting(false); }
  }

  const name = [entity.firstname, entity.lastname].filter(Boolean).join(" ") || entity.normalized_key;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
      <div className="bg-white rounded-xl shadow-xl w-full max-w-md p-6 mx-4">
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-sm font-semibold text-slate-800">{t.reportIssue.replace("{name}", name)}</h3>
          <button onClick={onClose} className="text-slate-400 hover:text-slate-600"><X size={16} /></button>
        </div>
        {done ? (
          <div className="flex items-center gap-2 text-emerald-700 text-sm">
            <CheckCircle size={16} /> {t.reported}
          </div>
        ) : (
          <form onSubmit={submit} className="space-y-4">
            <div>
              <label className="block text-xs font-medium text-slate-600 mb-1">{t.issueType}</label>
              <select
                value={flagType}
                onChange={e => setFlagType(e.target.value as "should_merge" | "should_split")}
                className="w-full rounded border border-slate-200 px-3 py-1.5 text-sm"
              >
                <option value="should_merge">{t.shouldMerge}</option>
                <option value="should_split">{t.shouldSplit}</option>
              </select>
            </div>
            <div>
              <label className="block text-xs font-medium text-slate-600 mb-1">{t.reasonOptional}</label>
              <textarea
                value={reason}
                onChange={e => setReason(e.target.value)}
                rows={3}
                placeholder={t.describeIssue}
                className="w-full rounded border border-slate-200 px-3 py-1.5 text-sm resize-none focus:outline-none focus:ring-2 focus:ring-blue-200"
              />
            </div>
            <div className="flex gap-2 justify-end">
              <button type="button" onClick={onClose} className="px-3 py-1.5 text-sm text-slate-600 hover:text-slate-800">{t.cancel}</button>
              <button type="submit" disabled={submitting} className="px-3 py-1.5 text-sm bg-blue-600 text-white rounded hover:bg-blue-700 disabled:opacity-50">
                {submitting ? t.sending : t.submit}
              </button>
            </div>
          </form>
        )}
      </div>
    </div>
  );
}

// ── Auditor clients timeline ───────────────────────────────────────────────────

interface AuditorGroup {
  key: string;
  name: string | null;
  uid: string | null;
  location: string | null;
  legal_form: string | null;
  companies: Array<{
    id: number | null;
    uid: string | null;
    name: string | null;
    is_current: boolean | null;
    pub_date: string | null;
  }>;
}

function AuditorClientsTimeline({ group, locale }: { group: AuditorGroup; locale: string }) {
  const { dict } = useI18n();
  const t = dict.app.people;
  const today = new Date().toISOString().slice(0, 10);
  const companies = group.companies;

  const dates = companies.map(c => c.pub_date).filter(Boolean) as string[];
  if (!dates.length) {
    return <p className="text-xs text-slate-400 py-3">{t.noDateData}</p>;
  }

  const minYear = Math.floor(Math.min(...dates.map(d => new Date(d).getFullYear())));
  const maxDecimal = dateToDecimal(today) + 0.4;
  const span = maxDecimal - minYear;

  function pos(dateStr: string): number {
    return clamp(((dateToDecimal(dateStr) - minYear) / span) * 100, 0, 100);
  }

  const todayPct = pos(today);
  const yearCount = Math.ceil(maxDecimal) - minYear;
  const tickEvery = yearCount > 20 ? 5 : yearCount > 10 ? 2 : 1;
  const years: number[] = [];
  for (let y = minYear; y <= Math.ceil(maxDecimal); y++) years.push(y);

  return (
    <div className="overflow-x-auto">
      <div style={{ minWidth: 520 }}>
        <div className="grid" style={{ gridTemplateColumns: "192px 1fr" }}>
          <div />
          <div className="relative h-7 border-b border-slate-200 mb-0.5">
            {years.map(y => (
              <div
                key={y}
                className="absolute bottom-0 flex flex-col items-center"
                style={{ left: `${pos(`${y}-01-01`)}%`, transform: "translateX(-50%)" }}
              >
                <div className="w-px h-2 bg-slate-300" />
                {y % tickEvery === 0 && (
                  <span className="text-[10px] text-slate-400 font-mono leading-none mt-0.5 select-none">{y}</span>
                )}
              </div>
            ))}
            <div
              className="absolute top-0 bottom-0 border-l border-red-400 border-dashed opacity-70"
              style={{ left: `${todayPct}%` }}
            >
              <span className="absolute -top-0 left-1 text-[9px] text-red-400 font-mono whitespace-nowrap leading-none">{t.now}</span>
            </div>
          </div>

          {companies.map((c, i) => {
            if (!c.pub_date) return null;
            const p = pos(c.pub_date);
            const barWidth = Math.max(1, (1 / (12 * span)) * 100);
            const isActive = !!c.is_current;

            return (
              <div key={i} className="contents">
                <div className="flex items-center gap-1.5 pr-3 py-1.5 border-b border-slate-100">
                  <span
                    className="w-1.5 h-1.5 rounded-full shrink-0"
                    style={{ background: isActive ? "#10b981" : "#cbd5e1" }}
                  />
                  {c.id ? (
                    <Link href={`/${locale}/app/companies/${c.id}`} className="text-xs text-blue-600 hover:underline truncate">
                      {c.name ?? c.uid}
                    </Link>
                  ) : (
                    <span className="text-xs text-slate-600 truncate">{c.name ?? c.uid ?? "—"}</span>
                  )}
                </div>
                <div className="relative h-8 border-b border-slate-100 bg-slate-50/20">
                  {years.map(y => (
                    <div key={y} className="absolute top-0 bottom-0 border-l border-slate-100" style={{ left: `${pos(`${y}-01-01`)}%` }} />
                  ))}
                  <div className="absolute top-0 bottom-0 border-l border-red-300 border-dashed opacity-40" style={{ left: `${todayPct}%` }} />
                  <div
                    className="absolute top-1.5 h-5"
                    style={{
                      left: `${p}%`,
                      width: `${Math.max(barWidth, 2)}%`,
                      background: isActive ? "#2563eb" : "#94a3b8",
                      opacity: isActive ? 1 : 0.55,
                    }}
                    title={`${c.name ?? c.uid} · ${c.pub_date}`}
                  />
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

// ── Auditor detail panel ───────────────────────────────────────────────────────

function AuditorDetailPanel({
  group,
  locale,
  onClose,
}: {
  group: AuditorGroup;
  locale: string;
  onClose: () => void;
}) {
  const { dict } = useI18n();
  const t = dict.app.people;
  return (
    <div className="bg-white rounded-xl border border-slate-200 shadow-md overflow-hidden">
      <div className="px-4 py-3 border-b border-slate-200 bg-slate-50/50 flex items-start gap-3">
        <div className="flex-1 min-w-0">
          <p className="text-sm font-semibold text-slate-800">{group.name ?? "—"}</p>
          <div className="flex flex-wrap gap-2 mt-0.5 text-xs text-slate-500">
            {group.location && <span>{group.location}</span>}
            {group.uid && <span className="font-mono text-[10px]">{group.uid}</span>}
            {group.legal_form && <span>{group.legal_form}</span>}
          </div>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          <span className="text-xs text-slate-500">
            <span className="font-mono font-semibold text-slate-700">{group.companies.length}</span>{" "}
            {group.companies.length === 1 ? t.client.replace("{count} ", "") : t.clients.replace("{count} ", "")}
          </span>
          <button onClick={onClose} className="text-slate-400 hover:text-slate-600 p-0.5" title={t.close}>
            <X size={14} />
          </button>
        </div>
      </div>
      <div className="p-4">
        <p className="text-[11px] font-medium text-slate-500 uppercase tracking-wide mb-3">{t.clientTimeline}</p>
        <AuditorClientsTimeline group={group} locale={locale} />
      </div>
    </div>
  );
}

// ── Person entity card (navigates to detail page) ──────────────────────────────

function PersonEntityCard({
  entity,
  locale,
  isSuperAdmin,
}: {
  entity: SogcPersonEntity;
  locale: string;
  isSuperAdmin?: boolean;
}) {
  const { dict } = useI18n();
  const t = dict.app.people;
  const [showFlag, setShowFlag] = useState(false);
  const name = [entity.firstname, entity.lastname].filter(Boolean).join(" ") || entity.normalized_key;

  return (
    <>
      <Link href={`/${locale}/app/people/${entity.id}`} className="block group">
        <div className="bg-white rounded-xl border border-slate-200 hover:border-blue-300 shadow-sm overflow-hidden transition-colors">
          <div className="px-4 py-3 flex items-start gap-3">
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-2 flex-wrap">
                <span className="text-sm font-semibold text-slate-800">{name}</span>
                {entity.is_verified && (
                  <CheckCircle size={13} className="text-emerald-500 shrink-0" aria-label={t.verifiedIdentity} />
                )}
                {isSuperAdmin && (
                  <span
                    className={`text-[10px] font-medium px-1.5 py-0.5 rounded ${CONFIDENCE_STYLE[entity.confidence_level] ?? CONFIDENCE_STYLE.medium}`}
                  >
                    {entity.confidence_level}
                  </span>
                )}
                {entity.is_foreign && entity.nationality && (
                  <span className="text-[9px] text-slate-400 italic">{t.nationalSuffix.replace("{nat}", entity.nationality)}</span>
                )}
              </div>

              <div className="flex flex-wrap gap-x-3 mt-0.5">
                {entity.hometown_municipality && (
                  <p className="text-xs text-slate-500">{t.fromMunicipality.replace("{place}", entity.hometown_municipality)}</p>
                )}
                {entity.current_residence_municipality && (
                  <p className="text-xs text-slate-500">{t.inMunicipality.replace("{place}", entity.current_residence_municipality)}</p>
                )}
              </div>

              {isSuperAdmin && entity.confidence_level === "low" && (
                <p className="text-[9px] text-amber-600/80 mt-0.5 flex items-center gap-1">
                  <AlertCircle size={9} />
                  {t.identityApprox}
                </p>
              )}

              <div className="flex flex-wrap gap-1.5 mt-2">
                {entity.active_company_count > 0 && (
                  <span className="text-[11px] bg-blue-50 text-blue-700 px-2 py-0.5 rounded-full border border-blue-100">
                    {(entity.active_company_count === 1 ? t.activeCompany : t.activeCompanies).replace("{count}", String(entity.active_company_count))}
                  </span>
                )}
                {entity.appearance_count > entity.active_company_count && (
                  <span className="text-[11px] bg-slate-100 text-slate-500 px-2 py-0.5 rounded-full">
                    {t.totalAppearances.replace("{count}", String(entity.appearance_count))}
                  </span>
                )}
              </div>
            </div>

            <div className="flex items-center gap-1 shrink-0">
              <button
                onClick={e => { e.preventDefault(); e.stopPropagation(); setShowFlag(true); }}
                className="text-slate-300 hover:text-amber-500 transition-colors p-1 relative z-10"
                title={t.reportIssueTitle}
              >
                <AlertCircle size={14} />
              </button>
              <span className="text-slate-400 group-hover:text-blue-400 transition-colors p-1">
                <ChevronRight size={14} />
              </span>
            </div>
          </div>
        </div>
      </Link>

      {showFlag && <FlagModal entity={entity} onClose={() => setShowFlag(false)} />}
    </>
  );
}

// ── Auditor card ───────────────────────────────────────────────────────────────

function groupAuditors(rows: SogcAuditor[]): AuditorGroup[] {
  const map = new Map<string, AuditorGroup>();
  for (const r of rows) {
    const key = r.auditor_uid ?? r.auditor_name_normalized ?? r.auditor_name ?? String(r.id);
    if (!map.has(key)) {
      map.set(key, { key, name: r.auditor_name, uid: r.auditor_uid, location: r.auditor_location, legal_form: r.auditor_legal_form, companies: [] });
    }
    const group = map.get(key)!;
    if (r.company_uid || r.company_id) {
      const alreadyIn = group.companies.some(c => c.uid === r.company_uid && c.id === r.company_id);
      if (!alreadyIn) {
        group.companies.push({ id: r.company_id, uid: r.company_uid, name: r.company_name, is_current: r.is_current, pub_date: r.pub_date });
      }
    }
  }
  return Array.from(map.values());
}

function AuditorCard({
  group,
  locale,
  isSelected,
  onSelect,
}: {
  group: AuditorGroup;
  locale: string;
  isSelected: boolean;
  onSelect: (key: string) => void;
}) {
  const { dict } = useI18n();
  const t = dict.app.people;
  const [expanded, setExpanded] = useState(false);
  const showToggle = group.companies.length > COLLAPSE_THRESHOLD;
  const visible = showToggle && !expanded ? group.companies.slice(0, COLLAPSE_THRESHOLD) : group.companies;

  return (
    <div
      className={`bg-white rounded-xl border shadow-sm overflow-hidden cursor-pointer transition-colors ${
        isSelected ? "border-blue-300 ring-1 ring-blue-200" : "border-slate-200 hover:border-slate-300"
      }`}
      onClick={() => onSelect(group.key)}
    >
      <div className="px-4 py-3">
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0">
            <p className="text-sm font-semibold text-slate-800">{group.name ?? "—"}</p>
            <div className="flex flex-wrap gap-2 mt-0.5 text-[11px] text-slate-500">
              {group.location && <span>{group.location}</span>}
              {group.uid && <span className="font-mono text-[10px]">{group.uid}</span>}
              {group.legal_form && <span>{group.legal_form}</span>}
            </div>
          </div>
          <div className="flex items-center gap-1.5 shrink-0">
            <span className="text-[11px] bg-slate-100 text-slate-500 px-2 py-0.5 rounded-full">
              {(group.companies.length === 1 ? t.client : t.clients).replace("{count}", String(group.companies.length))}
            </span>
            <span className="text-slate-400 p-0.5">
              {isSelected ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
            </span>
          </div>
        </div>

        {!isSelected && group.companies.length > 0 && (
          <div className="mt-2 space-y-1">
            {visible.map((c, i) => (
              <div key={i} className="flex items-center gap-2 text-xs">
                <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${c.is_current ? "bg-emerald-400" : "bg-slate-300"}`} />
                {c.id ? (
                  <Link href={`/${locale}/app/companies/${c.id}`} className="text-blue-600 hover:underline truncate" onClick={e => e.stopPropagation()}>
                    {c.name ?? c.uid ?? String(c.id)}
                  </Link>
                ) : (
                  <span className="text-slate-600 truncate">{c.name ?? c.uid ?? "—"}</span>
                )}
                {c.pub_date && (
                  <span className="text-slate-400 shrink-0 ml-auto">{c.pub_date.slice(0, 7)}</span>
                )}
              </div>
            ))}
            {showToggle && (
              <button
                onClick={e => { e.stopPropagation(); setExpanded(v => !v); }}
                className="flex items-center gap-1 text-[11px] text-blue-600 hover:text-blue-800 mt-1"
              >
                {expanded ? <ChevronDown size={11} /> : <ChevronRight size={11} />}
                {expanded ? t.showLess : t.showMore.replace("{count}", String(group.companies.length - COLLAPSE_THRESHOLD))}
              </button>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

// ── Main component ─────────────────────────────────────────────────────────────

export function PeopleClient({
  initialQ,
  initialTab,
}: {
  initialQ?: string;
  initialTab?: "persons" | "auditors";
} = {}) {
  const params = useParams();
  const locale = (params?.locale as string) ?? "de";
  const { dict } = useI18n();
  const t = dict.app.people;

  const { data: me } = useSWR("me", fetchCurrentUser, { revalidateOnFocus: false });
  const isSuperAdmin = !!me?.is_superadmin;

  const [tab, setTab] = useState<"persons" | "auditors">(initialTab ?? "persons");

  // Person filters
  const [q, setQ] = useState(initialTab !== "auditors" ? (initialQ ?? "") : "");
  const [hometown, setHometown] = useState("");
  const [nationality, setNationality] = useState("");
  const [minCompanies, setMinCompanies] = useState("");
  const [confidenceFilter, setConfidenceFilter] = useState("");
  const [sortBy, setSortBy] = useState("companies");
  const [offset, setOffset] = useState(0);

  // Auditor filters
  const [auditorQ, setAuditorQ] = useState(initialTab === "auditors" ? (initialQ ?? "") : "");
  const [auditorLocation, setAuditorLocation] = useState("");
  const [auditorLegalForm, setAuditorLegalForm] = useState("");
  const [auditorCurrentOnly, setAuditorCurrentOnly] = useState(false);
  const [auditorSortBy, setAuditorSortBy] = useState("clients");
  const [auditorOffset, setAuditorOffset] = useState(0);
  const [selectedAuditorKey, setSelectedAuditorKey] = useState<string | null>(null);

  const { data: persons = [], isLoading: personsLoading } = useSWR(
    tab === "persons"
      ? ["people-search", q, hometown, nationality, minCompanies, confidenceFilter, sortBy, offset]
      : null,
    () => searchPersonEntities({
      q: q || undefined,
      hometown: hometown || undefined,
      nationality: nationality || undefined,
      min_active_companies: minCompanies ? parseInt(minCompanies, 10) : undefined,
      confidence_level: confidenceFilter || undefined,
      sort_by: sortBy,
      limit: LIMIT,
      offset,
    }),
    { keepPreviousData: true }
  );

  const { data: auditors = [], isLoading: auditorsLoading } = useSWR(
    tab === "auditors"
      ? ["auditors-search", auditorQ, auditorLocation, auditorLegalForm, auditorCurrentOnly, auditorOffset]
      : null,
    () => searchAuditors({
      q: auditorQ || undefined,
      location: auditorLocation || undefined,
      legal_form: auditorLegalForm || undefined,
      is_current: auditorCurrentOnly ? true : undefined,
      limit: LIMIT,
      offset: auditorOffset,
    }),
    { keepPreviousData: true }
  );

  const resetOffset = () => setOffset(0);
  const resetAuditorOffset = () => setAuditorOffset(0);

  function handleAuditorSelect(key: string) {
    setSelectedAuditorKey(prev => (prev === key ? null : key));
  }

  const auditorGroups = useMemo(
    () => groupAuditors(auditors).sort((a, b) =>
      auditorSortBy === "name"
        ? (a.name ?? "").localeCompare(b.name ?? "")
        : b.companies.length - a.companies.length
    ),
    [auditors, auditorSortBy]
  );
  const selectedAuditorGroup = auditorGroups.find(g => g.key === selectedAuditorKey) ?? null;

  return (
    <div className="max-w-4xl mx-auto p-6 space-y-5">
      <div className="flex items-center gap-3">
        <Users size={20} className="text-slate-400" />
        <h1 className="text-xl font-semibold text-slate-800">{t.title}</h1>
      </div>

      {/* Tabs */}
      <div className="flex border-b border-slate-200">
        {(["persons", "auditors"] as const).map(tabId => (
          <button
            key={tabId}
            onClick={() => { setTab(tabId); setSelectedAuditorKey(null); }}
            className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors ${
              tab === tabId
                ? "border-blue-500 text-blue-700"
                : "border-transparent text-slate-500 hover:text-slate-700"
            }`}
          >
            {tabId === "persons" ? (
              <><Users size={13} className="inline mr-1.5" />{t.tabs.persons}</>
            ) : (
              <><Building2 size={13} className="inline mr-1.5" />{t.tabs.auditors}</>
            )}
          </button>
        ))}
      </div>

      {/* ── Persons tab ───────────────────────────────────────────────────────── */}
      {tab === "persons" && (
        <>
          <div className="flex flex-wrap gap-2 items-center">
            <div className="relative">
              <Search size={13} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-400" />
              <input
                type="search"
                placeholder={t.searchName}
                value={q}
                onChange={e => { setQ(e.target.value); resetOffset(); }}
                className="pl-8 pr-3 py-1.5 rounded-lg border border-slate-200 text-sm focus:outline-none focus:ring-2 focus:ring-blue-200 w-52"
              />
            </div>
            <input
              type="text"
              placeholder={t.hometown}
              value={hometown}
              onChange={e => { setHometown(e.target.value); resetOffset(); }}
              className="px-3 py-1.5 rounded-lg border border-slate-200 text-sm focus:outline-none focus:ring-2 focus:ring-blue-200 w-36"
            />
            <input
              type="text"
              placeholder={t.nationality}
              value={nationality}
              onChange={e => { setNationality(e.target.value); resetOffset(); }}
              className="px-3 py-1.5 rounded-lg border border-slate-200 text-sm focus:outline-none focus:ring-2 focus:ring-blue-200 w-32"
            />
            <input
              type="number"
              placeholder={t.minCompanies}
              value={minCompanies}
              min={0}
              onChange={e => { setMinCompanies(e.target.value); resetOffset(); }}
              className="px-3 py-1.5 rounded-lg border border-slate-200 text-sm focus:outline-none focus:ring-2 focus:ring-blue-200 w-32"
            />
            {isSuperAdmin && (
              <select
                value={confidenceFilter}
                onChange={e => { setConfidenceFilter(e.target.value); resetOffset(); }}
                className="px-3 py-1.5 rounded-lg border border-slate-200 text-sm text-slate-600 bg-white"
              >
                <option value="">{t.allConfidence}</option>
                <option value="high">{t.confHigh}</option>
                <option value="medium">{t.confMedium}</option>
                <option value="low">{t.confLow}</option>
              </select>
            )}
            <select
              value={sortBy}
              onChange={e => { setSortBy(e.target.value); resetOffset(); }}
              className="px-3 py-1.5 rounded-lg border border-slate-200 text-sm text-slate-600 bg-white"
            >
              <option value="companies">{t.sortCompanies}</option>
              {isSuperAdmin && <option value="confidence">{t.sortConfidence}</option>}
              <option value="appearances">{t.sortAppearances}</option>
            </select>
          </div>

          {personsLoading ? (
            <p className="text-sm text-slate-400">{t.loading}</p>
          ) : persons.length === 0 ? (
            <p className="text-sm text-slate-400">{t.noResults}</p>
          ) : (
            <div className="space-y-2">
              {persons.map(entity => (
                <PersonEntityCard key={entity.id} entity={entity} locale={locale} isSuperAdmin={isSuperAdmin} />
              ))}
            </div>
          )}

          <div className="flex gap-2">
            <button
              disabled={offset === 0}
              onClick={() => setOffset(Math.max(0, offset - LIMIT))}
              className="text-sm px-3 py-1.5 rounded border border-slate-200 disabled:opacity-40 hover:bg-slate-50"
            >
              {t.previous}
            </button>
            <button
              disabled={persons.length < LIMIT}
              onClick={() => setOffset(offset + LIMIT)}
              className="text-sm px-3 py-1.5 rounded border border-slate-200 disabled:opacity-40 hover:bg-slate-50"
            >
              {t.next}
            </button>
          </div>
        </>
      )}

      {/* ── Auditors tab ──────────────────────────────────────────────────────── */}
      {tab === "auditors" && (
        <>
          <div className="flex flex-wrap gap-2 items-center">
            <div className="relative">
              <Search size={13} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-400" />
              <input
                type="search"
                placeholder={t.searchAuditor}
                value={auditorQ}
                onChange={e => { setAuditorQ(e.target.value); resetAuditorOffset(); setSelectedAuditorKey(null); }}
                className="pl-8 pr-3 py-1.5 rounded-lg border border-slate-200 text-sm focus:outline-none focus:ring-2 focus:ring-blue-200 w-52"
              />
            </div>
            <input
              type="text"
              placeholder={t.location}
              value={auditorLocation}
              onChange={e => { setAuditorLocation(e.target.value); resetAuditorOffset(); }}
              className="px-3 py-1.5 rounded-lg border border-slate-200 text-sm focus:outline-none focus:ring-2 focus:ring-blue-200 w-32"
            />
            <input
              type="text"
              placeholder={t.legalForm}
              value={auditorLegalForm}
              onChange={e => { setAuditorLegalForm(e.target.value); resetAuditorOffset(); }}
              className="px-3 py-1.5 rounded-lg border border-slate-200 text-sm focus:outline-none focus:ring-2 focus:ring-blue-200 w-28"
            />
            <select
              value={auditorSortBy}
              onChange={e => setAuditorSortBy(e.target.value)}
              className="px-3 py-1.5 rounded-lg border border-slate-200 text-sm text-slate-600 bg-white"
            >
              <option value="clients">{t.sortMostClients}</option>
              <option value="name">{t.sortNameAz}</option>
            </select>
            <label className="flex items-center gap-1.5 text-sm text-slate-600 cursor-pointer select-none">
              <input
                type="checkbox"
                checked={auditorCurrentOnly}
                onChange={e => { setAuditorCurrentOnly(e.target.checked); resetAuditorOffset(); }}
                className="rounded border-slate-300"
              />
              {t.currentOnly}
            </label>
          </div>

          {auditorsLoading ? (
            <p className="text-sm text-slate-400">{t.loading}</p>
          ) : auditorGroups.length === 0 ? (
            <p className="text-sm text-slate-400">{t.noResults}</p>
          ) : (
            <div className="space-y-2">
              {auditorGroups.map(group => (
                <div key={group.key}>
                  <AuditorCard
                    group={group}
                    locale={locale}
                    isSelected={selectedAuditorKey === group.key}
                    onSelect={handleAuditorSelect}
                  />
                  {selectedAuditorKey === group.key && selectedAuditorGroup && (
                    <div className="mt-2">
                      <AuditorDetailPanel
                        group={selectedAuditorGroup}
                        locale={locale}
                        onClose={() => setSelectedAuditorKey(null)}
                      />
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}

          <div className="flex gap-2">
            <button
              disabled={auditorOffset === 0}
              onClick={() => { setAuditorOffset(Math.max(0, auditorOffset - LIMIT)); setSelectedAuditorKey(null); }}
              className="text-sm px-3 py-1.5 rounded border border-slate-200 disabled:opacity-40 hover:bg-slate-50"
            >
              {t.previous}
            </button>
            <button
              disabled={auditors.length < LIMIT}
              onClick={() => { setAuditorOffset(auditorOffset + LIMIT); setSelectedAuditorKey(null); }}
              className="text-sm px-3 py-1.5 rounded border border-slate-200 disabled:opacity-40 hover:bg-slate-50"
            >
              {t.next}
            </button>
          </div>
        </>
      )}
    </div>
  );
}
