"""
fetch.py - Fetch all Keka projects, phases, and tasks and save to fetched.json

Usage:
    python fetch.py
    python fetch.py --port 9222
    python fetch.py --port 9222 --output fetched.json

Prerequisites:
    1. Launch Chrome with remote debugging:
       "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe"
         --remote-debugging-port=9222 --user-data-dir=C:\\ChromeDebug
    2. Log in to https://cloudsufi.keka.com
    3. Navigate to the Timesheet page, then run this script.

Output schema (fetched.json):
    {
        "projects": [
            {
                "project": "CSIN-INTERN",
                "phases": [
                    {
                        "phase": "Training - Month 1 - Feb 2026",
                        "tasks": ["SQL Fundamentals + Python Basics", ...]
                    },
                    ...
                ],
                "standalone_tasks": []   // tasks with no phase header
            },
            ...
        ]
    }
"""

import json
import sys
import time
import argparse

from browser import connect_to_chrome, shutdown_browser
from utils import console, load_env, print_banner

# ─────────────────────────────────────────────────────────────────────────────
# JavaScript that runs inside the browser page
# ─────────────────────────────────────────────────────────────────────────────
# Key insight from the Keka UI:
#   • Items in the PHASE/TASKS column that have a  ›  (ki-chevron-right) icon
#     are PHASE HEADERS (they are collapsible groups).
#   • Clicking the chevron expands the phase and reveals child TASK rows
#     (which have no chevron of their own).
#   • Items with no chevron that are already visible = standalone tasks
#     (they belong to no phase).
#
# Strategy per project:
#   1. First collapse everything to get a clean baseline.
#   2. Identify baseline items: chevron → phase header, no chevron → standalone task.
#   3. For each phase header: expand → collect newly visible non-chevron items → collapse.
# ─────────────────────────────────────────────────────────────────────────────
_EXTRACT_JS = r"""
async () => {
    const delay = ms => new Promise(res => setTimeout(res, ms));

    // ─── Find the container element for a labelled column ───────────────────
    const findCol = (title) => {
        const labels = Array.from(document.querySelectorAll(
            'label, .text-muted, span, small, h1, h2, h3, h4, h5, h6'
        ));
        let lb = labels.find(l => l.innerText.trim().toUpperCase() === title.toUpperCase());
        if (!lb) lb = labels.find(l => l.innerText.trim().toUpperCase().includes(title.toUpperCase()));
        if (!lb) return null;
        let curr = lb.parentElement;
        while (curr && curr !== document.body) {
            if (curr.querySelector('.cursor-pointer')) return curr;
            curr = curr.parentElement;
        }
        return lb.parentElement;
    };

    // ─── Locate the two columns ─────────────────────────────────────────────
    const pContainer = findCol('PROJECTS');
    const tContainer = findCol('PHASE/TASKS') || findCol('TASKS');

    const allItems = Array.from(document.querySelectorAll('.cursor-pointer')).filter(el => {
        const s = window.getComputedStyle(el);
        return s.display !== 'none' && s.visibility !== 'hidden' && el.offsetHeight > 0;
    });

    if (allItems.length === 0) {
        return { error: 'No clickable items visible. Is the Add Time Entry panel open?' };
    }

    // Horizontal boundary between the Projects column and the Phase/Tasks column
    let splitX = 400;
    if (pContainer && tContainer) {
        splitX = (pContainer.getBoundingClientRect().right +
                  tContainer.getBoundingClientRect().left) / 2;
    } else {
        const lefts = allItems.map(i => i.getBoundingClientRect().left);
        splitX = (Math.min(...lefts) + Math.max(...lefts)) / 2 + 30;
    }

    // ─── Helpers to read the task column ────────────────────────────────────

    // Returns all visible .cursor-pointer elements that live in the task column.
    const getTaskColItems = () =>
        Array.from(document.querySelectorAll('.cursor-pointer')).filter(el => {
            const s = window.getComputedStyle(el);
            return s.display !== 'none' &&
                   s.visibility !== 'hidden' &&
                   el.offsetHeight > 0 &&
                   el.getBoundingClientRect().left >= splitX;
        });

    // Extract clean display text from an element.
    const getText = (el) => {
        const p = el.querySelector('p');
        return (p ? p : el).innerText.split('\n')[0].trim();
    };

    // True if el is a phase header (has a collapsible chevron icon).
    const isPhaseHeader = (el) =>
        !!(el.querySelector('.ki-chevron-right') || el.querySelector('.ki-chevron-down'));

    // Filter noise strings.
    const isNoise = (t) =>
        !t ||
        t.length <= 2 ||
        t.toLowerCase().includes('search') ||
        t.toLowerCase().includes('no task') ||
        t.toLowerCase().includes('attach file') ||
        t.includes('Alt + K') ||
        t.includes('+Add Task');

    // ─── Per-project phase/task scraper ─────────────────────────────────────
    const scrapeTaskCol = async () => {
        // Step 1 – collapse any already-expanded phases for a clean slate
        const downChevrons = Array.from(document.querySelectorAll('.ki-chevron-down')).filter(el => {
            return el.getBoundingClientRect().left >= splitX && el.offsetHeight > 0;
        });
        for (const chev of downChevrons) {
            chev.click();
            await delay(250);
        }
        await delay(400);

        // Step 2 – snapshot the collapsed state: only chevron items + standalone tasks visible
        const baseItems   = getTaskColItems();
        const baseNames   = new Set(baseItems.map(getText));

        const phaseEls    = baseItems.filter(isPhaseHeader);
        const standAlones = baseItems.filter(el => !isPhaseHeader(el)).map(getText).filter(t => !isNoise(t));

        const phases = [];

        // Step 3 – expand each phase one at a time and harvest its children
        for (const phEl of phaseEls) {
            const phaseName = getText(phEl);
            if (isNoise(phaseName)) continue;

            // Click the right-chevron to expand
            const chevIcon = phEl.querySelector('.ki-chevron-right');
            if (chevIcon) {
                chevIcon.click();
                await delay(600);
            } else {
                phEl.click();
                await delay(600);
            }

            // Children = newly appeared non-chevron items in task column
            const afterItems = getTaskColItems();
            const childTasks = afterItems
                .filter(el => !isPhaseHeader(el) && !baseNames.has(getText(el)))
                .map(getText)
                .filter(t => !isNoise(t));

            // De-duplicate while preserving order
            const seen = new Set();
            const uniqueTasks = [];
            for (const t of childTasks) {
                if (!seen.has(t)) { seen.add(t); uniqueTasks.push(t); }
            }

            phases.push({ phase: phaseName, tasks: uniqueTasks });

            // Collapse this phase again before processing the next
            const downChev = phEl.querySelector('.ki-chevron-down');
            if (downChev) {
                downChev.click();
                await delay(350);
            }
        }

        // De-duplicate standalone tasks
        const seenSt = new Set();
        const uniqueStandAlones = [];
        for (const t of standAlones) {
            if (!seenSt.has(t)) { seenSt.add(t); uniqueStandAlones.push(t); }
        }

        return { phases, standalone_tasks: uniqueStandAlones };
    };

    // ─── Main project iteration loop ────────────────────────────────────────
    const projects = [];
    const seenProjects = new Set();
    let noNewCount = 0;
    let lastScroll = -1;

    if (pContainer) pContainer.scrollTop = 0;

    while (noNewCount < 3) {
        const visible = Array.from(document.querySelectorAll('.cursor-pointer')).filter(el => {
            const s = window.getComputedStyle(el);
            return s.display !== 'none' && s.visibility !== 'hidden' && el.offsetHeight > 0;
        });

        const projItems = visible.filter(el => el.getBoundingClientRect().left < splitX);
        let foundNew = false;

        for (const item of projItems) {
            const nameEl = item.querySelector('p') || item.querySelector('div') || item;
            const rawName = nameEl.innerText.split('\n')[0].trim();

            if (!rawName ||
                rawName.toLowerCase().includes('search') ||
                rawName.includes('Alt + K') ||
                rawName.toLowerCase().includes('attach file') ||
                seenProjects.has(rawName)) continue;

            foundNew = true;
            seenProjects.add(rawName);

            // Click project to populate the task column
            try {
                item.click();
                await delay(1200);
            } catch (e) { continue; }

            const { phases, standalone_tasks } = await scrapeTaskCol();

            projects.push({ project: rawName, phases, standalone_tasks });
        }

        if (!foundNew) noNewCount++; else noNewCount = 0;

        if (pContainer) {
            pContainer.scrollTop += 350;
            await delay(600);
            if (pContainer.scrollTop === lastScroll) break;
            lastScroll = pContainer.scrollTop;
        } else {
            break;
        }
    }

    return { projects };
}
"""


