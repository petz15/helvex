import type {
  AppSettings,
  BoilerplatePattern,
  Company,
  CompanyFilters,
  CompanyPage,
  CompanyStats,
  GoogleDirectoryDomain,
  GoogleStopword,
  Job,
  JobEvent,
  MapCluster,
  SavedView,
  TfidfStopword,
} from "./types";

const BASE = process.env.NEXT_PUBLIC_API_BASE ?? "";

// ── Org context ────────────────────────────────────────────────────────────────

export interface OrgInfo {
  id: number;
  name: string;
  slug: string;
  tier: string;
  role?: string | null;
}

export interface CurrentUser {
  id: number;
  email: string;
  billing_address_json: string | null;
  payment_customer_id: string | null;
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

export interface BillingCheckoutResponse {
  provider: string;
  checkout_url: string;
  external_id: string | null;
  amount_chf: number;
}

export interface BillingTier {
  id: number;
  slug: string;
  display_name: string;
  description: string;
  monthly_price_chf: number;
  yearly_multiplier: number;
  yearly_price_chf: number;
  topup_bonus_rate: number;
  sort_order: number;
  is_active: boolean;
  is_public: boolean;
}

// ── Billing history (user-facing) ─────────────────────────────────────────────

export interface BillingSummary {
  org_id: number;
  tier: string;
  billing_cycle: string;
  subscription_period_end: string | null;
  credits_balance: number;
  credits_balance_chf: number;
  has_saved_payment_method: boolean;
}

export interface CreditTransaction {
  id: number;
  amount: number;
  type: string;
  action_type: string | null;
  reference_id: string | null;
  credits_before: number;
  credits_after: number;
  created_at: string;
}

export interface PaymentRecord {
  id: number;
  provider: string;
  kind: string;
  status: string;
  decline_reason: string | null;
  amount_chf: number;
  payment_method: string | null;
  subscription_tier: string | null;
  subscription_billing_cycle: string | null;
  credits_purchased: number | null;
  credits_bonus: number | null;
  credits_total_granted: number | null;
  created_at: string;
  authorized_at: string | null;
  refunded_at: string | null;
  refunded_amount_chf: number | null;
}

export interface PaginatedResult<T> {
  total: number;
  page: number;
  page_size: number;
  items: T[];
}

export async function fetchBillingSummary(): Promise<BillingSummary> {
  const res = await fetch("/api/v1/billing/summary", { credentials: "include" });
  if (!res.ok) throw new Error("Failed to fetch billing summary");
  return res.json();
}

export async function fetchBillingTiers(): Promise<BillingTier[]> {
  const res = await fetch("/api/v1/billing/tiers", { credentials: "include" });
  if (!res.ok) throw new Error("Failed to fetch billing tiers");
  return res.json();
}

export async function fetchCreditTransactions(page = 1, page_size = 20): Promise<PaginatedResult<CreditTransaction>> {
  const res = await fetch(`/api/v1/billing/credits?page=${page}&page_size=${page_size}`, { credentials: "include" });
  if (!res.ok) throw new Error("Failed to fetch credit history");
  return res.json();
}

export async function fetchPaymentHistory(page = 1, page_size = 20): Promise<PaginatedResult<PaymentRecord>> {
  const res = await fetch(`/api/v1/billing/payments?page=${page}&page_size=${page_size}`, { credentials: "include" });
  if (!res.ok) throw new Error("Failed to fetch payment history");
  return res.json();
}

export async function cancelPendingPayment(paymentId: number): Promise<void> {
  const res = await fetch(`/api/v1/billing/payments/${paymentId}/cancel`, {
    method: "POST",
    credentials: "include",
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail ?? "Failed to cancel payment");
  }
}

// ── Admin billing ──────────────────────────────────────────────────────────────

export interface AdminPaymentTransaction {
  id: number;
  org_id: number;
  provider: string;
  external_id: string;
  order_reference: string;
  amount_chf: number;
  kind: string;
  status: string;
  payment_method: string | null;
  provider_transaction_id: string | null;
  cardholder_name: string | null;
  billing_address: string | null;  // JSON string
  subscription_tier: string | null;
  subscription_billing_cycle: string | null;
  credits_purchased: number | null;
  credits_bonus: number | null;
  credits_total_granted: number | null;
  error_code: string | null;
  error_message: string | null;
  refunded_amount_chf: number | null;
  webhook_processed_at: string | null;
  created_at: string;
  authorized_at: string | null;
  refunded_at: string | null;
}

export async function fetchAdminPaymentTransactions(params?: {
  org_id?: number; provider?: string; status?: string; kind?: string;
  page?: number; page_size?: number;
}): Promise<AdminPage<AdminPaymentTransaction>> {
  const url = buildUrl("/api/v1/admin/payment-transactions", params as Record<string, string | number | undefined | null>);
  const res = await fetch(url, { credentials: "include" });
  if (!res.ok) throw new Error("Failed to fetch payment transactions");
  return res.json();
}

export async function fetchAdminOrgPaymentTransactions(
  orgId: number,
  params?: { page?: number; page_size?: number },
): Promise<AdminPage<AdminPaymentTransaction>> {
  const url = buildUrl(`/api/v1/admin/orgs/${orgId}/payment-transactions`, params as Record<string, string | number | undefined | null>);
  const res = await fetch(url, { credentials: "include" });
  if (!res.ok) throw new Error("Failed to fetch org payment transactions");
  return res.json();
}

export interface BillingAddressPayload {
  first_name: string;
  last_name: string;
  street: string;
  number: string;
  postal_code: string;
  city: string;
  country: string;
  company_name?: string;
}

export interface BillingAddressItem extends BillingAddressPayload {
  id: string;
  label?: string | null;
}

export interface BillingAddressBook {
  addresses: BillingAddressItem[];
  default_id: string | null;
}

export interface PaymentMethodRegistrationResponse {
  provider: string;
  checkout_url: string;
  external_id: string | null;
}

export async function createSubscriptionCheckout(data: {
  tier: string;
  billing_cycle: "monthly" | "yearly";
  success_url: string;
  cancel_url: string;
  billing_address?: BillingAddressPayload | null;
  save_payment_method?: boolean;
  provider?: "worldline" | "stripe" | null;
}): Promise<BillingCheckoutResponse> {
  const res = await fetch("/api/v1/billing/checkout/subscription", {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  if (!res.ok) {
    const raw = await res.text();
    let detail: string | undefined;
    try {
      const parsed = JSON.parse(raw) as { detail?: string };
      detail = parsed?.detail;
    } catch {
      detail = raw.trim() || undefined;
    }
    throw new Error(detail ?? `Subscription checkout failed (HTTP ${res.status})`);
  }
  return res.json();
}

export async function createTopupCheckout(data: {
  credits: number;
  success_url: string;
  cancel_url: string;
  billing_address?: BillingAddressPayload | null;
  save_payment_method?: boolean;
  use_new_card?: boolean;
  provider?: "worldline" | "stripe" | null;
}): Promise<BillingCheckoutResponse> {
  const res = await fetch("/api/v1/billing/checkout/topup", {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  if (!res.ok) {
    const raw = await res.text();
    let detail: string | undefined;
    try {
      const parsed = JSON.parse(raw) as { detail?: string };
      detail = parsed?.detail;
    } catch {
      detail = raw.trim() || undefined;
    }
    throw new Error(detail ?? `Top-up checkout failed (HTTP ${res.status})`);
  }
  return res.json();
}

export async function createWorldlineCardRegistration(data: {
  success_url: string;
  cancel_url: string;
  billing_address?: BillingAddressPayload | null;
}): Promise<PaymentMethodRegistrationResponse> {
  const res = await fetch("/api/v1/billing/payment-methods/worldline/register", {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  if (!res.ok) {
    const raw = await res.text();
    let detail: string | undefined;
    try {
      const parsed = JSON.parse(raw) as { detail?: string };
      detail = parsed?.detail;
    } catch {
      detail = raw.trim() || undefined;
    }
    throw new Error(detail ?? `Card registration failed (HTTP ${res.status})`);
  }
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
  const { page = 1, page_size = 50, sort = "-updated", has_website, ...rest } = filters;
  const params: Record<string, string | number | undefined | null> = { page, page_size, sort, ...rest };
  if (has_website !== undefined && has_website !== null) params.has_website = String(has_website);
  const url = buildUrl("/api/v1/companies", params);
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

export async function bulkUpdateCompanies(
  companyIds: number[],
  field: string,
  value: string | null,
): Promise<{ updated: number }> {
  const res = await fetch(`/api/v1/companies/bulk-update`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ company_ids: companyIds, field, value }),
  });
  if (!res.ok) throw new Error("Bulk update failed");
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

export async function runCompanyWebSearch(companyId: number, num = 10): Promise<void> {
  const res = await fetch(`/api/v1/companies/${companyId}/google-search?num=${encodeURIComponent(String(num))}`, {
    method: "GET",
    credentials: "include",
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail ?? "Failed to run web search");
  }
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

export async function fetchGoogleStopwords(): Promise<GoogleStopword[]> {
  const res = await fetch("/api/v1/google-stopwords", { credentials: "include" });
  if (!res.ok) return [];
  return res.json();
}

export async function createGoogleStopword(data: { value: string; description?: string }): Promise<GoogleStopword> {
  const res = await fetch("/api/v1/google-stopwords", {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  if (!res.ok) throw new Error("Failed to create stopword");
  return res.json();
}

export async function toggleGoogleStopword(id: number): Promise<void> {
  await fetch(`/api/v1/google-stopwords/${id}/toggle`, { method: "PATCH", credentials: "include" });
}

export async function deleteGoogleStopword(id: number): Promise<void> {
  await fetch(`/api/v1/google-stopwords/${id}`, { method: "DELETE", credentials: "include" });
}

export async function fetchGoogleDirectoryDomains(): Promise<GoogleDirectoryDomain[]> {
  const res = await fetch("/api/v1/google-directory-domains", { credentials: "include" });
  if (!res.ok) return [];
  return res.json();
}

export async function createGoogleDirectoryDomain(data: { value: string; description?: string }): Promise<GoogleDirectoryDomain> {
  const res = await fetch("/api/v1/google-directory-domains", {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  if (!res.ok) throw new Error("Failed to create directory domain");
  return res.json();
}

export async function toggleGoogleDirectoryDomain(id: number): Promise<void> {
  await fetch(`/api/v1/google-directory-domains/${id}/toggle`, { method: "PATCH", credentials: "include" });
}

export async function deleteGoogleDirectoryDomain(id: number): Promise<void> {
  await fetch(`/api/v1/google-directory-domains/${id}`, { method: "DELETE", credentials: "include" });
}

export async function fetchTfidfStopwords(): Promise<TfidfStopword[]> {
  const res = await fetch("/api/v1/tfidf-stopwords", { credentials: "include" });
  if (!res.ok) return [];
  return res.json();
}

export async function createTfidfStopword(data: { value: string; description?: string }): Promise<TfidfStopword> {
  const res = await fetch("/api/v1/tfidf-stopwords", {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  if (!res.ok) throw new Error("Failed to create TF-IDF stopword");
  return res.json();
}

export async function toggleTfidfStopword(id: number): Promise<void> {
  await fetch(`/api/v1/tfidf-stopwords/${id}/toggle`, { method: "PATCH", credentials: "include" });
}

export async function deleteTfidfStopword(id: number): Promise<void> {
  await fetch(`/api/v1/tfidf-stopwords/${id}`, { method: "DELETE", credentials: "include" });
}

export async function seedDefaults(): Promise<{ google_stopwords: number; directory_domains: number; tfidf_stopwords: number }> {
  const res = await fetch("/api/v1/settings/seed-defaults", { method: "POST", credentials: "include" });
  if (!res.ok) throw new Error("Failed to seed defaults");
  return res.json();
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

export async function geocodeMapAddress(address: string): Promise<{ lat: number; lon: number; address: string }> {
  const url = buildUrl("/api/v1/map/geocode", { address });
  const res = await fetch(url, { credentials: "include" });
  if (!res.ok) throw new Error("Failed to geocode address");
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

export interface OrgEffectiveSettings {
  anthropic_api_key_set: boolean;
  claude_target_description: string;
  claude_classify_prompt: string;
  claude_classify_categories: string;
  scoring_target_clusters: string;
  scoring_exclude_clusters: string;
  scoring_cluster_hit_points: string;
  scoring_cluster_exclude_points: string;
  scoring_target_keywords: string;
  scoring_exclude_keywords: string;
  scoring_keyword_hit_points: string;
  scoring_keyword_exclude_points: string;
  scoring_origin_lat: string;
  scoring_origin_lon: string;
  scoring_dist_15km: string;
  scoring_dist_40km: string;
  scoring_dist_80km: string;
  scoring_dist_130km: string;
  scoring_dist_far: string;
  scoring_legal_form_scores: string;
  scoring_legal_form_default: string;
  scoring_cancelled_score: string;
  scoring_weight_ai: string;
  scoring_weight_web: string;
  scoring_weight_flex: string;
}

export async function fetchOrgEffectiveSettings(orgId: number): Promise<OrgEffectiveSettings> {
  const res = await fetch(orgPath(orgId, "/settings/effective"), { credentials: "include" });
  if (!res.ok) throw new Error("Failed to fetch org effective settings");
  return res.json();
}

export async function saveOrgWorkspaceSettings(
  orgId: number,
  data: Partial<Record<string, string | null>>,
): Promise<void> {
  const res = await fetch(orgPath(orgId, "/settings"), {
    method: "PUT",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  if (!res.ok) throw new Error("Failed to save workspace settings");
}

export async function setOrgSetting(orgId: number, key: string, value: string): Promise<void> {
  const res = await fetch(orgPath(orgId, `/settings/${encodeURIComponent(key)}`), {
    method: "PUT",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ value }),
  });
  if (!res.ok) throw new Error("Failed to save org setting");
}

export async function deleteOrgSetting(orgId: number, key: string): Promise<void> {
  const res = await fetch(orgPath(orgId, `/settings/${encodeURIComponent(key)}`), {
    method: "DELETE",
    credentials: "include",
  });
  if (!res.ok && res.status !== 404) throw new Error("Failed to delete org setting");
}

// ── Org management ────────────────────────────────────────────────────────────

export interface OrgDetail {
  id: number;
  name: string;
  slug: string;
  tier: string;
  credits_balance: number;
  verified_business: boolean;
  verified_domain: string | null;
  billing_address_json: string | null;
  default_payment_user_id: number | null;
  custom_features: Record<string, unknown> | null;
  member_count: number;
}

export interface OrgMember {
  id: number;
  email: string;
  org_role: string;
  is_active: boolean;
  created_at: string;
  has_saved_payment_method: boolean;
}

export async function fetchOrg(orgId: number): Promise<OrgDetail> {
  const res = await fetch(orgPath(orgId, ""), { credentials: "include" });
  if (!res.ok) throw new Error("Failed to fetch org");
  return res.json();
}

export function parseBillingAddressJson(value: string | null | undefined): BillingAddressPayload | null {
  if (!value) return null;
  try {
    const parsed = JSON.parse(value) as Record<string, unknown>;
    if (!parsed || typeof parsed !== "object") return null;
    if (Array.isArray((parsed as { addresses?: unknown }).addresses)) {
      const list = parsed as { addresses: Array<Record<string, unknown>>; default_id?: string };
      if (!list.addresses.length) return null;
      const selected = list.addresses.find((a) => String(a.id ?? "") === String(list.default_id ?? "")) ?? list.addresses[0];
      return {
        first_name: String(selected.first_name ?? ""),
        last_name: String(selected.last_name ?? ""),
        street: String(selected.street ?? ""),
        number: String(selected.number ?? ""),
        postal_code: String(selected.postal_code ?? ""),
        city: String(selected.city ?? ""),
        country: String(selected.country ?? "CH"),
        company_name: selected.company_name ? String(selected.company_name) : undefined,
      };
    }
    const firstName = parsed.first_name;
    const lastName = parsed.last_name;
    const street = parsed.street;
    const number = parsed.number;
    const postalCode = parsed.postal_code;
    const city = parsed.city;
    const country = parsed.country;

    if (
      typeof firstName !== "string" ||
      typeof lastName !== "string" ||
      typeof street !== "string" ||
      typeof number !== "string" ||
      typeof postalCode !== "string" ||
      typeof city !== "string" ||
      typeof country !== "string"
    ) {
      return null;
    }

    return {
      first_name: firstName,
      last_name: lastName,
      street,
      number,
      postal_code: postalCode,
      city,
      country,
      company_name: typeof parsed.company_name === "string" ? parsed.company_name : undefined,
    };
  } catch {
    return null;
  }
}

export async function fetchCurrentUserBillingAddresses(): Promise<BillingAddressBook> {
  const res = await fetch("/api/v1/auth/me/billing-addresses", { credentials: "include" });
  if (!res.ok) throw new Error("Failed to load billing addresses");
  return res.json();
}

export async function addCurrentUserBillingAddress(
  address: BillingAddressPayload & { label?: string; make_default?: boolean },
): Promise<BillingAddressBook> {
  const res = await fetch("/api/v1/auth/me/billing-addresses", {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(address),
  });
  if (!res.ok) throw new Error("Failed to save billing address");
  return res.json();
}

export async function setCurrentUserDefaultBillingAddress(addressId: string): Promise<BillingAddressBook> {
  const res = await fetch(`/api/v1/auth/me/billing-addresses/${encodeURIComponent(addressId)}/default`, {
    method: "PUT",
    credentials: "include",
  });
  if (!res.ok) throw new Error("Failed to set default billing address");
  return res.json();
}

export async function deleteCurrentUserBillingAddress(addressId: string): Promise<BillingAddressBook> {
  const res = await fetch(`/api/v1/auth/me/billing-addresses/${encodeURIComponent(addressId)}`, {
    method: "DELETE",
    credentials: "include",
  });
  if (!res.ok) throw new Error("Failed to delete billing address");
  return res.json();
}

export async function updateOrg(orgId: number, data: { name?: string; billing_address?: BillingAddressPayload | null }): Promise<OrgDetail> {
  const res = await fetch(orgPath(orgId, ""), {
    method: "PATCH",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  if (!res.ok) throw new Error("Failed to update org");
  return res.json();
}

export async function setOrgDefaultPaymentUser(orgId: number, userId: number | null): Promise<OrgDetail> {
  const res = await fetch(orgPath(orgId, "/default-payment-user"), {
    method: "PUT",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ user_id: userId }),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail ?? "Failed to update default payment method");
  }
  return res.json();
}

export async function updateCurrentUserBillingAddress(billingAddress: BillingAddressPayload): Promise<CurrentUser> {
  const res = await fetch("/api/v1/auth/me/billing-address", {
    method: "PUT",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ...billingAddress }),
  });
  if (!res.ok) throw new Error("Failed to save billing address");
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

export async function updateOrgBillingAddress(orgId: number, billingAddress: BillingAddressPayload): Promise<OrgDetail> {
  const res = await fetch(orgPath(orgId, "/billing-address"), {
    method: "PUT",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ...billingAddress }),
  });
  if (!res.ok) throw new Error("Failed to save billing address");
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

export async function fetchMyOrgs(): Promise<OrgInfo[]> {
  const res = await fetch("/api/v1/orgs/me", { credentials: "include" });
  if (!res.ok) throw new Error("Failed to fetch organizations");
  return res.json();
}

export async function switchOrg(orgId: number): Promise<void> {
  const res = await fetch(`/api/v1/orgs/switch/${orgId}`, {
    method: "POST",
    credentials: "include",
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail ?? "Failed to switch organization");
  }
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

export async function fetchAdminTiers(): Promise<BillingTier[]> {
  const res = await fetch("/api/v1/admin/tiers", { credentials: "include" });
  if (!res.ok) throw new Error("Failed to fetch billing tiers");
  return res.json();
}

export async function fetchAdminStats(): Promise<AdminStats> {
  const res = await fetch("/api/v1/admin/stats", { credentials: "include" });
  if (!res.ok) throw new Error("Failed to fetch admin stats");
  return res.json();
}

export async function fetchAdminUsers(params?: {
  q?: string; is_active?: boolean; page?: number; page_size?: number;
}): Promise<AdminPage<AdminUser>> {
  const url = buildUrl("/api/v1/admin/users", params as Record<string, string | number | undefined | null>);
  const res = await fetch(url, { credentials: "include" });
  if (!res.ok) throw new Error("Failed to fetch users");
  return res.json();
}

export async function updateAdminUser(userId: number, data: {
  is_active?: boolean; is_superadmin?: boolean;
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

// ── CSV Export job ────────────────────────────────────────────────────────────

export interface CSVExportStatus {
  job: import("./types").Job | null;
  download_url: string | null;
  expires_at: string | null;
  row_count: number | null;
}

export async function enqueueCSVExport(filters: import("./types").CompanyFilters): Promise<import("./types").Job> {
  const { page: _p, page_size: _ps, ...rest } = filters;
  const res = await fetch("/api/v1/jobs/enqueue/csv-export", {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(rest),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail ?? "Failed to queue CSV export");
  }
  return res.json();
}

export async function fetchCSVExportStatus(): Promise<CSVExportStatus> {
  const res = await fetch("/api/v1/jobs/csv-export/status", { credentials: "include" });
  if (!res.ok) throw new Error("Failed to fetch export status");
  return res.json();
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
