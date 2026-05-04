"""
utils.py - Shared utilities: logging, config, retry helpers, screenshot support
"""

import os
import sys
import json
import time
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from rich.console import Console
from rich.logging import RichHandler
from rich.table import Table
from rich.panel import Panel
from rich import print as rprint

# ──────────────────────────────────────────────
# Console (global, used everywhere)
# ──────────────────────────────────────────────
console = Console()

# ──────────────────────────────────────────────
# Logger setup
# ──────────────────────────────────────────────
def get_logger(name: str = "keka") -> logging.Logger:
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(message)s",
        datefmt="[%X]",
        handlers=[
            RichHandler(console=console, rich_tracebacks=True, show_path=False),
        ],
    )
    logger = logging.getLogger(name)
    return logger


logger = get_logger()


# ──────────────────────────────────────────────
# Config / env
# ──────────────────────────────────────────────
def load_env():
    """Load .env file if present."""
    env_path = Path(".env")
    if env_path.exists():
        from dotenv import load_dotenv
        load_dotenv(env_path)
        logger.debug(".env file loaded")


def get_gemini_api_key() -> str:
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key:
        console.print("[bold red]GEMINI_API_KEY not set.[/bold red]")
        console.print("    Set it via:  export GEMINI_API_KEY=your_key")
        console.print("    Or create a .env file with GEMINI_API_KEY=your_key")
        sys.exit(1)
    return key


# ──────────────────────────────────────────────
# Cache helpers  (JSON file-based)
# ──────────────────────────────────────────────
CACHE_FILE = Path(".keka_cache.json")


def load_cache() -> dict:
    if CACHE_FILE.exists():
        try:
            return json.loads(CACHE_FILE.read_text())
        except Exception:
            pass
    return {}


def save_cache(data: dict):
    try:
        CACHE_FILE.write_text(json.dumps(data, indent=2))
    except Exception as e:
        logger.warning(f"Cache write failed: {e}")


def cache_get(key: str):
    return load_cache().get(key)


def cache_set(key: str, value):
    c = load_cache()
    c[key] = value
    save_cache(c)


# ──────────────────────────────────────────────
# Screenshot helpers
# ──────────────────────────────────────────────
SCREENSHOTS_DIR = Path("screenshots")


def take_screenshot(page, label: str = "error") -> Optional[Path]:
    """Save a screenshot; returns path or None."""
    try:
        SCREENSHOTS_DIR.mkdir(exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = SCREENSHOTS_DIR / f"{label}_{ts}.png"
        page.screenshot(path=str(path), full_page=True)
        logger.info(f"Screenshot saved -> {path}")
        return path
    except Exception as e:
        logger.warning(f"Screenshot failed: {e}")
        return None


# ──────────────────────────────────────────────
# Rich display helpers
# ──────────────────────────────────────────────
def print_entries_table(entries: list[dict]):
    """Print a Rich table of timesheet entries."""
    table = Table(title="Timesheet Entries", show_header=True, header_style="bold cyan")
    table.add_column("Date", style="dim")
    table.add_column("Project", style="green")
    table.add_column("Task", style="yellow")
    table.add_column("Hours", justify="right", style="bold magenta")
    table.add_column("Comment", style="dim")
    table.add_column("Start/End", style="blue")
    for e in entries:
        table.add_row(
            str(e.get("date", "")),
            str(e.get("project", "")),
            str(e.get("task", "")),
            str(e.get("hours", "")),
            str(e.get("comment", "")),
            f"{e.get('start_time', '')}-{e.get('end_time', '')}"
        )
    console.print(table)


def print_missing_days(missing: list[str]):
    if not missing:
        console.print("[bold green]No missing days found![/bold green]")
        return
    console.print(Panel(
        "\n".join(f"  * [bold yellow]{d}[/bold yellow]" for d in missing),
        title="[bold red]Missing Timesheet Days[/bold red]",
        border_style="red",
    ))


def print_banner():
    console.print(Panel(
        "[bold cyan]Keka Timesheet Automation[/bold cyan]\n"
        "[dim]Powered by Playwright + Gemini AI[/dim]",
        border_style="cyan",
        padding=(1, 4),
    ))


# ──────────────────────────────────────────────
# Retry wrapper (simple, no tenacity dep needed)
# ──────────────────────────────────────────────
def retry(fn, retries: int = 3, delay: float = 1.5, label: str = ""):
    """Call fn up to `retries` times, sleeping between attempts."""
    last_exc = None
    for attempt in range(1, retries + 1):
        try:
            return fn()
        except Exception as exc:
            last_exc = exc
            logger.warning(f"ATTEMPT {attempt}/{retries} failed: {exc}")
            if attempt < retries:
                time.sleep(delay)
    raise last_exc
