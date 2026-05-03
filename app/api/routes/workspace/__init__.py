"""Workspace routes package."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.routes.workspace import company_state, members, org

router = APIRouter(prefix="/orgs/{org_id}", tags=["workspace"])
router.include_router(org.router)
router.include_router(members.router)
router.include_router(company_state.router)
