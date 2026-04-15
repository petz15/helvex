"use client";
import useSWR from "swr";
import { BarChart2, Building2, TrendingUp, Users, Loader2, RefreshCw } from "lucide-react";
import { fetchAdminAnalytics, type AdminAnalytics } from "@/lib/api";

const TIER_ORDER = ["free", "simple", "explorer", "researcher", "strategist", "custom", "superadmin"];

const ACTION_LABELS: Record<string, string> = {
  batch_llm:          "AI scoring (batch)",
  immediate_llm:      "AI scoring (instant)",
  web_search:         "Web search",
  flex_rescore:       "Flex rescore",
  recluster:          "Recluster",
  bulk_export_basic:  "Export (basic)",
  bulk_export_detail: "Export (detailed)",
};

function KpiCard({ label, value, sub, icon: Icon }: {
  label: string;
  value: string | number;
  sub?: string;
  icon: React.ElementType;
}) {
  return (
    <div className="bg-white rounded-xl border border-slate-200 p-4 flex items-start gap-3">
      <div className="mt-0.5 p-2 rounded-lg bg-purple-50">
        <Icon size={16} className="text-purple-600" />
      </div>
      <div>
        <p className="text-xs text-slate-500">{label}</p>
        <p className="text-xl font-semibold text-slate-900 tabular-nums">{value}</p>
        {sub && <p className="text-xs text-slate-400 mt-0.5">{sub}</p>}
      </div>
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="bg-white rounded-xl border border-slate-200 overflow-hidden">
      <div className="px-4 py-3 border-b border-slate-100">
        <h2 className="text-sm font-semibold text-slate-700">{title}</h2>
      </div>
      <div className="p-4">{children}</div>
    </div>
  );
}

export default function AnalyticsClient() {
  const { data, isLoading, error, mutate } = useSWR<AdminAnalytics>("admin-analytics", fetchAdminAnalytics);

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-64 text-slate-400">
        <Loader2 size={20} className="animate-spin" />
      </div>
    );
  }

  if (error || !data) {
    return (
      <div className="p-8 text-sm text-red-500">
        Failed to load analytics.{" "}
        <button onClick={() => void mutate()} className="underline">Retry</button>
      </div>
    );
  }

  const tierEntries = TIER_ORDER
    .filter(t => data.orgs_by_tier[t])
    .map(t => [t, data.orgs_by_tier[t]] as [string, number]);

  const jobEntries = Object.entries(data.job_volume_by_type)
    .sort((a, b) => b[1] - a[1]);
  const maxJobs = jobEntries.length > 0 ? jobEntries[0][1] : 1;

  return (
    <div className="p-6 max-w-5xl space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-lg font-semibold text-slate-800">Platform Analytics</h1>
          <p className="text-xs text-slate-400 mt-0.5">Last 30 days · superadmin only</p>
        </div>
        <button
          onClick={() => void mutate()}
          className="flex items-center gap-1.5 text-xs text-slate-500 hover:text-slate-700 px-2.5 py-1.5 rounded-lg border border-slate-200 hover:bg-slate-50 transition-colors"
        >
          <RefreshCw size={12} /> Refresh
        </button>
      </div>

      {/* KPI row */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <KpiCard label="Total orgs" value={data.total_orgs.toLocaleString()} icon={Building2} />
        <KpiCard label="New orgs (30d)" value={data.new_orgs_30d} icon={TrendingUp} />
        <KpiCard label="Active orgs (30d)" value={data.active_orgs_30d} icon={Users} />
        <KpiCard
          label="MRR estimate"
          value={`CHF ${data.mrr_estimate_chf.toFixed(0)}`}
          sub="subscription revenue only"
          icon={BarChart2}
        />
      </div>

      <div className="grid md:grid-cols-2 gap-4">
        {/* Orgs by tier */}
        <Section title="Orgs by tier">
          <table className="w-full text-sm">
            <tbody>
              {tierEntries.map(([tier, count]) => (
                <tr key={tier} className="border-b border-slate-50 last:border-0">
                  <td className="py-1.5 capitalize text-slate-700">{tier}</td>
                  <td className="py-1.5 text-right font-mono font-medium text-slate-900">{count}</td>
                  <td className="py-1.5 pl-3 w-24">
                    <div className="h-1.5 bg-slate-100 rounded-full overflow-hidden">
                      <div
                        className="h-full bg-purple-400 rounded-full"
                        style={{ width: `${Math.round((count / data.total_orgs) * 100)}%` }}
                      />
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Section>

        {/* Top credit consumers */}
        <Section title="Top credit consumers (30d)">
          {data.top_credit_consumers.length === 0 ? (
            <p className="text-xs text-slate-400 text-center py-4">No credit consumption in this period.</p>
          ) : (
            <table className="w-full text-sm">
              <thead>
                <tr className="text-xs text-slate-500 border-b border-slate-200">
                  <th className="text-left pb-2">Org</th>
                  <th className="text-right pb-2">Credits</th>
                  <th className="text-right pb-2">CHF</th>
                </tr>
              </thead>
              <tbody>
                {data.top_credit_consumers.map((row) => (
                  <tr key={row.org_id} className="border-b border-slate-50 last:border-0">
                    <td className="py-1.5 text-slate-700 truncate max-w-[140px]">{row.org_name}</td>
                    <td className="py-1.5 text-right font-mono text-slate-800">{row.net_spend_credits.toLocaleString()}</td>
                    <td className="py-1.5 text-right font-mono text-slate-500">{row.net_spend_chf.toFixed(2)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Section>
      </div>

      {/* Job volume */}
      <Section title="Job volume by type (30d)">
        {jobEntries.length === 0 ? (
          <p className="text-xs text-slate-400 text-center py-4">No jobs in this period.</p>
        ) : (
          <div className="space-y-2">
            {jobEntries.map(([type, count]) => (
              <div key={type} className="flex items-center gap-3">
                <span className="text-xs font-mono text-slate-600 w-44 shrink-0 truncate">
                  {ACTION_LABELS[type] ?? type}
                </span>
                <div className="flex-1 h-2 bg-slate-100 rounded-full overflow-hidden">
                  <div
                    className="h-full bg-blue-400 rounded-full"
                    style={{ width: `${Math.round((count / maxJobs) * 100)}%` }}
                  />
                </div>
                <span className="text-xs font-mono text-slate-700 w-10 text-right shrink-0">{count}</span>
              </div>
            ))}
          </div>
        )}
      </Section>

      <p className="text-xs text-slate-400">
        Total companies in DB: <span className="font-mono">{data.total_companies_db.toLocaleString()}</span>
      </p>
    </div>
  );
}
