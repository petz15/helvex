import type { AppSettings, BoilerplatePattern, Company, CompanyFilters, CompanyPage, CompanyStats, Job, JobEvent, MapCluster, SavedView } from "./types";

const BASE = process.env.NEXT_PUBLIC_API_BASE ?? "";

// ── Org context ────────────────────────────────────────────────────────────────

export interface OrgInfo {
  id: number;
  name: string;
  slug: string;
  tier: string;
}

export interface CurrentUser {
  id: number;
  email: string;
  tier: string;
  org_role: string;
  is_active: boolean;
  email_verified: boolean;
  is_superadmin: boolean;
  org_id: number | null;
  org: OrgInfo | null;
}

export async function fetchCurrentUser(): Promise<CurrentUser> {
  const res = await fetch("/api/v1/auth/me", { credentials: "include" });
  if (!res.ok) throw new Error("Not authenticated");
  return res.json();
}

/**
 * Build an org-scoped path for workspace routes.
 * Usage: orgPath(orgId, "/companies/123/state")
 */
export function orgPath(orgId: number, suffix: string): string {
  return `/api/v1/orgs/${orgId}${suffix}`;
}

function buildUrl(path: string, params?: Record<string, string | number | undefined | null>): string {
  const url = new URL(BASE + path, typeof window !== "undefined" ? window.location.href : "http://localhost:3000");
  if (params) {
    for (const [k, v] of Object.entries(params)) {
      if (v !== undefined && v !== null && v !== "") {
        url.searchParams.set(k, String(v));
      }
    }
  }
  return url.pathname + url.search;
}

export async function fetchCompanies(filters: CompanyFilters = {}): Promise<CompanyPage> {
  const { page = 1, page_size = 50, sort = "-updated", ...rest } = filters;
  const url = buildUrl("/api/v1/companies", { page, page_size, sort, ...rest });
  const res = await fetch(url, { credentials: "include" });
  if (!res.ok) throw new Error(`Failed to fetch companies: ${res.status}`);
  return res.json();
}

export async function fetchCompany(id: number): Promise<Company> {
  const res = await fetch(`/api/v1/companies/${id}`, { credentials: "include" });
  if (!res.ok) throw new Error(`Company ${id} not found`);
  return res.json();
}

export async function fetchStats(): Promise<CompanyStats> {
  const res = await fetch("/api/v1/companies/stats", { credentials: "include" });
  if (!res.ok) throw new Error("Failed to fetch stats");
  return res.json();
}

export async function fetchCantons(): Promise<string[]> {
  const res = await fetch("/api/v1/companies/cantons", { credentials: "include" });
  if (!res.ok) return [];
  return res.json();
}

export async function fetchTaxonomy(): Promise<Record<string, [string, number][]>> {
  const res = await fetch("/api/v1/companies/taxonomy", { credentials: "include" });
  if (!res.ok) return {};
  return res.json();
}

export async function createNote(companyId: number, content: string): Promise<import("./types").Note> {
  const res = await fetch(`/api/v1/companies/${companyId}/notes`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ content }),
  });
  if (!res.ok) throw new Error("Failed to create note");
  return res.json();
}

export async function deleteNote(companyId: number, noteId: number): Promise<void> {
  await fetch(`/api/v1/companies/${companyId}/notes/${noteId}`, {
    method: "DELETE",
    credentials: "include",
  });
}

export async function updateCompany(id: number, data: Partial<Company>): Promise<Company> {
  const res = await fetch(`/api/v1/companies/${id}`, {
    method: "PATCH",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  if (!res.ok) throw new Error("Failed to update company");
  return res.json();
}

export async function selectCompanyWebsite(companyId: number, link: string): Promise<Company> {
  const res = await fetch(`/api/v1/companies/${companyId}/website`, {
    method: "PATCH",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ link }),
  });
  if (!res.ok) throw new Error("Failed to select website");
  return res.json();
}

// ── Jobs ──────────────────────────────────────────────────────────────────────

export async function fetchJobs(): Promise<Job[]> {
  const res = await fetch("/api/v1/jobs", { credentials: "include" });
  if (!res.ok) throw new Error("Failed to fetch jobs");
  return res.json();
}

