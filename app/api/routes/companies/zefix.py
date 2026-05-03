"""Zefix search and import routes."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app import crud
from app.clients import zefix_client
from app.auth import get_current_user
from app.database import get_db
from app.models.user import User
from app.schemas.company import CompanyCreate, CompanyRead, CompanyUpdate, ZefixSearchResult

from app.api.routes.companies._shared import _clear_noga_cache

router = APIRouter()


@router.get("/zefix/search", response_model=list[ZefixSearchResult], summary="Search Zefix API")
def zefix_search(
    name: str = Query(..., description="Company name to search for"),
    max_results: int = Query(20, ge=1, le=100, description="Maximum number of results"),
    active_only: bool = Query(False, description="Return only active companies"),
    _: User = Depends(get_current_user),
):
    """Query the Zefix REST API for Swiss companies matching *name*."""
    try:
        return zefix_client.search_companies(name, max_results=max_results, active_only=active_only)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc


@router.get("/zefix/{uid}", response_model=dict, summary="Get full Zefix company details")
def zefix_get_company(uid: str, _: User = Depends(get_current_user)):
    """Fetch the full company record from the Zefix API by UID."""
    try:
        return zefix_client.get_company(uid)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc


@router.post(
    "/zefix/import/{uid}",
    response_model=CompanyRead,
    status_code=status.HTTP_201_CREATED,
    summary="Import a company from Zefix into the database",
)
def import_from_zefix(uid: str, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    """Fetch a company from the Zefix API and store it in the local database."""
    try:
        raw = zefix_client.get_company(uid)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    name_raw = raw.get("name", "")
    if isinstance(name_raw, dict):
        name = name_raw.get("de") or name_raw.get("fr") or name_raw.get("it") or next(iter(name_raw.values()), "")
    else:
        name = str(name_raw)

    legal_form_raw = raw.get("legalForm", {})
    if isinstance(legal_form_raw, dict):
        legal_form = legal_form_raw.get("de") or legal_form_raw.get("shortName") or None
    else:
        legal_form = str(legal_form_raw) if legal_form_raw else None

    address_parts = raw.get("address", {}) or {}
    address_str: str | None = None
    if isinstance(address_parts, dict):
        parts = [
            address_parts.get("street"),
            address_parts.get("houseNumber"),
            address_parts.get("swissZipCode"),
            address_parts.get("city"),
        ]
        address_str = " ".join(str(p) for p in parts if p) or None

    uid_normalised = zefix_client._normalise_uid(str(raw.get("uid", uid)))

    purpose_raw = raw.get("purpose") or raw.get("purposes") or None
    if isinstance(purpose_raw, list):
        purpose = " ".join(str(p) for p in purpose_raw if p) or None
    elif isinstance(purpose_raw, dict):
        purpose = (
            purpose_raw.get("de") or purpose_raw.get("fr")
            or purpose_raw.get("it") or purpose_raw.get("en")
            or next(iter(purpose_raw.values()), None) or None
        )
    else:
        purpose = str(purpose_raw) if purpose_raw else None

    company_data = CompanyCreate(
        uid=uid_normalised,
        name=name,
        legal_form=legal_form,
        status=str(raw.get("status", "")) or None,
        municipality=raw.get("municipality") or None,
        canton=raw.get("canton") or None,
        purpose=purpose,
        address=address_str,
        zefix_raw=json.dumps(raw),
    )

    existing = crud.get_company_by_uid(db, uid_normalised)
    if existing:
        result = crud.update_company(db, existing, CompanyUpdate(**company_data.model_dump(exclude={"uid"})))
        _clear_noga_cache()
        return result
    result = crud.create_company(db, company_data)
    _clear_noga_cache()
    return result
