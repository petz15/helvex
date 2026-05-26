"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import useSWR from "swr";
import { Building2, ArrowRight, ExternalLink } from "lucide-react";
import { fetchCompanyCorporateRoles } from "@/lib/api";
import type { SogcCorporateRole } from "@/lib/types";

function RoleBadge({ role, is_current }: { role: string | null; is_current: boolean | null }) {
  if (!role) return null;
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded text-[10px] font-medium border ${
      is_current === false
        ? "bg-slate-50 text-slate-500 border-slate-200"
        : "bg-violet-50 text-violet-700 border-violet-200"
    }`}>
      {role}
    </span>
  );
}

export function CorporateShareholdersPanel({ companyUid }: { companyUid: string }) {
  const params = useParams();
  const locale = (params?.locale as string) || "en";

  const { data: roles, isLoading } = useSWR<SogcCorporateRole[]>(
    companyUid ? ["corporate-roles", companyUid] : null,
    () => fetchCompanyCorporateRoles(companyUid),
    { revalidateOnFocus: false },
  );

  if (isLoading) return null;
  if (!roles || roles.length === 0) return null;

  // Separate role appointments from capital-only changes
  const roleRecords = roles.filter(r => r.change_subtype !== "capital_change");
  const capitalRecords = roles.filter(r => r.change_subtype === "capital_change");

  const currentRoles = roleRecords.filter(r => r.is_current !== false);
  const pastRoles = roleRecords.filter(r => r.is_current === false);
  const displayedRoles = currentRoles.length > 0 ? currentRoles : pastRoles.slice(0, 5);

  if (displayedRoles.length === 0 && capitalRecords.length === 0) return null;

  return (
    <div className="bg-white rounded-xl border border-slate-200 p-5 shadow-sm">
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-2">
          <Building2 size={15} className="text-violet-500" />
          <h2 className="text-sm font-semibold text-slate-800">
            Corporate Shareholders
            <span className="ml-2 text-[11px] font-normal text-slate-400">
              ({roleRecords.length} role{roleRecords.length !== 1 ? "s" : ""}{capitalRecords.length > 0 ? ` · ${capitalRecords.length} capital change${capitalRecords.length !== 1 ? "s" : ""}` : ""})
            </span>
          </h2>
        </div>
        <Link
          href={`/${locale}/app/corporate?company_uid=${companyUid}`}
          className="text-[11px] text-blue-600 hover:text-blue-800 transition-colors flex items-center gap-1"
        >
          All <ExternalLink size={10} />
        </Link>
      </div>

      {/* Role appointments */}
      {displayedRoles.length > 0 && (
        <div className="space-y-2">
          {displayedRoles.map(r => (
            <div
              key={r.id}
              className={`flex items-start justify-between gap-3 rounded-lg px-3 py-2.5 ${
                r.is_current === false ? "bg-slate-50/60 opacity-70" : "bg-violet-50/40"
              }`}
            >
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2 flex-wrap mb-0.5">
                  <span className="text-sm font-medium text-slate-800 truncate">
                    {r.entity_name ?? "—"}
                  </span>
                  {r.entity_che && (
                    <span className="text-[10px] text-slate-400 font-mono shrink-0">{r.entity_che}</span>
                  )}
                </div>
                <div className="flex items-center gap-2 flex-wrap">
                  <RoleBadge role={r.role} is_current={r.is_current} />
                  {r.entity_location && (
                    <span className="text-[10px] text-slate-400">{r.entity_location}</span>
                  )}
                </div>
              </div>
              <div className="shrink-0 text-[10px] text-slate-400">{r.pub_date ?? ""}</div>
            </div>
          ))}
          {pastRoles.length > 0 && currentRoles.length > 0 && (
            <p className="text-[10px] text-slate-400">
              +{pastRoles.length} past record{pastRoles.length !== 1 ? "s" : ""}
            </p>
          )}
        </div>
      )}

      {/* Capital changes — shown as a collapsible subsection */}
      {capitalRecords.length > 0 && (
        <div className={displayedRoles.length > 0 ? "mt-3 pt-3 border-t border-slate-100" : ""}>
          <p className="text-[10px] font-semibold text-slate-400 uppercase tracking-wide mb-2">
            Capital changes
          </p>
          <div className="space-y-1.5">
            {capitalRecords.slice(0, 5).map(r => (
              <div key={r.id} className="flex items-start justify-between gap-3 rounded-lg px-3 py-2 bg-amber-50/40">
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className="text-xs font-medium text-slate-700 truncate">{r.entity_name ?? "—"}</span>
                    {r.entity_che && <span className="text-[10px] text-slate-400 font-mono">{r.entity_che}</span>}
                  </div>
                  {r.role_details && (
                    <p className="text-[10px] text-slate-500 truncate mt-0.5" title={r.role_details}>{r.role_details}</p>
                  )}
                </div>
                <div className="shrink-0 text-[10px] text-slate-400">{r.pub_date ?? ""}</div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