export async function fetchJobEvents(jobId: number): Promise<JobEvent[]> {
  const res = await fetch(`/api/v1/jobs/${jobId}/events`, { credentials: "include" });
  if (!res.ok) return [];
  return res.json();
}

export async function cancelJob(id: number): Promise<void> {
  await fetch(`/api/v1/jobs/${id}/cancel`, { method: "POST", credentials: "include" });
}

export async function pauseJob(id: number): Promise<void> {
  await fetch(`/api/v1/jobs/${id}/pause`, { method: "POST", credentials: "include" });
}

export async function resumeJob(id: number): Promise<void> {
  await fetch(`/api/v1/jobs/${id}/resume`, { method: "POST", credentials: "include" });
}

export async function triggerJob(endpoint: string, body?: object): Promise<Job> {
  const res = await fetch(`/api/v1/${endpoint}`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: body ? JSON.stringify(body) : "{}",
  });
  if (!res.ok) throw new Error(`Failed to trigger job: ${res.status}`);
  return res.json();
}

// ── Settings ──────────────────────────────────────────────────────────────────

export async function fetchSettings(): Promise<AppSettings> {
  const res = await fetch("/api/v1/settings", { credentials: "include" });
  if (!res.ok) throw new Error("Failed to fetch settings");
  return res.json();
}

