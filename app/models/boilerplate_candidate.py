from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class BoilerplateCandidate(Base):
    """Auto-mined boilerplate term or phrase candidate.

    Candidates are generated automatically by three mechanisms:
      - corpus_mined: terms with high cross-cluster entropy (spread uniformly, no discriminative power)
      - cluster_feedback: terms appearing in top labels of >30% of clusters after a pipeline run
      - llm_seeded: Claude-classified as BOILERPLATE during the one-time bootstrap pass

    Candidates with confidence >= auto_promote_threshold are promoted to
    boilerplate_patterns automatically. Others queue for manual review.
    """

    __tablename__ = "boilerplate_candidates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    term: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    # How often this term appears in the corpus (fraction of documents, 0.0–1.0)
    doc_frequency: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Shannon entropy of this term across cluster centroids (high = generic)
    cluster_entropy: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Fraction of cluster labels this term appears in (0.0–1.0)
    cluster_label_frequency: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Overall confidence that this is boilerplate (0.0–1.0)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Source: 'corpus_mined' | 'cluster_feedback' | 'llm_seeded'
    source_type: Mapped[str] = mapped_column(String(32), nullable=False, default="corpus_mined")
    # Language hint if detected (de/fr/it/all)
    language: Mapped[str | None] = mapped_column(String(8), nullable=True)
    # LLM classification if available: 'BOILERPLATE' | 'SIGNAL' | 'AMBIGUOUS'
    llm_label: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # Whether promoted to boilerplate_patterns (converted to active regex)
    promoted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # ID of the boilerplate_pattern row created on promotion (NULL if not promoted)
    promoted_pattern_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
