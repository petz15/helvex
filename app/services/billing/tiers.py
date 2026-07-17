"""Tier feature-gate service.

Single source of truth for what each org tier can do.  All feature checks,
limits, discounts, and queue routing should go through this module.

Tier names: free | simple | explorer | researcher | strategist | custom
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.models.billing_tier import BillingTier
from app.models.organization import Organization
from app.models.user import User

if TYPE_CHECKING:
    pass


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Canonical tier names in ascending privilege order.
TIER_NAMES = ["free", "simple", "explorer", "researcher", "strategist", "custom", "superadmin"]

#: Integer rank per tier (higher = more privileged).
TIER_RANK: dict[str, int] = {name: idx for idx, name in enumerate(TIER_NAMES)}

#: DB integer → canonical tier name (excludes "superadmin" which is not stored in DB).
TIER_NAME_BY_ID: dict[int, str] = {
    0: "free",
    1: "simple",
    2: "explorer",
    3: "researcher",
    4: "strategist",
    5: "custom",
}

#: Canonical tier name → DB integer (reverse of TIER_NAME_BY_ID).
TIER_ID_BY_NAME: dict[str, int] = {v: k for k, v in TIER_NAME_BY_ID.items()}

DEFAULT_BILLING_TIERS: list[dict[str, object]] = [
    {
        "id": 0,
        "slug": "free",
        "display_name": "Free",
        "description": "Try the core search and export features at no cost.",
        "monthly_price_chf": 0.0,
        "yearly_multiplier": 10.0,
        "topup_bonus_rate": 0.0,
        "sort_order": 0,
        "is_active": True,
        "is_public": True,
    },
    {
        "id": 1,
        "slug": "simple",
        "display_name": "Simple",
        "description": "For individuals who need a clean workspace without ads.",
        "monthly_price_chf": 6.0,
        "yearly_multiplier": 10.0,
        "topup_bonus_rate": 0.05,
        "sort_order": 1,
        "is_active": True,
        "is_public": True,
    },
    {
        "id": 2,
        "slug": "explorer",
        "display_name": "Explorer",
        "description": "Immediate LLM scoring and automated flex rescoring.",
        "monthly_price_chf": 12.0,
        "yearly_multiplier": 10.0,
        "topup_bonus_rate": 0.10,
        "sort_order": 2,
        "is_active": True,
        "is_public": True,
    },
    {
        "id": 3,
        "slug": "researcher",
        "display_name": "Researcher",
        "description": "Full LLM auto-scoring and custom ML stopwords.",
        "monthly_price_chf": 17.0,
        "yearly_multiplier": 10.0,
        "topup_bonus_rate": 0.15,
        "sort_order": 3,
        "is_active": True,
        "is_public": True,
    },
    {
        "id": 4,
        "slug": "strategist",
        "display_name": "Strategist",
        "description": "Highest priority, BYO keys, and future API access.",
        "monthly_price_chf": 37.0,
        "yearly_multiplier": 10.0,
        "topup_bonus_rate": 0.20,
        "sort_order": 4,
        "is_active": True,
        "is_public": True,
    },
    {
        "id": 5,
        "slug": "custom",
        "display_name": "Custom",
        "description": "Build a modular plan with exactly the features you need.",
        "monthly_price_chf": 1.0,
        "yearly_multiplier": 10.0,
        "topup_bonus_rate": 0.0,
        "sort_order": 5,
        "is_active": True,
        "is_public": False,
    },
]


def _default_billing_tier_map() -> dict[str, dict[str, object]]:
    return {str(row["slug"]): row for row in DEFAULT_BILLING_TIERS}


def ensure_default_billing_tiers(db: Session) -> None:
    if db.query(BillingTier).count() > 0:
        return
    for row in DEFAULT_BILLING_TIERS:
        db.add(BillingTier(**row))
    db.commit()


def get_billing_tiers(db: Session, *, include_inactive: bool = False) -> list[BillingTier]:
    ensure_default_billing_tiers(db)
    query = db.query(BillingTier)
    if not include_inactive:
        query = query.filter(BillingTier.is_active.is_(True))
    return query.order_by(BillingTier.sort_order.asc(), BillingTier.id.asc()).all()


def get_billing_tier_by_slug(db: Session, slug: str) -> BillingTier | None:
    ensure_default_billing_tiers(db)
    return db.query(BillingTier).filter(BillingTier.slug == slug).first()


def get_billing_tier_names(db: Session, *, include_custom: bool = True) -> list[str]:
    tiers = get_billing_tiers(db)
    names = [tier.slug for tier in tiers if include_custom or tier.slug != "custom"]
    return names


def get_user_tier_names(db: Session) -> list[str]:
    return get_billing_tier_names(db) + ["superadmin"]


def get_tier_display_name(db: Session, slug: str) -> str:
    tier = get_billing_tier_by_slug(db, slug)
    if tier:
        return tier.display_name
    return _default_billing_tier_map().get(slug, {}).get("display_name", slug.title())  # type: ignore[return-value]


def get_tier_price_chf(db: Session, slug: str) -> float:
    tier = get_billing_tier_by_slug(db, slug)
    if tier:
        return float(tier.monthly_price_chf)
    default = _default_billing_tier_map().get(slug)
    if default:
        return float(default.get("monthly_price_chf", 0.0))
    return 0.0


def get_tier_bonus_rate(db: Session, slug: str) -> float:
    tier = get_billing_tier_by_slug(db, slug)
    if tier:
        return float(tier.topup_bonus_rate)
    default = _default_billing_tier_map().get(slug)
    if default:
        return float(default.get("topup_bonus_rate", 0.0))
    return 0.0


def get_tier_yearly_price_chf(db: Session, slug: str) -> float:
    tier = get_billing_tier_by_slug(db, slug)
    if tier:
        return round(float(tier.monthly_price_chf) * float(tier.yearly_multiplier), 2)
    default = _default_billing_tier_map().get(slug)
    if default:
        return round(float(default.get("monthly_price_chf", 0.0)) * float(default.get("yearly_multiplier", 10.0)), 2)
    return 0.0

#: Legacy DB values → new names (for rows not yet data-migrated).
_LEGACY_TIER_MAP: dict[str, str] = {
    "pro": "simple",
    "team": "explorer",
    "enterprise": "strategist",
    # admin.py used these names before alignment
    "starter": "simple",
    "professional": "explorer",
}

# Feature flags enabled per fixed tier (cumulative — each tier inherits all
# features of the tiers below it).
_TIER_FEATURES: dict[str, frozenset[str]] = {
    "free": frozenset(),
    "simple": frozenset({"no_ads", "multi_user", "daily_notifications", "web_search"}),
    "explorer": frozenset({
        "no_ads", "multi_user", "daily_notifications", "web_search",
        "immediate_llm", "flex_auto_score_new",
    }),
    "researcher": frozenset({
        "no_ads", "multi_user", "daily_notifications", "web_search",
        "immediate_llm", "flex_auto_score_new",
        "llm_auto_score_new", "custom_ml_stopwords",
    }),
    "strategist": frozenset({
        "no_ads", "multi_user", "daily_notifications", "web_search",
        "immediate_llm", "flex_auto_score_new",
        "llm_auto_score_new", "custom_ml_stopwords",
        "byo_llm_keys", "api_access",
    }),
    # "custom" tier has no fixed feature set — entirely driven by custom_features JSONB
    "custom": frozenset(),
    # "superadmin" tier has all features and unlimited rights
    "superadmin": frozenset({
        "no_ads", "multi_user", "daily_notifications", "web_search",
        "immediate_llm", "flex_auto_score_new",
        "llm_auto_score_new", "custom_ml_stopwords",
        "byo_llm_keys", "api_access",
    }),
}

# Custom-tier JSONB key → feature name mapping
_CUSTOM_FEATURE_KEYS: dict[str, str] = {
    "no_ads": "no_ads",
    "multi_user": "multi_user",
    "daily_notifications": "daily_notifications",
    "immediate_llm": "immediate_llm",
    "flex_auto_score": "flex_auto_score_new",
    "llm_auto_score": "llm_auto_score_new",
    "custom_ml_stopwords": "custom_ml_stopwords",
    "byo_llm_keys": "byo_llm_keys",
    "api_access": "api_access",
}

#: Max CSV-export rows per tier.
EXPORT_LIMITS: dict[str, int] = {
    "free": 100,
    "simple": 1_000,
    "explorer": 5_000,
    "researcher": 20_000,
    "strategist": 100_000,
    # custom: 100k if export_100k feature purchased, else 100 (base)
    "superadmin": 999_999_999_999,  # unlimited
}

#: RQ queue priority (0 = lowest, 5 = highest).
#  Workers listen to all queues in descending order so higher-priority
#  jobs are always processed first when a worker is free.
QUEUE_PRIORITY: dict[str, int] = {
    "free": 0,
    "simple": 1,
    "explorer": 2,
    "researcher": 3,
    "strategist": 4,
    "superadmin": 5,  # highest priority
    # custom: driven by custom_features["priority_level"] (0–4)
}

#: Bonus credit rate applied when topping up credits (not at deduction time).
#: E.g. simple=0.10 means buying 10,000 credits grants 1,000 bonus credits on top.
BONUS_RATE: dict[str, float] = {
    "free": 0.0,
    "simple": 0.05,
    "explorer": 0.10,
    "researcher": 0.15,
    "strategist": 0.20,
    "superadmin": 0.0,  # superadmin orgs use credits_unlimited instead
    # custom: bonus_steps * 0.05, capped at 0.40
}

#: Web-results privacy duration in months per tier.
#  Strategist gets a very large value while active; the actual "tier_held + 6m"
#  grace period after downgrade is enforced separately via subscription_period_end.
WEB_RESULTS_PRIVATE_MONTHS: dict[str, float] = {
    "free": 0.0,
    "simple": 0.25,   # ≈ 1 week
    "explorer": 1.0,
    "researcher": 3.0,
    "strategist": 999.0,   # effectively indefinite while active
    "superadmin": 999.0,   # effectively indefinite
    # custom: custom_features["web_results_months"]
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def normalize_tier(raw_tier: "str | int") -> str:
    """Resolve a tier value (integer ID or string name) to its canonical name."""
    if isinstance(raw_tier, int):
        return TIER_NAME_BY_ID.get(raw_tier, "free")
    return _LEGACY_TIER_MAP.get(raw_tier, raw_tier)


def get_tier_rank(org: Organization) -> int:
    """Return the integer rank for an org's tier."""
    tier = normalize_tier(org.tier)
    return TIER_RANK.get(tier, 0)


