"use client";
import { useState, useCallback } from "react";
import useSWR from "swr";
import { Search, Loader2, Shield, ShieldOff, CheckCircle2, XCircle, ChevronLeft, ChevronRight } from "lucide-react";
import {
  fetchAdminStats,
  fetchAdminUsers,
  updateAdminUser,
  type AdminUser,
  type AdminStats,
} from "@/lib/api";

const TIERS = ["free", "starter", "professional", "enterprise"];

const TIER_COLORS: Record<string, string> = {
  free: "bg-slate-100 text-slate-600",
  starter: "bg-blue-100 text-blue-700",
  professional: "bg-indigo-100 text-indigo-700",
  enterprise: "bg-purple-100 text-purple-700",
};

function StatCard({ label, value }: { label: string; value: number }) {
  return (
    <div className="rounded-xl border border-slate-200 bg-white p-4">
      <p className="text-2xl font-bold text-slate-900">{value.toLocaleString()}</p>
      <p className="text-xs text-slate-500 mt-0.5">{label}</p>
    </div>
  );
}

export function UsersAdminClient() {
  const [q, setQ] = useState("");
  const [tierFilter, setTierFilter] = useState("");
  const [page, setPage] = useState(1);
  const [saving, setSaving] = useState<number | null>(null);
  const [banner, setBanner] = useState<{ kind: "success" | "error"; msg: string } | null>(null);

  const PAGE_SIZE = 50;

  const { data: stats } = useSWR<AdminStats>("admin-stats", fetchAdminStats);

  const swrKey = `admin-users-${q}-${tierFilter}-${page}`;
  const { data, mutate } = useSWR(swrKey, () =>
    fetchAdminUsers({ q: q || undefined, tier: tierFilter || undefined, page, page_size: PAGE_SIZE })
  );

  const flash = useCallback((kind: "success" | "error", msg: string) => {
    setBanner({ kind, msg });
    setTimeout(() => setBanner(null), 4000);
  }, []);

  async function patch(userId: number, update: Parameters<typeof updateAdminUser>[1]) {
    setSaving(userId);
    try {
      await updateAdminUser(userId, update);
      await mutate();
      flash("success", "Updated.");
    } catch (e) {
      flash("error", e instanceof Error ? e.message : "Failed");
    } finally {
      setSaving(null);
    }
  }

  const total = data?.total ?? 0;
  const totalPages = Math.ceil(total / PAGE_SIZE);

  return (
    <div className="p-6 space-y-5">
      <div>
        <h1 className="text-xl font-semibold text-slate-900">Users</h1>
        <p className="text-sm text-slate-500 mt-0.5">Manage all platform users</p>
      </div>

      {stats && (
        <div className="grid grid-cols-2 sm:grid-cols-5 gap-3">
          <StatCard label="Total users" value={stats.total_users} />
          <StatCard label="Active" value={stats.active_users} />
          <StatCard label="Verified email" value={stats.verified_users} />
          <StatCard label="In an org" value={stats.users_in_org} />
          <StatCard label="Total orgs" value={stats.total_orgs} />
        </div>
      )}

      {banner && (
        <div className={`rounded-lg border px-4 py-2.5 text-sm ${
          banner.kind === "success"
            ? "border-green-200 bg-green-50 text-green-800"
            : "border-red-200 bg-red-50 text-red-800"
        }`}>
          {banner.msg}
        </div>
      )}

      {/* Filters */}
      <div className="flex gap-3 flex-wrap">
        <div className="relative">
          <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
          <input
            type="search"
            placeholder="Search email…"
            value={q}
            onChange={(e) => { setQ(e.target.value); setPage(1); }}
            className="pl-8 pr-3 py-2 border border-slate-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-300 w-60"
          />
        </div>
        <select
          value={tierFilter}
          onChange={(e) => { setTierFilter(e.target.value); setPage(1); }}
          className="border border-slate-200 rounded-lg text-sm px-3 py-2 focus:outline-none focus:ring-2 focus:ring-blue-300"
        >
          <option value="">All tiers</option>
          {TIERS.map((t) => <option key={t} value={t}>{t}</option>)}
        </select>
      </div>

      {/* Table */}
      <div className="rounded-xl border border-slate-200 overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-slate-50 border-b border-slate-200">
            <tr>
              <th className="text-left px-4 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wide">Email</th>
              <th className="text-left px-4 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wide">Tier</th>
              <th className="text-left px-4 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wide">Org</th>
              <th className="text-left px-4 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wide">Status</th>
              <th className="text-left px-4 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wide">Actions</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {!data && (
              <tr><td colSpan={5} className="text-center py-8 text-slate-400">
                <Loader2 size={20} className="animate-spin mx-auto" />
              </td></tr>
            )}
            {data?.items.map((u: AdminUser) => (
              <tr key={u.id} className="hover:bg-slate-50 transition-colors">
                <td className="px-4 py-3">
                  <div className="flex items-center gap-2">
                    <span className="font-medium text-slate-800">{u.email}</span>
                    {u.is_superadmin && (
                      <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-xs bg-purple-100 text-purple-700 font-medium">
                        <Shield size={10} /> SA
                      </span>
                    )}
                    {!u.email_verified && (
                      <span className="text-xs text-amber-600">unverified</span>
                    )}
                  </div>
                  <div className="text-xs text-slate-400 mt-0.5">id:{u.id} · {u.created_at.slice(0, 10)}</div>
                </td>
                <td className="px-4 py-3">
                  <select
                    value={u.tier}
                    disabled={saving === u.id}
                    onChange={(e) => patch(u.id, { tier: e.target.value })}
                    className={`text-xs font-medium px-2 py-0.5 rounded border-0 cursor-pointer focus:outline-none focus:ring-2 focus:ring-blue-300 ${TIER_COLORS[u.tier] ?? "bg-slate-100 text-slate-600"}`}
                  >
                    {TIERS.map((t) => <option key={t} value={t}>{t}</option>)}
                  </select>
                </td>
                <td className="px-4 py-3 text-slate-600">
                  {u.org_name
                    ? <span>{u.org_name} <span className="text-slate-400 text-xs">({u.org_role})</span></span>
                    : <span className="text-slate-400">—</span>
                  }
                </td>
                <td className="px-4 py-3">
                  <button
                    onClick={() => patch(u.id, { is_active: !u.is_active })}
                    disabled={saving === u.id}
                    title={u.is_active ? "Deactivate" : "Activate"}
                    className="flex items-center gap-1 text-xs disabled:opacity-50"
                  >
                    {saving === u.id
                      ? <Loader2 size={14} className="animate-spin text-slate-400" />
                      : u.is_active
                        ? <CheckCircle2 size={14} className="text-green-600" />
                        : <XCircle size={14} className="text-red-500" />
                    }
                    <span className={u.is_active ? "text-green-700" : "text-red-600"}>
                      {u.is_active ? "Active" : "Inactive"}
                    </span>
                  </button>
                </td>
                <td className="px-4 py-3">
                  <button
                    onClick={() => patch(u.id, { is_superadmin: !u.is_superadmin })}
                    disabled={saving === u.id}
                    title={u.is_superadmin ? "Revoke superadmin" : "Grant superadmin"}
                    className={`flex items-center gap-1 text-xs transition-colors disabled:opacity-50 ${
                      u.is_superadmin
                        ? "text-purple-600 hover:text-red-600"
                        : "text-slate-400 hover:text-purple-600"
                    }`}
                  >
                    {u.is_superadmin ? <ShieldOff size={13} /> : <Shield size={13} />}
                    {u.is_superadmin ? "Revoke SA" : "Grant SA"}
                  </button>
                </td>
              </tr>
            ))}
            {data?.items.length === 0 && (
              <tr><td colSpan={5} className="text-center py-8 text-slate-400 text-sm">No users found</td></tr>
            )}
          </tbody>
        </table>
      </div>

      {/* Pagination */}
      {totalPages > 1 && (
        <div className="flex items-center justify-between text-sm text-slate-500">
          <span>{total} users · page {page}/{totalPages}</span>
          <div className="flex gap-1">
            <button
              onClick={() => setPage((p) => Math.max(1, p - 1))}
              disabled={page === 1}
              className="p-1.5 rounded hover:bg-slate-100 disabled:opacity-40"
            >
              <ChevronLeft size={16} />
            </button>
            <button
              onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
              disabled={page === totalPages}
              className="p-1.5 rounded hover:bg-slate-100 disabled:opacity-40"
            >
              <ChevronRight size={16} />
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
