"""OpenAI API thin wrapper — immediate and batch calls.

Supported models: gpt-4o, gpt-4-turbo, gpt-4, gpt-3.5-turbo, etc.
Per-key default model configured via AppSetting.
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass
from typing import Any, Iterator

logger = logging.getLogger(__name__)

_DEFAULT_MODEL = "gpt-4o"


# ── Key resolution ────────────────────────────────────────────────────────────

def resolve_openai_api_key(db: Any, org_id: int | None) -> str | None:
    """Return effective OpenAI API key for this context.

    Org with byo_llm_keys: org-level openai_api_key setting
    Org without byo_llm_keys: platform key (never exposed)
    """
    from app.config import settings as _settings
    from app import crud

    if org_id is None:
        return _settings.openai_api_key or None

    try:
        from app.models.organization import Organization
        from app.services.tiers import has_feature
        org = db.get(Organization, org_id)
        if org and has_feature(org, "byo_llm_keys"):
            key = crud.get_effective_setting(db, "openai_api_key", org_id=org_id, default="") or ""
            return key or None
    except Exception:
        pass

    return _settings.openai_api_key or None


def get_openai_default_model(db: Any, api_key: str | None) -> str:
    """Get the default model for an API key.

    If superadmin key: check app setting or use _DEFAULT_MODEL
    If org key: check org setting or fallback to superadmin default
    """
    from app import crud

    if not api_key:
        return _DEFAULT_MODEL

    setting = crud.get_effective_setting(
        db, "openai_default_model", default=""
    ) or ""
    return setting or _DEFAULT_MODEL


# ── Shared helpers ────────────────────────────────────────────────────────────

def strip_fences(text: str) -> str:
    """Strip markdown code fences from a response."""
    return re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.MULTILINE).strip()


# ── Immediate call ────────────────────────────────────────────────────────────

def openai_call(
    system: str,
    user: str,
    *,
    api_key: str,
    model: str | None = None,
    max_tokens: int = 256,
    temperature: float = 1.0,
    parse_json: bool = False,
) -> tuple[Any, dict[str, int]]:
    """Single synchronous OpenAI call.

    Returns (response, token_stats) where token_stats = {"input_tokens": N, "output_tokens": N}.
    With parse_json=True the response is json.loads'd after stripping markdown fences.
    """
    from openai import OpenAI

    client = OpenAI(api_key=api_key)
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


# ── Batch API (File-based batches) ────────────────────────────────────────────

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


def openai_batch_create(
    requests: list[dict],
    *,
    api_key: str,
) -> str:
    """Submit an OpenAI Batch API request. Returns batch_id.

    Each request in the list should have:
    {
      "custom_id": "request-1",
      "params": {
        "model": "gpt-4o",
        "max_tokens": 256,
        "messages": [{"role": "user", "content": "..."}],
        ...
      }
    }
    """
    from openai import OpenAI

    client = OpenAI(api_key=api_key)

    # Convert to OpenAI batch format
    batch_requests = []
    for req in requests:
        batch_requests.append({
            "custom_id": req["custom_id"],
            "method": "POST",
            "url": "/v1/chat/completions",
            "body": req["params"],
        })

    # Create temporary file and submit
    import tempfile
    import jsonl

    with tempfile.NamedTemporaryFile(mode='w', suffix='.jsonl', delete=False) as f:
        for br in batch_requests:
            f.write(json.dumps(br) + '\n')
        temp_path = f.name

    try:
        with open(temp_path, 'rb') as f:
            batch_file = client.beta.files.upload(file=f, purpose="batch")
        batch = client.beta.batches.create(
            input_file_id=batch_file.id,
            endpoint="/v1/chat/completions",
            timeout_hours=24,
        )
        return batch.id
    finally:
        import os
        os.unlink(temp_path)


def openai_batch_poll(batch_id: str, *, api_key: str) -> str:
    """Return current status of a batch without blocking."""
    from openai import OpenAI

    client = OpenAI(api_key=api_key)
    batch = client.beta.batches.retrieve(batch_id)
    return batch.status


def openai_batch_iter_results(
    batch_id: str,
    *,
    api_key: str,
    poll: bool = True,
    poll_interval: float = 30.0,
    progress_cb: Any = None,
    total_hint: int = 0,
) -> Iterator[BatchResult]:
    """Iterate results for a batch, optionally polling until it finishes.

    poll=True (default): block-polls until status == "completed", then yields.
    poll=False: assumes the batch has already ended and goes straight to results.
    progress_cb(done: int, total: int) is called after each poll tick when poll=True.
    """
    from openai import OpenAI

    client = OpenAI(api_key=api_key)

    if poll:
        batch = client.beta.batches.retrieve(batch_id)
        while batch.status not in ("completed", "failed", "expired"):
            time.sleep(poll_interval)
            batch = client.beta.batches.retrieve(batch_id)
            if progress_cb:
                done = (batch.request_counts.completed or 0) + (batch.request_counts.failed or 0)
                progress_cb(done, total_hint)

    batch = client.beta.batches.retrieve(batch_id)

    # Iterate results
    for result in client.beta.batches.results(batch_id):
        if result.status_code == 200:
            msg = result.response["choices"][0]["message"]
            yield BatchResult(
                custom_id=result.custom_id,
                text=msg["content"].strip() if isinstance(msg["content"], str) else str(msg["content"]),
                error=None,
                input_tokens=result.response["usage"]["prompt_tokens"],
                output_tokens=result.response["usage"]["completion_tokens"],
            )
        else:
            yield BatchResult(
                custom_id=result.custom_id,
                text=None,
                error=f"HTTP {result.status_code}: {result.response.get('error', {}).get('message', 'Unknown error')}",
                input_tokens=0,
                output_tokens=0,
            )
