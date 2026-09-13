"""Domain logic: links, incidents, screenshot handling, UA parsing."""

from __future__ import annotations

import re
import secrets
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Incident, SupportLink, utcnow

# Only these fields are ever accepted from the browser. Anything else is dropped.
ALLOWED_META_FIELDS = ("browser", "os", "viewport", "timezone", "language", "page_url")
META_MAX_LEN = {
    "browser": 120,
    "os": 120,
    "viewport": 40,
    "timezone": 80,
    "language": 40,
    "page_url": 2048,
}

IMAGE_SIGNATURES: dict[str, tuple[bytes, str]] = {
    "png": (b"\x89PNG\r\n\x1a\n", "image/png"),
    "jpg": (b"\xff\xd8\xff", "image/jpeg"),
    "gif": (b"GIF8", "image/gif"),
}


class LinkUnavailableError(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason  # "not_found" | "expired" | "used"


class InvalidScreenshotError(ValueError):
    pass


# ---------------------------------------------------------------- links


def create_link(db: Session, ttl_hours: int, note: str = "") -> SupportLink:
    settings = get_settings()
    if not 1 <= ttl_hours <= settings.max_ttl_hours:
        raise ValueError(f"ttl_hours must be between 1 and {settings.max_ttl_hours}")
    link = SupportLink(
        token=secrets.token_urlsafe(32),
        note=note.strip()[:200],
        expires_at=utcnow() + timedelta(hours=ttl_hours),
    )
    db.add(link)
    db.commit()
    db.refresh(link)
    return link


def get_link(db: Session, token: str) -> SupportLink | None:
    return db.scalar(select(SupportLink).where(SupportLink.token == token))


def get_usable_link(db: Session, token: str) -> SupportLink:
    """Return the link only if it can still accept a submission."""
    link = get_link(db, token)
    if link is None:
        raise LinkUnavailableError("not_found")
    if link.is_used:
        raise LinkUnavailableError("used")
    if link.is_expired:
        raise LinkUnavailableError("expired")
    return link


def list_links(db: Session, limit: int = 50) -> list[SupportLink]:
    return list(db.scalars(select(SupportLink).order_by(SupportLink.id.desc()).limit(limit)))


# ---------------------------------------------------------------- incidents


@dataclass
class Screenshot:
    original_name: str
    content: bytes


def sniff_image(content: bytes) -> tuple[str, str]:
    """Return (ext, mime) from magic bytes or raise InvalidScreenshotError."""
    if content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return "webp", "image/webp"
    for ext, (sig, mime) in IMAGE_SIGNATURES.items():
        if content.startswith(sig):
            return ext, mime
    raise InvalidScreenshotError("Only PNG, JPEG, GIF or WebP images are accepted")


def _safe_display_name(name: str, ext: str) -> str:
    base = Path(name or "").name
    base = re.sub(r"[^\w.\- ]+", "_", base).strip(" .")[:120]
    if not base:
        base = f"screenshot.{ext}"
    return base


def sanitize_meta(raw: dict[str, str | None]) -> dict[str, str]:
    """Whitelist + trim browser metadata. Unknown keys are discarded."""
    out: dict[str, str] = {}
    for key in ALLOWED_META_FIELDS:
        value = (raw.get(key) or "").strip()
        value = value.replace("\r", " ").replace("\n", " ")
        out[key] = value[: META_MAX_LEN[key]]
    return out


def submit_incident(
    db: Session,
    link: SupportLink,
    description: str,
    meta: dict[str, str],
    user_agent: str,
    screenshot: Screenshot | None,
) -> Incident:
    settings = get_settings()
    description = description.strip()
    if not description:
        raise ValueError("Description is required")
    if len(description) > 5000:
        raise ValueError("Description is too long (max 5000 characters)")

    # Re-check atomically-ish: the link must still be usable right before writing.
    if link.is_used:
        raise LinkUnavailableError("used")
    if link.is_expired:
        raise LinkUnavailableError("expired")

    stored_name = display_name = mime = ""
    if screenshot is not None and screenshot.content:
        if len(screenshot.content) > settings.max_upload_bytes:
            raise InvalidScreenshotError(f"Screenshot exceeds {settings.max_upload_mb} MB")
        ext, mime = sniff_image(screenshot.content)
        display_name = _safe_display_name(screenshot.original_name, ext)
        stored_name = f"{secrets.token_hex(16)}.{ext}"
        settings.upload_dir.mkdir(parents=True, exist_ok=True)
        (settings.upload_dir / stored_name).write_bytes(screenshot.content)

    meta = sanitize_meta(meta)
    if not meta["browser"] or not meta["os"]:
        ua_browser, ua_os = parse_user_agent(user_agent)
        meta["browser"] = meta["browser"] or ua_browser
        meta["os"] = meta["os"] or ua_os

    incident = Incident(
        link=link,
        description=description,
        user_agent=(user_agent or "")[:512],
        screenshot_name=display_name,
        screenshot_path=stored_name,
        screenshot_mime=mime,
        **meta,
    )
    link.used_at = utcnow()
    db.add(incident)
    db.commit()
    db.refresh(incident)
    return incident


def get_incident(db: Session, incident_id: int) -> Incident | None:
    return db.get(Incident, incident_id)


def list_incidents(db: Session, limit: int = 100) -> list[Incident]:
    return list(db.scalars(select(Incident).order_by(Incident.id.desc()).limit(limit)))


def screenshot_file(incident: Incident) -> Path | None:
    if not incident.screenshot_path:
        return None
    path = get_settings().upload_dir / Path(incident.screenshot_path).name
    return path if path.is_file() else None


def render_bundle_text(incident: Incident) -> str:
    lines = [
        f"Incident #{incident.id}",
        "",
        f"Browser: {incident.browser or '-'}",
        f"OS: {incident.os or '-'}",
        f"Viewport: {incident.viewport or '-'}",
        f"Timezone: {incident.timezone or '-'}",
        f"Language: {incident.language or '-'}",
        f"URL: {incident.page_url or '(not shared)'}",
        "",
        "Problem:",
        incident.description,
        "",
        "Attachment:",
        incident.screenshot_name or "(none)",
        "",
        f"Submitted: {incident.created_at:%Y-%m-%d %H:%M:%S} UTC",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------- UA fallback

_BROWSER_RULES: tuple[tuple[str, str], ...] = (
    (r"Edg(?:e|A|iOS)?/([\d.]+)", "Edge"),
    (r"OPR/([\d.]+)", "Opera"),
    (r"SamsungBrowser/([\d.]+)", "Samsung Internet"),
    (r"YaBrowser/([\d.]+)", "Yandex Browser"),
    (r"Firefox/([\d.]+)", "Firefox"),
    (r"Chrome/([\d.]+)", "Chrome"),
    (r"CriOS/([\d.]+)", "Chrome"),
    (r"Version/([\d.]+).*Safari/", "Safari"),
)

_OS_RULES: tuple[tuple[str, str], ...] = (
    (r"Windows NT 10\.0", "Windows 10/11"),
    (r"Windows NT 6\.3", "Windows 8.1"),
    (r"Windows NT 6\.1", "Windows 7"),
    (r"Android ([\d.]+)", "Android {0}"),
    (r"iPhone OS ([\d_.]+)", "iOS {0}"),
    (r"iPad; CPU OS ([\d_.]+)", "iPadOS {0}"),
    (r"Mac OS X ([\d_.]+)", "macOS {0}"),
    (r"CrOS", "ChromeOS"),
    (r"Linux", "Linux"),
)


def parse_user_agent(ua: str) -> tuple[str, str]:
    """Conservative UA parser used only as a fallback when the client sent nothing."""
    ua = ua or ""
    browser = "Unknown"
    for pattern, name in _BROWSER_RULES:
        m = re.search(pattern, ua)
        if m:
            browser = f"{name} {m.group(1).split('.')[0]}"
            break
    os_name = "Unknown"
    for pattern, name in _OS_RULES:
        m = re.search(pattern, ua)
        if m:
            version = m.group(1).replace("_", ".") if m.groups() else ""
            os_name = name.format(version)
            break
    return browser, os_name
