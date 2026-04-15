"""REST API for cluster registry management (superadmin only)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth import require_superadmin
from app.database import get_db
from app.models.user import User

router = APIRouter(tags=["clusters"])


class ClusterRegistryOut(BaseModel):
    id: int
    canonical_name: str
    top_terms: str        # JSON string
    active: bool
    company_count: int
    created_at: str
    updated_at: str

    model_config = {"from_attributes": True}


class RenameBody(BaseModel):
    new_name: str


@router.get("/clusters/registry", response_model=list[ClusterRegistryOut])
def list_registry(
    db: Session = Depends(get_db),
    _: User = Depends(require_superadmin),
):
    """List all cluster registry entries with live company counts."""
    from app.crud.cluster_registry import list_all_clusters, get_cluster_company_count

    entries = list_all_clusters(db)
    result = []
    for e in entries:
        result.append(ClusterRegistryOut(
            id=e.id,
            canonical_name=e.canonical_name,
            top_terms=e.top_terms,
            active=e.active,
            company_count=get_cluster_company_count(db, e.canonical_name),
            created_at=e.created_at.isoformat() if e.created_at else "",
            updated_at=e.updated_at.isoformat() if e.updated_at else "",
        ))
    return result


@router.patch("/clusters/registry/{registry_id}", response_model=ClusterRegistryOut)
def rename_cluster(
    registry_id: int,
    body: RenameBody,
    db: Session = Depends(get_db),
    _: User = Depends(require_superadmin),
):
    """Rename a cluster's canonical_name and update all companies that reference it."""
    from app.crud.cluster_registry import rename_cluster as _rename, get_cluster_company_count

    try:
        entry = _rename(db, registry_id, body.new_name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if entry is None:
        raise HTTPException(status_code=404, detail="Registry entry not found")

    return ClusterRegistryOut(
        id=entry.id,
        canonical_name=entry.canonical_name,
        top_terms=entry.top_terms,
        active=entry.active,
        company_count=get_cluster_company_count(db, entry.canonical_name),
        created_at=entry.created_at.isoformat() if entry.created_at else "",
        updated_at=entry.updated_at.isoformat() if entry.updated_at else "",
    )
