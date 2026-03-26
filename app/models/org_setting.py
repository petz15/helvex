"""Per-org settings overrides.

Works as a second layer on top of global app_settings.
When loading a setting for an org, check here first, then fall back
to app_settings (global defaults).
"""
from sqlalchemy import ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class OrgSetting(Base):
    __tablename__ = "org_settings"
    __table_args__ = (
        UniqueConstraint("org_id", "key", name="uq_org_settings_key"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    org_id: Mapped[int] = mapped_column(Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    key: Mapped[str] = mapped_column(String(128), nullable=False)
    value: Mapped[str | None] = mapped_column(Text, nullable=True)
