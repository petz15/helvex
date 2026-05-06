"""Admin-only routes."""

from fastapi import APIRouter

from .api_keys import router as api_keys_router

router = APIRouter()
router.include_router(api_keys_router)

__all__ = ["router", "api_keys_router"]
