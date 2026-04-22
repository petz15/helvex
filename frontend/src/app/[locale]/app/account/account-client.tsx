"use client";
import { useState } from "react";
import useSWR from "swr";
import { useI18n } from "@/i18n/context";
import { useRouter } from "next/navigation";
import Link from "next/link";
import {
  Mail, Building2, Loader2, Check, Plus, ArrowRight, Users,
} from "lucide-react";
import {
  fetchCurrentUser,
  fetchMyOrgs,
  requestEmailChange,
  createOrg,
  switchOrg,
  leaveOrg,
} from "@/lib/api";

type AccountClientProps = Record<string, never>;

const ROLE_COLORS: Record<string, string> = {
  owner: "bg-purple-100 text-purple-700",
  admin: "bg-blue-100 text-blue-700",
  member: "bg-slate-100 text-slate-700",
  viewer: "bg-slate-50 text-slate-500",
};

const inputCls =
  "w-full px-3 py-2 border border-slate-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-300 focus:border-transparent bg-white";

function SectionTitle({ title }: { title: string }) {
  return (
    <h2 className="text-sm font-semibold text-slate-500 uppercase tracking-wider pt-4 pb-2 border-b border-slate-100">
      {title}
    </h2>
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


function ChangePasswordForm() {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [saving, setSaving] = useState(false);
  const [banner, setBanner] = useState<{ kind: "success" | "error"; message: string } | null>(null);

  const { dict } = useI18n();
  const t = dict.app.account;

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setSaving(true);
    setBanner(null);
    try {
      const res = await fetch("/api/v1/auth/change-password", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ current_password: current, new_password: next }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        setBanner({ kind: "error", message: body.detail ?? "Failed to change password" });
        return;
      }
      setBanner({ kind: "success", message: "Password updated." });
      setCurrent("");
      setNext("");
    } finally {
      setSaving(false);
      setTimeout(() => setBanner(null), 4000);
    }
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-4">
      {banner && <Banner {...banner} />}
      <div className="grid grid-cols-2 gap-4">
        <div>
          <label className="block text-sm font-medium text-slate-700 mb-1">{t.currentpassword}</label>
          <input
            type="password"
            required
            autoComplete="current-password"
            value={current}
            onChange={(e) => setCurrent(e.target.value)}
            className={inputCls}
          />
        </div>
        <div>
          <label className="block text-sm font-medium text-slate-700 mb-1">{t.newpassword}
            <span className="text-slate-400 font-normal ml-1 text-xs">{t.minchars}</span>
          </label>
          <input
            type="password"
            required
            minLength={8}
            autoComplete="new-password"
            value={next}
            onChange={(e) => setNext(e.target.value)}
            className={inputCls}
          />
        </div>
      </div>
      <button
        type="submit"
        disabled={saving}
        className="flex items-center gap-2 bg-slate-100 hover:bg-slate-200 disabled:opacity-60 text-slate-700 px-4 py-2 rounded-lg text-sm font-medium transition-colors"
      >
        {saving ? <Loader2 size={14} className="animate-spin" /> : <Check size={14} />}
        {t.changepassword}
      </button>
    </form>
  );
}

