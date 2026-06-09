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

export class ApiError extends Error {
  status: number;
  detail: string | null;
  retryAfter: number | null;
  constructor(message: string, status: number, detail: string | null = null, retryAfter: number | null = null) {
    super(message);
    this.status = status;
    this.detail = detail;
    this.retryAfter = retryAfter;
  }
}

async function _handleErrorResponse(res: Response): Promise<never> {
  let detail: string | null = null;
  try {
    const body = await res.json();
    detail = body?.detail ?? null;
  } catch { /* ignore */ }
  const retryAfter = res.headers.get("Retry-After") ? Number(res.headers.get("Retry-After")) : null;
  throw new ApiError(detail ?? res.statusText, res.status, detail, retryAfter);
}

export function createFetcher(url: string): Promise<any> {
  return fetch(url, { credentials: "include" }).then((res) => {
    if (!res.ok) return _handleErrorResponse(res);
    return res.json();
  });
}

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
  if (!res.ok) throw new ApiError("Not authenticated", res.status);
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
  subscription_cancel_at_period_end: boolean;
  pending_downgrade_tier: string | null;
  credits_balance: number;
  credits_balance_chf: number;
  has_saved_payment_method: boolean;
  low_credit_alert_at: string | null;
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
  vat_rate: number | null;
  vat_amount_chf: number | null;
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

export interface PaymentMethod {
  id: string;
  scope: "org" | "personal";
  provider: string;
  alias_id: string;
  masked_number: string | null;
  brand: string | null;
  holder_name: string | null;
  exp_year: number | null;
  exp_month: number | null;
  is_default: boolean;
  label: string | null;
}

export async function fetchPaymentMethods(): Promise<{ items: PaymentMethod[] }> {
  const res = await fetch("/api/v1/billing/payment-methods", { credentials: "include" });
  if (!res.ok) throw new Error("Failed to fetch payment methods");
  return res.json();
}

export async function deletePaymentMethod(aliasId: string, scope: "org" | "personal"): Promise<void> {
  const res = await fetch(
    `/api/v1/billing/payment-methods/${encodeURIComponent(aliasId)}?scope=${scope}`,
    { method: "DELETE", credentials: "include" },
  );
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error((body as { detail?: string }).detail ?? "Failed to remove payment method");
  }
}

export async function setPaymentMethodDefault(aliasId: string): Promise<void> {
  const res = await fetch(
    `/api/v1/billing/payment-methods/${encodeURIComponent(aliasId)}/set-default`,
    { method: "PUT", credentials: "include" },
  );
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error((body as { detail?: string }).detail ?? "Failed to set default");
  }
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

export async function cancelSubscription(): Promise<void> {
  const res = await fetch("/api/v1/billing/subscription/cancel", {
    method: "POST",
    credentials: "include",
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error((body as { detail?: string }).detail ?? "Failed to cancel subscription");
  }
}

export async function reactivateSubscription(): Promise<void> {
  const res = await fetch("/api/v1/billing/subscription/reactivate", {
    method: "POST",
    credentials: "include",
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error((body as { detail?: string }).detail ?? "Failed to reactivate subscription");
  }
}

export async function scheduleDowngrade(tier: string): Promise<void> {
  const res = await fetch("/api/v1/billing/subscription/schedule-downgrade", {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ tier }),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error((body as { detail?: string }).detail ?? "Failed to schedule downgrade");
  }
}

export interface UpgradeProration {
  credits_granted: number;
  credits_chf: number;
  remaining_days: number;
  plan_cost_chf: number;
}

export async function claimUpgradeProration(): Promise<UpgradeProration> {
  const res = await fetch("/api/v1/billing/subscription/upgrade-proration", {
    method: "POST",
    credentials: "include",
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error((body as { detail?: string }).detail ?? "Failed to calculate proration");
  }
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

export async function adminAdjustOrgCredits(
  orgId: number,
  amount: number,
  adjustmentType: "grant" | "deduct",
  reason: string,
): Promise<void> {
  const res = await fetch(`/api/v1/admin/orgs/${orgId}/credits`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ amount, adjustment_type: adjustmentType, reason }),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error((body as { detail?: string }).detail ?? "Failed to adjust credits");
  }
}

export async function adminRefundTransaction(
  txId: number,
  amountChf: number,
  reason: string,
): Promise<AdminPaymentTransaction> {
  const res = await fetch(`/api/v1/admin/payment-transactions/${txId}/refund`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ amount_chf: amountChf, reason }),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error((body as { detail?: string }).detail ?? "Refund failed");
  }
  return res.json();
}

