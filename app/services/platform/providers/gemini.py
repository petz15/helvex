"""Google Gemini API thin wrapper — immediate and batch calls.

Supported models: gemini-2.0-flash, gemini-2.0-flash-lite, gemini-1.5-pro, etc.
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

_DEFAULT_MODEL = "gemini-2.0-flash"


# ── Key resolution ────────────────────────────────────────────────────────────

def get_gemini_default_model(db: Any, api_key: str | None) -> str:
    """Get the default model for an API key."""
    from app import crud

    if not api_key:
        return _DEFAULT_MODEL

    setting = crud.get_effective_setting(
        db, "gemini_default_model", default=""
    ) or ""
    return setting or _DEFAULT_MODEL


# ── Shared helpers ────────────────────────────────────────────────────────────

def strip_fences(text: str) -> str:
    """Strip markdown code fences from a response."""
    return re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.MULTILINE).strip()


# ── Immediate call ────────────────────────────────────────────────────────────

def gemini_call(
    system: str,
    user: str,
    *,
    api_key: str,
    model: str | None = None,
    max_tokens: int = 256,
    temperature: float = 1.0,
    parse_json: bool = False,
) -> tuple[Any, dict[str, int]]:
    """Single synchronous Gemini call.

    Returns (response, token_stats) where token_stats = {"input_tokens": N, "output_tokens": N}.
    With parse_json=True the response is json.loads'd after stripping markdown fences.
    """
    import google.generativeai as genai

    genai.configure(api_key=api_key)
    if not model:
        model = _DEFAULT_MODEL

    client = genai.GenerativeModel(model_name=model)

    messages = []
    if system:
        messages.append({"role": "user", "parts": [{"text": system}]})
    messages.append({"role": "user", "parts": [{"text": user}]})

    response = client.generate_content(
        messages,
        generation_config={
            "max_output_tokens": max_tokens,
            "temperature": temperature,
        },
    )

    text = response.text.strip()
    # Gemini SDK provides usage_metadata
    usage = response.usage_metadata if hasattr(response, 'usage_metadata') else None
    tokens = {
        "input_tokens": usage.prompt_token_count if usage else 0,
        "output_tokens": usage.candidates_token_count if usage else 0,
    }

    if parse_json:
        return json.loads(strip_fences(text)), tokens
    return text, tokens


# ── Batch API (Gemini uses async with polling) ────────────────────────────────

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


def gemini_batch_create(
    requests: list[dict],
    *,
    api_key: str,
) -> str:
    """Create a batch of Gemini requests and return a batch_id for polling.

    NOTE: Gemini doesn't have a native batch API like OpenAI.
    We store requests in the AppSetting table (Postgres) and return a
    pseudo-batch ID for polling.
    """
    import uuid
    from app.database import SessionLocal
    from app import crud

    batch_id = f"gemini_batch_{uuid.uuid4().hex[:8]}"

    # Store batch metadata in AppSetting
    db = SessionLocal()
    try:
        batch_data = {
            "batch_id": batch_id,
            "provider": "gemini",
            "requests": requests,
            "created_at": time.time(),
            "status": "queued",
            "results": {},
        }
        crud.upsert_app_setting(
            db,
            key=f"batch_{batch_id}",
            value=json.dumps(batch_data),
        )
        logger.info("gemini_batch_create batch_id=%s requests=%d", batch_id, len(requests))
    finally:
        db.close()

    return batch_id


def gemini_batch_iter_results(
    batch_id: str,
    *,
    api_key: str,
    poll: bool = True,
    poll_interval: float = 5.0,
    progress_cb: Any = None,
    total_hint: int = 0,
) -> Iterator[BatchResult]:
    """Iterate results for a Gemini pseudo-batch, processing on-demand.

    Since Gemini doesn't have native batch API, we process requests serially
    and store results in the batch metadata. This is a simplified implementation.
    """
    from app.database import SessionLocal
    from app import crud
    import google.generativeai as genai

    db = SessionLocal()
    try:
        batch_data_str = crud.get_effective_setting(db, f"batch_{batch_id}", default="")
        if not batch_data_str:
            raise ValueError(f"Batch {batch_id} not found")

        batch_data = json.loads(batch_data_str)
        requests = batch_data.get("requests", [])
        results = batch_data.get("results", {})

        if poll and batch_data.get("status") == "queued":
            batch_data["status"] = "processing"
            genai.configure(api_key=api_key)
            model = requests[0].get("params", {}).get("model", _DEFAULT_MODEL) if requests else _DEFAULT_MODEL
            client = genai.GenerativeModel(model_name=model)

            for i, req in enumerate(requests):
                custom_id = req.get("custom_id")
                try:
                    params = req.get("params", {})
                    messages = params.get("messages", [])
                    response = client.generate_content(messages)
                    results[custom_id] = {
                        "succeeded": True,
                        "text": response.text.strip(),
                        "tokens": {
                            "input": response.usage_metadata.prompt_token_count if hasattr(response, 'usage_metadata') else 0,
                            "output": response.usage_metadata.candidates_token_count if hasattr(response, 'usage_metadata') else 0,
                        }
                    }
                except Exception as exc:
                    results[custom_id] = {
                        "succeeded": False,
                        "error": str(exc),
                        "text": None,
                        "tokens": {"input": 0, "output": 0}
                    }

                if progress_cb:
                    progress_cb(i + 1, len(requests))

            batch_data["status"] = "completed"
            batch_data["results"] = results
            crud.upsert_app_setting(
                db,
                key=f"batch_{batch_id}",
                value=json.dumps(batch_data),
            )

        # Yield results
        for custom_id, result in results.items():
            if result.get("succeeded"):
                yield BatchResult(
                    custom_id=custom_id,
                    text=result.get("text"),
                    error=None,
                    input_tokens=result.get("tokens", {}).get("input", 0),
                    output_tokens=result.get("tokens", {}).get("output", 0),
                )
            else:
                yield BatchResult(
                    custom_id=custom_id,
                    text=None,
                    error=result.get("error"),
                    input_tokens=0,
                    output_tokens=0,
                )
    finally:
        db.close()
