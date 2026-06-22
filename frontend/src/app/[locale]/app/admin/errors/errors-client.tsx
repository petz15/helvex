"use client";
import { useState, useCallback } from "react";
import useSWR from "swr";
import {
  AlertTriangle, RefreshCw, Loader2, CheckCircle2, XCircle,
  Globe, Brain, Database, MapPin, Flag, ChevronLeft, ChevronRight,
  ChevronDown, ChevronUp, EyeOff, Check,
} from "lucide-react";
import {
  fetchDataQualitySummary,
  fetchPipelineErrors,
  fetchJobFailures,
  fetchAdminCrawlerFailures,
  resolveAdminError,
  ignoreAdminError,
  correctCompanyData,
  backfillCrawlerErrors,
  type DataQualitySummary,
  type PipelineError,
  type JobFailure,
  type AdminCrawlerFailure,
  type CompanyCorrection,
} from "@/lib/api";

// ── Quality card ────────────────────────────────────────────────────────────

function qualityColour(pct: number) {
  if (pct >= 80) return "text-green-700 bg-green-50 border-green-200";
  if (pct >= 50) return "text-amber-700 bg-amber-50 border-amber-200";
  return "text-red-700 bg-red-50 border-red-200";
}

function countColour(n: number, warnAt: number, redAt: number) {
  if (n === 0) return "text-green-700 bg-green-50 border-green-200";
  if (n < redAt) return "text-amber-700 bg-amber-50 border-amber-200";
  return "text-red-700 bg-red-50 border-red-200";
}

function QualityCard({
  label, value, sub, icon: Icon, colour,
}: { label: string; value: string | number; sub?: string; icon: React.ElementType; colour: string }) {
  return (
    <div className={`rounded-xl border p-4 flex items-start gap-3 ${colour}`}>
      <div className="mt-0.5">
        <Icon size={16} className="text-current opacity-70" />
      </div>
      <div className="min-w-0">
        <p className="text-xs font-medium opacity-70 truncate">{label}</p>
        <p className="text-2xl font-bold tabular-nums leading-tight">{value}</p>
        {sub && <p className="text-xs opacity-60 mt-0.5">{sub}</p>}
      </div>
    </div>
  );
}

// ── Tabs ────────────────────────────────────────────────────────────────────

type Tab = "pipeline" | "crawler" | "jobs";

