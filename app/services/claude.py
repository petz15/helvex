"""Claude API thin wrappers — immediate and batch calls.

API key tiers (resolved via resolve_claude_api_key):
  - org_id=None (superadmin / platform jobs): platform key from settings
  - org WITH byo_llm_keys feature: org-level stored key — their own billing
  - org WITHOUT byo_llm_keys feature: platform key — never exposed to that org
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass
from typing import Any, Iterator

logger = logging.getLogger(__name__)

_DEFAULT_MODEL = "claude-haiku-4-5-20251001"


# ── Key resolution ────────────────────────────────────────────────────────────

def resolve_claude_api_key(db: Any, org_id: int | None) -> str | None:
    """Return effective Claude API key for this context.

    Never returns or logs the platform key to org-level callers that lack the
    byo_llm_keys feature — they simply run against the platform key silently.
    """
    from app.config import settings as _settings
    from app import crud

    if org_id is None:
        return _settings.anthropic_api_key or None

    try:
        from app.models.organization import Organization
        from app.services.tiers import has_feature
        org = db.get(Organization, org_id)
        if org and has_feature(org, "byo_llm_keys"):
            key = crud.get_effective_setting(db, "anthropic_api_key", org_id=org_id, default="") or ""
            return key or None
    except Exception:
        pass

    return _settings.anthropic_api_key or None


def get_claude_default_model(db: Any, api_key: str | None = None) -> str:
    """Get the default model for Claude. Always returns the configured default."""
    from app import crud

    if not api_key:
        return _DEFAULT_MODEL

    setting = crud.get_effective_setting(db, "claude_default_model", default="") or ""
    return setting or _DEFAULT_MODEL


# ── Shared helpers ────────────────────────────────────────────────────────────

def strip_fences(text: str) -> str:
    """Strip markdown code fences from a Claude response."""
    return re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.MULTILINE).strip()


def _system_param(system: str, cache: bool) -> Any:
    if not system:
        return None
    if cache:
        return [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
    return system


# ── Immediate call ────────────────────────────────────────────────────────────

def claude_call(
    system: str,
    user: str,
    *,
    api_key: str,
    model: str = _DEFAULT_MODEL,
    max_tokens: int = 256,
    cache_system: bool = True,
    parse_json: bool = False,
) -> tuple[Any, dict[str, int]]:
    """Single synchronous Claude call.

    Returns (response, token_stats) where token_stats = {"input_tokens": N, "output_tokens": N}.
    With parse_json=True the response is json.loads'd after stripping markdown fences.
    """
    import anthropic

    client = anthropic.Anthropic(api_key=api_key)
    kwargs: dict[str, Any] = dict(
        model=model,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": user}],
    )
    sp = _system_param(system, cache_system)
    if sp is not None:
        kwargs["system"] = sp

    msg = client.messages.create(**kwargs)
    text = msg.content[0].text.strip()
    tokens: dict[str, int] = {
        "input_tokens": msg.usage.input_tokens,
        "output_tokens": msg.usage.output_tokens,
    }
    if parse_json:
        return json.loads(strip_fences(text)), tokens
    return text, tokens


# ── Batch API ─────────────────────────────────────────────────────────────────

@dataclass
class BatchResult:
    custom_id: str
    text: str | None        # None on error
    error: str | None       # None on success
    input_tokens: int
    output_tokens: int

    @property
    def succeeded(self) -> bool:
        return self.text is not None

    def parse_json(self) -> Any:
        if self.text is None:
            raise ValueError(f"BatchResult {self.custom_id!r} has no text (error: {self.error})")
        return json.loads(strip_fences(self.text))


def claude_batch_create(
    requests: list[dict],
    *,
    api_key: str,
) -> str:
    """Submit an Anthropic Message Batch. Returns the batch_id."""
    import anthropic

    client = anthropic.Anthropic(api_key=api_key)
    batch = client.beta.messages.batches.create(requests=requests)
    return batch.id


def claude_batch_poll(batch_id: str, *, api_key: str) -> str:
    """Return current processing_status of a batch without blocking."""
    import anthropic

    client = anthropic.Anthropic(api_key=api_key)
    return client.beta.messages.batches.retrieve(batch_id).processing_status


def claude_batch_iter_results(
    batch_id: str,
    *,
    api_key: str,
    poll: bool = True,
    poll_interval: float = 15.0,
    progress_cb: Any = None,
    total_hint: int = 0,
) -> Iterator[BatchResult]:
    """Iterate results for a batch, optionally polling until it finishes.

    poll=True (default): block-polls until processing_status == "ended", then yields.
    poll=False: assumes the batch has already ended and goes straight to results.
    progress_cb(done: int, total: int) is called after each poll tick when poll=True.
    """
    import anthropic

    client = anthropic.Anthropic(api_key=api_key)

    if poll:
        batch = client.beta.messages.batches.retrieve(batch_id)
        while batch.processing_status != "ended":
            time.sleep(poll_interval)
            batch = client.beta.messages.batches.retrieve(batch_id)
            if progress_cb:
                counts = batch.request_counts
                done = (counts.succeeded or 0) + (counts.errored or 0)
                progress_cb(done, total_hint)

    for result in client.beta.messages.batches.results(batch_id):
        if result.result.type == "succeeded":
            msg = result.result.message
            yield BatchResult(
                custom_id=result.custom_id,
                text=msg.content[0].text.strip(),
                error=None,
                input_tokens=msg.usage.input_tokens,
                output_tokens=msg.usage.output_tokens,
            )
        else:
            yield BatchResult(
                custom_id=result.custom_id,
                text=None,
                error=str(getattr(result.result, "error", result.result.type)),
                input_tokens=0,
                output_tokens=0,
            )
