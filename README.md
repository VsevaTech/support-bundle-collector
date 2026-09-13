# Support Bundle Collector

[![CI](https://github.com/VsevaTech/support-bundle-collector/actions/workflows/ci.yml/badge.svg)](https://github.com/VsevaTech/support-bundle-collector/actions/workflows/ci.yml)

One-time support links that replace the "what browser? which OS? send a screenshot?" ping-pong with a
single structured incident bundle.

```text
support creates one-time link
→ sends it to customer
→ customer opens link
→ browser diagnostics collected (shown to the customer before sending)
→ customer adds description + screenshot
→ support receives structured incident bundle
```

What support gets:

```text
Incident #82

Browser: Chrome 128
OS: Windows 11
Viewport: 1920x1080
Timezone: Europe/Berlin
Language: de-DE
URL: https://shop.example.com/checkout

Problem:
Payment button does nothing.

Attachment:
screenshot.png
```

## Features

- Support page: create a link with a TTL (1h – 7 days), optional internal note, copy button (HTMX, no reload).
- Links are **single-use** and **expire** after the TTL; expired/used links return `410 Gone` with a friendly page.
- Customer form: description (required), screenshot (PNG/JPEG/GIF/WebP, size-limited, magic-bytes checked),
  automatic diagnostics rendered in a table *before* submission.
- Page URL is collected **only** when the customer ticks the checkbox; the value is visible and editable.
- Support UI: incident list, incident detail with inline screenshot, plain-text bundle (`/support/incidents/{id}.txt`).
- Server-side User-Agent fallback when the browser sends no client hints.
- Stack: Python 3.11+, FastAPI, SQLAlchemy 2, SQLite, Jinja2 + HTMX (support UI only, loaded from jsDelivr with SRI), vanilla JS, Docker.

## Quick start

### Docker (recommended)

```bash
cp .env.example .env          # optional; set SBC_SUPPORT_ACCESS_KEY and SBC_BASE_URL
docker compose up --build
open http://localhost:8000/support
```

Data (SQLite DB + screenshots) lives in the `sbc-data` volume at `/data`. The container runs as a non-root user
and exposes a health check on `/healthz`.

### Local

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
uvicorn app.main:app --reload
```

The DB and uploads are created under `./data/`.

## Demo flow

1. Open `http://localhost:8000/support`, pick TTL `24h`, click **Create link**, copy the URL.
2. Open the URL in another browser / private window (the "customer").
3. The form shows exactly what will be sent (browser, OS, viewport, timezone, language). Type a description,
   attach a screenshot, optionally tick *share the page address*, click **Send to support**.
4. Reload the customer link — it is now `410 already used`.
5. Back in the support UI open **Incidents → #N**: the bundle, the screenshot and a `.txt` download.

`tests/e2e/demo_flow.py` performs exactly this scenario in a real Chromium via Playwright and is part of CI.

## Configuration

All settings are environment variables prefixed with `SBC_` (see `.env.example`):

| Variable                 | Default                     | Purpose                                                       |
|--------------------------|-----------------------------|---------------------------------------------------------------|
| `SBC_BASE_URL`           | *(derived from request)*    | Public origin used in customer links                          |
| `SBC_SUPPORT_ACCESS_KEY` | *(empty = open)*            | Shared secret for `/support/*`; open `/support?key=…` once    |
| `SBC_DEFAULT_TTL_HOURS`  | `24`                        | Default TTL                                                   |
| `SBC_MAX_TTL_HOURS`      | `168`                       | Upper bound for TTL                                           |
| `SBC_MAX_UPLOAD_MB`      | `8`                         | Screenshot size limit                                         |
| `SBC_DATABASE_URL`       | `sqlite:///./data/sbc.db`   | SQLAlchemy URL (`sqlite:////data/sbc.db` in Docker)           |
| `SBC_UPLOAD_DIR`         | `./data/uploads`            | Screenshot storage (`/data/uploads` in Docker)                |

## Privacy & Security

**Collected (and shown to the customer before sending):**

| Field     | Source                                                                          |
|-----------|---------------------------------------------------------------------------------|
| Browser   | UA Client Hints (`navigator.userAgentData`) with `navigator.userAgent` fallback  |
| OS        | Client Hints `platform` + `platformVersion` (distinguishes Windows 10/11)        |
| Viewport  | `window.innerWidth × innerHeight` (+ device pixel ratio)                         |
| Timezone  | `Intl.DateTimeFormat().resolvedOptions().timeZone`                               |
| Language  | `navigator.language`                                                             |
| URL       | **Opt-in only.** Pre-filled from `document.referrer` / `?from=` and editable     |
| User-Agent| Request header, stored verbatim for the support team                             |

Plus the free-text description and the screenshot the customer chooses to upload.

**Never collected — by design and enforced by tests (`tests/test_privacy.py`):**

- cookies (`document.cookie`, request `Cookie` header is never read on customer routes)
- `localStorage`, `sessionStorage`, IndexedDB
- passwords, credentials (`navigator.credentials`, WebAuthn)
- auth tokens of any kind
- browser history (`window.history`)
- geolocation, plugins, battery and other fingerprinting APIs

How this is enforced:

- `static/collect.js` is scanned in tests for forbidden API references.
- The server accepts only a whitelist of metadata fields; any extra form field (e.g. `cookies=…`, `password=…`)
  is discarded and a test verifies the raw SQLite file contains none of it.
- The `incidents` table has no column that could hold such data.
- Customer pages set no cookies and load no third-party assets (htmx is used only on the support UI, pinned with an SRI hash; CSP allows the CDN only under `/support`).
- Strict headers on every response: `Content-Security-Policy` (`default-src 'self'`, no inline scripts/styles),
  `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`.

Other safeguards:

- Link tokens are 256-bit `secrets.token_urlsafe(32)`; a link is invalidated at first successful submission.
- TTL is enforced server-side on both `GET` and `POST`; bounds are configurable.
- Screenshots are validated by magic bytes (not by extension or `Content-Type`), size-limited, stored under a random
  file name outside the web root and served only through the support UI with `nosniff`.
- The original file name is sanitised (no path separators, HTML or control characters) and kept only for display.
- Metadata values are trimmed to fixed maximum lengths and collapsed to a single line.
- Description is limited to 5000 characters and always HTML-escaped by Jinja2.
- The container runs as an unprivileged user (`uid 10001`).

**Known limitations of the MVP (read before exposing to the internet):**

- `/support/*` is protected only by the optional shared secret `SBC_SUPPORT_ACCESS_KEY`. Put it behind your SSO /
  reverse-proxy auth for real deployments.
- Set `SBC_BASE_URL` in production; otherwise the link origin is derived from the request `Host` header.
- No rate limiting on the customer endpoint (tokens are unguessable, but add a proxy-level limit if needed).
- No retention policy: incidents and screenshots are kept until deleted manually.

## Development

```bash
pip install -r requirements-dev.txt
ruff check . && ruff format --check .
pytest                                     # 26 unit/integration tests
pip install playwright && playwright install chromium
uvicorn app.main:app & python tests/e2e/demo_flow.py   # browser demo flow
```

Tests cover: valid link, expired link, one-time submission, screenshot upload (valid, oversized, wrong type,
malicious filename), browser metadata (client-side values and server-side UA fallback), URL opt-in, and the
absence of sensitive data collection.

## Project layout

```text
app/            FastAPI app: config, db, models, services, routes
templates/      Jinja2 templates (support UI + customer form)
static/         collect.js (diagnostics), support.js, app.css
tests/          pytest suite + tests/e2e/demo_flow.py (Playwright)
Dockerfile, docker-compose.yml, .env.example
.github/workflows/ci.yml   ruff + pytest, docker compose smoke, browser e2e
```

## License

MIT — see [LICENSE](LICENSE).
