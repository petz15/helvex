"""Companies routes package."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.routes.companies import analytics, bulk, detail, list as list_mod, search, zefix

router = APIRouter(prefix="/companies", tags=["companies"])

# Order matters: specific paths before parameterized ones
router.include_router(zefix.router)
router.include_router(analytics.router)
router.include_router(search.router)
router.include_router(bulk.router)
router.include_router(list_mod.router)
router.include_router(detail.router)