function TabBtn({ active, onClick, children }: { active: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button
      onClick={onClick}
      className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors ${
        active
          ? "border-purple-600 text-purple-700"
          : "border-transparent text-slate-500 hover:text-slate-700 hover:border-slate-300"
      }`}
    >
      {children}
    </button>
  );
}

// ── Source badge ─────────────────────────────────────────────────────────────

const SOURCE_COLOUR: Record<string, string> = {
  web_enrichment: "bg-blue-100 text-blue-800",
  zefix_import: "bg-purple-100 text-purple-800",
  geocoding: "bg-teal-100 text-teal-800",
  noga: "bg-orange-100 text-orange-800",
  crawler: "bg-red-100 text-red-800",
};

function SourceBadge({ source }: { source: string }) {
  return (
    <span className={`inline-block px-2 py-0.5 rounded-full text-xs font-medium ${SOURCE_COLOUR[source] ?? "bg-slate-100 text-slate-600"}`}>
      {source.replace("_", " ")}
    </span>
  );
}

// ── Inline correction panel ──────────────────────────────────────────────────

function CorrectionPanel({
  error,
  onSaved,
  onCancel,
}: {
  error: PipelineError;
  onSaved: () => void;
  onCancel: () => void;
}) {
  const [form, setForm] = useState<CompanyCorrection>({});
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const field = (key: keyof CompanyCorrection, label: string, hint?: string, multiline?: boolean) => (
    <div>
      <label className="block text-xs font-medium text-slate-600 mb-1">{label}</label>
      {multiline ? (
        <textarea
          rows={3}
          className="w-full text-sm border border-slate-300 rounded-lg px-3 py-2 focus:outline-none focus:ring-2 focus:ring-purple-400 resize-none"
          placeholder={hint}
          value={(form[key] as string) ?? ""}
          onChange={(e) => setForm((f) => ({ ...f, [key]: e.target.value }))}
        />
      ) : (
        <input
          type={key === "ai_score" ? "number" : "text"}
          className="w-full text-sm border border-slate-300 rounded-lg px-3 py-2 focus:outline-none focus:ring-2 focus:ring-purple-400"
          placeholder={hint}
          value={(form[key] as string | number) ?? ""}
          onChange={(e) => setForm((f) => ({ ...f, [key]: key === "ai_score" ? Number(e.target.value) : e.target.value }))}
        />
      )}
    </div>
  );

  const handleSave = async () => {
    if (!error.company_id) return;
    setSaving(true);
    setErr(null);
    try {
      const payload: CompanyCorrection = {};
      for (const [k, v] of Object.entries(form)) {
        if (v !== "" && v !== undefined) payload[k as keyof CompanyCorrection] = v as never;
      }
      await correctCompanyData(error.company_id, payload);
      await resolveAdminError(error.id);
      onSaved();
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : "Save failed");
    } finally {
      setSaving(false);
    }
  };

  const sourceFields: Record<string, React.ReactNode> = {
    web_enrichment: (
      <>
        {field("website_url", "Website URL", "https://example.ch")}
      </>
    ),
    zefix_import: (
      <>
        {field("purpose", "Company purpose", "Beratung und Handel...", true)}
        <div className="grid grid-cols-2 gap-3">
          {field("address_city", "City", "Zürich")}
          {field("address_zip", "ZIP", "8001")}
        </div>
      </>
    ),
    geocoding: (
      <div className="grid grid-cols-2 gap-3">
        {field("address_city", "City", "Zürich")}
        {field("address_zip", "ZIP", "8001")}
      </div>
    ),
    noga: (
      <>
        {field("noga_code", "NOGA code", "47.11")}
        {field("noga_label", "NOGA label", "Einzelhandel...")}
      </>
    ),
  };

  return (
    <div className="mt-2 p-4 bg-slate-50 border border-slate-200 rounded-xl space-y-3">
      <p className="text-xs font-semibold text-slate-500 uppercase tracking-wider">Data Correction</p>
      {sourceFields[error.error_source] ?? (
        <div className="grid grid-cols-2 gap-3">
          {field("website_url", "Website URL", "https://...")}
          {field("noga_code", "NOGA code", "47.11")}
        </div>
      )}
      <div className="grid grid-cols-2 gap-3">
        {field("ai_category", "AI category", "Software / SaaS")}
        {field("ai_score", "AI score (0–100)", "75")}
      </div>
      {err && <p className="text-xs text-red-600">{err}</p>}
      <div className="flex gap-2 pt-1">
        <button
          onClick={handleSave}
          disabled={saving}
          className="flex items-center gap-1.5 px-3 py-1.5 bg-purple-600 hover:bg-purple-700 text-white text-sm rounded-lg disabled:opacity-50 transition-colors"
        >
          {saving ? <Loader2 size={13} className="animate-spin" /> : <Check size={13} />}
          Save & Resolve
        </button>
        <button
          onClick={onCancel}
          className="px-3 py-1.5 text-sm text-slate-600 hover:text-slate-900 hover:bg-slate-200 rounded-lg transition-colors"
        >
          Cancel
        </button>
      </div>
    </div>
  );
}

// ── Pipeline errors table ────────────────────────────────────────────────────

function PipelineErrorsTab() {
  const [page, setPage] = useState(1);
  const [source, setSource] = useState("");
  const [showResolved, setShowResolved] = useState(false);
  const [expandedId, setExpandedId] = useState<number | null>(null);
  const [acting, setActing] = useState<number | null>(null);
  const [backfilling, setBackfilling] = useState(false);
  const [banner, setBanner] = useState<{ kind: "success" | "error"; msg: string } | null>(null);

  const flash = useCallback((kind: "success" | "error", msg: string) => {
    setBanner({ kind, msg });
    setTimeout(() => setBanner(null), 4000);
  }, []);

  const swrKey = `admin-pipeline-errors-${page}-${source}-${showResolved}`;
  const { data, isLoading, mutate } = useSWR(
    swrKey,
    () => fetchPipelineErrors({ page, page_size: 50, source: source || undefined, show_resolved: showResolved }),
  );

  const handleIgnore = async (id: number) => {
    setActing(id);
    try {
      await ignoreAdminError(id);
      await mutate();
      flash("success", "Error ignored");
    } catch {
      flash("error", "Failed to ignore");
    } finally {
      setActing(null);
    }
  };

  const handleSaved = async () => {
    await mutate();
    setExpandedId(null);
    flash("success", "Saved and resolved");
  };

  const items: PipelineError[] = data?.items ?? [];
  const total = data?.total ?? 0;
  const totalPages = Math.ceil(total / 50);

  return (
    <div className="space-y-3">
      {banner && (
        <div className={`flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium ${banner.kind === "success" ? "bg-green-50 text-green-800" : "bg-red-50 text-red-700"}`}>
          {banner.kind === "success" ? <CheckCircle2 size={14} /> : <XCircle size={14} />}
          {banner.msg}
        </div>
      )}
      <div className="flex flex-wrap items-center gap-3">
        <select
          value={source}
          onChange={(e) => { setSource(e.target.value); setPage(1); }}
          className="text-sm border border-slate-300 rounded-lg px-3 py-1.5 bg-white focus:outline-none focus:ring-2 focus:ring-purple-400"
        >
          <option value="">All sources</option>
          <option value="crawler">Crawler</option>
          <option value="web_enrichment">Web enrichment</option>
          <option value="zefix_import">Zefix import</option>
          <option value="geocoding">Geocoding</option>
          <option value="noga">NOGA</option>
          <option value="shab_old_pdf">SHAB old PDF</option>
        </select>
        <label className="flex items-center gap-2 text-sm text-slate-600 cursor-pointer">
          <input
            type="checkbox"
            checked={showResolved}
            onChange={(e) => { setShowResolved(e.target.checked); setPage(1); }}
            className="rounded"
          />
          Show resolved
        </label>
        <button
          disabled={backfilling}
          onClick={async () => {
            setBackfilling(true);
            try {
              const r = await backfillCrawlerErrors();
              await mutate();
              flash("success", `Backfilled ${r.backfilled} crawler errors`);
            } catch {
              flash("error", "Backfill failed");
            } finally {
              setBackfilling(false);
            }
          }}
          className="ml-auto flex items-center gap-1.5 px-2 py-1 text-xs rounded bg-slate-100 text-slate-600 hover:bg-slate-200 disabled:opacity-50 transition-colors"
          title="Import existing terminal crawler failures into pipeline errors"
        >
          {backfilling ? <Loader2 size={12} className="animate-spin" /> : <Database size={12} />}
          Backfill crawler
        </button>
        <button onClick={() => mutate()} className="text-slate-500 hover:text-slate-700">
          <RefreshCw size={14} />
        </button>
      </div>

      {isLoading ? (
        <div className="flex justify-center py-12"><Loader2 size={20} className="animate-spin text-slate-400" /></div>
      ) : items.length === 0 ? (
        <div className="text-center py-12 text-slate-400 text-sm">No errors found</div>
      ) : (
        <div className="border border-slate-200 rounded-xl overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-slate-50 border-b border-slate-200">
              <tr>
                <th className="text-left px-4 py-2.5 text-xs font-semibold text-slate-500 uppercase tracking-wider">Company</th>
                <th className="text-left px-4 py-2.5 text-xs font-semibold text-slate-500 uppercase tracking-wider">Source</th>
                <th className="text-left px-4 py-2.5 text-xs font-semibold text-slate-500 uppercase tracking-wider">Error</th>
                <th className="text-left px-4 py-2.5 text-xs font-semibold text-slate-500 uppercase tracking-wider">Date</th>
                <th className="text-right px-4 py-2.5 text-xs font-semibold text-slate-500 uppercase tracking-wider">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {items.map((err) => (
                <>
                  <tr
                    key={err.id}
                    className={`hover:bg-slate-50 cursor-pointer ${err.resolved_at ? "opacity-60" : ""}`}
                    onClick={() => setExpandedId(expandedId === err.id ? null : err.id)}
                  >
                    <td className="px-4 py-3">
                      <div className="font-medium text-slate-800 truncate max-w-[160px]">
                        {err.company_name ?? <span className="text-slate-400 italic">No company</span>}
                      </div>
                      {err.company_uid && <div className="text-xs text-slate-400 font-mono">{err.company_uid}</div>}
                    </td>
                    <td className="px-4 py-3">
                      <SourceBadge source={err.error_source} />
                    </td>
                    <td className="px-4 py-3">
                      <div className="text-slate-700 text-xs font-mono truncate max-w-[220px]" title={err.message ?? ""}>
                        {err.error_type}{err.message ? ` — ${err.message}` : ""}
                      </div>
                    </td>
                    <td className="px-4 py-3 text-xs text-slate-400 whitespace-nowrap">
                      {new Date(err.created_at).toLocaleDateString("de-CH")}
                    </td>
                    <td className="px-4 py-3 text-right">
                      <div className="flex items-center justify-end gap-1" onClick={(e) => e.stopPropagation()}>
                        {err.resolved_at ? (
                          <span className="text-xs text-green-600 flex items-center gap-1"><CheckCircle2 size={12} /> Resolved</span>
                        ) : (
                          <>
                            <button
                              disabled={acting === err.id}
                              onClick={() => setExpandedId(expandedId === err.id ? null : err.id)}
                              className="px-2 py-1 text-xs rounded bg-purple-50 text-purple-700 hover:bg-purple-100 transition-colors"
                            >
                              {expandedId === err.id ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
                            </button>
                            <button
                              disabled={acting === err.id}
                              onClick={() => handleIgnore(err.id)}
                              className="px-2 py-1 text-xs rounded bg-slate-100 text-slate-600 hover:bg-slate-200 transition-colors flex items-center gap-1"
                            >
                              {acting === err.id ? <Loader2 size={11} className="animate-spin" /> : <EyeOff size={11} />}
                              Ignore
                            </button>
                          </>
                        )}
                      </div>
                    </td>
                  </tr>
                  {expandedId === err.id && (
                    <tr key={`${err.id}-expand`}>
                      <td colSpan={5} className="px-4 pb-4">
                        <div className="mt-2 space-y-2">
                          {/* Full error text */}
                          {err.message && (
                            <div className="p-3 bg-red-50 border border-red-200 rounded-lg">
                              <p className="text-xs font-semibold text-red-700 mb-1 uppercase tracking-wider">Message</p>
                              <p className="text-xs text-red-900 font-mono whitespace-pre-wrap break-all">{err.message}</p>
                            </div>
                          )}
                          {err.detail_json && (
                            <div className="rounded-lg overflow-hidden">
                              <p className="text-xs font-semibold text-slate-400 uppercase tracking-wider bg-slate-800 px-3 pt-2 pb-1">Detail</p>
                              <pre className="bg-slate-900 text-slate-100 text-xs px-3 pb-3 overflow-auto max-h-48 whitespace-pre-wrap">
                                {(() => { try { return JSON.stringify(JSON.parse(err.detail_json), null, 2); } catch { return err.detail_json; } })()}
                              </pre>
                            </div>
                          )}
                          {/* Correction form (only for unresolved errors) */}
                          {!err.resolved_at && (
                            <CorrectionPanel
                              error={err}
                              onSaved={handleSaved}
                              onCancel={() => setExpandedId(null)}
                            />
                          )}
                        </div>
                      </td>
                    </tr>
                  )}
                </>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {totalPages > 1 && (
        <div className="flex items-center justify-between text-sm text-slate-500">
          <span>{total} total</span>
          <div className="flex gap-2">
            <button disabled={page <= 1} onClick={() => setPage((p) => p - 1)}
              className="p-1 rounded hover:bg-slate-100 disabled:opacity-30">
              <ChevronLeft size={16} />
            </button>
            <span className="px-2 py-1">{page} / {totalPages}</span>
            <button disabled={page >= totalPages} onClick={() => setPage((p) => p + 1)}
              className="p-1 rounded hover:bg-slate-100 disabled:opacity-30">
              <ChevronRight size={16} />
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

// ── Crawler failures tab (re-uses existing endpoint) ─────────────────────────

function CrawlerTab() {
  const [page, setPage] = useState(1);
  const [statusFilter, setStatusFilter] = useState("");
  const [expandedId, setExpandedId] = useState<number | null>(null);
  const [form, setForm] = useState<{ [id: number]: { website_url: string } }>({});
  const [saving, setSaving] = useState<number | null>(null);
  const [banner, setBanner] = useState<{ kind: "success" | "error"; msg: string } | null>(null);

  const flash = useCallback((kind: "success" | "error", msg: string) => {
    setBanner({ kind, msg });
    setTimeout(() => setBanner(null), 4000);
  }, []);

  const { data, isLoading, mutate } = useSWR(
    `admin-crawler-failures-ec-${page}-${statusFilter}`,
    () => fetchAdminCrawlerFailures({ page, page_size: 50, status_filter: statusFilter || undefined }),
  );

  const handleSaveWebsite = async (item: AdminCrawlerFailure) => {
    const url = form[item.company_id]?.website_url;
    if (!url) return;
    setSaving(item.company_id);
    try {
      await correctCompanyData(item.company_id, { website_url: url });
      await mutate();
      setExpandedId(null);
      flash("success", "Website URL saved");
    } catch {
      flash("error", "Save failed");
    } finally {
      setSaving(null);
    }
  };

  const items: AdminCrawlerFailure[] = data?.items ?? [];
  const total = data?.total ?? 0;
  const totalPages = Math.ceil(total / 50);

  const STATUS_COLOUR: Record<string, string> = {
    bot_blocked: "bg-red-100 text-red-800",
    http_error: "bg-red-100 text-red-800",
    timeout: "bg-orange-100 text-orange-800",
    no_content: "bg-slate-100 text-slate-600",
    no_website: "bg-slate-100 text-slate-500",
  };

  return (
    <div className="space-y-3">
      {banner && (
        <div className={`flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium ${banner.kind === "success" ? "bg-green-50 text-green-800" : "bg-red-50 text-red-700"}`}>
          {banner.kind === "success" ? <CheckCircle2 size={14} /> : <XCircle size={14} />}
          {banner.msg}
        </div>
      )}
      <div className="flex items-center gap-3">
        <select
          value={statusFilter}
          onChange={(e) => { setStatusFilter(e.target.value); setPage(1); }}
          className="text-sm border border-slate-300 rounded-lg px-3 py-1.5 bg-white focus:outline-none focus:ring-2 focus:ring-purple-400"
        >
          <option value="">All failure types</option>
          <option value="bot_blocked">Bot blocked</option>
          <option value="http_error">HTTP error</option>
          <option value="timeout">Timeout</option>
          <option value="no_content">No content</option>
          <option value="no_website">No website</option>
        </select>
        <button onClick={() => mutate()} className="ml-auto text-slate-500 hover:text-slate-700">
          <RefreshCw size={14} />
        </button>
      </div>

      {isLoading ? (
        <div className="flex justify-center py-12"><Loader2 size={20} className="animate-spin text-slate-400" /></div>
      ) : items.length === 0 ? (
        <div className="text-center py-12 text-slate-400 text-sm">No failures found</div>
      ) : (
        <div className="border border-slate-200 rounded-xl overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-slate-50 border-b border-slate-200">
              <tr>
                <th className="text-left px-4 py-2.5 text-xs font-semibold text-slate-500 uppercase tracking-wider">Company</th>
                <th className="text-left px-4 py-2.5 text-xs font-semibold text-slate-500 uppercase tracking-wider">Status</th>
                <th className="text-left px-4 py-2.5 text-xs font-semibold text-slate-500 uppercase tracking-wider">Detail</th>
                <th className="text-left px-4 py-2.5 text-xs font-semibold text-slate-500 uppercase tracking-wider">Failures</th>
                <th className="text-right px-4 py-2.5 text-xs font-semibold text-slate-500 uppercase tracking-wider">Correct</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {items.map((item) => (
                <>
                  <tr key={item.company_id} className="hover:bg-slate-50">
                    <td className="px-4 py-3">
                      <div className="font-medium text-slate-800 truncate max-w-[160px]">{item.company_name}</div>
                      <div className="text-xs text-slate-400 font-mono">{item.company_uid}</div>
                    </td>
                    <td className="px-4 py-3">
                      <span className={`inline-block px-2 py-0.5 rounded-full text-xs font-medium ${STATUS_COLOUR[item.crawl_status] ?? "bg-slate-100 text-slate-600"}`}>
                        {item.crawl_status.replace("_", " ")}
                      </span>
                    </td>
                    <td className="px-4 py-3 text-xs text-slate-500 max-w-[200px] truncate" title={item.crawl_error_detail ?? ""}>
                      {item.crawl_error_detail ?? item.bot_protection_type ?? "—"}
                    </td>
                    <td className="px-4 py-3 text-xs text-slate-500 tabular-nums">{item.consecutive_failures}</td>
                    <td className="px-4 py-3 text-right">
                      <button
                        onClick={() => setExpandedId(expandedId === item.company_id ? null : item.company_id)}
                        className="px-2 py-1 text-xs rounded bg-purple-50 text-purple-700 hover:bg-purple-100 transition-colors"
                      >
                        {expandedId === item.company_id ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
                      </button>
                    </td>
                  </tr>
                  {expandedId === item.company_id && (
                    <tr key={`${item.company_id}-expand`}>
                      <td colSpan={5} className="px-4 pb-3">
                        <div className="mt-2 p-4 bg-slate-50 border border-slate-200 rounded-xl space-y-3">
                          <p className="text-xs font-semibold text-slate-500 uppercase tracking-wider">Override Website URL</p>
                          <input
                            type="text"
                            className="w-full text-sm border border-slate-300 rounded-lg px-3 py-2 focus:outline-none focus:ring-2 focus:ring-purple-400"
                            placeholder="https://..."
                            value={form[item.company_id]?.website_url ?? ""}
                            onChange={(e) => setForm((f) => ({ ...f, [item.company_id]: { website_url: e.target.value } }))}
                          />
                          <div className="flex gap-2">
                            <button
                              disabled={saving === item.company_id}
                              onClick={() => handleSaveWebsite(item)}
                              className="flex items-center gap-1.5 px-3 py-1.5 bg-purple-600 hover:bg-purple-700 text-white text-sm rounded-lg disabled:opacity-50 transition-colors"
                            >
                              {saving === item.company_id ? <Loader2 size={13} className="animate-spin" /> : <Check size={13} />}
                              Save URL
                            </button>
                            <button
                              onClick={() => setExpandedId(null)}
                              className="px-3 py-1.5 text-sm text-slate-600 hover:text-slate-900 hover:bg-slate-200 rounded-lg transition-colors"
                            >
                              Cancel
                            </button>
                          </div>
                        </div>
                      </td>
                    </tr>
                  )}
                </>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {totalPages > 1 && (
        <div className="flex items-center justify-between text-sm text-slate-500">
          <span>{total} total</span>
          <div className="flex gap-2">
            <button disabled={page <= 1} onClick={() => setPage((p) => p - 1)}
              className="p-1 rounded hover:bg-slate-100 disabled:opacity-30"><ChevronLeft size={16} /></button>
            <span className="px-2 py-1">{page} / {totalPages}</span>
            <button disabled={page >= totalPages} onClick={() => setPage((p) => p + 1)}
              className="p-1 rounded hover:bg-slate-100 disabled:opacity-30"><ChevronRight size={16} /></button>
          </div>
        </div>
      )}
    </div>
  );
}

// ── Job failures tab ─────────────────────────────────────────────────────────

function JobFailuresTab() {
  const [page, setPage] = useState(1);
  const [expandedId, setExpandedId] = useState<number | null>(null);

  const { data, isLoading, mutate } = useSWR(
    `admin-job-failures-${page}`,
    () => fetchJobFailures({ page, page_size: 50 }),
  );

  const items: JobFailure[] = data?.items ?? [];
  const total = data?.total ?? 0;
  const totalPages = Math.ceil(total / 50);

  return (
    <div className="space-y-3">
      <div className="flex justify-end">
        <button onClick={() => mutate()} className="text-slate-500 hover:text-slate-700">
          <RefreshCw size={14} />
        </button>
      </div>
      {isLoading ? (
        <div className="flex justify-center py-12"><Loader2 size={20} className="animate-spin text-slate-400" /></div>
      ) : items.length === 0 ? (
        <div className="text-center py-12 text-slate-400 text-sm">No failed jobs</div>
      ) : (
        <div className="border border-slate-200 rounded-xl overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-slate-50 border-b border-slate-200">
              <tr>
                <th className="text-left px-4 py-2.5 text-xs font-semibold text-slate-500 uppercase tracking-wider">Job</th>
                <th className="text-left px-4 py-2.5 text-xs font-semibold text-slate-500 uppercase tracking-wider">Error</th>
                <th className="text-left px-4 py-2.5 text-xs font-semibold text-slate-500 uppercase tracking-wider">Date</th>
                <th className="text-right px-4 py-2.5 text-xs font-semibold text-slate-500 uppercase tracking-wider">Detail</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {items.map((job) => (
                <>
                  <tr key={job.id} className="hover:bg-slate-50 cursor-pointer" onClick={() => setExpandedId(expandedId === job.id ? null : job.id)}>
                    <td className="px-4 py-3">
                      <div className="font-medium text-slate-800">{job.job_type}</div>
                      <div className="text-xs text-slate-400 truncate max-w-[160px]">{job.label}</div>
                    </td>
                    <td className="px-4 py-3 text-xs text-slate-600 max-w-[220px] truncate font-mono" title={job.error ?? ""}>
                      {job.error ?? job.message ?? "—"}
                    </td>
                    <td className="px-4 py-3 text-xs text-slate-400 whitespace-nowrap">
                      {job.completed_at ? new Date(job.completed_at).toLocaleDateString("de-CH") : "—"}
                    </td>
                    <td className="px-4 py-3 text-right">
                      {expandedId === job.id ? <ChevronUp size={14} className="inline text-slate-400" /> : <ChevronDown size={14} className="inline text-slate-400" />}
                    </td>
                  </tr>
                  {expandedId === job.id && (
                    <tr key={`${job.id}-expand`}>
                      <td colSpan={4} className="px-4 pb-3">
                        <pre className="mt-2 p-3 bg-slate-900 text-slate-100 text-xs rounded-lg overflow-auto max-h-48 whitespace-pre-wrap">
                          {job.error ?? "(no traceback stored)"}
                          {job.stats_json && `\n\nStats:\n${job.stats_json}`}
                        </pre>
                      </td>
                    </tr>
                  )}
                </>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {totalPages > 1 && (
        <div className="flex items-center justify-between text-sm text-slate-500">
          <span>{total} total</span>
          <div className="flex gap-2">
            <button disabled={page <= 1} onClick={() => setPage((p) => p - 1)}
              className="p-1 rounded hover:bg-slate-100 disabled:opacity-30"><ChevronLeft size={16} /></button>
            <span className="px-2 py-1">{page} / {totalPages}</span>
            <button disabled={page >= totalPages} onClick={() => setPage((p) => p + 1)}
              className="p-1 rounded hover:bg-slate-100 disabled:opacity-30"><ChevronRight size={16} /></button>
          </div>
        </div>
      )}
    </div>
  );
}

// ── Main page ────────────────────────────────────────────────────────────────

export function ErrorCenterClient() {
  const [tab, setTab] = useState<Tab>("pipeline");

  const { data: quality, isLoading: qualityLoading, mutate: mutateQuality } =
    useSWR<DataQualitySummary>("admin-data-quality-summary", fetchDataQualitySummary);

  const q = quality;

  return (
    <div className="p-6 space-y-6 max-w-7xl">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold text-slate-900 flex items-center gap-2">
            <AlertTriangle size={18} className="text-amber-500" />
            Error Center
          </h1>
          <p className="text-sm text-slate-500 mt-0.5">Data quality overview and pipeline error review</p>
        </div>
        <button onClick={() => mutateQuality()} className="text-slate-400 hover:text-slate-700">
          <RefreshCw size={15} />
        </button>
      </div>

      {/* Quality Overview */}
      <div className="space-y-3">
        <h2 className="text-sm font-semibold text-slate-600 uppercase tracking-wider">Data Quality</h2>
        {qualityLoading ? (
          <div className="flex justify-center py-6"><Loader2 size={18} className="animate-spin text-slate-400" /></div>
        ) : q ? (
          <>
            <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-7 gap-3">
              <QualityCard
                label="Website URL"
                value={`${q.pct_website_url}%`}
                sub={`of ${q.total_companies.toLocaleString()}`}
                icon={Globe}
                colour={qualityColour(q.pct_website_url)}
              />
              <QualityCard
                label="Web Extract"
                value={`${q.pct_web_extract}%`}
                icon={Database}
                colour={qualityColour(q.pct_web_extract)}
              />
              <QualityCard
                label="AI Score"
                value={`${q.pct_ai_score}%`}
                icon={Brain}
                colour={qualityColour(q.pct_ai_score)}
              />
              <QualityCard
                label="NOGA Code"
                value={`${q.pct_noga_code}%`}
                sub={`${q.pct_noga_high_conf}% high-conf`}
                icon={Database}
                colour={qualityColour(q.pct_noga_code)}
              />
              <QualityCard
                label="Geocoded"
                value={`${q.pct_geocoded}%`}
                icon={MapPin}
                colour={qualityColour(q.pct_geocoded)}
              />
              <QualityCard
                label="Crawler Failures"
                value={q.crawler_terminal_count.toLocaleString()}
                sub={`${q.crawler_bot_blocked} bot / ${q.crawler_http_error} HTTP / ${q.crawler_timeout} timeout`}
                icon={AlertTriangle}
                colour={countColour(q.crawler_terminal_count, 500, 2000)}
              />
              <QualityCard
                label="Review Flags"
                value={q.review_flag_count.toLocaleString()}
                sub={`${q.uid_mismatch_count} UID mismatch`}
                icon={Flag}
                colour={countColour(q.review_flag_count, 50, 200)}
              />
            </div>
            {q.active_pipeline_errors > 0 && (
              <div className="flex items-center gap-2 px-4 py-2.5 bg-amber-50 border border-amber-200 rounded-xl text-sm text-amber-800">
                <AlertTriangle size={14} />
                <span><strong>{q.active_pipeline_errors}</strong> active pipeline errors need review</span>
              </div>
            )}
          </>
        ) : null}
      </div>

      {/* Tabs + content */}
      <div className="bg-white rounded-xl border border-slate-200 overflow-hidden">
        <div className="border-b border-slate-200 flex">
          <TabBtn active={tab === "pipeline"} onClick={() => setTab("pipeline")}>
            Pipeline Errors {q?.active_pipeline_errors ? `(${q.active_pipeline_errors})` : ""}
          </TabBtn>
          <TabBtn active={tab === "crawler"} onClick={() => setTab("crawler")}>
            Crawler Failures {q?.crawler_terminal_count ? `(${q.crawler_terminal_count.toLocaleString()})` : ""}
          </TabBtn>
          <TabBtn active={tab === "jobs"} onClick={() => setTab("jobs")}>
            Job Failures
          </TabBtn>
        </div>
        <div className="p-4">
          {tab === "pipeline" && <PipelineErrorsTab />}
          {tab === "crawler" && <CrawlerTab />}
          {tab === "jobs" && <JobFailuresTab />}
        </div>
      </div>
    </div>
  );
}
