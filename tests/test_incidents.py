import re

from app.models import Incident
from app.services import parse_user_agent
from tests.conftest import CHROME_WIN_UA, PNG_BYTES, create_link, meta_form


def _incident_id(html: str) -> int:
    return int(re.search(r"Incident #(\d+)", html).group(1))


def test_browser_metadata_is_stored_and_rendered(client, db):
    token, _ = create_link(client, note="ACME")
    resp = client.post(f"/s/{token}", data=meta_form(), headers={"User-Agent": CHROME_WIN_UA})
    assert resp.status_code == 200
    incident_id = _incident_id(resp.text)

    inc = db.get(Incident, incident_id)
    assert inc.browser == "Chrome 128"
    assert inc.os == "Windows 11"
    assert inc.viewport == "1920x1080"
    assert inc.timezone == "Europe/Berlin"
    assert inc.language == "de-DE"
    assert inc.page_url == "https://shop.example.com/checkout"
    assert inc.user_agent == CHROME_WIN_UA

    detail = client.get(f"/support/incidents/{incident_id}")
    assert detail.status_code == 200
    for value in ("Chrome 128", "Windows 11", "1920x1080", "Europe/Berlin", "de-DE", "ACME"):
        assert value in detail.text
    assert "https://shop.example.com/checkout" in detail.text

    txt = client.get(f"/support/incidents/{incident_id}.txt")
    assert txt.status_code == 200
    assert txt.text.startswith(f"Incident #{incident_id}\n")
    assert "Browser: Chrome 128\nOS: Windows 11\nViewport: 1920x1080" in txt.text
    assert "Problem:\nPayment button does nothing." in txt.text
    assert "Attachment:\n(none)" in txt.text

    listing = client.get("/support/incidents")
    assert f"#{incident_id}" in listing.text


def test_url_is_only_stored_with_explicit_opt_in(client, db):
    token, _ = create_link(client)
    resp = client.post(f"/s/{token}", data=meta_form(share_url=""))
    inc = db.get(Incident, _incident_id(resp.text))
    assert inc.page_url == ""
    detail = client.get(f"/support/incidents/{inc.id}")
    assert "not shared" in detail.text


def test_server_side_ua_fallback_when_client_sends_nothing(client, db):
    token, _ = create_link(client)
    resp = client.post(
        f"/s/{token}",
        data=meta_form(browser="", os=""),
        headers={"User-Agent": CHROME_WIN_UA},
    )
    inc = db.get(Incident, _incident_id(resp.text))
    assert inc.browser == "Chrome 128"
    assert inc.os == "Windows 10/11"


def test_parse_user_agent_variants():
    ff = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:129.0) Gecko/20100101 Firefox/129.0"
    assert parse_user_agent(ff) == ("Firefox 129", "macOS 10.15")
    edge = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/127.0.0.0 Safari/537.36 Edg/127.0.2651.74"
    )
    assert parse_user_agent(edge) == ("Edge 127", "Windows 10/11")
    safari = (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 "
        "(KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1"
    )
    assert parse_user_agent(safari) == ("Safari 17", "iOS 17.5")
    assert parse_user_agent("") == ("Unknown", "Unknown")


def test_screenshot_upload_is_stored_and_served(client, db, upload_dir):
    token, _ = create_link(client)
    resp = client.post(
        f"/s/{token}",
        data=meta_form(),
        files={"screenshot": ("my screenshot.png", PNG_BYTES, "image/png")},
    )
    assert resp.status_code == 200
    inc = db.get(Incident, _incident_id(resp.text))

    assert inc.screenshot_name == "my screenshot.png"
    assert inc.screenshot_mime == "image/png"
    assert inc.screenshot_path.endswith(".png")
    assert inc.screenshot_path != inc.screenshot_name  # stored under a random name
    assert (upload_dir / inc.screenshot_path).read_bytes() == PNG_BYTES

    shot = client.get(f"/support/incidents/{inc.id}/screenshot")
    assert shot.status_code == 200
    assert shot.headers["content-type"] == "image/png"
    assert shot.content == PNG_BYTES

    detail = client.get(f"/support/incidents/{inc.id}")
    assert "my screenshot.png" in detail.text
    assert f"/support/incidents/{inc.id}/screenshot" in detail.text
    txt = client.get(f"/support/incidents/{inc.id}.txt").text
    assert "Attachment:\nmy screenshot.png" in txt


def test_screenshot_filename_is_sanitized(client, db):
    token, _ = create_link(client)
    resp = client.post(
        f"/s/{token}",
        data=meta_form(),
        files={"screenshot": ("../../etc/passwd<script>.png", PNG_BYTES, "image/png")},
    )
    inc = db.get(Incident, _incident_id(resp.text))
    assert "/" not in inc.screenshot_path and ".." not in inc.screenshot_path
    assert "<" not in inc.screenshot_name and "/" not in inc.screenshot_name


def test_non_image_screenshot_is_rejected(client):
    token, _ = create_link(client)
    resp = client.post(
        f"/s/{token}",
        data=meta_form(),
        files={"screenshot": ("evil.png", b"<html>not an image</html>", "image/png")},
    )
    assert resp.status_code == 422
    assert "Only PNG, JPEG, GIF or WebP" in resp.text
    # link is still usable
    assert client.get(f"/s/{token}").status_code == 200


def test_oversized_screenshot_is_rejected(client):
    token, _ = create_link(client)
    big = PNG_BYTES + b"\0" * (1024 * 1024 + 10)  # > SBC_MAX_UPLOAD_MB=1
    resp = client.post(
        f"/s/{token}", data=meta_form(), files={"screenshot": ("big.png", big, "image/png")}
    )
    assert resp.status_code == 422
    assert "exceeds" in resp.text


def test_missing_incident_and_screenshot_404(client):
    assert client.get("/support/incidents/999999").status_code == 404
    assert client.get("/support/incidents/999999/screenshot").status_code == 404
    token, _ = create_link(client)
    resp = client.post(f"/s/{token}", data=meta_form())
    inc_id = _incident_id(resp.text)
    assert client.get(f"/support/incidents/{inc_id}/screenshot").status_code == 404