# ---------------------------------------------------------------------------
# Feature gates
# ---------------------------------------------------------------------------

def has_feature(org: Organization, feature: str) -> bool:
    """Return True if the org's tier (or custom feature bundle) includes *feature*.

    For the ``custom`` tier, feature presence is determined by the
    ``custom_features`` JSONB column.  For all other tiers, the fixed
    ``_TIER_FEATURES`` lookup is used.
    """
    tier = normalize_tier(org.tier)
    if tier != "custom":
        return feature in _TIER_FEATURES.get(tier, frozenset())

    # Custom tier — read JSONB feature bundle
    features: dict = org.custom_features or {}
    # Try direct key match first (e.g. "no_ads": True)
    if feature in features and features[feature]:
        return True
    # Try inverse mapping (e.g. feature = "flex_auto_score_new", key = "flex_auto_score")
    for jsonb_key, feat_name in _CUSTOM_FEATURE_KEYS.items():
        if feat_name == feature and features.get(jsonb_key):
            return True
    return False


# ---------------------------------------------------------------------------
# Limits and routing
# ---------------------------------------------------------------------------

def get_export_limit(org: Organization) -> int:
    """Max rows for a CSV export job."""
    tier = normalize_tier(org.tier)
    if tier == "custom":
        features: dict = org.custom_features or {}
        return 100_000 if features.get("export_100k") else 100
    return EXPORT_LIMITS.get(tier, 100)


