"use client";
import { useState } from "react";
import useSWR from "swr";
import { useRouter } from "next/navigation";
import {
  KeyRound, Mail, Building2, Loader2, Check, ExternalLink, Plus,
} from "lucide-react";
import {
  fetchCurrentUser,
  requestEmailChange,
  createOrg,
  leaveOrg,
} from "@/lib/api";

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
          <label className="block text-sm font-medium text-slate-700 mb-1">Current password</label>
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
          <label className="block text-sm font-medium text-slate-700 mb-1">New password
            <span className="text-slate-400 font-normal ml-1 text-xs">Min. 8 characters</span>
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
        {saving ? <Loader2 size={14} className="animate-spin" /> : <KeyRound size={14} />}
        Change password
      </button>
    </form>
  );
}

export function AccountClient() {
  const { data: me, mutate: reloadMe } = useSWR("me", fetchCurrentUser);
  const router = useRouter();

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

  // Leave org
  const [leavingOrg, setLeavingOrg] = useState(false);

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
      await reloadMe();
      setShowCreateOrg(false);
      setOrgName("");
      flash(setOrgBanner, "success", "Organization created! You are now the owner.");
    } catch (err) {
      flash(setOrgBanner, "error", err instanceof Error ? err.message : "Failed to create org");
    } finally {
      setCreatingOrg(false);
    }
  }

  async function handleLeaveOrg() {
    if (!me?.org_id) return;
    if (!confirm(`Leave "${me.org?.name}"? You will lose access to its data.`)) return;
    setLeavingOrg(true);
    try {
      await leaveOrg(me.org_id);
      await reloadMe();
      flash(setOrgBanner, "success", "You have left the organization.");
    } catch (err) {
      flash(setOrgBanner, "error", err instanceof Error ? err.message : "Failed to leave org");
    } finally {
      setLeavingOrg(false);
    }
  }

  if (!me) return <div className="p-6 text-slate-400 text-sm">Loading…</div>;

  const ROLE_COLORS: Record<string, string> = {
    owner: "bg-purple-100 text-purple-700",
    admin: "bg-blue-100 text-blue-700",
    member: "bg-slate-100 text-slate-700",
    viewer: "bg-slate-50 text-slate-500",
  };

  return (
    <div className="p-6 max-w-2xl mx-auto space-y-5">
      <div>
        <h1 className="text-xl font-semibold text-slate-900">My Account</h1>
        <p className="text-sm text-slate-500 mt-0.5">Profile, security, and organization</p>
      </div>

      {/* Profile */}
      <SectionTitle title="Profile" />
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
              Change email
            </button>
          )}
        </div>

        {showEmailForm && (
          <form onSubmit={handleRequestEmailChange} className="border-t border-slate-100 pt-3 space-y-3">
            <p className="text-xs text-slate-500">A verification link will be sent to the new address.</p>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="block text-xs font-medium text-slate-600 mb-1">New email</label>
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
                <label className="block text-xs font-medium text-slate-600 mb-1">Current password</label>
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
                Send verification
              </button>
              <button
                type="button"
                onClick={() => setShowEmailForm(false)}
                className="px-3 py-1.5 text-xs text-slate-500 hover:text-slate-700 transition-colors"
              >
                Cancel
              </button>
            </div>
          </form>
        )}

        <div className="border-t border-slate-100 pt-3 flex items-center gap-3 text-xs text-slate-500">
          <span className={`inline-flex items-center px-2 py-0.5 rounded font-medium capitalize ${
            me.tier === "enterprise" ? "bg-purple-100 text-purple-700" : "bg-slate-100 text-slate-600"
          }`}>
            {me.tier}
          </span>
          {me.email_verified ? (
            <span className="text-green-600 flex items-center gap-1"><Check size={11} /> Verified</span>
          ) : (
            <span className="text-amber-600">Email not verified</span>
          )}
        </div>
      </div>

      {/* Security */}
      <SectionTitle title="Security" />
      <ChangePasswordForm />

      {/* Organization */}
      <SectionTitle title="Organization" />
      {orgBanner && <Banner {...orgBanner} />}

      {me.org ? (
        <div className="rounded-xl border border-slate-200 bg-white p-5 space-y-3">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <Building2 size={16} className="text-blue-600" />
              <span className="text-sm font-semibold text-slate-800">{me.org.name}</span>
              <span className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-medium capitalize ${
                ROLE_COLORS[me.org_role] ?? "bg-slate-100 text-slate-600"
              }`}>
                {me.org_role}
              </span>
            </div>
            <a
              href="/app/org"
              className="flex items-center gap-1 text-xs text-blue-600 hover:text-blue-800 font-medium"
            >
              Manage team <ExternalLink size={11} />
            </a>
          </div>
          <p className="text-xs text-slate-400 font-mono">{me.org.slug}</p>
          <div className="border-t border-slate-100 pt-3">
            <button
              onClick={handleLeaveOrg}
              disabled={leavingOrg}
              className="text-xs text-red-500 hover:text-red-700 font-medium disabled:opacity-50 transition-colors"
            >
              {leavingOrg ? "Leaving…" : "Leave organization"}
            </button>
          </div>
        </div>
      ) : (
        <div className="rounded-xl border border-dashed border-slate-200 bg-slate-50 p-5 space-y-3">
          <p className="text-sm text-slate-500">You're not part of any organization.</p>
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
