"use client";
import { useState, useCallback } from "react";
import useSWR from "swr";
import { Search, Loader2, Trash2, ChevronLeft, ChevronRight, ExternalLink } from "lucide-react";
import {
  fetchAdminOrgs,
  updateAdminOrg,
  deleteAdminOrg,
  type AdminOrg,
} from "@/lib/api";

const TIERS = ["free", "starter", "professional", "enterprise"];

const TIER_COLORS: Record<string, string> = {
  free: "bg-slate-100 text-slate-600",
  starter: "bg-blue-100 text-blue-700",
  professional: "bg-indigo-100 text-indigo-700",
  enterprise: "bg-purple-100 text-purple-700",
};

export function OrgsAdminClient() {
  const [q, setQ] = useState("");
  const [tierFilter, setTierFilter] = useState("");
  const [page, setPage] = useState(1);
  const [saving, setSaving] = useState<number | null>(null);
  const [deleting, setDeleting] = useState<number | null>(null);
  const [banner, setBanner] = useState<{ kind: "success" | "error"; msg: string } | null>(null);

  const PAGE_SIZE = 50;

  const swrKey = `admin-orgs-${q}-${tierFilter}-${page}`;
  const { data, mutate } = useSWR(swrKey, () =>
    fetchAdminOrgs({ q: q || undefined, tier: tierFilter || undefined, page, page_size: PAGE_SIZE })
  );

  const flash = useCallback((kind: "success" | "error", msg: string) => {
    setBanner({ kind, msg });
    setTimeout(() => setBanner(null), 4000);
  }, []);

  async function handleTierChange(orgId: number, tier: string) {
    setSaving(orgId);
    try {
      await updateAdminOrg(orgId, { tier });
      await mutate();
      flash("success", "Tier updated.");
    } catch (e) {
      flash("error", e instanceof Error ? e.message : "Failed");
    } finally {
      setSaving(null);
    }
  }

  async function handleDelete(org: AdminOrg) {
    if (!confirm(`Delete "${org.name}"? This will kick all ${org.member_count} member(s) and cannot be undone.`)) return;
    setDeleting(org.id);
    try {
      await deleteAdminOrg(org.id);
      await mutate();
      flash("success", `"${org.name}" deleted.`);
    } catch (e) {
      flash("error", e instanceof Error ? e.message : "Failed to delete");
    } finally {
      setDeleting(null);
    }
  }

  const total = data?.total ?? 0;
  const totalPages = Math.ceil(total / PAGE_SIZE);

  return (
    <div className="p-6 space-y-5">
      <div>
        <h1 className="text-xl font-semibold text-slate-900">Organizations</h1>
        <p className="text-sm text-slate-500 mt-0.5">Manage all orgs — {total} total</p>
      </div>

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
            placeholder="Search name…"
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
              <th className="text-left px-4 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wide">Name</th>
              <th className="text-left px-4 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wide">Tier</th>
              <th className="text-left px-4 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wide">Members</th>
              <th className="text-left px-4 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wide">Created</th>
              <th className="text-left px-4 py-3 text-xs font-semibold text-slate-500 uppercase tracking-wide">Actions</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {!data && (
              <tr><td colSpan={5} className="text-center py-8 text-slate-400">
                <Loader2 size={20} className="animate-spin mx-auto" />
              </td></tr>
            )}
            {data?.items.map((org: AdminOrg) => (
              <tr key={org.id} className="hover:bg-slate-50 transition-colors">
                <td className="px-4 py-3">
                  <div className="font-medium text-slate-800">{org.name}</div>
                  <div className="text-xs text-slate-400 font-mono mt-0.5">{org.slug}</div>
                </td>
                <td className="px-4 py-3">
                  <select
                    value={org.tier}
                    disabled={saving === org.id}
                    onChange={(e) => handleTierChange(org.id, e.target.value)}
                    className={`text-xs font-medium px-2 py-0.5 rounded border-0 cursor-pointer focus:outline-none focus:ring-2 focus:ring-blue-300 ${TIER_COLORS[org.tier] ?? "bg-slate-100 text-slate-600"}`}
                  >
                    {TIERS.map((t) => <option key={t} value={t}>{t}</option>)}
                  </select>
                </td>
                <td className="px-4 py-3 text-slate-600">{org.member_count}</td>
                <td className="px-4 py-3 text-slate-400 text-xs">{org.created_at.slice(0, 10)}</td>
                <td className="px-4 py-3">
                  <div className="flex items-center gap-2">
                    <a
                      href={`/app/org`}
                      className="text-xs text-blue-600 hover:text-blue-800 flex items-center gap-0.5"
                      title="View team page"
                    >
                      <ExternalLink size={12} />
                    </a>
                    <button
                      onClick={() => handleDelete(org)}
                      disabled={deleting === org.id}
                      title="Delete org"
                      className="text-xs text-red-400 hover:text-red-600 disabled:opacity-40 transition-colors"
                    >
                      {deleting === org.id
                        ? <Loader2 size={13} className="animate-spin" />
                        : <Trash2 size={13} />
                      }
                    </button>
                  </div>
                </td>
              </tr>
            ))}
            {data?.items.length === 0 && (
              <tr><td colSpan={5} className="text-center py-8 text-slate-400 text-sm">No organizations found</td></tr>
            )}
          </tbody>
        </table>
      </div>

      {totalPages > 1 && (
        <div className="flex items-center justify-between text-sm text-slate-500">
          <span>{total} orgs · page {page}/{totalPages}</span>
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
