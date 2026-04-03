from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class ClusterRegistry(Base):
    __tablename__ = "cluster_registry"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    # Stable human-readable name used as tfidf_cluster value across runs
    canonical_name: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    # JSON array of top terms, e.g. ["software", "entwicklung", "cloud"]
    top_terms: Mapped[str] = mapped_column(Text, nullable=False)
    # False when a previously active cluster is no longer produced by the pipeline
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
