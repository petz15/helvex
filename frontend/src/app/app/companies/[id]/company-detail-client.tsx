"use client";
import { useState } from "react";
import Link from "next/link";
import { ExternalLink, ChevronLeft, Globe, MapPin, Building2, Phone, Mail, FileText, Plus, Trash2, Loader2 } from "lucide-react";
import { ScoreBar } from "@/components/ui/score-bar";
import { Badge } from "@/components/ui/badge";
import { reviewBadgeClass, proposalBadgeClass, fmtDate, fmtDateTime, cn } from "@/lib/utils";
import { createNote, deleteNote, updateCompany } from "@/lib/api";
import { REVIEW_STATUSES, PROPOSAL_STATUSES } from "@/lib/types";
import type { Company, Note } from "@/lib/types";

interface Props {
  company: Company;
}

export function CompanyDetailClient({ company: initial }: Props) {
  const [company, setCompany] = useState(initial);
  const [notes, setNotes] = useState<Note[]>(initial.notes ?? []);
  const [saving, setSaving] = useState(false);
  const [noteText, setNoteText] = useState("");
  const [addingNote, setAddingNote] = useState(false);

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
      <div className="bg-white rounded-xl border border-slate-200 p-6 shadow-sm">
        <div className="flex items-start justify-between gap-4">
          <div>
            <h1 className="text-2xl font-bold text-slate-900">{company.name}</h1>
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
          {company.website_url && (
            <a
              href={company.website_url}
              target="_blank"
              rel="noopener noreferrer"
              className="flex items-center gap-1.5 text-sm text-blue-600 hover:text-blue-800 px-3 py-1.5 rounded-lg border border-blue-200 hover:bg-blue-50 shrink-0 transition-colors"
            >
              <Globe size={13} /> Visit website <ExternalLink size={11} />
            </a>
          )}
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Scores */}
        <div className="bg-white rounded-xl border border-slate-200 p-5 space-y-4 shadow-sm">
          <h2 className="text-sm font-semibold text-slate-700">Scores</h2>
          {[
            { label: "Combined", score: company.combined_score, date: null },
            { label: "Google", score: company.website_match_score, date: company.website_checked_at },
            { label: "Claude", score: company.claude_score, date: company.claude_scored_at },
            { label: "Zefix", score: company.zefix_score, date: company.zefix_scored_at },
          ].map(({ label, score, date }) => (
            <div key={label}>
              <div className="flex items-center justify-between mb-1">
                <span className="text-xs text-slate-500">{label}</span>
                {date && <span className="text-xs text-slate-400">{fmtDate(date)}</span>}
              </div>
              <ScoreBar score={score} />
            </div>
          ))}
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
                <dd className="text-slate-700">{value}</dd>
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
              <p className="text-xs text-slate-600 leading-relaxed">{company.purpose}</p>
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
            <h2 className="text-sm font-semibold text-slate-700">Status</h2>
            <div>
              <label className="text-xs text-slate-500 block mb-1">Review status</label>
              <select
                value={company.review_status ?? ""}
                disabled={saving}
                onChange={e => handleStatusChange("review_status", e.target.value)}
                className="w-full rounded-lg border border-slate-200 px-2.5 py-1.5 text-sm text-slate-800 focus:outline-none focus:ring-2 focus:ring-blue-400"
              >
                <option value="">— pending —</option>
                {REVIEW_STATUSES.map(s => <option key={s.value} value={s.value}>{s.label}</option>)}
              </select>
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
        <form onSubmit={handleAddNote} className="flex gap-2 mb-4">
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
        </form>
        {notes.length === 0 && (
          <p className="text-sm text-slate-400 text-center py-4">No notes yet</p>
        )}
        <div className="space-y-2">
          {notes.map(n => (
            <div key={n.id} className="flex gap-3 bg-slate-50 rounded-lg p-3">
              <div className="flex-1 min-w-0">
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
