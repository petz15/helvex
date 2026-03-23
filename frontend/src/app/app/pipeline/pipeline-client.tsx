"use client";
import { useState } from "react";
import useSWR from "swr";
import Link from "next/link";
import { ScoreBar } from "@/components/ui/score-bar";
import { Badge } from "@/components/ui/badge";
import { proposalBadgeClass } from "@/lib/utils";
import { fetchCompanies, updateCompany } from "@/lib/api";
import type { Company } from "@/lib/types";

const COLUMNS = [
  { key: "potential_proposal", label: "Potential Proposal", color: "bg-blue-50 border-blue-200", headerColor: "bg-blue-100 text-blue-800" },
  { key: "confirmed_proposal", label: "Confirmed Proposal", color: "bg-green-50 border-green-200", headerColor: "bg-green-100 text-green-800" },
  { key: "potential_generic", label: "Potential Generic", color: "bg-sky-50 border-sky-200", headerColor: "bg-sky-100 text-sky-800" },
  { key: "confirmed_generic", label: "Confirmed Generic", color: "bg-teal-50 border-teal-200", headerColor: "bg-teal-100 text-teal-800" },
  { key: "interesting", label: "Interesting", color: "bg-yellow-50 border-yellow-200", headerColor: "bg-yellow-100 text-yellow-800" },
  { key: "rejected", label: "Rejected", color: "bg-red-50 border-red-200", headerColor: "bg-red-100 text-red-700" },
];

function CompanyCard({ company, onStatusChange }: { company: Company; onStatusChange: (id: number, status: string) => void }) {
  return (
    <div className="bg-white rounded-lg border border-slate-200 p-3 shadow-sm hover:shadow-md transition-shadow">
      <Link href={`/app/companies/${company.id}`} className="block">
        <h3 className="text-sm font-medium text-slate-800 leading-snug hover:text-blue-600">{company.name}</h3>
        <p className="text-xs text-slate-400 mt-0.5">{company.canton} · {company.legal_form}</p>
      </Link>
      {company.combined_score !== null && (
        <div className="mt-2">
          <ScoreBar score={company.combined_score} />
        </div>
      )}
      {company.proposal_status && company.proposal_status !== "not_sent" && (
        <Badge className={`mt-2 text-xs ${proposalBadgeClass(company.proposal_status)}`}>
          {company.proposal_status}
        </Badge>
      )}
      {company.website_url && (
        <a href={company.website_url} target="_blank" rel="noopener noreferrer"
          className="block mt-1.5 text-xs text-blue-500 hover:underline truncate">
          {company.website_url.replace(/^https?:\/\//, "")}
        </a>
      )}
      {/* Quick-move menu */}
      <select
        value={company.review_status ?? ""}
        onChange={(e) => onStatusChange(company.id, e.target.value)}
        onClick={(e) => e.stopPropagation()}
        className="mt-2 w-full rounded border border-slate-200 px-1.5 py-1 text-xs text-slate-500 bg-slate-50 focus:outline-none focus:ring-1 focus:ring-blue-400"
      >
        {COLUMNS.map((c) => <option key={c.key} value={c.key}>{c.label}</option>)}
        <option value="rejected">Rejected</option>
      </select>
    </div>
  );
}

function KanbanColumn({ col, companies, onStatusChange }: {
  col: typeof COLUMNS[number];
  companies: Company[];
  onStatusChange: (id: number, status: string) => void;
}) {
  return (
    <div className={`flex flex-col rounded-xl border ${col.color} min-w-[240px] w-64 shrink-0`}>
      <div className={`flex items-center justify-between px-3 py-2 rounded-t-xl ${col.headerColor}`}>
        <span className="text-xs font-semibold">{col.label}</span>
        <span className="text-xs opacity-70">{companies.length}</span>
      </div>
      <div className="flex-1 overflow-y-auto p-2 space-y-2 max-h-[calc(100vh-12rem)]">
        {companies.length === 0 ? (
          <p className="text-xs text-center text-slate-400 py-6">Empty</p>
        ) : (
          companies.map((c) => (
            <CompanyCard key={c.id} company={c} onStatusChange={onStatusChange} />
          ))
        )}
      </div>
    </div>
  );
}

export function PipelineClient() {
  const [mutating, setMutating] = useState<Set<number>>(new Set());

  const { data, mutate } = useSWR(
    "pipeline",
    () => fetchCompanies({ page_size: 500, sort: "-combined_score" }),
    { keepPreviousData: true }
  );

  const companies = data?.items ?? [];

  const grouped = COLUMNS.reduce<Record<string, Company[]>>((acc, col) => {
    acc[col.key] = companies.filter((c) => c.review_status === col.key);
    return acc;
  }, {});

  async function handleStatusChange(id: number, newStatus: string) {
    if (mutating.has(id)) return;
    setMutating((s) => new Set(s).add(id));
    try {
      await updateCompany(id, { review_status: newStatus || null });
      await mutate();
    } finally {
      setMutating((s) => { const n = new Set(s); n.delete(id); return n; });
    }
  }

  return (
    <div className="flex-1 overflow-x-auto p-4">
      <div className="flex gap-4 min-w-max">
        {COLUMNS.map((col) => (
          <KanbanColumn
            key={col.key}
            col={col}
            companies={grouped[col.key] ?? []}
            onStatusChange={handleStatusChange}
          />
        ))}
      </div>
    </div>
  );
}
