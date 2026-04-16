"use client";
import { useState, useCallback } from "react";
import useSWR from "swr";
import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  CheckCircle2, XCircle, Clock, Loader2, PauseCircle,
  RefreshCw, Calendar, ArrowUpRight, Search, ChevronRight,
  Building2, Pencil, Trash2,
} from "lucide-react";
import { fetchJobs, triggerJob, fetchCurrentUser, fetchCompanies, fetchCantons } from "@/lib/api";
import type { Job, Company, CompanyFilters } from "@/lib/types";

// ── helpers ───────────────────────────────────────────────────────────────────

const SHAB_JOB_TYPES = new Set(["shab_daily", "shab_backfill"]);

const CANTON_LABELS: Record<string, string> = {
  AG: "Aargau", AI: "Appenzell I.Rh.", AR: "Appenzell A.Rh.", BE: "Bern",
  BL: "Basel-Land", BS: "Basel-Stadt", FR: "Fribourg", GE: "Genf",
  GL: "Glarus", GR: "Graubünden", JU: "Jura", LU: "Luzern",
  NE: "Neuenburg", NW: "Nidwalden", OW: "Obwalden", SG: "St. Gallen",
  SH: "Schaffhausen", SO: "Solothurn", SZ: "Schwyz", TG: "Thurgau",
  TI: "Tessin", UR: "Uri", VD: "Waadt", VS: "Wallis",
  ZG: "Zug", ZH: "Zürich",
};

function fmtDate(iso: string) {
  return new Date(iso).toLocaleString("de-CH", {
    day: "2-digit", month: "short", year: "numeric",
    hour: "2-digit", minute: "2-digit",
  });
}

function fmtDay(iso: string | null) {
  if (!iso) return "—";
  return new Date(iso).toLocaleDateString("de-CH", {
    day: "2-digit", month: "short", year: "numeric",
  });
}

function statusBadge(s: Job["status"]) {
  const base = "inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium";
  switch (s) {
    case "completed": return <span className={`${base} bg-emerald-50 text-emerald-700`}><CheckCircle2 size={11} /> done</span>;
    case "failed":    return <span className={`${base} bg-red-50 text-red-700`}><XCircle size={11} /> failed</span>;
    case "cancelled": return <span className={`${base} bg-slate-100 text-slate-500`}><XCircle size={11} /> cancelled</span>;
    case "running":   return <span className={`${base} bg-blue-50 text-blue-700`}><Loader2 size={11} className="animate-spin" /> running</span>;
    case "paused":    return <span className={`${base} bg-amber-50 text-amber-700`}><PauseCircle size={11} /> paused</span>;
    default:          return <span className={`${base} bg-slate-100 text-slate-600`}><Clock size={11} /> queued</span>;
  }
}

function shabTypeBadge(type: string | undefined) {
  const base = "inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-semibold";
  switch (type) {
    case "new":      return <span className={`${base} bg-emerald-50 text-emerald-700`}><Building2 size={10} /> HR01 New</span>;
    case "mutation": return <span className={`${base} bg-blue-50 text-blue-600`}><Pencil size={10} /> HR02 Mutation</span>;
    case "deleted":  return <span className={`${base} bg-red-50 text-red-600`}><Trash2 size={10} /> HR03 Deleted</span>;
    default:         return null;
  }
}

function inferShabType(company: Company): "new" | "mutation" | "deleted" | undefined {
  const deleted = ["Gelöscht", "CANCELLED", "BEING_CANCELLED"];
  if (company.status && deleted.includes(company.status)) return "deleted";
  if (company.first_sogc_date && company.sogc_date) {
    if (company.first_sogc_date === company.sogc_date) return "new";
    return "mutation";
  }
  return undefined;
}

