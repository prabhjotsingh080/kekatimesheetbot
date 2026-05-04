"""
browser.py - Connect to an already-running Chrome via CDP (port 9222).

Usage:
    from browser import connect_to_chrome
    browser, context, page = connect_to_chrome()
"""

import requests
from playwright.sync_api import sync_playwright, Browser, BrowserContext, Page

from utils import logger, console

CDP_URL = "http://localhost:9222"
KEKA_URL = "https://cloudsufi.keka.com/#/me/timesheet/all-timesheets"


def _check_chrome_running(port: int = 9222) -> bool:
    """Return True if Chrome debug endpoint is reachable."""
    try:
        r = requests.get(f"http://localhost:{port}/json/version", timeout=3)
        return r.status_code == 200
    except Exception:
        return False


_playwright = None

def connect_to_chrome(port: int = 9222) -> tuple[Browser, BrowserContext, Page]:
    """
    Connect Playwright to an existing Chrome session via CDP.

    Returns (browser, context, page) pointing at the Keka timesheet.

    Raises:
        RuntimeError: If Chrome is not reachable on the given port.
    """
    global _playwright
    if not _check_chrome_running(port):
        raise RuntimeError(
            f"\n[bold red]Chrome not found on port {port}.[/bold red]\n\n"
            "Launch Chrome first with:\n"
            "  Windows:\n"
            '    "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe" '
            "--remote-debugging-port=9222 --user-data-dir=C:\\ChromeDebug\n\n"
            "  Then log in to https://cloudsufi.keka.com and re-run this tool."
        )

    logger.info(f"Connecting to Chrome on port {port} ...")
    if _playwright is None:
        _playwright = sync_playwright().start()

    browser = _playwright.chromium.connect_over_cdp(f"http://localhost:{port}")
    logger.info("Connected to Chrome")

    # Use existing context / page if one is already on Keka, else open new tab
    contexts = browser.contexts
    if not contexts:
        raise RuntimeError("No browser contexts found. Make sure Chrome is logged in to Keka.")

    context = contexts[0]

    # Look for a page already showing Keka
    keka_page = None
    for p in context.pages:
        if "keka.com" in p.url:
            keka_page = p
            logger.info(f"Reusing existing Keka tab: {p.url}")
            break

    if keka_page is None:
        logger.info("Opening new Keka timesheet tab ...")
        keka_page = context.new_page()

    # Navigate to timesheet page (always ensure we're on the right URL)
    if "#/me/timesheet" not in keka_page.url:
        logger.info(f"Navigating to {KEKA_URL}")
        keka_page.goto(KEKA_URL, wait_until="networkidle", timeout=30_000)
    else:
        logger.info("Already on timesheet page")

    keka_page.bring_to_front()
    return browser, context, keka_page


def shutdown_browser():
    """Stop the global Playwright instance."""
    global _playwright
    if _playwright:
        _playwright.stop()
        _playwright = None
