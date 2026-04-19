from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Company(Base):
    __tablename__ = "companies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    uid: Mapped[str] = mapped_column(String(20), unique=True, index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(512), nullable=False, index=True)
    legal_form: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[str | None] = mapped_column(String(64), nullable=True)
    municipality: Mapped[str | None] = mapped_column(String(256), nullable=True)
    canton: Mapped[str | None] = mapped_column(String(8), nullable=True)
    purpose: Mapped[str | None] = mapped_column(Text, nullable=True)
    address: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Website found via Google Search
    website_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    website_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Top-5 Google results as JSON [{title, link, snippet, score}, ...] sorted by score desc
    google_search_results_raw: Mapped[str | None] = mapped_column(Text, nullable=True)
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
    sogc_pub: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON array
    capital_nominal: Mapped[str | None] = mapped_column(String(64), nullable=True)
    capital_currency: Mapped[str | None] = mapped_column(String(16), nullable=True)
    head_offices: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON
    further_head_offices: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON
    branch_offices: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON
    has_taken_over: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON
    was_taken_over_by: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON
    audit_companies: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON
    old_names: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON
    cantonal_excerpt_web: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    translations: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON array
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
    purpose_keywords_arr: Mapped[list[str] | None] = mapped_column(ARRAY(Text), nullable=True)
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
    ai_scored_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Rule-based business model classification: 'b2b' | 'b2c' | 'b2g' | 'mixed' | NULL
    business_model: Mapped[str | None] = mapped_column(String(16), nullable=True, index=True)
    flex_scored_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Stored combined score: weighted avg(ai*0.7, web*0.2, flex*0.1) with renormalisation.
    # Written by scoring jobs whenever any component score changes. Enables indexed filtering.
    combined_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Raw JSON from Zefix API stored for reference
    zefix_raw: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    @staticmethod
    def compute_combined_score(
        ai_score: int | None,
        web_score: int | None,
        flex_score: int | None,
        w_ai: float = 0.70,
        w_web: float = 0.20,
        w_flex: float = 0.10,
    ) -> float | None:
        """Weighted average of whichever component scores are present.

        Weights renormalised so missing scores don't drag the result down.
        Returns None when all three are absent.
        """
        _WEIGHTS = ((ai_score, w_ai), (web_score, w_web), (flex_score, w_flex))
        present = [(s, w) for s, w in _WEIGHTS if s is not None]
        if not present:
            return None
        total_w = sum(w for _, w in present)
        return round(sum(s * w for s, w in present) / total_w)

    notes: Mapped[list["Note"]] = relationship("Note", back_populates="company", cascade="all, delete-orphan")  # noqa: F821
