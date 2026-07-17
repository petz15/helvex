"""Claude (Anthropic) provider — re-exports the shared Claude client.

The implementation lives in app.services.scoring.claude (used directly by the
default AI-scoring pipeline via job_handlers/claude.py); this module re-exports
it so Claude also fits the pluggable multi-provider interface in
app.services.platform.llm alongside openai/gemini/deepseek/groq.
"""
from app.services.scoring.claude import (
    claude_batch_create,
    claude_batch_iter_results,
    claude_batch_poll,
    claude_call,
    get_claude_default_model,
    resolve_claude_api_key,
    strip_fences,
)

__all__ = [
    "claude_batch_create",
    "claude_batch_iter_results",
    "claude_batch_poll",
    "claude_call",
    "get_claude_default_model",
    "resolve_claude_api_key",
    "strip_fences",
]
