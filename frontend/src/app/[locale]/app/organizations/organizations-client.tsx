"use client";
import { useState } from "react";
import useSWR from "swr";
import { useRouter } from "next/navigation";
import {
  Building2, Plus, Loader2, Check, ArrowRight, Users, Crown, Shield, Eye, User, X, Bell,
} from "lucide-react";
import {
  fetchCurrentUser,
  fetchMyOrgs,
  createOrg,
  switchOrg,
  leaveOrg,
  fetchMyNotificationPreferences,
  updateMyNotificationPreferences,
  type OrgInfo,
  type NotificationPreferences,
} from "@/lib/api";
import { OrgClient } from "@/app/[locale]/app/org/org-client";
import { useI18n } from "@/i18n/context";

const inputCls =
  "w-full px-3 py-2 border border-slate-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-300 focus:border-transparent bg-white";

const ROLE_META: Record<string, { icon: React.ElementType; cls: string }> = {
  owner:  { icon: Crown,  cls: "text-purple-600 bg-purple-50 border-purple-200" },
  admin:  { icon: Shield, cls: "text-blue-600 bg-blue-50 border-blue-200" },
  member: { icon: User,   cls: "text-slate-600 bg-slate-50 border-slate-200" },
  viewer: { icon: Eye,    cls: "text-slate-400 bg-slate-50 border-slate-100" },
};

const TIER_COLORS: Record<string, string> = {
  free:        "bg-slate-100 text-slate-600",
  simple:      "bg-slate-100 text-slate-700",
  explorer:    "bg-blue-100 text-blue-700",
  researcher:  "bg-violet-100 text-violet-700",
  strategist:  "bg-slate-800 text-white",
};

function Banner({ kind, message, onClose }: { kind: "success" | "error"; message: string; onClose: () => void }) {
  return (
    <div className={`rounded-lg border px-4 py-3 text-sm flex items-center justify-between gap-3 ${
      kind === "success" ? "border-green-200 bg-green-50 text-green-800" : "border-red-200 bg-red-50 text-red-800"
    }`}>
      <span>{message}</span>
      <button onClick={onClose} className="shrink-0 opacity-60 hover:opacity-100 transition-opacity">
        <X size={14} />
      </button>
    </div>
  );
}

