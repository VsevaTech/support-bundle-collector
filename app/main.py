from __future__ import annotations

import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app import services
from app.config import Settings, get_settings
from app.db import get_db, init_db

BASE_DIR = Path(__file__).resolve().parent.parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

SUPPORT_COOKIE = "sbc_support_key"


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


app = FastAPI(title="Support Bundle Collector", lifespan=lifespan, docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    # Customer pages are fully self-hosted. Only the support UI may load htmx from the CDN.
    script_src = "'self'"
    if request.url.path.startswith("/support"):
        script_src += " https://cdn.jsdelivr.net"
    response.headers.setdefault(
        "Content-Security-Policy",
        f"default-src 'self'; img-src 'self' blob: data:; script-src {script_src}; "
        "style-src 'self'; frame-ancestors 'none'; form-action 'self'",
    )
    return response


# ---------------------------------------------------------------- helpers


def public_base_url(request: Request, settings: Settings) -> str:
    if settings.base_url:
        return settings.base_url.rstrip("/")
    return str(request.base_url).rstrip("/")


def require_support_access(request: Request, settings: Settings = Depends(get_settings)) -> None:
    """Optional shared-secret gate for /support pages (SBC_SUPPORT_ACCESS_KEY)."""
    if not settings.support_access_key:
        return
    presented = request.query_params.get("key") or request.cookies.get(SUPPORT_COOKIE) or ""
    if not secrets.compare_digest(presented, settings.support_access_key):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Support access key required")


def _remember_key(request: Request, response, settings: Settings) -> None:
    if settings.support_access_key and request.query_params.get("key"):
        response.set_cookie(
            SUPPORT_COOKIE,
            settings.support_access_key,
            httponly=True,
            samesite="strict",
            max_age=12 * 3600,
        )


SupportDep = Depends(require_support_access)


def link_url(request: Request, settings: Settings, token: str) -> str:
    return f"{public_base_url(request, settings)}/s/{token}"


# ---------------------------------------------------------------- misc


@app.get("/healthz", response_class=PlainTextResponse)
def healthz() -> str:
    return "ok"


@app.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    return RedirectResponse("/support", status_code=status.HTTP_302_FOUND)


# ---------------------------------------------------------------- support UI


@app.get("/support", response_class=HTMLResponse, dependencies=[SupportDep])
def support_index(
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    links = services.list_links(db)
    response = templates.TemplateResponse(
        request,
        "support_index.html",
        {
            "links": links,
            "settings": settings,
            "link_url": lambda t: link_url(request, settings, t),
        },
    )
    _remember_key(request, response, settings)
    return response


@app.post("/support/links", response_class=HTMLResponse, dependencies=[SupportDep])
def support_create_link(
    request: Request,
    ttl_hours: Annotated[int, Form()] = 24,
    note: Annotated[str, Form()] = "",
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    try:
        link = services.create_link(db, ttl_hours=ttl_hours, note=note)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    ctx = {"link": link, "url": link_url(request, settings, link.token)}
    if request.headers.get("HX-Request"):
        return templates.TemplateResponse(request, "partials/link_created.html", ctx)
    return templates.TemplateResponse(request, "link_created.html", ctx)


@app.get("/support/incidents", response_class=HTMLResponse, dependencies=[SupportDep])
def support_incidents(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse(
        request, "incidents_list.html", {"incidents": services.list_incidents(db)}
    )


@app.get(
    "/support/incidents/{incident_id}.txt",
    response_class=PlainTextResponse,
    dependencies=[SupportDep],
)
def support_incident_text(incident_id: int, db: Session = Depends(get_db)) -> str:
    incident = services.get_incident(db, incident_id)
    if incident is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Incident not found")
    return services.render_bundle_text(incident)


@app.get("/support/incidents/{incident_id}", response_class=HTMLResponse, dependencies=[SupportDep])
def support_incident_detail(request: Request, incident_id: int, db: Session = Depends(get_db)):
    incident = services.get_incident(db, incident_id)
    if incident is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Incident not found")
    return templates.TemplateResponse(
        request,
        "incident_detail.html",
        {"incident": incident, "bundle_text": services.render_bundle_text(incident)},
    )


@app.get("/support/incidents/{incident_id}/screenshot", dependencies=[SupportDep])
def support_incident_screenshot(incident_id: int, db: Session = Depends(get_db)):
    incident = services.get_incident(db, incident_id)
    if incident is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Incident not found")
    path = services.screenshot_file(incident)
    if path is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No screenshot attached")
    return FileResponse(
        path,
        media_type=incident.screenshot_mime or "application/octet-stream",
        filename=incident.screenshot_name or path.name,
        content_disposition_type="inline",
    )


# ---------------------------------------------------------------- customer flow


def _unavailable(request: Request, reason: str) -> HTMLResponse:
    code = {
        "not_found": status.HTTP_404_NOT_FOUND,
        "expired": status.HTTP_410_GONE,
        "used": status.HTTP_410_GONE,
    }[reason]
    return templates.TemplateResponse(
        request, "link_unavailable.html", {"reason": reason}, status_code=code
    )


@app.get("/s/{token}", response_class=HTMLResponse)
def customer_form(
    request: Request,
    token: str,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    try:
        link = services.get_usable_link(db, token)
    except services.LinkUnavailableError as exc:
        return _unavailable(request, exc.reason)
    return templates.TemplateResponse(
        request,
        "customer_form.html",
        {"link": link, "max_upload_mb": settings.max_upload_mb, "error": None, "form": {}},
    )


@app.post("/s/{token}", response_class=HTMLResponse)
async def customer_submit(
    request: Request,
    token: str,
    description: Annotated[str, Form()] = "",
    browser: Annotated[str, Form()] = "",
    os: Annotated[str, Form()] = "",
    viewport: Annotated[str, Form()] = "",
    timezone: Annotated[str, Form()] = "",
    language: Annotated[str, Form()] = "",
    page_url: Annotated[str, Form()] = "",
    share_url: Annotated[str, Form()] = "",
    screenshot: Annotated[UploadFile | None, File()] = None,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    # NOTE: request.cookies is intentionally never read on customer routes.
    try:
        link = services.get_usable_link(db, token)
    except services.LinkUnavailableError as exc:
        return _unavailable(request, exc.reason)

    meta = {
        "browser": browser,
        "os": os,
        "viewport": viewport,
        "timezone": timezone,
        "language": language,
        # URL is stored only when the customer explicitly opted in.
        "page_url": page_url if share_url == "on" else "",
    }

    shot: services.Screenshot | None = None
    if screenshot is not None and screenshot.filename:
        content = await screenshot.read(settings.max_upload_bytes + 1)
        shot = services.Screenshot(original_name=screenshot.filename, content=content)

    try:
        incident = services.submit_incident(
            db,
            link,
            description=description,
            meta=meta,
            user_agent=request.headers.get("user-agent", ""),
            screenshot=shot,
        )
    except services.LinkUnavailableError as exc:
        return _unavailable(request, exc.reason)
    except (ValueError, services.InvalidScreenshotError) as exc:
        return templates.TemplateResponse(
            request,
            "customer_form.html",
            {
                "link": link,
                "max_upload_mb": settings.max_upload_mb,
                "error": str(exc),
                "form": {"description": description},
            },
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        )

    return templates.TemplateResponse(request, "submitted.html", {"incident": incident})
