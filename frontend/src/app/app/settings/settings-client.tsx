"use client";
import { useEffect, useState } from "react";
import useSWR from "swr";
import { useRouter } from "next/navigation";
import { Save, Plus, Trash2, ToggleLeft, ToggleRight, Loader2, Landmark, Search, MapPin, FileText } from "lucide-react";
import {
  createTfidfStopword,
  createBoilerplate,
  createGoogleDirectoryDomain,
  createGoogleStopword,
  deleteTfidfStopword,
  deleteBoilerplate,
  deleteGoogleDirectoryDomain,
  deleteGoogleStopword,
  fetchTfidfStopwords,
  fetchBoilerplate,
  fetchGoogleDirectoryDomains,
  fetchGoogleStopwords,
  fetchSettings,
  saveSettings,
  seedDefaults,
  toggleTfidfStopword,
  toggleBoilerplate,
  toggleGoogleDirectoryDomain,
  toggleGoogleStopword,
  triggerJob,
} from "@/lib/api";
import type { AppSettings, BoilerplatePattern, GoogleDirectoryDomain, GoogleStopword, TfidfStopword } from "@/lib/types";

const inputCls = "w-full px-3 py-2 border border-slate-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-300 focus:border-transparent bg-white";
const textareaCls = inputCls + " resize-y";

function Field({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <div>
      <label className="block text-sm font-medium text-slate-700 mb-1">{label}</label>
      {children}
      {hint && <p className="mt-1 text-xs text-slate-400">{hint}</p>}
    </div>
  );
}

function SectionTitle({ title }: { title: string }) {
  return <h2 className="text-sm font-semibold text-slate-500 uppercase tracking-wider pt-4 pb-2 border-b border-slate-100">{title}</h2>;
}

