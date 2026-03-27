"use client";
import { useEffect, useState } from "react";
import { X, ExternalLink, ChevronRight, MapPin, Loader2 } from "lucide-react";
import Link from "next/link";
import { ScoreBar } from "@/components/ui/score-bar";
import { Badge } from "@/components/ui/badge";
import { reviewBadgeClass, proposalBadgeClass, fmtDate, cn } from "@/lib/utils";
import { updateCompany } from "@/lib/api";
import type { Company } from "@/lib/types";

interface CompanyPreviewProps {
  company: Company | null;
  onClose: () => void;
  onUpdated?: (company: Company) => void;
}

function avatarBg(score: number | null): string {
  if (score == null) return "bg-slate-200 text-slate-700";
  if (score >= 70) return "bg-green-100 text-green-800";
  if (score >= 40) return "bg-yellow-100 text-yellow-800";
  return "bg-red-100 text-red-800";
}

export function CompanyPreview({ company: incoming, onClose, onUpdated }: CompanyPreviewProps) {
  const [company, setCompany] = useState<Company | null>(incoming);
  const [updating, setUpdating] = useState<string | null>(null);

  // keep local copy in sync when selection changes
  useEffect(() => {
    setCompany(incoming);
  }, [incoming]);

  if (!company) return null;

  const initials = (company.name || "?").trim().slice(0, 1).toUpperCase();

  async function quickSetReview(status: string) {
    if (!company) return;
    const current = company;
    setUpdating(status);
    try {
      const updated = await updateCompany(current.id, { review_status: status });
      setCompany((c) => (c ? { ...c, ...updated } : c));
      onUpdated?.({ ...current, ...updated });
    } finally {
      setUpdating(null);
    }
  }

  return (
    <aside className="w-80 shrink-0 flex flex-col bg-white border-l border-slate-200 overflow-y-auto">
      {/* Header */}
      <div className="flex items-start justify-between px-4 py-3 border-b border-slate-100">
        <div className="flex-1 min-w-0 flex gap-2">
          <div className={cn("h-8 w-8 rounded-full flex items-center justify-center text-xs font-bold shrink-0", avatarBg(company.combined_score))}>
            {initials}
          </div>
          <div className="min-w-0">
            <h2 className="font-semibold text-slate-800 text-sm leading-snug truncate flex items-center gap-1">
              {company.name}
              {company.zefix_detail_web && (
                <a href={company.zefix_detail_web} target="_blank" rel="noopener noreferrer" className="text-slate-400 hover:text-blue-600 shrink-0" title="View on Zefix">
                  <ExternalLink size={11} />
                </a>
              )}
            </h2>
            <p className="text-xs text-slate-500 mt-0.5 flex items-center gap-1">
              <MapPin size={12} className="text-slate-400" />
              <span className="truncate">{[company.municipality, company.canton].filter(Boolean).join(", ") || "—"}</span>
              {company.legal_form && <span className="text-slate-300">·</span>}
              {company.legal_form && <span className="truncate">{company.legal_form}</span>}
            </p>
          </div>
        </div>
        <button onClick={onClose} className="ml-2 text-slate-400 hover:text-slate-600 mt-0.5">
          <X size={16} />
        </button>
      </div>

      {/* Quick actions */}
      <div className="px-4 py-2 flex flex-wrap gap-2 border-b border-slate-100">
        <button
          type="button"
          disabled={!!updating}
          onClick={() => quickSetReview("interesting")}
          className="px-2.5 py-1 rounded-full text-xs font-medium bg-yellow-100 text-yellow-800 hover:bg-yellow-200 disabled:opacity-50 transition-colors"
        >
          {updating === "interesting" ? <Loader2 size={12} className="inline mr-1 animate-spin" /> : null}
          Mark interesting
        </button>
        <button
          type="button"
          disabled={!!updating}
          onClick={() => quickSetReview("rejected")}
          className="px-2.5 py-1 rounded-full text-xs font-medium bg-red-100 text-red-800 hover:bg-red-200 disabled:opacity-50 transition-colors"
        >
          {updating === "rejected" ? <Loader2 size={12} className="inline mr-1 animate-spin" /> : null}
          Mark rejected
        </button>
        <button
          type="button"
          disabled={!!updating}
          onClick={() => quickSetReview("confirmed_proposal")}
          className="px-2.5 py-1 rounded-full text-xs font-medium bg-green-100 text-green-800 hover:bg-green-200 disabled:opacity-50 transition-colors"
        >
          {updating === "confirmed_proposal" ? <Loader2 size={12} className="inline mr-1 animate-spin" /> : null}
          Mark confirmed
        </button>
      </div>

      {/* Badges */}
      <div className="px-4 py-2 flex flex-wrap gap-1.5 border-b border-slate-100">
        <Badge className={reviewBadgeClass(company.review_status)}>
          {company.review_status?.replace(/_/g, " ") ?? "Pending"}
        </Badge>
        {company.contact_status && company.contact_status !== "not_sent" && (
          <Badge className={proposalBadgeClass(company.contact_status)}>
            {company.contact_status}
          </Badge>
        )}
        {company.tags && company.tags.split(",").map((t) => (
          <Badge key={t.trim()} className="bg-slate-100 text-slate-600">{t.trim()}</Badge>
        ))}
      </div>

      {/* Scores */}
      <div className="px-4 py-3 flex flex-col gap-2 border-b border-slate-100">
        <div className="flex items-center justify-between text-xs">
          <span className="text-slate-500 w-16">Combined</span>
          <ScoreBar score={company.combined_score} className="flex-1" />
        </div>
        <div className="flex items-center justify-between text-xs">
          <span className="text-slate-500 w-16">Web</span>
          <ScoreBar score={company.web_score} className="flex-1" />
        </div>
        <div className="flex items-center justify-between text-xs">
          <span className="text-slate-500 w-16">AI</span>
          <ScoreBar score={company.ai_score} className="flex-1" />
        </div>
        <div className="flex items-center justify-between text-xs">
          <span className="text-slate-500 w-16">Flex</span>
          <ScoreBar score={company.flex_score} className="flex-1" />
        </div>
      </div>

      {/* Details */}
      <div className="px-4 py-3 flex flex-col gap-2 text-sm border-b border-slate-100">
        {company.website_url && (
          <div>
            <span className="text-xs text-slate-400 block mb-0.5">Website</span>
            <a href={company.website_url} target="_blank" rel="noopener noreferrer"
              className="text-blue-600 hover:underline flex items-center gap-1 text-xs truncate">
              {company.website_url} <ExternalLink size={11} />
            </a>
          </div>
        )}
        {(company.address || company.address_city || company.address_zip) && (
          <div>
            <span className="text-xs text-slate-400 block mb-0.5">Address</span>
            {company.address && <span className="text-xs text-slate-700">{company.address}</span>}
            {(company.address_zip || company.address_city) && (
              <span className="text-xs text-slate-500 block mt-0.5">
                {[company.address_zip, company.address_city].filter(Boolean).join(" ")}
              </span>
            )}
          </div>
        )}
        {company.purpose && (
          <div>
            <span className="text-xs text-slate-400 block mb-0.5">Purpose</span>
            <p className="text-xs text-slate-700 line-clamp-4">{company.purpose}</p>
          </div>
        )}
        {company.ai_category && (
          <div>
            <span className="text-xs text-slate-400 block mb-0.5">AI category</span>
            <span className="text-xs text-slate-700">{company.ai_category}</span>
          </div>
        )}
        {company.translations && (() => {
          try {
            const names: string[] = JSON.parse(company.translations);
            if (names.length > 0) {
              return (
                <div>
                  <span className="text-xs text-slate-400 block mb-0.5">Also known as:</span>
                  <div className="flex flex-wrap gap-1">
                    {names.map((t, i) => (
                      <Badge key={i} className="bg-slate-100 text-slate-600 text-xs">{t}</Badge>
                    ))}
                  </div>
                </div>
              );
            }
          } catch {
            // malformed JSON — skip
          }
          return null;
        })()}
        {(company.contact_name || company.contact_email || company.contact_phone) && (
          <div>
            <span className="text-xs text-slate-400 block mb-0.5">Contact</span>
            <div className="text-xs text-slate-700 space-y-0.5">
              {company.contact_name && <div>{company.contact_name}</div>}
              {company.contact_email && (
                <a href={`mailto:${company.contact_email}`} className="text-blue-600 hover:underline block">
                  {company.contact_email}
                </a>
              )}
              {company.contact_phone && <div>{company.contact_phone}</div>}
            </div>
          </div>
        )}
        <div className="flex gap-4">
          <div>
            <span className="text-xs text-slate-400 block">Updated</span>
            <span className="text-xs text-slate-600">{fmtDate(company.updated_at)}</span>
          </div>
          <div>
            <span className="text-xs text-slate-400 block">Created</span>
            <span className="text-xs text-slate-600">{fmtDate(company.created_at)}</span>
          </div>
        </div>
      </div>

      {/* Notes preview */}
      {company.notes.length > 0 && (
        <div className="px-4 py-3 border-b border-slate-100">
          <span className="text-xs font-medium text-slate-500 block mb-2">Notes ({company.notes.length})</span>
          <div className="space-y-2">
            {company.notes.slice(0, 2).map((n) => (
              <div key={n.id} className="text-xs text-slate-700 bg-slate-50 rounded p-2 line-clamp-3">
                {n.content}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Open full page */}
      <div className="px-4 py-3 mt-auto">
        <Link
          href={`/app/companies/${company.id}`}
          className="flex items-center justify-center gap-1.5 w-full py-2 rounded-lg bg-blue-600 text-white text-sm font-medium hover:bg-blue-700 transition-colors"
        >
          Open full profile <ChevronRight size={15} />
        </Link>
      </div>
    </aside>
  );
}
