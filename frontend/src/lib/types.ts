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
  website_match_score: number | null;
  zefix_score: number | null;
  zefix_scored_at: string | null;
  claude_score: number | null;
  claude_scored_at: string | null;
  claude_category: string | null;
  claude_freeform: string | null;
  combined_score: number | null;
  review_status: string | null;
  proposal_status: string | null;
  contact_name: string | null;
  contact_email: string | null;
  contact_phone: string | null;
  tags: string | null;
  purpose_keywords: string | null;
  tfidf_cluster: string | null;
  capital_nominal: string | null;
  capital_currency: string | null;
  cantonal_excerpt_web: string | null;
  old_names: string | null;
  lat: number | null;
  lon: number | null;
  created_at: string;
  updated_at: string;
  notes: Note[];
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
  proposal: Record<string, number>;
}

export interface CompanyFilters {
  q?: string;
  canton?: string;
  review_status?: string;
  proposal_status?: string;
  google_searched?: string;
  min_google_score?: number;
  min_zefix_score?: number;
  min_claude_score?: number;
  claude_category?: string;
  tags?: string;
  tfidf_cluster?: string;
  purpose_keywords?: string;
  exclude_tags?: string;
  exclude_review_status?: string;
  exclude_canton?: string;
  exclude_proposal_status?: string;
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

export const PROPOSAL_STATUSES = [
  { value: "sent", label: "Sent", color: "yellow" },
  { value: "responded", label: "Responded", color: "blue" },
  { value: "converted", label: "Converted", color: "green" },
  { value: "rejected", label: "Rejected", color: "red" },
] as const;

export interface Job {
  id: number;
  job_type: string;
  label: string;
  status: "queued" | "running" | "paused" | "completed" | "failed" | "cancelled";
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
}

export interface MapFeature {
  id: number;
  name: string;
  lat: number;
  lon: number;
  google_score: number | null;
  zefix_score: number | null;
  claude_score: number | null;
  canton: string | null;
  municipality: string | null;
  website: string | null;
  review: string | null;
  status: string | null;
}
