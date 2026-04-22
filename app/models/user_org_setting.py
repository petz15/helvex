"""Per-user per-org settings (e.g. notification preferences).

Each row is scoped to a (user_id, org_id) pair. Notification keys stored here
take precedence over the org-level OrgSetting fallback.
"""
from sqlalchemy import ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class UserOrgSetting(Base):
    __tablename__ = "user_org_settings"
    __table_args__ = (
        UniqueConstraint("user_id", "org_id", "key", name="uq_user_org_settings_key"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    org_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    key: Mapped[str] = mapped_column(String(128), nullable=False)
    value: Mapped[str | None] = mapped_column(Text, nullable=True)
