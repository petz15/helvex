from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

if TYPE_CHECKING:
    from app.models.organization import Organization
    from app.models.user import User

from app.database import Base


class OrgMember(Base):
    """Join table for multi-org membership.

    A user can belong to many orgs; their active org is tracked on
    User.active_org_id (renamed from org_id in migration 0039).
    Role within a specific org lives here instead of on the User row.
    """

    __tablename__ = "org_members"
    __table_args__ = (UniqueConstraint("org_id", "user_id", name="uq_org_members_org_user"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    # owner | admin | member | viewer
    role: Mapped[str] = mapped_column(String(32), nullable=False, default="member")
    joined_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(tz=timezone.utc),
    )

    org: Mapped[Organization] = relationship("Organization", back_populates="members")
    user: Mapped[User] = relationship("User", back_populates="org_memberships")
