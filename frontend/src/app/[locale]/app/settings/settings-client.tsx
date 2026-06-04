"use client";
import { useEffect, useState } from "react";
import useSWR from "swr";
import { useRouter, useSearchParams } from "next/navigation";
import { ChevronDown, ChevronUp, Save, Plus, Trash2, ToggleLeft, ToggleRight, Loader2, Landmark, Search, MapPin, FileText, Sparkles, Settings2, Brain, Scissors } from "lucide-react";
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
  toggleBoilerplateTruncate,
  toggleGoogleDirectoryDomain,
  toggleGoogleStopword,
  triggerJob,
  fetchCurrentUser,
} from "@/lib/api";
import type { AppSettings, BoilerplatePattern, GoogleDirectoryDomain, GoogleStopword, TfidfStopword } from "@/lib/types";
import { OrgScoringSection } from "@/app/[locale]/app/org/org-client";
import { useApiErrorHandler } from "@/lib/use-api-error";
import { useI18n } from "@/i18n/context";

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

const getTabs = (dict: any) => [
  { id: "llm", label: dict.app.settings.tabs.llm, icon: Brain },
  { id: "flex", label: dict.app.settings.tabs.flex, icon: Settings2 },
  { id: "admin", label: dict.app.settings.tabs.admin, icon: Sparkles },
] as const;
type TabId = "llm" | "flex" | "admin";