export async function saveSettings(data: Partial<AppSettings>): Promise<void> {
  const res = await fetch("/api/v1/settings", {
    method: "PUT",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  if (!res.ok) throw new Error("Failed to save settings");
}

export async function fetchBoilerplate(): Promise<BoilerplatePattern[]> {
  const res = await fetch("/api/v1/boilerplate", { credentials: "include" });
  if (!res.ok) return [];
  return res.json();
}

export async function createBoilerplate(data: { pattern: string; description?: string; example?: string }): Promise<BoilerplatePattern> {
  const res = await fetch("/api/v1/boilerplate", {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  if (!res.ok) throw new Error("Failed to create boilerplate pattern");
  return res.json();
}

export async function toggleBoilerplate(id: number): Promise<void> {
  await fetch(`/api/v1/boilerplate/${id}/toggle`, { method: "PATCH", credentials: "include" });
}

export async function deleteBoilerplate(id: number): Promise<void> {
  await fetch(`/api/v1/boilerplate/${id}`, { method: "DELETE", credentials: "include" });
}

// ── Map ───────────────────────────────────────────────────────────────────────

export async function fetchMapData(params?: Record<string, string>): Promise<{ features: import("./types").MapFeature[]; truncated: boolean; count: number }> {
  const url = buildUrl("/api/v1/map", params as Record<string, string | number | undefined | null>);
  const res = await fetch(url, { credentials: "include" });
  if (!res.ok) throw new Error("Failed to fetch map data");
  return res.json();
}

export async function fetchMapClusters(params?: Record<string, string>): Promise<{ cells: MapCluster[]; total: number }> {
  const url = buildUrl("/api/v1/map/clusters", params as Record<string, string | number | undefined | null>);
  const res = await fetch(url, { credentials: "include" });
  if (!res.ok) throw new Error("Failed to fetch map clusters");
  return res.json();
}

// ── Workspace (org-scoped overlay) ────────────────────────────────────────────

export interface OrgCompanyState {
  org_id: number;
  company_id: number;
  tags: string | null;
  review_status: string | null;
  contact_status: string | null;
  contact_name: string | null;
  contact_email: string | null;
  contact_phone: string | null;
  website_url: string | null;
  web_score: number | null;
  social_media_only: boolean | null;
  website_checked_at: string | null;
}

export interface UserCompanyState {
  user_id: number;
  company_id: number;
  ai_score: number | null;
  ai_category: string | null;
  ai_freeform: string | null;
  personal_score_override: number | null;
}

export async function fetchOrgCompanyState(orgId: number, companyId: number): Promise<OrgCompanyState | null> {
  const res = await fetch(orgPath(orgId, `/companies/${companyId}/state`), { credentials: "include" });
  if (res.status === 404) return null;
  if (!res.ok) throw new Error("Failed to fetch org company state");
  return res.json();
}

export async function updateOrgCompanyState(
  orgId: number,
  companyId: number,
  data: Partial<Omit<OrgCompanyState, "org_id" | "company_id" | "website_url" | "web_score" | "social_media_only" | "website_checked_at">>,
): Promise<OrgCompanyState> {
  const res = await fetch(orgPath(orgId, `/companies/${companyId}/state`), {
    method: "PATCH",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  if (!res.ok) throw new Error("Failed to update org company state");
  return res.json();
}

export async function fetchMyCompanyState(orgId: number, companyId: number): Promise<UserCompanyState | null> {
  const res = await fetch(orgPath(orgId, `/companies/${companyId}/my-state`), { credentials: "include" });
  if (res.status === 404) return null;
  if (!res.ok) throw new Error("Failed to fetch user company state");
  return res.json();
}

export async function fetchOrgJobs(orgId: number): Promise<Job[]> {
  const res = await fetch(orgPath(orgId, "/jobs"), { credentials: "include" });
  if (!res.ok) throw new Error("Failed to fetch org jobs");
  return res.json();
}

export async function fetchOrgSettings(orgId: number): Promise<Record<string, string>> {
  const res = await fetch(orgPath(orgId, "/settings"), { credentials: "include" });
  if (!res.ok) throw new Error("Failed to fetch org settings");
  return res.json();
}

// ── Org management ────────────────────────────────────────────────────────────

export interface OrgDetail {
  id: number;
  name: string;
  slug: string;
  tier: string;
  member_count: number;
}

export interface OrgMember {
  id: number;
  email: string;
  org_role: string;
  is_active: boolean;
  created_at: string;
}

export async function fetchOrg(orgId: number): Promise<OrgDetail> {
  const res = await fetch(orgPath(orgId, ""), { credentials: "include" });
  if (!res.ok) throw new Error("Failed to fetch org");
  return res.json();
}

export async function updateOrg(orgId: number, data: { name?: string }): Promise<OrgDetail> {
  const res = await fetch(orgPath(orgId, ""), {
    method: "PATCH",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  if (!res.ok) throw new Error("Failed to update org");
  return res.json();
}

export async function fetchOrgMembers(orgId: number): Promise<OrgMember[]> {
  const res = await fetch(orgPath(orgId, "/members"), { credentials: "include" });
  if (!res.ok) throw new Error("Failed to fetch members");
  return res.json();
}

export async function addOrgMember(
  orgId: number,
  data: { email: string; password: string; org_role: string },
): Promise<OrgMember> {
  const res = await fetch(orgPath(orgId, "/members"), {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail ?? "Failed to add member");
  }
  return res.json();
}

export async function updateMemberRole(
  orgId: number,
  userId: number,
  org_role: string,
): Promise<OrgMember> {
  const res = await fetch(orgPath(orgId, `/members/${userId}`), {
    method: "PATCH",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ org_role }),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail ?? "Failed to update role");
  }
  return res.json();
}

export async function removeOrgMember(orgId: number, userId: number): Promise<void> {
  const res = await fetch(orgPath(orgId, `/members/${userId}`), {
    method: "DELETE",
    credentials: "include",
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail ?? "Failed to remove member");
  }
}

export async function sendInvite(orgId: number, email: string): Promise<void> {
  const res = await fetch(orgPath(orgId, "/invites"), {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email }),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail ?? "Failed to send invite");
  }
}

// ── Org lifecycle ─────────────────────────────────────────────────────────────

export async function createOrg(name: string): Promise<OrgInfo> {
  const res = await fetch("/api/v1/orgs", {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name }),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail ?? "Failed to create org");
  }
  return res.json();
}

export async function leaveOrg(orgId: number): Promise<void> {
  const res = await fetch(`/api/v1/orgs/${orgId}/leave`, {
    method: "POST",
    credentials: "include",
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail ?? "Failed to leave org");
  }
}

// ── Invite acceptance ─────────────────────────────────────────────────────────

export interface InvitePreview {
  org_id: number;
  org_name: string;
  invited_email: string;
  user_exists: boolean;
}

export async function fetchInvitePreview(token: string): Promise<InvitePreview> {
  const res = await fetch(`/api/v1/invites/preview?token=${encodeURIComponent(token)}`, {
    credentials: "include",
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail ?? "Invalid or expired invite");
  }
  return res.json();
}

export async function acceptInvite(token: string, force = false): Promise<void> {
  const res = await fetch("/api/v1/invites/accept", {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ token, force }),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw Object.assign(new Error(body.detail?.code ?? body.detail ?? "Failed to accept invite"), {
      detail: body.detail,
    });
  }
}

export async function registerAndAcceptInvite(token: string, password: string): Promise<void> {
  const res = await fetch("/api/v1/invites/register-and-accept", {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ token, password }),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail ?? "Registration failed");
  }
}