export function getPaymentInvoiceUrl(paymentId: number): string {
  return `/api/v1/billing/payments/${paymentId}/invoice`;
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
  upgrade_proration_credits?: number | null;
  selected_alias_id?: string | null;
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
  selected_alias_id?: string | null;
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

export async function addPersonalCardToOrg(): Promise<void> {
  const res = await fetch("/api/v1/billing/payment-methods/add-personal-to-org", {
    method: "POST",
    credentials: "include",
  });
  if (!res.ok) {
    const raw = await res.text();
    let detail: string | undefined;
    try { detail = (JSON.parse(raw) as { detail?: string })?.detail; } catch { detail = raw.trim() || undefined; }
    throw new Error(detail ?? `Failed to add card (HTTP ${res.status})`);
  }
}

export async function createWorldlineCardRegistration(data: {
  success_url: string;
  cancel_url: string;
  billing_address?: BillingAddressPayload | null;
  scope?: "org" | "personal";
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
  const { page = 1, page_size = 50, sort = "-updated", has_website, active_only, ...rest } = filters;
  const params: Record<string, string | number | undefined | null> = { page, page_size, sort, ...rest };
  if (has_website !== undefined && has_website !== null) params.has_website = String(has_website);
  if (active_only !== undefined && active_only !== null) params.active_only = String(active_only);
  const url = buildUrl("/api/v1/companies/", params);
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

export interface CategoryStats {
  count: number;
  avg_ai_score: number | null;
  avg_flex_score: number | null;
  avg_web_score: number | null;
  avg_combined_score: number | null;
  bands: { "80plus": number; "60to80": number; "40to60": number; "below40": number; "unscored": number };
  canton_breakdown: [string, number][];
  unscored_count: number;
}

export async function fetchCategoryStats(
  type: string,
  value: string,
  orgId?: number | null,
): Promise<CategoryStats> {
  const params = new URLSearchParams({ type, value });
  if (orgId) params.set("org_id", String(orgId));
  const res = await fetch(`/api/v1/companies/category-stats?${params}`, { credentials: "include" });
  if (!res.ok) throw new Error("Failed to fetch category stats");
  return res.json();
}

export interface MarketSegment {
  section: string;
  labels: Record<string, string>;
  company_count: number;
  avg_relevance: number | null;
  top_keywords: string[];
  canton_top: string | null;
  canton_pct: number;
  growth_recent: number;
}

export async function fetchMarketSegments(): Promise<MarketSegment[]> {
  const res = await fetch("/api/v1/companies/market-segments", { credentials: "include" });
  if (!res.ok) throw new Error("Failed to fetch market segments");
  return res.json();
}

export async function enqueueGenericJob(
  endpoint: string,
  params: Record<string, unknown> = {},
): Promise<{ id: number; status: string }> {
  const res = await fetch(`/api/v1/jobs/enqueue/${endpoint}`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(params),
  });
  if (!res.ok) throw new Error(`Failed to enqueue job: ${endpoint}`);
  return res.json();
}

export interface NogaNode {
  code: string;
  label: string;
  labels?: Record<string, string>;
  level: string;
  own_count: number;
  count: number;
  children: NogaNode[];
}

export async function fetchNogaHierarchy(): Promise<NogaNode[]> {
  const res = await fetch("/api/v1/companies/noga-hierarchy", { credentials: "include" });
  if (!res.ok) throw new ApiError(res.statusText, res.status);
  return res.json();
}

export interface NogaAnnotation {
  type: 'INCLUDES' | 'EXCLUDES' | 'INCLUDES_ALSO' | 'HIER_LEVEL';
  text?: Record<string, string>;
}

export async function fetchNogaDescription(code: string): Promise<{ code: string; annotations: NogaAnnotation[] }> {
  const res = await fetch(`/api/v1/companies/noga-description/${encodeURIComponent(code)}`, { credentials: "include" });
  if (!res.ok) throw new ApiError(res.statusText, res.status);
  return res.json();
}

export interface SemanticSearchResult {
  value: string;
  count: number;
  similarity: number;
}

export interface SemanticSearchResponse {
  query: string;
  clusters: SemanticSearchResult[];
  categories: SemanticSearchResult[];
  keywords: SemanticSearchResult[];
  noga_codes: SemanticSearchResult[];
}

export async function semanticSearch(q: string, topK = 8): Promise<SemanticSearchResponse> {
  const res = await fetch(
    `/api/v1/companies/semantic-search?q=${encodeURIComponent(q)}&top_k=${topK}`,
    { credentials: "include" },
  );
  if (!res.ok) throw new ApiError(res.statusText, res.status);
  return res.json();
}

export interface PurposeSearchHit {
  company_id: number;
  name: string;
  uid: string;
  canton: string | null;
  purpose: string | null;
  similarity: number;
  lang: string | null;
}

export interface PurposeSearchResponse {
  query: string;
  embedding_type: string;
  results: PurposeSearchHit[];
}

export async function fetchPurposeSemanticSearch(q: string, limit = 50): Promise<PurposeSearchResponse> {
  const res = await fetch(
    `/api/v1/search/semantic?q=${encodeURIComponent(q)}&limit=${limit}`,
    { credentials: "include" },
  );
  if (!res.ok) throw new ApiError(res.statusText, res.status);
  return res.json();
}

export async function searchKeywords(q: string, limit = 20): Promise<{ keyword: string; count: number }[]> {
  const res = await fetch(`/api/v1/companies/keywords/search?q=${encodeURIComponent(q)}&limit=${limit}`, { credentials: "include" });
  if (!res.ok) return [];
  return res.json();
}

export async function searchClusters(q: string, limit = 20): Promise<{ cluster: string; count: number }[]> {
  const res = await fetch(`/api/v1/companies/clusters/search?q=${encodeURIComponent(q)}&limit=${limit}`, { credentials: "include" });
  if (!res.ok) return [];
  return res.json();
}

export interface ClusterRegistryEntry {
  id: number;
  canonical_name: string;
  top_terms: string; // JSON array string
  active: boolean;
  company_count: number;
  created_at: string;
  updated_at: string;
}

export async function fetchClusterRegistry(): Promise<ClusterRegistryEntry[]> {
  const res = await fetch("/api/v1/clusters/registry", { credentials: "include" });
  if (!res.ok) throw new Error("Failed to fetch cluster registry");
  return res.json();
}

export async function renameCluster(id: number, newName: string): Promise<ClusterRegistryEntry> {
  const res = await fetch(`/api/v1/clusters/registry/${id}`, {
    method: "PATCH",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ new_name: newName }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error((err as { detail?: string }).detail ?? "Rename failed");
  }
  return res.json();
}

export async function bulkTagCompanies(
  companyIds: number[],
  tag: string,
  action: "add" | "remove",
): Promise<{ updated: number }> {
  const res = await fetch("/api/v1/companies/bulk-tag", {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ company_ids: companyIds, tag, action }),
  });
  if (!res.ok) throw new Error("Bulk tag failed");
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
  if (!res.ok) return _handleErrorResponse(res);
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
  if (!res.ok) return _handleErrorResponse(res);
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

export async function createBoilerplate(data: { pattern: string; description?: string; example?: string; truncate?: boolean }): Promise<BoilerplatePattern> {
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

export async function toggleBoilerplateTruncate(id: number): Promise<void> {
  await fetch(`/api/v1/boilerplate/${id}/toggle-truncate`, { method: "PATCH", credentials: "include" });
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
  scoring_noga_targets: string;
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
  vat_id: string | null;
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
  const res = await fetch(orgPath(orgId, "/"), { credentials: "include" });
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

export async function updateOrg(orgId: number, data: { name?: string; billing_address?: BillingAddressPayload | null; vat_id?: string | null }): Promise<OrgDetail> {
  const res = await fetch(orgPath(orgId, "/"), {
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
  credits_balance: number;
  credits_unlimited: boolean;
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
  capped: boolean | null;
  tier_limit: number | null;
  total_matching: number | null;
  upgrade_to: string | null;
}

// ── Credit usage breakdown ────────────────────────────────────────────────────

export interface CreditUsageByAction {
  spent: number;
  refunded: number;
  net: number;
  transactions: number;
}

export interface CreditUsage {
  days: number;
  total_spent: number;
  total_refunded: number;
  net_spent: number;
  current_balance: number;
  by_action: Record<string, CreditUsageByAction>;
}

export async function fetchCreditUsage(days = 30): Promise<CreditUsage> {
  const res = await fetch(`/api/v1/billing/credits/usage?days=${days}`, { credentials: "include" });
  if (!res.ok) throw new Error("Failed to fetch credit usage");
  return res.json();
}

// ── AI Preview ───────────────────────────────────────────────────────────────

export interface ClaudePreviewResult {
  company_id: number;
  name: string;
  ai_score: number | null;
  ai_category: string | null;
  ai_freeform: string | null;
}

export interface ClaudePreviewOut {
  results: ClaudePreviewResult[];
  previews_used: number;
  previews_remaining: number;
}

export interface ClaudePreviewParams {
  canton?: string | null;
  min_zefix_score?: number | null;
  max_zefix_score?: number | null;
  purpose_keywords?: string | null;
  use_fixed_categories?: boolean;
}

export async function fetchClaudePreview(params: ClaudePreviewParams): Promise<ClaudePreviewOut> {
  const res = await fetch("/api/v1/scoring/claude-preview", {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(params),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error((body as { detail?: string }).detail ?? "Preview failed");
  }
  return res.json();
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

export async function toggleViewAlert(id: number, enabled: boolean): Promise<SavedView> {
  const res = await fetch(`/api/v1/views/${id}/alert`, {
    method: "PATCH",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ enabled }),
  });
  if (!res.ok) throw new Error("Failed to update alert");
  return res.json();
}

// ── Notification preferences ─────────────────────────────────────────────────

export interface NotificationPreferences {
  email_notifications: boolean;
  notif_low_credit: boolean;
  notif_export_ready: boolean;
  notif_job_failed: boolean;
  notif_saved_view: boolean;
}

export async function fetchNotificationPreferences(orgId: number): Promise<NotificationPreferences> {
  const res = await fetch(`/api/v1/orgs/${orgId}/notifications`, { credentials: "include" });
  if (!res.ok) throw new Error("Failed to fetch notification preferences");
  return res.json();
}

export async function updateNotificationPreferences(
  orgId: number,
  prefs: NotificationPreferences,
): Promise<NotificationPreferences> {
  const res = await fetch(`/api/v1/orgs/${orgId}/notifications`, {
    method: "PATCH",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(prefs),
  });
  if (!res.ok) throw new Error("Failed to update notification preferences");
  return res.json();
}

export async function fetchMyNotificationPreferences(orgId: number): Promise<NotificationPreferences> {
  const res = await fetch(`/api/v1/orgs/${orgId}/my-notifications`, { credentials: "include" });
  if (!res.ok) throw new Error("Failed to fetch notification preferences");
  return res.json();
}

export async function updateMyNotificationPreferences(
  orgId: number,
  prefs: NotificationPreferences,
): Promise<NotificationPreferences> {
  const res = await fetch(`/api/v1/orgs/${orgId}/my-notifications`, {
    method: "PATCH",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(prefs),
  });
  if (!res.ok) throw new Error("Failed to update notification preferences");
  return res.json();
}

// ── Admin analytics ───────────────────────────────────────────────────────────

export interface AdminAnalytics {
  total_orgs: number;
  new_orgs_30d: number;
  orgs_by_tier: Record<string, number>;
  mrr_estimate_chf: number;
  top_credit_consumers: Array<{
    org_id: number;
    org_name: string;
    net_spend_credits: number;
    net_spend_chf: number;
  }>;
  job_volume_by_type: Record<string, number>;
  total_companies_db: number;
  active_orgs_30d: number;
}

export async function fetchAdminAnalytics(): Promise<AdminAnalytics> {
  const res = await fetch("/api/v1/admin/analytics", { credentials: "include" });
  if (!res.ok) throw new Error("Failed to fetch analytics");
  return res.json();
}

// ── Admin activity logs ───────────────────────────────────────────────────────

export interface ActivityLogEntry {
  id: number;
  action: string;
  user_id: number | null;
  user_email: string | null;
  org_id: number | null;
  resource_type: string | null;
  resource_id: number | null;
  meta: Record<string, unknown> | null;
  ip: string | null;
  created_at: string;
}

export interface ActivityLogSummary {
  period_days: number;
  total_events: number;
  distinct_active_users: number;
  by_action: Record<string, number>;
}

export async function fetchAdminActivityLogs(params?: {
  user_id?: number;
  org_id?: number;
  action?: string;
  resource_type?: string;
  page?: number;
  page_size?: number;
}): Promise<AdminPage<ActivityLogEntry>> {
  const url = buildUrl("/api/v1/admin/activity-logs", params as Record<string, string | number | undefined | null>);
  const res = await fetch(url, { credentials: "include" });
  if (!res.ok) throw new Error("Failed to fetch activity logs");
  return res.json();
}

export async function fetchAdminActivitySummary(days = 30): Promise<ActivityLogSummary> {
  const res = await fetch(`/api/v1/admin/activity-logs/summary?days=${days}`, { credentials: "include" });
  if (!res.ok) throw new Error("Failed to fetch activity summary");
  return res.json();
}

// ── Admin credit transactions ─────────────────────────────────────────────────

export interface AdminCreditTransaction {
  id: number;
  org_id: number | null;
  org_name: string | null;
  amount: number;
  type: string;
  action_type: string | null;
  reference_id: string | null;
  credits_before: number | null;
  credits_after: number | null;
  expires_at: string | null;
  created_at: string;
}

export async function fetchAdminCreditTransactions(params?: {
  org_id?: number;
  tx_type?: string;
  action_type?: string;
  page?: number;
  page_size?: number;
}): Promise<AdminPage<AdminCreditTransaction>> {
  const url = buildUrl("/api/v1/admin/credit-transactions", params as Record<string, string | number | undefined | null>);
  const res = await fetch(url, { credentials: "include" });
  if (!res.ok) throw new Error("Failed to fetch credit transactions");
  return res.json();
}

// ── Admin: API Key Management ─────────────────────────────────────────────────

export interface PlatformApiKeyStatus {
  provider: string;
  keyFingerprint: string | null;
  status: "valid" | "invalid" | "unconfigured";
  lastUpdated: string | null;
  tokenUsageMonth: number;
  costMonth: number;
}

export interface OrgApiKeyConfig {
  orgId: number;
  orgName: string;
  hasBYOFeature: boolean;
  hasCustomKey: boolean;
  keyFingerprint: string | null;
  lastUpdated: string | null;
  monthlyTokens: number;
  monthlyCost: number;
  estimatedTokens: number;
}

export interface FunctionOverride {
  functionId: string;
  functionName: string;
  provider: "platform" | "custom";
  keyFingerprint: string | null;
  status: "active" | "inactive";
}

export interface StandardKeyInfo {
  provider: string;
  has_key: boolean;
  keyFingerprint: string | null;
  providerName: string;
  defaultModel: string;
}

export async function fetchAdminPlatformApiKey(): Promise<PlatformApiKeyStatus> {
  const res = await fetch("/api/v1/admin/api-keys/platform", { credentials: "include" });
  if (!res.ok) throw new Error("Failed to fetch platform API key status");
  return res.json();
}

export async function updateAdminPlatformApiKey(apiKey: string): Promise<void> {
  const res = await fetch("/api/v1/admin/api-keys/platform", {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ apiKey }),
  });
  if (!res.ok) throw new Error("Failed to update platform API key");
}

export async function fetchAdminOrgApiKeys(): Promise<OrgApiKeyConfig[]> {
  const res = await fetch("/api/v1/admin/api-keys/orgs", { credentials: "include" });
  if (!res.ok) throw new Error("Failed to fetch org API keys");
  return res.json();
}

export async function setAdminOrgApiKey(orgId: number, apiKey: string | null): Promise<void> {
  const res = await fetch(`/api/v1/admin/api-keys/orgs/${orgId}/key`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ orgId, apiKey }),
  });
  if (!res.ok) throw new Error("Failed to set org API key");
}

