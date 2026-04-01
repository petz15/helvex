"use client";
import { useState } from "react";
import useSWR from "swr";
import {
  Building2, Edit2, Check, X, Trash2, Plus, Loader2, ShieldCheck, UserCog, Mail, Sparkles, ChevronDown, ChevronUp,
} from "lucide-react";
import { useRouter } from "next/navigation";
import {
  fetchCurrentUser,
  fetchOrg,
  fetchOrgMembers,
  fetchOrgSettings,
  saveOrgWorkspaceSettings,
  updateOrg,
  addOrgMember,
  updateMemberRole,
  removeOrgMember,
  sendInvite,
  deleteOrg,
  triggerJob,
  type OrgMember,
} from "@/lib/api";

const inputCls =
  "w-full px-3 py-2 border border-slate-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-300 focus:border-transparent bg-white";

const textareaCls =
  "w-full px-3 py-2 border border-slate-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-300 focus:border-transparent bg-white resize-y font-mono";

// ── Scoring & AI config section ───────────────────────────────────────────────

const ALL_SETTING_KEYS = [
  "anthropic_api_key",
  "claude_target_description",
  "claude_classify_categories",
  "claude_classify_prompt",
  "scoring_target_clusters",
  "scoring_exclude_clusters",
  "scoring_target_keywords",
  "scoring_exclude_keywords",
  "scoring_origin_lat",
  "scoring_origin_lon",
  "scoring_legal_form_scores",
  "scoring_weight_ai",
  "scoring_weight_web",
  "scoring_weight_flex",
] as const;

type SettingKey = typeof ALL_SETTING_KEYS[number];

