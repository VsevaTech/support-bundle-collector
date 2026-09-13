from datetime import timedelta

from sqlalchemy import select

from app.models import SupportLink, utcnow
from tests.conftest import create_link, meta_form


def test_support_index_renders(client):
    resp = client.get("/support")
    assert resp.status_code == 200
    assert "Create support link" in resp.text
    assert "24h" in resp.text


def test_create_link_htmx_partial_and_full_page(client):
    token, url = create_link(client, ttl_hours=24, note="ticket #1")
    assert len(token) >= 40
    assert url == f"http://support.example.test/s/{token}"

    full = client.post("/support/links", data={"ttl_hours": "4"}, follow_redirects=False)
    assert full.status_code == 200
    assert "Support link created" in full.text


def test_create_link_rejects_bad_ttl(client):
    assert client.post("/support/links", data={"ttl_hours": "0"}).status_code == 422
    assert client.post("/support/links", data={"ttl_hours": "9999"}).status_code == 422
    assert client.post("/support/links", data={"ttl_hours": "abc"}).status_code == 422


def test_ttl_is_persisted(client, db):
    token, _ = create_link(client, ttl_hours=4)
    link = db.scalar(select(SupportLink).where(SupportLink.token == token))
    delta = link.expires_at - link.created_at
    assert timedelta(hours=3, minutes=59) < delta <= timedelta(hours=4)
    assert link.status == "active"


def test_valid_link_opens_form(client):
    token, _ = create_link(client)
    resp = client.get(f"/s/{token}")
    assert resp.status_code == 200
    assert "Report a problem" in resp.text
    assert 'name="description"' in resp.text
    assert 'name="screenshot"' in resp.text
    assert "/static/collect.js" in resp.text


def test_unknown_link_is_404(client):
    resp = client.get("/s/does-not-exist")
    assert resp.status_code == 404
    assert "Link not found" in resp.text


def test_expired_link_is_rejected_on_get_and_post(client, db):
    token, _ = create_link(client, ttl_hours=1)
    link = db.scalar(select(SupportLink).where(SupportLink.token == token))
    link.expires_at = utcnow() - timedelta(seconds=1)
    db.commit()

    get = client.get(f"/s/{token}")
    assert get.status_code == 410
    assert "expired" in get.text.lower()

    post = client.post(f"/s/{token}", data=meta_form())
    assert post.status_code == 410
    assert link.incident is None

    db.refresh(link)
    assert link.status == "expired"
    index = client.get("/support")
    assert "expired" in index.text


def test_one_time_submission(client, db):
    token, _ = create_link(client)
    first = client.post(f"/s/{token}", data=meta_form())
    assert first.status_code == 200
    assert "Thank you" in first.text

    again_get = client.get(f"/s/{token}")
    assert again_get.status_code == 410
    assert "already used" in again_get.text

    again_post = client.post(f"/s/{token}", data=meta_form(description="second attempt"))
    assert again_post.status_code == 410

    link = db.scalar(select(SupportLink).where(SupportLink.token == token))
    assert link.status == "used"
    assert link.incident is not None
    assert link.incident.description == "Payment button does nothing."


def test_description_is_required(client):
    token, _ = create_link(client)
    resp = client.post(f"/s/{token}", data=meta_form(description="   "))
    assert resp.status_code == 422
    assert "Description is required" in resp.text
    # link stays usable after a validation error
    assert client.get(f"/s/{token}").status_code == 200