export async function fetchAdminFunctionOverrides(): Promise<FunctionOverride[]> {
  const res = await fetch("/api/v1/admin/api-keys/functions", { credentials: "include" });
  if (!res.ok) throw new Error("Failed to fetch function overrides");
  return res.json();
}

export async function fetchAdminStandardKeys(): Promise<Record<string, StandardKeyInfo>> {
  const res = await fetch("/api/v1/admin/api-keys/standard-keys", { credentials: "include" });
  if (!res.ok) throw new Error("Failed to fetch standard keys");
  const data = await res.json();
  return data.standard_keys;
}

export async function setAdminStandardKey(provider: string, apiKey: string): Promise<void> {
  const res = await fetch(`/api/v1/admin/api-keys/standard-keys/${provider}`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ apiKey }),
  });
  if (!res.ok) throw new Error(`Failed to set standard key for ${provider}`);
}

export async function resetAdminStandardKey(provider: string): Promise<void> {
  const res = await fetch(`/api/v1/admin/api-keys/standard-keys/${provider}`, {
    method: "DELETE",
    credentials: "include",
  });
  if (!res.ok) throw new Error(`Failed to reset standard key for ${provider}`);
}

export interface SearchProviderKeyInfo {
  provider: string;
  providerName: string;
  hasDbKey: boolean;
  hasEnvKey: boolean;
  keyFingerprint: string | null;
  source: "db" | "env" | "none";
}

