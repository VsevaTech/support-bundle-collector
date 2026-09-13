"""Guards against collecting sensitive data: cookies, storage, passwords, tokens, history."""

import re
import sqlite3
from pathlib import Path

from sqlalchemy import inspect

from app.db import engine
from app.models import Incident
from tests.conftest import PNG_BYTES, create_link, meta_form

ROOT = Path(__file__).resolve().parent.parent

# Browser APIs that must never appear in customer-facing JavaScript.
FORBIDDEN_JS_APIS = [
    r"document\.cookie",
    r"\blocalStorage\b",
    r"\bsessionStorage\b",
    r"\bindexedDB\b",
    r"navigator\.credentials",
    r"PasswordCredential",
    r"window\.history\b",
    r"\bhistory\.(back|forward|go|length|state|pushState|replaceState)",
    r"navigator\.plugins",
    r"getBattery",
    r"geolocation",
    r"WebAuthn|PublicKeyCredential",
]

SENSITIVE_COLUMN_WORDS = ("cookie", "storage", "password", "token", "history", "secret", "auth")


def test_customer_js_never_touches_sensitive_browser_apis():
    src = (ROOT / "static" / "collect.js").read_text()
    for pattern in FORBIDDEN_JS_APIS:
        assert not re.search(pattern, src), f"collect.js references forbidden API: {pattern}"


def test_customer_templates_have_no_inline_scripts_or_third_party_assets():
    for name in ("customer_form.html", "link_unavailable.html", "submitted.html", "base.html"):
        html = (ROOT / "templates" / name).read_text()
        assert "<script>" not in html, f"{name} has an inline script"
        assert not re.search(r'src="https?://', html), f"{name} loads a third-party asset"


def test_incident_schema_has_no_sensitive_columns():
    columns = [c["name"] for c in inspect(engine).get_columns("incidents")]
    for col in columns:
        for word in SENSITIVE_COLUMN_WORDS:
            assert word not in col.lower(), f"suspicious column {col!r}"
    # The support-link token column exists on support_links only (it IS the capability).
    assert "token" not in columns


def test_extra_and_sensitive_form_fields_are_dropped(client, db):
    token, _ = create_link(client)
    payload = meta_form()
    payload.update(
        {
            "cookies": "session=abc123",
            "localStorage": '{"jwt":"eyJ..."}',
            "sessionStorage": "x=1",
            "password": "hunter2",
            "auth_token": "Bearer secret",
            "history": "https://bank.example/account",
        }
    )
    resp = client.post(
        f"/s/{token}",
        data=payload,
        headers={"Cookie": "customer_session=SECRET-COOKIE; auth=SECRET-TOKEN"},
    )
    assert resp.status_code == 200
    inc_id = int(re.search(r"Incident #(\d+)", resp.text).group(1))

    # Nothing sensitive in the ORM object...
    inc = db.get(Incident, inc_id)
    dumped = " ".join(str(getattr(inc, c.key)) for c in Incident.__table__.columns)
    for secret in ("abc123", "eyJ", "hunter2", "Bearer secret", "bank.example", "SECRET-"):
        assert secret not in dumped

    # ...and nothing sensitive anywhere in the raw SQLite file either.
    db_path = engine.url.database
    raw = sqlite3.connect(db_path)
    try:
        blob = " ".join(
            " ".join(map(str, row))
            for table in ("incidents", "support_links")
            for row in raw.execute(f"SELECT * FROM {table}")  # noqa: S608 (constant table names)
        )
    finally:
        raw.close()
    for secret in ("abc123", "eyJ", "hunter2", "Bearer secret", "bank.example", "SECRET-"):
        assert secret not in blob


def test_customer_form_declares_what_is_collected(client):
    token, _ = create_link(client)
    html = client.get(f"/s/{token}").text
    # Every collected field is displayed to the customer before submission.
    for field in ("Browser", "OS", "Viewport", "Timezone", "Language"):
        assert f"<th>{field}</th>" in html
    # URL sharing is opt-in and off by default.
    assert 'name="share_url"' in html
    assert re.search(r'name="share_url"[^>]*checked', html) is None
    assert "Not collected: cookies" in html


def test_customer_response_sets_no_cookies_and_has_security_headers(client):
    token, _ = create_link(client)
    resp = client.get(f"/s/{token}")
    assert "set-cookie" not in resp.headers
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert resp.headers["referrer-policy"] == "no-referrer"
    assert "script-src 'self'" in resp.headers["content-security-policy"]
    resp = client.post(
        f"/s/{token}", data=meta_form(), files={"screenshot": ("s.png", PNG_BYTES, "image/png")}
    )
    assert "set-cookie" not in resp.headers


def test_metadata_is_length_limited_and_single_line(client, db):
    token, _ = create_link(client)
    resp = client.post(
        f"/s/{token}",
        data=meta_form(browser="X" * 500, timezone="Europe/\nBerlin"),
    )
    inc = db.get(Incident, int(re.search(r"Incident #(\d+)", resp.text).group(1)))
    assert len(inc.browser) == 120
    assert "\n" not in inc.timezone


def test_csp_allows_cdn_only_on_support_pages(client):
    token, _ = create_link(client)
    customer_csp = client.get(f"/s/{token}").headers["content-security-policy"]
    support_csp = client.get("/support").headers["content-security-policy"]
    assert "cdn.jsdelivr.net" not in customer_csp
    assert "script-src 'self' https://cdn.jsdelivr.net" in support_csp
