"use client";

import { useState } from "react";
import useSWR from "swr";
import {
  Globe, Mail, MapPin, Link2, Languages, Tag, FileText,
  CheckCircle2, AlertTriangle, ExternalLink, Loader2, Hash, Users,
  ChevronUp, Trash2, Flag, RefreshCw, Search, Briefcase,
} from "lucide-react";
import {
  fetchAllWebExtracts, promoteWebExtract, discardWebExtract, runCompanyWebSearch,
  fetchSerpAnalysis,
  type WebExtractSummary, type SerpAnalysis, type SerpAboveItem,
} from "@/lib/api";
import { useI18n } from "@/i18n/context";

interface WebExtract {
  url_candidate_id: number;
  url: string | null;
  title: string | null;
  emails: string[];
  phones: string[];
  socials: Record<string, string>;
  address: string | null;
  uid: string | null;
  uid_matches_zefix: boolean | null;
  name_address_verified: boolean;
  persons: string[];
  persons_struct: { name: string; role: string | null }[];
  languages: string[];
  description: string | null;
  about_text: string | null;
  service_keywords: string[];
  services_struct: { title: string; summary: string }[];
  extraction_method: string | null;
  confidence: number | null;
  evidence: { dimension: string; direction: "+" | "-"; strength: string; value: unknown }[];
  identity_category: string | null;
  identity_probability: number | null;
  page_count: number | null;
  extracted_at: string | null;
}

interface WebPage {
  page_type: string;
  url: string;
  final_url: string | null;
  http_status: number | null;
  lang: string | null;
  word_count: number | null;
  image_count: number | null;
  video_count: number | null;
  has_contact_form: boolean | null;
  has_html: boolean;
  crawled: boolean;
  discovered_via: string | null;
  crawled_at: string | null;
}

interface WebExtractResponse {
  extract: WebExtract | null;
  pages: WebPage[];
  candidate_count: number;
}

async function fetchWebExtract(companyId: number): Promise<WebExtractResponse> {
  const res = await fetch(`/api/v1/companies/${companyId}/web-extract`, {
    credentials: "include",
  });
  if (!res.ok) throw new Error("Failed to load web extract");  // dev-only; UI shows failLoadData
  return res.json();
}

function fmtDate(iso: string | null): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString("de-CH", {
    day: "2-digit", month: "2-digit", year: "numeric",
    hour: "2-digit", minute: "2-digit",
  });
}

/** A labelled value row; renders "—" muted when empty so gaps are visible. */
function Field({ label, children, empty }: { label: string; children?: React.ReactNode; empty?: boolean }) {
  return (
    <div className="flex gap-3 py-1.5">
      <span className="text-[12px] text-slate-400 w-28 shrink-0 pt-0.5">{label}</span>
      <div className="text-[13px] text-slate-700 min-w-0 flex-1">
        {empty ? <span className="text-slate-300">—</span> : children}
      </div>
    </div>
  );
}

function Card({ title, icon: Icon, children }: { title: string; icon: React.ElementType; children: React.ReactNode }) {
  return (
    <div className="bg-white rounded-xl border border-[#e6e8ec] overflow-hidden">
      <div className="px-4 py-2.5 border-b border-[#eef0f3] flex items-center gap-2">
        <Icon size={14} className="text-[#2563eb]" />
        <h3 className="text-[13px] font-semibold text-slate-700">{title}</h3>
      </div>
      <div className="p-4">{children}</div>
    </div>
  );
}

const SOCIAL_LABELS: Record<string, string> = {
  linkedin: "LinkedIn", xing: "Xing", facebook: "Facebook",
  instagram: "Instagram", twitter: "Twitter / X", youtube: "YouTube",
};

function ConfBadge({ conf }: { conf: number | null }) {
  if (conf == null) return <span className="text-slate-300 text-[11px]">—</span>;
  const pct = (conf * 100).toFixed(0);
  const cls = conf >= 0.75 ? "text-green-700 bg-green-50 border-green-200"
    : conf >= 0.5 ? "text-amber-700 bg-amber-50 border-amber-200"
    : "text-red-700 bg-red-50 border-red-200";
  return <span className={`text-[11px] px-1.5 py-0.5 rounded-full border ${cls}`}>{pct}%</span>;
}