function OrgCard({
  org,
  isActive,
  onSwitch,
  onLeave,
  switching,
  leaving,
}: {
  org: OrgInfo;
  isActive: boolean;
  onSwitch: () => void;
  onLeave: () => void;
  switching: boolean;
  leaving: boolean;
}) {
  const { dict } = useI18n();
  const t = dict.app.organizations;
  const role = org.role ?? "member";
  const meta = ROLE_META[role] ?? ROLE_META.member;
  const RoleIcon = meta.icon;
  const roleLabel = t.roles[role as keyof typeof t.roles] ?? t.roles.member;

  return (
    <div className={`relative rounded-xl border p-5 transition-all duration-200 ${
      isActive
        ? "border-blue-300 bg-blue-50/60 shadow-sm shadow-blue-100"
        : "border-slate-200 bg-white hover:border-slate-300 hover:shadow-sm"
    }`}>
      {isActive && (
        <div className="absolute top-3 right-3 flex items-center gap-1 text-xs font-medium text-blue-600 bg-blue-100 px-2 py-0.5 rounded-full">
          <Check size={10} />
          {t.active}
        </div>
      )}

      <div className="flex items-start gap-3 pr-16">
        <div className={`w-9 h-9 rounded-lg flex items-center justify-center shrink-0 ${
          isActive ? "bg-blue-600 text-white" : "bg-slate-100 text-slate-500"
        }`}>
          <Building2 size={18} />
        </div>
        <div className="min-w-0 flex-1">
          <p className="font-semibold text-slate-900 truncate">{org.name}</p>
          <p className="text-xs text-slate-400 font-mono mt-0.5">{org.slug}</p>
        </div>
      </div>

      <div className="mt-4 flex items-center gap-2 flex-wrap">
        <span className={`inline-flex items-center gap-1 border text-xs font-medium px-2 py-0.5 rounded-full ${meta.cls}`}>
          <RoleIcon size={10} />
          {roleLabel}
        </span>
        <span className={`inline-flex items-center text-xs font-medium px-2 py-0.5 rounded-full capitalize ${TIER_COLORS[org.tier] ?? "bg-slate-100 text-slate-600"}`}>
          {org.tier}
        </span>
      </div>

      <div className="mt-4 pt-4 border-t border-slate-100 flex items-center gap-2">
        {!isActive && (
          <button
            onClick={onSwitch}
            disabled={switching}
            className="flex items-center gap-1.5 text-sm font-medium text-blue-600 hover:text-blue-800 disabled:opacity-50 transition-colors"
          >
            {switching ? <Loader2 size={13} className="animate-spin" /> : <ArrowRight size={13} />}
            {t.switchTo}
          </button>
        )}
        {isActive && (
          <span className="text-xs text-slate-400">{t.currentlyActive}</span>
        )}
        <div className="ml-auto">
          {role !== "owner" && (
            <button
              onClick={onLeave}
              disabled={leaving}
              className="text-xs text-red-400 hover:text-red-600 font-medium disabled:opacity-50 transition-colors"
            >
              {leaving ? t.leaving : t.leave}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

const NOTIF_ITEMS: { key: keyof NotificationPreferences; tkey: "low_credit" | "export_ready" | "job_failed" | "saved_view" }[] = [
  { key: "notif_low_credit",   tkey: "low_credit" },
  { key: "notif_export_ready", tkey: "export_ready" },
  { key: "notif_job_failed",   tkey: "job_failed" },
  { key: "notif_saved_view",   tkey: "saved_view" },
];

function Toggle({ checked, onChange, disabled }: { checked: boolean; onChange: (v: boolean) => void; disabled?: boolean }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className={`relative inline-flex h-5 w-9 shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors duration-200 focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-400 disabled:opacity-40 disabled:cursor-not-allowed ${
        checked ? "bg-blue-600" : "bg-slate-200"
      }`}
    >
      <span
        className={`pointer-events-none inline-block h-4 w-4 transform rounded-full bg-white shadow ring-0 transition-transform duration-200 ${
          checked ? "translate-x-4" : "translate-x-0"
        }`}
      />
    </button>
  );
}

function NotificationSettings({ orgId }: { orgId: number }) {
  const { dict } = useI18n();
  const t = dict.app.organizations;
  const { data: prefs, mutate } = useSWR(
    `my-notifications-${orgId}`,
    () => fetchMyNotificationPreferences(orgId),
  );
  const [saving, setSaving] = useState(false);
  const [savingKey, setSavingKey] = useState<string | null>(null);

  async function handleToggle(key: keyof NotificationPreferences, value: boolean) {
    if (!prefs) return;
    const next = { ...prefs, [key]: value };
    // If master switch turned off, disable all others too
    if (key === "email_notifications" && !value) {
      Object.keys(next).forEach((k) => { (next as Record<string, boolean>)[k] = false; });
    }
    setSavingKey(key);
    setSaving(true);
    try {
      const updated = await updateMyNotificationPreferences(orgId, next);
      await mutate(updated, false);
    } finally {
      setSaving(false);
      setSavingKey(null);
    }
  }

  if (!prefs) {
    return (
      <div className="flex items-center gap-2 text-xs text-slate-400 py-2">
        <Loader2 size={12} className="animate-spin" />
        {t.loadingPrefs}
      </div>
    );
  }

  const masterOn = prefs.email_notifications;

  return (
    <div className="space-y-1">
      {/* Master toggle */}
      <div className="flex items-center justify-between py-2.5 border-b border-slate-100">
        <div>
          <p className="text-sm font-medium text-slate-800">{t.emailNotifications}</p>
          <p className="text-xs text-slate-400 mt-0.5">{t.emailMaster}</p>
        </div>
        <Toggle
          checked={masterOn}
          onChange={(v) => handleToggle("email_notifications", v)}
          disabled={saving}
        />
      </div>

      {/* Individual toggles */}
      <div className={`space-y-0 transition-opacity duration-200 ${masterOn ? "opacity-100" : "opacity-40 pointer-events-none"}`}>
        {NOTIF_ITEMS.map(({ key, tkey }) => (
          <div key={key} className="flex items-center justify-between py-2.5">
            <div>
              <p className="text-sm text-slate-700">{t.notif[tkey].label}</p>
              <p className="text-xs text-slate-400">{t.notif[tkey].desc}</p>
            </div>
            <Toggle
              checked={prefs[key] as boolean}
              onChange={(v) => handleToggle(key, v)}
              disabled={saving || savingKey === key}
            />
          </div>
        ))}
      </div>
    </div>
  );
}

export function OrganizationsClient() {
  const router = useRouter();
  const { dict } = useI18n();
  const t = dict.app.organizations;
  const { data: me, mutate: reloadMe } = useSWR("me", fetchCurrentUser);
  const { data: orgs = [], mutate: reloadOrgs } = useSWR(me ? "my-orgs" : null, fetchMyOrgs);

  const [showCreate, setShowCreate] = useState(false);
  const [orgName, setOrgName] = useState("");
  const [creating, setCreating] = useState(false);
  const [switchingId, setSwitchingId] = useState<number | null>(null);
  const [leavingId, setLeavingId] = useState<number | null>(null);
  const [banner, setBanner] = useState<{ kind: "success" | "error"; message: string } | null>(null);

  function flash(kind: "success" | "error", message: string) {
    setBanner({ kind, message });
    setTimeout(() => setBanner(null), 5000);
  }

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault();
    if (!orgName.trim()) return;
    setCreating(true);
    try {
      await createOrg(orgName.trim());
      await Promise.all([reloadMe(), reloadOrgs()]);
      setOrgName("");
      setShowCreate(false);
      flash("success", t.created);
      router.refresh();
    } catch (err) {
      flash("error", err instanceof Error ? err.message : t.createFailed);
    } finally {
      setCreating(false);
    }
  }

  async function handleSwitch(orgId: number) {
    if (!me || orgId === me.org_id) return;
    setSwitchingId(orgId);
    try {
      await switchOrg(orgId);
      await Promise.all([reloadMe(), reloadOrgs()]);
      flash("success", t.switched);
      router.refresh();
    } catch (err) {
      flash("error", err instanceof Error ? err.message : t.switchFailed);
    } finally {
      setSwitchingId(null);
    }
  }

  async function handleLeave(org: OrgInfo) {
    if (!confirm(t.leaveConfirm.replace("{name}", org.name))) return;
    setLeavingId(org.id);
    try {
      await leaveOrg(org.id);
      await Promise.all([reloadMe(), reloadOrgs()]);
      flash("success", t.leftOrg.replace("{name}", org.name));
      router.refresh();
    } catch (err) {
      flash("error", err instanceof Error ? err.message : t.leaveFailed);
    } finally {
      setLeavingId(null);
    }
  }

  if (!me) return <div className="p-6 text-slate-400 text-sm">{t.loading}</div>;

  const activeOrgId = me.org_id;

  return (
    <div className="p-6 max-w-3xl mx-auto space-y-8">
      {/* Header */}
      <div>
        <h1 className="text-xl font-semibold text-slate-900">{t.title}</h1>
        <p className="text-sm text-slate-500 mt-0.5">
          {t.subtitle}
        </p>
      </div>

      {banner && <Banner {...banner} onClose={() => setBanner(null)} />}

      {/* Org grid */}
      <div className="space-y-4">
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-semibold text-slate-500 uppercase tracking-wider">
            {t.yourOrgs} <span className="normal-case font-normal text-slate-400 ml-1">({orgs.length})</span>
          </h2>
          <button
            onClick={() => setShowCreate((v) => !v)}
            className="flex items-center gap-1.5 text-sm font-medium text-blue-600 hover:text-blue-800 transition-colors"
          >
            <Plus size={14} />
            {t.newOrg}
          </button>
        </div>

        {/* Create form */}
        {showCreate && (
          <form
            onSubmit={handleCreate}
            className="rounded-xl border border-blue-200 bg-blue-50 p-5 space-y-4"
          >
            <div className="flex items-center gap-2 text-sm font-semibold text-blue-700">
              <Building2 size={15} />
              {t.createTitle}
            </div>
            <div>
              <label className="block text-xs font-medium text-slate-600 mb-1">{t.orgName}</label>
              <input
                autoFocus
                required
                value={orgName}
                onChange={(e) => setOrgName(e.target.value)}
                className={inputCls}
                placeholder={t.orgNamePlaceholder}
              />
            </div>
            <div className="flex gap-2">
              <button
                type="submit"
                disabled={creating || !orgName.trim()}
                className="flex items-center gap-1.5 bg-blue-600 hover:bg-blue-700 disabled:opacity-60 text-white px-4 py-2 rounded-lg text-sm font-medium transition-colors"
              >
                {creating ? <Loader2 size={14} className="animate-spin" /> : <Building2 size={14} />}
                {t.create}
              </button>
              <button
                type="button"
                onClick={() => { setShowCreate(false); setOrgName(""); }}
                className="px-4 py-2 text-sm text-slate-500 hover:text-slate-700 rounded-lg hover:bg-white/60 transition-colors"
              >
                {t.cancel}
              </button>
            </div>
          </form>
        )}

        {/* Org cards */}
        {orgs.length === 0 ? (
          <div className="rounded-xl border border-dashed border-slate-200 bg-slate-50 p-8 text-center">
            <Building2 size={28} className="text-slate-300 mx-auto mb-3" />
            <p className="text-sm text-slate-500">{t.emptyTitle}</p>
            <p className="text-xs text-slate-400 mt-1">{t.emptyHint}</p>
          </div>
        ) : (
          <div className="grid gap-3 sm:grid-cols-2">
            {orgs.map((org) => (
              <OrgCard
                key={org.id}
                org={org}
                isActive={org.id === activeOrgId}
                onSwitch={() => handleSwitch(org.id)}
                onLeave={() => handleLeave(org)}
                switching={switchingId === org.id}
                leaving={leavingId === org.id}
              />
            ))}
          </div>
        )}
      </div>

      {/* Team management for active org */}
      {activeOrgId && (
        <div className="space-y-4">
          <div className="border-t border-slate-100 pt-6">
            <div className="flex items-center gap-2 mb-1">
              <Users size={16} className="text-slate-500" />
              <h2 className="text-sm font-semibold text-slate-500 uppercase tracking-wider">{t.team}</h2>
            </div>
            <p className="text-xs text-slate-400">
              {t.teamHint}
            </p>
          </div>
          <div className="rounded-xl border border-slate-200 bg-white p-4">
            <OrgClient embedded />
          </div>
        </div>
      )}

      {/* Per-user notification preferences for active org */}
      {activeOrgId && (
        <div className="space-y-4">
          <div className="border-t border-slate-100 pt-6">
            <div className="flex items-center gap-2 mb-1">
              <Bell size={16} className="text-slate-500" />
              <h2 className="text-sm font-semibold text-slate-500 uppercase tracking-wider">{t.notifications}</h2>
            </div>
            <p className="text-xs text-slate-400">
              {t.notifHint}
            </p>
          </div>
          <div className="rounded-xl border border-slate-200 bg-white px-5 py-3">
            <NotificationSettings orgId={activeOrgId} />
          </div>
        </div>
      )}
    </div>
  );
}
