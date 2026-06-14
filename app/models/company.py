from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import TypeDecorator

from app.database import Base


class ArrayOfText(TypeDecorator):
    """ARRAY(Text) on PostgreSQL; JSON on SQLite (and other dialects) for test compatibility."""
    impl = JSON
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(ARRAY(Text))
        return dialect.type_descriptor(JSON())


class Company(Base):
    __tablename__ = "companies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    uid: Mapped[str] = mapped_column(String(20), unique=True, index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(512), nullable=False, index=True)
    legal_form: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    status: Mapped[str | None] = mapped_column(String(64), nullable=True)
    municipality: Mapped[str | None] = mapped_column(String(256), nullable=True)
    canton: Mapped[str | None] = mapped_column(String(8), nullable=True)
    purpose: Mapped[str | None] = mapped_column(Text, nullable=True)
    address: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Website found via Google Search.
    # DUAL-WRITE: website_url, website_checked_at, google_search_results_raw, web_score,
    # and social_media_only also exist on org_company_state for org-specific re-scores.
    # Company holds the global master result (seed value); org_company_state holds any
    # org-specific override. Prefer org_company_state when org_id is available.
    website_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    website_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Top-5 Google results as JSON [{title, link, snippet, score}, ...] sorted by score desc
    google_search_results_raw: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Full provider JSON response (all fields: search_information, local_results, organic_results, etc.)
    google_search_full_raw: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 0-100 auto match score for the current website_url; None = not yet scored
    web_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # True if Google only returned social media results (no real website found)
    social_media_only: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    # Manual workflow statuses
    # None='pending' | 'interesting' | 'rejected'
    # | 'potential_proposal' | 'confirmed_proposal'
    # | 'potential_generic' | 'confirmed_generic'
    review_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # 'not_sent' | 'sent' | 'responded' | 'converted' | 'rejected'
    contact_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # Contact info (manually entered)
    contact_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    contact_email: Mapped[str | None] = mapped_column(String(512), nullable=True)
    contact_phone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Comma-separated free-form labels, e.g. "saas,b2b,warm-lead"
    tags: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Zefix administrative identifiers
    ehraid: Mapped[str | None] = mapped_column(String(64), nullable=True)
    chid: Mapped[str | None] = mapped_column(String(32), nullable=True)
    legal_seat_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    legal_form_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    legal_form_uid: Mapped[str | None] = mapped_column(String(64), nullable=True)
    legal_form_short_name: Mapped[str | None] = mapped_column(String(32), nullable=True)
    sogc_date: Mapped[str | None] = mapped_column(String(32), nullable=True)
    first_sogc_date: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    deletion_date: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # Extended Zefix detail fields (populated from per-UID endpoint only)
    capital_nominal: Mapped[str | None] = mapped_column(String(64), nullable=True)
    capital_currency: Mapped[str | None] = mapped_column(String(16), nullable=True)
    head_offices: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON
    further_head_offices: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON
    branch_offices: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON
    has_taken_over: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON
    was_taken_over_by: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON
    audit_companies: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON
    old_names: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON
    translations: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON
    sogc_pub: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON
    cantonal_excerpt_web: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    zefix_detail_web: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    address_city: Mapped[str | None] = mapped_column(String(256), nullable=True)
    address_zip: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # Priority/lead score derived from Zefix data alone (0-100)
    flex_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # JSON object with component-level flex scoring contributions
    flex_score_breakdown: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Geocoded coordinates (from Nominatim, based on the Zefix address)
    lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    lon: Mapped[float | None] = mapped_column(Float, nullable=True)
    # TF-IDF keyword tags (comma-separated, e.g. "werkzeugmaschinen,robotics,handlinggeräte")
    tfidf_cluster: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Per-company top TF-IDF keywords extracted from this company's own purpose text
    purpose_keywords: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Array form of purpose_keywords (GIN-indexed for exact-match membership queries)
    purpose_keywords_arr: Mapped[list[str] | None] = mapped_column(ArrayOfText, nullable=True)
    # AI classification score, category, and optional free-form text
    ai_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ai_category: Mapped[str | None] = mapped_column(String(128), nullable=True)
    ai_freeform: Mapped[str | None] = mapped_column(Text, nullable=True)
    # NOGA classification derived from official taxonomy
    noga_code: Mapped[str | None] = mapped_column(String(16), nullable=True, index=True)
    noga_label: Mapped[str | None] = mapped_column(String(512), nullable=True)
    noga_level: Mapped[str | None] = mapped_column(String(32), nullable=True)
    noga_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    noga_classified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Full ancestry path, pipe-separated root→leaf, e.g. "C|26|263|2630|263001"
    noga_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    noga_path_labels: Mapped[str | None] = mapped_column(Text, nullable=True)
    noga_level_confidence: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # Best-match code from global embedding search (peak before constrained descent)
    noga_peak_code: Mapped[str | None] = mapped_column(String(16), nullable=True)
    noga_peak_label: Mapped[str | None] = mapped_column(String(512), nullable=True)
    ai_scored_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Rule-based business model classification: 'b2b' | 'b2c' | 'b2g' | 'mixed' | NULL
    business_model: Mapped[str | None] = mapped_column(String(16), nullable=True, index=True)
    flex_scored_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Stored combined score: relevance formula (ai*0.60 + noga_confidence*0.25 + keyword_density*0.15).
    # Written by scoring jobs whenever any component score changes. Enables indexed filtering.
    combined_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Detected language of purpose text: 'de' | 'fr' | 'it' | 'en' | 'rm' | NULL
    purpose_language: Mapped[str | None] = mapped_column(String(8), nullable=True, index=True)
    # Raw JSON from Zefix API stored for reference
    zefix_raw: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Exact search params sent to the Google Search API for this company's last search.
    # Keys: q, provider, gl/country, hl/language, location.
    # Stored so bad results (wrong language, wrong location) can be diagnosed after the fact.
    google_search_params: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    @staticmethod
    def compute_combined_score(
        ai_score: int | None,
        noga_confidence: float | None = None,
        purpose_keywords: str | None = None,
        # Legacy params kept for backward-compat with old call sites
        web_score: int | None = None,
        flex_score: int | None = None,
        w_ai: float = 0.60,
        w_web: float = 0.0,
        w_flex: float = 0.0,
    ) -> float | None:
        """Relevance score: ai_score×0.60 + noga_confidence×100×0.25 + keyword_density×100×0.15.

        If ai_score is absent, remaining weights renormalise to 0.62/0.38.
        Returns None when all components are absent.
        """
        from app.services.scoring import compute_relevance_score as _compute

        class _FakeCompany:
            pass

        c = _FakeCompany()
        c.ai_score = ai_score
        c.noga_confidence = noga_confidence
        c.purpose_keywords = purpose_keywords
        return _compute(c)

    notes: Mapped[list["Note"]] = relationship("Note", back_populates="company", cascade="all, delete-orphan")  # noqa: F821
    sogc_publications: Mapped[list["SogcPublication"]] = relationship("SogcPublication", foreign_keys="SogcPublication.company_id", back_populates="company", cascade="all, delete-orphan")  # noqa: F821