export function SettingsClient() {
  const { data: initial, mutate: reloadSettings } = useSWR<AppSettings>("settings", fetchSettings);
  const { data: boilerplate = [], mutate: reloadBoilerplate } = useSWR<BoilerplatePattern[]>("boilerplate", fetchBoilerplate);
  const { data: stopwords = [], mutate: reloadStopwords } = useSWR<GoogleStopword[]>("google-stopwords", fetchGoogleStopwords);
  const { data: directoryDomains = [], mutate: reloadDirectoryDomains } = useSWR<GoogleDirectoryDomain[]>("google-directory-domains", fetchGoogleDirectoryDomains);
  const { data: tfidfStopwords = [], mutate: reloadTfidfStopwords } = useSWR<TfidfStopword[]>("tfidf-stopwords", fetchTfidfStopwords);

  const [form, setForm] = useState<Partial<AppSettings>>({});
  const [newStopword, setNewStopword] = useState("");
  const [newTfidfStopword, setNewTfidfStopword] = useState("");
  const [newDirectoryDomain, setNewDirectoryDomain] = useState("");
  const [addingStopword, setAddingStopword] = useState(false);
  const [addingTfidfStopword, setAddingTfidfStopword] = useState(false);
  const [addingDirectoryDomain, setAddingDirectoryDomain] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [newPattern, setNewPattern] = useState({ pattern: "", description: "", example: "" });
  const [addingPattern, setAddingPattern] = useState(false);
  const [triggering, setTriggering] = useState<string | null>(null);
  const [seeding, setSeeding] = useState(false);
  const [banner, setBanner] = useState<{ kind: "success" | "error"; message: string } | null>(null);
  const router = useRouter();

  useEffect(() => {
    if (!initial) return;
    setForm(initial);
  }, [initial]);

  function set(key: keyof AppSettings, value: string) {
    setForm(f => ({ ...f, [key]: value }));
  }

  async function handleSeedDefaults() {
    setSeeding(true);
    try {
      const result = await seedDefaults();
      reloadStopwords();
      reloadDirectoryDomains();
      reloadTfidfStopwords();
      setBanner({ kind: "success", message: `Seeded: ${result.google_stopwords} Google stopwords, ${result.directory_domains} directory domains, ${result.tfidf_stopwords} TF-IDF stopwords.` });
    } catch {
      setBanner({ kind: "error", message: "Failed to seed defaults." });
    } finally {
      setSeeding(false);
    }
  }

  async function handleSave(e: React.FormEvent) {
    e.preventDefault();
    setSaving(true);
    try {
      await saveSettings({ ...form, google_search_enabled: form.google_search_enabled });
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
      reloadSettings();
    } finally {
      setSaving(false);
    }
  }

  async function handleTrigger(endpoint: string, payload?: object) {
    setTriggering(endpoint);
    setBanner(null);
    try {
      await triggerJob(endpoint, payload);
      setBanner({ kind: "success", message: "Job queued → redirecting to Jobs…" });
      setTimeout(() => router.push("/app/jobs"), 800);
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      setBanner({ kind: "error", message: `Failed to queue job: ${msg}` });
    } finally {
      setTriggering(null);
      setTimeout(() => setBanner(null), 4000);
    }
  }

  async function handleToggle(id: number) {
    await toggleBoilerplate(id);
    reloadBoilerplate();
  }

  async function handleDelete(id: number) {
    await deleteBoilerplate(id);
    reloadBoilerplate();
  }

  async function handleAddPattern(e: React.FormEvent) {
    e.preventDefault();
    setAddingPattern(true);
    try {
      await createBoilerplate(newPattern);
      setNewPattern({ pattern: "", description: "", example: "" });
      reloadBoilerplate();
    } finally {
      setAddingPattern(false);
    }
  }

  async function addStopword() {
    const value = newStopword.trim();
    if (!value) return;
    setAddingStopword(true);
    try {
      await createGoogleStopword({ value });
      setNewStopword("");
      reloadStopwords();
    } finally {
      setAddingStopword(false);
    }
  }

  async function addTfidfStopword() {
    const value = newTfidfStopword.trim();
    if (!value) return;
    setAddingTfidfStopword(true);
    try {
      await createTfidfStopword({ value });
      setNewTfidfStopword("");
      reloadTfidfStopwords();
    } finally {
      setAddingTfidfStopword(false);
    }
  }

  async function toggleStopword(id: number) {
    await toggleGoogleStopword(id);
    reloadStopwords();
  }

  async function removeStopword(id: number) {
    await deleteGoogleStopword(id);
    reloadStopwords();
  }

  async function toggleManagedTfidfStopword(id: number) {
    await toggleTfidfStopword(id);
    reloadTfidfStopwords();
  }

  async function removeTfidfStopword(id: number) {
    await deleteTfidfStopword(id);
    reloadTfidfStopwords();
  }

  async function addDirectoryDomain() {
    const value = newDirectoryDomain.trim().toLowerCase();
    if (!value) return;
    setAddingDirectoryDomain(true);
    try {
      await createGoogleDirectoryDomain({ value });
      setNewDirectoryDomain("");
      reloadDirectoryDomains();
    } finally {
      setAddingDirectoryDomain(false);
    }
  }

  async function toggleDirectoryDomain(id: number) {
    await toggleGoogleDirectoryDomain(id);
    reloadDirectoryDomains();
  }

  async function removeDirectoryDomain(id: number) {
    await deleteGoogleDirectoryDomain(id);
    reloadDirectoryDomains();
  }

  if (!initial) return <div className="p-6 text-slate-400 text-sm">Loading…</div>;

  return (
    <div className="p-6 max-w-3xl mx-auto space-y-5">
      {banner && (
        <div
          role="status"
          className={cn(
            "rounded-lg border px-4 py-3 text-sm",
            banner.kind === "success"
              ? "border-green-200 bg-green-50 text-green-800"
              : "border-red-200 bg-red-50 text-red-800",
          )}
        >
          {banner.message}
        </div>
      )}

      <form onSubmit={handleSave} className="space-y-5">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold text-slate-900">Settings</h1>
          <p className="text-sm text-slate-500 mt-0.5">Scoring parameters, API keys, and data quality filters</p>
        </div>
        <button
          type="submit"
          disabled={saving}
          className="flex items-center gap-2 bg-blue-600 hover:bg-blue-700 disabled:opacity-60 text-white px-4 py-2 rounded-lg text-sm font-medium transition-colors"
        >
          <Save size={14} />
          {saved ? "Saved!" : saving ? "Saving…" : "Save settings"}
        </button>
      </div>

      {/* Google */}
      <SectionTitle title="Google search" />
      <div className="grid grid-cols-2 gap-4">
        <Field label="Enable Google search">
          <label className="flex items-center gap-2 mt-1 cursor-pointer">
            <input
              type="checkbox"
              checked={form.google_search_enabled === "true"}
              onChange={e => set("google_search_enabled", e.target.checked ? "true" : "false")}
              className="rounded border-slate-300 text-blue-600"
            />
            <span className="text-sm text-slate-700">Enabled</span>
          </label>
        </Field>
        <Field label="Daily quota">
          <input type="number" min="1" value={form.google_daily_quota ?? "100"} onChange={e => set("google_daily_quota", e.target.value)} className={inputCls} />
        </Field>
      </div>

      <SectionTitle title="Google scoring filters" />
      <div className="flex items-center justify-between py-2">
        <p className="text-xs text-slate-500">Seed the database with all built-in defaults: Google stopwords, directory domains, and TF-IDF stopwords. Safe to run multiple times — skips existing rows.</p>
        <button
          type="button"
          onClick={handleSeedDefaults}
          disabled={seeding}
          className="flex items-center gap-1.5 rounded border border-slate-200 px-3 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-50 transition-colors"
        >
          {seeding ? <Loader2 size={13} className="animate-spin" /> : <Plus size={13} />}
          Seed defaults
        </button>
      </div>
      <div className="space-y-5">
        <Field
          label="Stop words"
          hint="Stop words used when extracting purpose keywords for Google match scoring. Use 'Seed defaults' to load the built-in list."
        >
          <div className="space-y-2">
            <div className="flex gap-2">
              <input
                value={newStopword}
                onChange={e => setNewStopword(e.target.value)}
                onKeyDown={e => {
                  if (e.key === "Enter") {
                    e.preventDefault();
                    addStopword();
                  }
                }}
                placeholder="Add stop word"
                className={cn(inputCls, "flex-1")}
              />
              <button
                type="button"
                onClick={addStopword}
                disabled={addingStopword}
                className="flex items-center gap-1.5 bg-slate-100 hover:bg-slate-200 text-slate-700 px-3 py-2 rounded-lg text-sm font-medium transition-colors"
              >
                {addingStopword ? <Loader2 size={14} className="animate-spin" /> : <Plus size={14} />}
                Add
              </button>
            </div>
            <div className="overflow-x-auto border border-slate-200 rounded-lg bg-white">
              <table className="w-full text-sm">
                <thead className="bg-slate-50 text-slate-600">
                  <tr>
                    <th className="text-left px-3 py-2 font-medium">Word</th>
                    <th className="text-right px-3 py-2 font-medium w-20">Action</th>
                  </tr>
                </thead>
                <tbody>
                  {stopwords.map((row) => (
                    <tr key={row.id} className="border-t border-slate-100">
                      <td className="px-3 py-2 text-slate-800">
                        <div className="flex items-center gap-2">
                          <button type="button" onClick={() => toggleStopword(row.id)} className="shrink-0 text-slate-400 hover:text-slate-700 transition-colors">
                            {row.active ? <ToggleRight size={18} className="text-blue-500" /> : <ToggleLeft size={18} />}
                          </button>
                          <span className={row.active ? "text-slate-800" : "text-slate-400 line-through"}>{row.value}</span>
                        </div>
                      </td>
                      <td className="px-3 py-2 text-right">
                        <button type="button" onClick={() => removeStopword(row.id)} className="text-slate-400 hover:text-red-500 transition-colors">
                          <Trash2 size={14} />
                        </button>
                      </td>
                    </tr>
                  ))}
                  {stopwords.length === 0 && (
                    <tr>
                      <td colSpan={2} className="px-3 py-3 text-slate-400">No stop words configured. Click &ldquo;Seed defaults&rdquo; to load built-ins.</td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>
        </Field>

        <Field
          label="Directory domains"
          hint="Domains in this table are always treated as directories and excluded from Google website matching."
        >
          <div className="space-y-2">
            <div className="flex gap-2">
              <input
                value={newDirectoryDomain}
                onChange={e => setNewDirectoryDomain(e.target.value)}
                onKeyDown={e => {
                  if (e.key === "Enter") {
                    e.preventDefault();
                    addDirectoryDomain();
                  }
                }}
                placeholder="Add directory domain (example.ch)"
                className={cn(inputCls, "flex-1")}
              />
              <button
                type="button"
                onClick={addDirectoryDomain}
                disabled={addingDirectoryDomain}
                className="flex items-center gap-1.5 bg-slate-100 hover:bg-slate-200 text-slate-700 px-3 py-2 rounded-lg text-sm font-medium transition-colors"
              >
                {addingDirectoryDomain ? <Loader2 size={14} className="animate-spin" /> : <Plus size={14} />}
                Add
              </button>
            </div>
            <div className="overflow-x-auto border border-slate-200 rounded-lg bg-white">
              <table className="w-full text-sm">
                <thead className="bg-slate-50 text-slate-600">
                  <tr>
                    <th className="text-left px-3 py-2 font-medium">Domain</th>
                    <th className="text-right px-3 py-2 font-medium w-20">Action</th>
                  </tr>
                </thead>
                <tbody>
                  {directoryDomains.map((row) => (
                    <tr key={row.id} className="border-t border-slate-100">
                      <td className="px-3 py-2 text-slate-800">
                        <div className="flex items-center gap-2">
                          <button type="button" onClick={() => toggleDirectoryDomain(row.id)} className="shrink-0 text-slate-400 hover:text-slate-700 transition-colors">
                            {row.active ? <ToggleRight size={18} className="text-blue-500" /> : <ToggleLeft size={18} />}
                          </button>
                          <span className={row.active ? "text-slate-800" : "text-slate-400 line-through"}>{row.value}</span>
                        </div>
                      </td>
                      <td className="px-3 py-2 text-right">
                        <button type="button" onClick={() => removeDirectoryDomain(row.id)} className="text-slate-400 hover:text-red-500 transition-colors">
                          <Trash2 size={14} />
                        </button>
                      </td>
                    </tr>
                  ))}
                  {directoryDomains.length === 0 && (
                    <tr>
                      <td colSpan={2} className="px-3 py-3 text-slate-400">No directory domains configured. Click &ldquo;Seed defaults&rdquo; to load built-ins.</td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>
        </Field>
      </div>

      <SectionTitle title="TF-IDF and purpose extraction" />
      <div className="space-y-5">
        <Field
          label="Stop words"
          hint="Stop words used by TF-IDF clustering and purpose keyword extraction. Use 'Seed defaults' to load the built-in list."
        >
          <div className="space-y-2">
            <div className="flex gap-2">
              <input
                value={newTfidfStopword}
                onChange={e => setNewTfidfStopword(e.target.value)}
                onKeyDown={e => {
                  if (e.key === "Enter") {
                    e.preventDefault();
                    addTfidfStopword();
                  }
                }}
                placeholder="Add TF-IDF stop word"
                className={cn(inputCls, "flex-1")}
              />
              <button
                type="button"
                onClick={addTfidfStopword}
                disabled={addingTfidfStopword}
                className="flex items-center gap-1.5 bg-slate-100 hover:bg-slate-200 text-slate-700 px-3 py-2 rounded-lg text-sm font-medium transition-colors"
              >
                {addingTfidfStopword ? <Loader2 size={14} className="animate-spin" /> : <Plus size={14} />}
                Add
              </button>
            </div>
            <div className="overflow-x-auto border border-slate-200 rounded-lg bg-white">
              <table className="w-full text-sm">
                <thead className="bg-slate-50 text-slate-600">
                  <tr>
                    <th className="text-left px-3 py-2 font-medium">Word</th>
                    <th className="text-right px-3 py-2 font-medium w-20">Action</th>
                  </tr>
                </thead>
                <tbody>
                  {tfidfStopwords.map((row) => (
                    <tr key={row.id} className="border-t border-slate-100">
                      <td className="px-3 py-2 text-slate-800">
                        <div className="flex items-center gap-2">
                          <button type="button" onClick={() => toggleManagedTfidfStopword(row.id)} className="shrink-0 text-slate-400 hover:text-slate-700 transition-colors">
                            {row.active ? <ToggleRight size={18} className="text-blue-500" /> : <ToggleLeft size={18} />}
                          </button>
                          <span className={row.active ? "text-slate-800" : "text-slate-400 line-through"}>{row.value}</span>
                        </div>
                      </td>
                      <td className="px-3 py-2 text-right">
                        <button type="button" onClick={() => removeTfidfStopword(row.id)} className="text-slate-400 hover:text-red-500 transition-colors">
                          <Trash2 size={14} />
                        </button>
                      </td>
                    </tr>
                  ))}
                  {tfidfStopwords.length === 0 && (
                    <tr>
                      <td colSpan={2} className="px-3 py-3 text-slate-400">No TF-IDF stop words configured. Click &ldquo;Seed defaults&rdquo; to load built-ins.</td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>
        </Field>
      </div>

      {/* Cluster scoring */}
      <SectionTitle title="Cluster scoring" />
      <div className="grid grid-cols-2 gap-4">
        <Field label="Target clusters" hint="Pipe-separated cluster labels that score positively">
          <textarea rows={3} value={form.scoring_target_clusters ?? ""} onChange={e => set("scoring_target_clusters", e.target.value)} className={textareaCls} />
        </Field>
        <Field label="Exclude clusters" hint="Pipe-separated cluster labels that score negatively">
          <textarea rows={3} value={form.scoring_exclude_clusters ?? ""} onChange={e => set("scoring_exclude_clusters", e.target.value)} className={textareaCls} />
        </Field>
        <Field label="Cluster hit points">
          <input type="number" value={form.scoring_cluster_hit_points ?? "10"} onChange={e => set("scoring_cluster_hit_points", e.target.value)} className={inputCls} />
        </Field>
        <Field label="Cluster exclude points">
          <input type="number" value={form.scoring_cluster_exclude_points ?? "10"} onChange={e => set("scoring_cluster_exclude_points", e.target.value)} className={inputCls} />
        </Field>
      </div>

      {/* Keyword scoring */}
      <SectionTitle title="Keyword scoring" />
      <div className="grid grid-cols-2 gap-4">
        <Field label="Target keywords">
          <textarea rows={3} value={form.scoring_target_keywords ?? ""} onChange={e => set("scoring_target_keywords", e.target.value)} className={textareaCls} />
        </Field>
        <Field label="Exclude keywords">
          <textarea rows={3} value={form.scoring_exclude_keywords ?? ""} onChange={e => set("scoring_exclude_keywords", e.target.value)} className={textareaCls} />
        </Field>
        <Field label="Keyword hit points">
          <input type="number" value={form.scoring_keyword_hit_points ?? "10"} onChange={e => set("scoring_keyword_hit_points", e.target.value)} className={inputCls} />
        </Field>
        <Field label="Keyword exclude points">
          <input type="number" value={form.scoring_keyword_exclude_points ?? "10"} onChange={e => set("scoring_keyword_exclude_points", e.target.value)} className={inputCls} />
        </Field>
      </div>

      {/* Distance scoring */}
      <SectionTitle title="Distance scoring" />
      <div className="grid grid-cols-3 gap-4">
        <Field label="Origin latitude">
          <input type="number" step="0.0001" value={form.scoring_origin_lat ?? "46.9266"} onChange={e => set("scoring_origin_lat", e.target.value)} className={inputCls} />
        </Field>
        <Field label="Origin longitude">
          <input type="number" step="0.0001" value={form.scoring_origin_lon ?? "7.4817"} onChange={e => set("scoring_origin_lon", e.target.value)} className={inputCls} />
        </Field>
      </div>
      <div className="grid grid-cols-5 gap-3">
        {(["15km", "40km", "80km", "130km", "far"] as const).map(band => (
          <Field key={band} label={`≤${band}`}>
            <input
              type="number"
              value={(form as Record<string, string>)[`scoring_dist_${band}`] ?? "0"}
              onChange={e => set(`scoring_dist_${band}` as keyof AppSettings, e.target.value)}
              className={inputCls}
            />
          </Field>
        ))}
      </div>

      {/* Legal form */}
      <SectionTitle title="Legal form scoring" />
      <div className="grid grid-cols-2 gap-4">
        <Field label="Legal form scores" hint="Comma-separated form:points pairs">
          <input value={form.scoring_legal_form_scores ?? ""} onChange={e => set("scoring_legal_form_scores", e.target.value)} className={inputCls} />
        </Field>
        <Field label="Default score">
          <input type="number" value={form.scoring_legal_form_default ?? "5"} onChange={e => set("scoring_legal_form_default", e.target.value)} className={inputCls} />
        </Field>
        <Field label="Cancelled company score">
          <input type="number" value={form.scoring_cancelled_score ?? "5"} onChange={e => set("scoring_cancelled_score", e.target.value)} className={inputCls} />
        </Field>
      </div>

      {/* Claude */}
      <SectionTitle title="Claude AI" />
      <div className="space-y-4">
        <Field label="Anthropic API key">
          <input type="password" value={form.anthropic_api_key ?? ""} onChange={e => set("anthropic_api_key", e.target.value)} className={inputCls} placeholder="sk-ant-…" />
        </Field>
        <Field label="Target description" hint="Describe your ideal target company for Claude scoring">
          <textarea rows={4} value={form.claude_target_description ?? ""} onChange={e => set("claude_target_description", e.target.value)} className={textareaCls} />
        </Field>
        <Field label="Classify prompt (optional override)">
          <textarea rows={3} value={form.claude_classify_prompt ?? ""} onChange={e => set("claude_classify_prompt", e.target.value)} className={textareaCls} />
        </Field>
        <Field label="Categories (one per line)">
          <textarea rows={8} value={form.claude_classify_categories ?? ""} onChange={e => set("claude_classify_categories", e.target.value)} className={textareaCls} />
        </Field>
        <Field label="Max purpose chars for Claude">
          <input type="number" min="100" value={form.scoring_claude_max_purpose_chars ?? "800"} onChange={e => set("scoring_claude_max_purpose_chars", e.target.value)} className={cn(inputCls, "w-32")} />
        </Field>
      </div>

      {/* Recalculate actions */}
      <SectionTitle title="Recalculate scores" />
      <div className="flex gap-3 flex-wrap">
        <button
          type="button"
          onClick={() => handleTrigger("scoring/zefix")}
          disabled={!!triggering}
          className={cn(
            "flex items-center gap-2 bg-slate-100 hover:bg-slate-200 disabled:opacity-60 text-slate-700 px-4 py-2 rounded-lg text-sm font-medium transition-colors",
          )}
        >
          {triggering === "scoring/zefix" ? <Loader2 size={16} className="animate-spin text-blue-600" /> : <Landmark size={16} className="text-blue-600" />}
          Recalculate Zefix scores
        </button>
        <button
          type="button"
          onClick={() => handleTrigger("scoring/google")}
          disabled={!!triggering}
          className={cn(
            "flex items-center gap-2 bg-slate-100 hover:bg-slate-200 disabled:opacity-60 text-slate-700 px-4 py-2 rounded-lg text-sm font-medium transition-colors",
          )}
        >
          {triggering === "scoring/google" ? <Loader2 size={16} className="animate-spin text-green-600" /> : <Search size={16} className="text-green-600" />}
          Recalculate Google scores
        </button>
        <button
          type="button"
          onClick={() => handleTrigger("scoring/re-geocode")}
          disabled={!!triggering}
          className={cn(
            "flex items-center gap-2 bg-amber-50 hover:bg-amber-100 disabled:opacity-60 text-amber-700 px-4 py-2 rounded-lg text-sm font-medium transition-colors",
          )}
        >
          {triggering === "scoring/re-geocode" ? <Loader2 size={16} className="animate-spin text-amber-700" /> : <MapPin size={16} className="text-amber-700" />}
          Re-geocode all companies
        </button>
        <button
          type="button"
          onClick={() => handleTrigger("scoring/reextract-purpose", { only_missing_purpose: true })}
          disabled={!!triggering}
          className={cn(
            "flex items-center gap-2 bg-indigo-50 hover:bg-indigo-100 disabled:opacity-60 text-indigo-700 px-4 py-2 rounded-lg text-sm font-medium transition-colors",
          )}
        >
          {triggering === "scoring/reextract-purpose" ? <Loader2 size={16} className="animate-spin text-indigo-700" /> : <FileText size={16} className="text-indigo-700" />}
          Re-extract purpose (missing only)
        </button>
        <button
          type="button"
          onClick={() => handleTrigger("scoring/reextract-purpose", { only_missing_purpose: false })}
          disabled={!!triggering}
          className={cn(
            "flex items-center gap-2 bg-indigo-100 hover:bg-indigo-200 disabled:opacity-60 text-indigo-800 px-4 py-2 rounded-lg text-sm font-medium transition-colors",
          )}
        >
          {triggering === "scoring/reextract-purpose" ? <Loader2 size={16} className="animate-spin text-indigo-800" /> : <FileText size={16} className="text-indigo-800" />}
          Re-extract purpose (overwrite all)
        </button>
        <button
          type="button"
          onClick={() => handleTrigger("scoring/reclassify-noga", { only_missing_noga: true, only_detailed_raw: true })}
          disabled={!!triggering}
          className={cn(
            "flex items-center gap-2 bg-emerald-50 hover:bg-emerald-100 disabled:opacity-60 text-emerald-700 px-4 py-2 rounded-lg text-sm font-medium transition-colors",
          )}
        >
          {triggering === "scoring/reclassify-noga" ? <Loader2 size={16} className="animate-spin text-emerald-700" /> : <FileText size={16} className="text-emerald-700" />}
          Reclassify NOGA (missing only)
        </button>
        <button
          type="button"
          onClick={() => handleTrigger("scoring/reclassify-noga", { only_missing_noga: false, only_detailed_raw: true })}
          disabled={!!triggering}
          className={cn(
            "flex items-center gap-2 bg-emerald-100 hover:bg-emerald-200 disabled:opacity-60 text-emerald-800 px-4 py-2 rounded-lg text-sm font-medium transition-colors",
          )}
        >
          {triggering === "scoring/reclassify-noga" ? <Loader2 size={16} className="animate-spin text-emerald-800" /> : <FileText size={16} className="text-emerald-800" />}
          Reclassify NOGA (overwrite all)
        </button>
      </div>

      {/* Boilerplate */}
      <SectionTitle title="Boilerplate patterns" />
      <div className="space-y-2">
        {boilerplate.map(bp => (
          <div key={bp.id} className="flex items-center gap-3 px-3 py-2.5 border border-slate-200 rounded-lg bg-white">
            <button type="button" onClick={() => handleToggle(bp.id)} className="shrink-0 text-slate-400 hover:text-slate-700 transition-colors">
              {bp.active ? <ToggleRight size={20} className="text-blue-500" /> : <ToggleLeft size={20} />}
            </button>
            <div className="flex-1 min-w-0">
              <p className="text-sm font-mono text-slate-700 truncate">{bp.pattern}</p>
              {bp.description && <p className="text-xs text-slate-400">{bp.description}</p>}
            </div>
            <button type="button" onClick={() => handleDelete(bp.id)} className="shrink-0 p-1 text-slate-300 hover:text-red-500 transition-colors">
              <Trash2 size={14} />
            </button>
          </div>
        ))}
      </div>

      </form>

      {/* Boilerplate add form (separate form to avoid nested forms) */}
      <form onSubmit={handleAddPattern} className="flex gap-2">
        <input
          value={newPattern.pattern}
          onChange={e => setNewPattern(p => ({ ...p, pattern: e.target.value }))}
          placeholder="Regex pattern"
          className={cn(inputCls, "flex-1")}
          required
        />
        <input
          value={newPattern.description}
          onChange={e => setNewPattern(p => ({ ...p, description: e.target.value }))}
          placeholder="Description (optional)"
          className={cn(inputCls, "w-48")}
        />
        <button
          type="submit"
          disabled={addingPattern}
          className="flex items-center gap-1.5 bg-slate-100 hover:bg-slate-200 disabled:opacity-60 text-slate-700 px-3 py-2 rounded-lg text-sm font-medium transition-colors shrink-0"
        >
          {addingPattern ? <Loader2 size={14} className="animate-spin" /> : <Plus size={14} />}
          Add
        </button>
      </form>
    </div>
  );
}

function cn(...classes: (string | undefined | false)[]) {
  return classes.filter(Boolean).join(" ");
}
