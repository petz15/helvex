"use client";

import { useState, useCallback } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import useSWR from "swr";
import { Building2, Search, X, ChevronRight, ArrowRight } from "lucide-react";
import { searchCorporateRoles } from "@/lib/api";
import type { SogcCorporateRole } from "@/lib/types";

const LIMIT = 50;

function RoleBadge({ role }: { role: string | null }) {
  if (!role) return null;
  return (
    <span className="inline-flex items-center px-2 py-0.5 rounded text-[10px] font-medium bg-violet-50 text-violet-700 border border-violet-200">
      {role}
    </span>
  );
}

function CorporateRoleCard({ role, locale }: { role: SogcCorporateRole; locale: string }) {
  const companyHref = role.company_id ? `/${locale}/app/companies/${role.company_id}` : null;

  return (
    <div className="bg-white rounded-xl border border-slate-200 p-4 hover:border-slate-300 hover:shadow-sm transition-all">
      <div className="flex items-start justify-between gap-3">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap mb-1">
            <span className="text-sm font-semibold text-slate-800 truncate">
              {role.entity_name ?? "—"}
            </span>
            {role.entity_che && (
              <span className="text-[11px] text-slate-400 font-mono shrink-0">
                {role.entity_che}
              </span>
            )}
          </div>

          <div className="flex items-center gap-2 flex-wrap text-[11px] text-slate-500 mb-2">
            {role.entity_location && <span>{role.entity_location}</span>}
            {role.entity_legal_form && (
              <>
                <span className="text-slate-300">·</span>
                <span>{role.entity_legal_form}</span>
              </>
            )}
          </div>

          {/* Role in parent company */}
          <div className="flex items-center gap-2 flex-wrap">
            <RoleBadge role={role.role} />
            {role.role_details && (
              <span className="text-[11px] text-slate-400 truncate max-w-xs" title={role.role_details}>
                {role.role_details}
              </span>
            )}
          </div>

          {/* Parent company link */}
          {companyHref && role.company_name && (
            <div className="mt-2 flex items-center gap-1 text-[11px] text-slate-500">
              <ArrowRight size={10} className="text-slate-400" />
              <span>In</span>
              <Link href={companyHref} className="text-blue-600 hover:text-blue-800 hover:underline font-medium">
                {role.company_name}
              </Link>
            </div>
          )}
        </div>

        <div className="flex flex-col items-end gap-1 shrink-0">
          {role.pub_date && (
            <span className="text-[10px] text-slate-400">{role.pub_date}</span>
          )}
          {role.is_current === true && (
            <span className="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-medium bg-emerald-50 text-emerald-700 border border-emerald-200">
              Active
            </span>
          )}
          {role.is_current === false && (
            <span className="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-medium bg-slate-50 text-slate-500 border border-slate-200">
              Past
            </span>
          )}
          {companyHref && (
            <Link href={companyHref} className="text-slate-400 hover:text-slate-600 mt-1">
              <ChevronRight size={14} />
            </Link>
          )}
        </div>
      </div>
    </div>
  );
}

