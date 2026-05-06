"""Generic LLM dispatcher and provider management.

Provides a unified interface to call any LLM provider (Claude, OpenAI, Gemini, DeepSeek, Groq).
Routes calls to the appropriate provider implementation.

Architecture:
  - Each provider has its own module (app/services/providers/*)
  - This module provides dispatch functions
  - Superadmin configures which provider keys are available
  - Orgs can override provider keys (if byo_llm_keys feature enabled)
"""

from __future__ import annotations

import logging
from typing import Any, Literal

logger = logging.getLogger(__name__)

Provider = Literal["claude", "openai", "gemini", "deepseek", "groq"]


# ── API Key Resolution ────────────────────────────────────────────────────────

def resolve_provider_api_key(
    db: Any,
    provider: Provider,
    org_id: int | None = None,
) -> str | None:
    """Resolve the effective API key for a provider in a given context.

    Resolution hierarchy (top to bottom):
    1. Explicit api_key parameter (handled by caller)
    2. Org-level key (if org has byo_llm_keys feature)
    3. Provider-specific standard key (superadmin configured)
    4. Platform key from environment/settings
    5. None (not configured)

    Args:
        db: Database session
        provider: which LLM provider (claude, openai, gemini, deepseek, groq)
        org_id: None for superadmin context, otherwise org ID

    Returns: the API key to use, or None if not configured
    """
    from app import crud
    from app.config import settings as _settings

    # 1. Org-level key (if org has feature)
    if org_id is not None:
        try:
            from app.models.organization import Organization
            from app.services.tiers import has_feature
            org = db.get(Organization, org_id)
            if org and has_feature(org, "byo_llm_keys"):
                org_key = crud.get_effective_setting(
                    db, f"{provider}_api_key", org_id=org_id, default=""
                ) or ""
                if org_key:
                    return org_key
        except Exception:
            pass

    # 2. Provider-specific standard key (superadmin configured)
    standard_key = crud.get_effective_setting(
        db, f"standard_{provider}_api_key", default=""
    ) or ""
    if standard_key:
        return standard_key

    # 3. Platform key from environment
    if provider == "claude":
        return _settings.anthropic_api_key or None
    elif provider == "openai":
        return _settings.openai_api_key or None
    elif provider == "gemini":
        return _settings.gemini_api_key or None
    elif provider == "deepseek":
        return _settings.deepseek_api_key or None
    elif provider == "groq":
        return _settings.groq_api_key or None
    else:
        raise ValueError(f"Unknown provider: {provider}")


def get_provider_default_model(
    db: Any,
    provider: Provider,
    api_key: str | None = None,
) -> str:
    """Get the default model for a provider and API key.

    Falls back to built-in defaults if not configured.
    """
    if provider == "claude":
        from app.services.providers.claude import get_claude_default_model
        return get_claude_default_model(db, api_key)
    elif provider == "openai":
        from app.services.providers.openai import get_openai_default_model
        return get_openai_default_model(db, api_key)
    elif provider == "gemini":
        from app.services.providers.gemini import get_gemini_default_model
        return get_gemini_default_model(db, api_key)
    elif provider == "deepseek":
        from app.services.providers.deepseek import get_deepseek_default_model
        return get_deepseek_default_model(db, api_key)
    elif provider == "groq":
        from app.services.providers.groq import get_groq_default_model
        return get_groq_default_model(db, api_key)
    else:
        raise ValueError(f"Unknown provider: {provider}")


# ── Unified Call Interface ────────────────────────────────────────────────────

def llm_call(
    provider: Provider,
    system: str,
    user: str,
    *,
    api_key: str | None = None,
    model: str | None = None,
    max_tokens: int = 256,
    temperature: float = 1.0,
    parse_json: bool = False,
    db: Any = None,
    org_id: int | None = None,
) -> tuple[Any, dict[str, int]]:
    """Generic LLM call to any provider.

    Args:
        provider: "claude", "openai", "gemini", "deepseek", or "groq"
        system: System prompt
        user: User message
        api_key: Optional. If not provided, resolved from provider+org_id
        model: Optional. If not provided, uses provider's default
        max_tokens: Max output tokens (default 256)
        temperature: Sampling temperature (default 1.0)
        parse_json: If True, parse response as JSON
        db: Database session (required if api_key is None)
        org_id: Organization ID (required if api_key is None)

    Returns: (response_text_or_dict, token_stats)

    Example (explicit key):
        response, tokens = llm_call(
            "openai", system="...", user="...",
            api_key="sk-proj-xxx",
        )

    Example (resolve from org context):
        response, tokens = llm_call(
            "openai", system="...", user="...",
            db=db, org_id=org_id,
        )
    """
    # Resolve API key if not provided
    if api_key is None:
        if db is None:
            raise ValueError("Either api_key or db must be provided")
        api_key = resolve_provider_api_key(db, provider, org_id=org_id)
        if not api_key:
            raise ValueError(f"No {provider} API key configured for org_id={org_id}")

    # Resolve default model if not provided
    if model is None:
        model = get_provider_default_model(db, provider, api_key)

    # Route to provider
    if provider == "claude":
        from app.services.providers.claude import claude_call
        return claude_call(system, user, api_key=api_key, model=model, max_tokens=max_tokens, parse_json=parse_json)
    elif provider == "openai":
        from app.services.providers.openai import openai_call
        return openai_call(system, user, api_key=api_key, model=model, max_tokens=max_tokens, temperature=temperature, parse_json=parse_json)
    elif provider == "gemini":
        from app.services.providers.gemini import gemini_call
        return gemini_call(system, user, api_key=api_key, model=model, max_tokens=max_tokens, temperature=temperature, parse_json=parse_json)
    elif provider == "deepseek":
        from app.services.providers.deepseek import deepseek_call
        return deepseek_call(system, user, api_key=api_key, model=model, max_tokens=max_tokens, temperature=temperature, parse_json=parse_json)
    elif provider == "groq":
        from app.services.providers.groq import groq_call
        return groq_call(system, user, api_key=api_key, model=model, max_tokens=max_tokens, temperature=temperature, parse_json=parse_json)
    else:
        raise ValueError(f"Unknown provider: {provider}")


