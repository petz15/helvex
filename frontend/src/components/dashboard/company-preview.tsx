"use client";
import { X, ExternalLink, ChevronRight } from "lucide-react";
import Link from "next/link";
import { ScoreBar } from "@/components/ui/score-bar";
import { Badge } from "@/components/ui/badge";
import { reviewBadgeClass, proposalBadgeClass, fmtDate } from "@/lib/utils";
import type { Company } from "@/lib/types";

interface CompanyPreviewProps {
  company: Company | null;
  onClose: () => void;
}

export function CompanyPreview({ company, onClose }: CompanyPreviewProps) {
  if (!company) return null;

  return (
    <aside className="w-80 shrink-0 flex flex-col bg-white border-l border-slate-200 overflow-y-auto">
      {/* Header */}
      <div className="flex items-start justify-between px-4 py-3 border-b border-slate-100">
        <div className="flex-1 min-w-0">
          <h2 className="font-semibold text-slate-800 text-sm leading-snug">{company.name}</h2>
          <p className="text-xs text-slate-500 mt-0.5">{company.canton} · {company.legal_form}</p>
        </div>
        <button onClick={onClose} className="ml-2 text-slate-400 hover:text-slate-600 mt-0.5">
          <X size={16} />
        </button>
      </div>

      {/* Badges */}
      <div className="px-4 py-2 flex flex-wrap gap-1.5 border-b border-slate-100">
        <Badge className={reviewBadgeClass(company.review_status)}>
          {company.review_status?.replace(/_/g, " ") ?? "Pending"}
        </Badge>
        {company.proposal_status && company.proposal_status !== "not_sent" && (
          <Badge className={proposalBadgeClass(company.proposal_status)}>
            {company.proposal_status}
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
          <span className="text-slate-500 w-16">Google</span>
          <ScoreBar score={company.website_match_score} className="flex-1" />
        </div>
        <div className="flex items-center justify-between text-xs">
          <span className="text-slate-500 w-16">Claude</span>
          <ScoreBar score={company.claude_score} className="flex-1" />
        </div>
        <div className="flex items-center justify-between text-xs">
          <span className="text-slate-500 w-16">Zefix</span>
          <ScoreBar score={company.zefix_score} className="flex-1" />
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
        {company.address && (
          <div>
            <span className="text-xs text-slate-400 block mb-0.5">Address</span>
            <span className="text-xs text-slate-700">{company.address}</span>
          </div>
        )}
        {company.purpose && (
          <div>
            <span className="text-xs text-slate-400 block mb-0.5">Purpose</span>
            <p className="text-xs text-slate-700 line-clamp-4">{company.purpose}</p>
          </div>
        )}
        {company.claude_category && (
          <div>
            <span className="text-xs text-slate-400 block mb-0.5">Claude category</span>
            <span className="text-xs text-slate-700">{company.claude_category}</span>
          </div>
        )}
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
