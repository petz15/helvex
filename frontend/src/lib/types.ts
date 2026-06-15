export interface Company {
  id: number;
  uid: string;
  name: string;
  legal_form: string | null;
  status: string | null;
  municipality: string | null;
  canton: string | null;
  purpose: string | null;
  address: string | null;
  website_url: string | null;
  website_checked_at: string | null;
  google_search_results_raw: string | null;
  web_score: number | null;
  social_media_only: boolean | null;
  flex_score: number | null;
  flex_score_breakdown: string | null;
  flex_scored_at: string | null;
  ai_score: number | null;
  ai_scored_at: string | null;
  ai_category: string | null;
  ai_freeform: string | null;
  noga_code: string | null;
  noga_label: string | null;
  noga_level: string | null;
  noga_confidence: number | null;
  noga_classified_at: string | null;
  noga_path: string | null;
  noga_path_labels: string | null;
  combined_score: number | null;
  review_status: string | null;
  contact_status: string | null;
  contact_name: string | null;
  contact_email: string | null;
  contact_phone: string | null;
  tags: string | null;
  purpose_keywords: string | null;
  tfidf_cluster: string | null;
  capital_nominal: string | null;
  capital_currency: string | null;
  cantonal_excerpt_web: string | null;
  translations: string | null;
  zefix_detail_web: string | null;
  address_city: string | null;
  address_zip: string | null;
  old_names: string | null;
  head_offices: string | null;
  further_head_offices: string | null;
  parent_company_id?: number | null;
  branch_offices: string | null;
  has_taken_over: string | null;
  was_taken_over_by: string | null;
  audit_companies: string | null;
  sogc_pub: string | null;
  sogc_date: string | null;
  first_sogc_date: string | null;
  deletion_date: string | null;
  ehraid: string | null;
  chid: string | null;
  lat: number | null;
  lon: number | null;
  business_model: string | null;
  purpose_language: string | null;
  created_at: string;
  updated_at: string;
  notes: Note[];
}

export interface GoogleScoredResult {
  title: string;
  link: string;
  snippet: string;
  score: number;
}

export interface Note {
  id: number;
  content: string;
  created_at: string;
  updated_at: string;
}

export interface CompanyPage {
  items: Company[];
  total: number;
  page: number;
  page_size: number;
  pages: number;
}

export interface CompanyStats {
  total: number;
  searched: number;
  with_website: number;
  searches_today: number;
  review: Record<string, number>;
  contact: Record<string, number>;
  score_distribution?: Record<string, number>;
}

export interface CompanyFilters {
  q?: string;
  uid?: string;
  status?: string;
  has_website?: boolean;
  active_only?: boolean;
  canton?: string;
  review_status?: string;
  contact_status?: string;
  google_searched?: string;
  min_web_score?: number;
  max_web_score?: number;
  min_flex_score?: number;
  max_flex_score?: number;
  min_ai_score?: number;
  max_ai_score?: number;
  min_combined_score?: number;
  max_combined_score?: number;
  ai_category?: string;
  tags?: string;
  tfidf_cluster?: string;
  purpose_keywords?: string;
  noga_code?: string;
  noga_label?: string;
  noga_level?: string;
  exclude_tags?: string;
  exclude_review_status?: string;
  exclude_canton?: string;
  exclude_contact_status?: string;
  exclude_tfidf_cluster?: string;
  exclude_purpose_keywords?: string;
  exclude_ai_category?: string;
  exclude_noga_code?: string;
  exclude_noga_label?: string;
  exclude_noga_level?: string;
  business_model?: string;
  purpose_language?: string;
  legal_form?: string;
  registered_after?: string;
  registered_before?: string;
  sogc_after?: string;
  sogc_before?: string;
  shab_type?: string;
  sort?: string;
  page?: number;
  page_size?: number;
}

export const REVIEW_STATUSES = [
  { value: "potential_proposal", label: "Potential proposal", color: "blue" },
  { value: "confirmed_proposal", label: "Confirmed proposal", color: "green" },
  { value: "potential_generic", label: "Potential generic", color: "blue" },
  { value: "confirmed_generic", label: "Confirmed generic", color: "green" },
  { value: "interesting", label: "Interesting", color: "yellow" },
  { value: "rejected", label: "Rejected", color: "red" },
] as const;

export const CONTACT_STATUSES = [
  { value: "sent", label: "Sent", color: "yellow" },
  { value: "responded", label: "Responded", color: "blue" },
  { value: "converted", label: "Converted", color: "green" },
  { value: "rejected", label: "Rejected", color: "red" },
] as const;

export interface Job {
  id: number;
  job_type: string;
  label: string;
  status: "queued" | "running" | "paused" | "completed" | "failed" | "cancelled" | "waiting_external";
  message: string | null;
  progress_done: number | null;
  progress_total: number | null;
  error: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
}

export interface JobEvent {
  id: number;
  job_id: number;
  level: "info" | "debug" | "warn" | "error";
  message: string;
  created_at: string;
}

export interface AppSettings {
  google_search_enabled: string;
  google_daily_quota: string;
  google_search_provider: string;
  serper_api_key: string;
  scrapingdog_api_key: string;
  scoring_target_clusters: string;
  scoring_cluster_hit_points: string;
  scoring_exclude_clusters: string;
  scoring_cluster_exclude_points: string;
  scoring_target_keywords: string;
  scoring_keyword_hit_points: string;
  scoring_exclude_keywords: string;
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
  scoring_claude_max_purpose_chars: string;
  anthropic_api_key: string;
  claude_model: string;
  claude_target_description: string;
  claude_classify_prompt: string;
  claude_classify_categories: string;
}

