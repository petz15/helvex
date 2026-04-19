"use client";
import { useState } from "react";
import useSWR from "swr";
import { Activity, Users, Zap, RefreshCw, Loader2, ChevronLeft, ChevronRight } from "lucide-react";
import {
  fetchAdminActivityLogs,
  fetchAdminActivitySummary,
  type ActivityLogEntry,
  type ActivityLogSummary,
  type AdminPage,
} from "@/lib/api";

// ── Action labels & colours ───────────────────────────────────────────────────

const ACTION_META: Record<string, { label: string; colour: string }> = {
  user_registered:      { label: "Registered",        colour: "bg-green-100 text-green-800" },
  user_login:           { label: "Login",              colour: "bg-blue-100 text-blue-800" },
  email_verified:       { label: "Email verified",     colour: "bg-teal-100 text-teal-800" },
  password_changed:     { label: "Password changed",   colour: "bg-yellow-100 text-yellow-800" },
  email_changed:        { label: "Email changed",      colour: "bg-yellow-100 text-yellow-800" },
  company_viewed:       { label: "Company viewed",     colour: "bg-slate-100 text-slate-700" },
  company_updated:      { label: "Company updated",    colour: "bg-purple-100 text-purple-800" },
  company_deleted:      { label: "Company deleted",    colour: "bg-red-100 text-red-800" },
  company_exported:     { label: "CSV export",         colour: "bg-indigo-100 text-indigo-800" },
  company_bulk_updated: { label: "Bulk update",        colour: "bg-orange-100 text-orange-800" },
  company_bulk_tagged:  { label: "Bulk tag",           colour: "bg-orange-100 text-orange-800" },
  company_website_set:  { label: "Website set",        colour: "bg-cyan-100 text-cyan-800" },
  view_created:         { label: "View saved",         colour: "bg-violet-100 text-violet-800" },
  view_deleted:         { label: "View deleted",       colour: "bg-red-100 text-red-800" },
  view_alert_toggled:   { label: "Alert toggled",      colour: "bg-amber-100 text-amber-800" },
  note_created:         { label: "Note created",       colour: "bg-lime-100 text-lime-800" },
  note_deleted:         { label: "Note deleted",       colour: "bg-red-100 text-red-800" },
  org_state_updated:    { label: "Org state updated",  colour: "bg-purple-100 text-purple-800" },
  org_member_invited:   { label: "Member invited",     colour: "bg-green-100 text-green-800" },
  org_member_removed:   { label: "Member removed",     colour: "bg-red-100 text-red-800" },
  org_created:          { label: "Org created",        colour: "bg-green-100 text-green-800" },
  admin_user_updated:   { label: "Admin: user update", colour: "bg-rose-100 text-rose-800" },
  admin_credit_granted: { label: "Admin: credit grant",colour: "bg-emerald-100 text-emerald-800" },
  admin_credit_deducted:{ label: "Admin: credit deduct",colour: "bg-rose-100 text-rose-800" },
  admin_org_updated:    { label: "Admin: org update",  colour: "bg-rose-100 text-rose-800" },
  admin_org_deleted:    { label: "Admin: org delete",  colour: "bg-rose-100 text-rose-800" },
  job_enqueued:         { label: "Job enqueued",       colour: "bg-sky-100 text-sky-800" },
  job_cancelled:        { label: "Job cancelled",      colour: "bg-slate-100 text-slate-600" },
  page_viewed:          { label: "Page view",          colour: "bg-slate-100 text-slate-500" },
  web_search_run:       { label: "Web search",         colour: "bg-cyan-100 text-cyan-800" },
};

function ActionBadge({ action }: { action: string }) {
  const m = ACTION_META[action];
  return (
    <span className={`inline-block px-1.5 py-0.5 rounded text-[11px] font-medium ${m?.colour ?? "bg-slate-100 text-slate-600"}`}>
      {m?.label ?? action}
    </span>
  );
}

// ── KPI card ─────────────────────────────────────────────────────────────────

function KpiCard({ label, value, icon: Icon }: { label: string; value: string | number; icon: React.ElementType }) {
  return (
    <div className="bg-white rounded-xl border border-slate-200 p-4 flex items-start gap-3">
      <div className="mt-0.5 p-2 rounded-lg bg-purple-50">
        <Icon size={16} className="text-purple-600" />
      </div>
      <div>
        <p className="text-xs text-slate-500">{label}</p>
        <p className="text-xl font-semibold text-slate-900 tabular-nums">{value}</p>
      </div>
    </div>
  );
}

// ── Filters ───────────────────────────────────────────────────────────────────

interface Filters {
  action: string;
  resource_type: string;
  page: number;
}

const PAGE_SIZE = 50;

// ── Main component ────────────────────────────────────────────────────────────