function AllExtractsPanel({ companyId, bestCandidateId, onMutate }: {
  companyId: number;
  bestCandidateId: number | undefined;
  onMutate: () => void;
}) {
  const { dict } = useI18n();
  const t = dict.app.websitePanel;
  const { data: extracts, mutate } = useSWR<WebExtractSummary[]>(
    `web-extracts-all-${companyId}`,
    () => fetchAllWebExtracts(companyId),
  );
  const [acting, setActing] = useState<number | null>(null);

  if (!extracts || extracts.length <= 1) return null;

  async function handlePromote(candidateId: number) {
    setActing(candidateId);
    try {
      await promoteWebExtract(companyId, candidateId);
      void mutate();
      onMutate();
    } finally {
      setActing(null);
    }
  }

  async function handleDiscard(candidateId: number) {
    setActing(candidateId);
    try {
      await discardWebExtract(companyId, candidateId);
      void mutate();
      onMutate();
    } finally {
      setActing(null);
    }
  }

  return (
    <Card title={t.allCandidates.replace("{count}", String(extracts.length))} icon={Globe}>
      <div className="overflow-x-auto -m-1 p-1">
        <table className="w-full text-[12px]">
          <thead>
            <tr className="text-slate-400 border-b border-slate-100">
              <th className="text-left font-medium py-1.5 pr-3">{t.colUrl}</th>
              <th className="text-center font-medium py-1.5 pr-3">{t.colConf}</th>
              <th className="text-center font-medium py-1.5 pr-3">{t.colUid}</th>
              <th className="text-left font-medium py-1.5 pr-3">{t.colStatus}</th>
              <th className="text-center font-medium py-1.5 pr-3">{t.colFlag}</th>
              <th className="py-1.5" />
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-50">
            {extracts.map(ex => {
              const isBest = ex.url_candidate_id === bestCandidateId;
              return (
                <tr key={ex.url_candidate_id} className={`${isBest ? "bg-blue-50/40" : ""} hover:bg-slate-50 transition-colors`}>
                  <td className="py-1.5 pr-3 max-w-[220px]">
                    {ex.url
                      ? <a href={ex.url} target="_blank" rel="noopener noreferrer" className="text-blue-600 hover:underline truncate block flex items-center gap-1">
                          {ex.url} <ExternalLink size={10} className="shrink-0" />
                        </a>
                      : <span className="text-slate-300">—</span>
                    }
                    {isBest && <span className="text-[10px] text-blue-600 font-medium">{t.best}</span>}
                  </td>
                  <td className="py-1.5 pr-3 text-center"><ConfBadge conf={ex.confidence} /></td>
                  <td className="py-1.5 pr-3 text-center">
                    {ex.uid_matches_zefix === true
                      ? <CheckCircle2 size={13} className="text-green-600 inline" />
                      : ex.uid_matches_zefix === false
                      ? <AlertTriangle size={13} className="text-red-500 inline" />
                      : <span className="text-slate-300 text-[11px]">—</span>
                    }
                  </td>
                  <td className="py-1.5 pr-3">
                    <span className={`text-[10px] px-1.5 py-0.5 rounded-full border ${
                      ex.candidate_status === "selected" ? "bg-blue-50 text-blue-700 border-blue-200"
                      : ex.candidate_status === "rejected" ? "bg-red-50 text-red-700 border-red-200"
                      : "bg-slate-50 text-slate-500 border-slate-200"
                    }`}>{ex.candidate_status ?? "—"}</span>
                  </td>
                  <td className="py-1.5 pr-3 text-center">
                    {ex.review_flag && (
                      <span title={ex.review_flag}>
                        <Flag size={12} className="text-amber-500 inline" />
                      </span>
                    )}
                  </td>
                  <td className="py-1.5 text-right">
                    <div className="flex items-center gap-1 justify-end">
                      {!isBest && (
                        <button
                          onClick={() => handlePromote(ex.url_candidate_id)}
                          disabled={acting !== null}
                          title={t.promoteTitle}
                          className="p-1 rounded hover:bg-green-50 text-green-600 disabled:opacity-40"
                        >
                          {acting === ex.url_candidate_id ? <Loader2 size={12} className="animate-spin" /> : <ChevronUp size={12} />}
                        </button>
                      )}
                      <button
                        onClick={() => handleDiscard(ex.url_candidate_id)}
                        disabled={acting !== null || isBest}
                        title={t.discardTitle}
                        className="p-1 rounded hover:bg-red-50 text-red-500 disabled:opacity-40"
                      >
                        <Trash2 size={12} />
                      </button>
                    </div>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </Card>
  );
}

// ── SERP Presence ─────────────────────────────────────────────────────────────

const TYPE_META: Record<SerpAboveItem["type"], { labelKey: "typeDir" | "typeSocial" | "typeNews" | "typeWeb" | "typeOther"; cls: string }> = {
  directory: { labelKey: "typeDir",    cls: "bg-orange-50 text-orange-600 border-orange-200" },
  social:    { labelKey: "typeSocial", cls: "bg-sky-50 text-sky-600 border-sky-200" },
  news:      { labelKey: "typeNews",   cls: "bg-purple-50 text-purple-600 border-purple-200" },
  own:       { labelKey: "typeWeb",    cls: "bg-slate-100 text-slate-500 border-slate-200" },
  none:      { labelKey: "typeOther",  cls: "bg-slate-100 text-slate-400 border-slate-200" },
};

function PositionBadge({ pos }: { pos: number | null }) {
  const { dict } = useI18n();
  const t = dict.app.websitePanel;
  if (pos == null) return <span className="text-[12px] text-slate-400">{t.notFound}</span>;
  const cls = pos <= 3 ? "bg-green-50 text-green-700 border-green-200"
    : pos <= 7 ? "bg-amber-50 text-amber-700 border-amber-200"
    : "bg-red-50 text-red-600 border-red-200";
  return (
    <span className={`text-[12px] font-semibold px-2 py-0.5 rounded-full border ${cls}`}>
      #{pos} {t.organic}
    </span>
  );
}

function SeoScoreBadge({ score }: { score: number | null }) {
  const { dict } = useI18n();
  const t = dict.app.websitePanel;
  if (score == null) return null;
  const cls = score >= 70 ? "bg-green-50 text-green-700 border-green-200"
    : score >= 40 ? "bg-amber-50 text-amber-700 border-amber-200"
    : "bg-red-50 text-red-600 border-red-200";
  return (
    <span
      className={`text-[12px] font-semibold px-2 py-0.5 rounded-full border ${cls}`}
      title={t.seoTitle}
    >
      {t.seoVisibility} {score}
    </span>
  );
}

function SerpPresenceCard({ companyId }: { companyId: number }) {
  const { dict } = useI18n();
  const t = dict.app.websitePanel;
  const { data, isLoading, error } = useSWR<SerpAnalysis>(
    `serp-analysis-${companyId}`,
    () => fetchSerpAnalysis(companyId),
  );

  if (isLoading) return (
    <div className="bg-white rounded-xl border border-[#e6e8ec] p-4 flex items-center gap-2 text-slate-400 text-[13px]">
      <Loader2 size={14} className="animate-spin" /> {t.loadingPresence}
    </div>
  );

  if (error || !data) return null;
  if (!data.searched_at) return null;  // no search done yet

  const pos = data.organic_position;
  const hasUrl = Boolean(data.company_url);

  return (
    <Card title={t.searchPresence} icon={Search}>
      {/* Summary row */}
      <div className="flex flex-wrap items-center gap-3 mb-4">
        {hasUrl ? (
          <PositionBadge pos={pos} />
        ) : (
          <span className="text-[12px] text-slate-400">{t.noWebsiteOnFile}</span>
        )}
        <SeoScoreBadge score={data.seo_visibility_score} />
        {data.ads_count > 0 && (
          <span className="text-[12px] px-2 py-0.5 rounded-full border bg-red-50 text-red-600 border-red-200">
            {(data.ads_count === 1 ? t.adAbove : t.adsAbove).replace("{count}", String(data.ads_count))}
          </span>
        )}
        {data.has_local_pack && (
          <span className="text-[12px] px-2 py-0.5 rounded-full border bg-indigo-50 text-indigo-700 border-indigo-200">
            {t.mapsPack.replace("{count}", String(data.local_count))}
          </span>
        )}
        {data.has_knowledge_graph && (
          <span className="text-[12px] px-2 py-0.5 rounded-full border bg-slate-100 text-slate-500 border-slate-200">
            {t.knowledgeGraph}
          </span>
        )}
        {data.search_query && (
          <span className="ml-auto text-[11px] text-slate-400 font-mono truncate max-w-[200px]" title={data.search_query}>
            q: {data.search_query}
          </span>
        )}
      </div>

      {/* SERP timeline — only when there's something interesting to show */}
      {(data.ads_count > 0 || data.has_local_pack || data.organic_above.length > 0 || pos != null) && (
        <div className="space-y-1">
          {/* Ads */}
          {data.ads.map((ad, i) => (
            <div key={i} className="flex items-center gap-2 py-1 text-[12px]">
              <span className="shrink-0 text-[10px] px-1.5 py-0.5 rounded border bg-red-50 text-red-600 border-red-200 font-medium w-14 text-center">{t.adLabel}</span>
              <a href={ad.link} target="_blank" rel="noopener noreferrer"
                 className="text-slate-500 hover:text-blue-600 truncate flex items-center gap-1">
                {ad.title || ad.link} <ExternalLink size={10} className="shrink-0 opacity-60" />
              </a>
            </div>
          ))}
          {data.ads_count > data.ads.length && (
            <div className="py-1 text-[11px] text-slate-400 pl-16">
              {(data.ads_count - data.ads.length === 1 ? t.moreAd : t.moreAds).replace("{count}", String(data.ads_count - data.ads.length))}
            </div>
          )}

          {/* Local pack */}
          {data.has_local_pack && (
            <div className="flex items-center gap-2 py-1 text-[12px]">
              <span className="shrink-0 text-[10px] px-1.5 py-0.5 rounded border bg-indigo-50 text-indigo-700 border-indigo-200 font-medium w-14 text-center">{t.mapsLabel}</span>
              <span className="text-slate-500">{(data.local_count === 1 ? t.localResult : t.localResults).replace("{count}", String(data.local_count))}</span>
            </div>
          )}

          {/* Organic results above company */}
          {data.organic_above.map(r => {
            const meta = TYPE_META[r.type] ?? TYPE_META.none;
            return (
              <div key={r.link} className="flex items-center gap-2 py-1 text-[12px]">
                <span className={`shrink-0 text-[10px] px-1.5 py-0.5 rounded border font-medium w-14 text-center ${meta.cls}`}>
                  #{r.position} {t[meta.labelKey]}
                </span>
                <a href={r.link} target="_blank" rel="noopener noreferrer"
                   className="text-slate-500 hover:text-blue-600 truncate flex items-center gap-1">
                  {r.title || r.link} <ExternalLink size={10} className="shrink-0 opacity-60" />
                </a>
              </div>
            );
          })}

          {/* Company's own website row */}
          {hasUrl && pos != null && (
            <div className="flex items-center gap-2 py-1 text-[12px] bg-green-50/60 rounded-lg px-2 -mx-2">
              <span className="shrink-0 text-[10px] px-1.5 py-0.5 rounded border bg-green-50 text-green-700 border-green-200 font-semibold w-14 text-center">
                #{pos}
              </span>
              <a href={data.company_url!} target="_blank" rel="noopener noreferrer"
                 className="text-green-700 font-medium hover:underline truncate flex items-center gap-1">
                {data.company_url} <ExternalLink size={10} className="shrink-0" />
              </a>
              <span className="shrink-0 text-[10px] text-green-600 font-medium">{t.yourSite}</span>
            </div>
          )}
          {hasUrl && pos == null && (
            <div className="py-2 text-[12px] text-slate-400">
              {t.notInOrganic.replace("{count}", String(data.total_organic))}
            </div>
          )}
        </div>
      )}
    </Card>
  );
}

const WEBSITE_STATUS_META: Record<string, { labelKey: string; cls: string; hintKey: string }> = {
  verified:       { labelKey: "statusVerified",      cls: "bg-green-50 text-green-700 border-green-200",       hintKey: "hintVerified" },
  confirmed:      { labelKey: "statusConfirmed",     cls: "bg-emerald-50 text-emerald-700 border-emerald-200", hintKey: "hintConfirmed" },
  likely:         { labelKey: "statusLikely",        cls: "bg-amber-50 text-amber-700 border-amber-200",       hintKey: "hintLikely" },
  social_only:    { labelKey: "statusSocialOnly",    cls: "bg-sky-50 text-sky-700 border-sky-200",             hintKey: "hintSocialOnly" },
  directory_only: { labelKey: "statusDirectoryOnly", cls: "bg-slate-100 text-slate-500 border-slate-200",      hintKey: "hintDirectoryOnly" },
  none:           { labelKey: "statusNone",          cls: "bg-red-50 text-red-600 border-red-200",             hintKey: "hintNone" },
};

function WebsiteStatusBadge({ status, count }: { status: string | null | undefined; count: number | null | undefined }) {
  const { dict } = useI18n();
  const t = dict.app.websitePanel;
  if (!status) return null;
  const meta = WEBSITE_STATUS_META[status];
  const label = meta ? t[meta.labelKey as keyof typeof t] : status;
  const hint = meta ? t[meta.hintKey as keyof typeof t] : "";
  const cls = meta?.cls ?? "bg-slate-100 text-slate-500 border-slate-200";
  return (
    <div className="flex items-center gap-1.5">
      <span title={hint} className={`text-[11px] px-2 py-0.5 rounded-full border ${cls}`}>{label}</span>
      {(count ?? 0) >= 2 && (
        <span title={t.distinctDetected.replace("{count}", String(count))} className="text-[11px] px-2 py-0.5 rounded-full border bg-indigo-50 text-indigo-700 border-indigo-200">
          {t.sitesCount.replace("{count}", String(count))}
        </span>
      )}
    </div>
  );
}

// Per-candidate identity category (website-pipeline holistic rework, Layer B).
// Distinct from WEBSITE_STATUS_META above, which is the company-level verdict.
const IDENTITY_CATEGORY_META: Record<string, { labelKey: string; cls: string }> = {
  MATCH_UID:    { labelKey: "identityMatchUid",    cls: "bg-green-50 text-green-700 border-green-200" },
  MATCH_STRONG: { labelKey: "identityMatchStrong", cls: "bg-emerald-50 text-emerald-700 border-emerald-200" },
  MATCH_WEAK:   { labelKey: "identityMatchWeak",   cls: "bg-amber-50 text-amber-700 border-amber-200" },
  MISMATCH:     { labelKey: "identityMismatch",    cls: "bg-red-50 text-red-600 border-red-200" },
  UNKNOWN:      { labelKey: "identityUnknown",     cls: "bg-slate-100 text-slate-500 border-slate-200" },
};

function IdentityCategoryBadge({ category }: { category: string | null | undefined }) {
  const { dict } = useI18n();
  const t = dict.app.websitePanel;
  if (!category) return null;
  const meta = IDENTITY_CATEGORY_META[category];
  const label = meta ? t[meta.labelKey as keyof typeof t] : category;
  const cls = meta?.cls ?? "bg-slate-100 text-slate-500 border-slate-200";
  return <span className={`text-[11px] px-2 py-0.5 rounded-full border ${cls}`}>{label}</span>;
}

export function WebsitePanel({ companyId, isSuperadmin = false, websiteStatus = null, websiteCount = null }: { companyId: number; isSuperadmin?: boolean; websiteStatus?: string | null; websiteCount?: number | null }) {
  const { dict } = useI18n();
  const t = dict.app.websitePanel;
  const { data, error, isLoading, mutate } = useSWR(
    `web-extract-${companyId}`,
    () => fetchWebExtract(companyId),
  );
  const [rerunning, setRerunning] = useState(false);

  async function handleRerunSearch() {
    setRerunning(true);
    try {
      await runCompanyWebSearch(companyId);
      await mutate();
    } finally {
      setRerunning(false);
    }
  }

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-40 text-slate-400">
        <Loader2 size={20} className="animate-spin" />
      </div>
    );
  }

  if (error) {
    return <div className="p-6 text-sm text-red-500">{t.failLoadData}</div>;
  }

  const extract = data?.extract;
  const pages = data?.pages ?? [];

  // Nothing crawled at all
  if (!extract && pages.length === 0) {
    return (
      <div className="space-y-4">
        <SerpPresenceCard companyId={companyId} />
        <div className="bg-white rounded-xl border border-[#e6e8ec] p-10 text-center">
        <Globe size={28} className="text-slate-300 mx-auto mb-3" />
        {websiteStatus && (
          <div className="flex justify-center mb-3"><WebsiteStatusBadge status={websiteStatus} count={websiteCount} /></div>
        )}
        <p className="text-sm text-slate-500">{t.notCrawled}</p>
        <p className="text-xs text-slate-400 mt-1">
          {t.runCrawler}
        </p>
        {isSuperadmin && (
          <button
            onClick={handleRerunSearch}
            disabled={rerunning}
            className="mt-4 inline-flex items-center gap-1.5 text-[12px] px-3 py-1.5 rounded-lg border border-purple-200 text-purple-700 hover:bg-purple-50 disabled:opacity-50 transition-colors"
          >
            {rerunning ? <Loader2 size={12} className="animate-spin" /> : <RefreshCw size={12} />}
            {rerunning ? t.searching : t.rerunWebSearch}
          </button>
        )}
        </div>
      </div>
    );
  }

  const conf = extract?.confidence;
  const confPct = conf != null ? `${(conf * 100).toFixed(0)}%` : "—";
  const confColour =
    conf == null ? "text-slate-400"
    : conf >= 0.75 ? "text-green-600"
    : conf >= 0.5 ? "text-amber-600"
    : "text-red-500";

  return (
    <div className="space-y-4">
      {/* ── Source strip ── */}
      <div className="bg-white rounded-xl border border-[#e6e8ec] px-4 py-3 flex flex-wrap items-center gap-x-6 gap-y-2">
        <WebsiteStatusBadge status={websiteStatus} count={websiteCount} />
        <IdentityCategoryBadge category={extract?.identity_category} />
        <div className="flex items-center gap-2 min-w-0">
          <Globe size={15} className="text-[#2563eb] shrink-0" />
          {extract?.url ? (
            <a href={extract.url} target="_blank" rel="noopener noreferrer"
               className="text-[13px] text-blue-600 hover:underline truncate flex items-center gap-1">
              {extract.url} <ExternalLink size={11} className="shrink-0" />
            </a>
          ) : (
            <span className="text-[13px] text-slate-400">{t.noUrl}</span>
          )}
        </div>
        <div className="flex items-center gap-1.5">
          <span className="text-[12px] text-slate-400">{t.confidence}</span>
          <span className={`text-[13px] font-semibold ${confColour}`}>{confPct}</span>
        </div>
        <div className="flex items-center gap-1.5">
          <span className="text-[12px] text-slate-400">{t.method}</span>
          <span className="text-[13px] text-slate-600 font-mono">{extract?.extraction_method ?? "—"}</span>
        </div>
        <div className="flex items-center gap-1.5">
          <span className="text-[12px] text-slate-400">{t.pages}</span>
          <span className="text-[13px] text-slate-600">{extract?.page_count ?? pages.length}</span>
        </div>
        <div className="flex items-center gap-1.5">
          <span className="text-[12px] text-slate-400">{t.crawled}</span>
          <span className="text-[13px] text-slate-600">{fmtDate(extract?.extracted_at ?? null)}</span>
        </div>
        {(data?.candidate_count ?? 0) > 1 && (
          <span className="text-[11px] px-2 py-0.5 rounded-full bg-amber-50 text-amber-700 border border-amber-200">
            {t.candidatesExtracted.replace("{count}", String(data!.candidate_count))}
          </span>
        )}
        {isSuperadmin && (
          <button
            onClick={handleRerunSearch}
            disabled={rerunning}
            className="ml-auto flex items-center gap-1.5 text-[12px] px-2.5 py-1 rounded-lg border border-purple-200 text-purple-700 hover:bg-purple-50 disabled:opacity-50 transition-colors"
          >
            {rerunning ? <Loader2 size={12} className="animate-spin" /> : <RefreshCw size={12} />}
            {rerunning ? t.searching : t.rerunSearch}
          </button>
        )}
      </div>

      {!extract && (
        <div className="bg-amber-50 border border-amber-200 rounded-xl px-4 py-3 text-sm text-amber-800">
          {t.noStructuredData}
        </div>
      )}

      <SerpPresenceCard companyId={companyId} />

      <div className="grid md:grid-cols-2 gap-4">
        {/* ── Contact ── */}
        <Card title={t.contact} icon={Mail}>
          <Field label={t.emails} empty={!extract?.emails.length}>
            <div className="space-y-0.5">
              {extract?.emails.map((e, i) => (
                <a key={e} href={`mailto:${e}`} className="flex items-center gap-1.5 text-blue-600 hover:underline">
                  {e}
                  {i === 0 && (
                    <span className="text-[10px] px-1.5 py-px rounded-full bg-blue-50 text-blue-600 border border-blue-200">{t.primary}</span>
                  )}
                </a>
              ))}
            </div>
          </Field>
          <Field label={t.phones} empty={!extract?.phones.length}>
            <div className="space-y-0.5">
              {extract?.phones.map(p => (
                <a key={p} href={`tel:${p}`} className="block text-blue-600 hover:underline">{p}</a>
              ))}
            </div>
          </Field>
          <Field label={t.address} empty={!extract?.address}>
            <span className="flex items-start gap-1.5">
              <MapPin size={13} className="text-slate-400 mt-0.5 shrink-0" />
              {extract?.address}
            </span>
          </Field>
          <Field label={t.uidOnSite} empty={!extract?.uid}>
            <span className="flex items-center gap-2">
              <span className="font-mono">{extract?.uid}</span>
              {extract?.uid_matches_zefix === true && (
                <span className="inline-flex items-center gap-1 text-[11px] text-green-700">
                  <CheckCircle2 size={12} /> {t.matchesZefix}
                </span>
              )}
              {extract?.uid_matches_zefix === false && (
                <span className="inline-flex items-center gap-1 text-[11px] text-red-600">
                  <AlertTriangle size={12} /> {t.differsZefix}
                </span>
              )}
              {extract?.uid_matches_zefix == null && extract?.name_address_verified && (
                <span className="inline-flex items-center gap-1 text-[11px] text-green-700">
                  <CheckCircle2 size={12} /> {t.nameAddrVerified}
                </span>
              )}
            </span>
          </Field>
        </Card>

        {/* ── Socials ── */}
        <Card title={t.socialProfiles} icon={Link2}>
          {extract && Object.keys(extract.socials).length > 0 ? (
            <div className="space-y-1.5">
              {Object.entries(extract.socials).map(([platform, url]) => (
                <a key={platform} href={url} target="_blank" rel="noopener noreferrer"
                   className="flex items-center gap-2 text-[13px] text-blue-600 hover:underline">
                  <span className="text-slate-500 w-20 shrink-0">{SOCIAL_LABELS[platform] ?? platform}</span>
                  <span className="truncate">{url}</span>
                  <ExternalLink size={11} className="shrink-0" />
                </a>
              ))}
            </div>
          ) : (
            <p className="text-[13px] text-slate-300">{t.noSocials}</p>
          )}
        </Card>
      </div>

      {/* ── Content ── */}
      <Card title={t.content} icon={FileText}>
        <Field label={t.description} empty={!extract?.description}>
          <p className="leading-relaxed">{extract?.description}</p>
        </Field>
        <Field label={t.aboutText} empty={!extract?.about_text || extract.about_text === extract.description}>
          <p className="leading-relaxed text-slate-600">{extract?.about_text}</p>
        </Field>
        <Field label={t.languages} empty={!extract?.languages.length}>
          <span className="flex items-center gap-1.5">
            <Languages size={13} className="text-slate-400" />
            {extract?.languages.map(l => l.toUpperCase()).join(", ")}
          </span>
        </Field>
        <Field label={t.keywords} empty={!extract?.service_keywords.length}>
          <div className="flex flex-wrap gap-1.5">
            {extract?.service_keywords.map(k => (
              <span key={k} className="inline-flex items-center gap-1 text-[12px] px-2 py-0.5 rounded-full bg-slate-100 text-slate-600">
                <Tag size={10} /> {k}
              </span>
            ))}
          </div>
        </Field>
        <Field label={t.servicesFound} empty={!extract?.services_struct.length}>
          <div className="space-y-2">
            {extract?.services_struct.map(s => (
              <div key={s.title} className="border-l-2 border-slate-200 pl-2.5">
                <div className="flex items-center gap-1.5 font-medium text-slate-700">
                  <Briefcase size={11} className="text-slate-400" /> {s.title}
                </div>
                <p className="text-[12px] text-slate-500 leading-snug">{s.summary}</p>
              </div>
            ))}
          </div>
        </Field>
        <Field label={t.people} empty={!extract?.persons.length && !extract?.persons_struct.length}>
          {extract && extract.persons_struct.length > 0 ? (
            <div className="flex flex-wrap gap-1.5">
              {extract.persons_struct.map(p => (
                <span key={p.name} className="inline-flex items-center gap-1.5 text-[12px] px-2 py-0.5 rounded-full bg-indigo-50 text-indigo-700">
                  <Users size={10} />
                  {p.name}
                  {p.role && <span className="text-indigo-400">· {p.role}</span>}
                </span>
              ))}
            </div>
          ) : (
            <div className="flex flex-wrap gap-1.5">
              {extract?.persons.map(p => (
                <span key={p} className="inline-flex items-center gap-1 text-[12px] px-2 py-0.5 rounded-full bg-indigo-50 text-indigo-700">
                  <Users size={10} /> {p}
                </span>
              ))}
            </div>
          )}
        </Field>
      </Card>

      {/* ── Crawl coverage (debug) ── */}
      <Card title={t.crawlCoverage} icon={Hash}>
        {pages.length === 0 ? (
          <p className="text-[13px] text-slate-300">{t.noPages}</p>
        ) : (
          <div className="overflow-x-auto -m-1 p-1">
            <table className="w-full text-[12px]">
              <thead>
                <tr className="text-slate-400 border-b border-slate-100">
                  <th className="text-left font-medium py-1.5 pr-3">{t.colPage}</th>
                  <th className="text-left font-medium py-1.5 pr-3">{t.colStatus}</th>
                  <th className="text-left font-medium py-1.5 pr-3">{t.colUrl}</th>
                  <th className="text-right font-medium py-1.5 pr-3">{t.colHttp}</th>
                  <th className="text-left font-medium py-1.5 pr-3">{t.colLang}</th>
                  <th className="text-right font-medium py-1.5 pr-3">{t.colWords}</th>
                  <th className="text-right font-medium py-1.5 pr-3">{t.colImg}</th>
                  <th className="text-right font-medium py-1.5 pr-3">{t.colVid}</th>
                  <th className="text-center font-medium py-1.5 pr-3">{t.colForm}</th>
                  <th className="text-center font-medium py-1.5">{t.colHtml}</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-50">
                {pages.map((p, i) => (
                  <tr key={i} className={p.crawled ? "text-slate-700" : "text-slate-400"}>
                    <td className="py-1.5 pr-3 font-medium capitalize">{p.page_type}</td>
                    <td className="py-1.5 pr-3">
                      <span className={
                        "inline-flex items-center gap-1 text-[11px] px-1.5 py-0.5 rounded-full border "
                        + (p.crawled ? "bg-emerald-50 text-emerald-700 border-emerald-200" : "bg-slate-50 text-slate-500 border-slate-200")
                      }>
                        {p.crawled ? t.statusCrawled : t.statusInventory}
                      </span>
                    </td>
                    <td className="py-1.5 pr-3 max-w-[220px]">
                      <a href={p.final_url ?? p.url} target="_blank" rel="noopener noreferrer"
                         className="text-blue-600 hover:underline truncate block">{p.url}</a>
                    </td>
                    <td className="py-1.5 pr-3 text-right font-mono">
                      <span className={p.http_status && p.http_status >= 400 ? "text-red-500" : ""}>
                        {p.http_status ?? "—"}
                      </span>
                    </td>
                    <td className="py-1.5 pr-3">{p.lang?.toUpperCase() ?? "—"}</td>
                    <td className="py-1.5 pr-3 text-right tabular-nums">{p.word_count?.toLocaleString() ?? "—"}</td>
                    <td className="py-1.5 pr-3 text-right tabular-nums">{p.image_count ?? "—"}</td>
                    <td className="py-1.5 pr-3 text-right tabular-nums">{p.video_count ?? "—"}</td>
                    <td className="py-1.5 pr-3 text-center">{p.has_contact_form ? "✓" : "—"}</td>
                    <td className="py-1.5 text-center">{p.has_html ? "✓" : "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {/* ── Multi-candidate comparison ── */}
      <AllExtractsPanel
        companyId={companyId}
        bestCandidateId={extract?.url_candidate_id}
        onMutate={() => void mutate()}
      />
    </div>
  );
}