function OrgScoringSection({ orgId, isAdmin }: { orgId: number; isAdmin: boolean }) {
  const { data: saved = {}, mutate: reloadSettings } = useSWR(
    ["org-settings", orgId],
    () => fetchOrgSettings(orgId),
  );

  const [form, setForm] = useState<Record<string, string>>({});
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [banner, setBanner] = useState<{ kind: "success" | "error"; message: string } | null>(null);
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [classifying, setClassifying] = useState(false);
  const [classifyBanner, setClassifyBanner] = useState<string | null>(null);

  async function handleClassify() {
    setClassifying(true);
    setClassifyBanner(null);
    try {
      await triggerJob("jobs/scoring/claude", { limit: 500, only_unscored: false });
      setClassifyBanner("AI classification job queued.");
      setTimeout(() => setClassifyBanner(null), 5000);
    } catch {
      setClassifyBanner("Failed to start classification job.");
    } finally {
      setClassifying(false);
    }
  }

  // Merge saved values into form on load (only if form not yet touched)
  const effective = { ...saved, ...form };

  function set(key: SettingKey, value: string) {
    setForm((f) => ({ ...f, [key]: value }));
    setDirty(true);
  }

  function val(key: SettingKey): string {
    return effective[key] ?? "";
  }

  async function handleSave() {
    setSaving(true);
    setBanner(null);
    try {
      const payload: Record<string, string | null> = {};
      for (const key of ALL_SETTING_KEYS) {
        const v = effective[key];
        payload[key] = (v === undefined || v === "") ? null : v;
      }
      await saveOrgWorkspaceSettings(orgId, payload);
      await reloadSettings();
      setForm({});
      setDirty(false);
      setBanner({ kind: "success", message: "Scoring & AI config saved." });
      setTimeout(() => setBanner(null), 4000);
    } catch (e) {
      setBanner({ kind: "error", message: e instanceof Error ? e.message : "Failed to save." });
    } finally {
      setSaving(false);
    }
  }

  const apiKeyIsSet = !!saved["anthropic_api_key"];
  const apiKeyDraftSet = form["anthropic_api_key"] !== undefined;

  return (
    <div className="space-y-4">
      {banner && (
        <div className={`rounded-lg border px-4 py-3 text-sm ${banner.kind === "success" ? "border-green-200 bg-green-50 text-green-800" : "border-red-200 bg-red-50 text-red-800"}`}>
          {banner.message}
        </div>
      )}

      <div className="rounded-xl border border-slate-200 bg-white p-5 space-y-5">

        {/* Anthropic API key */}
        <div className="space-y-1.5">
          <label className="block text-sm font-semibold text-slate-700">
            Anthropic API key
            {apiKeyIsSet && !apiKeyDraftSet && (
              <span className="ml-2 text-xs font-normal text-green-600 bg-green-50 border border-green-200 rounded px-1.5 py-0.5">configured</span>
            )}
          </label>
          <p className="text-xs text-slate-500">
            Required for AI scoring. Stored per-workspace — never shared across orgs.
          </p>
          {apiKeyIsSet && !apiKeyDraftSet ? (
            <div className="flex items-center gap-2">
              <span className="text-sm text-slate-400 font-mono">sk-ant-••••••••</span>
              {isAdmin && (
                <button
                  type="button"
                  onClick={() => set("anthropic_api_key", "")}
                  className="text-xs text-blue-600 hover:underline"
                >
                  Replace
                </button>
              )}
            </div>
          ) : (
            <input
              type="password"
              placeholder="sk-ant-…"
              value={val("anthropic_api_key")}
              onChange={(e) => set("anthropic_api_key", e.target.value)}
              disabled={!isAdmin}
              className={inputCls}
              autoComplete="new-password"
            />
          )}
        </div>

        {/* AI target description — most important */}
        <div className="space-y-1.5">
          <label className="block text-sm font-semibold text-slate-700">
            What are you looking for?
          </label>
          <p className="text-xs text-slate-500">
            Describe your ideal target company. This is appended to the AI classification prompt and directly shapes lead scores for your org.
          </p>
          <textarea
            rows={4}
            placeholder="e.g. We are looking for B2B software companies in the DACH region with 5–50 employees that could benefit from HR automation tools."
            value={val("claude_target_description")}
            onChange={(e) => set("claude_target_description", e.target.value)}
            disabled={!isAdmin}
            className={textareaCls}
          />
        </div>

        {/* AI categories */}
        <div className="space-y-1.5">
          <label className="block text-sm font-medium text-slate-700">Custom categories</label>
          <p className="text-xs text-slate-500">
            One category per line. If set, the AI will only output these exact labels instead of free-form ones. Leave empty to use free-form categorisation.
          </p>
          <textarea
            rows={5}
            placeholder={"SaaS\nFinTech\nLogistics\nHealthcare\nE-Commerce"}
            value={val("claude_classify_categories")}
            onChange={(e) => set("claude_classify_categories", e.target.value)}
            disabled={!isAdmin}
            className={textareaCls}
          />
        </div>

        {/* Advanced toggle */}
        <button
          type="button"
          onClick={() => setShowAdvanced((v) => !v)}
          className="flex items-center gap-1.5 text-xs text-slate-500 hover:text-slate-700 transition-colors"
        >
          {showAdvanced ? <ChevronUp size={13} /> : <ChevronDown size={13} />}
          {showAdvanced ? "Hide advanced settings" : "Show advanced settings"}
        </button>

        {showAdvanced && (
          <div className="space-y-5 pt-2 border-t border-slate-100">

            {/* Flex score — cluster targets */}
            <div className="grid grid-cols-2 gap-4">
              <div className="space-y-1.5">
                <label className="block text-sm font-medium text-slate-700">Target clusters</label>
                <p className="text-xs text-slate-500">Pipe-separated cluster label substrings. Hits add points to Flex score.</p>
                <textarea rows={3} value={val("scoring_target_clusters")} onChange={(e) => set("scoring_target_clusters", e.target.value)} disabled={!isAdmin} className={textareaCls} placeholder="software|tech|digital" />
              </div>
              <div className="space-y-1.5">
                <label className="block text-sm font-medium text-slate-700">Exclude clusters</label>
                <p className="text-xs text-slate-500">Pipe-separated cluster label substrings to penalise.</p>
                <textarea rows={3} value={val("scoring_exclude_clusters")} onChange={(e) => set("scoring_exclude_clusters", e.target.value)} disabled={!isAdmin} className={textareaCls} placeholder="immobilien|gastronomie" />
              </div>
            </div>

            {/* Flex score — keyword targets */}
            <div className="grid grid-cols-2 gap-4">
              <div className="space-y-1.5">
                <label className="block text-sm font-medium text-slate-700">Target keywords</label>
                <p className="text-xs text-slate-500">Pipe-separated purpose keyword substrings. Hits add points.</p>
                <textarea rows={3} value={val("scoring_target_keywords")} onChange={(e) => set("scoring_target_keywords", e.target.value)} disabled={!isAdmin} className={textareaCls} placeholder="software|beratung|entwicklung" />
              </div>
              <div className="space-y-1.5">
                <label className="block text-sm font-medium text-slate-700">Exclude keywords</label>
                <p className="text-xs text-slate-500">Pipe-separated keywords to penalise.</p>
                <textarea rows={3} value={val("scoring_exclude_keywords")} onChange={(e) => set("scoring_exclude_keywords", e.target.value)} disabled={!isAdmin} className={textareaCls} placeholder="treuhand|buchhaltung" />
              </div>
            </div>

            {/* Distance origin */}
            <div className="space-y-1.5">
              <label className="block text-sm font-medium text-slate-700">Distance origin (lat / lon)</label>
              <p className="text-xs text-slate-500">Your geographic base for distance scoring. Closer companies score higher.</p>
              <div className="flex gap-3">
                <input type="number" step="0.0001" value={val("scoring_origin_lat")} onChange={(e) => set("scoring_origin_lat", e.target.value)} disabled={!isAdmin} className={inputCls + " max-w-[180px]"} placeholder="46.9266" />
                <input type="number" step="0.0001" value={val("scoring_origin_lon")} onChange={(e) => set("scoring_origin_lon", e.target.value)} disabled={!isAdmin} className={inputCls + " max-w-[180px]"} placeholder="7.4817" />
              </div>
            </div>

            {/* Legal form scores */}
            <div className="space-y-1.5">
              <label className="block text-sm font-medium text-slate-700">Legal form scores</label>
              <p className="text-xs text-slate-500">Comma-separated <code className="bg-slate-100 px-1 rounded text-xs">form:points</code> pairs, e.g. <code className="bg-slate-100 px-1 rounded text-xs">ag:20,gmbh:15,eg:10</code></p>
              <input type="text" value={val("scoring_legal_form_scores")} onChange={(e) => set("scoring_legal_form_scores", e.target.value)} disabled={!isAdmin} className={inputCls} placeholder="ag:20,gmbh:15,eg:10,einzelfirma:8" />
            </div>

            {/* Combined score weights */}
            <div className="space-y-1.5">
              <label className="block text-sm font-medium text-slate-700">Combined score weights</label>
              <p className="text-xs text-slate-500">How much each score type contributes to the combined score. Defaults: AI 70 %, Web 20 %, Flex 10 %.</p>
              <div className="flex gap-3">
                {(["scoring_weight_ai", "scoring_weight_web", "scoring_weight_flex"] as const).map((key) => (
                  <div key={key} className="flex-1">
                    <label className="block text-xs text-slate-500 mb-1">
                      {key === "scoring_weight_ai" ? "AI" : key === "scoring_weight_web" ? "Web" : "Flex"}
                    </label>
                    <div className="flex items-center gap-2">
                      <input
                        type="range" min={0} max={1} step={0.05}
                        value={val(key) || (key === "scoring_weight_ai" ? "0.70" : key === "scoring_weight_web" ? "0.20" : "0.10")}
                        onChange={(e) => set(key, e.target.value)}
                        disabled={!isAdmin}
                        className="flex-1"
                      />
                      <span className="text-xs text-slate-600 w-10 text-right">
                        {Math.round(parseFloat(val(key) || (key === "scoring_weight_ai" ? "0.70" : key === "scoring_weight_web" ? "0.20" : "0.10")) * 100)} %
                      </span>
                    </div>
                  </div>
                ))}
              </div>
            </div>

            {/* Prompt override */}
            <div className="space-y-1.5">
              <label className="block text-sm font-medium text-slate-700">AI prompt override</label>
              <p className="text-xs text-slate-500">
                Replaces the entire default classification prompt. Advanced — leave empty to use the default prompt with your target description appended.
              </p>
              <textarea
                rows={6}
                value={val("claude_classify_prompt")}
                onChange={(e) => set("claude_classify_prompt", e.target.value)}
                disabled={!isAdmin}
                className={textareaCls}
                placeholder="Leave empty to use the default prompt…"
              />
            </div>
          </div>
        )}

        {classifyBanner && (
          <p className="text-xs text-blue-700 bg-blue-50 border border-blue-200 rounded-lg px-3 py-2">{classifyBanner}</p>
        )}

        {isAdmin && (
          <div className="flex items-center gap-3 pt-2">
            <button
              onClick={handleSave}
              disabled={saving || !dirty}
              className="flex items-center gap-1.5 bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white px-4 py-2 rounded-lg text-sm font-medium transition-colors"
            >
              {saving ? <Loader2 size={14} className="animate-spin" /> : <Sparkles size={14} />}
              Save config
            </button>
            <button
              onClick={handleClassify}
              disabled={classifying}
              className="flex items-center gap-1.5 bg-purple-600 hover:bg-purple-700 disabled:opacity-50 text-white px-4 py-2 rounded-lg text-sm font-medium transition-colors"
            >
              {classifying ? <Loader2 size={14} className="animate-spin" /> : <Sparkles size={14} />}
              Run AI Classification
            </button>
            {dirty && (
              <button
                onClick={() => { setForm({}); setDirty(false); }}
                className="text-sm text-slate-500 hover:text-slate-700 px-3 py-2 rounded-lg hover:bg-slate-100 transition-colors"
              >
                Discard
              </button>
            )}
            {!dirty && <span className="text-xs text-slate-400">No unsaved changes</span>}
          </div>
        )}
      </div>
    </div>
  );
}