export function AccountClient({}: AccountClientProps) {
  const { dict } = useI18n();
  const t = dict.app.account;

  const router = useRouter();
  const { data: me, mutate: reloadMe } = useSWR("me", fetchCurrentUser);
  const { data: orgs = [], mutate: reloadOrgs } = useSWR(me ? "my-orgs" : null, fetchMyOrgs);

  // Email change
  const [showEmailForm, setShowEmailForm] = useState(false);
  const [newEmail, setNewEmail] = useState("");
  const [emailPassword, setEmailPassword] = useState("");
  const [emailBanner, setEmailBanner] = useState<{ kind: "success" | "error"; message: string } | null>(null);
  const [savingEmail, setSavingEmail] = useState(false);

  // Org creation
  const [showCreateOrg, setShowCreateOrg] = useState(false);
  const [orgName, setOrgName] = useState("");
  const [creatingOrg, setCreatingOrg] = useState(false);
  const [orgBanner, setOrgBanner] = useState<{ kind: "success" | "error"; message: string } | null>(null);

  // Leave org / switch org
  const [leavingOrg, setLeavingOrg] = useState(false);
  const [switchingOrgId, setSwitchingOrgId] = useState<number | null>(null);
  const profileTier = me?.org?.tier ?? "free";

  function flash(
    setter: React.Dispatch<React.SetStateAction<{ kind: "success" | "error"; message: string } | null>>,
    kind: "success" | "error",
    message: string,
  ) {
    setter({ kind, message });
    setTimeout(() => setter(null), 5000);
  }

  async function handleRequestEmailChange(e: React.FormEvent) {
    e.preventDefault();
    setSavingEmail(true);
    setEmailBanner(null);
    try {
      await requestEmailChange(newEmail, emailPassword);
      setShowEmailForm(false);
      setNewEmail("");
      setEmailPassword("");
      flash(setEmailBanner, "success", `Verification sent to ${newEmail}. Click the link to confirm.`);
    } catch (err) {
      flash(setEmailBanner, "error", err instanceof Error ? err.message : "Failed to request email change");
    } finally {
      setSavingEmail(false);
    }
  }

  async function handleCreateOrg(e: React.FormEvent) {
    e.preventDefault();
    setCreatingOrg(true);
    setOrgBanner(null);
    try {
      await createOrg(orgName);
      await Promise.all([reloadMe(), reloadOrgs()]);
      setShowCreateOrg(false);
      setOrgName("");
      flash(setOrgBanner, "success", "Organization created! You are now the owner.");
      router.refresh();
    } catch (err) {
      flash(setOrgBanner, "error", err instanceof Error ? err.message : "Failed to create org");
    } finally {
      setCreatingOrg(false);
    }
  }

  async function handleSwitchOrg(orgId: number) {

    if (!me || orgId === me.org_id) return;
    setSwitchingOrgId(orgId);
    try {
      await switchOrg(orgId);
      await Promise.all([reloadMe(), reloadOrgs()]);
      flash(setOrgBanner, "success", t.orgswitched.replace("{orgName}", me.org?.name || ""));
      router.refresh();
    } catch (err) {
      flash(setOrgBanner, "error", err instanceof Error ? err.message : "Failed to switch organization");
    } finally {
      setSwitchingOrgId(null);
    }
  }

  async function handleLeaveOrg() {

    if (!me?.org_id) return;
    if (!confirm(t.leaveorgconfirm.replace("{orgName}", me.org?.name || ""))) return;
    setLeavingOrg(true);
    try {
      await leaveOrg(me.org_id);
      await Promise.all([reloadMe(), reloadOrgs()]);
      flash(setOrgBanner, "success", t.successmsgleaveorg.replace("{orgName}", me.org?.name || ""));
      router.refresh();
    } catch (err) {
      flash(setOrgBanner, "error", err instanceof Error ? err.message : t.errormsgleaveorg);
    } finally {
      setLeavingOrg(false);
    }
  }



  if (!me) return <div className="p-6 text-slate-400 text-sm">Loading…</div>;

  return (
    <div className="p-6 max-w-2xl mx-auto space-y-5">
      <div>
        <h1 className="text-xl font-semibold text-slate-900">{t.title}</h1>
        <p className="text-sm text-slate-500 mt-0.5">{t.subtitle}</p>
      </div>

      {/* Profile */}
      <SectionTitle title={t.profile} />
      {emailBanner && <Banner {...emailBanner} />}
      <div className="rounded-xl border border-slate-200 bg-white p-5 space-y-3">
        <div className="flex items-center justify-between">
          <div>
            <p className="text-xs text-slate-400 uppercase tracking-wide font-medium">Email</p>
            <p className="text-sm text-slate-800 font-medium mt-0.5">{me.email}</p>
          </div>
          {!showEmailForm && (
            <button
              onClick={() => setShowEmailForm(true)}
              className="text-xs text-blue-600 hover:text-blue-800 font-medium"
            >
              {t.changemail}
            </button>
          )}
        </div>

        {showEmailForm && (
          <form onSubmit={handleRequestEmailChange} className="border-t border-slate-100 pt-3 space-y-3">
            <p className="text-xs text-slate-500">{t.verifymailmessage}</p>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="block text-xs font-medium text-slate-600 mb-1">{t.newemail}</label>
                <input
                  type="email"
                  required
                  value={newEmail}
                  onChange={(e) => setNewEmail(e.target.value)}
                  className={inputCls}
                  placeholder="new@example.com"
                />
              </div>
              <div>
                <label className="block text-xs font-medium text-slate-600 mb-1">{t.currentpassword}</label>
                <input
                  type="password"
                  required
                  value={emailPassword}
                  onChange={(e) => setEmailPassword(e.target.value)}
                  className={inputCls}
                  autoComplete="current-password"
                />
              </div>
            </div>
            <div className="flex gap-2">
              <button
                type="submit"
                disabled={savingEmail}
                className="flex items-center gap-1.5 bg-blue-600 hover:bg-blue-700 disabled:opacity-60 text-white px-3 py-1.5 rounded-lg text-xs font-medium transition-colors"
              >
                {savingEmail ? <Loader2 size={12} className="animate-spin" /> : <Mail size={12} />}
                {t.sendverification}
              </button>
              <button
                type="button"
                onClick={() => setShowEmailForm(false)}
                className="px-3 py-1.5 text-xs text-slate-500 hover:text-slate-700 transition-colors"
              >
                {t.cancel}
              </button>
            </div>
          </form>
        )}

        <div className="border-t border-slate-100 pt-3 flex items-center gap-3 text-xs text-slate-500">
          <span className={`inline-flex items-center px-2 py-0.5 rounded font-medium capitalize ${
            profileTier === "strategist" ? "bg-purple-100 text-purple-700" : "bg-slate-100 text-slate-600"
          }`}>
            {profileTier}
          </span>
          {me.email_verified ? (
            <span className="text-green-600 flex items-center gap-1"><Check size={11} /> {t.emailverified}</span>
          ) : (
            <span className="text-amber-600">{t.emailnotverified}</span>
          )}
        </div>
      </div>

      {/* Security */}
      <SectionTitle title={t.security} />
      <ChangePasswordForm />

      {/* Organization */}
      <SectionTitle title={t.organization} />
      {orgBanner && <Banner {...orgBanner} />}

      {me.org ? (
        <div className="rounded-xl border border-slate-200 bg-white p-5 space-y-4">
          {/* Active org info */}
          <div className="flex items-center gap-2">
            <Building2 size={16} className="text-blue-600" />
            <span className="text-sm font-semibold text-slate-800">{me.org.name}</span>
            <span className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-medium capitalize ${
              ROLE_COLORS[me.org_role] ?? "bg-slate-100 text-slate-600"
            }`}>
              {me.org_role}
            </span>
          </div>
          <p className="text-xs text-slate-400 font-mono">{me.org.slug}</p>

          {/* Switch between orgs if multiple */}
          {orgs.length > 1 && (
            <div className="border-t border-slate-100 pt-3 space-y-2">
              <p className="text-xs font-medium text-slate-500">{t.switchorganization}</p>
              <div className="flex flex-wrap gap-2">
                {orgs.map((org) => (
                  <button
                    key={org.id}
                    onClick={() => handleSwitchOrg(org.id)}
                    disabled={org.id === me.org_id || switchingOrgId !== null}
                    className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium border transition-colors disabled:cursor-default ${
                      org.id === me.org_id
                        ? "bg-blue-50 border-blue-200 text-blue-700"
                        : "bg-white border-slate-200 text-slate-600 hover:border-blue-300 hover:text-blue-600"
                    }`}
                  >
                    {switchingOrgId === org.id ? (
                      <Loader2 size={11} className="animate-spin" />
                    ) : org.id === me.org_id ? (
                      <Check size={11} />
                    ) : (
                      <ArrowRight size={11} />
                    )}
                    {org.name}
                  </button>
                ))}
              </div>
            </div>
          )}

          {/* Create new org */}
          {!showCreateOrg ? (
            <div className="border-t border-slate-100 pt-3 flex items-center justify-between">
              <button
                onClick={() => setShowCreateOrg(true)}
                className="flex items-center gap-1.5 text-xs text-blue-600 hover:text-blue-800 font-medium transition-colors"
              >
                <Plus size={12} />
                {t.createneworganization}
              </button>
              <div className="flex items-center gap-3">
                <Link
                  href="organizations"
                  className="flex items-center gap-1 text-xs text-slate-500 hover:text-slate-700 font-medium transition-colors"
                >
                  <Users size={12} />
                  {t.manageteam}
                  <ArrowRight size={11} />
                </Link>
                {me.org_role !== "owner" && (
                  <button
                    onClick={handleLeaveOrg}
                    disabled={leavingOrg}
                    className="text-xs text-red-400 hover:text-red-600 font-medium disabled:opacity-50 transition-colors"
                  >
                    {leavingOrg ? "Leaving…" : "Leave"}
                  </button>
                )}
              </div>
            </div>
          ) : (
            <form onSubmit={handleCreateOrg} className="border-t border-slate-100 pt-3 space-y-3">
              <p className="text-xs font-medium text-slate-600">{t.neworgname}</p>
              <div className="flex gap-2">
                <input
                  autoFocus
                  required
                  value={orgName}
                  onChange={(e) => setOrgName(e.target.value)}
                  className={inputCls}
                  placeholder="Acme Corp"
                />
                <button
                  type="submit"
                  disabled={creatingOrg || !orgName.trim()}
                  className="flex items-center gap-1.5 bg-blue-600 hover:bg-blue-700 disabled:opacity-60 text-white px-3 py-2 rounded-lg text-xs font-medium transition-colors shrink-0"
                >
                  {creatingOrg ? <Loader2 size={12} className="animate-spin" /> : <Building2 size={12} />}
                  {t.create}
                </button>
                <button
                  type="button"
                  onClick={() => { setShowCreateOrg(false); setOrgName(""); }}
                  className="px-3 py-2 text-xs text-slate-500 hover:text-slate-700 rounded-lg hover:bg-slate-100 transition-colors"
                >
                  {t.cancel}
                </button>
              </div>
            </form>
          )}
        </div>
      ) : (
        <div className="rounded-xl border border-dashed border-slate-200 bg-slate-50 p-5 space-y-3">
          <p className="text-sm text-slate-500">You are not part of any organization.</p>
          <p className="text-xs text-slate-400">
            Create your own or ask a team owner to send you an invite link.
          </p>

          {!showCreateOrg ? (
            <button
              onClick={() => setShowCreateOrg(true)}
              className="flex items-center gap-1.5 text-sm text-blue-600 hover:text-blue-800 font-medium transition-colors"
            >
              <Plus size={14} />
              Create organization
            </button>
          ) : (
            <form onSubmit={handleCreateOrg} className="space-y-3">
              <div>
                <label className="block text-xs font-medium text-slate-600 mb-1">Organization name</label>
                <input
                  autoFocus
                  required
                  value={orgName}
                  onChange={(e) => setOrgName(e.target.value)}
                  className={inputCls}
                  placeholder="Acme Corp"
                />
              </div>
              <div className="flex gap-2">
                <button
                  type="submit"
                  disabled={creatingOrg || !orgName.trim()}
                  className="flex items-center gap-1.5 bg-blue-600 hover:bg-blue-700 disabled:opacity-60 text-white px-3 py-1.5 rounded-lg text-xs font-medium transition-colors"
                >
                  {creatingOrg ? <Loader2 size={12} className="animate-spin" /> : <Building2 size={12} />}
                  Create
                </button>
                <button
                  type="button"
                  onClick={() => { setShowCreateOrg(false); setOrgName(""); }}
                  className="px-3 py-1.5 text-xs text-slate-500 hover:text-slate-700 transition-colors"
                >
                  Cancel
                </button>
              </div>
            </form>
          )}
        </div>
      )}

    </div>
  );
}
