"use client";
import { useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { ExternalLink, ChevronLeft, Globe, MapPin, Building2, Phone, Mail, FileText, Plus, Trash2, Loader2, Search } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { fmtDate, fmtDateTime, fmtRelativeTime, cn, formatClusterLabel } from "@/lib/utils";
import { createNote, deleteNote, fetchCompany, runCompanyWebSearch, selectCompanyWebsite } from "@/lib/api";
import type { Company, Note, GoogleScoredResult } from "@/lib/types";
import "leaflet/dist/leaflet.css";
import { SignersPanelDB, SogcTimelineDB } from "@/components/sogc-history";
import { BoardPanel } from "@/components/board-panel";
import { CorporateShareholdersPanel } from "@/components/corporate-shareholders-panel";
import { useI18n } from "@/i18n/context";
import { useApiErrorHandler } from "@/lib/use-api-error";

interface NogaTestGlobalCandidate {
  code: string;
  label: string | null;
  level_no: number;
  raw_sim: number;
  excl_sim: number | null;
  penalized_sim: number;
  depth_bonus: number;
  adjusted_score: number;
  is_peak: boolean;
}

interface NogaTestDescentCandidate {
  code: string;
  label: string | null;
  sim: number;
  is_winner: boolean;
}

interface NogaTestDescentLevel {
  level_no: number;
  code: string;
  label: string | null;
  sim: number;
  top_candidates: NogaTestDescentCandidate[];
}

interface NogaTestResult {
  company_name: string;
  stored_noga_code: string | null;
  stored_noga_label: string | null;
  stored_noga_confidence: number | null;
  stored_noga_path_labels: string | null;
  lang: string;
  embed_text: string;
  depth_bonus_per_level: number;
  global_top_candidates: NogaTestGlobalCandidate[];
  peak_code: string;
  peak_level_no: number;
  peak_label: string | null;
  peak_raw_sim: number;
  peak_penalized_sim: number;
  peak_adjusted_score: number;
  peak_path: string | null;
  peak_path_labels: string | null;
  descent_levels: NogaTestDescentLevel[];
  leaf_code: string;
  leaf_label: string | null;
  leaf_sim: number;
  leaf_path: string | null;
  leaf_path_labels: string | null;
}

interface Props {
  company: Company;
  readOnlyDemo?: boolean;
  isSuperadmin?: boolean;
}

interface CompanyShortEntry {
  name?: string;
  uid?: string;
  status?: string;
  legalSeat?: string;
  legalForm?: { de?: string; shortName?: string | { de?: string } } | string;
}

interface OldNameEntry {
  name?: string;
  sequenceNr?: number;
  translation?: string[];
}

function parseJsonList<T>(raw: string | null | undefined): T[] {
  if (!raw) return [];
  try {
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

function legalFormLabel(lf: CompanyShortEntry["legalForm"]): string {
  if (!lf) return "";
  if (typeof lf === "string") return lf;
  const sn = lf.shortName;
  if (typeof sn === "string") return sn;
  if (typeof sn === "object" && sn?.de) return sn.de;
  if (lf.de) return lf.de;
  return "";
}

function RelatedCompaniesList({ items, label }: { items: CompanyShortEntry[]; label: string }) {
  if (items.length === 0) return null;
  return (
    <div>
      <dt className="text-xs font-medium text-slate-600 mb-1">{label}</dt>
      <dd className="space-y-1">
        {items.map((c, i) => (
          <div key={c.uid ?? i} className="text-xs text-slate-700 flex items-center gap-1.5 flex-wrap">
            <span className="font-medium">{c.name ?? "—"}</span>
            {c.uid && <span className="text-slate-400 font-mono">{c.uid}</span>}
            {legalFormLabel(c.legalForm) && (
              <Badge className="bg-slate-100 text-slate-500 text-xs">{legalFormLabel(c.legalForm)}</Badge>
            )}
            {c.legalSeat && <span className="text-slate-400">{c.legalSeat}</span>}
            {c.status && c.status !== "ACTIVE" && (
              <Badge className="bg-red-50 text-red-600 text-xs">{c.status}</Badge>
            )}
          </div>
        ))}
      </dd>
    </div>
  );
}

export function CompanyDetailClient({ company: initial, readOnlyDemo = false, isSuperadmin = false }: Props) {
  const router = useRouter();
  const handleApiError = useApiErrorHandler();
  const [company, setCompany] = useState(initial);
  const [notes, setNotes] = useState<Note[]>(initial.notes ?? []);
  const [noteText, setNoteText] = useState("");
  const [addingNote, setAddingNote] = useState(false);
  const [purposeExpanded, setPurposeExpanded] = useState(false);
  const [showWebsitePicker, setShowWebsitePicker] = useState(false);
  const [selectingWebsite, setSelectingWebsite] = useState<string | null>(null);
  const [searchingWeb, setSearchingWeb] = useState(false);
  const [nogaTestOpen, setNogaTestOpen] = useState(false);
  const [nogaTestJobId, setNogaTestJobId] = useState<number | null>(null);
  const [nogaTestJobStatus, setNogaTestJobStatus] = useState<string | null>(null);
  const [nogaTestData, setNogaTestData] = useState<NogaTestResult | null>(null);
  const [nogaTestError, setNogaTestError] = useState<string | null>(null);

  const { dict } = useI18n();
  const t = dict.app.companydetail;

  const mapRef = useRef<HTMLDivElement>(null);
  const mapInstanceRef = useRef<import("leaflet").Map | null>(null);

  const googleResults = useMemo<GoogleScoredResult[]>(() => {
    const raw = company.google_search_results_raw;
    if (!raw) return [];
    try {
      const parsed = JSON.parse(raw);
      if (!Array.isArray(parsed)) return [];
      return parsed
        .map((r: unknown) => {
          const obj = (typeof r === "object" && r !== null) ? (r as Record<string, unknown>) : {};
          return {
            title: String(obj.title ?? ""),
            link: String(obj.link ?? ""),
            snippet: String(obj.snippet ?? ""),
            score: Number(obj.score ?? 0),
          };
        })
        .filter(r => r.link);
    } catch {
      return [];
    }
  }, [company.google_search_results_raw]);

  const alternativeWebsiteResults = useMemo(() => {
    const current = (company.website_url ?? "").trim();
    return googleResults.filter(r => (r.link ?? "").trim() !== current);
  }, [googleResults, company.website_url]);

  // Related company lists parsed from JSON
  const headOffices = parseJsonList<CompanyShortEntry>(company.head_offices);
  const furtherHeadOffices = parseJsonList<CompanyShortEntry>(company.further_head_offices);
  const branchOffices = parseJsonList<CompanyShortEntry>(company.branch_offices);
  const hasTakenOver = parseJsonList<CompanyShortEntry>(company.has_taken_over);
  const wasTakenOverBy = parseJsonList<CompanyShortEntry>(company.was_taken_over_by);
  const auditCompanies = parseJsonList<CompanyShortEntry>(company.audit_companies);
  const oldNames = parseJsonList<OldNameEntry>(company.old_names);
  const translations = parseJsonList<string>(company.translations);

  const hasStructureData = headOffices.length > 0 || furtherHeadOffices.length > 0
    || branchOffices.length > 0 || hasTakenOver.length > 0 || wasTakenOverBy.length > 0
    || auditCompanies.length > 0 || oldNames.length > 0 || translations.length > 0;

  useEffect(() => {
    const lat = company.lat;
    const lon = company.lon;
    if (!mapRef.current || lat == null || lon == null) return;
    if (mapInstanceRef.current) return;

    let mounted = true;
    (async () => {
      const L = await import("leaflet");
      if (!mounted || !mapRef.current) return;

      const map = L.map(mapRef.current, {
        zoomControl: false,
        attributionControl: false,
      }).setView([lat, lon], 14);

      L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
        maxZoom: 19,
        attribution: '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
      }).addTo(map);

      L.circleMarker([lat, lon], {
        radius: 7,
        fillColor: "#3b82f6",
        color: "#fff",
        weight: 2,
        fillOpacity: 0.9,
      }).addTo(map);

      mapInstanceRef.current = map;
    })();

    return () => {
      mounted = false;
      mapInstanceRef.current?.remove();
      mapInstanceRef.current = null;
    };
  }, [company.lat, company.lon]);

  async function handleAddNote(e: React.FormEvent) {
    if (readOnlyDemo) return;
    e.preventDefault();
    if (!noteText.trim()) return;
    setAddingNote(true);
    try {
      const note = await createNote(company.id, noteText.trim());
      setNotes(ns => [note, ...ns]);
      setNoteText("");
    } finally {
      setAddingNote(false);
    }
  }

  async function handleDeleteNote(noteId: number) {
    if (readOnlyDemo) return;
    await deleteNote(company.id, noteId);
    setNotes(ns => ns.filter(n => n.id !== noteId));
  }

  async function handleSelectWebsite(link: string) {
    if (readOnlyDemo) return;
    setSelectingWebsite(link);
    try {
      const updated = await selectCompanyWebsite(company.id, link);
      setCompany(updated);
      setShowWebsitePicker(false);
    } finally {
      setSelectingWebsite(null);
    }
  }

  async function handleWebSearch() {
    if (readOnlyDemo) return;
    setSearchingWeb(true);
    try {
      await runCompanyWebSearch(company.id, 10);
      const fresh = await fetchCompany(company.id);
      setCompany(fresh);
      setNotes(fresh.notes ?? []);
    } catch (e) {
      handleApiError(e);
    } finally {
      setSearchingWeb(false);
    }
  }

  async function handleNogaTest() {
    setNogaTestData(null);
    setNogaTestError(null);
    setNogaTestJobId(null);
    setNogaTestJobStatus(null);
    setNogaTestOpen(true);
    try {
      const res = await fetch(`/api/v1/companies/${company.id}/noga-v2-explain`, {
        method: "POST",
        credentials: "include",
      });
      if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
      const { job_id, status: jobStatus } = await res.json();
      setNogaTestJobId(job_id);
      setNogaTestJobStatus(jobStatus);
    } catch (e) {
      handleApiError(e);
      setNogaTestOpen(false);
    }
  }

  useEffect(() => {
    if (!nogaTestJobId || !nogaTestOpen) return;
    if (nogaTestJobStatus === "completed" || nogaTestJobStatus === "failed" || nogaTestJobStatus === "cancelled") return;
    const interval = setInterval(async () => {
      try {
        const res = await fetch(`/api/v1/companies/${company.id}/noga-v2-explain/${nogaTestJobId}`, { credentials: "include" });
        if (!res.ok) return;
        const data = await res.json();
        setNogaTestJobStatus(data.status);
        if (data.status === "completed") setNogaTestData(data.result);
        else if (data.status === "failed") setNogaTestError(data.error ?? "Job failed");
      } catch { /* ignore transient errors */ }
    }, 2000);
    return () => clearInterval(interval);
  }, [nogaTestJobId, nogaTestJobStatus, nogaTestOpen, company.id]);

  return (
    <>
    <div className="flex w-full px-4 py-6 gap-4">

      {/* Main content */}
      <div className="flex-1 min-w-0 max-w-5xl mx-auto space-y-6">
      {/* Breadcrumb */}
      <div className="flex items-center gap-2 text-sm text-slate-600">
        {readOnlyDemo ? (
          <Link href="/demo" className="hover:text-slate-700 flex items-center gap-1">
            <ChevronLeft size={14} /> Demo
          </Link>
        ) : (
          <button type="button" onClick={() => router.back()} className="hover:text-slate-700 flex items-center gap-1">
            <ChevronLeft size={14} /> Search
          </button>
        )}
        <span>/</span>
        <span className="text-slate-800 font-medium">{company.name}</span>
      </div>

      {/* Header */}
      <div className="bg-white rounded-xl border border-slate-200 p-6 shadow-sm">
        <div className="flex items-start justify-between gap-4">
          <div>
            <h1 className="text-2xl font-bold text-slate-900">{company.name}</h1>
            <p className="text-sm text-slate-600 mt-0.5">
              {[company.legal_form, company.canton, company.municipality].filter(Boolean).join(" · ")}
            </p>
            <div className="flex flex-wrap gap-2 mt-3">
              {company.tags && company.tags.split(",").map(t => (
                <Badge key={t.trim()} className="bg-slate-100 text-slate-600 text-xs">{t.trim()}</Badge>
              ))}
            </div>
          </div>
          <div className="flex flex-col gap-2 items-end shrink-0">
            {!readOnlyDemo && !company.website_checked_at && (
              <button
                type="button"
                onClick={handleWebSearch}
                disabled={searchingWeb}
                className="flex items-center gap-1.5 text-sm text-emerald-700 hover:text-emerald-900 px-3 py-1.5 rounded-lg border border-emerald-200 hover:bg-emerald-50 transition-colors disabled:opacity-60"
              >
                {searchingWeb ? <Loader2 size={13} className="animate-spin" /> : <Search size={13} />}
                {searchingWeb ? t.queuing : t.runwebsearch}
              </button>
            )}
            {company.website_url && (
              <a
                href={company.website_url}
                target="_blank"
                rel="noopener noreferrer"
                className="flex items-center gap-1.5 text-sm text-blue-600 hover:text-blue-800 px-3 py-1.5 rounded-lg border border-blue-200 hover:bg-blue-50 transition-colors"
              >
                <Globe size={13} /> {t.visitWebsite} <ExternalLink size={11} />
              </a>
            )}
            {!readOnlyDemo && !!company.website_checked_at && (
              <button
                type="button"
                onClick={() => setShowWebsitePicker(v => !v)}
                className="flex items-center gap-1.5 text-sm text-slate-700 hover:text-slate-900 px-3 py-1.5 rounded-lg border border-slate-200 hover:bg-slate-50 transition-colors"
              >
                {t.changeWebsite}
              </button>
            )}
            {company.cantonal_excerpt_web && (
              <a
                href={company.cantonal_excerpt_web}
                target="_blank"
                rel="noopener noreferrer"
                className="flex items-center gap-1.5 text-sm text-slate-700 hover:text-slate-900 px-3 py-1.5 rounded-lg border border-slate-200 hover:bg-slate-50 transition-colors"
              >
                {t.cantonalexcerpt} <ExternalLink size={11} />
              </a>
            )}
            {isSuperadmin && (
              <button
                type="button"
                onClick={handleNogaTest}
                className="flex items-center gap-1.5 text-sm text-amber-700 hover:text-amber-900 px-3 py-1.5 rounded-lg border border-amber-200 hover:bg-amber-50 transition-colors"
              >
                {t.nogaExplain}
              </button>
            )}
          </div>
        </div>
      </div>

      {!readOnlyDemo && showWebsitePicker && (
        <div className="bg-white rounded-xl border border-slate-200 p-5 shadow-sm">
          <div className="flex items-center justify-between">
            <h2 className="text-base font-semibold text-slate-800">{t.selectdifferentwebsite}</h2>
            <button
              type="button"
              onClick={() => setShowWebsitePicker(false)}
              className="text-sm text-slate-600 hover:text-slate-900"
            >
              {t.close}
            </button>
          </div>
          <div className="mt-3 space-y-2">
            {alternativeWebsiteResults.map(r => (
              <div key={r.link} className="flex items-start justify-between gap-3 rounded-lg border border-slate-200 p-3">
                <div className="min-w-0">
                  <div className="text-sm font-medium text-slate-800 truncate">{r.title || r.link}</div>
                  <div className="text-xs text-slate-500 truncate">{r.link}</div>
                  {r.snippet && <div className="text-xs text-slate-500 mt-1 line-clamp-2">{r.snippet}</div>}
                </div>
                <div className="shrink-0 flex flex-col items-end gap-2">
                  <div className={cn("text-sm font-bold", r.score >= 70 ? "text-green-700" : r.score >= 40 ? "text-yellow-700" : "text-red-700")}>
                    {Math.round(r.score)}
                  </div>
                  <button
                    type="button"
                    disabled={selectingWebsite === r.link}
                    onClick={() => handleSelectWebsite(r.link)}
                    className={cn(
                      "text-sm px-3 py-1.5 rounded-lg border transition-colors",
                      selectingWebsite === r.link
                        ? "border-slate-200 text-slate-400"
                        : "border-blue-200 text-blue-600 hover:text-blue-800 hover:bg-blue-50"
                    )}
                  >
                    {selectingWebsite === r.link ? t.selectingWebsite : t.useThisWebsite}
                  </button>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Classification */}
        <div className="bg-white rounded-xl border border-slate-200 p-5 space-y-4 shadow-sm">
          <h2 className="text-base font-semibold text-slate-800">{t.classification}</h2>

          {company.purpose_language && (() => {
            const LANG_META: Record<string, { flag: string; label: string }> = {
              de: { flag: "🇩🇪", label: t.languageLabelDe },
              fr: { flag: "🇫🇷", label: t.languageLabelFr },
              it: { flag: "🇮🇹", label: t.languageLabelIt },
              en: { flag: "🇬🇧", label: t.languageLabelEn },
              rm: { flag: "🏔️", label: t.languageLabelRm },
            };
            const meta = LANG_META[company.purpose_language] ?? { flag: "🌐", label: company.purpose_language.toUpperCase() };
            return (
              <div className="flex items-center gap-2">
                <span className="text-xs font-medium text-slate-500">{t.purposeLanguage}</span>
                <Link href={`/app/search?purpose_language=${encodeURIComponent(company.purpose_language)}`}>
                  <Badge className="bg-indigo-50 text-indigo-700 text-xs cursor-pointer hover:bg-indigo-100 gap-1">
                    <span>{meta.flag}</span>
                    <span>{meta.label}</span>
                  </Badge>
                </Link>
              </div>
            );
          })()}

          {(company.tfidf_cluster || company.purpose_keywords) && (
            <div className="pt-2 border-t border-slate-100 space-y-2">
              {company.tfidf_cluster && (
                <div>
                  <span className="text-xs font-medium text-slate-600 block mb-1">{t.flexcluster}</span>
                  <div className="flex flex-wrap gap-1">
                    {company.tfidf_cluster.split("|").map(cluster => cluster.trim()).filter(Boolean).map(cluster => (
                      <Link key={cluster} href={`/app/search?tfidf_cluster=${encodeURIComponent(cluster)}`}>
                        <Badge className="bg-purple-50 text-purple-700 text-xs cursor-pointer hover:bg-purple-100">{formatClusterLabel(cluster)}</Badge>
                      </Link>
                    ))}
                  </div>
                </div>
              )}
              {company.purpose_keywords && (
                <div>
                  <span className="text-xs font-medium text-slate-600 block mb-1">{t.purposekeywords}</span>
                  <div className="flex flex-wrap gap-1">
                    {company.purpose_keywords.split(",").map(k => (
                      <Link key={k.trim()} href={`/app/search?purpose_keywords=${encodeURIComponent(k.trim())}`}>
                        <Badge className="bg-blue-50 text-blue-700 text-xs cursor-pointer hover:bg-blue-100">{k.trim()}</Badge>
                      </Link>
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}
          {(company.noga_code || company.noga_label || company.noga_path) && (() => {
            const codes = (company.noga_path ?? "").split("|").filter(Boolean);
            const labels = (company.noga_path_labels ?? "").split("|").filter(Boolean);
            const lowConf = company.noga_confidence != null && company.noga_confidence < 0.5;
            const segments = codes.length > 0
              ? codes.map((code, i) => ({ code, label: labels[i] ?? code }))
              : (company.noga_code ? [{ code: company.noga_code, label: company.noga_label ?? company.noga_code }] : []);
            return (
              <div className={cn("pt-2 border-t border-slate-100 space-y-2", lowConf && "opacity-70")}>
                <div className="flex items-center gap-2">
                  <span className="text-xs font-medium text-slate-600">{t.noga}</span>
                  {company.noga_level && (
                    <Badge className="bg-teal-50 text-teal-700 text-[10px]">{company.noga_level}</Badge>
                  )}
                  {lowConf && (
                    <span className="text-[10px] uppercase tracking-wide text-amber-700 bg-amber-50 border border-amber-200 rounded px-1.5 py-0.5">{t.lowConfidence}</span>
                  )}
                </div>
                {segments.length > 0 && (
                  <div className="flex flex-wrap items-center gap-1 text-xs">
                    {segments.map((seg, i) => (
                      <span key={`${seg.code}-${i}`} className="inline-flex items-center gap-1">
                        {i > 0 && <span className="text-slate-400">›</span>}
                        <Link href={`/app/search?noga_code=${encodeURIComponent(seg.code)}`}>
                          <Badge className={cn(
                            "text-xs cursor-pointer truncate max-w-[260px]",
                            i === segments.length - 1
                              ? "bg-emerald-50 text-emerald-700 hover:bg-emerald-100"
                              : "bg-slate-50 text-slate-600 hover:bg-slate-100"
                          )}>
                            <span className="font-mono mr-1 opacity-70">{seg.code}</span>
                            {seg.label}
                          </Badge>
                        </Link>
                      </span>
                    ))}
                  </div>
                )}
                {company.noga_confidence != null && (
                  <p className="text-xs text-slate-500">{t.confidence}: {Math.round(company.noga_confidence * 100)}%</p>
                )}
              </div>
            );
          })()}
          {company.ai_category && (
            <div className="pt-2 border-t border-slate-100">
              <span className="text-xs font-medium text-slate-600 block mb-1">{t.aiclassification}</span>
              <Link href={`/app/search?ai_category=${encodeURIComponent(company.ai_category)}`}>
                <Badge className="bg-slate-100 text-slate-700 text-xs cursor-pointer hover:bg-slate-200">{company.ai_category}</Badge>
              </Link>
            </div>
          )}
          {company.ai_freeform && (
            <div>
              <span className="text-xs font-medium text-slate-600 block mb-1">{t.ainotes}</span>
              <p className="text-xs text-slate-600 whitespace-pre-wrap">{company.ai_freeform}</p>
            </div>
          )}
        </div>

        {/* Company info */}
        <div className="bg-white rounded-xl border border-slate-200 p-5 space-y-3 shadow-sm lg:col-span-2">
          <h2 className="text-base font-semibold text-slate-800">{t.companyinfo}</h2>

          {company.lat != null && company.lon != null && (
            <div className="rounded-lg border border-slate-200 overflow-hidden isolate">
              <div ref={mapRef} className="h-40 w-full" />
            </div>
          )}

          <dl className="space-y-2 text-sm">
            {[
              { label: t.cheuid, value: company.uid },
              { label: t.legalform, value: company.legal_form },
              { label: t.canton, value: company.canton },
              { label: t.municipality, value: company.municipality },
            ].map(({ label, value }) => value && (
              <div key={label} className="flex gap-2">
                <dt className="text-slate-600 w-24 shrink-0">{label}</dt>
                <dd className="text-slate-700">{value}</dd>
              </div>
            ))}
            {company.address && (
              <div className="flex gap-2">
                <dt className="text-slate-600 w-24 shrink-0 flex items-center gap-1"><MapPin size={11} /> {t.address}</dt>
                <dd className="text-slate-700">{company.address}</dd>
              </div>
            )}
            {company.capital_nominal && (
              <div className="flex gap-2">
                <dt className="text-slate-600 w-24 shrink-0">{t.capital}</dt>
                <dd className="text-slate-700">{company.capital_nominal} {company.capital_currency}</dd>
              </div>
            )}
            {company.sogc_date && (
              <div className="flex gap-2">
                <dt className="text-slate-600 w-24 shrink-0">{t.firstsogcafter}</dt>
                <dd className="text-slate-700">{company.sogc_date}</dd>
              </div>
            )}
            {company.deletion_date && (
              <div className="flex gap-2">
                <dt className="text-slate-600 w-24 shrink-0">{t.deletiondate}</dt>
                <dd className="text-red-700 font-medium">{company.deletion_date}</dd>
              </div>
            )}
          </dl>
          {(company.contact_name || company.contact_email || company.contact_phone) && (
            <div className="pt-3 border-t border-slate-100 space-y-2">
              <h3 className="text-base font-semibold text-slate-800 flex items-center gap-1.5">
                <Building2 size={14} /> {t.contact}
              </h3>
              <div className="space-y-1 text-sm">
                {company.contact_name && <p className="text-slate-700">{company.contact_name}</p>}
                {company.contact_email && (
                  <a href={`mailto:${company.contact_email}`} className="text-blue-600 hover:underline flex items-center gap-1 text-xs">
                    <Mail size={12} /> {company.contact_email}
                  </a>
                )}
                {company.contact_phone && (
                  <a href={`tel:${company.contact_phone}`} className="text-slate-600 flex items-center gap-1 text-xs">
                    <Phone size={12} /> {company.contact_phone}
                  </a>
                )}
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Purpose */}
      {company.purpose && (
        <div className="bg-white rounded-xl border border-slate-200 p-5 shadow-sm">
          <h2 className="text-base font-semibold text-slate-800 mb-2 flex items-center gap-1.5">
            <FileText size={14} /> {t.purpose}
          </h2>
          <div className="relative">
            <p className={cn("text-sm text-slate-700 leading-relaxed whitespace-pre-wrap", !purposeExpanded && "max-h-40 overflow-hidden")}>
              {company.purpose}
            </p>
            {!purposeExpanded && company.purpose.length > 500 && (
              <div className="absolute inset-x-0 bottom-0 h-10 bg-gradient-to-t from-white to-transparent" />
            )}
          </div>
          {company.purpose.length > 500 && (
            <button type="button" onClick={() => setPurposeExpanded(v => !v)} className="mt-2 text-xs text-blue-600 hover:underline">
              {purposeExpanded ? t.showless : t.showmore}
            </button>
          )}
        </div>
      )}


      {/* Corporate Structure */}
      {hasStructureData && (
        <div className="bg-white rounded-xl border border-slate-200 p-5 shadow-sm">
          <h2 className="text-base font-semibold text-slate-800 mb-4">{t.corporatestructure}</h2>
          <dl className="space-y-4">
            {translations.length > 0 && (
              <div>
                <dt className="text-xs font-medium text-slate-600 mb-1">{t.aliasname}</dt>
                <dd className="flex flex-wrap gap-1.5">
                  {translations.map((t, i) => (
                    <Badge key={i} className="bg-slate-100 text-slate-600 text-xs">{t}</Badge>
                  ))}
                </dd>
              </div>
            )}
            {oldNames.length > 0 && (
              <div>
                <dt className="text-xs font-medium text-slate-600 mb-1">{t.previousnames}</dt>
                <dd className="space-y-0.5">
                  {oldNames
                    .sort((a, b) => (b.sequenceNr ?? 0) - (a.sequenceNr ?? 0))
                    .map((n, i) => (
                      <div key={i} className="text-xs text-slate-700">
                        {n.name}
                        {n.translation && n.translation.length > 0 && (
                          <span className="text-slate-400 ml-1">({n.translation.join(", ")})</span>
                        )}
                      </div>
                    ))}
                </dd>
              </div>
            )}
            <RelatedCompaniesList items={headOffices} label={t.headoffice} />
            <RelatedCompaniesList items={furtherHeadOffices} label={t.furtherheadoffices} />
            <RelatedCompaniesList items={branchOffices} label={t.branchoffices} />
            <RelatedCompaniesList items={hasTakenOver} label={t.hastakenover} />
            <RelatedCompaniesList items={wasTakenOverBy} label={t.wastakenoverby} />
            <RelatedCompaniesList items={auditCompanies} label={t.auditcompanies} />
          </dl>
        </div>
      )}


      {/* Structured board & officers from SOGC person graph */}
      <BoardPanel companyId={company.id} />

      {/* Corporate shareholders detected via CHE numbers in SOGC publications */}
      <CorporateShareholdersPanel companyId={company.id} />

      {/* Current signers derived from sogc_publications text (fallback when person graph is empty) */}
      <SignersPanelDB companyId={company.id} />

      {/* SHAB timeline from preprocessed sogc_publications table */}
      <SogcTimelineDB companyId={company.id} />

      {/* Notes */}
      {!readOnlyDemo && <div className="bg-white rounded-xl border border-slate-200 p-5 shadow-sm">
        <h2 className="text-base font-semibold text-slate-800 mb-4">{t.notes} ({notes.length})</h2>
        <form onSubmit={handleAddNote} className="mb-4">
          <div className="flex gap-2">
            <textarea
              value={noteText}
              onChange={e => setNoteText(e.target.value)}
              placeholder={t.addnote}
              rows={2}
              className="flex-1 px-3 py-2 border border-slate-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-300 resize-none"
            />
            <button
              type="submit"
              disabled={addingNote || !noteText.trim()}
              className="flex items-center gap-1.5 bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white px-3 py-2 rounded-lg text-sm font-medium transition-colors self-start"
            >
              {addingNote ? <Loader2 size={14} className="animate-spin" /> : <Plus size={14} />}
              {t.addnote}
            </button>
          </div>
          <div className="mt-1 flex justify-end">
            <span className="text-xs text-slate-500">{noteText.length} chars</span>
          </div>
        </form>
        {notes.length === 0 && (
          <p className="text-sm text-slate-500 text-center py-4">{t.nonotes}</p>
        )}
        <div className="space-y-2">
          {notes.map(n => (
            <div key={n.id} className="flex gap-3 bg-slate-50 rounded-lg p-3">
              <div className="shrink-0 pt-0.5">
                <div className="h-8 w-8 rounded-full bg-slate-200 text-slate-700 flex items-center justify-center text-xs font-semibold">
                  U
                </div>
              </div>
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2">
                  <span className="text-xs font-semibold text-slate-600">{t.user}</span>
                  <span className="text-xs text-slate-500">{fmtRelativeTime(n.created_at)}</span>
                </div>
                <p className="text-sm text-slate-700 whitespace-pre-wrap">{n.content}</p>
                <p className="text-xs text-slate-500 mt-1">{fmtDateTime(n.created_at)}</p>
              </div>
              <button
                onClick={() => handleDeleteNote(n.id)}
                className="p-1 text-slate-300 hover:text-red-500 transition-colors shrink-0"
              >
                <Trash2 size={14} />
              </button>
            </div>
          ))}
        </div>
      </div>}

    </div>

  </div>

  {/* NOGA v2 Test Modal (superadmin only) */}
  {nogaTestOpen && (
    <div className="fixed inset-0 z-[1000] bg-black/40 flex items-start justify-center overflow-y-auto py-8 px-4">
      <div className="bg-white rounded-xl shadow-xl w-full max-w-5xl">
        <div className="flex items-center justify-between px-6 py-4 border-b border-slate-200">
          <h2 className="text-base font-semibold text-slate-900">NOGA explain — {company.name}</h2>
          <button type="button" onClick={() => setNogaTestOpen(false)} className="text-slate-400 hover:text-slate-700 text-xl leading-none">&times;</button>
        </div>

        {nogaTestError ? (
          <div className="px-6 py-10 text-center text-red-600 text-sm">{nogaTestError}</div>
        ) : !nogaTestData ? (
          <div className="flex flex-col items-center justify-center py-16 text-slate-500 gap-3">
            <Loader2 size={24} className="animate-spin" />
            <span className="text-sm capitalize">{nogaTestJobStatus ?? t.queuing} — {t.runningOnMLWorker}</span>
          </div>
        ) : (
          <div className="px-6 py-5 space-y-6 text-sm">

            {/* Comparison: stored vs peak vs leaf */}
            <section className="space-y-2">
              <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-500">Result comparison</h3>
              <div className="grid grid-cols-3 gap-3">
                <div className="border border-slate-200 rounded-lg p-3 space-y-1">
                  <p className="text-[10px] uppercase tracking-wide text-slate-400 font-semibold">Stored (current)</p>
                  <p className="font-mono font-semibold text-slate-800">{nogaTestData.stored_noga_code ?? "—"}</p>
                  <p className="text-xs text-slate-600 truncate">{nogaTestData.stored_noga_label ?? "—"}</p>
                  <p className="text-xs text-slate-500">Confidence: {nogaTestData.stored_noga_confidence != null ? `${Math.round(nogaTestData.stored_noga_confidence * 100)}%` : "—"}</p>
                  <p className="text-xs text-slate-400 truncate">{nogaTestData.stored_noga_path_labels ?? "—"}</p>
                </div>
                <div className="border border-amber-200 bg-amber-50 rounded-lg p-3 space-y-1">
                  <p className="text-[10px] uppercase tracking-wide text-amber-600 font-semibold">v2 Peak (L{nogaTestData.peak_level_no})</p>
                  <p className="font-mono font-semibold text-slate-800">{nogaTestData.peak_code}</p>
                  <p className="text-xs text-slate-600 truncate">{nogaTestData.peak_label ?? "—"}</p>
                  <p className="text-xs text-slate-500">Raw sim: {nogaTestData.peak_raw_sim.toFixed(3)} · Adj: {nogaTestData.peak_adjusted_score.toFixed(3)}</p>
                  <p className="text-xs text-slate-400 truncate">{nogaTestData.peak_path_labels ?? "—"}</p>
                </div>
                <div className="border border-emerald-200 bg-emerald-50 rounded-lg p-3 space-y-1">
                  <p className="text-[10px] uppercase tracking-wide text-emerald-600 font-semibold">v2 Leaf (descended)</p>
                  <p className="font-mono font-semibold text-slate-800">{nogaTestData.leaf_code}</p>
                  <p className="text-xs text-slate-600 truncate">{nogaTestData.leaf_label ?? "—"}</p>
                  <p className="text-xs text-slate-500">Sim: {nogaTestData.leaf_sim.toFixed(3)}</p>
                  <p className="text-xs text-slate-400 truncate">{nogaTestData.leaf_path_labels ?? "—"}</p>
                </div>
              </div>
            </section>

            {/* Input */}
            <section className="space-y-1">
              <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-500">Input</h3>
              <div className="flex flex-wrap gap-4 text-slate-700 text-xs">
                <span><span className="text-slate-500">Language:</span> <code>{nogaTestData.lang}</code></span>
                <span><span className="text-slate-500">Depth bonus/level:</span> {nogaTestData.depth_bonus_per_level}</span>
              </div>
              <div>
                <p className="text-slate-500 mb-0.5 text-xs">Embed text:</p>
                <p className="bg-slate-50 border border-slate-200 rounded p-2 text-xs font-mono text-slate-700 whitespace-pre-wrap">{nogaTestData.embed_text}</p>
              </div>
            </section>

            {/* Global top candidates */}
            <section className="space-y-2">
              <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-500">Global top candidates (all levels)</h3>
              <div className="border border-slate-200 rounded-lg overflow-hidden">
                <table className="w-full text-xs">
                  <thead>
                    <tr className="text-slate-500 bg-slate-50 border-b border-slate-100">
                      <th className="text-left px-3 py-1.5 font-medium">Code</th>
                      <th className="text-left px-3 py-1.5 font-medium">Label</th>
                      <th className="text-center px-3 py-1.5 font-medium">L</th>
                      <th className="text-right px-3 py-1.5 font-medium">Raw sim</th>
                      <th className="text-right px-3 py-1.5 font-medium">Excl sim</th>
                      <th className="text-right px-3 py-1.5 font-medium">Penalized</th>
                      <th className="text-right px-3 py-1.5 font-medium">Depth+</th>
                      <th className="text-right px-3 py-1.5 font-medium">Adjusted</th>
                    </tr>
                  </thead>
                  <tbody>
                    {nogaTestData.global_top_candidates.map((c, i) => (
                      <tr key={`${c.code}-${i}`} className={cn(
                        "border-b border-slate-100 last:border-0",
                        c.is_peak && "bg-amber-50 font-semibold",
                      )}>
                        <td className="px-3 py-1.5 font-mono text-slate-800">{c.code}{c.is_peak ? " ★" : ""}</td>
                        <td className="px-3 py-1.5 text-slate-600 max-w-[180px] truncate">{c.label ?? "—"}</td>
                        <td className="px-3 py-1.5 text-center text-slate-500">{c.level_no}</td>
                        <td className="px-3 py-1.5 text-right text-slate-700">{c.raw_sim.toFixed(3)}</td>
                        <td className={cn("px-3 py-1.5 text-right", c.excl_sim != null && c.excl_sim > 0.3 ? "text-red-600 font-medium" : "text-slate-500")}>
                          {c.excl_sim != null ? c.excl_sim.toFixed(3) : "—"}
                        </td>
                        <td className="px-3 py-1.5 text-right text-slate-700">{c.penalized_sim.toFixed(3)}</td>
                        <td className="px-3 py-1.5 text-right text-slate-500">+{c.depth_bonus.toFixed(3)}</td>
                        <td className="px-3 py-1.5 text-right font-semibold text-slate-800">{c.adjusted_score.toFixed(4)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>

            {/* Constrained descent */}
            {nogaTestData.descent_levels.length > 0 && (
              <section className="space-y-2">
                <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-500">Constrained descent from peak</h3>
                {nogaTestData.descent_levels.map(dl => (
                  <div key={dl.level_no} className="border border-slate-200 rounded-lg overflow-hidden">
                    <div className="flex items-center gap-2 px-4 py-2 bg-slate-50 border-b border-slate-200">
                      <span className="font-semibold text-slate-800">Level {dl.level_no}</span>
                      <Badge className="bg-emerald-50 text-emerald-700 font-mono">{dl.code} — {dl.label}</Badge>
                      <span className="text-slate-500 text-xs">sim: {dl.sim.toFixed(3)}</span>
                    </div>
                    <table className="w-full text-xs">
                      <thead>
                        <tr className="text-slate-500 border-b border-slate-100">
                          <th className="text-left px-4 py-1.5 font-medium">Code</th>
                          <th className="text-left px-4 py-1.5 font-medium">Label</th>
                          <th className="text-right px-4 py-1.5 font-medium">Sim</th>
                        </tr>
                      </thead>
                      <tbody>
                        {dl.top_candidates.map(tc => (
                          <tr key={tc.code} className={cn("border-b border-slate-100 last:border-0", tc.is_winner && "bg-emerald-50")}>
                            <td className="px-4 py-1.5 font-mono text-slate-800">{tc.code}{tc.is_winner ? " ✓" : ""}</td>
                            <td className="px-4 py-1.5 text-slate-600 max-w-[260px] truncate">{tc.label ?? "—"}</td>
                            <td className="px-4 py-1.5 text-right text-slate-700">{tc.sim.toFixed(3)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                ))}
              </section>
            )}

          </div>
        )}
      </div>
    </div>
  )}

  </>
  );
}
