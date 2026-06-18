"""Company-to-Purpose-Keyword many-to-many relationship."""
from sqlalchemy import Column, Integer, String, ForeignKey, Index

from app.database import Base


class CompanyPurposeKeyword(Base):
    __tablename__ = "company_purpose_keywords"

    id = Column(Integer, primary_key=True)
    company_id = Column(Integer, ForeignKey("companies.id", ondelete="CASCADE"), nullable=False)
    keyword = Column(String(255), nullable=False)

    __table_args__ = (
        Index("ix_company_purpose_keywords_keyword", "keyword"),
        Index("ix_company_purpose_keywords_company_id", "company_id"),
    )