// ── Account / email change ────────────────────────────────────────────────────

export async function requestEmailChange(newEmail: string, currentPassword: string): Promise<void> {
  const res = await fetch("/api/v1/auth/request-email-change", {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ new_email: newEmail, current_password: currentPassword }),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail ?? "Failed to request email change");
  }
}

export async function deleteOrg(orgId: number): Promise<void> {
  const res = await fetch(`/api/v1/orgs/${orgId}`, {
    method: "DELETE",
    credentials: "include",
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail ?? "Failed to delete org");
  }
}

// ── Admin (superadmin only) ───────────────────────────────────────────────────

export interface AdminStats {
  total_users: number;
  active_users: number;
  verified_users: number;
  total_orgs: number;
  users_in_org: number;
}

export interface AdminUser {
  id: number;
  email: string;
  tier: string;
  is_active: boolean;
  email_verified: boolean;
  is_superadmin: boolean;
  org_id: number | null;
  org_name: string | null;
  org_role: string;
  created_at: string;
}

export interface AdminOrg {
  id: number;
  name: string;
  slug: string;
  tier: string;
  member_count: number;
  created_at: string;
}

export interface AdminPage<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
}

export async function fetchAdminStats(): Promise<AdminStats> {
  const res = await fetch("/api/v1/admin/stats", { credentials: "include" });
  if (!res.ok) throw new Error("Failed to fetch admin stats");
  return res.json();
}

export async function fetchAdminUsers(params?: {
  q?: string; tier?: string; is_active?: boolean; page?: number; page_size?: number;
}): Promise<AdminPage<AdminUser>> {
  const url = buildUrl("/api/v1/admin/users", params as Record<string, string | number | undefined | null>);
  const res = await fetch(url, { credentials: "include" });
  if (!res.ok) throw new Error("Failed to fetch users");
  return res.json();
}

export async function updateAdminUser(userId: number, data: {
  tier?: string; is_active?: boolean; is_superadmin?: boolean;
}): Promise<AdminUser> {
  const res = await fetch(`/api/v1/admin/users/${userId}`, {
    method: "PATCH",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail ?? "Failed to update user");
  }
  return res.json();
}

export async function fetchAdminOrgs(params?: {
  q?: string; tier?: string; page?: number; page_size?: number;
}): Promise<AdminPage<AdminOrg>> {
  const url = buildUrl("/api/v1/admin/orgs", params as Record<string, string | number | undefined | null>);
  const res = await fetch(url, { credentials: "include" });
  if (!res.ok) throw new Error("Failed to fetch orgs");
  return res.json();
}

export async function updateAdminOrg(orgId: number, data: {
  name?: string; tier?: string;
}): Promise<AdminOrg> {
  const res = await fetch(`/api/v1/admin/orgs/${orgId}`, {
    method: "PATCH",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail ?? "Failed to update org");
  }
  return res.json();
}

export async function deleteAdminOrg(orgId: number): Promise<void> {
  const res = await fetch(`/api/v1/admin/orgs/${orgId}`, {
    method: "DELETE",
    credentials: "include",
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail ?? "Failed to delete org");
  }
}

// ── Saved Views ───────────────────────────────────────────────────────────────

export async function fetchSavedViews(): Promise<SavedView[]> {
  const res = await fetch("/api/v1/views");
  if (!res.ok) throw new Error("Failed to fetch views");
  return res.json();
}

export async function saveView(name: string, filters: CompanyFilters): Promise<SavedView> {
  const res = await fetch("/api/v1/views", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, filters }),
  });
  if (!res.ok) throw new Error("Failed to save view");
  return res.json();
}

export async function deleteView(id: number): Promise<void> {
  await fetch(`/api/v1/views/${id}`, { method: "DELETE" });
}
