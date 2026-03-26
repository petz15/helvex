"use client";
import { useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { ExternalLink, ChevronLeft, Globe, MapPin, Building2, Phone, Mail, FileText, Plus, Trash2, Loader2 } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { reviewBadgeClass, proposalBadgeClass, fmtDate, fmtDateTime, fmtRelativeTime, cn, scoreColor } from "@/lib/utils";
import { createNote, deleteNote, selectCompanyWebsite, updateCompany } from "@/lib/api";
import { REVIEW_STATUSES, PROPOSAL_STATUSES } from "@/lib/types";
import type { Company, Note, GoogleScoredResult } from "@/lib/types";
import "leaflet/dist/leaflet.css";

interface Props {
  company: Company;
}

export function CompanyDetailClient({ company: initial }: Props) {
  const [company, setCompany] = useState(initial);
  const [notes, setNotes] = useState<Note[]>(initial.notes ?? []);
  const [saving, setSaving] = useState(false);
  const [noteText, setNoteText] = useState("");
  const [addingNote, setAddingNote] = useState(false);
  const [purposeExpanded, setPurposeExpanded] = useState(false);
  const [showWebsitePicker, setShowWebsitePicker] = useState(false);
  const [selectingWebsite, setSelectingWebsite] = useState<string | null>(null);

  const mapRef = useRef<HTMLDivElement>(null);
  const mapInstanceRef = useRef<import("leaflet").Map | null>(null);

  const combinedScore = company.combined_score;

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

  const headerAccentClass = useMemo(() => {
    if (combinedScore == null) return "border-l-slate-200";
    if (combinedScore >= 70) return "border-l-green-500";
    if (combinedScore >= 40) return "border-l-yellow-400";
    return "border-l-red-400";
  }, [combinedScore]);

  const scoreTextClass = useMemo(() => {
    if (combinedScore == null) return "text-slate-600";
    if (combinedScore >= 70) return "text-green-700";
    if (combinedScore >= 40) return "text-yellow-700";
    return "text-red-700";
  }, [combinedScore]);

  const lastScoredIso = useMemo(() => {
    const dates = [company.zefix_scored_at, company.claude_scored_at, company.website_checked_at].filter(Boolean) as string[];
    if (dates.length === 0) return null;
    dates.sort((a, b) => new Date(b).getTime() - new Date(a).getTime());
    return dates[0] ?? null;
  }, [company.zefix_scored_at, company.claude_scored_at, company.website_checked_at]);

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

  async function handleStatusChange(field: "review_status" | "proposal_status", value: string) {
    setSaving(true);
    try {
      const updated = await updateCompany(company.id, { [field]: value || null });
      setCompany(c => ({ ...c, ...updated }));
    } finally {
      setSaving(false);
    }
  }

  async function handleAddNote(e: React.FormEvent) {
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
    await deleteNote(company.id, noteId);
    setNotes(ns => ns.filter(n => n.id !== noteId));
  }

  async function handleSelectWebsite(link: string) {
    setSelectingWebsite(link);
    try {
      const updated = await selectCompanyWebsite(company.id, link);
      setCompany(updated);
      setShowWebsitePicker(false);
    } finally {
      setSelectingWebsite(null);
    }
  }

  return (
    <div className="max-w-5xl mx-auto px-4 py-6 space-y-6">
      {/* Breadcrumb */}
      <div className="flex items-center gap-2 text-sm text-slate-500">
        <Link href="/app/dashboard" className="hover:text-slate-700 flex items-center gap-1">
          <ChevronLeft size={14} /> Dashboard
        </Link>
        <span>/</span>
        <span className="text-slate-800 font-medium">{company.name}</span>
      </div>

      {/* Header */}
      <div className={cn("bg-white rounded-xl border border-slate-200 border-l-4 p-6 shadow-sm", headerAccentClass)}>
        <div className="flex items-start justify-between gap-4">
          <div>
            <div className="flex items-start gap-4">
              <h1 className="text-2xl font-bold text-slate-900">{company.name}</h1>
              <div className="shrink-0">
                <div className={cn("text-2xl font-extrabold leading-none", scoreTextClass)}>
                  {combinedScore == null ? "—" : Math.round(combinedScore)} <span className="text-sm font-semibold text-slate-400">/ 100</span>
                </div>
                <div className="text-xs text-slate-400 mt-0.5">Combined score</div>
              </div>
            </div>
            <p className="text-sm text-slate-500 mt-0.5">
              {[company.legal_form, company.canton, company.municipality].filter(Boolean).join(" · ")}
            </p>
            <div className="flex flex-wrap gap-2 mt-3">
              <Badge className={cn("text-xs", reviewBadgeClass(company.review_status))}>
                {company.review_status?.replace(/_/g, " ") ?? "Pending review"}
              </Badge>
              {company.proposal_status && company.proposal_status !== "not_sent" && (
                <Badge className={cn("text-xs", proposalBadgeClass(company.proposal_status))}>
                  Proposal: {company.proposal_status}
                </Badge>
              )}
              {company.tags && company.tags.split(",").map(t => (
                <Badge key={t.trim()} className="bg-slate-100 text-slate-600 text-xs">{t.trim()}</Badge>
              ))}
            </div>
          </div>
          <div className="flex flex-col gap-2 items-end shrink-0">
            {company.website_url && (
              <a
                href={company.website_url}
                target="_blank"
                rel="noopener noreferrer"
                className="flex items-center gap-1.5 text-sm text-blue-600 hover:text-blue-800 px-3 py-1.5 rounded-lg border border-blue-200 hover:bg-blue-50 transition-colors"
              >
                <Globe size={13} /> Visit website <ExternalLink size={11} />
              </a>
            )}

            {alternativeWebsiteResults.length > 0 && (
              <button
                type="button"
                onClick={() => setShowWebsitePicker(v => !v)}
                className="flex items-center gap-1.5 text-sm text-slate-700 hover:text-slate-900 px-3 py-1.5 rounded-lg border border-slate-200 hover:bg-slate-50 transition-colors"
              >
                Change website
              </button>
            )}
            {company.uid && (
              <a
                href={`https://www.zefix.ch/en/search/entity/list/firm/${company.uid}`}
                target="_blank"
                rel="noopener noreferrer"
                className="flex items-center gap-1.5 text-sm text-slate-700 hover:text-slate-900 px-3 py-1.5 rounded-lg border border-slate-200 hover:bg-slate-50 transition-colors"
              >
                View on Zefix <ExternalLink size={11} />
              </a>
            )}
          </div>
        </div>
      </div>

      {showWebsitePicker && (
        <div className="bg-white rounded-xl border border-slate-200 p-5 shadow-sm">
          <div className="flex items-center justify-between">
            <h2 className="text-sm font-semibold text-slate-700">Select a different website</h2>
            <button
              type="button"
              onClick={() => setShowWebsitePicker(false)}
              className="text-sm text-slate-600 hover:text-slate-900"
            >
              Close
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
                    {selectingWebsite === r.link ? "Selecting…" : "Use this"}
                  </button>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Scores */}
        <div className="bg-white rounded-xl border border-slate-200 p-5 space-y-4 shadow-sm">
          <div className="flex items-center justify-between">
            <h2 className="text-sm font-semibold text-slate-700">Scores</h2>
            {lastScoredIso && (
              <span className="text-xs text-slate-500 bg-slate-50 border border-slate-200 rounded-full px-2 py-0.5">
                last scored {fmtRelativeTime(lastScoredIso)}
              </span>
            )}
          </div>

          {[
            { label: "Combined", score: company.combined_score, date: null },
            { label: "Google", score: company.website_match_score, date: company.website_checked_at },
            { label: "Claude", score: company.claude_score, date: company.claude_scored_at },
            { label: "Zefix", score: company.zefix_score, date: company.zefix_scored_at },
          ].map(({ label, score, date }) => {
            const textCls = score == null ? "text-slate-600" : score >= 70 ? "text-green-700" : score >= 40 ? "text-yellow-700" : "text-red-700";
            return (
              <div key={label} className="rounded-lg border border-slate-200 bg-white p-3">
                <div className="flex items-center justify-between">
                  <div>
                    <div className="text-xs text-slate-500">{label}</div>
                    {date && <div className="text-xs text-slate-400">{fmtDate(date)}</div>}
                  </div>
                  <div className={cn("text-lg font-bold", textCls)}>
                    {score == null ? "—" : Math.round(score)}
                  </div>
                </div>
                <div className="mt-2 h-2 bg-slate-100 rounded-full overflow-hidden">
                  <div
                    className={cn("h-full rounded-full transition-all", scoreColor(score))}
                    style={{ width: `${score ?? 0}%` }}
                  />
                </div>
              </div>
            );
          })}
          {company.claude_category && (
            <div className="pt-2 border-t border-slate-100">
              <span className="text-xs text-slate-400 block mb-1">Category</span>
              <span className="text-sm text-slate-700">{company.claude_category}</span>
            </div>
          )}
          {company.claude_freeform && (
            <div>
              <span className="text-xs text-slate-400 block mb-1">Claude notes</span>
              <p className="text-xs text-slate-600 whitespace-pre-wrap">{company.claude_freeform}</p>
            </div>
          )}
        </div>

        {/* Company info */}
        <div className="bg-white rounded-xl border border-slate-200 p-5 space-y-3 shadow-sm">
          <h2 className="text-sm font-semibold text-slate-700">Company Info</h2>

          {company.lat != null && company.lon != null && (
            <div className="rounded-lg border border-slate-200 overflow-hidden">
              <div ref={mapRef} className="h-40 w-full" />
            </div>
          )}

          <dl className="space-y-2 text-sm">
            {[
              { label: "UID", value: company.uid },
              { label: "Legal form", value: company.legal_form },
              { label: "Status", value: company.status },
              { label: "Canton", value: company.canton },
              { label: "Municipality", value: company.municipality },
            ].map(({ label, value }) => value && (
              <div key={label} className="flex gap-2">
                <dt className="text-slate-400 w-24 shrink-0">{label}</dt>
                <dd className={cn("text-slate-700", label === "Status" && String(value).toLowerCase() === "cancelled" && "text-red-700 font-semibold")}>{value}</dd>
              </div>
            ))}
            {company.address && (
              <div className="flex gap-2">
                <dt className="text-slate-400 w-24 shrink-0 flex items-center gap-1"><MapPin size={11} /> Address</dt>
                <dd className="text-slate-700">{company.address}</dd>
              </div>
            )}
            {company.capital_nominal && (
              <div className="flex gap-2">
                <dt className="text-slate-400 w-24 shrink-0">Capital</dt>
                <dd className="text-slate-700">{company.capital_nominal} {company.capital_currency}</dd>
              </div>
            )}
          </dl>
          {company.purpose && (
            <div className="pt-2 border-t border-slate-100">
              <span className="text-xs text-slate-400 flex items-center gap-1 mb-1"><FileText size={11} /> Purpose</span>
              <div className="relative">
                <p
                  className={cn(
                    "text-xs text-slate-600 leading-relaxed whitespace-pre-wrap",
                    !purposeExpanded && "max-h-16 overflow-hidden",
                  )}
                >
                  {company.purpose}
                </p>
                {!purposeExpanded && company.purpose.length > 220 && (
                  <div className="absolute inset-x-0 bottom-0 h-8 bg-gradient-to-t from-white to-transparent" />
                )}
              </div>
              {company.purpose.length > 220 && (
                <button
                  type="button"
                  onClick={() => setPurposeExpanded(v => !v)}
                  className="mt-1 text-xs text-blue-600 hover:underline"
                >
                  {purposeExpanded ? "Show less" : "Show more"}
                </button>
              )}
            </div>
          )}
          {company.cantonal_excerpt_web && (
            <a href={company.cantonal_excerpt_web} target="_blank" rel="noopener noreferrer"
              className="text-xs text-blue-600 hover:underline flex items-center gap-1 mt-2">
              Cantonal excerpt <ExternalLink size={10} />
            </a>
          )}
        </div>

        {/* Actions + Contact */}
        <div className="space-y-4">
          <div className="bg-white rounded-xl border border-slate-200 p-5 space-y-4 shadow-sm">
            <div className="relative">
              <h2 className="text-sm font-semibold text-slate-700">Status</h2>

              {saving && (
                <div className="absolute inset-0 bg-white/60 rounded-xl flex items-center justify-center">
                  <Loader2 size={18} className="animate-spin text-slate-600" />
                </div>
              )}
            </div>
            <div>
              <label className="text-xs text-slate-500 block mb-1">Review status</label>
              <div className="flex flex-wrap gap-2">
                <button
                  type="button"
                  disabled={saving}
                  onClick={() => handleStatusChange("review_status", "")}
                  className={cn(
                    "px-2.5 py-1 rounded-full text-xs font-medium border transition-colors",
                    (company.review_status ?? "") === "" ? "bg-gray-100 text-gray-600 border-gray-200" : "bg-white text-gray-500 border-slate-200 hover:bg-slate-50",
                  )}
                >
                  Pending
                </button>
                {REVIEW_STATUSES.map(s => {
                  const active = company.review_status === s.value;
                  return (
                    <button
                      key={s.value}
                      type="button"
                      disabled={saving}
                      onClick={() => handleStatusChange("review_status", s.value)}
                      className={cn(
                        "px-2.5 py-1 rounded-full text-xs font-medium border transition-all",
                        reviewBadgeClass(s.value),
                        active ? "border-slate-300" : "border-transparent opacity-70 hover:opacity-100",
                      )}
                    >
                      {s.label}
                    </button>
                  );
                })}
              </div>
            </div>
            <div>
              <label className="text-xs text-slate-500 block mb-1">Proposal status</label>
              <select
                value={company.proposal_status ?? ""}
                disabled={saving}
                onChange={e => handleStatusChange("proposal_status", e.target.value)}
                className="w-full rounded-lg border border-slate-200 px-2.5 py-1.5 text-sm text-slate-800 focus:outline-none focus:ring-2 focus:ring-blue-400"
              >
                <option value="">— none —</option>
                {PROPOSAL_STATUSES.map(s => <option key={s.value} value={s.value}>{s.label}</option>)}
              </select>
            </div>
          </div>

          {(company.contact_name || company.contact_email || company.contact_phone) && (
            <div className="bg-white rounded-xl border border-slate-200 p-5 space-y-2 shadow-sm">
              <h2 className="text-sm font-semibold text-slate-700 flex items-center gap-1.5">
                <Building2 size={14} /> Contact
              </h2>
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

          <div className="bg-white rounded-xl border border-slate-200 p-5 space-y-2 shadow-sm">
            <h2 className="text-sm font-semibold text-slate-700">Timeline</h2>
            <dl className="space-y-1 text-xs">
              {[
                { label: "Created", value: fmtDate(company.created_at) },
                { label: "Updated", value: fmtDateTime(company.updated_at) },
                { label: "Google searched", value: fmtDateTime(company.website_checked_at) },
              ].map(({ label, value }) => (
                <div key={label} className="flex justify-between">
                  <dt className="text-slate-400">{label}</dt>
                  <dd className="text-slate-600">{value}</dd>
                </div>
              ))}
            </dl>
          </div>
        </div>
      </div>

      {/* Notes */}
      <div className="bg-white rounded-xl border border-slate-200 p-5 shadow-sm">
        <h2 className="text-sm font-semibold text-slate-700 mb-4">Notes ({notes.length})</h2>
        <form onSubmit={handleAddNote} className="mb-4">
          <div className="flex gap-2">
            <textarea
              value={noteText}
              onChange={e => setNoteText(e.target.value)}
              placeholder="Add a note…"
              rows={2}
              className="flex-1 px-3 py-2 border border-slate-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-300 resize-none"
            />
            <button
              type="submit"
              disabled={addingNote || !noteText.trim()}
              className="flex items-center gap-1.5 bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white px-3 py-2 rounded-lg text-sm font-medium transition-colors self-start"
            >
              {addingNote ? <Loader2 size={14} className="animate-spin" /> : <Plus size={14} />}
              Add
            </button>
          </div>
          <div className="mt-1 flex justify-end">
            <span className="text-xs text-slate-400">{noteText.length} chars</span>
          </div>
        </form>
        {notes.length === 0 && (
          <p className="text-sm text-slate-400 text-center py-4">No notes yet</p>
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
                  <span className="text-xs font-semibold text-slate-600">User</span>
                  <span className="text-xs text-slate-400">{fmtRelativeTime(n.created_at)}</span>
                </div>
                <p className="text-sm text-slate-700 whitespace-pre-wrap">{n.content}</p>
                <p className="text-xs text-slate-400 mt-1">{fmtDateTime(n.created_at)}</p>
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
      </div>
    </div>
  );
}