def get_queue_priority(org: Organization) -> int:
    """RQ queue priority level (0–4). Used as suffix in queue name."""
    tier = normalize_tier(org.tier)
    if tier == "custom":
        features: dict = org.custom_features or {}
        raw = features.get("priority_level", 0)
        return max(0, min(4, int(raw)))
    return QUEUE_PRIORITY.get(tier, 0)


def get_topup_bonus_rate(org: Organization) -> float:
    """Bonus credit rate for this org's tier, applied when topping up credits.

    E.g. 0.15 means buying 10,000 credits also grants 1,500 bonus credits.
    Range: [0.0, 0.40] for fixed tiers; up to 0.40 for custom (8 bonus steps).
    """
    tier = normalize_tier(org.tier)
    if tier == "custom":
        features: dict = org.custom_features or {}
        steps = max(0, min(8, int(features.get("bonus_steps", 0))))  # max 40%
        return steps * 0.05
    return BONUS_RATE.get(tier, 0.0)


def get_web_results_privacy_months(org: Organization) -> float:
    """Number of months web-search results are kept private for this org."""
    tier = normalize_tier(org.tier)
    if tier == "custom":
        features: dict = org.custom_features or {}
        return float(features.get("web_results_months", 0))
    # If the org has an explicit override stored, honour it (used after downgrade
    # to preserve the "strategist + 6 months" grace period).
    if org.web_results_private_months > 0 and tier not in ("strategist",):
        return org.web_results_private_months
    return WEB_RESULTS_PRIVATE_MONTHS.get(tier, 0.0)