# ── Batch API (Only Claude, OpenAI, Gemini support batches) ────────────────────

def llm_batch_create(
    provider: Provider,
    requests: list[dict],
    *,
    api_key: str | None = None,
    db: Any = None,
    org_id: int | None = None,
) -> str:
    """Create a batch. Returns batch_id for polling/results.

    Either api_key or (db, org_id) must be provided.
    """
    if api_key is None:
        if db is None:
            raise ValueError("Either api_key or db must be provided")
        api_key = resolve_provider_api_key(db, provider, org_id=org_id)
        if not api_key:
            raise ValueError(f"No {provider} API key configured for org_id={org_id}")

    if provider == "claude":
        from app.services.providers.claude import claude_batch_create
        return claude_batch_create(requests, api_key=api_key)
    elif provider == "openai":
        from app.services.providers.openai import openai_batch_create
        return openai_batch_create(requests, api_key=api_key)
    elif provider == "gemini":
        from app.services.providers.gemini import gemini_batch_create
        return gemini_batch_create(requests, api_key=api_key)
    elif provider in ("deepseek", "groq"):
        raise NotImplementedError(f"{provider} does not support batch API")
    else:
        raise ValueError(f"Unknown provider: {provider}")


def llm_batch_poll(batch_id: str, *, provider: Provider, api_key: str) -> str:
    """Check batch status. Returns processing_status or similar."""
    if provider == "claude":
        from app.services.providers.claude import claude_batch_poll
        return claude_batch_poll(batch_id, api_key=api_key)
    elif provider == "openai":
        from app.services.providers.openai import openai_batch_poll
        return openai_batch_poll(batch_id, api_key=api_key)
    elif provider == "gemini":
        from app.services.providers.gemini import gemini_batch_poll
        return gemini_batch_poll(batch_id, api_key=api_key)
    else:
        raise ValueError(f"Provider {provider} does not support batch API")


def llm_batch_iter_results(
    batch_id: str,
    *,
    provider: Provider,
    api_key: str,
    poll: bool = True,
    poll_interval: float = 15.0,
    progress_cb: Any = None,
    total_hint: int = 0,
):
    """Iterate batch results. Yields BatchResult objects."""
    if provider == "claude":
        from app.services.providers.claude import claude_batch_iter_results
        return claude_batch_iter_results(batch_id, api_key=api_key, poll=poll, poll_interval=poll_interval, progress_cb=progress_cb, total_hint=total_hint)
    elif provider == "openai":
        from app.services.providers.openai import openai_batch_iter_results
        return openai_batch_iter_results(batch_id, api_key=api_key, poll=poll, poll_interval=poll_interval, progress_cb=progress_cb, total_hint=total_hint)
    elif provider == "gemini":
        from app.services.providers.gemini import gemini_batch_iter_results
        return gemini_batch_iter_results(batch_id, api_key=api_key, poll=poll, poll_interval=poll_interval, progress_cb=progress_cb, total_hint=total_hint)
    else:
        raise ValueError(f"Provider {provider} does not support batch API")


# ── Provider Metadata ────────────────────────────────────────────────────────

PROVIDER_INFO = {
    "claude": {
        "name": "Claude (Anthropic)",
        "models": ["claude-opus-4-7", "claude-sonnet-4-6", "claude-haiku-4-5-20251001"],
        "default_model": "claude-sonnet-4-6",
        "supports_batch": True,
        "supports_vision": True,
        "estimated_cost_per_1m_tokens": {"input": 3.0, "output": 15.0},  # USD
    },
    "openai": {
        "name": "OpenAI",
        "models": ["gpt-4o", "gpt-4-turbo", "gpt-4", "gpt-3.5-turbo"],
        "default_model": "gpt-4o",
        "supports_batch": True,
        "supports_vision": True,
        "estimated_cost_per_1m_tokens": {"input": 5.0, "output": 15.0},
    },
    "gemini": {
        "name": "Google Gemini",
        "models": ["gemini-2.0-flash", "gemini-2.0-flash-lite", "gemini-1.5-pro"],
        "default_model": "gemini-2.0-flash",
        "supports_batch": False,  # Pseudo-batch via polling
        "supports_vision": True,
        "estimated_cost_per_1m_tokens": {"input": 0.075, "output": 0.3},
    },
    "deepseek": {
        "name": "DeepSeek",
        "models": ["deepseek-chat", "deepseek-coder"],
        "default_model": "deepseek-chat",
        "supports_batch": False,
        "supports_vision": False,
        "estimated_cost_per_1m_tokens": {"input": 0.14, "output": 0.28},
    },
    "groq": {
        "name": "Groq",
        "models": ["llama-3.1-405b-versatile", "llama-3.1-70b-versatile", "mixtral-8x7b-32768"],
        "default_model": "llama-3.1-405b-versatile",
        "supports_batch": False,
        "supports_vision": False,
        "estimated_cost_per_1m_tokens": {"input": 0.59, "output": 0.79},
    },
}