export async function fetchAdminSearchProviderKeys(): Promise<Record<string, SearchProviderKeyInfo>> {
  const res = await fetch("/api/v1/admin/api-keys/search-provider-keys", { credentials: "include" });
  if (!res.ok) throw new Error("Failed to fetch search provider keys");
  const data = await res.json();
  return data.search_provider_keys;
}

export async function setAdminSearchProviderKey(provider: string, apiKey: string): Promise<void> {
  const res = await fetch(`/api/v1/admin/api-keys/search-provider-keys/${provider}`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ apiKey }),
  });
  if (!res.ok) throw new Error(`Failed to set key for ${provider}`);
}

export async function resetAdminSearchProviderKey(provider: string): Promise<void> {
  const res = await fetch(`/api/v1/admin/api-keys/search-provider-keys/${provider}`, {
    method: "DELETE",
    credentials: "include",
  });
  if (!res.ok) throw new Error(`Failed to reset key for ${provider}`);
}

// ── SOGC Persons ───────────────────────────────────────────────────────────────

import type { SogcPersonEntity, SogcPersonAppearance, SogcAuditor, SogcPersonFlag, SogcPublicationDetail, GlobalSearchResult, SogcCorporateRole } from "./types";

export async function searchPersonEntities(params: {
  q?: string;
  hometown?: string;
  confidence_level?: string;
  nationality?: string;
  min_active_companies?: number;
  is_verified?: boolean;
  is_current?: boolean;
  sort_by?: string;
  limit?: number;
  offset?: number;
}): Promise<SogcPersonEntity[]> {
  const qs = new URLSearchParams();
  if (params.q) qs.set("q", params.q);
  if (params.hometown) qs.set("hometown", params.hometown);
  if (params.confidence_level) qs.set("confidence_level", params.confidence_level);
  if (params.nationality) qs.set("nationality", params.nationality);
  if (params.min_active_companies !== undefined) qs.set("min_active_companies", String(params.min_active_companies));
  if (params.is_verified !== undefined) qs.set("is_verified", String(params.is_verified));
  if (params.is_current !== undefined) qs.set("is_current", String(params.is_current));
  if (params.sort_by) qs.set("sort_by", params.sort_by);
  if (params.limit !== undefined) qs.set("limit", String(params.limit));
  if (params.offset !== undefined) qs.set("offset", String(params.offset));
  const res = await fetch(`/api/v1/sogc/persons/search?${qs}`, { credentials: "include" });
  if (!res.ok) return _handleErrorResponse(res);
  return res.json();
}