# ---------------------------------------------------------------------------
# Custom-tier pricing calculator
# ---------------------------------------------------------------------------

#: CHF/month cost for each purchasable feature unit.
CUSTOM_FEATURE_PRICES: dict[str, float] = {
    "base": 1.0,                  # no_ads + multi_user (required)
    "web_results_month": 1.0,     # per additional month of web-result privacy
    "export_100k": 2.0,           # unlimited CSV export
    "bonus_step": 2.0,            # per 5% topup bonus step (max 8 steps = 40%)
    "priority_level": 2.0,        # per job-queue priority level (max 4)
    "immediate_llm": 1.0,
    "byo_llm_keys": 14.0,
    "flex_auto_score": 1.0,
    "llm_auto_score": 1.0,
    "daily_notifications": 0.0,   # included in base
}


def calculate_custom_tier_price(features: dict) -> float:
    """Return the monthly CHF price for a custom-tier feature bundle.

    *features* is the dict stored in ``org.custom_features``.  Always includes
    the base charge (1 CHF) even if not explicitly set.

    Example::

        calculate_custom_tier_price({
            "web_results_months": 2,
            "export_100k": True,
            "bonus_steps": 3,
            "priority_level": 2,
            "immediate_llm": True,
            "byo_llm_keys": False,
        })
        # → 1 + 2 + 2 + 6 + 4 + 1 = 16.0 CHF/month
    """
    price = CUSTOM_FEATURE_PRICES["base"]

    web_months = max(0, int(features.get("web_results_months", 0)))
    price += web_months * CUSTOM_FEATURE_PRICES["web_results_month"]

    if features.get("export_100k"):
        price += CUSTOM_FEATURE_PRICES["export_100k"]

    bonus_steps = max(0, min(8, int(features.get("bonus_steps", 0))))
    price += bonus_steps * CUSTOM_FEATURE_PRICES["bonus_step"]

    priority_level = max(0, min(4, int(features.get("priority_level", 0))))
    price += priority_level * CUSTOM_FEATURE_PRICES["priority_level"]

    if features.get("immediate_llm"):
        price += CUSTOM_FEATURE_PRICES["immediate_llm"]

    if features.get("byo_llm_keys"):
        price += CUSTOM_FEATURE_PRICES["byo_llm_keys"]

    if features.get("flex_auto_score"):
        price += CUSTOM_FEATURE_PRICES["flex_auto_score"]

    if features.get("llm_auto_score"):
        price += CUSTOM_FEATURE_PRICES["llm_auto_score"]

    return price


# ---------------------------------------------------------------------------
# FastAPI dependency: require an org at a minimum tier
# ---------------------------------------------------------------------------

def require_org_tier(min_tier: str):  # noqa: ANN201
    """Dependency factory — raises 403 if the active org's tier is below *min_tier*.

    Superadmins always pass.

    Usage::

        @router.get("/researcher-feature")
        def endpoint(user_org=Depends(require_org_tier("researcher"))):
            user, org = user_org
            ...
    """
    required_rank = TIER_RANK.get(normalize_tier(min_tier), 0)

    from app.api.deps import get_current_org as _get_current_org

    def _check(
        user_org: tuple[User, Organization] = Depends(_get_current_org),
    ) -> tuple[User, Organization]:
        user, org = user_org
        if user.is_superadmin:
            return user_org
        org_rank = get_tier_rank(org)
        if org_rank < required_rank:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"This feature requires the '{min_tier}' tier or above.",
            )
        return user_org

    return _check