function statsFromJob(job: Job): { new: number; updated: number; deleted: number; errors: number } | null {
  try {
    if (!job.message) return null;
    const nm = job.message.match(/(\d+)\s+new/);
    const um = job.message.match(/(\d+)\s+updated/);
    const dm = job.message.match(/(\d+)\s+deleted/);
    const em = job.message.match(/(\d+)\s+errors/);
    if (!nm && !um && !dm) return null;
    return {
      new: nm ? parseInt(nm[1]) : 0,
      updated: um ? parseInt(um[1]) : 0,
      deleted: dm ? parseInt(dm[1]) : 0,
      errors: em ? parseInt(em[1]) : 0,
    };
  } catch { return null; }
}

const inputCls = "w-full px-3 py-2 border border-slate-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-300";

// ── JobCard ───────────────────────────────────────────────────────────────────

function JobCard({ job }: { job: Job }) {
  const stats = statsFromJob(job);
  return (
    <div className={`rounded-xl border p-4 ${
      job.status === "running" ? "border-blue-200 bg-blue-50/30" :
      job.status === "failed" ? "border-red-200 bg-red-50/30" :
      "border-slate-200 bg-white"
    }`}>
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2 flex-wrap">
            {statusBadge(job.status)}
            <span className="text-xs text-slate-400 font-mono">#{job.id}</span>
            <span className="text-xs text-slate-500 bg-slate-100 px-1.5 py-0.5 rounded">{job.job_type}</span>
          </div>
          <p className="mt-1 text-sm font-medium text-slate-800">{job.label}</p>
          {job.message && !job.message.startsWith("Done") && (
            <p className="mt-0.5 text-xs text-slate-500 truncate">{job.message}</p>
          )}
          {job.status === "running" && job.progress_done != null && job.progress_total != null && (
            <div className="mt-2 space-y-1">
              <div className="h-1.5 bg-slate-100 rounded-full overflow-hidden">
                <div className="h-full bg-blue-500 rounded-full transition-all"
                  style={{ width: `${Math.min(100, Math.round((job.progress_done / job.progress_total) * 100))}%` }} />
              </div>
              <p className="text-xs text-slate-400">{job.progress_done} / {job.progress_total}</p>
            </div>
          )}
        </div>
        <Link href="/app/jobs" className="text-xs text-blue-600 hover:underline shrink-0 flex items-center gap-0.5">
          Details <ArrowUpRight size={11} />
        </Link>
      </div>
      {stats && (
        <div className="mt-3 flex flex-wrap gap-4 text-xs">
          <span className="text-emerald-700 font-medium">+{stats.new} new</span>
          <span className="text-blue-600 font-medium">{stats.updated} updated</span>
          <span className="text-slate-500">{stats.deleted} deleted</span>
          {stats.errors > 0 && <span className="text-red-500">{stats.errors} errors</span>}
        </div>
      )}
      <div className="mt-2 flex flex-wrap gap-3 text-xs text-slate-400">
        {job.created_at && <span>Queued {fmtDate(job.created_at)}</span>}
        {job.started_at && <span>Started {fmtDate(job.started_at)}</span>}
        {job.finished_at && <span>Finished {fmtDate(job.finished_at)}</span>}
      </div>
      {job.error && (
        <details className="mt-2">
          <summary className="text-xs text-red-500 cursor-pointer">Show error</summary>
          <pre className="mt-1 text-xs text-red-400 bg-red-50 rounded-lg p-2 overflow-auto max-h-24 whitespace-pre-wrap">{job.error}</pre>
        </details>
      )}
    </div>
  );
}

// ── BrowseTab ─────────────────────────────────────────────────────────────────

const SHAB_TYPES = [
  { value: "", label: "All types" },
  { value: "new", label: "HR01 — New" },
  { value: "mutation", label: "HR02 — Mutation" },
  { value: "deleted", label: "HR03 — Deleted" },
];

const PAGE_SIZE = 50;

