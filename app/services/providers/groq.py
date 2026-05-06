"""Groq API thin wrapper — immediate calls only.

Supported models: llama-3.1-405b, llama-3.1-70b, mixtral-8x7b, etc.
Groq doesn't have batch API, so only immediate calls are supported.
Focus on ultra-fast inference (low latency).
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

_DEFAULT_MODEL = "llama-3.1-405b-versatile"


# ── Key resolution ────────────────────────────────────────────────────────────

def resolve_groq_api_key(db: Any, org_id: int | None) -> str | None:
    """Return effective Groq API key for this context.

    Org with byo_llm_keys: org-level groq_api_key setting
    Org without byo_llm_keys: platform key (never exposed)
    """
    from app.config import settings as _settings
    from app import crud

    if org_id is None:
        return _settings.groq_api_key or None

    try:
        from app.models.organization import Organization
        from app.services.tiers import has_feature
        org = db.get(Organization, org_id)
        if org and has_feature(org, "byo_llm_keys"):
            key = crud.get_effective_setting(db, "groq_api_key", org_id=org_id, default="") or ""
            return key or None
    except Exception:
        pass

    return _settings.groq_api_key or None


def get_groq_default_model(db: Any, api_key: str | None) -> str:
    """Get the default model for an API key."""
    from app import crud

    if not api_key:
        return _DEFAULT_MODEL

    setting = crud.get_effective_setting(
        db, "groq_default_model", default=""
    ) or ""
    return setting or _DEFAULT_MODEL


# ── Shared helpers ────────────────────────────────────────────────────────────

def strip_fences(text: str) -> str:
    """Strip markdown code fences from a response."""
    return re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.MULTILINE).strip()


# ── Immediate call (OpenAI-compatible API) ────────────────────────────────────

def groq_call(
    system: str,
    user: str,
    *,
    api_key: str,
    model: str | None = None,
    max_tokens: int = 256,
    temperature: float = 1.0,
    parse_json: bool = False,
) -> tuple[Any, dict[str, int]]:
    """Single synchronous Groq call.

    Groq uses OpenAI-compatible API, so we use the Groq SDK (which extends OpenAI).
    Returns (response, token_stats) where token_stats = {"input_tokens": N, "output_tokens": N}.
    """
    from groq import Groq

    client = Groq(api_key=api_key)

    if not model:
        model = _DEFAULT_MODEL

    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": user})

    response = client.chat.completions.create(
        model=model,
        messages=messages,
        max_tokens=max_tokens,
        temperature=temperature,
    )

    text = response.choices[0].message.content.strip()
    tokens = {
        "input_tokens": response.usage.prompt_tokens,
        "output_tokens": response.usage.completion_tokens,
    }

    if parse_json:
        return json.loads(strip_fences(text)), tokens
    return text, tokens
