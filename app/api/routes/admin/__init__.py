"""Admin-only routes."""

from .api_keys import router as api_keys_router

__all__ = ["api_keys_router"]
