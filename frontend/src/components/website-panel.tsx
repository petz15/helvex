"use client";

import useSWR from "swr";
import {
  Globe, Mail, MapPin, Link2, Languages, Tag, FileText,
  CheckCircle2, AlertTriangle, ExternalLink, Loader2, Hash, Users,
} from "lucide-react";

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
  languages: string[];
  description: string | null;
  service_keywords: string[];
  extraction_method: string | null;
  confidence: number | null;
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
  if (!res.ok) throw new Error("Failed to load web extract");
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

export function WebsitePanel({ companyId }: { companyId: number }) {
  const { data, error, isLoading } = useSWR(
    `web-extract-${companyId}`,
    () => fetchWebExtract(companyId),
  );

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-40 text-slate-400">
        <Loader2 size={20} className="animate-spin" />
      </div>
    );
  }

  if (error) {
    return <div className="p-6 text-sm text-red-500">Failed to load website data.</div>;
  }

  const extract = data?.extract;
  const pages = data?.pages ?? [];

  // Nothing crawled at all
  if (!extract && pages.length === 0) {
    return (
      <div className="bg-white rounded-xl border border-[#e6e8ec] p-10 text-center">
        <Globe size={28} className="text-slate-300 mx-auto mb-3" />
        <p className="text-sm text-slate-500">This company has not been crawled yet.</p>
        <p className="text-xs text-slate-400 mt-1">
          Run the web crawler to fetch and extract website data.
        </p>
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
        <div className="flex items-center gap-2 min-w-0">
          <Globe size={15} className="text-[#2563eb] shrink-0" />
          {extract?.url ? (
            <a href={extract.url} target="_blank" rel="noopener noreferrer"
               className="text-[13px] text-blue-600 hover:underline truncate flex items-center gap-1">
              {extract.url} <ExternalLink size={11} className="shrink-0" />
            </a>
          ) : (
            <span className="text-[13px] text-slate-400">No URL</span>
          )}
        </div>
        <div className="flex items-center gap-1.5">
          <span className="text-[12px] text-slate-400">Confidence</span>
          <span className={`text-[13px] font-semibold ${confColour}`}>{confPct}</span>
        </div>
        <div className="flex items-center gap-1.5">
          <span className="text-[12px] text-slate-400">Method</span>
          <span className="text-[13px] text-slate-600 font-mono">{extract?.extraction_method ?? "—"}</span>
        </div>
        <div className="flex items-center gap-1.5">
          <span className="text-[12px] text-slate-400">Pages</span>
          <span className="text-[13px] text-slate-600">{extract?.page_count ?? pages.length}</span>
        </div>
        <div className="flex items-center gap-1.5">
          <span className="text-[12px] text-slate-400">Crawled</span>
          <span className="text-[13px] text-slate-600">{fmtDate(extract?.extracted_at ?? null)}</span>
        </div>
        {(data?.candidate_count ?? 0) > 1 && (
          <span className="text-[11px] px-2 py-0.5 rounded-full bg-amber-50 text-amber-700 border border-amber-200">
            {data!.candidate_count} URL candidates extracted
          </span>
        )}
      </div>

      {!extract && (
        <div className="bg-amber-50 border border-amber-200 rounded-xl px-4 py-3 text-sm text-amber-800">
          Pages were crawled but no structured data could be extracted. See crawl coverage below.
        </div>
      )}

      <div className="grid md:grid-cols-2 gap-4">
        {/* ── Contact ── */}
        <Card title="Contact" icon={Mail}>
          <Field label="Emails" empty={!extract?.emails.length}>
            <div className="space-y-0.5">
              {extract?.emails.map((e, i) => (
                <a key={e} href={`mailto:${e}`} className="flex items-center gap-1.5 text-blue-600 hover:underline">
                  {e}
                  {i === 0 && (
                    <span className="text-[10px] px-1.5 py-px rounded-full bg-blue-50 text-blue-600 border border-blue-200">primary</span>
                  )}
                </a>
              ))}
            </div>
          </Field>
          <Field label="Phones" empty={!extract?.phones.length}>
            <div className="space-y-0.5">
              {extract?.phones.map(p => (
                <a key={p} href={`tel:${p}`} className="block text-blue-600 hover:underline">{p}</a>
              ))}
            </div>
          </Field>
          <Field label="Address" empty={!extract?.address}>
            <span className="flex items-start gap-1.5">
              <MapPin size={13} className="text-slate-400 mt-0.5 shrink-0" />
              {extract?.address}
            </span>
          </Field>
          <Field label="UID on site" empty={!extract?.uid}>
            <span className="flex items-center gap-2">
              <span className="font-mono">{extract?.uid}</span>
              {extract?.uid_matches_zefix === true && (
                <span className="inline-flex items-center gap-1 text-[11px] text-green-700">
                  <CheckCircle2 size={12} /> matches Zefix
                </span>
              )}
              {extract?.uid_matches_zefix === false && (
                <span className="inline-flex items-center gap-1 text-[11px] text-red-600">
                  <AlertTriangle size={12} /> differs from Zefix
                </span>
              )}
              {extract?.uid_matches_zefix == null && extract?.name_address_verified && (
                <span className="inline-flex items-center gap-1 text-[11px] text-green-700">
                  <CheckCircle2 size={12} /> name + address verified
                </span>
              )}
            </span>
          </Field>
        </Card>

        {/* ── Socials ── */}
        <Card title="Social profiles" icon={Link2}>
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
            <p className="text-[13px] text-slate-300">No social profiles found.</p>
          )}
        </Card>
      </div>

      {/* ── Content ── */}
      <Card title="Content" icon={FileText}>
        <Field label="Description" empty={!extract?.description}>
          <p className="leading-relaxed">{extract?.description}</p>
        </Field>
        <Field label="Languages" empty={!extract?.languages.length}>
          <span className="flex items-center gap-1.5">
            <Languages size={13} className="text-slate-400" />
            {extract?.languages.map(l => l.toUpperCase()).join(", ")}
          </span>
        </Field>
        <Field label="Keywords" empty={!extract?.service_keywords.length}>
          <div className="flex flex-wrap gap-1.5">
            {extract?.service_keywords.map(k => (
              <span key={k} className="inline-flex items-center gap-1 text-[12px] px-2 py-0.5 rounded-full bg-slate-100 text-slate-600">
                <Tag size={10} /> {k}
              </span>
            ))}
          </div>
        </Field>
        <Field label="People" empty={!extract?.persons.length}>
          <div className="flex flex-wrap gap-1.5">
            {extract?.persons.map(p => (
              <span key={p} className="inline-flex items-center gap-1 text-[12px] px-2 py-0.5 rounded-full bg-indigo-50 text-indigo-700">
                <Users size={10} /> {p}
              </span>
            ))}
          </div>
        </Field>
      </Card>

      {/* ── Crawl coverage (debug) ── */}
      <Card title="Crawl coverage" icon={Hash}>
        {pages.length === 0 ? (
          <p className="text-[13px] text-slate-300">No pages recorded.</p>
        ) : (
          <div className="overflow-x-auto -m-1 p-1">
            <table className="w-full text-[12px]">
              <thead>
                <tr className="text-slate-400 border-b border-slate-100">
                  <th className="text-left font-medium py-1.5 pr-3">Page</th>
                  <th className="text-left font-medium py-1.5 pr-3">URL</th>
                  <th className="text-right font-medium py-1.5 pr-3">HTTP</th>
                  <th className="text-left font-medium py-1.5 pr-3">Lang</th>
                  <th className="text-right font-medium py-1.5 pr-3">Words</th>
                  <th className="text-right font-medium py-1.5 pr-3">Img</th>
                  <th className="text-right font-medium py-1.5 pr-3">Vid</th>
                  <th className="text-center font-medium py-1.5 pr-3">Form</th>
                  <th className="text-center font-medium py-1.5">HTML</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-50">
                {pages.map((p, i) => (
                  <tr key={i} className="text-slate-700">
                    <td className="py-1.5 pr-3 font-medium capitalize">{p.page_type}</td>
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
    </div>
  );
}
