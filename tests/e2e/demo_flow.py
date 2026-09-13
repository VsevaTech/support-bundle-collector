"""End-to-end demo flow in a real browser.

support creates link -> customer submits (another browser context) -> support views incident.
Usage: python tests/e2e/demo_flow.py [BASE_URL] [ARTIFACT_DIR]
"""

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
OUT = Path(sys.argv[2] if len(sys.argv) > 2 else "e2e-artifacts")
OUT.mkdir(parents=True, exist_ok=True)

PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d4944415478"
    "9c6360f8cfc0000002010100c9fe92ef0000000049454e44ae426082"
)
shot = OUT / "screenshot.png"
shot.write_bytes(PNG)

with sync_playwright() as p:
    browser = p.chromium.launch()
    errors = []

    # --- support: create link (HTMX) -----------------------------------------
    support = browser.new_context(viewport={"width": 1200, "height": 800})
    sp = support.new_page()
    sp.on("pageerror", lambda e: errors.append(f"support pageerror: {e}"))
    sp.on(
        "console",
        lambda m: (
            errors.append(f"support console {m.type}: {m.text}")
            if m.type == "error" and "410" not in m.text
            else None
        ),
    )
    sp.goto(f"{BASE}/support")
    sp.select_option("select[name=ttl_hours]", "24")
    sp.fill("input[name=note]", "ticket #4821 — ACME Ltd")
    sp.click("button:has-text('Create link')")
    sp.wait_for_selector("#link-created")
    link = sp.input_value("#link-url")
    assert sp.url.rstrip("/") == f"{BASE}/support", "HTMX swap expected, got full navigation"
    sp.screenshot(path=OUT / "1_support_create.png")
    print("link:", link)

    # --- customer: another browser context (separate profile, separate cookies)
    customer = browser.new_context(
        viewport={"width": 1366, "height": 768},
        locale="de-DE",
        timezone_id="Europe/Berlin",
    )
    cp = customer.new_page()
    cp.on("pageerror", lambda e: errors.append(f"customer pageerror: {e}"))
    cp.on(
        "console",
        lambda m: (
            errors.append(f"customer console {m.type}: {m.text}")
            if m.type == "error" and "410" not in m.text
            else None
        ),
    )
    cp.goto(link, referer="https://shop.example.com/checkout")
    cp.wait_for_function(
        "document.querySelector('[data-field=browser]').textContent !== 'detecting…'"
    )
    diag = {
        k: cp.text_content(f"[data-field={k}]")
        for k in ("browser", "os", "viewport", "timezone", "language")
    }
    print("diagnostics shown to customer:", diag)
    assert diag["viewport"].startswith("1366x"), diag
    assert diag["timezone"] == "Europe/Berlin", diag
    assert diag["language"] == "de-DE", diag
    assert "Chrom" in diag["browser"] or "HeadlessChrome" in diag["browser"], diag

    cp.fill("textarea[name=description]", "Payment button does nothing.")
    cp.set_input_files("input[name=screenshot]", str(shot))
    cp.wait_for_selector("#preview:not([hidden])")
    cp.check("#share_url")
    assert not cp.is_hidden("#url-row")
    assert cp.input_value("#page_url") == "https://shop.example.com/checkout", cp.input_value(
        "#page_url"
    )
    cp.screenshot(path=OUT / "2_customer_form.png", full_page=True)
    cp.click("button:has-text('Send to support')")
    cp.wait_for_selector("text=Thank you")
    thanks = cp.text_content("main")
    print("customer sees:", " ".join(thanks.split())[:120])
    cp.screenshot(path=OUT / "3_customer_thanks.png")

    # one-time: reopening the link must be 410
    r = cp.goto(link)
    assert r.status == 410, r.status
    assert "already used" in cp.text_content("main")
    print("re-open link ->", r.status)

    # --- support: view incident ------------------------------------------------
    sp.goto(f"{BASE}/support/incidents")
    sp.click("table tbody tr:first-child a")
    sp.wait_for_selector("pre.bundle", state="attached")
    sp.click("details summary")
    bundle = sp.text_content("pre.bundle")
    print("\n" + bundle + "\n")
    assert "Browser:" in bundle and "Payment button does nothing." in bundle
    assert "URL: https://shop.example.com/checkout" in bundle
    assert "screenshot.png" in bundle
    img = sp.get_attribute("img.shot", "src")
    resp = support.request.get(f"{BASE}{img}")
    assert resp.status == 200 and resp.headers["content-type"] == "image/png"
    sp.screenshot(path=OUT / "4_support_incident.png", full_page=True)

    # customer context must have no cookies at all
    assert customer.cookies() == [], customer.cookies()

    browser.close()
    if errors:
        print("BROWSER ERRORS:", *errors, sep="\n  ")
        sys.exit(1)
    print("DEMO FLOW OK")
