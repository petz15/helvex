"use client";
import Link from "next/link";
import { useState } from "react";
import useSWR from "swr";
import {
  Building2, Edit2, Check, X, Trash2, Plus, Loader2, ShieldCheck, UserCog, Mail, Sparkles, ChevronDown, ChevronUp,
} from "lucide-react";
import { useRouter, useSearchParams } from "next/navigation";
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
import { useI18n } from "@/i18n/context";

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

export function OrgScoringSection({ orgId, isAdmin }: { orgId: number; isAdmin: boolean }) {
  const { dict } = useI18n();
  const t = dict.app.org;
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
      setClassifyBanner(t.classifyQueued);
      setTimeout(() => setClassifyBanner(null), 5000);
    } catch {
      setClassifyBanner(t.classifyFailed);
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
      setBanner({ kind: "success", message: t.cfgSaved });
      setTimeout(() => setBanner(null), 4000);
    } catch (e) {
      setBanner({ kind: "error", message: e instanceof Error ? e.message : t.cfgSaveFailed });
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
            {t.anthropicKey}
            {apiKeyIsSet && !apiKeyDraftSet && (
              <span className="ml-2 text-xs font-normal text-green-600 bg-green-50 border border-green-200 rounded px-1.5 py-0.5">{t.configured}</span>
            )}
          </label>
          <p className="text-xs text-slate-500">
            {t.anthropicKeyDesc}
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
                  {t.replace}
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
            {t.lookingFor}
          </label>
          <p className="text-xs text-slate-500">
            {t.lookingForDesc}
          </p>
          <textarea
            rows={4}
            placeholder={t.lookingForPlaceholder}
            value={val("claude_target_description")}
            onChange={(e) => set("claude_target_description", e.target.value)}
            disabled={!isAdmin}
            className={textareaCls}
          />
        </div>

        {/* AI categories */}
        <div className="space-y-1.5">
          <label className="block text-sm font-medium text-slate-700">{t.customCategories}</label>
          <p className="text-xs text-slate-500">
            {t.customCategoriesDesc}
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
          {showAdvanced ? t.hideAdvanced : t.showAdvanced}
        </button>

        {showAdvanced && (
          <div className="space-y-5 pt-2 border-t border-slate-100">

            {/* Flex score — cluster targets */}
            <div className="grid grid-cols-2 gap-4">
              <div className="space-y-1.5">
                <label className="block text-sm font-medium text-slate-700">{t.targetClusters}</label>
                <p className="text-xs text-slate-500">{t.targetClustersDesc}</p>
                <textarea rows={3} value={val("scoring_target_clusters")} onChange={(e) => set("scoring_target_clusters", e.target.value)} disabled={!isAdmin} className={textareaCls} placeholder="software|tech|digital" />
              </div>
              <div className="space-y-1.5">
                <label className="block text-sm font-medium text-slate-700">{t.excludeClusters}</label>
                <p className="text-xs text-slate-500">{t.excludeClustersDesc}</p>
                <textarea rows={3} value={val("scoring_exclude_clusters")} onChange={(e) => set("scoring_exclude_clusters", e.target.value)} disabled={!isAdmin} className={textareaCls} placeholder="immobilien|gastronomie" />
              </div>
            </div>

            {/* Flex score — keyword targets */}
            <div className="grid grid-cols-2 gap-4">
              <div className="space-y-1.5">
                <label className="block text-sm font-medium text-slate-700">{t.targetKeywords}</label>
                <p className="text-xs text-slate-500">{t.targetKeywordsDesc}</p>
                <textarea rows={3} value={val("scoring_target_keywords")} onChange={(e) => set("scoring_target_keywords", e.target.value)} disabled={!isAdmin} className={textareaCls} placeholder="software|beratung|entwicklung" />
              </div>
              <div className="space-y-1.5">
                <label className="block text-sm font-medium text-slate-700">{t.excludeKeywords}</label>
                <p className="text-xs text-slate-500">{t.excludeKeywordsDesc}</p>
                <textarea rows={3} value={val("scoring_exclude_keywords")} onChange={(e) => set("scoring_exclude_keywords", e.target.value)} disabled={!isAdmin} className={textareaCls} placeholder="treuhand|buchhaltung" />
              </div>
            </div>

            {/* Distance origin */}
            <div className="space-y-1.5">
              <label className="block text-sm font-medium text-slate-700">{t.distanceOrigin}</label>
              <p className="text-xs text-slate-500">{t.distanceOriginDesc}</p>
              <div className="flex gap-3">
                <input type="number" step="0.0001" value={val("scoring_origin_lat")} onChange={(e) => set("scoring_origin_lat", e.target.value)} disabled={!isAdmin} className={inputCls + " max-w-[180px]"} placeholder="46.9266" />
                <input type="number" step="0.0001" value={val("scoring_origin_lon")} onChange={(e) => set("scoring_origin_lon", e.target.value)} disabled={!isAdmin} className={inputCls + " max-w-[180px]"} placeholder="7.4817" />
              </div>
            </div>

            {/* Legal form scores */}
            <div className="space-y-1.5">
              <label className="block text-sm font-medium text-slate-700">{t.legalFormScores}</label>
              <p className="text-xs text-slate-500">{t.legalFormScoresDesc}</p>
              <input type="text" value={val("scoring_legal_form_scores")} onChange={(e) => set("scoring_legal_form_scores", e.target.value)} disabled={!isAdmin} className={inputCls} placeholder="ag:20,gmbh:15,eg:10,einzelfirma:8" />
            </div>

            {/* Combined score weights */}
            <div className="space-y-1.5">
              <label className="block text-sm font-medium text-slate-700">{t.combinedWeights}</label>
              <p className="text-xs text-slate-500">{t.combinedWeightsDesc}</p>
              <div className="flex gap-3">
                {(["scoring_weight_ai", "scoring_weight_web", "scoring_weight_flex"] as const).map((key) => (
                  <div key={key} className="flex-1">
                    <label className="block text-xs text-slate-500 mb-1">
                      {key === "scoring_weight_ai" ? t.weightAi : key === "scoring_weight_web" ? t.weightWeb : t.weightFlex}
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
              <label className="block text-sm font-medium text-slate-700">{t.promptOverride}</label>
              <p className="text-xs text-slate-500">
                {t.promptOverrideDesc}
              </p>
              <textarea
                rows={6}
                value={val("claude_classify_prompt")}
                onChange={(e) => set("claude_classify_prompt", e.target.value)}
                disabled={!isAdmin}
                className={textareaCls}
                placeholder={t.promptOverridePlaceholder}
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
              {t.saveConfig}
            </button>
            <button
              onClick={handleClassify}
              disabled={classifying}
              className="flex items-center gap-1.5 bg-purple-600 hover:bg-purple-700 disabled:opacity-50 text-white px-4 py-2 rounded-lg text-sm font-medium transition-colors"
            >
              {classifying ? <Loader2 size={14} className="animate-spin" /> : <Sparkles size={14} />}
              {t.runClassification}
            </button>
            {dirty && (
              <button
                onClick={() => { setForm({}); setDirty(false); }}
                className="text-sm text-slate-500 hover:text-slate-700 px-3 py-2 rounded-lg hover:bg-slate-100 transition-colors"
              >
                {t.discard}
              </button>
            )}
            {!dirty && <span className="text-xs text-slate-400">{t.noUnsavedChanges}</span>}
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
  const { dict } = useI18n();
  const t = dict.app.org;
  const cls = ROLE_COLORS[role as Role] ?? "bg-slate-100 text-slate-600";
  const label = t.roles[role as keyof typeof t.roles] ?? role;
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-medium ${cls}`}>
      {label}
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
  const { dict } = useI18n();
  const t = dict.app.org;
  const { data: me, mutate: reloadMe } = useSWR("me", fetchCurrentUser);
  const router = useRouter();
  const searchParams = useSearchParams();
  const requestedOrgId = Number(searchParams?.get("org_id"));
  const hasRequestedOrgId = Number.isInteger(requestedOrgId) && requestedOrgId > 0;
  const orgId = me?.is_superadmin && hasRequestedOrgId ? requestedOrgId : me?.org?.id;
  const isSuperadminOrgOverride = !!me?.is_superadmin && hasRequestedOrgId;

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
      t.deleteConfirm.replace("{name}", org.name).replace("{count}", String(org.member_count))
    );
    if (!confirmed) return;
    setDeletingOrg(true);
    try {
      await deleteOrg(orgId);
      await reloadMe();
      router.push("/app/search");
    } catch (e) {
      flash(setNameBanner, "error", e instanceof Error ? e.message : t.deleteFailed);
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
      flash(setNameBanner, "success", t.nameUpdated);
    } catch (e) {
      flash(setNameBanner, "error", e instanceof Error ? e.message : t.nameUpdateFailed);
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
      flash(setMemberBanner, "success", t.memberAdded.replace("{email}", addForm.email));
    } catch (e) {
      flash(setAddBanner, "error", e instanceof Error ? e.message : t.memberAddFailed);
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
      flash(setMemberBanner, "success", t.inviteSent.replace("{email}", inviteEmail));
    } catch (err) {
      flash(setInviteBanner, "error", err instanceof Error ? err.message : t.inviteFailed);
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
      flash(setMemberBanner, "success", t.roleUpdated.replace("{email}", member.email));
    } catch (e) {
      flash(setMemberBanner, "error", e instanceof Error ? e.message : t.roleUpdateFailed);
    } finally {
      setSavingRole(false);
    }
  }

  async function handleRemove(member: OrgMember) {
    if (!orgId) return;
    if (!confirm(t.removeConfirm.replace("{email}", member.email))) return;
    setRemovingId(member.id);
    try {
      await removeOrgMember(orgId, member.id);
      await reloadMembers();
      await reloadOrg();
      flash(setMemberBanner, "success", t.memberRemoved.replace("{email}", member.email));
    } catch (e) {
      flash(setMemberBanner, "error", e instanceof Error ? e.message : t.memberRemoveFailed);
    } finally {
      setRemovingId(null);
    }
  }

  if (!me || !org) {
    return <div className={embedded ? "text-slate-400 text-sm" : "p-6 text-slate-400 text-sm"}>{t.loading}</div>;
  }

  if (!me.org_id) {
    return (
      <div className={embedded ? "" : "p-6 max-w-xl mx-auto"}>
        <div className="rounded-lg border border-slate-200 bg-slate-50 p-6 text-center text-slate-500 text-sm">
          {t.notMember}
        </div>
      </div>
    );
  }

  if (!isAdmin) {
    return (
      <div className={embedded ? "text-slate-500 text-sm" : "p-6 max-w-xl mx-auto text-slate-500 text-sm"}>
        {t.needAdmin}
      </div>
    );
  }

  if (me && !me.org_id) {
    return (
      <div className={embedded ? "flex flex-col items-center justify-center py-16 text-center" : "p-6 max-w-3xl mx-auto flex flex-col items-center justify-center py-24 text-center"}>
        <span className="text-5xl mb-4 select-none">😢</span>
        <h2 className="text-lg font-semibold text-slate-800 mb-2">{t.noTeamTitle}</h2>
        <p className="text-sm text-slate-500">{t.noTeamDesc}</p>
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
            <h1 className="text-xl font-semibold text-slate-900">{t.title}</h1>
            <p className="text-sm text-slate-500 mt-0.5">{t.subtitle}</p>
            {isSuperadminOrgOverride && (
              <span className="mt-2 inline-flex items-center rounded-full border border-amber-200 bg-amber-50 px-2.5 py-1 text-xs font-medium text-amber-800">
                {t.viewingAs.replace("{id}", String(org.id)).replace("{name}", org.name)}
              </span>
            )}
          </div>
        </div>
      )}

      {/* Org info */}
      <SectionTitle title={t.orgInfo} />
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
                  placeholder={t.orgNamePlaceholder}
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
            <p className="text-xs text-slate-400 mt-1">{(org.member_count === 1 ? t.memberCount : t.memberCountPlural).replace("{count}", String(org.member_count))}</p>
          </div>
        </div>
      </div>

      {/* Members */}
      <div className="flex items-center justify-between">
        <SectionTitle title={t.members} />
        {isAdmin && (
          <div className="flex items-center gap-3 mt-1">
            <button
              onClick={() => { setShowInviteForm((v) => !v); setShowAddForm(false); }}
              className="flex items-center gap-1.5 text-sm text-blue-600 hover:text-blue-800 font-medium transition-colors"
            >
              <Mail size={14} />
              {t.inviteByEmail}
            </button>
            {isOwner && (
              <button
                onClick={() => { setShowAddForm((v) => !v); setShowInviteForm(false); }}
                className="flex items-center gap-1.5 text-sm text-slate-500 hover:text-slate-700 font-medium transition-colors"
              >
                <Plus size={14} />
                {t.createAccount}
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
            {t.inviteByEmail}
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
              {t.sendInvite}
            </button>
            <button
              type="button"
              onClick={() => setShowInviteForm(false)}
              className="px-3 py-2 rounded-lg text-sm text-slate-600 hover:bg-slate-100 transition-colors"
            >
              {t.cancel}
            </button>
          </div>
          <p className="text-xs text-blue-600">{t.inviteValidity}</p>
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
            {t.newMember}
          </div>
          {addBanner && <Banner {...addBanner} />}
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-xs font-medium text-slate-600 mb-1">{t.emailLabel}</label>
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
              <label className="block text-xs font-medium text-slate-600 mb-1">{t.roleLabel}</label>
              <select
                value={addForm.org_role}
                onChange={(e) => setAddForm((f) => ({ ...f, org_role: e.target.value as Role }))}
                className={inputCls}
              >
                {ROLES.map((r) => (
                  <option key={r} value={r}>
                    {t.roles[r]}
                  </option>
                ))}
              </select>
            </div>
            <div className="col-span-2">
              <label className="block text-xs font-medium text-slate-600 mb-1">{t.tempPassword}</label>
              <input
                required
                type="password"
                minLength={8}
                value={addForm.password}
                onChange={(e) => setAddForm((f) => ({ ...f, password: e.target.value }))}
                className={inputCls}
                placeholder={t.tempPasswordPlaceholder}
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
              {t.createUser}
            </button>
            <button
              type="button"
              onClick={() => setShowAddForm(false)}
              className="px-3 py-2 rounded-lg text-sm text-slate-600 hover:bg-slate-100 transition-colors"
            >
              {t.cancel}
            </button>
          </div>
        </form>
      )}

      {/* Members table */}
      <div className="rounded-xl border border-slate-200 bg-white overflow-hidden">
        {members.length === 0 ? (
          <div className="p-6 text-center text-slate-400 text-sm">{t.noMembers}</div>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-slate-100 text-xs text-slate-500 uppercase tracking-wider">
                <th className="px-4 py-3 text-left font-medium">{t.colUser}</th>
                <th className="px-4 py-3 text-left font-medium">{t.colEmail}</th>
                <th className="px-4 py-3 text-left font-medium">{t.colRole}</th>
                {isOwner && <th className="px-4 py-3 text-right font-medium">{t.colActions}</th>}
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {members.map((member) => (
                <tr key={member.id} className="hover:bg-slate-50 transition-colors">
                  <td className="px-4 py-3">
                    <div className="flex items-center gap-2">
                      <span className="font-medium text-slate-800">{member.email}</span>
                      {member.id === me.id && (
                        <span className="text-xs text-slate-400">{t.you}</span>
                      )}
                      {!member.is_active && (
                        <span className="text-xs text-red-400">{t.inactive}</span>
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
                            <option key={r} value={r}>{t.roles[r]}</option>
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

      {/* Scoring & AI config — moved to Settings page */}
      <div className="flex items-center gap-2 pt-2">
        <Sparkles size={15} className="text-blue-500" />
        <SectionTitle title={t.scoringAiConfig} />
      </div>
      <div className="rounded-xl border border-blue-100 bg-blue-50 p-4 flex items-center justify-between">
        <p className="text-sm text-blue-700">
          {t.settingsMoved}
        </p>
        <Link
          href="/app/settings?tab=llm"
          className="ml-4 shrink-0 flex items-center gap-1.5 bg-blue-600 hover:bg-blue-700 text-white px-3 py-1.5 rounded-lg text-sm font-medium transition-colors"
        >
          <Sparkles size={13} /> {t.openSettings}
        </Link>
      </div>

      {/* Danger Zone */}
      {isOwner && (
        <div className="mt-6">
          <h2 className="text-sm font-semibold text-red-500 uppercase tracking-wider pt-4 pb-2 border-b border-red-100">
            {t.dangerZone}
          </h2>
          <div className="mt-3 rounded-xl border border-red-200 bg-red-50 p-4 flex items-center justify-between">
            <div>
              <p className="text-sm font-medium text-red-800">{t.deleteOrgTitle}</p>
              <p className="text-xs text-red-600 mt-0.5">
                {t.deleteOrgDesc}
              </p>
            </div>
            <button
              onClick={handleDeleteOrg}
              disabled={deletingOrg}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-red-600 hover:bg-red-700 text-white text-xs font-medium disabled:opacity-50 transition-colors"
            >
              {deletingOrg ? <Loader2 size={13} className="animate-spin" /> : <Trash2 size={13} />}
              {t.deleteOrgBtn}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
