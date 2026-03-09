from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import String, Text

from app.database import Base


class AppSetting(Base):
    """Simple key-value store for runtime-configurable application settings."""

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False, default="")