export async function fetchPersonEntity(entityId: number): Promise<SogcPersonEntity> {
  const res = await fetch(`/api/v1/sogc/persons/${entityId}`, { credentials: "include" });
  if (!res.ok) return _handleErrorResponse(res);
  return res.json();
}

export async function fetchPersonNetwork(
  entityId: number,
  params?: { include_past?: boolean; co_director_limit?: number },
): Promise<import("./types").PersonNetworkData> {
  const qs = new URLSearchParams();
  if (params?.include_past !== undefined) qs.set("include_past", String(params.include_past));
  if (params?.co_director_limit !== undefined) qs.set("co_director_limit", String(params.co_director_limit));
  const res = await fetch(`/api/v1/sogc/persons/${entityId}/network?${qs}`, { credentials: "include" });
  if (!res.ok) return _handleErrorResponse(res);
  return res.json();
}

export async function fetchPersonAppearances(entityId: number, is_current?: boolean): Promise<SogcPersonAppearance[]> {
  const qs = is_current !== undefined ? `?is_current=${is_current}` : "";
  const res = await fetch(`/api/v1/sogc/persons/${entityId}/appearances${qs}`, { credentials: "include" });
  if (!res.ok) return _handleErrorResponse(res);
  return res.json();
}

export async function reportPersonFlag(
  entityId: number,
  body: { flag_type: string; secondary_entity_id?: number | null; appearance_id?: number | null; reason?: string | null }
): Promise<SogcPersonFlag> {
  const res = await fetch(`/api/v1/sogc/persons/${entityId}/flag`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) return _handleErrorResponse(res);
  return res.json();
}

