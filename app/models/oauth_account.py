from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class OAuthAccount(Base):
    """Links a third-party OAuth identity (Google, LinkedIn, …) to a local User."""

    __tablename__ = "oauth_accounts"
    __table_args__ = (UniqueConstraint("provider", "provider_user_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    # e.g. "google" | "linkedin"
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    # Opaque user ID returned by the provider (Google "sub", LinkedIn "sub")
    provider_user_id: Mapped[str] = mapped_column(String(256), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(tz=timezone.utc),
    )

    user: Mapped["User"] = relationship("User", back_populates="oauth_accounts")
