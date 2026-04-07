"use client";
import { useState } from "react";
import useSWR from "swr";
import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  CheckCircle2, XCircle, Clock, Loader2, PauseCircle,
  RefreshCw, Calendar, ArrowUpRight,
} from "lucide-react";
import { fetchJobs, triggerJob } from "@/lib/api";
import type { Job } from "@/lib/types";

// ── helpers ───────────────────────────────────────────────────────────────────

const SHAB_JOB_TYPES = new Set(["shab_daily", "shab_backfill"]);

function fmtDate(iso: string) {
  return new Date(iso).toLocaleString("de-CH", {
    day: "2-digit", month: "short", year: "numeric",
    hour: "2-digit", minute: "2-digit",
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

function statsFromJob(job: Job): { new: number; updated: number; deleted: number; errors: number } | null {
  try {
    if (!job.message) return null;
    // Parse stats from the job message: "Done — X new, Y updated, Z deleted, W skipped, E errors (N publications fetched)"
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

// ── sub-components ────────────────────────────────────────────────────────────

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
          {/* Progress bar */}
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
        <Link
          href="/app/jobs"
          className="text-xs text-blue-600 hover:underline shrink-0 flex items-center gap-0.5"
        >
          Details <ArrowUpRight size={11} />
        </Link>
      </div>

      {/* Stats row */}
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

// ── main component ────────────────────────────────────────────────────────────

export function ShabClient() {
  const router = useRouter();
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

  // Form state for daily
  const [dailyDate, setDailyDate] = useState("");
  // Form state for backfill
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

  return (
    <div className="p-6 max-w-4xl mx-auto space-y-8">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold text-slate-900 flex items-center gap-2">
            <Calendar size={18} className="text-blue-500" /> SHAB Imports
          </h1>
          <p className="text-sm text-slate-500 mt-0.5">
            Swiss Official Gazette — HR01 (new), HR02 (mutations), HR03 (deletions).
            Daily imports run automatically at 02:00 Zurich time.
          </p>
        </div>
        <button
          onClick={() => void mutate()}
          className="flex items-center gap-1.5 text-sm text-slate-500 hover:text-slate-700 px-3 py-1.5 rounded-lg border border-slate-200 hover:bg-slate-50 transition-colors"
        >
          <RefreshCw size={14} /> Refresh
        </button>
      </div>

      {triggerError && (
        <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700 flex items-start gap-2">
          <span className="font-semibold shrink-0">Error:</span>
          <span>{triggerError}</span>
          <button onClick={() => setTriggerError(null)} className="ml-auto text-red-400 hover:text-red-600 shrink-0">✕</button>
        </div>
      )}

      {/* Trigger panels */}
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
              placeholder="Leave empty for yesterday"
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
              <input
                type="date"
                value={backfillFrom}
                onChange={e => setBackfillFrom(e.target.value)}
                className={inputCls}
                required
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-slate-600 mb-1">To date (optional)</label>
              <input
                type="date"
                value={backfillTo}
                onChange={e => setBackfillTo(e.target.value)}
                className={inputCls}
              />
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
  );
}