export default function ActivityClient() {
  const [filters, setFilters] = useState<Filters>({ action: "", resource_type: "", page: 1 });

  const summaryKey = "admin-activity-summary";
  const { data: summary, isLoading: summaryLoading, mutate: mutateSummary } =
    useSWR<ActivityLogSummary>(summaryKey, () => fetchAdminActivitySummary(30));

  const logsKey = ["admin-activity-logs", filters] as const;
  const { data: logs, isLoading: logsLoading, mutate: mutateLogs } =
    useSWR<AdminPage<ActivityLogEntry>>(logsKey, () =>
      fetchAdminActivityLogs({
        action: filters.action || undefined,
        resource_type: filters.resource_type || undefined,
        page: filters.page,
        page_size: PAGE_SIZE,
      })
    );

  function refresh() {
    void mutateSummary();
    void mutateLogs();
  }

  const totalPages = logs ? Math.ceil(logs.total / PAGE_SIZE) : 1;

  return (
    <div className="p-6 max-w-6xl space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-lg font-semibold text-slate-800">User Activity</h1>
          <p className="text-xs text-slate-400 mt-0.5">Last 30 days · superadmin only</p>
        </div>
        <button
          onClick={refresh}
          className="flex items-center gap-1.5 text-xs text-slate-500 hover:text-slate-700 px-2.5 py-1.5 rounded-lg border border-slate-200 hover:bg-slate-50 transition-colors"
        >
          <RefreshCw size={12} /> Refresh
        </button>
      </div>

      {/* KPIs */}
      {summaryLoading ? (
        <div className="flex items-center gap-2 text-slate-400 text-sm"><Loader2 size={14} className="animate-spin" /> Loading…</div>
      ) : summary ? (
        <div className="grid grid-cols-2 md:grid-cols-3 gap-4">
          <KpiCard label="Total events (30d)" value={summary.total_events.toLocaleString()} icon={Zap} />
          <KpiCard label="Active users (30d)" value={summary.distinct_active_users.toLocaleString()} icon={Users} />
          <KpiCard label="Distinct actions" value={Object.keys(summary.by_action).length} icon={Activity} />
        </div>
      ) : null}

      {/* Action breakdown */}
      {summary && Object.keys(summary.by_action).length > 0 && (
        <div className="bg-white rounded-xl border border-slate-200 overflow-hidden">
          <div className="px-4 py-3 border-b border-slate-100">
            <h2 className="text-sm font-semibold text-slate-700">Events by action (30d)</h2>
          </div>
          <div className="p-4 grid grid-cols-2 md:grid-cols-3 gap-x-8 gap-y-1">
            {Object.entries(summary.by_action)
              .sort((a, b) => b[1] - a[1])
              .map(([action, count]) => (
                <div key={action} className="flex items-center justify-between py-0.5">
                  <ActionBadge action={action} />
                  <span className="text-xs font-mono text-slate-700 ml-2">{count.toLocaleString()}</span>
                </div>
              ))}
          </div>
        </div>
      )}

      {/* Filters */}
      <div className="flex flex-wrap gap-3 items-end">
        <div>
          <label className="block text-xs text-slate-500 mb-1">Action</label>
          <select
            value={filters.action}
            onChange={(e) => setFilters({ ...filters, action: e.target.value, page: 1 })}
            className="text-sm border border-slate-200 rounded-lg px-2 py-1.5 bg-white focus:outline-none focus:ring-2 focus:ring-purple-300"
          >
            <option value="">All actions</option>
            {Object.entries(ACTION_META).map(([slug, { label }]) => (
              <option key={slug} value={slug}>{label}</option>
            ))}
          </select>
        </div>
        <div>
          <label className="block text-xs text-slate-500 mb-1">Resource type</label>
          <select
            value={filters.resource_type}
            onChange={(e) => setFilters({ ...filters, resource_type: e.target.value, page: 1 })}
            className="text-sm border border-slate-200 rounded-lg px-2 py-1.5 bg-white focus:outline-none focus:ring-2 focus:ring-purple-300"
          >
            <option value="">All types</option>
            {["company", "view", "note", "org", "user"].map((t) => (
              <option key={t} value={t}>{t}</option>
            ))}
          </select>
        </div>
        {(filters.action || filters.resource_type) && (
          <button
            onClick={() => setFilters({ action: "", resource_type: "", page: 1 })}
            className="text-xs text-slate-500 hover:text-slate-700 underline pb-1.5"
          >
            Clear filters
          </button>
        )}
      </div>

      {/* Log table */}
      <div className="bg-white rounded-xl border border-slate-200 overflow-hidden">
        <div className="px-4 py-3 border-b border-slate-100 flex items-center justify-between">
          <h2 className="text-sm font-semibold text-slate-700">
            Log entries
            {logs && <span className="ml-2 text-xs font-normal text-slate-400">({logs.total.toLocaleString()} total)</span>}
          </h2>
          {/* Pagination */}
          {logs && totalPages > 1 && (
            <div className="flex items-center gap-2 text-xs text-slate-500">
              <button
                disabled={filters.page <= 1}
                onClick={() => setFilters({ ...filters, page: filters.page - 1 })}
                className="p-0.5 rounded hover:bg-slate-100 disabled:opacity-30"
              >
                <ChevronLeft size={14} />
              </button>
              <span>Page {filters.page} / {totalPages}</span>
              <button
                disabled={filters.page >= totalPages}
                onClick={() => setFilters({ ...filters, page: filters.page + 1 })}
                className="p-0.5 rounded hover:bg-slate-100 disabled:opacity-30"
              >
                <ChevronRight size={14} />
              </button>
            </div>
          )}
        </div>

        {logsLoading ? (
          <div className="flex items-center justify-center h-32 text-slate-400">
            <Loader2 size={20} className="animate-spin" />
          </div>
        ) : !logs?.items.length ? (
          <p className="text-xs text-slate-400 text-center py-10">No activity logged yet.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="text-left text-slate-500 border-b border-slate-100 bg-slate-50">
                  <th className="px-4 py-2 font-medium">When</th>
                  <th className="px-4 py-2 font-medium">User</th>
                  <th className="px-4 py-2 font-medium">Action</th>
                  <th className="px-4 py-2 font-medium">Resource</th>
                  <th className="px-4 py-2 font-medium">Details</th>
                </tr>
              </thead>
              <tbody>
                {logs.items.map((row) => (
                  <tr key={row.id} className="border-b border-slate-50 last:border-0 hover:bg-slate-50/60 transition-colors">
                    <td className="px-4 py-2 text-slate-500 whitespace-nowrap font-mono">
                      {new Date(row.created_at).toLocaleString("de-CH", {
                        day: "2-digit", month: "2-digit", year: "2-digit",
                        hour: "2-digit", minute: "2-digit",
                      })}
                    </td>
                    <td className="px-4 py-2 text-slate-700 max-w-[160px] truncate" title={row.user_email ?? undefined}>
                      {row.user_email ?? <span className="text-slate-400 italic">—</span>}
                    </td>
                    <td className="px-4 py-2">
                      <ActionBadge action={row.action} />
                    </td>
                    <td className="px-4 py-2 text-slate-600">
                      {row.resource_type ? (
                        <span>
                          {row.resource_type}
                          {row.resource_id != null && (
                            <span className="ml-1 text-slate-400 font-mono">#{row.resource_id}</span>
                          )}
                        </span>
                      ) : (
                        <span className="text-slate-300">—</span>
                      )}
                    </td>
                    <td className="px-4 py-2 text-slate-500 max-w-[260px] truncate" title={row.meta ? JSON.stringify(row.meta) : undefined}>
                      {row.meta ? (
                        <MetaSummary meta={row.meta} action={row.action} />
                      ) : (
                        <span className="text-slate-300">—</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}

// ── Meta summary ──────────────────────────────────────────────────────────────

function MetaSummary({ meta, action }: { meta: Record<string, unknown>; action: string }) {
  // Render a short human-readable summary of common meta shapes
  if (action === "company_viewed" && meta.name) return <>{String(meta.name)}</>;
  if (action === "company_deleted" && meta.name) return <>Deleted: {String(meta.name)}</>;
  if (action === "company_updated" && Array.isArray(meta.fields)) return <>Fields: {(meta.fields as string[]).join(", ")}</>;
  if (action === "company_bulk_updated") return <>{String(meta.count ?? "?")} rows · {String(meta.field)}={String(meta.value ?? "null")}</>;
  if (action === "company_bulk_tagged") return <>{String(meta.action)} tag "{String(meta.tag)}" on {String(meta.count ?? "?")} rows</>;
  if (action === "company_exported") return <>Job #{String(meta.job_id ?? "?")}</>;
  if (action === "company_website_set") return <>{String(meta.url ?? "")}</>;
  if (action === "view_created" || action === "view_deleted") return <>"{String(meta.name ?? "")}"</>;
  if (action === "view_alert_toggled") return <>{meta.enabled ? "enabled" : "disabled"} on "{String(meta.view_name ?? "")}"</>;
  if (action === "page_viewed" && meta.path) return <>{String(meta.path)}</>;
  if (action === "web_search_run" && meta.company_id) return <>Company #{String(meta.company_id)}</>;
  if (action === "user_login") return <>via {String(meta.method ?? "")}</>;
  if (action === "user_registered") return <>{String(meta.email ?? "")}</>;
  // fallback: compact JSON
  return <span className="font-mono text-[10px]">{JSON.stringify(meta)}</span>;
}