# ─────────────────────────────────────────────────────────────────────────────
# Python driver
# ─────────────────────────────────────────────────────────────────────────────

def fetch(port: int = 9222, output: str = "fetched.json") -> dict:
    """Connect to Chrome, scrape Keka projects/phases/tasks, write to *output*."""
    load_env()
    print_banner()

    # Step 1 – connect
    console.rule("[bold cyan]Step 1: Connect to Chrome[/bold cyan]")
    try:
        browser, ctx, page = connect_to_chrome(port=port)
    except RuntimeError as e:
        console.print(f"[bold red]{e}[/bold red]")
        sys.exit(1)

    # Step 2 – open Add Time Entry panel
    console.rule("[bold cyan]Step 2: Open Add Time Entry Panel[/bold cyan]")
    console.print("[dim]Clicking 'Add Time Entry'…[/dim]")
    try:
        if not page.locator("text=PHASE/TASKS").is_visible(timeout=2000):
            page.locator("text=Add Time Entry").first.click()
        page.wait_for_selector("text=PHASE/TASKS", timeout=15_000)
        time.sleep(1.5)
    except Exception as e:
        console.print(f"[bold red]Could not open 'Add Time Entry' panel: {e}[/bold red]")
        console.print(
            "[dim]Make sure you are on the Keka Timesheet page "
            "(https://cloudsufi.keka.com/#/me/timesheet/all-timesheets)[/dim]"
        )
        sys.exit(1)

    # Step 3 – extract
    console.rule("[bold cyan]Step 3: Extracting Projects / Phases / Tasks[/bold cyan]")
    console.print("[dim]Clicking through each project — this may take a minute…[/dim]")
    data = page.evaluate(_EXTRACT_JS)

    # Step 4 – save
    console.rule("[bold cyan]Step 4: Save Results[/bold cyan]")

    if "error" in data:
        console.print(f"[bold red]Extraction error: {data['error']}[/bold red]")
        sys.exit(1)

    projects = data.get("projects", [])
    if not projects:
        console.print(
            "[bold yellow]⚠  No projects found. "
            "Ensure the Add Time Entry panel is open and has projects.[/bold yellow]"
        )
    else:
        console.print(f"[bold green]✅  Extracted {len(projects)} project(s).[/bold green]")

    with open(output, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)

    console.print(f"[bold blue]💾  Saved to [underline]{output}[/underline][/bold blue]")

    console.print("\n[bold]Summary:[/bold]")
    for p in projects:
        phase_count = len(p.get("phases", []))
        task_count  = sum(len(ph.get("tasks", [])) for ph in p.get("phases", []))
        st_count    = len(p.get("standalone_tasks", []))
        console.print(
            f"  [cyan]•[/cyan] [bold]{p['project']}[/bold]  "
            f"[dim]({phase_count} phase(s), {task_count} tasks inside phases, "
            f"{st_count} standalone task(s))[/dim]"
        )

    browser.close()
    return data


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Fetch all Keka projects, phases and tasks → fetched.json",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--port", "-p", type=int, default=9222,
                        help="Chrome remote debugging port (default: 9222)")
    parser.add_argument("--output", "-o", default="fetched.json",
                        help="Output JSON file path (default: fetched.json)")
    args = parser.parse_args()

    try:
        fetch(port=args.port, output=args.output)
    finally:
        shutdown_browser()


if __name__ == "__main__":
    main()
