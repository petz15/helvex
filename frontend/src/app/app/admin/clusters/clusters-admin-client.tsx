"use client";
import { useState } from "react";
import useSWR from "swr";
import { Pencil, Check, X, Search, RefreshCw, AlertCircle } from "lucide-react";
import { cn, formatClusterLabel } from "@/lib/utils";
import { fetchClusterRegistry, renameCluster, type ClusterRegistryEntry } from "@/lib/api";

function TopTermsBadges({ json }: { json: string }) {
  let terms: string[] = [];
  try { terms = JSON.parse(json); } catch { /* ignore */ }
  return (
    <div className="flex flex-wrap gap-1 mt-0.5">
      {terms.slice(0, 8).map((t) => (
        <span key={t} className="text-[10px] bg-slate-100 text-slate-500 px-1.5 py-0.5 rounded font-mono">{t}</span>
      ))}
    </div>
  );
}

function RenameCell({ entry, onSaved }: { entry: ClusterRegistryEntry; onSaved: (updated: ClusterRegistryEntry) => void }) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(entry.canonical_name);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function save() {
    if (draft.trim() === entry.canonical_name) { setEditing(false); return; }
    setSaving(true);
    setError(null);
    try {
      const updated = await renameCluster(entry.id, draft.trim());
      onSaved(updated);
      setEditing(false);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Rename failed");
    } finally {
      setSaving(false);
    }
  }

  if (!editing) {
    return (
      <div className="flex items-start gap-2 group">
        <div className="min-w-0">
          <span className="font-medium text-slate-800 text-sm">{formatClusterLabel(entry.canonical_name)}</span>
          <TopTermsBadges json={entry.top_terms} />
        </div>
        <button
          type="button"
          onClick={() => { setDraft(entry.canonical_name); setEditing(true); }}
          className="opacity-0 group-hover:opacity-100 p-1 rounded hover:bg-slate-100 text-slate-400 hover:text-slate-600 transition-all shrink-0 mt-0.5"
          title="Rename"
        >
          <Pencil size={12} />
        </button>
      </div>
    );
  }

  return (
    <div className="space-y-1">
      <div className="flex items-center gap-1.5">
        <input
          autoFocus
          type="text"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter") save(); if (e.key === "Escape") setEditing(false); }}
          className="flex-1 text-sm border border-slate-300 rounded px-2 py-1 focus:outline-none focus:ring-2 focus:ring-blue-300 font-mono"
        />
        <button
          type="button"
          onClick={save}
          disabled={saving}
          className="p-1.5 rounded bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-50"
          title="Save"
        >
          {saving ? <RefreshCw size={12} className="animate-spin" /> : <Check size={12} />}
        </button>
        <button
          type="button"
          onClick={() => { setEditing(false); setError(null); }}
          className="p-1.5 rounded hover:bg-slate-100 text-slate-400"
          title="Cancel"
        >
          <X size={12} />
        </button>
      </div>
      <p className="text-xs text-slate-400 font-mono">raw: {draft}</p>
      {error && <p className="text-xs text-red-500 flex items-center gap-1"><AlertCircle size={11} />{error}</p>}
    </div>
  );
}

export function ClustersAdminClient() {
  const { data: entries = [], isLoading, mutate } = useSWR<ClusterRegistryEntry[]>(
    "cluster-registry",
    fetchClusterRegistry,
  );

  const [search, setSearch] = useState("");
  const [showInactive, setShowInactive] = useState(false);

  const filtered = entries.filter((e) => {
    if (!showInactive && !e.active) return false;
    if (!search) return true;
    const q = search.toLowerCase();
    return (
      e.canonical_name.toLowerCase().includes(q) ||
      formatClusterLabel(e.canonical_name).toLowerCase().includes(q)
    );
  });

  const active = entries.filter((e) => e.active).length;
  const inactive = entries.length - active;

  function handleSaved(updated: ClusterRegistryEntry) {
    mutate((prev) => prev?.map((e) => (e.id === updated.id ? updated : e)), false);
  }

  return (
    <div className="p-6 max-w-5xl mx-auto space-y-6">
      <div>
        <h1 className="text-xl font-semibold text-slate-900">Cluster Registry</h1>
        <p className="text-sm text-slate-500 mt-0.5">
          {active} active · {inactive} inactive — canonical cluster names written to <code className="text-xs bg-slate-100 px-1 rounded">tfidf_cluster</code>.
          Renaming updates all matching company records.
        </p>
      </div>

      <div className="flex items-center gap-3 flex-wrap">
        <div className="relative flex-1 min-w-[200px]">
          <Search size={13} className="absolute left-2.5 top-2.5 text-slate-400 pointer-events-none" />
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search clusters…"
            className="w-full pl-8 pr-3 py-2 text-sm border border-slate-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-300"
          />
        </div>
        <label className="flex items-center gap-1.5 text-sm text-slate-500 cursor-pointer select-none">
          <input
            type="checkbox"
            checked={showInactive}
            onChange={(e) => setShowInactive(e.target.checked)}
            className="rounded border-slate-300"
          />
          Show inactive
        </label>
        <button
          onClick={() => mutate()}
          className="flex items-center gap-1.5 text-sm text-slate-500 hover:text-slate-700 px-3 py-2 rounded-lg border border-slate-200 hover:bg-slate-50"
        >
          <RefreshCw size={13} /> Refresh
        </button>
      </div>

      {isLoading && <p className="text-sm text-slate-400">Loading…</p>}

      {!isLoading && filtered.length === 0 && (
        <p className="text-sm text-slate-400">
          {entries.length === 0
            ? "No clusters in registry. Run a clustering job first."
            : "No clusters match your search."}
        </p>
      )}

      <div className="rounded-xl border border-slate-200 overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-slate-50 border-b border-slate-200">
            <tr>
              <th className="text-left px-4 py-2.5 text-xs font-medium text-slate-500 w-8">#</th>
              <th className="text-left px-4 py-2.5 text-xs font-medium text-slate-500">Cluster name / raw terms</th>
              <th className="text-right px-4 py-2.5 text-xs font-medium text-slate-500 w-24">Companies</th>
              <th className="text-center px-4 py-2.5 text-xs font-medium text-slate-500 w-20">Status</th>
              <th className="text-right px-4 py-2.5 text-xs font-medium text-slate-500 w-28">Updated</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {filtered.map((entry) => (
              <tr
                key={entry.id}
                className={cn(
                  "hover:bg-slate-50 transition-colors",
                  !entry.active && "opacity-50"
                )}
              >
                <td className="px-4 py-3 text-xs text-slate-400 font-mono">{entry.id}</td>
                <td className="px-4 py-3">
                  <RenameCell entry={entry} onSaved={handleSaved} />
                </td>
                <td className="px-4 py-3 text-right tabular-nums text-slate-600">
                  {entry.company_count.toLocaleString()}
                </td>
                <td className="px-4 py-3 text-center">
                  <span className={cn(
                    "inline-flex px-1.5 py-0.5 rounded-full text-[10px] font-medium",
                    entry.active
                      ? "bg-emerald-50 text-emerald-700"
                      : "bg-slate-100 text-slate-500"
                  )}>
                    {entry.active ? "active" : "inactive"}
                  </span>
                </td>
                <td className="px-4 py-3 text-right text-xs text-slate-400">
                  {new Date(entry.updated_at).toLocaleDateString("de-CH")}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <p className="text-xs text-slate-400">
        Hover a row to reveal the rename button. Inactive clusters were not produced in the latest pipeline run — they still exist in company data until the next full re-cluster overwrites them.
      </p>
    </div>
  );
}
