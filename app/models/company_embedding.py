from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class CompanyEmbedding(Base):
    """768-dim L2-normalised embedding for a company's purpose text.

    embedding_type is either 'purpose_full' (raw purpose) or
    'purpose_clean' (boilerplate-stripped purpose).

    Vector operations are done via raw SQL (pgvector) — this model handles
    CRUD and FK constraints only.
    """

    __tablename__ = "company_embeddings"
    __table_args__ = (
        UniqueConstraint("company_id", "embedding_type", name="company_embeddings_company_type_key"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    embedding_type: Mapped[str] = mapped_column(String(32), nullable=False)
    # Vector column is managed via raw SQL; ORM reads it as None (not needed for queries).
    lang: Mapped[str | None] = mapped_column(String(8), nullable=True)
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
