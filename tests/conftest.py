import os
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

# Isolated storage for the whole test session; must be set before the app is imported.
_TMP = Path(tempfile.mkdtemp(prefix="sbc-tests-"))
os.environ["SBC_DATABASE_URL"] = f"sqlite:///{_TMP / 'test.db'}"
os.environ["SBC_UPLOAD_DIR"] = str(_TMP / "uploads")
os.environ["SBC_BASE_URL"] = "http://support.example.test"
os.environ["SBC_MAX_UPLOAD_MB"] = "1"
os.environ.pop("SBC_SUPPORT_ACCESS_KEY", None)

from fastapi.testclient import TestClient  # noqa: E402

from app.db import SessionLocal, init_db  # noqa: E402
from app.main import app  # noqa: E402

# 1x1 PNG
PNG_BYTES = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d4944415478"
    "9c6360f8cfc0000002010100c9fe92ef0000000049454e44ae426082"
)

CHROME_WIN_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/128.0.0.0 Safari/537.36"
)


@pytest.fixture(scope="session")
def client() -> Iterator[TestClient]:
    with TestClient(app, base_url="http://support.example.test") as c:
        yield c


@pytest.fixture
def db() -> Iterator:
    init_db()
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def upload_dir() -> Path:
    return Path(os.environ["SBC_UPLOAD_DIR"])


def create_link(client: TestClient, ttl_hours: int = 24, note: str = "") -> tuple[str, str]:
    """Create a link through the support UI; returns (token, url)."""
    resp = client.post(
        "/support/links",
        data={"ttl_hours": str(ttl_hours), "note": note},
        headers={"HX-Request": "true"},
    )
    assert resp.status_code == 200, resp.text
    marker = "http://support.example.test/s/"
    start = resp.text.index(marker)
    url = resp.text[start : resp.text.index('"', start)]
    return url.rsplit("/", 1)[1], url


def meta_form(**overrides: str) -> dict[str, str]:
    base = {
        "description": "Payment button does nothing.",
        "browser": "Chrome 128",
        "os": "Windows 11",
        "viewport": "1920x1080",
        "timezone": "Europe/Berlin",
        "language": "de-DE",
        "page_url": "https://shop.example.com/checkout",
        "share_url": "on",
    }
    base.update(overrides)
    return base