export interface BoilerplatePattern {
  id: number;
  pattern: string;
  description: string | null;
  example: string | null;
  active: boolean;
  truncate: boolean;
}

export interface GoogleStopword {
  id: number;
  value: string;
  description: string | null;
  active: boolean;
}

export interface GoogleDirectoryDomain {
  id: number;
  value: string;
  description: string | null;
  active: boolean;
}

export interface TfidfStopword {
  id: number;
  value: string;
  description: string | null;
  active: boolean;
}

export interface MapCluster {
  lat: number;
  lon: number;
  count: number;
  avg_score: number | null;
}

export interface MapFeature {
  id: number;
  name: string;
  lat: number;
  lon: number;
  web_score: number | null;
  flex_score: number | null;
  ai_score: number | null;
  canton: string | null;
  municipality: string | null;
  website: string | null;
  review: string | null;
  status: string | null;
}

export interface SavedView {
  id: number;
  name: string;
  filters: CompanyFilters;
  created_at: string;
  alert_enabled: boolean;
  alert_last_checked_at: string | null;
}

export interface SogcPersonEntity {
  id: number;
  normalized_key: string;
  lastname: string | null;
  firstname: string | null;
  hometown_municipality: string | null;
  current_residence_municipality: string | null;
  is_foreign: boolean;
  nationality: string | null;
  confidence_level: "high" | "medium" | "low";
  is_verified: boolean;
  verified_at: string | null;
  appearance_count: number;
  active_company_count: number;
  linkedin_url: string | null;
  linkedin_verified_at: string | null;
  merged_into_id: number | null;
  identity_notes: string | null;
  created_at: string;
  updated_at: string;
}

export interface SogcPersonAppearance {
  id: number;
  person_entity_id: number;
  entity_override_id: number | null;
  sogc_change_id: number;
  sogc_publication_id: number;
  company_uid: string | null;
  company_id: number | null;
  company_name: string | null;
  pub_date: string | null;
  change_type: string;
  change_subtype: string | null;
  role: string | null;
  role_category: "director" | "officer" | "other" | null;
  signature_type: string | null;
  bisher_role: string | null;
  residence_municipality: string | null;
  is_current: boolean | null;
  title: string | null;
  raw_excerpt: string | null;
  created_at: string;
}

export interface SogcAuditor {
  id: number;
  sogc_change_id: number;
  sogc_publication_id: number;
  company_uid: string | null;
  company_id: number | null;
  company_name: string | null;
  pub_date: string | null;
  change_type: string;
  auditor_name: string | null;
  auditor_uid: string | null;
  auditor_legal_form: string | null;
  auditor_location: string | null;
  auditor_name_normalized: string | null;
  is_current: boolean | null;
  created_at: string;
}

export interface CoDirector {
  entity_id: number;
  lastname: string | null;
  firstname: string | null;
  role: string | null;
  role_category: string | null;
  is_current: boolean | null;
  active_company_count: number;
}

export interface MandateItem {
  company_uid: string;
  company_id: number | null;
  company_name: string | null;
  role: string | null;
  role_category: string | null;
  signature_type: string | null;
  date_from: string | null;
  date_to: string | null;
  is_current: boolean | null;
  co_directors: CoDirector[];
}

export interface PersonNetworkData {
  entity: SogcPersonEntity;
  mandates: MandateItem[];
}

export interface SogcCorporateRole {
  id: number;
  sogc_change_id: number | null;
  sogc_publication_id: number | null;
  entity_name: string | null;
  entity_name_normalized: string | null;
  entity_che: string | null;
  entity_location: string | null;
  entity_legal_form: string | null;
  company_uid: string | null;
  company_id: number | null;
  company_name: string | null;
  role: string | null;
  role_details: string | null;
  raw_excerpt: string | null;
  pub_date: string | null;
  change_type: string | null;
  change_subtype: string | null;
  is_current: boolean | null;
  created_at: string;
}

export interface GlobalCompanySnippet {
  id: number;
  uid: string;
  name: string;
  canton: string | null;
  legal_form: string | null;
  status: string | null;
  matched_old_name: string | null;
}

export interface GlobalPersonSnippet {
  id: number;
  firstname: string | null;
  lastname: string | null;
  hometown_municipality: string | null;
  active_company_count: number;
  confidence_level: string;
}

export interface GlobalAuditorSnippet {
  key: string;
  name: string;
  location: string | null;
  client_count: number;
}

export interface GlobalSearchResult {
  companies: GlobalCompanySnippet[];
  persons: GlobalPersonSnippet[];
  auditors: GlobalAuditorSnippet[];
}

export interface SogcPersonFlag {
  id: number;
  flag_type: "should_merge" | "should_split";
  primary_entity_id: number;
  secondary_entity_id: number | null;
  appearance_id: number | null;
  reason: string | null;
  is_resolved: boolean;
  resolution_action: string | null;
  resolved_at: string | null;
  reported_by_user_id: number | null;
  created_at: string;
}

export interface SogcChangeDetail {
  id: number;
  change_type: string;
  change_subtype: string | null;
  keywords_matched: string | null;
  raw_excerpt: string | null;
}

export interface SogcPublicationDetail {
  id: number;
  sogc_id: string;
  company_uid: string | null;
  company_id: number | null;
  pub_date: string | null;
  sub_rubric: string | null;
  pub_number: string | null;
  text_de: string | null;
  text_fr: string | null;
  text_it: string | null;
  text_en: string | null;
  detected_language: string | null;
  preprocessed_at: string | null;
  changes: SogcChangeDetail[];
}