export function CorporateClient({ initialQ, initialChe }: { initialQ?: string; initialChe?: string }) {
  const params = useParams();
  const locale = (params?.locale as string) || "en";

  const [q, setQ] = useState(initialQ ?? "");
  const [che, setChe] = useState(initialChe ?? "");
  const [isCurrentOnly, setIsCurrentOnly] = useState(false);
  const [offset, setOffset] = useState(0);

  const swrKey = ["corporate-roles", q, che, isCurrentOnly, offset];
  const { data: roles, isLoading } = useSWR<SogcCorporateRole[]>(
    swrKey,
    () =>
      searchCorporateRoles({
        q: q || undefined,
        che: che || undefined,
        is_current: isCurrentOnly ? true : undefined,
        limit: LIMIT,
        offset,
      }),
    { revalidateOnFocus: false, keepPreviousData: true },
  );

  const clearQ = useCallback(() => { setQ(""); setOffset(0); }, []);
  const clearChe = useCallback(() => { setChe(""); setOffset(0); }, []);

  const hasMore = (roles?.length ?? 0) >= LIMIT;

  return (
    <div className="max-w-4xl mx-auto px-4 py-8 space-y-6">
      {/* Header */}
      <div className="flex items-center gap-3">
        <div className="w-9 h-9 rounded-lg bg-violet-100 flex items-center justify-center shrink-0">
          <Building2 size={18} className="text-violet-600" />
        </div>
        <div>
          <h1 className="text-lg font-semibold text-slate-800">Corporate Shareholders</h1>
          <p className="text-xs text-slate-500">
            Companies appearing as shareholders or officers in SOGC publications
          </p>
        </div>
      </div>

      {/* Search bar */}
      <div className="flex flex-col sm:flex-row gap-2">
        <div className="relative flex-1">
          <Search size={13} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
          <input
            type="text"
            placeholder="Search by entity name…"
            value={q}
            onChange={e => { setQ(e.target.value); setOffset(0); }}
            className="w-full pl-8 pr-8 py-2 text-sm rounded-lg border border-slate-200 bg-white focus:outline-none focus:border-slate-400"
          />
          {q && (
            <button onClick={clearQ} className="absolute right-2.5 top-1/2 -translate-y-1/2 text-slate-400 hover:text-slate-600">
              <X size={13} />
            </button>
          )}
        </div>

        <div className="relative w-full sm:w-52">
          <input
            type="text"
            placeholder="CHE-xxx.xxx.xxx"
            value={che}
            onChange={e => { setChe(e.target.value); setOffset(0); }}
            className="w-full pl-3 pr-8 py-2 text-sm rounded-lg border border-slate-200 bg-white focus:outline-none focus:border-slate-400 font-mono"
          />
          {che && (
            <button onClick={clearChe} className="absolute right-2.5 top-1/2 -translate-y-1/2 text-slate-400 hover:text-slate-600">
              <X size={13} />
            </button>
          )}
        </div>

        <label className="flex items-center gap-2 text-sm text-slate-600 cursor-pointer shrink-0 px-3 py-2 rounded-lg border border-slate-200 bg-white">
          <input
            type="checkbox"
            checked={isCurrentOnly}
            onChange={e => { setIsCurrentOnly(e.target.checked); setOffset(0); }}
            className="rounded"
          />
          Active only
        </label>
      </div>

      {/* Results */}
      {isLoading ? (
        <div className="space-y-3">
          {[...Array(6)].map((_, i) => (
            <div key={i} className="h-24 bg-slate-100 rounded-xl animate-pulse" />
          ))}
        </div>
      ) : !roles?.length ? (
        <div className="text-center py-16 text-slate-400 text-sm">
          {q || che ? "No results found." : "Enter a name or CHE number to search."}
        </div>
      ) : (
        <>
          <div className="space-y-3">
            {roles.map(r => (
              <CorporateRoleCard key={r.id} role={r} locale={locale} />
            ))}
          </div>

          <div className="flex items-center justify-between text-xs text-slate-400 pt-2">
            <span>{roles.length} result{roles.length !== 1 ? "s" : ""}</span>
            <div className="flex gap-2">
              {offset > 0 && (
                <button
                  onClick={() => setOffset(Math.max(0, offset - LIMIT))}
                  className="px-3 py-1.5 rounded-lg border border-slate-200 text-slate-600 hover:bg-slate-50"
                >
                  Previous
                </button>
              )}
              {hasMore && (
                <button
                  onClick={() => setOffset(offset + LIMIT)}
                  className="px-3 py-1.5 rounded-lg border border-slate-200 text-slate-600 hover:bg-slate-50"
                >
                  Next
                </button>
              )}
            </div>
          </div>
        </>
      )}
    </div>
  );
}
