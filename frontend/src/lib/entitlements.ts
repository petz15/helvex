export interface OrgEntitlementInput {
  tier: string;
  customFeatures?: Record<string, unknown> | null;
}

const EXPORT_LIMITS: Record<string, number> = {
  free: 100,
  simple: 1000,
  explorer: 5000,
  researcher: 20000,
  strategist: 100000,
};

export function creditsToChf(credits: number): number {
  return credits * 0.0001;
}

export function getExportLimit(input: OrgEntitlementInput): number {
  const tier = (input.tier || "free").toLowerCase();
  if (tier === "custom") {
    return input.customFeatures?.export_100k ? 100000 : 100;
  }
  return EXPORT_LIMITS[tier] ?? 100;
}

export function hasNoAds(input: OrgEntitlementInput): boolean {
  const tier = (input.tier || "free").toLowerCase();
  if (tier === "custom") {
    return Boolean(input.customFeatures?.no_ads);
  }
  return tier !== "free";
}
