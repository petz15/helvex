"""Superadmin API key management routes."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

from app import crud
from app.auth import require_superadmin
from app.database import get_db
from app.models.organization import Organization

router = APIRouter(prefix="/api-keys", tags=["admin"])


# ── Schemas ────────────────────────────────────────────────────────────────

class ApiKeyFingerprint(BaseModel):
    """Redacted API key fingerprint (first 8 + last 4 chars)."""
    fingerprint: str = Field(..., description="First 8 and last 4 chars of key")
    lastUpdated: str | None = Field(None, description="ISO timestamp")


class PlatformApiKeyResponse(BaseModel):
    """Platform-wide Claude API key status."""
    provider: str = "claude"
    keyFingerprint: str | None = Field(None, description="Masked key identifier")
    status: str = Field(..., description="valid | invalid | unconfigured")
    lastUpdated: str | None = None
    tokenUsageMonth: int = Field(0, description="Approximate tokens used this month")
    costMonth: float = Field(0.0, description="Estimated cost this month")


class OrgApiKeyConfig(BaseModel):
    """Per-org API key configuration."""
    orgId: int
    orgName: str
    hasBYOFeature: bool = Field(..., description="Is byo_llm_keys feature enabled?")
    hasCustomKey: bool = Field(..., description="Does org have a custom key set?")
    keyFingerprint: str | None = None
    lastUpdated: str | None = None
    monthlyTokens: int = 0
    monthlyCost: float = 0.0
    estimatedTokens: int = 0


class FunctionOverride(BaseModel):
    """Function-level API key override."""
    functionId: str = Field(..., description="e.g., claude_classify, noga")
    functionName: str
    provider: str = Field(..., description="platform | custom")
    keyFingerprint: str | None = None
    status: str = Field(..., description="active | inactive")


class UpdatePlatformKeyRequest(BaseModel):
    """Request to update platform API key."""
    apiKey: str = Field(..., min_length=20, description="Raw API key (sk-proj-*)")


class UpdateOrgKeyRequest(BaseModel):
    """Request to update org API key."""
    orgId: int
    apiKey: str | None = Field(None, description="Raw API key or null to reset")


class FunctionOverrideRequest(BaseModel):
    """Request to set function-level override."""
    functionId: str
    provider: str = Field(..., description="platform | custom")
    apiKey: str | None = Field(None, description="Only needed if provider=custom")


# ── Helpers ────────────────────────────────────────────────────────────────

def _key_fingerprint(key: str | None) -> str | None:
    """Return first 8 + last 4 chars of key."""
    if not key or len(key) < 12:
        return None
    return f"{key[:8]}...{key[-4:]}"


# ── Routes ─────────────────────────────────────────────────────────────────

@router.get("/platform", response_model=PlatformApiKeyResponse, dependencies=[Depends(require_superadmin)])
async def get_platform_api_key_status(db: Session = Depends(get_db)) -> PlatformApiKeyResponse:
    """Get platform-wide Claude API key status. DB value takes priority over env."""
    from app.config import settings as app_settings

    db_key = (crud.get_setting(db, "anthropic_api_key", "") or "").strip()
    env_key = (app_settings.anthropic_api_key or "").strip()
    key = db_key or env_key
    key_status = "valid" if key else "unconfigured"
    return PlatformApiKeyResponse(
        keyFingerprint=_key_fingerprint(key),
        status=key_status,
        tokenUsageMonth=0,
        costMonth=0.0,
    )


@router.post("/platform", dependencies=[Depends(require_superadmin)])
async def update_platform_api_key(
    request: UpdatePlatformKeyRequest,
    db: Session = Depends(get_db),
) -> dict:
    """Store the platform-wide Anthropic API key in the DB (overrides .env value)."""
    crud.set_setting(db, "anthropic_api_key", request.apiKey.strip())
    logger.info("admin.update_platform_api_key fingerprint=%s", _key_fingerprint(request.apiKey))
    return {"message": "Platform API key updated", "status": "valid", "fingerprint": _key_fingerprint(request.apiKey)}


@router.get("/orgs", response_model=list[OrgApiKeyConfig], dependencies=[Depends(require_superadmin)])
async def list_org_api_keys(db: Session = Depends(get_db)) -> list[OrgApiKeyConfig]:
    """List all organizations and their API key configuration."""
    orgs = db.query(Organization).all()
    result = []
    for org in orgs:
        has_custom = bool(
            crud.get_effective_setting(db, "anthropic_api_key", org_id=org.id, default="")
        )
        from app.services.tiers import has_feature
        has_byo = has_feature(org, "byo_llm_keys")
        result.append(
            OrgApiKeyConfig(
                orgId=org.id,
                orgName=org.name,
                hasBYOFeature=has_byo,
                hasCustomKey=has_custom,
                keyFingerprint=_key_fingerprint(
                    crud.get_effective_setting(db, "anthropic_api_key", org_id=org.id, default="")
                ) if has_custom else None,
                lastUpdated=None,
                monthlyTokens=0,
                monthlyCost=0.0,
                estimatedTokens=0,
            )
        )
    return result


@router.post("/orgs/{org_id}/key", dependencies=[Depends(require_superadmin)])
async def set_org_api_key(
    org_id: int,
    request: UpdateOrgKeyRequest,
    db: Session = Depends(get_db),
) -> dict:
    """Set or reset custom API key for an organization."""
    org = db.get(Organization, org_id)
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")

    from app.services.tiers import has_feature
    if not has_feature(org, "byo_llm_keys"):
        raise HTTPException(
            status_code=403,
            detail="Organization does not have byo_llm_keys feature",
        )

    if request.apiKey:
        crud.upsert_app_setting(
            db,
            key="anthropic_api_key",
            value=request.apiKey,
            org_id=org_id,
        )
        return {"message": f"API key set for {org.name}", "fingerprint": _key_fingerprint(request.apiKey)}
    else:
        existing = crud.get_app_setting_row(db, "anthropic_api_key", org_id=org_id)
        if existing:
            db.delete(existing)
            db.commit()
        return {"message": f"API key reset for {org.name} (using platform key)"}


@router.get("/functions", response_model=list[FunctionOverride], dependencies=[Depends(require_superadmin)])
async def list_function_overrides(db: Session = Depends(get_db)) -> list[FunctionOverride]:
    """List function-level API key overrides."""
    # Define available functions
    functions = [
        {"id": "claude_classify", "name": "Company Classification"},
        {"id": "noga", "name": "NOGA Clustering"},
        {"id": "stopword_discovery", "name": "Stopword Discovery"},
    ]
    result = []
    for fn in functions:
        override = crud.get_effective_setting(
            db, f"api_key_override_{fn['id']}", default=""
        )
        result.append(
            FunctionOverride(
                functionId=fn["id"],
                functionName=fn["name"],
                provider="custom" if override else "platform",
                keyFingerprint=_key_fingerprint(override) if override else None,
                status="active",
            )
        )
    return result


@router.post("/functions/{function_id}/override", dependencies=[Depends(require_superadmin)])
async def set_function_override(
    function_id: str,
    request: FunctionOverrideRequest,
    db: Session = Depends(get_db),
) -> dict:
    """Set a function-level API key override."""
    if request.provider == "custom" and not request.apiKey:
        raise HTTPException(
            status_code=400,
            detail="apiKey required when provider=custom",
        )
    if request.provider == "platform":
        crud.delete_app_setting_by_key(db, f"api_key_override_{function_id}")
        return {"message": f"{function_id} reset to platform key"}
    else:
        crud.upsert_app_setting(
            db,
            key=f"api_key_override_{function_id}",
            value=request.apiKey,
        )
        return {
            "message": f"{function_id} now uses custom key",
            "fingerprint": _key_fingerprint(request.apiKey),
        }


@router.get("/standard-keys", response_model=dict, dependencies=[Depends(require_superadmin)])
async def list_standard_keys(db: Session = Depends(get_db)) -> dict:
    """List all configured standard API keys per provider.

    Standard keys are superadmin-configured fallback keys used when:
    - Org doesn't have a BYO key, AND
    - Platform key from .env isn't set
    """
    from app.services.llm import PROVIDER_INFO

    standard_keys = {}
    for provider in PROVIDER_INFO.keys():
        key_setting = crud.get_effective_setting(
            db, f"standard_{provider}_api_key", default=""
        ) or ""
        standard_keys[provider] = {
            "provider": provider,
            "has_key": bool(key_setting),
            "keyFingerprint": _key_fingerprint(key_setting) if key_setting else None,
            "providerName": PROVIDER_INFO[provider]["name"],
            "defaultModel": PROVIDER_INFO[provider]["default_model"],
        }
    return {"standard_keys": standard_keys}


@router.post("/standard-keys/{provider}", dependencies=[Depends(require_superadmin)])
async def set_standard_key(
    provider: str,
    body: UpdatePlatformKeyRequest,
    db: Session = Depends(get_db),
) -> dict:
    """Set the standard (fallback) API key for a provider.

    This key is used when:
    - Org has no BYO key, AND
    - Platform key from .env isn't configured

    Provides a middle ground between platform-wide and per-org keys.
    """
    from app.services.llm import PROVIDER_INFO

    if provider not in PROVIDER_INFO:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown provider. Must be one of: {', '.join(PROVIDER_INFO.keys())}",
        )

    crud.upsert_app_setting(
        db,
        key=f"standard_{provider}_api_key",
        value=body.apiKey,
    )

    logger.info("admin.set_standard_key provider=%s fingerprint=%s", provider, _key_fingerprint(body.apiKey))
    return {
        "message": f"Standard {provider} API key updated",
        "provider": provider,
        "fingerprint": _key_fingerprint(body.apiKey),
    }


@router.delete("/standard-keys/{provider}", dependencies=[Depends(require_superadmin)])
async def reset_standard_key(
    provider: str,
    db: Session = Depends(get_db),
) -> dict:
    """Reset the standard API key for a provider.

    After reset, orgs will fall through to platform key from .env.
    """
    from app.services.llm import PROVIDER_INFO

    if provider not in PROVIDER_INFO:
        raise HTTPException(status_code=400, detail=f"Unknown provider: {provider}")

    existing = crud.get_app_setting_row(db, f"standard_{provider}_api_key")
    if existing:
        db.delete(existing)
        db.commit()

    logger.info("admin.reset_standard_key provider=%s", provider)
    return {
        "message": f"Standard {provider} API key reset. Orgs now use platform key.",
        "provider": provider,
    }


_SEARCH_PROVIDERS = {
    "serper": {"name": "Serper.dev", "env_attr": "serper_api_key"},
    "scrapingdog": {"name": "ScrapingDog", "env_attr": "scrapingdog_api_key"},
}


@router.get("/search-provider-keys", dependencies=[Depends(require_superadmin)])
async def list_search_provider_keys(db: Session = Depends(get_db)) -> dict:
    """List configured search provider API keys (Serper.dev, ScrapingDog)."""
    from app.config import settings as app_settings

    result = {}
    for provider, info in _SEARCH_PROVIDERS.items():
        db_key = crud.get_setting(db, f"{provider}_api_key", "") or ""
        env_key = getattr(app_settings, info["env_attr"], "") or ""
        active_key = db_key or env_key
        result[provider] = {
            "provider": provider,
            "providerName": info["name"],
            "hasDbKey": bool(db_key),
            "hasEnvKey": bool(env_key),
            "keyFingerprint": _key_fingerprint(active_key) if active_key else None,
            "source": "db" if db_key else ("env" if env_key else "none"),
        }
    return {"search_provider_keys": result}


@router.post("/search-provider-keys/{provider}", dependencies=[Depends(require_superadmin)])
async def set_search_provider_key(
    provider: str,
    body: UpdatePlatformKeyRequest,
    db: Session = Depends(get_db),
) -> dict:
    """Set the API key for a search provider (stored in DB, overrides .env)."""
    if provider not in _SEARCH_PROVIDERS:
        raise HTTPException(status_code=400, detail=f"Unknown provider. Must be one of: {', '.join(_SEARCH_PROVIDERS)}")

    crud.set_setting(db, f"{provider}_api_key", body.apiKey.strip())
    logger.info("admin.set_search_provider_key provider=%s", provider)
    return {
        "message": f"{_SEARCH_PROVIDERS[provider]['name']} API key updated",
        "provider": provider,
        "fingerprint": _key_fingerprint(body.apiKey),
    }


@router.delete("/search-provider-keys/{provider}", dependencies=[Depends(require_superadmin)])
async def reset_search_provider_key(
    provider: str,
    db: Session = Depends(get_db),
) -> dict:
    """Remove the DB override for a search provider key (falls back to .env)."""
    if provider not in _SEARCH_PROVIDERS:
        raise HTTPException(status_code=400, detail=f"Unknown provider: {provider}")

    from app.models.app_setting import AppSetting
    row = db.get(AppSetting, f"{provider}_api_key")
    if row:
        db.delete(row)
        db.commit()

    logger.info("admin.reset_search_provider_key provider=%s", provider)
    return {"message": f"{_SEARCH_PROVIDERS[provider]['name']} DB key removed (falling back to .env)", "provider": provider}


@router.get("/audit-log", dependencies=[Depends(require_superadmin)])
async def get_audit_log(db: Session = Depends(get_db), limit: int = 50) -> dict:
    """Get audit log of API key changes. Not yet implemented — returns empty list."""
    return {"logs": [], "total": 0}
