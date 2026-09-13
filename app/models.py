from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class SupportLink(Base):
    __tablename__ = "support_links"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    token: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    note: Mapped[str] = mapped_column(String(200), default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    incident: Mapped["Incident | None"] = relationship(back_populates="link", uselist=False)

    @property
    def is_expired(self) -> bool:
        return utcnow() >= self.expires_at

    @property
    def is_used(self) -> bool:
        return self.used_at is not None

    @property
    def status(self) -> str:
        if self.is_used:
            return "used"
        if self.is_expired:
            return "expired"
        return "active"


class Incident(Base):
    """A structured support bundle submitted by a customer.

    Deliberately contains ONLY safe, explicitly collected fields.
    No cookies, storage, tokens, history or credentials are ever persisted.
    """

    __tablename__ = "incidents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    link_id: Mapped[int] = mapped_column(
        ForeignKey("support_links.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

    browser: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    os: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    viewport: Mapped[str] = mapped_column(String(40), default="", nullable=False)
    timezone: Mapped[str] = mapped_column(String(80), default="", nullable=False)
    language: Mapped[str] = mapped_column(String(40), default="", nullable=False)
    page_url: Mapped[str] = mapped_column(String(2048), default="", nullable=False)
    user_agent: Mapped[str] = mapped_column(String(512), default="", nullable=False)

    description: Mapped[str] = mapped_column(Text, nullable=False)
    screenshot_name: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    screenshot_path: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    screenshot_mime: Mapped[str] = mapped_column(String(64), default="", nullable=False)

    link: Mapped[SupportLink] = relationship(back_populates="incident")