export async function searchAuditors(params: {
  q?: string;
  location?: string;
  legal_form?: string;
  is_current?: boolean;
  limit?: number;
  offset?: number;
}): Promise<SogcAuditor[]> {
  const qs = new URLSearchParams();
  if (params.q) qs.set("q", params.q);
  if (params.location) qs.set("location", params.location);
  if (params.legal_form) qs.set("legal_form", params.legal_form);
  if (params.is_current !== undefined) qs.set("is_current", String(params.is_current));
  if (params.limit !== undefined) qs.set("limit", String(params.limit));
  if (params.offset !== undefined) qs.set("offset", String(params.offset));
  const res = await fetch(`/api/v1/sogc/auditors/search?${qs}`, { credentials: "include" });
  if (!res.ok) return _handleErrorResponse(res);
  return res.json();
}

export async function globalSearch(q: string): Promise<GlobalSearchResult> {
  const res = await fetch(`/api/v1/search/global?q=${encodeURIComponent(q)}&limit=6`, { credentials: "include" });
  if (!res.ok) return _handleErrorResponse(res);
  return res.json();
}

export async function fetchCompanyPersons(companyId: number, is_current?: boolean): Promise<SogcPersonAppearance[]> {
  const qs = is_current !== undefined ? `?is_current=${is_current}` : "";
  const res = await fetch(`/api/v1/companies/${companyId}/persons${qs}`, { credentials: "include" });
  if (!res.ok) return _handleErrorResponse(res);
  return res.json();
}

