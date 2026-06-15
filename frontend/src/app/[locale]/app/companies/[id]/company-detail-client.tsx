"use client";
import { useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  ExternalLink, ChevronLeft, Globe, MapPin, Building2, Phone, Mail, FileText,
  Plus, Trash2, Loader2, Search, Share2, Settings, ChevronDown, X, FlaskConical,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { fmtDateTime, fmtRelativeTime, cn, formatClusterLabel } from "@/lib/utils";
import { createNote, deleteNote, fetchCompany, runCompanyWebSearch, selectCompanyWebsite } from "@/lib/api";
import type { Company, Note, GoogleScoredResult } from "@/lib/types";
import "leaflet/dist/leaflet.css";
import { SignersPanelDB, SogcTimelineDB } from "@/components/sogc-history";
import { BoardPanel, AuditorsPanel } from "@/components/board-panel";
import { CorporateShareholdersPanel } from "@/components/corporate-shareholders-panel";
import { SimapPanel } from "@/components/simap-panel";
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

// ── Inline atoms ────────────────────────────────────────────────────────────

function StatusPill({ status }: { status: string | null }) {
  if (!status) return null;
  const s = status.toUpperCase();
  if (s === "ACTIVE")
    return (
      <span className="inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-[12px] font-semibold" style={{ background: "#e9f6ee", color: "#15803d" }}>
        <span className="h-1.5 w-1.5 rounded-full bg-[#15803d]" /> Active
      </span>
    );
  if (s === "BEING_CANCELLED")
    return (
      <span className="inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-[12px] font-semibold" style={{ background: "#fdf3e7", color: "#b45309" }}>
        <span className="h-1.5 w-1.5 rounded-full bg-[#b45309]" /> Being cancelled
      </span>
    );
  if (s === "CANCELLED" || s === "GELÖSCHT")
    return (
      <span className="inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-[12px] font-semibold" style={{ background: "#fdeaea", color: "#b91c1c" }}>
        <span className="h-1.5 w-1.5 rounded-full bg-[#b91c1c]" /> {s === "GELÖSCHT" ? "Deleted" : "Cancelled"}
      </span>
    );
  return (
    <span className="inline-flex items-center rounded-full px-2.5 py-0.5 text-[12px] font-semibold" style={{ background: "#f7f9fc", color: "#6b7480" }}>
      {status}
    </span>
  );
}

function ScoreRing({ value, size = 72 }: { value: number; size?: number }) {
  const r = (size - 10) / 2;
  const circ = 2 * Math.PI * r;
  const pct = Math.max(0, Math.min(100, value ?? 0));
  const offset = circ * (1 - pct / 100);
  const color = pct >= 75 ? "#15803d" : pct >= 45 ? "#2563eb" : pct > 0 ? "#b45309" : "#eef0f3";
  return (
    <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`}>
      <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="#eef0f3" strokeWidth={8} />
      {pct > 0 && (
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke={color} strokeWidth={8}
          strokeDasharray={`${circ} ${circ}`} strokeDashoffset={offset}
          strokeLinecap="round" transform={`rotate(-90 ${size / 2} ${size / 2})`} />
      )}
      <text x={size / 2} y={size / 2} textAnchor="middle" dominantBaseline="central"
        fontFamily='ui-monospace,"SF Mono",Menlo,monospace' fontWeight="700"
        fontSize={size * 0.22} fill={pct > 0 ? color : "#9aa2ad"}>
        {pct > 0 ? pct : "—"}
      </text>
    </svg>
  );
}

function ScoreBar({ value, label }: { value: number; label: string }) {
  const pct = Math.max(0, Math.min(100, value ?? 0));
  const color = pct >= 75 ? "#15803d" : pct >= 45 ? "#2563eb" : pct > 0 ? "#b45309" : "#eef0f3";
  return (
    <div>
      <div className="flex items-center justify-between mb-1">
        <span className="text-[12px]" style={{ color: "#6b7480" }}>{label}</span>
        <span className="font-mono text-[12px] font-semibold" style={{ color: pct > 0 ? color : "#9aa2ad" }}>
          {pct > 0 ? pct : "—"}
        </span>
      </div>
      <div className="h-1.5 rounded-full overflow-hidden" style={{ background: "#eef0f3" }}>
        {pct > 0 && <div className="h-full rounded-full" style={{ width: `${pct}%`, background: color }} />}
      </div>
    </div>
  );
}

function CoverageItem({ label, present }: { label: string; present: boolean }) {
  return (
    <div className="flex items-center gap-1.5">
      <span className="text-[12px] font-bold" style={{ color: present ? "#15803d" : "#9aa2ad" }}>
        {present ? "✓" : "—"}
      </span>
      <span className="text-[12px]" style={{ color: present ? "#1f2733" : "#9aa2ad" }}>{label}</span>
    </div>
  );
}

// ── Helpers ──────────────────────────────────────────────────────────────────

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

function RelatedCompaniesList({ items, label, getLink }: { items: CompanyShortEntry[]; label: string; getLink?: (item: CompanyShortEntry) => string | undefined }) {
  if (items.length === 0) return null;
  return (
    <div>
      <dt className="text-[12px] font-semibold mb-1" style={{ color: "#6b7480" }}>{label}</dt>
      <dd className="space-y-1">
        {items.map((c, i) => {
          const link = getLink?.(c);
          const nameEl = link ? (
            <Link href={link} className="font-medium hover:text-[#2563eb] hover:underline">{c.name ?? "—"}</Link>
          ) : (
            <span className="font-medium">{c.name ?? "—"}</span>
          );
          return (
            <div key={c.uid ?? i} className="text-[12px] flex items-center gap-1.5 flex-wrap" style={{ color: "#3f4854" }}>
              {nameEl}
              {c.uid && <span className="font-mono" style={{ color: "#9aa2ad" }}>{c.uid}</span>}
              {legalFormLabel(c.legalForm) && (
                <Badge className="bg-slate-100 text-slate-500 text-xs">{legalFormLabel(c.legalForm)}</Badge>
              )}
              {c.legalSeat && <span style={{ color: "#9aa2ad" }}>{c.legalSeat}</span>}
              {c.status && c.status !== "ACTIVE" && (
                <Badge className="bg-red-50 text-red-600 text-xs">{c.status}</Badge>
              )}
            </div>
          );
        })}
      </dd>
    </div>
  );
}

// ── Main component ────────────────────────────────────────────────────────────

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
  const [adminMenuOpen, setAdminMenuOpen] = useState(false);

  const { dict } = useI18n();
  const t = dict.app.companydetail;

  const mapRef = useRef<HTMLDivElement>(null);
  const mapInstanceRef = useRef<import("leaflet").Map | null>(null);
  const adminMenuRef = useRef<HTMLDivElement>(null);

  const googleResults = useMemo<GoogleScoredResult[]>(() => {
    const raw = company.google_search_results_raw;
    if (!raw) return [];
    try {
      const parsed = JSON.parse(raw);
      if (!Array.isArray(parsed)) return [];
      return parsed
        .map((r: unknown) => {
          const obj = typeof r === "object" && r !== null ? (r as Record<string, unknown>) : {};
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

  const headOffices = parseJsonList<CompanyShortEntry>(company.head_offices);
  const furtherHeadOffices = parseJsonList<CompanyShortEntry>(company.further_head_offices);
  const branchOffices = parseJsonList<CompanyShortEntry>(company.branch_offices);
  const hasTakenOver = parseJsonList<CompanyShortEntry>(company.has_taken_over);
  const wasTakenOverBy = parseJsonList<CompanyShortEntry>(company.was_taken_over_by);
  const auditCompanies = parseJsonList<CompanyShortEntry>(company.audit_companies);
  const oldNames = parseJsonList<OldNameEntry>(company.old_names);
  const translations = parseJsonList<string>(company.translations);

  const resolvedUids = company.resolved_uids ?? {};
  const getRelatedLink = (c: CompanyShortEntry) =>
    c.uid && resolvedUids[c.uid] ? `/app/companies/${resolvedUids[c.uid]}` : undefined;

  const hasStructureData =
    headOffices.length > 0 || furtherHeadOffices.length > 0 ||
    branchOffices.length > 0 || hasTakenOver.length > 0 ||
    wasTakenOverBy.length > 0 || auditCompanies.length > 0 ||
    oldNames.length > 0 || translations.length > 0;

  // Map init
  useEffect(() => {
    const lat = company.lat;
    const lon = company.lon;
    if (!mapRef.current || lat == null || lon == null) return;
    if (mapInstanceRef.current) return;
    let mounted = true;
    (async () => {
      const L = await import("leaflet");
      if (!mounted || !mapRef.current) return;
      const map = L.map(mapRef.current, { zoomControl: false, attributionControl: false }).setView([lat, lon], 14);
      L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 19 }).addTo(map);
      L.circleMarker([lat, lon], { radius: 7, fillColor: "#2563eb", color: "#fff", weight: 2, fillOpacity: 0.9 }).addTo(map);
      mapInstanceRef.current = map;
    })();
    return () => {
      mounted = false;
      mapInstanceRef.current?.remove();
      mapInstanceRef.current = null;
    };
  }, [company.lat, company.lon]);

  // Admin menu outside-click
  useEffect(() => {
    if (!adminMenuOpen) return;
    function onDown(e: MouseEvent) {
      if (adminMenuRef.current && !adminMenuRef.current.contains(e.target as Node)) {
        setAdminMenuOpen(false);
      }
    }
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [adminMenuOpen]);

  // NOGA job polling
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
      } catch { /* ignore transient */ }
    }, 2000);
    return () => clearInterval(interval);
  }, [nogaTestJobId, nogaTestJobStatus, nogaTestOpen, company.id]);

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
      const res = await fetch(`/api/v1/companies/${company.id}/noga-v2-explain`, { method: "POST", credentials: "include" });
      if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
      const { job_id, status: jobStatus } = await res.json();
      setNogaTestJobId(job_id);
      setNogaTestJobStatus(jobStatus);
    } catch (e) {
      handleApiError(e);
      setNogaTestOpen(false);
    }
  }

  const SUB_TABS = ["Overview", "Timeline", "People", "Documents", "Financials"] as const;

  return (
    <>
      {/* Sub-tab bar */}
      <div className="bg-white border-b border-[#eef0f3] sticky top-0 z-40">
        <div className="max-w-[1240px] mx-auto px-10">
          <div className="flex">
            {SUB_TABS.map((tab, i) => (
              <button key={tab} type="button" disabled={i > 0}
                className={cn(
                  "px-4 py-3 text-[13px] font-semibold border-b-2 transition-colors",
                  i === 0 ? "border-[#2563eb] text-[#2563eb]" : "border-transparent text-[#9aa2ad] cursor-not-allowed"
                )}>
                {tab}
              </button>
            ))}
          </div>
        </div>
      </div>

      {/* Page */}
      <div style={{ background: "#f7f9fc" }} className="min-h-screen">
        <div className="max-w-[1240px] mx-auto" style={{ padding: "26px 40px 40px" }}>

          {/* Breadcrumb */}
          <div className="flex items-center gap-1.5 text-[13px] mb-4" style={{ color: "#6b7480" }}>
            {readOnlyDemo ? (
              <Link href="/demo" className="hover:text-[#1f2733] flex items-center gap-1">
                <ChevronLeft size={14} /> Demo
              </Link>
            ) : (
              <button type="button" onClick={() => router.back()} className="hover:text-[#1f2733] flex items-center gap-1">
                <ChevronLeft size={14} /> Companies
              </button>
            )}
            <span style={{ color: "#e6e8ec" }}>›</span>
            <span style={{ color: "#1f2733", fontWeight: 600 }}>{company.name}</span>
          </div>

          {/* ── Header card ── */}
          <div className="bg-white rounded-2xl border border-[#e6e8ec] overflow-hidden">
            <div className="p-6">
              <div className="flex items-start justify-between gap-4">
                {/* Avatar + name */}
                <div className="flex items-start gap-4">
                  <div className="w-14 h-14 rounded-2xl bg-[#eff4fe] flex items-center justify-center shrink-0">
                    <Building2 size={28} className="text-[#2563eb]" />
                  </div>
                  <div>
                    <div className="flex items-center gap-2.5 flex-wrap">
                      <h1 style={{ fontSize: 28, fontWeight: 700, letterSpacing: "-0.02em", color: "#1f2733", lineHeight: 1.2 }}>
                        {company.name}
                      </h1>
                      <StatusPill status={company.status} />
                    </div>
                    {/* Identity row */}
                    <div className="flex items-center flex-wrap mt-2 text-[13px]" style={{ color: "#3f4854" }}>
                      {company.uid && (
                        <>
                          <span className="font-mono">{company.uid}</span>
                          <button type="button" title="Copy UID"
                            onClick={() => navigator.clipboard.writeText(company.uid ?? "")}
                            className="ml-1 mr-3 transition-colors" style={{ color: "#9aa2ad" }}
                            onMouseEnter={e => (e.currentTarget.style.color = "#3f4854")}
                            onMouseLeave={e => (e.currentTarget.style.color = "#9aa2ad")}>
                            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                              <rect x="9" y="9" width="13" height="13" rx="2" />
                              <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" />
                            </svg>
                          </button>
                        </>
                      )}
                      {company.legal_form && (
                        <><span style={{ color: "#e6e8ec" }} className="mx-2">|</span><span>{company.legal_form}</span></>
                      )}
                      {(company.municipality || company.canton) && (
                        <><span style={{ color: "#e6e8ec" }} className="mx-2">|</span>
                          <span><MapPin size={11} className="inline mr-0.5 opacity-60" />{[company.municipality, company.canton].filter(Boolean).join(", ")}</span>
                        </>
                      )}
                      {company.purpose_language && (
                        <><span style={{ color: "#e6e8ec" }} className="mx-2">|</span><span>{company.purpose_language.toUpperCase()}</span></>
                      )}
                    </div>
                  </div>
                </div>

                {/* Actions */}
                <div className="flex items-center gap-2 shrink-0 mt-1">
                  <button type="button"
                    onClick={() => { if (typeof window !== "undefined") navigator.clipboard.writeText(window.location.href); }}
                    className="flex items-center gap-1.5 text-[13px] px-3 py-1.5 rounded-[10px] border border-[#e6e8ec] hover:bg-[#f7f9fc] transition-colors"
                    style={{ color: "#3f4854" }}>
                    <Share2 size={14} /> Share
                  </button>
                  {company.cantonal_excerpt_web && (
                    <a href={company.cantonal_excerpt_web} target="_blank" rel="noopener noreferrer"
                      className="flex items-center gap-1.5 text-[13px] px-3 py-1.5 rounded-[10px] border border-[#e6e8ec] hover:bg-[#f7f9fc] transition-colors"
                      style={{ color: "#3f4854" }}>
                      Registry <ExternalLink size={11} />
                    </a>
                  )}
                  {company.website_url && (
                    <a href={company.website_url} target="_blank" rel="noopener noreferrer"
                      className="flex items-center gap-1.5 text-[13px] px-3 py-1.5 rounded-[10px] border border-[#cdddfb] hover:bg-[#eff4fe] transition-colors"
                      style={{ color: "#2563eb" }}>
                      <Globe size={13} /> Website <ExternalLink size={11} />
                    </a>
                  )}
                  {!readOnlyDemo && !company.website_checked_at && (
                    <button type="button" onClick={handleWebSearch} disabled={searchingWeb}
                      className="flex items-center gap-1.5 text-[13px] px-3 py-1.5 rounded-[10px] border transition-colors disabled:opacity-50"
                      style={{ borderColor: "#c8e6c9", color: "#15803d" }}>
                      {searchingWeb ? <Loader2 size={13} className="animate-spin" /> : <Search size={13} />}
                      {searchingWeb ? "Searching…" : t.runwebsearch}
                    </button>
                  )}
                  {isSuperadmin && (
                    <div className="relative" ref={adminMenuRef}>
                      <button type="button" onClick={() => setAdminMenuOpen(v => !v)}
                        className="flex items-center gap-1.5 text-[13px] px-3 py-1.5 rounded-[10px] border border-purple-200 hover:bg-purple-50 transition-colors"
                        style={{ color: "#9333ea" }}>
                        <Settings size={14} /> Admin <ChevronDown size={11} />
                      </button>
                      {adminMenuOpen && (
                        <div className="absolute right-0 top-full mt-1.5 w-52 bg-white rounded-xl border border-[#e6e8ec] shadow-lg z-50 py-1 overflow-hidden">
                          <button type="button"
                            onClick={() => { setShowWebsitePicker(true); setAdminMenuOpen(false); }}
                            className="w-full flex items-center gap-2.5 px-4 py-2.5 text-[13px] text-[#1f2733] hover:bg-[#f7f9fc] transition-colors text-left">
                            <Globe size={14} className="shrink-0" style={{ color: "#2563eb" }} /> Change website
                          </button>
                          <button type="button"
                            onClick={() => { handleNogaTest(); setAdminMenuOpen(false); }}
                            className="w-full flex items-center gap-2.5 px-4 py-2.5 text-[13px] text-[#1f2733] hover:bg-[#f7f9fc] transition-colors text-left">
                            <FlaskConical size={14} className="shrink-0" style={{ color: "#b45309" }} /> NOGA explain
                          </button>
                        </div>
                      )}
                    </div>
                  )}
                </div>
              </div>

              {/* NOGA chip */}
              {(company.noga_code || company.noga_label) && (
                <div className="mt-3">
                  <Link href={`/app/search?noga_code=${encodeURIComponent(company.noga_code ?? "")}`}>
                    <span className="inline-flex items-center gap-1.5 bg-[#eff4fe] border border-[#cdddfb] rounded-full px-3 py-1 text-[12px] cursor-pointer hover:bg-[#dbeafe] transition-colors">
                      {company.noga_code && <span className="font-mono font-semibold text-[#2563eb]">{company.noga_code}</span>}
                      {company.noga_label && <span style={{ color: "#1d4ed8" }}>{company.noga_label}</span>}
                      {company.noga_confidence != null && (
                        <span className="font-mono" style={{ color: "#9aa2ad", fontSize: 11 }}>{Math.round(company.noga_confidence * 100)}%</span>
                      )}
                    </span>
                  </Link>
                </div>
              )}
            </div>

            {/* Divider */}
            <div className="border-t border-[#eef0f3]" />

            {/* AI summary + key facts */}
            <div className="grid" style={{ gridTemplateColumns: "1.55fr 1fr" }}>
              {/* AI summary */}
              <div className="p-6 border-r border-[#eef0f3]" style={{ background: "linear-gradient(180deg,#f7faff 0%,#fff 100%)" }}>
                <div className="flex items-center gap-1.5 mb-2" style={{ color: "#2563eb" }}>
                  <svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor">
                    <path d="M12 1L9.5 8.5 2 6l5.5 6L2 18l7.5-2.5L12 23l2.5-7.5L22 18l-5.5-6L22 6l-7.5 2.5z" />
                  </svg>
                  <span className="font-mono font-bold uppercase" style={{ fontSize: 11, letterSpacing: "0.08em" }}>AI Summary</span>
                </div>
                {company.ai_freeform ? (
                  <p style={{ fontSize: 15, lineHeight: 1.6, color: "#3f4854" }}>{company.ai_freeform}</p>
                ) : (
                  <p className="italic" style={{ fontSize: 14, color: "#9aa2ad" }}>No AI summary available yet.</p>
                )}
                <div className="flex flex-wrap gap-1.5 mt-3">
                  {company.sogc_date && <span className="text-[11px] bg-white border border-[#eef0f3] rounded-full px-2 py-0.5" style={{ color: "#6b7480" }}>SOGC</span>}
                  {company.noga_code && <span className="text-[11px] bg-white border border-[#eef0f3] rounded-full px-2 py-0.5" style={{ color: "#6b7480" }}>NOGA</span>}
                  {company.website_url && <span className="text-[11px] bg-white border border-[#eef0f3] rounded-full px-2 py-0.5" style={{ color: "#6b7480" }}>Web</span>}
                  {company.ai_category && <span className="text-[11px] bg-white border border-[#eef0f3] rounded-full px-2 py-0.5" style={{ color: "#6b7480" }}>AI</span>}
                </div>
              </div>
              {/* Key facts */}
              <div className="p-6">
                <div className="grid grid-cols-2 gap-4">
                  <div>
                    <div className="font-mono font-semibold uppercase mb-0.5" style={{ fontSize: 11, letterSpacing: "0.06em", color: "#9aa2ad" }}>Founded</div>
                    <div className="font-mono font-bold" style={{ fontSize: 17, color: "#1f2733" }}>
                      {company.sogc_date ? company.sogc_date.slice(0, 4) : "—"}
                    </div>
                  </div>
                  <div>
                    <div className="font-mono font-semibold uppercase mb-0.5" style={{ fontSize: 11, letterSpacing: "0.06em", color: "#9aa2ad" }}>Share capital</div>
                    {company.capital_nominal ? (
                      <div className="font-bold" style={{ fontSize: 15, color: "#1f2733" }}>
                        {company.capital_currency} {Number(company.capital_nominal).toLocaleString("de-CH")}
                      </div>
                    ) : (
                      <div className="font-mono font-bold" style={{ fontSize: 17, color: "#9aa2ad" }}>—</div>
                    )}
                  </div>
                  <div>
                    <div className="font-mono font-semibold uppercase mb-0.5" style={{ fontSize: 11, letterSpacing: "0.06em", color: "#9aa2ad" }}>Size</div>
                    <div className="font-mono font-bold" style={{ fontSize: 17, color: "#9aa2ad" }}>—</div>
                  </div>
                  <div>
                    <div className="font-mono font-semibold uppercase mb-0.5" style={{ fontSize: 11, letterSpacing: "0.06em", color: "#9aa2ad" }}>Auditor</div>
                    <div style={{ fontSize: 13, fontWeight: 500, color: auditCompanies.length > 0 ? "#1f2733" : "#9aa2ad" }}>
                      {auditCompanies.length > 0 ? (auditCompanies[0].name ?? "—") : "Opted out"}
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </div>

          {/* ── Body grid ── */}
          <div className="mt-[18px] grid gap-[18px]" style={{ gridTemplateColumns: "1.55fr 1fr", alignItems: "start" }}>

            {/* Main column */}
            <div className="space-y-4">

              {/* Purpose */}
              {company.purpose && (
                <div className="bg-white rounded-2xl border border-[#e6e8ec] p-5">
                  <h2 className="font-bold mb-2 flex items-center gap-1.5" style={{ fontSize: 15, color: "#1f2733" }}>
                    <FileText size={14} style={{ color: "#6b7480" }} /> {t.purpose}
                  </h2>
                  <div className="relative">
                    <p className={cn("leading-relaxed whitespace-pre-wrap", !purposeExpanded && "max-h-40 overflow-hidden")}
                      style={{ fontSize: 13, color: "#3f4854" }}>
                      {company.purpose}
                    </p>
                    {!purposeExpanded && company.purpose.length > 500 && (
                      <div className="absolute inset-x-0 bottom-0 h-10 bg-gradient-to-t from-white to-transparent" />
                    )}
                  </div>
                  {company.purpose.length > 500 && (
                    <button type="button" onClick={() => setPurposeExpanded(v => !v)}
                      className="mt-2 hover:underline" style={{ fontSize: 12, color: "#2563eb" }}>
                      {purposeExpanded ? t.showless : t.showmore}
                    </button>
                  )}
                </div>
              )}

              <SimapPanel companyId={company.id} />

              <BoardPanel companyId={company.id} />

              {/* Group / Corporate structure */}
              {hasStructureData && (
                <div className="bg-white rounded-2xl border border-[#e6e8ec] p-5">
                  <h2 className="font-bold mb-4" style={{ fontSize: 15, color: "#1f2733" }}>{t.corporatestructure}</h2>
                  <dl className="space-y-4 text-sm">
                    {translations.length > 0 && (
                      <div>
                        <dt className="text-[12px] font-semibold mb-1" style={{ color: "#6b7480" }}>{t.aliasname}</dt>
                        <dd className="flex flex-wrap gap-1.5">
                          {translations.map((tr, i) => (
                            <Badge key={i} className="bg-slate-100 text-slate-600 text-xs">{tr}</Badge>
                          ))}
                        </dd>
                      </div>
                    )}
                    {oldNames.length > 0 && (
                      <div>
                        <dt className="text-[12px] font-semibold mb-1" style={{ color: "#6b7480" }}>{t.previousnames}</dt>
                        <dd className="space-y-0.5">
                          {oldNames
                            .sort((a, b) => (b.sequenceNr ?? 0) - (a.sequenceNr ?? 0))
                            .map((n, i) => (
                              <div key={i} className="text-[12px]" style={{ color: "#3f4854" }}>
                                {n.name}
                                {n.translation && n.translation.length > 0 && (
                                  <span className="ml-1" style={{ color: "#9aa2ad" }}>({n.translation.join(", ")})</span>
                                )}
                              </div>
                            ))}
                        </dd>
                      </div>
                    )}
                    <RelatedCompaniesList items={headOffices} label={t.headoffice} getLink={getRelatedLink} />
                    <RelatedCompaniesList items={furtherHeadOffices} label={t.furtherheadoffices} getLink={getRelatedLink} />
                    <RelatedCompaniesList items={branchOffices} label={t.branchoffices} getLink={getRelatedLink} />
                    <RelatedCompaniesList items={hasTakenOver} label={t.hastakenover} getLink={getRelatedLink} />
                    <RelatedCompaniesList items={wasTakenOverBy} label={t.wastakenoverby} getLink={getRelatedLink} />
                    <RelatedCompaniesList items={auditCompanies} label={t.auditcompanies} getLink={getRelatedLink} />
                  </dl>
                </div>
              )}

              <AuditorsPanel companyId={company.id} />

              <SignersPanelDB companyId={company.id} />

              <SogcTimelineDB companyId={company.id} />

              {/* Contact */}
              {(company.contact_name || company.contact_email || company.contact_phone) && (
                <div className="bg-white rounded-2xl border border-[#e6e8ec] p-5">
                  <h2 className="font-bold mb-3" style={{ fontSize: 15, color: "#1f2733" }}>{t.contact}</h2>
                  <div className="space-y-1.5" style={{ fontSize: 13 }}>
                    {company.contact_name && <p style={{ color: "#1f2733" }}>{company.contact_name}</p>}
                    {company.contact_email && (
                      <a href={`mailto:${company.contact_email}`} className="flex items-center gap-1.5 hover:underline" style={{ color: "#2563eb" }}>
                        <Mail size={13} /> {company.contact_email}
                      </a>
                    )}
                    {company.contact_phone && (
                      <a href={`tel:${company.contact_phone}`} className="flex items-center gap-1.5" style={{ color: "#3f4854" }}>
                        <Phone size={13} /> {company.contact_phone}
                      </a>
                    )}
                  </div>
                </div>
              )}
            </div>

            {/* Sidebar */}
            <div className="space-y-4">

              {/* Scoring — only shown when at least one score has been computed */}
              {(company.combined_score != null || company.ai_score != null || company.web_score != null) && (
                <div className="bg-white rounded-2xl border border-[#e6e8ec] p-5">
                  <h2 className="font-bold mb-4" style={{ fontSize: 15, color: "#1f2733" }}>Scores</h2>
                  <div className="flex items-center gap-4 mb-5">
                    <ScoreRing value={Math.round(company.combined_score ?? 0)} size={72} />
                    <div>
                      <div className="font-semibold uppercase mb-0.5" style={{ fontSize: 12, letterSpacing: "0.04em", color: "#9aa2ad" }}>Combined</div>
                      <div style={{ fontSize: 12, color: "#6b7480" }}>70% AI · 20% Web · 10% Flex</div>
                    </div>
                  </div>
                  <div className="space-y-3">
                    <ScoreBar value={Math.round(company.web_score ?? 0)} label="Web" />
                    <ScoreBar value={Math.round(company.ai_score ?? 0)} label="AI" />
                    <ScoreBar value={Math.round(company.flex_score ?? 0)} label="Flex" />
                  </div>
                </div>
              )}

              {/* Location */}
              <div className="bg-white rounded-2xl border border-[#e6e8ec] overflow-hidden">
                {company.lat != null && company.lon != null && (
                  <div ref={mapRef} className="h-44 w-full" />
                )}
                <div className="p-5">
                  <h2 className="font-bold mb-2" style={{ fontSize: 15, color: "#1f2733" }}>Location</h2>
                  {company.address && (
                    <p className="mt-1" style={{ fontSize: 13, color: "#3f4854" }}>{company.address}</p>
                  )}
                  {(company.municipality || company.canton) && (
                    <p className="mt-0.5" style={{ fontSize: 12, color: "#6b7480" }}>
                      {[company.municipality, company.canton].filter(Boolean).join(", ")}
                    </p>
                  )}
                  {branchOffices.length > 0 && (
                    <p className="mt-1.5" style={{ fontSize: 12, color: "#6b7480" }}>
                      {branchOffices.length} branch office{branchOffices.length !== 1 ? "s" : ""}
                    </p>
                  )}
                  {company.lat == null && (
                    <p className="italic" style={{ fontSize: 13, color: "#9aa2ad" }}>Not geocoded</p>
                  )}
                </div>
              </div>

              {/* Website */}
              <div className="bg-white rounded-2xl border border-[#e6e8ec] p-5">
                <div className="flex items-center justify-between mb-3">
                  <h2 className="font-bold" style={{ fontSize: 15, color: "#1f2733" }}>Website</h2>
                  {!readOnlyDemo && !company.website_checked_at && (
                    <button type="button" onClick={handleWebSearch} disabled={searchingWeb}
                      className="hover:underline disabled:opacity-50" style={{ fontSize: 12, color: "#2563eb" }}>
                      {searchingWeb ? "Searching…" : "Find website"}
                    </button>
                  )}
                </div>
                {company.website_url ? (
                  <a href={company.website_url} target="_blank" rel="noopener noreferrer"
                    className="flex items-center gap-2 min-w-0 hover:underline" style={{ fontSize: 13, color: "#2563eb" }}>
                    <Globe size={13} className="shrink-0" />
                    <span className="truncate">{company.website_url.replace(/^https?:\/\//, "")}</span>
                    <ExternalLink size={11} className="shrink-0" />
                  </a>
                ) : (
                  <p className="italic" style={{ fontSize: 13, color: "#9aa2ad" }}>No website found</p>
                )}
              </div>

              {/* Keywords */}
              {(company.purpose_keywords || company.tfidf_cluster) && (
                <div className="bg-white rounded-2xl border border-[#e6e8ec] p-5">
                  <h2 className="font-bold mb-3" style={{ fontSize: 15, color: "#1f2733" }}>Keywords</h2>
                  <div className="flex flex-wrap gap-1.5">
                    {company.purpose_keywords?.split(",").filter(Boolean).map(k => k.trim()).filter(Boolean).map(k => (
                      <Link key={k} href={`/app/search?purpose_keywords=${encodeURIComponent(k)}`}>
                        <span className="inline-block bg-[#f7f9fc] border border-[#eef0f3] rounded-full px-2.5 py-0.5 cursor-pointer hover:bg-[#eff4fe] hover:border-[#cdddfb] hover:text-[#1d4ed8] transition-colors"
                          style={{ fontSize: 12, color: "#3f4854" }}>
                          {k}
                        </span>
                      </Link>
                    ))}
                    {company.tfidf_cluster?.split("|").filter(Boolean).map(cluster => cluster.trim()).filter(Boolean).map(cluster => (
                      <Link key={cluster} href={`/app/search?tfidf_cluster=${encodeURIComponent(cluster)}`}>
                        <span className="inline-block bg-[#f3ecfd] border border-[#e9d5fb] rounded-full px-2.5 py-0.5 cursor-pointer hover:bg-[#ede9fe] transition-colors"
                          style={{ fontSize: 12, color: "#7c3aed" }}>
                          {formatClusterLabel(cluster)}
                        </span>
                      </Link>
                    ))}
                  </div>
                </div>
              )}

              {/* Classification (compact) */}
              {(company.ai_category || company.purpose_language) && (
                <div className="bg-white rounded-2xl border border-[#e6e8ec] p-5 space-y-3">
                  <h2 className="font-bold" style={{ fontSize: 15, color: "#1f2733" }}>{t.classification}</h2>
                  {company.ai_category && (
                    <div>
                      <div className="font-semibold uppercase mb-1" style={{ fontSize: 11, letterSpacing: "0.06em", color: "#9aa2ad" }}>AI category</div>
                      <Link href={`/app/search?ai_category=${encodeURIComponent(company.ai_category)}`}>
                        <Badge className="bg-slate-100 text-slate-700 text-xs cursor-pointer hover:bg-slate-200">{company.ai_category}</Badge>
                      </Link>
                    </div>
                  )}
                  {company.purpose_language && (
                    <div className="flex items-center gap-2">
                      <div className="font-semibold uppercase" style={{ fontSize: 11, letterSpacing: "0.06em", color: "#9aa2ad" }}>Language</div>
                      <Badge className="bg-indigo-50 text-indigo-700 text-xs">{company.purpose_language.toUpperCase()}</Badge>
                    </div>
                  )}
                </div>
              )}

              {/* Source coverage */}
              <div className="bg-white rounded-2xl border border-[#e6e8ec] p-5">
                <h2 className="font-bold mb-3" style={{ fontSize: 15, color: "#1f2733" }}>Source coverage</h2>
                <div className="grid grid-cols-2 gap-x-4 gap-y-2">
                  <CoverageItem label="Zefix" present={true} />
                  <CoverageItem label="Purpose" present={!!company.purpose} />
                  <CoverageItem label="NOGA" present={!!company.noga_code} />
                  <CoverageItem label="AI class." present={!!company.ai_category} />
                  <CoverageItem label="Website" present={!!company.website_url} />
                  <CoverageItem label="Geocoded" present={company.lat != null} />
                  <CoverageItem label="SOGC" present={!!company.sogc_date} />
                  <CoverageItem label="AI score" present={(company.ai_score ?? 0) > 0} />
                </div>
              </div>

              <CorporateShareholdersPanel companyId={company.id} />

              {/* Notes */}
              {!readOnlyDemo && (
                <div className="bg-white rounded-2xl border border-[#e6e8ec] p-5">
                  <h2 className="font-bold mb-4" style={{ fontSize: 15, color: "#1f2733" }}>{t.notes} ({notes.length})</h2>
                  <form onSubmit={handleAddNote} className="mb-4">
                    <div className="flex gap-2">
                      <textarea
                        value={noteText}
                        onChange={e => setNoteText(e.target.value)}
                        placeholder={t.addnote}
                        rows={2}
                        className="flex-1 px-3 py-2 border border-[#e6e8ec] rounded-xl focus:outline-none focus:ring-2 focus:ring-blue-300 resize-none"
                        style={{ fontSize: 13 }}
                      />
                      <button type="submit" disabled={addingNote || !noteText.trim()}
                        className="flex items-center gap-1.5 bg-[#2563eb] hover:bg-[#1d4ed8] disabled:opacity-50 text-white px-3 py-2 rounded-xl font-medium transition-colors self-start"
                        style={{ fontSize: 13 }}>
                        {addingNote ? <Loader2 size={14} className="animate-spin" /> : <Plus size={14} />}
                        {t.addnote}
                      </button>
                    </div>
                  </form>
                  {notes.length === 0 && (
                    <p className="text-center py-4" style={{ fontSize: 13, color: "#9aa2ad" }}>{t.nonotes}</p>
                  )}
                  <div className="space-y-2">
                    {notes.map(n => (
                      <div key={n.id} className="flex gap-3 rounded-xl p-3" style={{ background: "#f7f9fc" }}>
                        <div className="h-8 w-8 rounded-full bg-[#eef0f3] flex items-center justify-center shrink-0 font-semibold"
                          style={{ fontSize: 12, color: "#6b7480" }}>U</div>
                        <div className="flex-1 min-w-0">
                          <div className="flex items-center gap-2">
                            <span className="font-semibold" style={{ fontSize: 12, color: "#6b7480" }}>{t.user}</span>
                            <span style={{ fontSize: 12, color: "#9aa2ad" }}>{fmtRelativeTime(n.created_at)}</span>
                          </div>
                          <p className="whitespace-pre-wrap mt-0.5" style={{ fontSize: 13, color: "#1f2733" }}>{n.content}</p>
                          <p className="mt-1" style={{ fontSize: 11, color: "#9aa2ad" }}>{fmtDateTime(n.created_at)}</p>
                        </div>
                        <button onClick={() => handleDeleteNote(n.id)} className="p-1 transition-colors shrink-0" style={{ color: "#9aa2ad" }}
                          onMouseEnter={e => (e.currentTarget.style.color = "#ef4444")}
                          onMouseLeave={e => (e.currentTarget.style.color = "#9aa2ad")}>
                          <Trash2 size={14} />
                        </button>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          </div>
        </div>
      </div>

      {/* Website picker modal */}
      {!readOnlyDemo && showWebsitePicker && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4" style={{ background: "rgba(23,32,46,0.34)" }}>
          <div className="bg-white rounded-2xl shadow-xl w-full max-w-2xl flex flex-col" style={{ maxHeight: "85vh" }}>
            <div className="flex items-start justify-between px-6 py-4 border-b border-[#eef0f3] shrink-0">
              <div>
                <h2 className="font-bold" style={{ fontSize: 15, color: "#1f2733" }}>{t.selectdifferentwebsite}</h2>
                <p className="mt-0.5" style={{ fontSize: 12, color: "#9aa2ad" }}>
                  Scores (0–100) reflect name + location + purpose match quality
                </p>
              </div>
              <button type="button" onClick={() => setShowWebsitePicker(false)} className="transition-colors ml-4 mt-0.5" style={{ color: "#9aa2ad" }}>
                <X size={20} />
              </button>
            </div>
            <div className="overflow-y-auto p-6 space-y-3">
              {googleResults.length === 0 ? (
                <p className="text-center py-8 italic" style={{ fontSize: 13, color: "#9aa2ad" }}>
                  No search results yet. Run a web search first.
                </p>
              ) : googleResults.map(r => {
                const score = Math.round(r.score);
                const scoreColor = score >= 70 ? "#15803d" : score >= 40 ? "#2563eb" : "#b45309";
                const isCurrent = r.link === company.website_url;
                return (
                  <div key={r.link} className="rounded-xl border p-4"
                    style={{ borderColor: isCurrent ? "#15803d" : "#e6e8ec", background: isCurrent ? "#f0fdf4" : "#fff" }}>
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center gap-2 mb-1.5">
                          <span className="font-mono font-bold" style={{ fontSize: 14, color: scoreColor }}>{score}</span>
                          <div className="h-1.5 w-16 rounded-full overflow-hidden" style={{ background: "#eef0f3" }}>
                            <div className="h-full rounded-full" style={{ width: `${score}%`, background: scoreColor }} />
                          </div>
                          {isCurrent && (
                            <span className="font-semibold rounded-full px-2 py-0.5" style={{ fontSize: 11, background: "#e9f6ee", color: "#15803d" }}>
                              Current
                            </span>
                          )}
                        </div>
                        <div className="font-semibold truncate" style={{ fontSize: 13, color: "#1f2733" }}>{r.title || r.link}</div>
                        <div className="truncate" style={{ fontSize: 12, color: "#2563eb" }}>{r.link}</div>
                        {r.snippet && (
                          <div className="mt-1 line-clamp-2" style={{ fontSize: 12, color: "#6b7480" }}>{r.snippet}</div>
                        )}
                        <div className="mt-2" style={{ fontSize: 11, color: "#9aa2ad" }}>
                          Score: company name match + location ({company.municipality ?? "—"}) + purpose keyword relevance
                        </div>
                      </div>
                      {!isCurrent && (
                        <button type="button" disabled={selectingWebsite === r.link}
                          onClick={() => handleSelectWebsite(r.link)}
                          className={cn(
                            "font-medium px-3 py-1.5 rounded-[10px] border transition-colors shrink-0",
                            selectingWebsite === r.link
                              ? "border-[#e6e8ec] text-[#9aa2ad]"
                              : "border-[#cdddfb] text-[#2563eb] hover:bg-[#eff4fe]"
                          )}
                          style={{ fontSize: 13 }}>
                          {selectingWebsite === r.link ? (
                            <><Loader2 size={12} className="animate-spin inline mr-1" />Selecting…</>
                          ) : t.useThisWebsite}
                        </button>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        </div>
      )}

      {/* NOGA v2 explain modal */}
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
                          <tr key={`${c.code}-${i}`} className={cn("border-b border-slate-100 last:border-0", c.is_peak && "bg-amber-50 font-semibold")}>
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
