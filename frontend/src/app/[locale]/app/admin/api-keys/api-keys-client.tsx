"use client";
import { useState } from "react";
import useSWR from "swr";
import {
  Key, RefreshCw, Loader2, CheckCircle, AlertCircle, Lock,
  Eye, EyeOff, ChevronDown, ChevronRight, Trash2,
} from "lucide-react";
import {
  fetchAdminPlatformApiKey,
  fetchAdminOrgApiKeys,
  fetchAdminFunctionOverrides,
  fetchAdminStandardKeys,
  fetchAdminSearchProviderKeys,
  fetchSettings,
  saveSettings,
  updateAdminPlatformApiKey,
  setAdminOrgApiKey,
  setAdminStandardKey,
  resetAdminStandardKey,
  setAdminSearchProviderKey,
  resetAdminSearchProviderKey,
  type PlatformApiKeyStatus,
  type OrgApiKeyConfig,
  type FunctionOverride,
  type StandardKeyInfo,
  type SearchProviderKeyInfo,
} from "@/lib/api";
import type { AppSettings } from "@/lib/types";

// ── Helpers ───────────────────────────────────────────────────────────────────

const PROVIDER_LABELS: Record<string, string> = {
  claude: "Claude (Anthropic)",
  openai: "OpenAI",
  gemini: "Google Gemini",
  deepseek: "DeepSeek",
  groq: "Groq",
};

function StatusBadge({ status }: { status: PlatformApiKeyStatus["status"] }) {
  if (status === "valid")
    return (
      <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium bg-emerald-100 text-emerald-700">
        <CheckCircle size={11} /> Valid
      </span>
    );
  if (status === "invalid")
    return (
      <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium bg-red-100 text-red-700">
        <AlertCircle size={11} /> Invalid
      </span>
    );
  return (
    <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium bg-slate-100 text-slate-500">
      <Lock size={11} /> Unconfigured
    </span>
  );
}

// ── Platform tab ──────────────────────────────────────────────────────────────

