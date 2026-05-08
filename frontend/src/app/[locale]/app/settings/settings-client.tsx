"use client";
import { useEffect, useState } from "react";
import useSWR from "swr";
import { useRouter, useSearchParams } from "next/navigation";
import { ChevronDown, ChevronUp, Save, Plus, Trash2, ToggleLeft, ToggleRight, Loader2, Landmark, Search, MapPin, FileText, Sparkles, Settings2, Brain } from "lucide-react";
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
  fetchCurrentUser,
} from "@/lib/api";
import type { AppSettings, BoilerplatePattern, GoogleDirectoryDomain, GoogleStopword, TfidfStopword } from "@/lib/types";
import { OrgScoringSection } from "@/app/[locale]/app/org/org-client";
import { useApiErrorHandler } from "@/lib/use-api-error";

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

function CollapsibleSection({
  title,
  description,
  count,
  defaultOpen = false,
  children,
}: {
  title: string;
  description?: string;
  count?: number;
  defaultOpen?: boolean;
  children: React.ReactNode;
}) {
  const [open, setOpen] = useState(defaultOpen);

  return (
    <div className="overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm">
      <button
        type="button"
        onClick={() => setOpen(value => !value)}
        className="flex w-full items-center justify-between gap-4 px-4 py-3 text-left transition-colors hover:bg-slate-50"
      >
        <div>
          <div className="flex items-center gap-2">
            <h3 className="text-sm font-semibold text-slate-800">{title}</h3>
            {typeof count === "number" && <span className="rounded-full bg-slate-100 px-2 py-0.5 text-[11px] font-medium text-slate-500">{count}</span>}
          </div>
          {description && <p className="mt-1 text-xs text-slate-500">{description}</p>}
        </div>
        <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full border border-slate-200 text-slate-500">
          {open ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
        </span>
      </button>
      {open && <div className="border-t border-slate-200 p-4">{children}</div>}
    </div>
  );
}

const TABS = [
  { id: "llm", label: "LLM / AI", icon: Brain },
  { id: "flex", label: "FLEX Scoring", icon: Settings2 },
  { id: "admin", label: "Admin", icon: Sparkles },
] as const;
type TabId = typeof TABS[number]["id"];

export function SettingsClient() {
  const searchParams = useSearchParams();
  const handleApiError = useApiErrorHandler();
  const [activeTab, setActiveTab] = useState<TabId>((searchParams?.get("tab") as TabId) ?? "llm");

  const { data: initial, mutate: reloadSettings } = useSWR<AppSettings>("settings", fetchSettings);
  const { data: boilerplate = [], mutate: reloadBoilerplate } = useSWR<BoilerplatePattern[]>("boilerplate", fetchBoilerplate);
  const { data: stopwords = [], mutate: reloadStopwords } = useSWR<GoogleStopword[]>("google-stopwords", fetchGoogleStopwords);
  const { data: directoryDomains = [], mutate: reloadDirectoryDomains } = useSWR<GoogleDirectoryDomain[]>("google-directory-domains", fetchGoogleDirectoryDomains);
  const { data: tfidfStopwords = [], mutate: reloadTfidfStopwords } = useSWR<TfidfStopword[]>("tfidf-stopwords", fetchTfidfStopwords);
  const { data: me } = useSWR("me", fetchCurrentUser);

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
  const [testText, setTestText] = useState("");
  const [testResults, setTestResults] = useState<{
    boilerplate: { id: number; pattern: string; description: string; matchCount: number }[];
    stripped: string;
    tfidfMatches: string[];
  } | null>(null);
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
      if (!handleApiError(error)) {
        const msg = error instanceof Error ? error.message : String(error);
        setBanner({ kind: "error", message: `Failed to queue job: ${msg}` });
      }
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

  function runPatternTest() {
    let stripped = testText;
    const bpMatches: { id: number; pattern: string; description: string; matchCount: number }[] = [];
    for (const bp of boilerplate) {
      if (!bp.active) continue;
      try {
        const re = new RegExp(bp.pattern, "gi");
        const matches = testText.match(re);
        if (matches && matches.length > 0) {
          bpMatches.push({ id: bp.id, pattern: bp.pattern, description: bp.description || "", matchCount: matches.length });
          stripped = stripped.replace(re, "");
        }
      } catch { /* skip invalid */ }
    }
    const words = new Set(testText.toLowerCase().split(/\W+/).filter(Boolean));
    const tfidfMatches = tfidfStopwords
      .filter(sw => sw.active && words.has(sw.value.toLowerCase()))
      .map(sw => sw.value);
    setTestResults({ boilerplate: bpMatches, stripped: stripped.trim(), tfidfMatches });
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

  const orgId = me?.org?.id;
  const isAdmin = me?.org_role === "admin" || me?.org_role === "owner" || !!me?.is_superadmin;

  return (
    <div className="p-6 max-w-4xl mx-auto space-y-5">
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

      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold text-slate-900">Settings</h1>
          <p className="text-sm text-slate-500 mt-0.5">LLM configuration, scoring parameters, and data quality filters</p>
        </div>
      </div>

      {/* Tab bar */}
      <div className="flex gap-1 border-b border-slate-200">
        {TABS.map(({ id, label, icon: Icon }) => (
          <button
            key={id}
            type="button"
            onClick={() => setActiveTab(id)}
            className={cn(
              "flex items-center gap-1.5 px-4 py-2 text-sm font-medium rounded-t-lg border-b-2 transition-colors",
              activeTab === id
                ? "border-blue-600 text-blue-700 bg-blue-50"
                : "border-transparent text-slate-500 hover:text-slate-700 hover:bg-slate-50"
            )}
          >
            <Icon size={14} />
            {label}
          </button>
        ))}
      </div>

      {/* ── LLM / AI tab ── */}
      {activeTab === "llm" && (
        <div className="space-y-5">
          {orgId ? (
            <>
              <p className="text-sm text-slate-500">Org-level LLM configuration — overrides global defaults for your organization.</p>
              <OrgScoringSection orgId={orgId} isAdmin={isAdmin} />
            </>
          ) : (
            <>
              <p className="text-sm text-slate-500">Global LLM defaults (no org context).</p>
              <form onSubmit={handleSave} className="space-y-5">
                <div className="flex justify-end">
                  <button type="submit" disabled={saving} className="flex items-center gap-2 bg-blue-600 hover:bg-blue-700 disabled:opacity-60 text-white px-4 py-2 rounded-lg text-sm font-medium transition-colors">
                    <Save size={14} /> {saved ? "Saved!" : saving ? "Saving…" : "Save"}
                  </button>
                </div>
                <SectionTitle title="Claude AI" />
                <div className="space-y-4">
                  <Field label="Anthropic API key">
                    <input type="password" value={form.anthropic_api_key ?? ""} onChange={e => set("anthropic_api_key", e.target.value)} className={inputCls} placeholder="sk-ant-…" />
                  </Field>
                  <Field label="Claude model" hint="Model used for AI classification jobs">
                    <select value={(form as Record<string, string>).claude_model ?? "claude-haiku-4-5-20251001"} onChange={e => set("claude_model" as keyof AppSettings, e.target.value)} className={inputCls}>
                      <option value="claude-haiku-4-5-20251001">claude-haiku-4-5 (fast, cost-effective)</option>
                      <option value="claude-sonnet-4-6">claude-sonnet-4-6 (balanced)</option>
                      <option value="claude-opus-4-6">claude-opus-4-6 (most capable)</option>
                    </select>
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
              </form>
            </>
          )}
        </div>
      )}

      {/* ── FLEX Scoring tab ── */}
      {activeTab === "flex" && (
        <form onSubmit={handleSave} className="space-y-5">
          <div className="flex justify-end">
            <button type="submit" disabled={saving} className="flex items-center gap-2 bg-blue-600 hover:bg-blue-700 disabled:opacity-60 text-white px-4 py-2 rounded-lg text-sm font-medium transition-colors">
              <Save size={14} /> {saved ? "Saved!" : saving ? "Saving…" : "Save settings"}
            </button>
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
        </form>
      )}

      {/* ── Admin tab ── */}
      {activeTab === "admin" && (
        <form onSubmit={handleSave} className="space-y-5">
          <div className="flex items-center justify-between">
            <p className="text-sm text-slate-500">Global admin settings — Google search, stopwords, boilerplate filters, and job triggers.</p>
            <button type="submit" disabled={saving} className="flex items-center gap-2 bg-blue-600 hover:bg-blue-700 disabled:opacity-60 text-white px-4 py-2 rounded-lg text-sm font-medium transition-colors">
              <Save size={14} /> {saved ? "Saved!" : saving ? "Saving…" : "Save settings"}
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
      <div className="space-y-4">
        <CollapsibleSection
          title="Google stop words"
          description="Stop words used when extracting purpose keywords for Google match scoring."
          count={stopwords.length}
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
        </CollapsibleSection>

        <CollapsibleSection
          title="Directory domains"
          description="Domains in this table are always treated as directories and excluded from Google website matching."
          count={directoryDomains.length}
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
        </CollapsibleSection>
      </div>

      <SectionTitle title="TF-IDF and purpose extraction" />
      <CollapsibleSection
        title="TF-IDF stop words"
        description="Stop words used by TF-IDF clustering and purpose keyword extraction."
        count={tfidfStopwords.length}
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
      </CollapsibleSection>

          {/* Recalculate actions */}
          <SectionTitle title="Recalculate scores" />
          <div className="flex gap-3 flex-wrap">
            <button type="button" onClick={() => handleTrigger("scoring/zefix")} disabled={!!triggering} className={cn("flex items-center gap-2 bg-slate-100 hover:bg-slate-200 disabled:opacity-60 text-slate-700 px-4 py-2 rounded-lg text-sm font-medium transition-colors")}>
              {triggering === "scoring/zefix" ? <Loader2 size={16} className="animate-spin text-blue-600" /> : <Landmark size={16} className="text-blue-600" />}
              Recalculate Zefix scores
            </button>
            <button type="button" onClick={() => handleTrigger("scoring/google")} disabled={!!triggering} className={cn("flex items-center gap-2 bg-slate-100 hover:bg-slate-200 disabled:opacity-60 text-slate-700 px-4 py-2 rounded-lg text-sm font-medium transition-colors")}>
              {triggering === "scoring/google" ? <Loader2 size={16} className="animate-spin text-green-600" /> : <Search size={16} className="text-green-600" />}
              Recalculate Google scores
            </button>
            <button type="button" onClick={() => handleTrigger("scoring/re-geocode")} disabled={!!triggering} className={cn("flex items-center gap-2 bg-amber-50 hover:bg-amber-100 disabled:opacity-60 text-amber-700 px-4 py-2 rounded-lg text-sm font-medium transition-colors")}>
              {triggering === "scoring/re-geocode" ? <Loader2 size={16} className="animate-spin text-amber-700" /> : <MapPin size={16} className="text-amber-700" />}
              Re-geocode all companies
            </button>
            <button type="button" onClick={() => handleTrigger("scoring/reextract-purpose", { only_missing_purpose: true })} disabled={!!triggering} className={cn("flex items-center gap-2 bg-indigo-50 hover:bg-indigo-100 disabled:opacity-60 text-indigo-700 px-4 py-2 rounded-lg text-sm font-medium transition-colors")}>
              {triggering === "scoring/reextract-purpose" ? <Loader2 size={16} className="animate-spin text-indigo-700" /> : <FileText size={16} className="text-indigo-700" />}
              Re-extract purpose (missing only)
            </button>
            <button type="button" onClick={() => handleTrigger("scoring/reextract-purpose", { only_missing_purpose: false })} disabled={!!triggering} className={cn("flex items-center gap-2 bg-indigo-100 hover:bg-indigo-200 disabled:opacity-60 text-indigo-800 px-4 py-2 rounded-lg text-sm font-medium transition-colors")}>
              {triggering === "scoring/reextract-purpose" ? <Loader2 size={16} className="animate-spin text-indigo-800" /> : <FileText size={16} className="text-indigo-800" />}
              Re-extract purpose (overwrite all)
            </button>
            <button type="button" onClick={() => handleTrigger("scoring/reclassify-noga", { only_missing_noga: true, only_detailed_raw: true })} disabled={!!triggering} className={cn("flex items-center gap-2 bg-emerald-50 hover:bg-emerald-100 disabled:opacity-60 text-emerald-700 px-4 py-2 rounded-lg text-sm font-medium transition-colors")}>
              {triggering === "scoring/reclassify-noga" ? <Loader2 size={16} className="animate-spin text-emerald-700" /> : <FileText size={16} className="text-emerald-700" />}
              Reclassify NOGA (missing only)
            </button>
            <button type="button" onClick={() => handleTrigger("scoring/reclassify-noga", { only_missing_noga: false, only_detailed_raw: true })} disabled={!!triggering} className={cn("flex items-center gap-2 bg-emerald-100 hover:bg-emerald-200 disabled:opacity-60 text-emerald-800 px-4 py-2 rounded-lg text-sm font-medium transition-colors")}>
              {triggering === "scoring/reclassify-noga" ? <Loader2 size={16} className="animate-spin text-emerald-800" /> : <FileText size={16} className="text-emerald-800" />}
              Reclassify NOGA (overwrite all)
            </button>
            <button type="button" onClick={() => handleTrigger("scoring/analyze-boilerplate")} disabled={!!triggering} className={cn("flex items-center gap-2 bg-orange-50 hover:bg-orange-100 disabled:opacity-60 text-orange-700 px-4 py-2 rounded-lg text-sm font-medium transition-colors")}>
              {triggering === "scoring/analyze-boilerplate" ? <Loader2 size={16} className="animate-spin text-orange-700" /> : <Sparkles size={16} className="text-orange-700" />}
              Find new boilerplate patterns
            </button>
          </div>

          <CollapsibleSection title="Boilerplate patterns" description="Regex templates used to filter boilerplate language from company text." count={boilerplate.length}>
            <div className="space-y-4">
              <div className="space-y-2">
                {boilerplate.map(bp => (
                  <div key={bp.id} className="flex items-center gap-3 px-3 py-2.5 border border-slate-200 rounded-lg bg-white">
                    <button type="button" onClick={() => handleToggle(bp.id)} className="shrink-0 text-slate-400 hover:text-slate-700 transition-colors">
                      {bp.active ? <ToggleRight size={20} className="text-blue-500" /> : <ToggleLeft size={20} />}
                    </button>
                    <div className="flex-1 min-w-0">
                      <p className="text-sm font-mono text-slate-700 break-all">{bp.pattern}</p>
                      {bp.description && <p className="text-xs text-slate-400">{bp.description}</p>}
                    </div>
                    <button type="button" onClick={() => handleDelete(bp.id)} className="shrink-0 p-1 text-slate-300 hover:text-red-500 transition-colors">
                      <Trash2 size={14} />
                    </button>
                  </div>
                ))}
              </div>
              <form onSubmit={handleAddPattern} className="flex gap-2">
                <input value={newPattern.pattern} onChange={e => setNewPattern(p => ({ ...p, pattern: e.target.value }))} placeholder="Regex pattern" className={cn(inputCls, "flex-1")} required />
                <input value={newPattern.description} onChange={e => setNewPattern(p => ({ ...p, description: e.target.value }))} placeholder="Description (optional)" className={cn(inputCls, "w-48")} />
                <button type="submit" disabled={addingPattern} className="flex items-center gap-1.5 bg-slate-100 hover:bg-slate-200 disabled:opacity-60 text-slate-700 px-3 py-2 rounded-lg text-sm font-medium transition-colors shrink-0">
                  {addingPattern ? <Loader2 size={14} className="animate-spin" /> : <Plus size={14} />} Add
                </button>
              </form>

              {/* ── Pattern & stop word tester ── */}
              <div className="border border-slate-200 rounded-lg bg-slate-50 p-3 space-y-3">
                <p className="text-xs font-medium text-slate-500 uppercase tracking-wide">Test patterns &amp; TF-IDF stop words</p>
                <textarea
                  value={testText}
                  onChange={e => { setTestText(e.target.value); setTestResults(null); }}
                  placeholder="Paste a company purpose text here…"
                  rows={3}
                  className="w-full px-3 py-2 border border-slate-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-300 bg-white resize-y"
                />
                <button
                  type="button"
                  onClick={runPatternTest}
                  disabled={!testText.trim()}
                  className="flex items-center gap-1.5 bg-slate-700 hover:bg-slate-800 disabled:opacity-40 text-white px-3 py-1.5 rounded-lg text-sm font-medium transition-colors"
                >
                  <Search size={13} /> Run test
                </button>
                {testResults && (
                  <div className="space-y-3 pt-1">
                    <div>
                      <p className="text-xs font-semibold text-slate-500 mb-1">Boilerplate pattern matches ({testResults.boilerplate.length})</p>
                      {testResults.boilerplate.length === 0
                        ? <p className="text-xs text-slate-400">No active patterns matched.</p>
                        : <ul className="space-y-1">
                            {testResults.boilerplate.map(m => (
                              <li key={m.id} className="text-xs bg-orange-50 border border-orange-200 rounded px-2 py-1">
                                <span className="font-mono text-orange-800 break-all">{m.pattern}</span>
                                {m.description && <span className="text-orange-500 ml-2">— {m.description}</span>}
                                <span className="ml-2 text-orange-400">({m.matchCount} hit{m.matchCount !== 1 ? "s" : ""})</span>
                              </li>
                            ))}
                          </ul>
                      }
                    </div>
                    <div>
                      <p className="text-xs font-semibold text-slate-500 mb-1">Text after stripping boilerplate</p>
                      <p className="text-xs font-mono bg-white border border-slate-200 rounded px-2 py-1.5 whitespace-pre-wrap text-slate-700">
                        {testResults.stripped || <span className="text-slate-400 italic">empty</span>}
                      </p>
                    </div>
                    <div>
                      <p className="text-xs font-semibold text-slate-500 mb-1">TF-IDF stop words found ({testResults.tfidfMatches.length})</p>
                      {testResults.tfidfMatches.length === 0
                        ? <p className="text-xs text-slate-400">No active TF-IDF stop words matched.</p>
                        : <div className="flex flex-wrap gap-1">
                            {testResults.tfidfMatches.map(w => (
                              <span key={w} className="text-xs bg-blue-50 border border-blue-200 text-blue-700 rounded px-1.5 py-0.5 font-mono">{w}</span>
                            ))}
                          </div>
                      }
                    </div>
                  </div>
                )}
              </div>
            </div>
          </CollapsibleSection>
        </form>
      )}
    </div>
  );
}

function cn(...classes: (string | undefined | false)[]) {
  return classes.filter(Boolean).join(" ");
}
