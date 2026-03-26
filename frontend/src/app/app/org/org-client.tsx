"use client";
import { useState } from "react";
import useSWR from "swr";
import {
  Building2, Users, Edit2, Check, X, Trash2, Plus, Loader2, ShieldCheck, UserCog, Mail,
} from "lucide-react";
import { useRouter } from "next/navigation";
import {
  fetchCurrentUser,
  fetchOrg,
  fetchOrgMembers,
  updateOrg,
  addOrgMember,
  updateMemberRole,
  removeOrgMember,
  sendInvite,
  deleteOrg,
  type OrgMember,
} from "@/lib/api";

const inputCls =
  "w-full px-3 py-2 border border-slate-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-300 focus:border-transparent bg-white";

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

export function OrgClient() {
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
      router.push("/app/dashboard");
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
    return <div className="p-6 text-slate-400 text-sm">Loading…</div>;
  }

  if (!me.org_id) {
    return (
      <div className="p-6 max-w-xl mx-auto">
        <div className="rounded-lg border border-slate-200 bg-slate-50 p-6 text-center text-slate-500 text-sm">
          You are not a member of any organization.
        </div>
      </div>
    );
  }

  if (!isAdmin) {
    return (
      <div className="p-6 max-w-xl mx-auto text-slate-500 text-sm">
        You need admin or owner role to manage the organization.
      </div>
    );
  }

  return (
    <div className="p-6 max-w-3xl mx-auto space-y-5">
      {/* Header */}
      <div className="flex items-center gap-3">
        <Building2 size={22} className="text-blue-600" />
        <div>
          <h1 className="text-xl font-semibold text-slate-900">Organization</h1>
          <p className="text-sm text-slate-500 mt-0.5">Manage your org and its members</p>
        </div>
      </div>

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