export function SettingsClient() {
  const { dict } = useI18n();
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
  const [newPattern, setNewPattern] = useState({ pattern: "", description: "", example: "", truncate: false });
  const [addingPattern, setAddingPattern] = useState(false);
  const [triggering, setTriggering] = useState<string | null>(null);
  const [testText, setTestText] = useState("");
  const [testResults, setTestResults] = useState<{
    boilerplate: { id: number; pattern: string; description: string; matchCount: number; truncate: boolean }[];
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
      const message = dict.app.settings.admin.seedSuccess
        .replace('{googleStopwords}', String(result.google_stopwords))
        .replace('{directoryDomains}', String(result.directory_domains))
        .replace('{tfidfStopwords}', String(result.tfidf_stopwords));
      setBanner({ kind: "success", message });
    } catch {
      setBanner({ kind: "error", message: dict.app.settings.admin.seedError });
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
      setBanner({ kind: "success", message: dict.app.settings.admin.jobQueuedSuccess });
      setTimeout(() => router.push("/app/jobs"), 800);
    } catch (error) {
      if (!handleApiError(error)) {
        const msg = error instanceof Error ? error.message : String(error);
        const message = dict.app.settings.admin.jobQueueError.replace('{error}', msg);
        setBanner({ kind: "error", message });
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

  async function handleToggleTruncate(id: number) {
    await toggleBoilerplateTruncate(id);
    reloadBoilerplate();
  }

  async function handleDelete(id: number) {
    await deleteBoilerplate(id);
    reloadBoilerplate();
  }

  function runPatternTest() {
    const bpMatches: { id: number; pattern: string; description: string; matchCount: number; truncate: boolean }[] = [];

    // Split into sentences (mirrors Python: split on ". " before uppercase)
    const sentenceSplit = /(?<=\.)\s+(?=[A-ZÄÖÜ])/;
    const sentences = testText.split(sentenceSplit);

    // 1. Find the first sentence matched by a truncating pattern → cut from there
    let cutoff = sentences.length;
    for (const bp of boilerplate) {
      if (!bp.active || !bp.truncate) continue;
      try {
        const re = new RegExp(bp.pattern, "i");
        const idx = sentences.findIndex(s => re.test(s));
        if (idx !== -1 && idx < cutoff) {
          cutoff = idx;
          bpMatches.push({ id: bp.id, pattern: bp.pattern, description: bp.description || "", matchCount: 1, truncate: true });
        }
      } catch { /* skip invalid */ }
    }

    const workingSentences = cutoff === 0 ? sentences : sentences.slice(0, cutoff);

    // 2. Remove individual sentences matched by non-truncating patterns
    const kept: string[] = [];
    for (const s of workingSentences) {
      let remove = false;
      for (const bp of boilerplate) {
        if (!bp.active || bp.truncate) continue;
        try {
          const re = new RegExp(bp.pattern, "i");
          if (re.test(s)) {
            const matches = s.match(new RegExp(bp.pattern, "gi"));
            bpMatches.push({ id: bp.id, pattern: bp.pattern, description: bp.description || "", matchCount: matches?.length ?? 1, truncate: false });
            remove = true;
            break;
          }
        } catch { /* skip invalid */ }
      }
      if (!remove) kept.push(s);
    }

    const stripped = kept.join(". ").trim() || workingSentences.join(". ").trim();
    const words = new Set(testText.toLowerCase().split(/\W+/).filter(Boolean));
    const tfidfMatches = tfidfStopwords
      .filter(sw => sw.active && words.has(sw.value.toLowerCase()))
      .map(sw => sw.value);
    setTestResults({ boilerplate: bpMatches, stripped, tfidfMatches });
  }

  async function handleAddPattern(e: React.FormEvent) {
    e.preventDefault();
    setAddingPattern(true);
    try {
      await createBoilerplate(newPattern);
      setNewPattern({ pattern: "", description: "", example: "", truncate: false });
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

  if (!initial) return <div className="p-6 text-slate-400 text-sm">{dict.app.settings.loading}</div>;

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
          <h1 className="text-xl font-semibold text-slate-900">{dict.app.settings.title}</h1>
          <p className="text-sm text-slate-500 mt-0.5">{dict.app.settings.subtitle}</p>
        </div>
      </div>

      {/* Tab bar */}
      <div className="flex gap-1 border-b border-slate-200">
        {getTabs(dict).map(({ id, label, icon: Icon }) => (
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
              <p className="text-sm text-slate-500">{dict.app.settings.orgLevelLlm}</p>
              <OrgScoringSection orgId={orgId} isAdmin={isAdmin} />
            </>
          ) : (
            <>
              <p className="text-sm text-slate-500">{dict.app.settings.globalLlm}</p>
              <form onSubmit={handleSave} className="space-y-5">
                <div className="flex justify-end">
                  <button type="submit" disabled={saving} className="flex items-center gap-2 bg-blue-600 hover:bg-blue-700 disabled:opacity-60 text-white px-4 py-2 rounded-lg text-sm font-medium transition-colors">
                    <Save size={14} /> {saved ? dict.app.settings.saved : saving ? dict.app.settings.saving : dict.app.settings.save}
                  </button>
                </div>
                <SectionTitle title={dict.app.settings.llm.sectionTitle} />
                <div className="space-y-4">
                  <Field label={dict.app.settings.llm.anthropicApiKey}>
                    <input type="password" value={form.anthropic_api_key ?? ""} onChange={e => set("anthropic_api_key", e.target.value)} className={inputCls} placeholder={dict.app.settings.llm.apiKeyPlaceholder} />
                  </Field>
                  <Field label={dict.app.settings.llm.claudeModel} hint={dict.app.settings.llm.claudeModelHint}>
                    <select value={form.claude_model ?? "claude-haiku-4-5-20251001"} onChange={e => set("claude_model", e.target.value)} className={inputCls}>
                      <option value="claude-haiku-4-5-20251001">{dict.app.settings.llm.claudeHaikuOption}</option>
                      <option value="claude-sonnet-4-6">{dict.app.settings.llm.claudeSonnetOption}</option>
                      <option value="claude-opus-4-6">{dict.app.settings.llm.claudeOpusOption}</option>
                    </select>
                  </Field>
                  <Field label="URL Search Provider" hint="Which provider fetches company URLs via Google Search. Serper.dev is a simple REST API; ScrapingDog queries google.ch with Swiss location context.">
                    <select value={form.google_search_provider ?? "serper"} onChange={e => set("google_search_provider", e.target.value)} className={inputCls}>
                      <option value="serper">Serper.dev</option>
                      <option value="scrapingdog">ScrapingDog (google.ch)</option>
                    </select>
                  </Field>
                  <Field label={dict.app.settings.llm.targetDescription} hint={dict.app.settings.llm.targetDescriptionHint}>
                    <textarea rows={4} value={form.claude_target_description ?? ""} onChange={e => set("claude_target_description", e.target.value)} className={textareaCls} />
                  </Field>
                  <Field label={dict.app.settings.llm.classifyPrompt}>
                    <textarea rows={3} value={form.claude_classify_prompt ?? ""} onChange={e => set("claude_classify_prompt", e.target.value)} className={textareaCls} />
                  </Field>
                  <Field label={dict.app.settings.llm.categories}>
                    <textarea rows={8} value={form.claude_classify_categories ?? ""} onChange={e => set("claude_classify_categories", e.target.value)} className={textareaCls} />
                  </Field>
                  <Field label={dict.app.settings.llm.maxPurposeChars}>
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
              <Save size={14} /> {saved ? dict.app.settings.saved : saving ? dict.app.settings.saving : dict.app.settings.saveSettings}
            </button>
          </div>

          {/* Cluster scoring */}
          <SectionTitle title={dict.app.settings.flex.clusterScoring} />
          <div className="grid grid-cols-2 gap-4">
            <Field label={dict.app.settings.flex.targetClusters} hint={dict.app.settings.flex.targetClustersHint}>
              <textarea rows={3} value={form.scoring_target_clusters ?? ""} onChange={e => set("scoring_target_clusters", e.target.value)} className={textareaCls} />
            </Field>
            <Field label={dict.app.settings.flex.excludeClusters} hint={dict.app.settings.flex.excludeClustersHint}>
              <textarea rows={3} value={form.scoring_exclude_clusters ?? ""} onChange={e => set("scoring_exclude_clusters", e.target.value)} className={textareaCls} />
            </Field>
            <Field label={dict.app.settings.flex.clusterHitPoints}>
              <input type="number" value={form.scoring_cluster_hit_points ?? "10"} onChange={e => set("scoring_cluster_hit_points", e.target.value)} className={inputCls} />
            </Field>
            <Field label={dict.app.settings.flex.clusterExcludePoints}>
              <input type="number" value={form.scoring_cluster_exclude_points ?? "10"} onChange={e => set("scoring_cluster_exclude_points", e.target.value)} className={inputCls} />
            </Field>
          </div>

          {/* Keyword scoring */}
          <SectionTitle title={dict.app.settings.flex.keywordScoring} />
          <div className="grid grid-cols-2 gap-4">
            <Field label={dict.app.settings.flex.targetKeywords}>
              <textarea rows={3} value={form.scoring_target_keywords ?? ""} onChange={e => set("scoring_target_keywords", e.target.value)} className={textareaCls} />
            </Field>
            <Field label={dict.app.settings.flex.excludeKeywords}>
              <textarea rows={3} value={form.scoring_exclude_keywords ?? ""} onChange={e => set("scoring_exclude_keywords", e.target.value)} className={textareaCls} />
            </Field>
            <Field label={dict.app.settings.flex.keywordHitPoints}>
              <input type="number" value={form.scoring_keyword_hit_points ?? "10"} onChange={e => set("scoring_keyword_hit_points", e.target.value)} className={inputCls} />
            </Field>
            <Field label={dict.app.settings.flex.keywordExcludePoints}>
              <input type="number" value={form.scoring_keyword_exclude_points ?? "10"} onChange={e => set("scoring_keyword_exclude_points", e.target.value)} className={inputCls} />
            </Field>
          </div>

          {/* Distance scoring */}
          <SectionTitle title={dict.app.settings.flex.distanceScoring} />
          <div className="grid grid-cols-3 gap-4">
            <Field label={dict.app.settings.flex.originLatitude}>
              <input type="number" step="0.0001" value={form.scoring_origin_lat ?? "46.9266"} onChange={e => set("scoring_origin_lat", e.target.value)} className={inputCls} />
            </Field>
            <Field label={dict.app.settings.flex.originLongitude}>
              <input type="number" step="0.0001" value={form.scoring_origin_lon ?? "7.4817"} onChange={e => set("scoring_origin_lon", e.target.value)} className={inputCls} />
            </Field>
          </div>
          <div className="grid grid-cols-5 gap-3">
            {([
              { key: "15km", label: dict.app.settings.flex.distanceBand15km },
              { key: "40km", label: dict.app.settings.flex.distanceBand40km },
              { key: "80km", label: dict.app.settings.flex.distanceBand80km },
              { key: "130km", label: dict.app.settings.flex.distanceBand130km },
              { key: "far", label: dict.app.settings.flex.distanceBandFar },
            ] as const).map(band => (
              <Field key={band.key} label={band.label}>
                <input
                  type="number"
                  value={(form as Record<string, string>)[`scoring_dist_${band.key}`] ?? "0"}
                  onChange={e => set(`scoring_dist_${band.key}` as keyof AppSettings, e.target.value)}
                  className={inputCls}
                />
              </Field>
            ))}
          </div>

          {/* Legal form */}
          <SectionTitle title={dict.app.settings.flex.legalFormScoring} />
          <div className="grid grid-cols-2 gap-4">
            <Field label={dict.app.settings.flex.legalFormScores} hint={dict.app.settings.flex.legalFormScoresHint}>
              <input value={form.scoring_legal_form_scores ?? ""} onChange={e => set("scoring_legal_form_scores", e.target.value)} className={inputCls} />
            </Field>
            <Field label={dict.app.settings.flex.defaultScore}>
              <input type="number" value={form.scoring_legal_form_default ?? "5"} onChange={e => set("scoring_legal_form_default", e.target.value)} className={inputCls} />
            </Field>
            <Field label={dict.app.settings.flex.cancelledCompanyScore}>
              <input type="number" value={form.scoring_cancelled_score ?? "5"} onChange={e => set("scoring_cancelled_score", e.target.value)} className={inputCls} />
            </Field>
          </div>
        </form>
      )}

      {/* ── Admin tab ── */}
      {activeTab === "admin" && (
        <form onSubmit={handleSave} className="space-y-5">
          <div className="flex items-center justify-between">
            <p className="text-sm text-slate-500">{dict.app.settings.globalAdmin}</p>
            <button type="submit" disabled={saving} className="flex items-center gap-2 bg-blue-600 hover:bg-blue-700 disabled:opacity-60 text-white px-4 py-2 rounded-lg text-sm font-medium transition-colors">
              <Save size={14} /> {saved ? dict.app.settings.saved : saving ? dict.app.settings.saving : dict.app.settings.saveSettings}
            </button>
          </div>

          {/* Google */}
          <SectionTitle title={dict.app.settings.admin.googleSearch} />
      <div className="grid grid-cols-2 gap-4">
        <Field label={dict.app.settings.admin.enableGoogleSearch}>
          <label className="flex items-center gap-2 mt-1 cursor-pointer">
            <input
              type="checkbox"
              checked={form.google_search_enabled === "true"}
              onChange={e => set("google_search_enabled", e.target.checked ? "true" : "false")}
              className="rounded border-slate-300 text-blue-600"
            />
            <span className="text-sm text-slate-700">{dict.app.settings.admin.enabled}</span>
          </label>
        </Field>
        <Field label={dict.app.settings.admin.dailyQuota}>
          <input type="number" min="1" value={form.google_daily_quota ?? "100"} onChange={e => set("google_daily_quota", e.target.value)} className={inputCls} />
        </Field>
        <Field label="Search provider" hint="Serper.dev uses POST; ScrapingDog uses google.ch with location/language context.">
          <select
            value={form.google_search_provider ?? "serper"}
            onChange={e => set("google_search_provider", e.target.value)}
            className={inputCls}
          >
            <option value="serper">Serper.dev</option>
            <option value="scrapingdog">ScrapingDog</option>
          </select>
        </Field>
      </div>

      <SectionTitle title={dict.app.settings.admin.googleScoringFilters} />
      <div className="flex items-center justify-between py-2">
        <p className="text-xs text-slate-500">{dict.app.settings.admin.seedDescription}</p>
        <button
          type="button"
          onClick={handleSeedDefaults}
          disabled={seeding}
          className="flex items-center gap-1.5 rounded border border-slate-200 px-3 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-50 transition-colors"
        >
          {seeding ? <Loader2 size={13} className="animate-spin" /> : <Plus size={13} />}
          {dict.app.settings.admin.seedDefaults}
        </button>
      </div>
      <div className="space-y-4">
        <CollapsibleSection
          title={dict.app.settings.admin.googleStopWords}
          description={dict.app.settings.admin.googleStopWordsDescription}
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
                  placeholder={dict.app.settings.admin.addStopWordPlaceholder}
                  className={cn(inputCls, "flex-1")}
                />
                <button
                  type="button"
                  onClick={addStopword}
                  disabled={addingStopword}
                  className="flex items-center gap-1.5 bg-slate-100 hover:bg-slate-200 text-slate-700 px-3 py-2 rounded-lg text-sm font-medium transition-colors"
                >
                  {addingStopword ? <Loader2 size={14} className="animate-spin" /> : <Plus size={14} />}
                  {dict.app.settings.admin.add}
                </button>
              </div>
              <div className="overflow-x-auto border border-slate-200 rounded-lg bg-white">
                <table className="w-full text-sm">
                  <thead className="bg-slate-50 text-slate-600">
                    <tr>
                      <th className="text-left px-3 py-2 font-medium">{dict.app.settings.admin.tableWordColumn}</th>
                      <th className="text-right px-3 py-2 font-medium w-20">{dict.app.settings.admin.tableActionColumn}</th>
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
                        <td colSpan={2} className="px-3 py-3 text-slate-400">{dict.app.settings.admin.noStopwordsConfigured}</td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>
          </div>
        </CollapsibleSection>

        <CollapsibleSection
          title={dict.app.settings.admin.directoryDomains}
          description={dict.app.settings.admin.directoryDomainsDescription}
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
                  placeholder={dict.app.settings.admin.addDirectoryDomainPlaceholder}
                  className={cn(inputCls, "flex-1")}
                />
                <button
                  type="button"
                  onClick={addDirectoryDomain}
                  disabled={addingDirectoryDomain}
                  className="flex items-center gap-1.5 bg-slate-100 hover:bg-slate-200 text-slate-700 px-3 py-2 rounded-lg text-sm font-medium transition-colors"
                >
                  {addingDirectoryDomain ? <Loader2 size={14} className="animate-spin" /> : <Plus size={14} />}
                  {dict.app.settings.admin.add}
                </button>
              </div>
              <div className="overflow-x-auto border border-slate-200 rounded-lg bg-white">
                <table className="w-full text-sm">
                  <thead className="bg-slate-50 text-slate-600">
                    <tr>
                      <th className="text-left px-3 py-2 font-medium">{dict.app.settings.admin.tableDomainColumn}</th>
                      <th className="text-right px-3 py-2 font-medium w-20">{dict.app.settings.admin.tableActionColumn}</th>
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
                        <td colSpan={2} className="px-3 py-3 text-slate-400">{dict.app.settings.admin.noDirectoriesConfigured}</td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>
          </div>
        </CollapsibleSection>
      </div>

      <SectionTitle title={dict.app.settings.admin.tfidfAndPurpose} />
      <CollapsibleSection
        title={dict.app.settings.admin.tfidfStopWords}
        description={dict.app.settings.admin.tfidfStopWordsDescription}
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
                placeholder={dict.app.settings.admin.addTfidfStopWordPlaceholder}
                className={cn(inputCls, "flex-1")}
              />
              <button
                type="button"
                onClick={addTfidfStopword}
                disabled={addingTfidfStopword}
                className="flex items-center gap-1.5 bg-slate-100 hover:bg-slate-200 text-slate-700 px-3 py-2 rounded-lg text-sm font-medium transition-colors"
              >
                {addingTfidfStopword ? <Loader2 size={14} className="animate-spin" /> : <Plus size={14} />}
                {dict.app.settings.admin.add}
              </button>
            </div>
            <div className="overflow-x-auto border border-slate-200 rounded-lg bg-white">
              <table className="w-full text-sm">
                <thead className="bg-slate-50 text-slate-600">
                  <tr>
                    <th className="text-left px-3 py-2 font-medium">{dict.app.settings.admin.tableWordColumn}</th>
                    <th className="text-right px-3 py-2 font-medium w-20">{dict.app.settings.admin.tableActionColumn}</th>
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
                      <td colSpan={2} className="px-3 py-3 text-slate-400">{dict.app.settings.admin.noTfidfConfigured}</td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
        </div>
      </CollapsibleSection>

          {/* Recalculate actions */}
          <SectionTitle title={dict.app.settings.admin.recalculateScores} />
          <div className="flex gap-3 flex-wrap">
            <button type="button" onClick={() => handleTrigger("scoring/zefix")} disabled={!!triggering} className={cn("flex items-center gap-2 bg-slate-100 hover:bg-slate-200 disabled:opacity-60 text-slate-700 px-4 py-2 rounded-lg text-sm font-medium transition-colors")}>
              {triggering === "scoring/zefix" ? <Loader2 size={16} className="animate-spin text-blue-600" /> : <Landmark size={16} className="text-blue-600" />}
              {dict.app.settings.admin.recalculateZefixScores}
            </button>
            <button type="button" onClick={() => handleTrigger("scoring/google")} disabled={!!triggering} className={cn("flex items-center gap-2 bg-slate-100 hover:bg-slate-200 disabled:opacity-60 text-slate-700 px-4 py-2 rounded-lg text-sm font-medium transition-colors")}>
              {triggering === "scoring/google" ? <Loader2 size={16} className="animate-spin text-green-600" /> : <Search size={16} className="text-green-600" />}
              {dict.app.settings.admin.recalculateGoogleScores}
            </button>
            <button type="button" onClick={() => handleTrigger("scoring/re-geocode")} disabled={!!triggering} className={cn("flex items-center gap-2 bg-amber-50 hover:bg-amber-100 disabled:opacity-60 text-amber-700 px-4 py-2 rounded-lg text-sm font-medium transition-colors")}>
              {triggering === "scoring/re-geocode" ? <Loader2 size={16} className="animate-spin text-amber-700" /> : <MapPin size={16} className="text-amber-700" />}
              {dict.app.settings.admin.reGeocodeAllCompanies}
            </button>
            <button type="button" onClick={() => handleTrigger("scoring/reextract-purpose", { only_missing_purpose: true })} disabled={!!triggering} className={cn("flex items-center gap-2 bg-indigo-50 hover:bg-indigo-100 disabled:opacity-60 text-indigo-700 px-4 py-2 rounded-lg text-sm font-medium transition-colors")}>
              {triggering === "scoring/reextract-purpose" ? <Loader2 size={16} className="animate-spin text-indigo-700" /> : <FileText size={16} className="text-indigo-700" />}
              {dict.app.settings.admin.reExtractPurposeMissing}
            </button>
            <button type="button" onClick={() => handleTrigger("scoring/reextract-purpose", { only_missing_purpose: false })} disabled={!!triggering} className={cn("flex items-center gap-2 bg-indigo-100 hover:bg-indigo-200 disabled:opacity-60 text-indigo-800 px-4 py-2 rounded-lg text-sm font-medium transition-colors")}>
              {triggering === "scoring/reextract-purpose" ? <Loader2 size={16} className="animate-spin text-indigo-800" /> : <FileText size={16} className="text-indigo-800" />}
              {dict.app.settings.admin.reExtractPurposeAll}
            </button>
            <button type="button" onClick={() => handleTrigger("scoring/reclassify-noga", { only_missing_noga: true, only_detailed_raw: true })} disabled={!!triggering} className={cn("flex items-center gap-2 bg-emerald-50 hover:bg-emerald-100 disabled:opacity-60 text-emerald-700 px-4 py-2 rounded-lg text-sm font-medium transition-colors")}>
              {triggering === "scoring/reclassify-noga" ? <Loader2 size={16} className="animate-spin text-emerald-700" /> : <FileText size={16} className="text-emerald-700" />}
              {dict.app.settings.admin.reclassifyNogaMissing}
            </button>
            <button type="button" onClick={() => handleTrigger("scoring/reclassify-noga", { only_missing_noga: false, only_detailed_raw: true })} disabled={!!triggering} className={cn("flex items-center gap-2 bg-emerald-100 hover:bg-emerald-200 disabled:opacity-60 text-emerald-800 px-4 py-2 rounded-lg text-sm font-medium transition-colors")}>
              {triggering === "scoring/reclassify-noga" ? <Loader2 size={16} className="animate-spin text-emerald-800" /> : <FileText size={16} className="text-emerald-800" />}
              {dict.app.settings.admin.reclassifyNogaAll}
            </button>

            <button type="button" onClick={() => handleTrigger("scoring/analyze-boilerplate")} disabled={!!triggering} className={cn("flex items-center gap-2 bg-orange-50 hover:bg-orange-100 disabled:opacity-60 text-orange-700 px-4 py-2 rounded-lg text-sm font-medium transition-colors")}>
              {triggering === "scoring/analyze-boilerplate" ? <Loader2 size={16} className="animate-spin text-orange-700" /> : <Sparkles size={16} className="text-orange-700" />}
              {dict.app.settings.admin.findNewBoilerplatePatterns}
            </button>
          </div>

          <CollapsibleSection title={dict.app.settings.admin.boilerplatePatterns} description={dict.app.settings.admin.boilerplateDescription} count={boilerplate.length}>
            <div className="space-y-4">
              <div className="space-y-2">
                {boilerplate.map(bp => (
                  <div key={bp.id} className="flex items-center gap-3 px-3 py-2.5 border border-slate-200 rounded-lg bg-white">
                    <button type="button" onClick={() => handleToggle(bp.id)} className="shrink-0 text-slate-400 hover:text-slate-700 transition-colors">
                      {bp.active ? <ToggleRight size={20} className="text-blue-500" /> : <ToggleLeft size={20} />}
                    </button>
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2">
                        <p className="text-sm font-mono text-slate-700 break-all">{bp.pattern}</p>
                        {bp.truncate && (
                          <span className="shrink-0 inline-flex items-center gap-0.5 text-xs bg-amber-100 text-amber-700 border border-amber-200 rounded px-1.5 py-0.5 font-medium">
                            <Scissors size={10} /> truncate
                          </span>
                        )}
                      </div>
                      {bp.description && <p className="text-xs text-slate-400">{bp.description}</p>}
                    </div>
                    <button
                      type="button"
                      title={bp.truncate ? "Disable truncation" : "Enable truncation (strip from here to end)"}
                      onClick={() => handleToggleTruncate(bp.id)}
                      className={cn("shrink-0 p-1 transition-colors", bp.truncate ? "text-amber-500 hover:text-amber-700" : "text-slate-300 hover:text-amber-500")}
                    >
                      <Scissors size={14} />
                    </button>
                    <button type="button" onClick={() => handleDelete(bp.id)} className="shrink-0 p-1 text-slate-300 hover:text-red-500 transition-colors">
                      <Trash2 size={14} />
                    </button>
                  </div>
                ))}
              </div>
              <form onSubmit={handleAddPattern} className="flex gap-2 flex-wrap">
                <input value={newPattern.pattern} onChange={e => setNewPattern(p => ({ ...p, pattern: e.target.value }))} placeholder={dict.app.settings.admin.regexPatternPlaceholder} className={cn(inputCls, "flex-1 min-w-48")} required />
                <input value={newPattern.description} onChange={e => setNewPattern(p => ({ ...p, description: e.target.value }))} placeholder={dict.app.settings.admin.descriptionPlaceholder} className={cn(inputCls, "w-48")} />
                <label className="flex items-center gap-1.5 text-sm text-slate-600 cursor-pointer select-none shrink-0">
                  <input type="checkbox" checked={newPattern.truncate} onChange={e => setNewPattern(p => ({ ...p, truncate: e.target.checked }))} className="rounded" />
                  <Scissors size={13} className={newPattern.truncate ? "text-amber-500" : "text-slate-400"} />
                  truncate
                </label>
                <button type="submit" disabled={addingPattern} className="flex items-center gap-1.5 bg-slate-100 hover:bg-slate-200 disabled:opacity-60 text-slate-700 px-3 py-2 rounded-lg text-sm font-medium transition-colors shrink-0">
                  {addingPattern ? <Loader2 size={14} className="animate-spin" /> : <Plus size={14} />} {dict.app.settings.admin.add}
                </button>
              </form>

              {/* ── Pattern & stop word tester ── */}
              <div className="border border-slate-200 rounded-lg bg-slate-50 p-3 space-y-3">
                <p className="text-xs font-medium text-slate-500 uppercase tracking-wide">{dict.app.settings.admin.testPatternsTitle}</p>
                <textarea
                  value={testText}
                  onChange={e => { setTestText(e.target.value); setTestResults(null); }}
                  placeholder={dict.app.settings.admin.testTextPlaceholder}
                  rows={3}
                  className="w-full px-3 py-2 border border-slate-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-300 bg-white resize-y"
                />
                <button
                  type="button"
                  onClick={runPatternTest}
                  disabled={!testText.trim()}
                  className="flex items-center gap-1.5 bg-slate-700 hover:bg-slate-800 disabled:opacity-40 text-white px-3 py-1.5 rounded-lg text-sm font-medium transition-colors"
                >
                  <Search size={13} /> {dict.app.settings.admin.runTest}
                </button>
                {testResults && (
                  <div className="space-y-3 pt-1">
                    <div>
                      <p className="text-xs font-semibold text-slate-500 mb-1">{dict.app.settings.admin.boilerplateMatches} ({testResults.boilerplate.length})</p>
                      {testResults.boilerplate.length === 0
                        ? <p className="text-xs text-slate-400">{dict.app.settings.admin.noPatternMatches}</p>
                        : <ul className="space-y-1">
                            {testResults.boilerplate.map(m => (
                              <li key={m.id} className="text-xs bg-orange-50 border border-orange-200 rounded px-2 py-1 flex items-start gap-2 flex-wrap">
                                <span className="font-mono text-orange-800 break-all">{m.pattern}</span>
                                {m.truncate && (
                                  <span className="inline-flex items-center gap-0.5 text-xs bg-amber-100 text-amber-700 border border-amber-200 rounded px-1 py-0.5 font-medium shrink-0">
                                    <Scissors size={9} /> truncate
                                  </span>
                                )}
                                {m.description && <span className="text-orange-500">— {m.description}</span>}
                                <span className="text-orange-400">({m.matchCount} hit{m.matchCount !== 1 ? "s" : ""})</span>
                              </li>
                            ))}
                          </ul>
                      }
                    </div>
                    <div>
                      <p className="text-xs font-semibold text-slate-500 mb-1">{dict.app.settings.admin.textAfterStripping}</p>
                      <p className="text-xs font-mono bg-white border border-slate-200 rounded px-2 py-1.5 whitespace-pre-wrap text-slate-700">
                        {testResults.stripped || <span className="text-slate-400 italic">{dict.app.settings.admin.emptyText}</span>}
                      </p>
                    </div>
                    <div>
                      <p className="text-xs font-semibold text-slate-500 mb-1">{dict.app.settings.admin.tfidfMatchesTitle} ({testResults.tfidfMatches.length})</p>
                      {testResults.tfidfMatches.length === 0
                        ? <p className="text-xs text-slate-400">{dict.app.settings.admin.noTfidfMatches}</p>
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