const ROLES = ["viewer", "member", "admin", "owner"] as const;
type Role = (typeof ROLES)[number];

const ROLE_COLORS: Record<Role, string> = {
  owner: "bg-purple-100 text-purple-700",
  admin: "bg-blue-100 text-blue-700",
  member: "bg-slate-100 text-slate-700",
  viewer: "bg-slate-50 text-slate-500",
};

function RoleBadge({ role }: { role: string }) {
  const cls = ROLE_COLORS[role as Role] ?? "bg-slate-100 text-slate-600";
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-medium capitalize ${cls}`}>
      {role}
    </span>
  );
}

function Banner({ kind, message }: { kind: "success" | "error"; message: string }) {
  return (
    <div
      role="status"
      className={`rounded-lg border px-4 py-3 text-sm ${
        kind === "success"
          ? "border-green-200 bg-green-50 text-green-800"
          : "border-red-200 bg-red-50 text-red-800"
      }`}
    >
      {message}
    </div>
  );
}

function SectionTitle({ title }: { title: string }) {
  return (
    <h2 className="text-sm font-semibold text-slate-500 uppercase tracking-wider pt-4 pb-2 border-b border-slate-100">
      {title}
    </h2>
  );
}

export function OrgClient({ embedded = false }: { embedded?: boolean }) {
  const { data: me, mutate: reloadMe } = useSWR("me", fetchCurrentUser);
  const router = useRouter();
  const orgId = me?.org?.id;

  const { data: org, mutate: reloadOrg } = useSWR(
    orgId ? ["org", orgId] : null,
    () => fetchOrg(orgId!),
  );
  const { data: members = [], mutate: reloadMembers } = useSWR(
    orgId ? ["members", orgId] : null,
    () => fetchOrgMembers(orgId!),
  );

  // Org name editing
  const [editingName, setEditingName] = useState(false);
  const [nameValue, setNameValue] = useState("");
  const [savingName, setSavingName] = useState(false);
  const [nameBanner, setNameBanner] = useState<{ kind: "success" | "error"; message: string } | null>(null);

  // Add member form (direct create)
  const [showAddForm, setShowAddForm] = useState(false);
  const [addForm, setAddForm] = useState({ email: "", password: "", org_role: "member" as Role });

  // Invite by email
  const [showInviteForm, setShowInviteForm] = useState(false);
  const [inviteEmail, setInviteEmail] = useState("");
  const [sendingInvite, setSendingInvite] = useState(false);
  const [inviteBanner, setInviteBanner] = useState<{ kind: "success" | "error"; message: string } | null>(null);
  const [addingMember, setAddingMember] = useState(false);
  const [addBanner, setAddBanner] = useState<{ kind: "success" | "error"; message: string } | null>(null);

  // Role editing per member
  const [editingRoleFor, setEditingRoleFor] = useState<number | null>(null);
  const [pendingRole, setPendingRole] = useState<Role>("member");
  const [savingRole, setSavingRole] = useState(false);

  // Remove member
  const [removingId, setRemovingId] = useState<number | null>(null);
  const [memberBanner, setMemberBanner] = useState<{ kind: "success" | "error"; message: string } | null>(null);

  // Delete org
  const [deletingOrg, setDeletingOrg] = useState(false);

  const isOwner = me?.org_role === "owner" || me?.is_superadmin;
  const isAdmin = isOwner || me?.org_role === "admin";

  function flash(
    setter: React.Dispatch<React.SetStateAction<{ kind: "success" | "error"; message: string } | null>>,
    kind: "success" | "error",
    message: string,
  ) {
    setter({ kind, message });
    setTimeout(() => setter(null), 4000);
  }

  async function handleDeleteOrg() {
    if (!orgId || !org) return;
    const confirmed = confirm(
      `Delete "${org.name}"? All ${org.member_count} member(s) will be removed from the org. This cannot be undone.`
    );
    if (!confirmed) return;
    setDeletingOrg(true);
    try {
      await deleteOrg(orgId);
      await reloadMe();
      router.push("/app/search");
    } catch (e) {
      flash(setNameBanner, "error", e instanceof Error ? e.message : "Failed to delete org");
      setDeletingOrg(false);
    }
  }

  async function handleSaveName() {
    if (!orgId || !nameValue.trim()) return;
    setSavingName(true);
    try {
      await updateOrg(orgId, { name: nameValue.trim() });
      await reloadOrg();
      setEditingName(false);
      flash(setNameBanner, "success", "Organization name updated.");
    } catch (e) {
      flash(setNameBanner, "error", e instanceof Error ? e.message : "Failed to update name");
    } finally {
      setSavingName(false);
    }
  }

  async function handleAddMember(e: React.FormEvent) {
    e.preventDefault();
    if (!orgId) return;
    setAddingMember(true);
    setAddBanner(null);
    try {
      await addOrgMember(orgId, {
        email: addForm.email,
        password: addForm.password,
        org_role: addForm.org_role,
      });
      setAddForm({ email: "", password: "", org_role: "member" });
      setShowAddForm(false);
      await reloadMembers();
      flash(setMemberBanner, "success", `User "${addForm.email}" added to org.`);
    } catch (e) {
      flash(setAddBanner, "error", e instanceof Error ? e.message : "Failed to add member");
    } finally {
      setAddingMember(false);
    }
  }

  async function handleSendInvite(e: React.FormEvent) {
    e.preventDefault();
    if (!orgId) return;
    setSendingInvite(true);
    setInviteBanner(null);
    try {
      await sendInvite(orgId, inviteEmail);
      setInviteEmail("");
      setShowInviteForm(false);
      flash(setMemberBanner, "success", `Invite sent to ${inviteEmail}.`);
    } catch (err) {
      flash(setInviteBanner, "error", err instanceof Error ? err.message : "Failed to send invite");
    } finally {
      setSendingInvite(false);
    }
  }

  async function handleSaveRole(member: OrgMember) {
    if (!orgId) return;
    setSavingRole(true);
    try {
      await updateMemberRole(orgId, member.id, pendingRole);
      await reloadMembers();
      setEditingRoleFor(null);
      flash(setMemberBanner, "success", `Role updated for "${member.email}".`);
    } catch (e) {
      flash(setMemberBanner, "error", e instanceof Error ? e.message : "Failed to update role");
    } finally {
      setSavingRole(false);
    }
  }

  async function handleRemove(member: OrgMember) {
    if (!orgId) return;
    if (!confirm(`Remove "${member.email}" from the org?`)) return;
    setRemovingId(member.id);
    try {
      await removeOrgMember(orgId, member.id);
      await reloadMembers();
      await reloadOrg();
      flash(setMemberBanner, "success", `"${member.email}" removed from org.`);
    } catch (e) {
      flash(setMemberBanner, "error", e instanceof Error ? e.message : "Failed to remove member");
    } finally {
      setRemovingId(null);
    }
  }

  if (!me || !org) {
    return <div className={embedded ? "text-slate-400 text-sm" : "p-6 text-slate-400 text-sm"}>Loading…</div>;
  }

  if (!me.org_id) {
    return (
      <div className={embedded ? "" : "p-6 max-w-xl mx-auto"}>
        <div className="rounded-lg border border-slate-200 bg-slate-50 p-6 text-center text-slate-500 text-sm">
          You are not a member of any organization.
        </div>
      </div>
    );
  }

  if (!isAdmin) {
    return (
      <div className={embedded ? "text-slate-500 text-sm" : "p-6 max-w-xl mx-auto text-slate-500 text-sm"}>
        You need admin or owner role to manage the organization.
      </div>
    );
  }

  if (me && !me.org_id) {
    return (
      <div className={embedded ? "flex flex-col items-center justify-center py-16 text-center" : "p-6 max-w-3xl mx-auto flex flex-col items-center justify-center py-24 text-center"}>
        <span className="text-5xl mb-4 select-none">😢</span>
        <h2 className="text-lg font-semibold text-slate-800 mb-2">You are sadly not part of a Team.</h2>
        <p className="text-sm text-slate-500">Create one and invite others!</p>
      </div>
    );
  }

  return (
    <div className={embedded ? "space-y-5" : "p-6 max-w-3xl mx-auto space-y-5"}>
      {/* Header */}
      {!embedded && (
        <div className="flex items-center gap-3">
          <Building2 size={22} className="text-blue-600" />
          <div>
            <h1 className="text-xl font-semibold text-slate-900">Organization</h1>
            <p className="text-sm text-slate-500 mt-0.5">Manage your org and its members</p>
          </div>
        </div>
      )}

      {/* Org info */}
      <SectionTitle title="Organization info" />
      {nameBanner && <Banner {...nameBanner} />}
      <div className="rounded-xl border border-slate-200 bg-white p-5 space-y-4">
        <div className="flex items-center justify-between gap-4">
          <div className="space-y-1 flex-1 min-w-0">
            {editingName ? (
              <div className="flex items-center gap-2">
                <input
                  autoFocus
                  value={nameValue}
                  onChange={(e) => setNameValue(e.target.value)}
                  className={inputCls + " max-w-xs"}
                  placeholder="Organization name"
                />
                <button
                  onClick={handleSaveName}
                  disabled={savingName || !nameValue.trim()}
                  className="p-1.5 rounded-lg bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-50"
                >
                  {savingName ? <Loader2 size={14} className="animate-spin" /> : <Check size={14} />}
                </button>
                <button
                  onClick={() => setEditingName(false)}
                  className="p-1.5 rounded-lg bg-slate-100 text-slate-500 hover:bg-slate-200"
                >
                  <X size={14} />
                </button>
              </div>
            ) : (
              <div className="flex items-center gap-2">
                <span className="text-base font-semibold text-slate-900">{org.name}</span>
                {isOwner && (
                  <button
                    onClick={() => { setNameValue(org.name); setEditingName(true); }}
                    className="p-1 text-slate-400 hover:text-slate-700 rounded"
                  >
                    <Edit2 size={13} />
                  </button>
                )}
              </div>
            )}
            <p className="text-xs text-slate-400 font-mono">{org.slug}</p>
          </div>
          <div className="text-right shrink-0">
            <span className="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-blue-50 text-blue-700 capitalize">
              {org.tier}
            </span>
            <p className="text-xs text-slate-400 mt-1">{org.member_count} member{org.member_count !== 1 ? "s" : ""}</p>
          </div>
        </div>
      </div>

      {/* Members */}
      <div className="flex items-center justify-between">
        <SectionTitle title="Members" />
        {isAdmin && (
          <div className="flex items-center gap-3 mt-1">
            <button
              onClick={() => { setShowInviteForm((v) => !v); setShowAddForm(false); }}
              className="flex items-center gap-1.5 text-sm text-blue-600 hover:text-blue-800 font-medium transition-colors"
            >
              <Mail size={14} />
              Invite by email
            </button>
            {isOwner && (
              <button
                onClick={() => { setShowAddForm((v) => !v); setShowInviteForm(false); }}
                className="flex items-center gap-1.5 text-sm text-slate-500 hover:text-slate-700 font-medium transition-colors"
              >
                <Plus size={14} />
                Create account
              </button>
            )}
          </div>
        )}
      </div>

      {memberBanner && <Banner {...memberBanner} />}

      {/* Invite by email form */}
      {showInviteForm && isAdmin && (
        <form
          onSubmit={handleSendInvite}
          className="rounded-xl border border-blue-200 bg-blue-50 p-4 space-y-3"
        >
          <div className="flex items-center gap-2 text-sm font-medium text-blue-700">
            <Mail size={14} />
            Invite by email
          </div>
          {inviteBanner && (
            <div className={`rounded border px-3 py-2 text-xs ${inviteBanner.kind === "success" ? "border-green-200 bg-green-50 text-green-700" : "border-red-200 bg-red-50 text-red-700"}`}>
              {inviteBanner.message}
            </div>
          )}
          <div className="flex gap-2">
            <input
              required
              type="email"
              value={inviteEmail}
              onChange={(e) => setInviteEmail(e.target.value)}
              className="flex-1 px-3 py-2 border border-slate-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-300 bg-white"
              placeholder="colleague@example.com"
            />
            <button
              type="submit"
              disabled={sendingInvite || !inviteEmail}
              className="flex items-center gap-1.5 bg-blue-600 hover:bg-blue-700 disabled:opacity-60 text-white px-3 py-2 rounded-lg text-sm font-medium transition-colors shrink-0"
            >
              {sendingInvite ? <Loader2 size={14} className="animate-spin" /> : <Mail size={14} />}
              Send invite
            </button>
            <button
              type="button"
              onClick={() => setShowInviteForm(false)}
              className="px-3 py-2 rounded-lg text-sm text-slate-600 hover:bg-slate-100 transition-colors"
            >
              Cancel
            </button>
          </div>
          <p className="text-xs text-blue-600">The invite link is valid for 7 days.</p>
        </form>
      )}

      {/* Add member form */}
      {showAddForm && isOwner && (
        <form
          onSubmit={handleAddMember}
          className="rounded-xl border border-blue-200 bg-blue-50 p-4 space-y-3"
        >
          <div className="flex items-center gap-2 text-sm font-medium text-blue-700">
            <UserCog size={14} />
            New member
          </div>
          {addBanner && <Banner {...addBanner} />}
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-xs font-medium text-slate-600 mb-1">Email *</label>
              <input
                required
                type="email"
                value={addForm.email}
                onChange={(e) => setAddForm((f) => ({ ...f, email: e.target.value }))}
                className={inputCls}
                placeholder="user@example.com"
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-slate-600 mb-1">Role *</label>
              <select
                value={addForm.org_role}
                onChange={(e) => setAddForm((f) => ({ ...f, org_role: e.target.value as Role }))}
                className={inputCls}
              >
                {ROLES.map((r) => (
                  <option key={r} value={r} className="capitalize">
                    {r}
                  </option>
                ))}
              </select>
            </div>
            <div className="col-span-2">
              <label className="block text-xs font-medium text-slate-600 mb-1">Temporary password *</label>
              <input
                required
                type="password"
                minLength={8}
                value={addForm.password}
                onChange={(e) => setAddForm((f) => ({ ...f, password: e.target.value }))}
                className={inputCls}
                placeholder="Min. 8 characters"
                autoComplete="new-password"
              />
            </div>
          </div>
          <div className="flex gap-2">
            <button
              type="submit"
              disabled={addingMember}
              className="flex items-center gap-1.5 bg-blue-600 hover:bg-blue-700 disabled:opacity-60 text-white px-3 py-2 rounded-lg text-sm font-medium transition-colors"
            >
              {addingMember ? <Loader2 size={14} className="animate-spin" /> : <Plus size={14} />}
              Create user
            </button>
            <button
              type="button"
              onClick={() => setShowAddForm(false)}
              className="px-3 py-2 rounded-lg text-sm text-slate-600 hover:bg-slate-100 transition-colors"
            >
              Cancel
            </button>
          </div>
        </form>
      )}

      {/* Members table */}
      <div className="rounded-xl border border-slate-200 bg-white overflow-hidden">
        {members.length === 0 ? (
          <div className="p-6 text-center text-slate-400 text-sm">No members yet.</div>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-slate-100 text-xs text-slate-500 uppercase tracking-wider">
                <th className="px-4 py-3 text-left font-medium">User</th>
                <th className="px-4 py-3 text-left font-medium">Email</th>
                <th className="px-4 py-3 text-left font-medium">Role</th>
                {isOwner && <th className="px-4 py-3 text-right font-medium">Actions</th>}
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {members.map((member) => (
                <tr key={member.id} className="hover:bg-slate-50 transition-colors">
                  <td className="px-4 py-3">
                    <div className="flex items-center gap-2">
                      <span className="font-medium text-slate-800">{member.email}</span>
                      {member.id === me.id && (
                        <span className="text-xs text-slate-400">(you)</span>
                      )}
                      {!member.is_active && (
                        <span className="text-xs text-red-400">inactive</span>
                      )}
                    </div>
                  </td>
                  <td className="px-4 py-3 text-slate-500">{member.email ?? "—"}</td>
                  <td className="px-4 py-3">
                    {isOwner && editingRoleFor === member.id ? (
                      <div className="flex items-center gap-1">
                        <select
                          value={pendingRole}
                          onChange={(e) => setPendingRole(e.target.value as Role)}
                          className="text-xs border border-slate-200 rounded px-2 py-1 focus:outline-none focus:ring-1 focus:ring-blue-300"
                        >
                          {ROLES.map((r) => (
                            <option key={r} value={r}>{r}</option>
                          ))}
                        </select>
                        <button
                          onClick={() => handleSaveRole(member)}
                          disabled={savingRole}
                          className="p-1 text-blue-600 hover:text-blue-800 disabled:opacity-50"
                        >
                          {savingRole ? <Loader2 size={12} className="animate-spin" /> : <Check size={12} />}
                        </button>
                        <button
                          onClick={() => setEditingRoleFor(null)}
                          className="p-1 text-slate-400 hover:text-slate-700"
                        >
                          <X size={12} />
                        </button>
                      </div>
                    ) : (
                      <div className="flex items-center gap-2">
                        <RoleBadge role={member.org_role} />
                        {isOwner && (
                          <button
                            onClick={() => {
                              setEditingRoleFor(member.id);
                              setPendingRole(member.org_role as Role);
                            }}
                            className="p-0.5 text-slate-300 hover:text-slate-600 transition-colors"
                          >
                            <ShieldCheck size={12} />
                          </button>
                        )}
                      </div>
                    )}
                  </td>
                  {isOwner && (
                    <td className="px-4 py-3 text-right">
                      {member.id !== me.id && (
                        <button
                          onClick={() => handleRemove(member)}
                          disabled={removingId === member.id}
                          className="p-1.5 text-slate-300 hover:text-red-500 disabled:opacity-50 transition-colors rounded"
                        >
                          {removingId === member.id ? (
                            <Loader2 size={14} className="animate-spin" />
                          ) : (
                            <Trash2 size={14} />
                          )}
                        </button>
                      )}
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {/* Scoring & AI config */}
      <div className="flex items-center gap-2 pt-2">
        <Sparkles size={15} className="text-blue-500" />
        <SectionTitle title="Scoring & AI config" />
      </div>
      <p className="text-xs text-slate-500 -mt-3">
        Override the global scoring defaults for your org. Changes here only affect your org&apos;s lead scores and AI classification.
      </p>
      {orgId && <OrgScoringSection orgId={orgId} isAdmin={isAdmin} />}

      {/* Danger Zone */}
      {isOwner && (
        <div className="mt-6">
          <h2 className="text-sm font-semibold text-red-500 uppercase tracking-wider pt-4 pb-2 border-b border-red-100">
            Danger Zone
          </h2>
          <div className="mt-3 rounded-xl border border-red-200 bg-red-50 p-4 flex items-center justify-between">
            <div>
              <p className="text-sm font-medium text-red-800">Delete organization</p>
              <p className="text-xs text-red-600 mt-0.5">
                Permanently deletes the org and removes all members. This cannot be undone.
              </p>
            </div>
            <button
              onClick={handleDeleteOrg}
              disabled={deletingOrg}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-red-600 hover:bg-red-700 text-white text-xs font-medium disabled:opacity-50 transition-colors"
            >
              {deletingOrg ? <Loader2 size={13} className="animate-spin" /> : <Trash2 size={13} />}
              Delete org
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
