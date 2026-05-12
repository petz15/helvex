"use client";

import { useState, useCallback } from "react";
import Link from "next/link";
import { useParams, useSearchParams } from "next/navigation";
import useSWR from "swr";
import { Newspaper, Search, ChevronDown, ChevronRight, X, Building2 } from "lucide-react";
import { searchSogcPublications } from "@/lib/api";
import type { SogcPublicationDetail } from "@/lib/types";
import { MutationBadge, renderFtTags, MUTATION_LABELS } from "@/components/sogc-history";

const LIMIT = 50;

// ── Publication card ───────────────────────────────────────────────────────────

function PublicationCard({ pub, locale }: { pub: SogcPublicationDetail; locale: string }) {
  const [expanded, setExpanded] = useState(false);

  const text =
    (pub.detected_language === "fr" && pub.text_fr) ||
    (pub.detected_language === "it" && pub.text_it) ||
    (pub.detected_language === "en" && pub.text_en) ||
    pub.text_de ||
    "";

  const plainPreview = text
    .replace(/<FT TYPE="[^"]+">([^<]*)<\/FT>/g, "$1")
    .slice(0, 220);

  return (
    <div className="bg-white rounded-xl border border-slate-200 shadow-sm overflow-hidden">
      <div className="px-4 py-3">
        {/* Header row: date + badges + expand */}
        <div className="flex items-start gap-3">
          <div className="flex-1 min-w-0">
            <div className="flex flex-wrap items-center gap-1.5 mb-1.5">
              {pub.pub_date ? (
                <span className="text-sm font-semibold text-slate-800 tabular-nums">
                  {new Date(pub.pub_date).toLocaleDateString("de-CH", {
                    day: "2-digit",
                    month: "2-digit",
                    year: "numeric",
                  })}
                </span>
              ) : (
                <span className="text-sm font-semibold text-slate-400">—</span>
              )}
              {pub.sub_rubric && (
                <span className="text-[11px] bg-slate-100 text-slate-500 px-1.5 py-0.5 rounded font-mono">
                  {pub.sub_rubric}
                </span>
              )}
              {pub.changes.map((c, i) => (
                <MutationBadge key={i} mutKey={c.change_type} />
              ))}
            </div>

            {/* Sub-header: company + pub number + sogc id */}
            <div className="flex flex-wrap items-center gap-2">
              {pub.company_uid && (
                pub.company_id ? (
                  <Link
                    href={`/${locale}/app/companies/${pub.company_id}`}
                    className="text-blue-600 hover:underline font-mono text-[11px]"
                  >
                    {pub.company_uid}
                  </Link>
                ) : (
                  <span className="font-mono text-[11px] text-slate-500">{pub.company_uid}</span>
                )
              )}
              {pub.pub_number && (
                <span className="text-slate-400 font-mono text-[11px]">#{pub.pub_number}</span>
              )}
              {pub.sogc_id && (
                <span className="text-slate-400 text-[11px] font-mono">{pub.sogc_id}</span>
              )}
            </div>
          </div>

          {text && (
            <button
              onClick={() => setExpanded((v) => !v)}
              className="text-slate-400 hover:text-slate-600 p-1 shrink-0 transition-colors"
              title={expanded ? "Collapse" : "Show full text"}
            >
              {expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
            </button>
          )}
        </div>

        {/* Collapsed preview */}
        {!expanded && text && (
          <p className="mt-2 text-xs text-slate-500 leading-relaxed line-clamp-2">
            {plainPreview}
            {text.length > 220 ? "…" : ""}
          </p>
        )}
      </div>

      {/* Expanded full text */}
      {expanded && text && (
        <div className="border-t border-slate-100 bg-slate-50/40 px-4 py-3">
          <div className="text-xs text-slate-600 leading-relaxed">
            {renderFtTags(text)}
          </div>
        </div>
      )}
    </div>
  );
}

// ── Main component ─────────────────────────────────────────────────────────────

export function SogcClient() {
  const params = useParams();
  const searchParams = useSearchParams();
  const locale = (params?.locale as string) ?? "de";

  const [q, setQ] = useState(searchParams?.get("q") ?? "");
  const [companyUid, setCompanyUid] = useState(searchParams?.get("company_uid") ?? "");
  const [changeType, setChangeType] = useState("");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [offset, setOffset] = useState(0);

  const resetOffset = useCallback(() => setOffset(0), []);

  const hasFilters = !!(q || companyUid || changeType || dateFrom || dateTo);

  const { data: publications = [], isLoading } = useSWR(
    ["sogc-search", q, companyUid, changeType, dateFrom, dateTo, offset],
    () =>
      searchSogcPublications({
        q: q || undefined,
        company_uid: companyUid || undefined,
        change_type: changeType || undefined,
        date_from: dateFrom || undefined,
        date_to: dateTo || undefined,
        limit: LIMIT,
        offset,
      }),
    { keepPreviousData: true },
  );

  function clearFilters() {
    setQ("");
    setCompanyUid("");
    setChangeType("");
    setDateFrom("");
    setDateTo("");
    setOffset(0);
  }

  return (
    <div className="max-w-4xl mx-auto p-6 space-y-5">
      {/* Page header */}
      <div className="flex items-center gap-3">
        <Newspaper size={20} className="text-slate-400" />
        <h1 className="text-xl font-semibold text-slate-800">SHAB Publications</h1>
      </div>

      {/* Filter bar */}
      <div className="flex flex-wrap gap-2 items-center">
        <div className="relative">
          <Search size={13} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-400" />
          <input
            type="search"
            placeholder="Search content, SOGC ID…"
            value={q}
            onChange={(e) => {
              setQ(e.target.value);
              resetOffset();
            }}
            className="pl-8 pr-3 py-1.5 rounded-lg border border-slate-200 text-sm focus:outline-none focus:ring-2 focus:ring-blue-200 w-56"
          />
        </div>

        <div className="relative">
          <Building2 size={13} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-400" />
          <input
            type="text"
            placeholder="CHE-xxx.xxx.xxx"
            value={companyUid}
            onChange={(e) => {
              setCompanyUid(e.target.value);
              resetOffset();
            }}
            className="pl-8 pr-3 py-1.5 rounded-lg border border-slate-200 text-sm focus:outline-none focus:ring-2 focus:ring-blue-200 w-44"
          />
        </div>

        <select
          value={changeType}
          onChange={(e) => {
            setChangeType(e.target.value);
            resetOffset();
          }}
          className="px-3 py-1.5 rounded-lg border border-slate-200 text-sm text-slate-600 bg-white"
        >
          <option value="">All change types</option>
          {Object.entries(MUTATION_LABELS).map(([key, { label }]) => (
            <option key={key} value={key}>
              {label}
            </option>
          ))}
        </select>

        <input
          type="date"
          value={dateFrom}
          onChange={(e) => {
            setDateFrom(e.target.value);
            resetOffset();
          }}
          className="px-3 py-1.5 rounded-lg border border-slate-200 text-sm text-slate-600 focus:outline-none focus:ring-2 focus:ring-blue-200"
          title="From date"
        />
        <input
          type="date"
          value={dateTo}
          onChange={(e) => {
            setDateTo(e.target.value);
            resetOffset();
          }}
          className="px-3 py-1.5 rounded-lg border border-slate-200 text-sm text-slate-600 focus:outline-none focus:ring-2 focus:ring-blue-200"
          title="To date"
        />

        {hasFilters && (
          <button
            onClick={clearFilters}
            className="flex items-center gap-1 text-xs text-slate-500 hover:text-slate-700 px-2 py-1.5 rounded-lg hover:bg-slate-100 transition-colors"
          >
            <X size={12} /> Clear
          </button>
        )}
      </div>

      {/* Results */}
      {isLoading ? (
        <p className="text-sm text-slate-400">Loading…</p>
      ) : publications.length === 0 ? (
        <div className="text-center py-12 text-slate-400">
          <Newspaper size={32} className="mx-auto mb-3 opacity-25" />
          <p className="text-sm">No publications found.</p>
        </div>
      ) : (
        <div className="space-y-2">
          {publications.map((pub) => (
            <PublicationCard key={pub.id} pub={pub} locale={locale} />
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
          disabled={publications.length < LIMIT}
          onClick={() => setOffset(offset + LIMIT)}
          className="text-sm px-3 py-1.5 rounded border border-slate-200 disabled:opacity-40 hover:bg-slate-50"
        >
          Next
        </button>
      </div>
    </div>
  );
}