function PlatformTab({
  data,
  onSaved,
}: {
  data: PlatformApiKeyStatus | undefined;
  onSaved: () => void;
}) {
  const [newKey, setNewKey] = useState("");
  const [show, setShow] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState(false);

  async function handleSave() {
    if (!newKey.trim()) return;
    setSaving(true);
    setError(null);
    setSuccess(false);
    try {
      await updateAdminPlatformApiKey(newKey.trim());
      setNewKey("");
      setSuccess(true);
      onSaved();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Unknown error");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="space-y-4">
      <div className="bg-white rounded-xl border border-slate-200 p-5">
        <div className="flex items-start justify-between gap-4 mb-4">
          <div>
            <h2 className="text-sm font-semibold text-slate-800">Platform API Key (Claude)</h2>
            <p className="text-xs text-slate-400 mt-0.5">
              Used by all orgs that don&apos;t have a custom key
            </p>
          </div>
          {data && <StatusBadge status={data.status} />}
        </div>
        {data?.keyFingerprint ? (
          <div className="flex items-center gap-2 mb-4">
            <code className="text-xs bg-slate-50 border border-slate-200 rounded px-2 py-1 font-mono text-slate-700">
              {data.keyFingerprint}
            </code>
          </div>
        ) : (
          <p className="text-xs text-slate-400 italic mb-4">No key configured</p>
        )}

        <div className="border-t border-slate-100 pt-4">
          <label className="block text-xs font-medium text-slate-600 mb-1">Update Key</label>
          <div className="flex gap-2">
            <div className="relative flex-1">
              <input
                type={show ? "text" : "password"}
                value={newKey}
                onChange={(e) => setNewKey(e.target.value)}
                placeholder="sk-ant-..."
                className="w-full text-sm border border-slate-200 rounded-lg px-3 py-1.5 pr-8 focus:outline-none focus:ring-2 focus:ring-purple-300"
              />
              <button
                type="button"
                onClick={() => setShow(!show)}
                className="absolute right-2 top-1/2 -translate-y-1/2 text-slate-400 hover:text-slate-600"
              >
                {show ? <EyeOff size={14} /> : <Eye size={14} />}
              </button>
            </div>
            <button
              onClick={handleSave}
              disabled={saving || !newKey.trim()}
              className="px-3 py-1.5 text-xs bg-purple-600 text-white rounded-lg hover:bg-purple-700 disabled:opacity-40 flex items-center gap-1.5 transition-colors"
            >
              {saving && <Loader2 size={12} className="animate-spin" />}
              Save
            </button>
          </div>
          {error && <p className="text-xs text-red-500 mt-1.5">{error}</p>}
          {success && <p className="text-xs text-emerald-600 mt-1.5">Key updated successfully.</p>}
          <p className="text-xs text-slate-400 mt-1">Key is stored server-side and never returned.</p>
        </div>
      </div>
    </div>
  );
}

// ── Standard Keys tab ─────────────────────────────────────────────────────────

function StandardKeysTab({
  data,
  onSaved,
}: {
  data: Record<string, StandardKeyInfo> | undefined;
  onSaved: () => void;
}) {
  const [editing, setEditing] = useState<string | null>(null);
  const [keyInputs, setKeyInputs] = useState<Record<string, string>>({});
  const [show, setShow] = useState<Record<string, boolean>>({});
  const [saving, setSaving] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function handleSave(provider: string) {
    const key = keyInputs[provider]?.trim();
    if (!key) return;
    setSaving(provider);
    setError(null);
    try {
      await setAdminStandardKey(provider, key);
      setKeyInputs((prev) => ({ ...prev, [provider]: "" }));
      setEditing(null);
      onSaved();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Unknown error");
    } finally {
      setSaving(null);
    }
  }

  async function handleReset(provider: string) {
    setSaving(provider);
    setError(null);
    try {
      await resetAdminStandardKey(provider);
      onSaved();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Unknown error");
    } finally {
      setSaving(null);
    }
  }

  if (!data) return null;

  return (
    <div className="space-y-3">
      <p className="text-xs text-slate-500">
        Standard keys are per-provider fallbacks used when an org has no BYO key. They take precedence
        over the platform env key.
      </p>
      {error && <p className="text-xs text-red-500">{error}</p>}
      {Object.entries(data).map(([provider, info]) => (
        <div key={provider} className="bg-white rounded-xl border border-slate-200 p-4">
          <div className="flex items-start justify-between gap-3">
            <div className="flex-1">
              <div className="flex items-center gap-2 mb-0.5">
                <span className="text-sm font-medium text-slate-800">{PROVIDER_LABELS[provider] ?? provider}</span>
                {info.has_key ? (
                  <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded-full text-[10px] font-medium bg-emerald-100 text-emerald-700">
                    <CheckCircle size={9} /> Configured
                  </span>
                ) : (
                  <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded-full text-[10px] font-medium bg-slate-100 text-slate-500">
                    <Lock size={9} /> Not set
                  </span>
                )}
              </div>
              <p className="text-xs text-slate-400">Default model: {info.defaultModel}</p>
              {info.keyFingerprint && (
                <code className="text-xs font-mono text-slate-500">{info.keyFingerprint}</code>
              )}
            </div>
            <div className="flex items-center gap-2 shrink-0">
              {info.has_key && (
                <button
                  onClick={() => void handleReset(provider)}
                  disabled={saving === provider}
                  className="flex items-center gap-1 text-xs text-red-500 hover:text-red-700 px-2 py-1 rounded border border-red-200 hover:bg-red-50 transition-colors disabled:opacity-40"
                >
                  {saving === provider ? <Loader2 size={11} className="animate-spin" /> : <Trash2 size={11} />}
                  Reset
                </button>
              )}
              <button
                onClick={() => setEditing(editing === provider ? null : provider)}
                className="flex items-center gap-1 text-xs text-purple-600 hover:text-purple-800 px-2 py-1 rounded border border-purple-200 hover:bg-purple-50 transition-colors"
              >
                <Key size={11} />
                {info.has_key ? "Rotate" : "Set key"}
              </button>
            </div>
          </div>

          {editing === provider && (
            <div className="mt-3 pt-3 border-t border-slate-100 flex gap-2">
              <div className="relative flex-1">
                <input
                  type={show[provider] ? "text" : "password"}
                  value={keyInputs[provider] ?? ""}
                  onChange={(e) => setKeyInputs((prev) => ({ ...prev, [provider]: e.target.value }))}
                  placeholder="Paste API key..."
                  className="w-full text-sm border border-slate-200 rounded-lg px-3 py-1.5 pr-8 focus:outline-none focus:ring-2 focus:ring-purple-300"
                />
                <button
                  type="button"
                  onClick={() => setShow((prev) => ({ ...prev, [provider]: !prev[provider] }))}
                  className="absolute right-2 top-1/2 -translate-y-1/2 text-slate-400 hover:text-slate-600"
                >
                  {show[provider] ? <EyeOff size={13} /> : <Eye size={13} />}
                </button>
              </div>
              <button
                onClick={() => void handleSave(provider)}
                disabled={saving === provider || !keyInputs[provider]?.trim()}
                className="px-3 py-1.5 text-xs bg-purple-600 text-white rounded-lg hover:bg-purple-700 disabled:opacity-40 flex items-center gap-1.5 transition-colors"
              >
                {saving === provider && <Loader2 size={11} className="animate-spin" />}
                Save
              </button>
              <button
                onClick={() => setEditing(null)}
                className="px-3 py-1.5 text-xs text-slate-500 hover:text-slate-700 rounded-lg border border-slate-200 hover:bg-slate-50 transition-colors"
              >
                Cancel
              </button>
            </div>
          )}
        </div>
      ))}
    </div>
  );
}

// ── Orgs tab ──────────────────────────────────────────────────────────────────

function OrgsTab({
  data,
  onSaved,
}: {
  data: OrgApiKeyConfig[] | undefined;
  onSaved: () => void;
}) {
  const [expandedOrgId, setExpandedOrgId] = useState<number | null>(null);
  const [keyInputs, setKeyInputs] = useState<Record<number, string>>({});
  const [show, setShow] = useState<Record<number, boolean>>({});
  const [saving, setSaving] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function handleSetKey(orgId: number) {
    const key = keyInputs[orgId]?.trim() || null;
    setSaving(orgId);
    setError(null);
    try {
      await setAdminOrgApiKey(orgId, key);
      setKeyInputs((prev) => ({ ...prev, [orgId]: "" }));
      onSaved();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Unknown error");
    } finally {
      setSaving(null);
    }
  }

  if (!data?.length)
    return <p className="text-xs text-slate-400 text-center py-10">No organizations found.</p>;

  return (
    <div className="space-y-2">
      {error && <p className="text-xs text-red-500">{error}</p>}
      <div className="bg-white rounded-xl border border-slate-200 overflow-hidden">
        <table className="w-full text-xs">
          <thead>
            <tr className="text-left text-slate-500 border-b border-slate-100 bg-slate-50">
              <th className="px-3 py-2 font-medium w-5"></th>
              <th className="px-3 py-2 font-medium">Organization</th>
              <th className="px-3 py-2 font-medium">BYO Feature</th>
              <th className="px-3 py-2 font-medium">Key</th>
              <th className="px-3 py-2 font-medium">Fingerprint</th>
            </tr>
          </thead>
          <tbody>
            {data.map((org) => (
              <>
                <tr
                  key={org.orgId}
                  className="border-b border-slate-50 hover:bg-slate-50/60 transition-colors cursor-pointer"
                  onClick={() => setExpandedOrgId(expandedOrgId === org.orgId ? null : org.orgId)}
                >
                  <td className="px-3 py-2 text-slate-400">
                    {expandedOrgId === org.orgId ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
                  </td>
                  <td className="px-3 py-2 font-medium text-slate-800">
                    {org.orgName}
                    <span className="ml-1 text-slate-400 font-normal font-mono">#{org.orgId}</span>
                  </td>
                  <td className="px-3 py-2">
                    {org.hasBYOFeature ? (
                      <span className="px-1.5 py-0.5 rounded-full text-[10px] font-medium bg-emerald-100 text-emerald-700">
                        Enabled
                      </span>
                    ) : (
                      <span className="px-1.5 py-0.5 rounded-full text-[10px] font-medium bg-slate-100 text-slate-500">
                        Disabled
                      </span>
                    )}
                  </td>
                  <td className="px-3 py-2">
                    {org.hasCustomKey ? (
                      <span className="inline-flex items-center gap-1 text-emerald-600">
                        <CheckCircle size={11} /> Custom
                      </span>
                    ) : (
                      <span className="text-slate-400">Platform</span>
                    )}
                  </td>
                  <td className="px-3 py-2 font-mono text-slate-500">
                    {org.keyFingerprint ?? "—"}
                  </td>
                </tr>
                {expandedOrgId === org.orgId && (
                  <tr key={`${org.orgId}-expanded`} className="bg-slate-50/80 border-b border-slate-100">
                    <td colSpan={5} className="px-6 py-3">
                      {!org.hasBYOFeature ? (
                        <p className="text-xs text-slate-400 italic">
                          BYO keys feature not enabled for this org. Upgrade their tier to allow custom keys.
                        </p>
                      ) : (
                        <div className="space-y-2">
                          <p className="text-xs text-slate-500 font-medium">
                            {org.hasCustomKey ? "Rotate or remove custom key:" : "Set a custom key for this org:"}
                          </p>
                          <div className="flex gap-2">
                            <div className="relative flex-1 max-w-sm">
                              <input
                                type={show[org.orgId] ? "text" : "password"}
                                value={keyInputs[org.orgId] ?? ""}
                                onChange={(e) => setKeyInputs((prev) => ({ ...prev, [org.orgId]: e.target.value }))}
                                placeholder="Paste API key (leave empty to reset to platform)"
                                className="w-full text-sm border border-slate-200 rounded-lg px-3 py-1.5 pr-8 focus:outline-none focus:ring-2 focus:ring-purple-300"
                              />
                              <button
                                type="button"
                                onClick={() => setShow((prev) => ({ ...prev, [org.orgId]: !prev[org.orgId] }))}
                                className="absolute right-2 top-1/2 -translate-y-1/2 text-slate-400 hover:text-slate-600"
                              >
                                {show[org.orgId] ? <EyeOff size={13} /> : <Eye size={13} />}
                              </button>
                            </div>
                            <button
                              onClick={() => void handleSetKey(org.orgId)}
                              disabled={saving === org.orgId}
                              className="px-3 py-1.5 text-xs bg-purple-600 text-white rounded-lg hover:bg-purple-700 disabled:opacity-40 flex items-center gap-1.5 transition-colors"
                            >
                              {saving === org.orgId && <Loader2 size={11} className="animate-spin" />}
                              {keyInputs[org.orgId]?.trim() ? "Set key" : "Reset to platform"}
                            </button>
                          </div>
                        </div>
                      )}
                    </td>
                  </tr>
                )}
              </>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ── Functions tab ─────────────────────────────────────────────────────────────

function FunctionsTab({ data }: { data: FunctionOverride[] | undefined }) {
  if (!data?.length)
    return <p className="text-xs text-slate-400 text-center py-10">No function overrides configured.</p>;

  return (
    <div className="space-y-3">
      <p className="text-xs text-slate-500">
        Function-level overrides let specific ML tasks use different API keys. Configure via the standard
        keys tab or org-level overrides above.
      </p>
      <div className="bg-white rounded-xl border border-slate-200 overflow-hidden">
        <table className="w-full text-xs">
          <thead>
            <tr className="text-left text-slate-500 border-b border-slate-100 bg-slate-50">
              <th className="px-3 py-2 font-medium">Function</th>
              <th className="px-3 py-2 font-medium">Provider</th>
              <th className="px-3 py-2 font-medium">Key</th>
              <th className="px-3 py-2 font-medium">Status</th>
            </tr>
          </thead>
          <tbody>
            {data.map((fn) => (
              <tr key={fn.functionId} className="border-b border-slate-50 last:border-0 hover:bg-slate-50/60">
                <td className="px-3 py-2 font-medium text-slate-800">
                  {fn.functionName}
                  <span className="ml-1.5 font-mono text-slate-400 font-normal">{fn.functionId}</span>
                </td>
                <td className="px-3 py-2">
                  {fn.provider === "custom" ? (
                    <span className="px-1.5 py-0.5 rounded-full text-[10px] font-medium bg-purple-100 text-purple-700">
                      Custom
                    </span>
                  ) : (
                    <span className="px-1.5 py-0.5 rounded-full text-[10px] font-medium bg-slate-100 text-slate-500">
                      Platform
                    </span>
                  )}
                </td>
                <td className="px-3 py-2 font-mono text-slate-500">
                  {fn.keyFingerprint ?? "—"}
                </td>
                <td className="px-3 py-2">
                  <span
                    className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded-full text-[10px] font-medium ${
                      fn.status === "active"
                        ? "bg-emerald-100 text-emerald-700"
                        : "bg-slate-100 text-slate-500"
                    }`}
                  >
                    {fn.status}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ── Main ──────────────────────────────────────────────────────────────────────

type Tab = "platform" | "standard" | "orgs" | "functions" | "search";

function SearchProviderKeysTab({
  data,
  onSaved,
}: {
  data: Record<string, SearchProviderKeyInfo> | undefined;
  onSaved: () => void;
}) {
  const [editing, setEditing] = useState<string | null>(null);
  const [keyInputs, setKeyInputs] = useState<Record<string, string>>({});
  const [show, setShow] = useState<Record<string, boolean>>({});
  const [saving, setSaving] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [providerSaving, setProviderSaving] = useState(false);
  const { data: appSettings, mutate: mutateSettings } = useSWR<AppSettings>("settings", fetchSettings);
  const activeProvider = (appSettings as Record<string, string> | undefined)?.google_search_provider ?? "serper";

  async function handleProviderChange(provider: string) {
    setProviderSaving(true);
    try {
      await saveSettings({ ...(appSettings ?? {}), google_search_provider: provider } as Partial<AppSettings>);
      await mutateSettings();
    } finally {
      setProviderSaving(false);
    }
  }

  async function handleSave(provider: string) {
    const key = keyInputs[provider]?.trim();
    if (!key) return;
    setSaving(provider);
    setError(null);
    try {
      await setAdminSearchProviderKey(provider, key);
      setKeyInputs((prev) => ({ ...prev, [provider]: "" }));
      setEditing(null);
      onSaved();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Unknown error");
    } finally {
      setSaving(null);
    }
  }

  async function handleReset(provider: string) {
    setSaving(provider);
    setError(null);
    try {
      await resetAdminSearchProviderKey(provider);
      onSaved();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Unknown error");
    } finally {
      setSaving(null);
    }
  }

  if (!data) return null;

  return (
    <div className="space-y-4">
      {/* Active provider selector */}
      <div className="bg-white rounded-xl border border-slate-200 p-4">
        <div className="flex items-center justify-between gap-4">
          <div>
            <p className="text-sm font-medium text-slate-800">Active search provider</p>
            <p className="text-xs text-slate-400 mt-0.5">Which provider is used for URL fetch jobs</p>
          </div>
          <div className="flex items-center gap-2">
            <select
              value={activeProvider}
              onChange={(e) => void handleProviderChange(e.target.value)}
              disabled={providerSaving || !appSettings}
              className="text-sm border border-slate-200 rounded-lg px-3 py-1.5 focus:outline-none focus:ring-2 focus:ring-purple-300 disabled:opacity-60"
            >
              <option value="serper">Serper.dev</option>
              <option value="scrapingdog">ScrapingDog (google.ch)</option>
            </select>
            {providerSaving && <Loader2 size={14} className="animate-spin text-slate-400" />}
          </div>
        </div>
      </div>

      <p className="text-xs text-slate-500">
        API keys for Serper.dev and ScrapingDog. DB keys override the <code className="font-mono">.env</code> value.
      </p>
      {error && <p className="text-xs text-red-500">{error}</p>}
      {Object.entries(data).map(([provider, info]) => (
        <div key={provider} className="bg-white rounded-xl border border-slate-200 p-4">
          <div className="flex items-start justify-between gap-3">
            <div className="flex-1">
              <div className="flex items-center gap-2 mb-0.5 flex-wrap">
                <span className="text-sm font-medium text-slate-800">{info.providerName}</span>
                {info.source !== "none" ? (
                  <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded-full text-[10px] font-medium bg-emerald-100 text-emerald-700">
                    <CheckCircle size={9} /> Configured
                  </span>
                ) : (
                  <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded-full text-[10px] font-medium bg-slate-100 text-slate-500">
                    <Lock size={9} /> Not set
                  </span>
                )}
                {info.source === "db" && (
                  <span className="inline-flex items-center px-1.5 py-0.5 rounded-full text-[10px] font-medium bg-blue-100 text-blue-700">DB</span>
                )}
                {info.source === "env" && (
                  <span className="inline-flex items-center px-1.5 py-0.5 rounded-full text-[10px] font-medium bg-amber-100 text-amber-700">env</span>
                )}
              </div>
              {info.keyFingerprint && (
                <code className="text-xs font-mono text-slate-500">{info.keyFingerprint}</code>
              )}
            </div>
            <div className="flex items-center gap-2 shrink-0">
              {info.hasDbKey && (
                <button
                  onClick={() => void handleReset(provider)}
                  disabled={saving === provider}
                  className="flex items-center gap-1 text-xs text-red-500 hover:text-red-700 px-2 py-1 rounded border border-red-200 hover:bg-red-50 transition-colors disabled:opacity-40"
                >
                  {saving === provider ? <Loader2 size={11} className="animate-spin" /> : <Trash2 size={11} />}
                  Remove DB key
                </button>
              )}
              <button
                onClick={() => setEditing(editing === provider ? null : provider)}
                className="flex items-center gap-1 text-xs text-purple-600 hover:text-purple-800 px-2 py-1 rounded border border-purple-200 hover:bg-purple-50 transition-colors"
              >
                <Key size={11} />
                {info.hasDbKey ? "Rotate" : "Set key"}
              </button>
            </div>
          </div>

          {editing === provider && (
            <div className="mt-3 pt-3 border-t border-slate-100 flex gap-2">
              <div className="relative flex-1">
                <input
                  type={show[provider] ? "text" : "password"}
                  value={keyInputs[provider] ?? ""}
                  onChange={(e) => setKeyInputs((prev) => ({ ...prev, [provider]: e.target.value }))}
                  placeholder="Paste API key..."
                  className="w-full text-sm border border-slate-200 rounded-lg px-3 py-1.5 pr-8 focus:outline-none focus:ring-2 focus:ring-purple-300"
                />
                <button
                  type="button"
                  onClick={() => setShow((prev) => ({ ...prev, [provider]: !prev[provider] }))}
                  className="absolute right-2 top-1/2 -translate-y-1/2 text-slate-400 hover:text-slate-600"
                >
                  {show[provider] ? <EyeOff size={13} /> : <Eye size={13} />}
                </button>
              </div>
              <button
                onClick={() => void handleSave(provider)}
                disabled={saving === provider || !keyInputs[provider]?.trim()}
                className="px-3 py-1.5 text-xs bg-purple-600 text-white rounded-lg hover:bg-purple-700 disabled:opacity-40 flex items-center gap-1.5 transition-colors"
              >
                {saving === provider && <Loader2 size={11} className="animate-spin" />}
                Save
              </button>
            </div>
          )}
        </div>
      ))}
    </div>
  );
}

export default function ApiKeysClient() {
  const [tab, setTab] = useState<Tab>("platform");

  const { data: platformData, isLoading: loadingPlatform, mutate: mutatePlatform } =
    useSWR<PlatformApiKeyStatus>("admin-api-keys-platform", fetchAdminPlatformApiKey);

  const { data: standardData, isLoading: loadingStandard, mutate: mutateStandard } =
    useSWR<Record<string, StandardKeyInfo>>("admin-api-keys-standard", fetchAdminStandardKeys);

  const { data: orgsData, isLoading: loadingOrgs, mutate: mutateOrgs } =
    useSWR<OrgApiKeyConfig[]>("admin-api-keys-orgs", fetchAdminOrgApiKeys);

  const { data: fnData, isLoading: loadingFn } =
    useSWR<FunctionOverride[]>("admin-api-keys-functions", fetchAdminFunctionOverrides);

  const { data: searchData, isLoading: loadingSearch, mutate: mutateSearch } =
    useSWR<Record<string, SearchProviderKeyInfo>>("admin-api-keys-search", fetchAdminSearchProviderKeys);

  const isLoading =
    (tab === "platform" && loadingPlatform) ||
    (tab === "standard" && loadingStandard) ||
    (tab === "orgs" && loadingOrgs) ||
    (tab === "functions" && loadingFn) ||
    (tab === "search" && loadingSearch);

  const TABS: { id: Tab; label: string }[] = [
    { id: "platform", label: "Platform Key" },
    { id: "standard", label: "Standard Keys" },
    { id: "orgs", label: "Per-Org Keys" },
    { id: "functions", label: "Function Overrides" },
    { id: "search", label: "Search Provider Keys" },
  ];

  function handleRefresh() {
    void mutatePlatform();
    void mutateStandard();
    void mutateOrgs();
  }

  return (
    <div className="p-6 max-w-5xl space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-lg font-semibold text-slate-800">LLM API Key Management</h1>
          <p className="text-xs text-slate-400 mt-0.5">
            Configure platform, per-provider, and per-org API keys · superadmin only
          </p>
        </div>
        <button
          onClick={handleRefresh}
          className="flex items-center gap-1.5 text-xs text-slate-500 hover:text-slate-700 px-2.5 py-1.5 rounded-lg border border-slate-200 hover:bg-slate-50 transition-colors"
        >
          <RefreshCw size={12} /> Refresh
        </button>
      </div>

      {/* Tab bar */}
      <div className="flex gap-1 border-b border-slate-200">
        {TABS.map(({ id, label }) => (
          <button
            key={id}
            onClick={() => setTab(id)}
            className={`px-3 py-2 text-xs font-medium transition-colors border-b-2 -mb-px ${
              tab === id
                ? "border-purple-500 text-purple-700"
                : "border-transparent text-slate-500 hover:text-slate-700"
            }`}
          >
            {label}
          </button>
        ))}
      </div>

      {isLoading ? (
        <div className="flex items-center justify-center h-40 text-slate-400">
          <Loader2 size={20} className="animate-spin" />
        </div>
      ) : (
        <>
          {tab === "platform" && (
            <PlatformTab data={platformData} onSaved={() => void mutatePlatform()} />
          )}
          {tab === "standard" && (
            <StandardKeysTab data={standardData} onSaved={() => void mutateStandard()} />
          )}
          {tab === "orgs" && (
            <OrgsTab data={orgsData} onSaved={() => void mutateOrgs()} />
          )}
          {tab === "functions" && <FunctionsTab data={fnData} />}
          {tab === "search" && (
            <SearchProviderKeysTab data={searchData} onSaved={() => void mutateSearch()} />
          )}
        </>
      )}
    </div>
  );
}
