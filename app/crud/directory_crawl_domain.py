"""CRUD for directory_crawl_domains — the managed list of directory sites to crawl."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models.directory_crawl_domain import DirectoryCrawlDomain


def get_approved_directory_crawl_domains(db: Session) -> set[str]:
    """Approved domains — i.e. "never a company's own website".

    Feeds `get_effective_crawl_blocklist`. Says nothing about whether the domain
    is worth harvesting; see get_harvestable_directory_domains.
    """
    rows = (
        db.query(DirectoryCrawlDomain.value)
        .filter(DirectoryCrawlDomain.status == "approved")
        .all()
    )
    return {r[0] for r in rows}


def get_harvestable_directory_domains(db: Session) -> set[str]:
    """Approved domains whose profile pages are worth crawling for data.

    The strict subset of the blocklist that carries value: local.ch and
    treuhandvergleich.ch (reviews, profiles) yes; kompass.ch, moneyland.ch and
    business-monitor.ch no. Before the `harvest` flag existed these two questions
    shared one column, so approving a domain to keep it out of candidate
    selection also silently signed it up for directory crawling.
    """
    rows = (
        db.query(DirectoryCrawlDomain.value)
        .filter(
            DirectoryCrawlDomain.status == "approved",
            DirectoryCrawlDomain.harvest.is_(True),
        )
        .all()
    )
    return {r[0] for r in rows}


def list_directory_crawl_domains(
    db: Session,
    status: str | None = None,
) -> list[DirectoryCrawlDomain]:
    q = db.query(DirectoryCrawlDomain)
    if status:
        q = q.filter(DirectoryCrawlDomain.status == status)
    return q.order_by(DirectoryCrawlDomain.company_count.desc().nullslast(), DirectoryCrawlDomain.value).all()


def get_directory_crawl_domain(db: Session, domain_id: int) -> DirectoryCrawlDomain | None:
    return db.get(DirectoryCrawlDomain, domain_id)


def get_directory_crawl_domain_by_value(db: Session, value: str) -> DirectoryCrawlDomain | None:
    return db.query(DirectoryCrawlDomain).filter(DirectoryCrawlDomain.value == value).first()


def upsert_directory_crawl_domain(
    db: Session,
    value: str,
    *,
    source: str = "auto_discovered",
    company_count: int | None = None,
    notes: str | None = None,
) -> tuple[DirectoryCrawlDomain, bool]:
    """Insert a new pending_review domain, or update company_count if it already exists.

    Returns (row, created). Existing rows are never demoted — their status is preserved.
    """
    existing = get_directory_crawl_domain_by_value(db, value)
    if existing:
        if company_count is not None:
            existing.company_count = company_count
        db.flush()
        return existing, False
    row = DirectoryCrawlDomain(
        value=value,
        status="pending_review",
        source=source,
        company_count=company_count,
        notes=notes,
    )
    db.add(row)
    db.flush()
    return row, True


def approve_directory_crawl_domain(db: Session, domain_id: int) -> DirectoryCrawlDomain | None:
    row = db.get(DirectoryCrawlDomain, domain_id)
    if row:
        row.status = "approved"
        row.reviewed_at = datetime.now(timezone.utc)
        db.flush()
    return row


def reject_directory_crawl_domain(db: Session, domain_id: int) -> DirectoryCrawlDomain | None:
    row = db.get(DirectoryCrawlDomain, domain_id)
    if row:
        row.status = "rejected"
        row.reviewed_at = datetime.now(timezone.utc)
        db.flush()
    return row


def delete_directory_crawl_domain(db: Session, domain_id: int) -> bool:
    row = db.get(DirectoryCrawlDomain, domain_id)
    if row:
        db.delete(row)
        db.flush()
        return True
    return False


def count_pending_directory_crawl_domains(db: Session) -> int:
    return db.query(DirectoryCrawlDomain).filter(DirectoryCrawlDomain.status == "pending_review").count()