export async function fetchCompanyAuditors(companyId: number, is_current?: boolean): Promise<SogcAuditor[]> {
  const qs = is_current !== undefined ? `?is_current=${is_current}` : "";
  const res = await fetch(`/api/v1/companies/${companyId}/auditors${qs}`, { credentials: "include" });
  if (!res.ok) return _handleErrorResponse(res);
  return res.json();
}

export async function fetchCompanyPublications(companyId: number): Promise<SogcPublicationDetail[]> {
  const res = await fetch(`/api/v1/companies/${companyId}/publications`, { credentials: "include" });
  if (!res.ok) return _handleErrorResponse(res);
  return res.json();
}

export async function searchSogcPublications(params: {
  q?: string;
  company_uid?: string;
  sub_rubric?: string;
  change_type?: string;
  date_from?: string;
  date_to?: string;
  limit?: number;
  offset?: number;
}): Promise<SogcPublicationDetail[]> {
  const qs = new URLSearchParams();
  if (params.q) qs.set("q", params.q);
  if (params.company_uid) qs.set("company_uid", params.company_uid);
  if (params.sub_rubric) qs.set("sub_rubric", params.sub_rubric);
  if (params.change_type) qs.set("change_type", params.change_type);
  if (params.date_from) qs.set("date_from", params.date_from);
  if (params.date_to) qs.set("date_to", params.date_to);
  if (params.limit !== undefined) qs.set("limit", String(params.limit));
  if (params.offset !== undefined) qs.set("offset", String(params.offset));
  const res = await fetch(`/api/v1/sogc/publications/search?${qs}`, { credentials: "include" });
  if (!res.ok) return _handleErrorResponse(res);
  return res.json();
}

export async function searchCorporateRoles(params: {
  q?: string;
  che?: string;
  company_uid?: string;
  is_current?: boolean;
  limit?: number;
  offset?: number;
}): Promise<SogcCorporateRole[]> {
  const qs = new URLSearchParams();
  if (params.q) qs.set("q", params.q);
  if (params.che) qs.set("che", params.che);
  if (params.company_uid) qs.set("company_uid", params.company_uid);
  if (params.is_current !== undefined) qs.set("is_current", String(params.is_current));
  if (params.limit !== undefined) qs.set("limit", String(params.limit));
  if (params.offset !== undefined) qs.set("offset", String(params.offset));
  const res = await fetch(`/api/v1/sogc/corporate-roles/search?${qs}`, { credentials: "include" });
  if (!res.ok) return _handleErrorResponse(res);
  return res.json();
}

export async function fetchCompanyCorporateRoles(companyId: number, is_current?: boolean): Promise<SogcCorporateRole[]> {
  const qs = is_current !== undefined ? `?is_current=${is_current}` : "";
  const res = await fetch(`/api/v1/companies/${companyId}/corporate-roles${qs}`, { credentials: "include" });
  if (!res.ok) return _handleErrorResponse(res);
  return res.json();
}
