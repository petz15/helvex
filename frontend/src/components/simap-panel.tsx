"use client";

import useSWR from "swr";
import { Building2, Award, ChevronDown, ChevronUp, ExternalLink } from "lucide-react";
import { useState } from "react";

interface SimapAward {
  id: number;
  publication_date: string | null;
  award_decision_date: string | null;
  best_title: string;
  best_proc_office: string;
  pub_type: string | null;
  process_type: string | null;
  project_type: string | null;
  project_subtype: string | null;
  cpv_code: string | null;
  number_of_submissions: number | null;
  lot_number: number | null;
  project_number: string | null;
  publication_number: string | null;
  simap_project_id: string | null;
  simap_publication_id: string | null;
  price: number | null;
  price_currency: string | null;
  total_price_selection: string | null;
  total_price_range_min: number | null;
  total_price_range_max: number | null;
  total_price_currency: string | null;
}

async function fetchSimapAwards(companyId: number): Promise<SimapAward[]> {
  const res = await fetch(`/api/v1/companies/${companyId}/simap-awards`, {
    credentials: "include",
  });
  if (!res.ok) return [];
  return res.json();
}

function formatPrice(award: SimapAward): string | null {
  if (award.price != null && award.price_currency) {
    return `${award.price_currency.toUpperCase()} ${award.price.toLocaleString("de-CH", { minimumFractionDigits: 0, maximumFractionDigits: 0 })}`;
  }
  if (
    award.total_price_selection === "price_range_of_selected_offers" &&
    award.total_price_range_min != null &&
    award.total_price_range_max != null &&
    award.total_price_currency
  ) {
    const cur = award.total_price_currency.toUpperCase();
    const min = award.total_price_range_min.toLocaleString("de-CH", { minimumFractionDigits: 0, maximumFractionDigits: 0 });
    const max = award.total_price_range_max.toLocaleString("de-CH", { minimumFractionDigits: 0, maximumFractionDigits: 0 });
    return `${cur} ${min} – ${max}`;
  }
  return null;
}

function AwardCard({ award }: { award: SimapAward }) {
  const priceStr = formatPrice(award);
  const pubDate = award.publication_date
    ? new Date(award.publication_date).toLocaleDateString("de-CH")
    : null;

  return (
    <div className="border border-slate-100 rounded-lg p-3 bg-slate-50 space-y-1.5">
      <div className="flex items-start justify-between gap-2">
        <p className="text-sm font-medium text-slate-800 leading-snug line-clamp-2">
          {award.best_title || `Project ${award.project_number}`}
        </p>
        <div className="flex items-center gap-2 shrink-0">
          {pubDate && (
            <span className="text-xs text-slate-400 whitespace-nowrap">{pubDate}</span>
          )}
          {award.simap_project_id && (
            <a
              href={`https://www.simap.ch/de/project-detail/${award.simap_project_id}`}
              target="_blank"
              rel="noopener noreferrer"
              className="text-slate-400 hover:text-blue-600 transition-colors"
              title="View on simap.ch"
            >
              <ExternalLink size={12} />
            </a>
          )}
        </div>
      </div>

      {award.best_proc_office && (
        <p className="text-xs text-slate-500 flex items-center gap-1">
          <Building2 size={11} className="shrink-0 text-slate-400" />
          {award.best_proc_office}
        </p>
      )}

      <div className="flex flex-wrap gap-x-3 gap-y-1 mt-1">
        {priceStr && (
          <span className="text-xs font-semibold text-emerald-700">{priceStr}</span>
        )}
        {award.number_of_submissions != null && (
          <span className="text-xs text-slate-400">
            {award.number_of_submissions} bid{award.number_of_submissions !== 1 ? "s" : ""}
          </span>
        )}
        {award.cpv_code && (
          <span className="text-xs text-slate-400">CPV {award.cpv_code}</span>
        )}
        {award.lot_number != null && (
          <span className="text-xs text-slate-400">Lot {award.lot_number}</span>
        )}
        {award.pub_type === "direct_award" && (
          <span className="text-xs bg-amber-50 text-amber-700 border border-amber-200 rounded px-1.5 py-0.5">
            Direct award
          </span>
        )}
      </div>
    </div>
  );
}

const INITIAL_SHOW = 3;

export function SimapPanel({ companyId }: { companyId: number }) {
  const [expanded, setExpanded] = useState(false);

  const { data: awards = [] } = useSWR(
    `simap-awards-${companyId}`,
    () => fetchSimapAwards(companyId),
    { revalidateOnFocus: false },
  );

  if (awards.length === 0) return null;

  const visible = expanded ? awards : awards.slice(0, INITIAL_SHOW);
  const hasMore = awards.length > INITIAL_SHOW;

  return (
    <div className="bg-white rounded-xl border border-slate-200 p-5 shadow-sm">
      <h2 className="text-sm font-semibold text-slate-700 flex items-center gap-1.5 mb-4">
        <Award size={15} className="text-slate-400" />
        Public Contracts
        <span className="ml-auto text-xs font-normal text-slate-400">{awards.length} award{awards.length !== 1 ? "s" : ""}</span>
      </h2>

      <div className="space-y-2">
        {visible.map((a) => (
          <AwardCard key={a.id} award={a} />
        ))}
      </div>

      {hasMore && (
        <button
          onClick={() => setExpanded((e) => !e)}
          className="mt-3 flex items-center gap-1 text-xs text-slate-500 hover:text-slate-700 transition-colors"
        >
          {expanded ? (
            <>
              <ChevronUp size={13} /> Show fewer
            </>
          ) : (
            <>
              <ChevronDown size={13} /> Show {awards.length - INITIAL_SHOW} more
            </>
          )}
        </button>
      )}
    </div>
  );
}
