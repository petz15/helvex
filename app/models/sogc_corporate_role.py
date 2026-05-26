from __future__ import annotations

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import mapped_column

from app.database import Base


class SogcCorporateRole(Base):
    """A corporate entity (company) appearing in an SOGC change in a non-auditor role.

    Examples: holding company as Gesellschafterin, parent AG as Aktionärin,
    another GmbH as board member via a nominee structure.

    Detected by presence of a CHE-xxx.xxx.xxx number in the raw_excerpt
    that is NOT flagged as a revision-body (auditor) excerpt.
    """

    __tablename__ = "sogc_corporate_roles"

    id = mapped_column(Integer, primary_key=True)

    sogc_change_id = mapped_column(
        Integer, ForeignKey("sogc_changes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sogc_publication_id = mapped_column(
        Integer, ForeignKey("sogc_publications.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # Corporate entity that holds the role
    entity_name = mapped_column(String(512), nullable=True)
    entity_name_normalized = mapped_column(String(512), nullable=True, index=True)
    entity_che = mapped_column(String(20), nullable=True, index=True)
    entity_location = mapped_column(String(256), nullable=True)
    entity_legal_form = mapped_column(String(256), nullable=True)

    # Company they appear in
    company_uid = mapped_column(
        String(20), ForeignKey("companies.uid", ondelete="SET NULL"), nullable=True, index=True
    )
    company_id = mapped_column(
        Integer, ForeignKey("companies.id", ondelete="SET NULL"), nullable=True, index=True
    )
    company_name = mapped_column(String(512), nullable=True)

    # Role details
    role = mapped_column(String(512), nullable=True)
    role_details = mapped_column(Text, nullable=True)   # share counts, capital amounts, etc.
    change_subtype = mapped_column(String(32), nullable=True, index=True)  # new_appointment | capital_change | role_change | resignation | other

    # Publication metadata
    raw_excerpt = mapped_column(Text, nullable=True)
    pub_date = mapped_column(String(20), nullable=True, index=True)
    change_type = mapped_column(String(40), nullable=True)
    is_current = mapped_column(Boolean, nullable=True, default=True)

    created_at = mapped_column(DateTime, server_default=func.now())
