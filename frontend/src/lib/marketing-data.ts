export type PricingTier = {
  id: "free" | "simple" | "explorer" | "researcher" | "strategist";
  name: string;
  monthly: number;
  yearly: number;
  description: string;
  color: "slate" | "blue" | "violet";
  popular: boolean;
  dark?: boolean;
};

export const PRICING_TIERS: readonly PricingTier[] = [
  {
    id: "free",
    name: "Free",
    monthly: 0,
    yearly: 0,
    description: "Try the core search and export features at no cost.",
    color: "slate",
    popular: false,
  },
  {
    id: "simple",
    name: "Simple",
    monthly: 6,
    yearly: 60,
    description: "For individuals who need a clean workspace without ads.",
    color: "slate",
    popular: false,
  },
  {
    id: "explorer",
    name: "Explorer",
    monthly: 12,
    yearly: 120,
    description: "Immediate LLM scoring and automated flex rescoring.",
    color: "blue",
    popular: true,
  },
  {
    id: "researcher",
    name: "Researcher",
    monthly: 17,
    yearly: 170,
    description: "Full LLM auto-scoring and custom ML stopwords.",
    color: "violet",
    popular: false,
  },
  {
    id: "strategist",
    name: "Strategist",
    monthly: 37,
    yearly: 370,
    description: "Highest priority, BYO keys, and future API access.",
    color: "slate",
    popular: false,
    dark: true,
  },
] as const;

export type CreditAction = {
  label: string;
  unit: string;
  base: number;
};

export const CREDIT_ACTIONS: readonly CreditAction[] = [
  {
    label: "Batch LLM classify",
    unit: "per company",
    base: 8,
  },
  {
    label: "Immediate LLM classify",
    unit: "per company",
    base: 12,
  },
  {
    label: "Web search",
    unit: "per company",
    base: 20,
  },
  {
    label: "Flex rescore",
    unit: "per company",
    base: 1,
  },
  {
    label: "Full reclustering",
    unit: "flat",
    base: 100_000,
  },
  {
    label: "Bulk export – basic (UID/Name/Canton)",
    unit: "per 10k rows",
    base: 6_000,
  },
  {
    label: "Bulk export – detailed",
    unit: "per 10k rows",
    base: 13_000,
  },
] as const;

export function creditsToChf(credits: number): string {
  return (credits * 0.0001).toFixed(credits >= 1000 ? 2 : 4);
}

export type LandingFeatureCard = {
  title: string;
  detail: string;
};

export const LANDING_FEATURE_CARDS: readonly LandingFeatureCard[] = [
  { title: "Live register sync", detail: "Swiss commercial register records stay current with automated SHAB ingestion." },
  { title: "UID-first identity", detail: "Every company profile is anchored to CHE UID for reliable joins and exports." },
  { title: "SHAB timeline", detail: "Replay mutations like name, signer, and address changes in chronological order." },
  { title: "Seat + canton data", detail: "Filter and segment by locality to target exact regions in Switzerland." },
  { title: "Purpose extraction", detail: "Company purpose text is normalized for scoring, clustering, and search." },

  { title: "AI fit score", detail: "Get a numeric relevance score to triage thousands of firms quickly." },
  { title: "LLM classify", detail: "Run batch or immediate LLM classification flows based on your speed needs." },
  { title: "Flex rescoring", detail: "Re-evaluate lists whenever your targeting criteria changes." },
  { title: "Auto-score newcomers", detail: "Newly discovered companies can be scored automatically as they enter." },
  { title: "Custom stopwords", detail: "Tune ML clustering behavior by excluding noisy or misleading terms." },

  { title: "Advanced filters", detail: "Combine status, geography, purpose, and score filters to narrow fast." },
  { title: "Collections", detail: "Organize target accounts into reusable collections for follow-up workflows." },
  { title: "Daily digests", detail: "Receive summaries of newly matched companies without manual checks." },
  { title: "Web search enrich", detail: "Pull web context for additional signal before outreach decisions." },
  { title: "Map + geodata", detail: "Use latitude/longitude and map views to inspect local clusters." },

  { title: "CSV exports", detail: "Export basic or detailed datasets for CRM import and offline analysis." },
  { title: "Credit economy", detail: "Transparent per-action credit costs with precise CHF conversion." },
  { title: "Top-up bonuses", detail: "Higher tiers receive bonus credits automatically on every purchase." },
  { title: "Org workspace", detail: "Invite teammates into a shared org with common data and balance." },
  { title: "Checkout flows", detail: "Subscription and billing flows support saved methods and return URLs." },

  { title: "No-ads tiers", detail: "Upgrade to work in a cleaner interface while researching companies." },
  { title: "Queue priority", detail: "Higher plans are processed first for faster turnaround on heavy jobs." },
  { title: "BYO LLM keys", detail: "Strategist tier can route model usage through your own provider keys." },
  { title: "API-ready architecture", detail: "Platform is structured for upcoming programmatic access." },
  { title: "Audit-friendly history", detail: "Trace scoring and data evolution with clear historical context." },
] as const;
