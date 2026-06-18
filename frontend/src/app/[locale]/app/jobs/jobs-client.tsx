"use client";
import Link from "next/link";
import { useState, useEffect, useRef } from "react";
import useSWR from "swr"; // still used for CSV export status
import { CheckCircle2, XCircle, Clock, Loader2, PauseCircle, Play, Square, ChevronDown, ChevronUp, RefreshCw, Download, FileText } from "lucide-react";
import { cn } from "@/lib/utils";
import { cancelJob, fetchCSVExportStatus, fetchJobEvents, fetchJobs, pauseJob, resumeJob } from "@/lib/api";
import type { Job, JobEvent } from "@/lib/types";
import { useI18n } from "@/i18n/context";


function useJobsSSE(): { jobs: Job[]; setJobs: (j: Job[]) => void; isLoading: boolean; reload: () => void } {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const esRef = useRef<EventSource | null>(null);

  function connect() {
    esRef.current?.close();
    const es = new EventSource("/api/v1/jobs/stream/active", { withCredentials: true });
    es.onmessage = (event) => {
      try {
        setJobs(JSON.parse(event.data) as Job[]);
        setIsLoading(false);
      } catch {
        // SSE heartbeat comments (": heartbeat") are never dispatched here;
        // only guard against malformed data.
      }
    };
    es.onerror = () => {
      setIsLoading(false);
      // Browser auto-reconnects EventSource after ~3 s — no manual retry needed.
    };
    esRef.current = es;
  }

  useEffect(() => {
    connect();
    return () => { esRef.current?.close(); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return { jobs, setJobs, isLoading, reload: connect };
}

function statusIcon(status: Job["status"]) {
  switch (status) {
    case "completed": return <CheckCircle2 size={14} className="text-emerald-500" />;
    case "failed": return <XCircle size={14} className="text-red-500" />;
    case "cancelled": return <XCircle size={14} className="text-slate-400" />;
    case "running": return <Loader2 size={14} className="text-blue-500 animate-spin" />;
    case "paused": return <PauseCircle size={14} className="text-amber-500" />;
    case "waiting_external": return <Clock size={14} className="text-violet-500 animate-pulse" />;
    default: return <Clock size={14} className="text-slate-400" />;
  }
}

function statusBadge(status: Job["status"]) {
  const base = "inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium";
  switch (status) {
    case "completed": return <span className={cn(base, "bg-emerald-50 text-emerald-700")}>{statusIcon(status)} completed</span>;
    case "failed": return <span className={cn(base, "bg-red-50 text-red-700")}>{statusIcon(status)} failed</span>;
    case "cancelled": return <span className={cn(base, "bg-slate-100 text-slate-500")}>{statusIcon(status)} cancelled</span>;
    case "running": return <span className={cn(base, "bg-blue-50 text-blue-700")}>{statusIcon(status)} running</span>;
    case "paused": return <span className={cn(base, "bg-amber-50 text-amber-700")}>{statusIcon(status)} paused</span>;
    case "waiting_external": return <span className={cn(base, "bg-violet-50 text-violet-700")}>{statusIcon(status)} waiting for Anthropic</span>;
    default: return <span className={cn(base, "bg-slate-100 text-slate-600")}>{statusIcon(status)} queued</span>;
  }
}

function ProgressBar({ done, total }: { done: number | null; total: number | null }) {
  if (!total || !done) return null;
  const pct = Math.min(100, Math.round((done / total) * 100));
  return (
    <div className="mt-1.5 h-1.5 bg-slate-100 rounded-full overflow-hidden">
      <div className="h-full bg-blue-500 rounded-full transition-all" style={{ width: `${pct}%` }} />
    </div>
  );
}

function JobEvents({ jobId }: { jobId: number }) {
  const { data: events = [] } = useSWR<JobEvent[]>(`events-${jobId}`, () => fetchJobEvents(jobId), { refreshInterval: 3000 });
  const [verbose, setVerbose] = useState(false);
  const visible = verbose ? events : events.filter(e => e.level !== "debug");
  return (
    <div className="mt-3">
      <label className="flex items-center gap-1.5 text-xs text-slate-400 mb-1 cursor-pointer select-none w-fit">
        <input type="checkbox" checked={verbose} onChange={e => setVerbose(e.target.checked)}
          className="rounded border-slate-300 text-blue-600 focus:ring-blue-300" />
        Verbose
      </label>
      <div className="max-h-48 overflow-y-auto bg-slate-950 rounded-lg p-3 font-mono text-xs space-y-0.5">
        {visible.length === 0 && <p className="text-slate-500">No events</p>}
        {[...visible].reverse().map(e => (
          <div key={e.id} className={cn(
            "leading-relaxed",
            e.level === "error" ? "text-red-400" : e.level === "warn" ? "text-amber-400" : "text-slate-300"
          )}>
            <span className="text-slate-600 mr-2">{new Date(e.created_at).toLocaleTimeString("de-CH")}</span>
            <span className={cn(
              "mr-2 uppercase tracking-wider text-[10px]",
              e.level === "error" ? "text-red-500" : e.level === "warn" ? "text-amber-500" : e.level === "debug" ? "text-slate-600" : "text-slate-400"
            )}>[{e.level}]</span>
            {e.message}
          </div>
        ))}
      </div>
    </div>
  );
}

function JobRow({ job, onAction }: { job: Job; onAction: (updated?: Job[]) => void }) {
  const [expanded, setExpanded] = useState(false);
  const active = job.status === "running" || job.status === "queued" || job.status === "paused" || job.status === "waiting_external";

  async function doCancel() {
    await cancelJob(job.id);
    // Optimistic refresh so the UI updates before the next SSE push
    onAction(await fetchJobs());
  }
  async function doPause() {
    await pauseJob(job.id);
    onAction(await fetchJobs());
  }
  async function doResume() {
    await resumeJob(job.id);
    onAction(await fetchJobs());
  }

  return (
    <div className={cn(
      "border rounded-xl p-4 transition-colors",
      job.status === "running" ? "border-blue-200 bg-blue-50/30" :
      job.status === "failed" ? "border-red-200 bg-red-50/30" :
      job.status === "paused" ? "border-amber-200 bg-amber-50/30" :
      job.status === "waiting_external" ? "border-violet-200 bg-violet-50/30" :
      "border-slate-200 bg-white"
    )}>
      <div className="flex items-start gap-3">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            {statusBadge(job.status)}
            <span className="text-xs text-slate-400 font-mono">#{job.id}</span>
            <span className="text-xs text-slate-500 bg-slate-100 px-1.5 py-0.5 rounded">{job.job_type}</span>
          </div>
          <p className="mt-1 font-medium text-slate-800 text-sm">{job.label}</p>
          {job.message && (
            <p className="mt-0.5 text-xs text-slate-500 truncate">{job.message}</p>
          )}
          <ProgressBar done={job.progress_done} total={job.progress_total} />
          {job.progress_done != null && job.progress_total != null && (
            <p className="mt-0.5 text-xs text-slate-400">{job.progress_done} / {job.progress_total}</p>
          )}
        </div>
        <div className="flex items-center gap-1.5 shrink-0">
          {job.status === "running" && (
            <button onClick={doPause} className="p-1.5 rounded-lg hover:bg-amber-50 text-amber-500 hover:text-amber-600 transition-colors" title="Pause">
              <PauseCircle size={16} />
            </button>
          )}
          {job.status === "paused" && (
            <button onClick={doResume} className="p-1.5 rounded-lg hover:bg-blue-50 text-blue-500 hover:text-blue-600 transition-colors" title="Resume">
              <Play size={16} />
            </button>
          )}
          {active && (
            <button onClick={doCancel} className="p-1.5 rounded-lg hover:bg-red-50 text-red-400 hover:text-red-500 transition-colors" title="Cancel">
              <Square size={16} />
            </button>
          )}
          <button onClick={() => setExpanded(v => !v)} className="p-1.5 rounded-lg hover:bg-slate-100 text-slate-400 transition-colors">
            {expanded ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
          </button>
        </div>
      </div>
      <div className="mt-2 flex gap-3 text-xs text-slate-400">
        <span>Created {new Date(job.created_at).toLocaleString("de-CH")}</span>
        {job.started_at && <span>Started {new Date(job.started_at).toLocaleString("de-CH")}</span>}
        {job.finished_at && <span>Finished {new Date(job.finished_at).toLocaleString("de-CH")}</span>}
      </div>
      {job.error && (
        <details className="mt-2">
          <summary className="text-xs text-red-500 cursor-pointer">Show error</summary>
          <pre className="mt-1 text-xs text-red-400 bg-red-50 rounded-lg p-2 overflow-auto max-h-32 whitespace-pre-wrap">{job.error}</pre>
        </details>
      )}
      {expanded && <JobEvents jobId={job.id} />}
    </div>
  );
}

export function JobsClient() {
  const { dict } = useI18n();
  const { jobs, setJobs, isLoading, reload: reloadJobs } = useJobsSSE();
  const { data: exportStatus, mutate: reloadExportStatus, isLoading: loadingExportStatus } = useSWR(
    "csv-export-status",
    fetchCSVExportStatus,
    { refreshInterval: 5000 },
  );

  const active = jobs.filter(j => ["running", "queued", "paused", "waiting_external"].includes(j.status));
  const finished = jobs.filter(j => !["running", "queued", "paused", "waiting_external"].includes(j.status));
  const csvJob = exportStatus?.job;

  function formatExpiry(iso: string) {
    const d = new Date(iso);
    return d.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
  }

  return (
    <div className="p-6 max-w-4xl mx-auto space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold text-slate-900">{dict.app.jobs.title}</h1>
          <p className="text-sm text-slate-500 mt-0.5">{active.length} active · {finished.length} finished</p>
        </div>
        <button
          onClick={() => reloadJobs()}
          className="flex items-center gap-1.5 text-sm text-slate-500 hover:text-slate-700 px-3 py-1.5 rounded-lg border border-slate-200 hover:bg-slate-50 transition-colors"
        >
          <RefreshCw size={14} /> Refresh
        </button>
      </div>

      {isLoading && <p className="text-slate-400 text-sm">Loading…</p>}

      

      <section className="rounded-xl border border-slate-200 bg-white p-4 space-y-3">
        <div className="flex items-start justify-between gap-4">
          <div>
            <p className="text-sm font-medium text-slate-800 flex items-center gap-1.5">
              <FileText size={14} className="text-slate-400" /> CSV exports
            </p>
            <p className="text-xs text-slate-500 mt-0.5">
              Queue exports from Search/Explorer/Dashboard and download them here.
            </p>
          </div>
          <button
            onClick={() => reloadExportStatus()}
            disabled={loadingExportStatus}
            className="text-xs text-slate-400 hover:text-slate-600 transition-colors shrink-0"
          >
            {loadingExportStatus ? <Loader2 size={12} className="animate-spin" /> : "Refresh"}
          </button>
        </div>

        {!csvJob && (
          <p className="text-xs text-slate-400">No export queued yet.</p>
        )}

        {csvJob && (
          <div className="border-t border-slate-100 pt-3 space-y-2">
            <div className="flex items-center gap-2 text-xs flex-wrap">
              {statusBadge(csvJob.status)}
              {csvJob.progress_done != null && csvJob.progress_total != null && csvJob.status === "running" && (
                <span className="text-slate-500">{csvJob.progress_done.toLocaleString()} / {csvJob.progress_total.toLocaleString()} rows</span>
              )}
              {exportStatus?.row_count != null && csvJob.status === "completed" && (
                <span className="text-slate-500">{exportStatus.row_count.toLocaleString()} rows</span>
              )}
              {exportStatus?.expires_at && (
                <span className="text-slate-400">expires {formatExpiry(exportStatus.expires_at)}</span>
              )}
            </div>

            {csvJob.message && <p className="text-xs text-slate-500">{csvJob.message}</p>}

            {exportStatus?.download_url && (
              <a
                href={exportStatus.download_url}
                download="helvex_export.csv"
                className="inline-flex items-center gap-1.5 text-xs bg-blue-600 hover:bg-blue-700 text-white px-3 py-1.5 rounded-lg font-medium transition-colors"
              >
                <Download size={12} /> Download CSV
              </a>
            )}
            {exportStatus?.capped && exportStatus.upgrade_to && (
              <div className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
                Export capped at {exportStatus.tier_limit?.toLocaleString()} rows
                {exportStatus.total_matching != null && ` — ${exportStatus.total_matching.toLocaleString()} matched your filters`}.{" "}
                <Link href="/app/billing" className="underline font-medium capitalize">
                  Upgrade to {exportStatus.upgrade_to}
                </Link>{" "}for more.
              </div>
            )}

            {csvJob.status === "failed" && csvJob.error && (
              <p className="text-xs text-red-600 font-mono truncate">{csvJob.error}</p>
            )}
          </div>
        )}
      </section>

      {active.length > 0 && (
        <section>
          <h2 className="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-3">Active</h2>
          <div className="space-y-3">
            {active.map(j => <JobRow key={j.id} job={j} onAction={(updated) => { if (updated) setJobs(updated); }} />)}
          </div>
        </section>
      )}

      {finished.length > 0 && (
        <section>
          <h2 className="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-3">History</h2>
          <div className="space-y-2">
            {finished.map(j => <JobRow key={j.id} job={j} onAction={(updated) => { if (updated) setJobs(updated); }} />)}
          </div>
        </section>
      )}

      {!isLoading && jobs.length === 0 && (
        <div className="text-center py-16 text-slate-400">
          <Clock size={32} className="mx-auto mb-3 opacity-40" />
          <p>No jobs yet</p>
        </div>
      )}
    </div>
  );
}
