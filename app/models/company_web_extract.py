from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Float, ForeignKey, Integer, PrimaryKeyConstraint, String, Text, func
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON, TypeDecorator

from app.database import Base


class _ArrayOfText(TypeDecorator):
    """ARRAY(Text) on PostgreSQL; JSON on SQLite for test compatibility."""
    impl = JSON
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(ARRAY(Text))
        return dialect.type_descriptor(JSON())


class CompanyWebExtract(Base):
    """Resolved structured data extracted from a company's crawled web pages.

    One row per (company, URL candidate). When a company is crawled via multiple
    URL candidates the best row (highest confidence, then most recent) is returned
    by get_best_web_extract(). This lets downstream code compare results across
    candidates and discard lower-quality ones without losing history.

    Produced by the ``web_extract`` job from raw HTML stored in S3. No API cost:
    trafilatura main-text extraction + deterministic parsers only.
    """

    __tablename__ = "company_web_extract"
    __table_args__ = (
        PrimaryKeyConstraint("company_id", "url_candidate_id"),
    )

    company_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
    )
    url_candidate_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("company_url_candidates.id", ondelete="CASCADE"),
        nullable=False,
    )
    emails: Mapped[list[str] | None] = mapped_column(_ArrayOfText, nullable=True)
    phones: Mapped[list[str] | None] = mapped_column(_ArrayOfText, nullable=True)
    # {linkedin, xing, facebook, instagram, twitter, youtube} → url
    socials: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # Postal address parsed from impressum / schema.org, if found
    address: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Swiss UID (CHE-xxx.xxx.xxx) — a verifiable company-match signal
    uid: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # True if site UID == Zefix UID (verified site); False if it contradicts;
    # None if the site exposed no UID to compare.
    uid_matches_zefix: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    # Management / contact names parsed from the impressum (role-labelled + NER).
    persons: Mapped[list[str] | None] = mapped_column(_ArrayOfText, nullable=True)
    # True if no UID was found but company name + zip/city address both match
    # Zefix exactly — a weaker but still solid fallback verification signal.
    name_address_verified: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    # Languages the site is offered in (hreflang / <html lang>)
    languages: Mapped[list[str] | None] = mapped_column(_ArrayOfText, nullable=True)
    # Short description (schema.org / OpenGraph / meta description)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Product/service keywords mined from cleaned main text
    service_keywords: Mapped[list[str] | None] = mapped_column(_ArrayOfText, nullable=True)
    # How values were obtained — e.g. "deterministic", "schema_org+regex"
    extraction_method: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # 0.0–1.0 heuristic confidence in the resolved record
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Number of crawled pages that fed this extraction
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    extracted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
