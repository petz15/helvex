"""Company-to-TFIDF-Cluster many-to-many relationship."""
from sqlalchemy import Column, Integer, String, ForeignKey, Index
from sqlalchemy.orm import relationship

from app.database import Base


class CompanyTfidfCluster(Base):
    __tablename__ = "company_tfidf_clusters"

    id = Column(Integer, primary_key=True)
    company_id = Column(Integer, ForeignKey("companies.id", ondelete="CASCADE"), nullable=False)
    cluster = Column(String(255), nullable=False)

    __table_args__ = (
        Index("ix_company_tfidf_clusters_cluster", "cluster"),
        Index("ix_company_tfidf_clusters_company_id", "company_id"),
    )