function BrowseTab() {
  const { data: cantons = [] } = useSWR("cantons", fetchCantons);

  const [q, setQ] = useState("");
  const [shabType, setShabType] = useState("");
  const [canton, setCanton] = useState("");
  const [sogcAfter, setSogcAfter] = useState("");
  const [sogcBefore, setSogcBefore] = useState("");
  const [page, setPage] = useState(1);

  // Committed filters (only update when user searches)
  const [committed, setCommitted] = useState<CompanyFilters>({
    sort: "-sogc_date", page_size: PAGE_SIZE, page: 1,
  });

  const applyFilters = useCallback(() => {
    setPage(1);
    setCommitted({
      q: q || undefined,
      shab_type: shabType || undefined,
      canton: canton || undefined,
      sogc_after: sogcAfter || undefined,
      sogc_before: sogcBefore || undefined,
      sort: "-sogc_date",
      page: 1,
      page_size: PAGE_SIZE,
    });
  }, [q, shabType, canton, sogcAfter, sogcBefore]);

  const filtersWithPage = { ...committed, page };

  const { data, isLoading } = useSWR(
    ["shab-browse", filtersWithPage],
    () => fetchCompanies(filtersWithPage),
    { keepPreviousData: true },
  );

  const companies = data?.items ?? [];
  const total = data?.total ?? 0;
  const pages = data?.pages ?? 1;

  function handleKeyDown(e: React.KeyboardEvent) {
    if (e.key === "Enter") applyFilters();
  }

  return (
    <div className="space-y-4">
      {/* Filter bar */}
      <div className="rounded-2xl border border-slate-200 bg-white p-4 space-y-3">
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
          {/* Name search */}
          <div className="relative">
            <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400 pointer-events-none" />
            <input
              type="text"
              placeholder="Search company name…"
              className="w-full pl-8 pr-3 py-2 border border-slate-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-300"
              value={q}
              onChange={(e) => setQ(e.target.value)}
              onKeyDown={handleKeyDown}
            />
          </div>

          {/* SHAB type */}
          <select
            className={inputCls}
            value={shabType}
            onChange={(e) => setShabType(e.target.value)}
          >
            {SHAB_TYPES.map((t) => (
              <option key={t.value} value={t.value}>{t.label}</option>
            ))}
          </select>

          {/* Canton */}
          <select
            className={inputCls}
            value={canton}
            onChange={(e) => setCanton(e.target.value)}
          >
            <option value="">All cantons</option>
            {cantons.map((c) => (
              <option key={c} value={c}>{CANTON_LABELS[c] ?? c} ({c})</option>
            ))}
          </select>

          {/* Apply button */}
          <button
            onClick={applyFilters}
            className="flex items-center justify-center gap-2 bg-blue-600 hover:bg-blue-700 text-white px-4 py-2 rounded-lg text-sm font-medium transition-colors"
          >
            <Search size={14} /> Search
          </button>
        </div>

        {/* Date range */}
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          <div>
            <label className="block text-xs font-medium text-slate-600 mb-1">SOGC date from</label>
            <input
              type="date"
              className={inputCls}
              value={sogcAfter}
              onChange={(e) => setSogcAfter(e.target.value)}
              onKeyDown={handleKeyDown}
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-slate-600 mb-1">SOGC date to</label>
            <input
              type="date"
              className={inputCls}
              value={sogcBefore}
              onChange={(e) => setSogcBefore(e.target.value)}
              onKeyDown={handleKeyDown}
            />
          </div>
        </div>

        {/* Active filter chips */}
        {(committed.shab_type || committed.canton || committed.sogc_after || committed.sogc_before || committed.q) && (
          <div className="flex flex-wrap gap-2 pt-1">
            {committed.q && <Chip label={`Name: ${committed.q}`} onRemove={() => { setQ(""); setCommitted(f => ({ ...f, q: undefined, page: 1 })); }} />}
            {committed.shab_type && <Chip label={SHAB_TYPES.find(t => t.value === committed.shab_type)?.label ?? committed.shab_type} onRemove={() => { setShabType(""); setCommitted(f => ({ ...f, shab_type: undefined, page: 1 })); }} />}
            {committed.canton && <Chip label={`Canton: ${committed.canton}`} onRemove={() => { setCanton(""); setCommitted(f => ({ ...f, canton: undefined, page: 1 })); }} />}
            {committed.sogc_after && <Chip label={`From: ${committed.sogc_after}`} onRemove={() => { setSogcAfter(""); setCommitted(f => ({ ...f, sogc_after: undefined, page: 1 })); }} />}
            {committed.sogc_before && <Chip label={`To: ${committed.sogc_before}`} onRemove={() => { setSogcBefore(""); setCommitted(f => ({ ...f, sogc_before: undefined, page: 1 })); }} />}
          </div>
        )}
      </div>

      {/* Results */}
      <div className="rounded-2xl border border-slate-200 bg-white overflow-hidden">
        <div className="px-4 py-3 border-b border-slate-100 flex items-center justify-between">
          <span className="text-sm font-medium text-slate-700">
            {isLoading ? "Loading…" : `${total.toLocaleString()} result${total !== 1 ? "s" : ""}`}
          </span>
          {isLoading && <Loader2 size={14} className="animate-spin text-slate-400" />}
        </div>

        {!isLoading && companies.length === 0 ? (
          <div className="px-4 py-10 text-center text-sm text-slate-400">
            No companies found. Try adjusting the filters above.
          </div>
        ) : (
          <div className="divide-y divide-slate-100">
            {companies.map((company) => (
              <CompanyRow key={company.id} company={company} />
            ))}
          </div>
        )}

        {/* Pagination */}
        {pages > 1 && (
          <div className="px-4 py-3 border-t border-slate-100 flex items-center justify-between gap-3">
            <span className="text-xs text-slate-400">
              Page {page} of {pages}
            </span>
            <div className="flex items-center gap-2">
              <button
                disabled={page <= 1}
                onClick={() => setPage(p => Math.max(1, p - 1))}
                className="px-3 py-1.5 rounded-lg border border-slate-200 text-sm text-slate-600 hover:bg-slate-50 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
              >
                Previous
              </button>
              <button
                disabled={page >= pages}
                onClick={() => setPage(p => Math.min(pages, p + 1))}
                className="px-3 py-1.5 rounded-lg border border-slate-200 text-sm text-slate-600 hover:bg-slate-50 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
              >
                Next
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

function Chip({ label, onRemove }: { label: string; onRemove: () => void }) {
  return (
    <span className="inline-flex items-center gap-1 px-2 py-1 rounded-full bg-blue-50 text-blue-700 text-xs font-medium">
      {label}
      <button onClick={onRemove} className="hover:text-blue-900 ml-0.5">
        <XCircle size={12} />
      </button>
    </span>
  );
}

function CompanyRow({ company }: { company: Company }) {
  const shabType = inferShabType(company);
  return (
    <Link
      href={`/app/companies/${company.id}`}
      className="flex items-center gap-3 px-4 py-3 hover:bg-slate-50 transition-colors group"
    >
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2 flex-wrap">
          <span className="text-sm font-medium text-slate-800 truncate">{company.name}</span>
          {shabType && shabTypeBadge(shabType)}
        </div>
        <div className="mt-0.5 flex flex-wrap gap-x-3 gap-y-0.5 text-xs text-slate-500">
          {company.legal_form && <span>{company.legal_form}</span>}
          {company.canton && <span>{CANTON_LABELS[company.canton] ?? company.canton}</span>}
          {company.municipality && <span>{company.municipality}</span>}
          {company.uid && <span className="font-mono text-slate-400">{company.uid}</span>}
        </div>
      </div>
      <div className="shrink-0 text-right">
        <div className="text-xs font-medium text-slate-700">{fmtDay(company.sogc_date)}</div>
        <div className="text-xs text-slate-400">SOGC date</div>
      </div>
      <ChevronRight size={14} className="shrink-0 text-slate-300 group-hover:text-slate-500 transition-colors" />
    </Link>
  );
}

// ── main component ────────────────────────────────────────────────────────────

type Tab = "browse" | "imports";

export function ShabClient() {
  const router = useRouter();
  const [activeTab, setActiveTab] = useState<Tab>("browse");
  const { data: me } = useSWR("me", fetchCurrentUser);
  const isSuperadmin = me?.is_superadmin === true;
  const { data: allJobs = [], isLoading, mutate } = useSWR(
    "shab-jobs",
    () => fetchJobs(),
    { refreshInterval: 5000 },
  );

  const jobs = allJobs.filter(j => SHAB_JOB_TYPES.has(j.job_type));
  const active = jobs.filter(j => ["running", "queued", "paused"].includes(j.status));
  const finished = jobs.filter(j => !["running", "queued", "paused"].includes(j.status));

  const [triggerLoading, setTriggerLoading] = useState<string | null>(null);
  const [triggerError, setTriggerError] = useState<string | null>(null);

  const [dailyDate, setDailyDate] = useState("");
  const [backfillFrom, setBackfillFrom] = useState("");
  const [backfillTo, setBackfillTo] = useState("");

  async function triggerDaily() {
    setTriggerLoading("daily");
    setTriggerError(null);
    try {
      await triggerJob("collection/shab-daily", {
        date: dailyDate || undefined,
        request_delay: 0.15,
      });
      await mutate();
      router.push("/app/jobs");
    } catch (e) {
      setTriggerError(e instanceof Error ? e.message : "Failed to trigger job");
    } finally {
      setTriggerLoading(null);
    }
  }

  async function triggerBackfill() {
    if (!backfillFrom) { setTriggerError("From date is required"); return; }
    setTriggerLoading("backfill");
    setTriggerError(null);
    try {
      await triggerJob("collection/shab-backfill", {
        from_date: backfillFrom,
        to_date: backfillTo || undefined,
        request_delay: 0.15,
      });
      await mutate();
      router.push("/app/jobs");
    } catch (e) {
      setTriggerError(e instanceof Error ? e.message : "Failed to trigger job");
    } finally {
      setTriggerLoading(null);
    }
  }

  const tabCls = (t: Tab) =>
    `px-4 py-2 text-sm font-medium rounded-lg transition-colors ${
      activeTab === t
        ? "bg-white text-slate-900 shadow-sm"
        : "text-slate-500 hover:text-slate-700"
    }`;

  return (
    <div className="p-6 max-w-5xl mx-auto space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold text-slate-900 flex items-center gap-2">
            <Calendar size={18} className="text-blue-500" /> SHAB
          </h1>
          <p className="text-sm text-slate-500 mt-0.5">
            Swiss Official Gazette — HR01 (new), HR02 (mutations), HR03 (deletions).
          </p>
        </div>
        {activeTab === "imports" && (
          <button
            onClick={() => void mutate()}
            className="flex items-center gap-1.5 text-sm text-slate-500 hover:text-slate-700 px-3 py-1.5 rounded-lg border border-slate-200 hover:bg-slate-50 transition-colors"
          >
            <RefreshCw size={14} /> Refresh
          </button>
        )}
      </div>

      {/* Tab switcher */}
      <div className="inline-flex items-center gap-1 bg-slate-100 rounded-xl p-1">
        <button className={tabCls("browse")} onClick={() => setActiveTab("browse")}>
          Browse changes
        </button>
        <button className={tabCls("imports")} onClick={() => setActiveTab("imports")}>
          Import jobs
        </button>
      </div>

      {/* Browse tab */}
      {activeTab === "browse" && <BrowseTab />}

      {/* Imports tab */}
      {activeTab === "imports" && (
        <div className="space-y-6">
          {triggerError && (
            <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700 flex items-start gap-2">
              <span className="font-semibold shrink-0">Error:</span>
              <span>{triggerError}</span>
              <button onClick={() => setTriggerError(null)} className="ml-auto text-red-400 hover:text-red-600 shrink-0">✕</button>
            </div>
          )}

          {!isSuperadmin && (
            <div className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-700">
              Triggering imports requires superadmin access. Daily imports run automatically at 02:00 Zurich time.
            </div>
          )}

          {isSuperadmin && (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              {/* Daily */}
              <div className="rounded-2xl border border-slate-200 bg-white p-5 space-y-4">
                <div>
                  <h2 className="text-sm font-semibold text-slate-800">Daily import</h2>
                  <p className="text-xs text-slate-400 mt-0.5">
                    Import all SHAB HR publications for a single day. Leave date empty to import yesterday.
                  </p>
                </div>
                <div>
                  <label className="block text-xs font-medium text-slate-600 mb-1">Date (YYYY-MM-DD)</label>
                  <input
                    type="date"
                    value={dailyDate}
                    onChange={e => setDailyDate(e.target.value)}
                    className={inputCls}
                  />
                </div>
                <button
                  onClick={() => void triggerDaily()}
                  disabled={triggerLoading !== null}
                  className="flex items-center gap-2 bg-blue-600 hover:bg-blue-700 disabled:opacity-60 text-white px-4 py-2 rounded-lg text-sm font-medium transition-colors"
                >
                  {triggerLoading === "daily" ? <Loader2 size={14} className="animate-spin" /> : <Calendar size={14} />}
                  {triggerLoading === "daily" ? "Queuing…" : "Run daily import"}
                </button>
              </div>

              {/* Backfill */}
              <div className="rounded-2xl border border-slate-200 bg-white p-5 space-y-4">
                <div>
                  <h2 className="text-sm font-semibold text-slate-800">Historical backfill</h2>
                  <p className="text-xs text-slate-400 mt-0.5">
                    Import all SHAB publications across a date range. Leave &quot;to&quot; empty to backfill through yesterday.
                  </p>
                </div>
                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <label className="block text-xs font-medium text-slate-600 mb-1">From date</label>
                    <input type="date" value={backfillFrom} onChange={e => setBackfillFrom(e.target.value)} className={inputCls} required />
                  </div>
                  <div>
                    <label className="block text-xs font-medium text-slate-600 mb-1">To date (optional)</label>
                    <input type="date" value={backfillTo} onChange={e => setBackfillTo(e.target.value)} className={inputCls} />
                  </div>
                </div>
                <button
                  onClick={() => void triggerBackfill()}
                  disabled={triggerLoading !== null || !backfillFrom}
                  className="flex items-center gap-2 bg-blue-600 hover:bg-blue-700 disabled:opacity-60 text-white px-4 py-2 rounded-lg text-sm font-medium transition-colors"
                >
                  {triggerLoading === "backfill" ? <Loader2 size={14} className="animate-spin" /> : <Calendar size={14} />}
                  {triggerLoading === "backfill" ? "Queuing…" : "Run backfill"}
                </button>
              </div>
            </div>
          )}

          {/* Active jobs */}
          {(isLoading || active.length > 0) && (
            <section className="space-y-3">
              <h2 className="text-sm font-semibold text-slate-700">
                Active {active.length > 0 && <span className="ml-1 text-blue-600">({active.length})</span>}
              </h2>
              {isLoading && <Loader2 size={16} className="animate-spin text-slate-300" />}
              {active.map(j => <JobCard key={j.id} job={j} />)}
            </section>
          )}

          {/* History */}
          <section className="space-y-3">
            <h2 className="text-sm font-semibold text-slate-700">
              Import history {finished.length > 0 && <span className="text-slate-400 font-normal ml-1">({finished.length} jobs)</span>}
            </h2>
            {!isLoading && finished.length === 0 && (
              <p className="text-sm text-slate-400">No SHAB imports yet. The daily job runs automatically at 02:00 Zurich time.</p>
            )}
            <div className="space-y-2">
              {finished.map(j => <JobCard key={j.id} job={j} />)}
            </div>
          </section>
        </div>
      )}
    </div>
  );
}
