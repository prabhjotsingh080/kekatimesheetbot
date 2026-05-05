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

    const allTasksMap = new Map();
    let hasNextPage = true;
    let loopGuard = 0;

    while (hasNextPage && loopGuard < 20) {
        loopGuard++;
        await delay(1500); // Wait for data/table to render

        // Find table rows: prefer standard <tr> inside <tbody>, fallback to role="row"
        let rows = Array.from(document.querySelectorAll('tbody tr'));
        if (rows.length === 0) {
            rows = Array.from(document.querySelectorAll('.table-row, [role="row"]')).filter(r => {
                // Filter out headers
                return !r.querySelector('th, [role="columnheader"]') && r.innerText.trim() !== '';
            });
        }

        // Attempt to find column indices dynamically
        let taskIdx = 0;
        let phaseIdx = 1;
        let projIdx = 2;
        let pmIdx = 3;
        let endIdx = 4;
        let hrsIdx = 5;
        let stageIdx = 6;
        
        const headers = Array.from(document.querySelectorAll('th, thead td, .header-cell, [role="columnheader"]'));
        if (headers.length > 0) {
            headers.forEach((h, i) => {
                const text = h.innerText.trim().toUpperCase();
                if (text === 'TASKS' || text === 'TASK') taskIdx = i;
                else if (text === 'PHASE') phaseIdx = i;
                else if (text === 'PROJECT' || text === 'PROJECTS') projIdx = i;
                else if (text.includes('PROJECT MANAGER') || text.includes('MANAGER')) pmIdx = i;
                else if (text.includes('END DATE')) endIdx = i;
                else if (text.includes('HOURS') || text === 'HOURS') hrsIdx = i;
                else if (text.includes('TASK STAGE') || text.includes('STAGE')) stageIdx = i;
            });
        }

        for (const row of rows) {
            // Find cells: prefer <td>, fallback to role="cell" or children
            let cells = Array.from(row.querySelectorAll('td'));
            if (cells.length === 0) {
                cells = Array.from(row.querySelectorAll('.table-cell, [role="cell"]'));
            }
            if (cells.length === 0) {
                // Last fallback: direct child divs
                cells = Array.from(row.children);
            }

            if (cells.length > Math.max(taskIdx, phaseIdx, projIdx)) {
                let taskText = cells[taskIdx] ? cells[taskIdx].innerText.trim() : "";
                let phaseText = cells[phaseIdx] ? cells[phaseIdx].innerText.trim() : "";
                let projText = cells[projIdx] ? cells[projIdx].innerText.trim() : "";
                let pmText = (cells[pmIdx]) ? cells[pmIdx].innerText.trim() : "";
                let endText = (cells[endIdx]) ? cells[endIdx].innerText.trim() : "";
                let hrsText = (cells[hrsIdx]) ? cells[hrsIdx].innerText.trim() : "";
                let stageText = (cells[stageIdx]) ? cells[stageIdx].innerText.trim() : "";

                let taskName = taskText.split('\n')[0].trim();
                let phaseName = phaseText.split('\n')[0].trim();
                let projName = projText.split('\n')[0].trim();

                // If taskName is empty or it's a "No records found" row, skip
                if (!taskName || taskName.toLowerCase().includes('no data') || !projName) continue;

                let taskObj = {
                    name: taskName,
                    phase: phaseName,
                    project: projName,
                    project_manager: pmText.replace(/\n/g, ' ').trim(),
                    end_date: endText.replace(/\n/g, ' ').trim(),
                    hours: hrsText.replace(/\n/g, ' ').trim(),
                    task_stage: stageText.replace(/\n/g, ' ').trim()
                };
                
                // Use taskName + projName as unique key to prevent duplicates across pages
                allTasksMap.set(`${taskName}::${projName}`, taskObj);
            }
        }

        // Try to click "Next Page" if it exists and is not disabled
        // Often pagination buttons have ki-chevron-right or text like ">"
        const nextBtns = Array.from(document.querySelectorAll('button, a, .page-link, .ki-chevron-right')).filter(el => {
            const text = el.innerText.trim();
            const aria = el.getAttribute('aria-label') || '';
            const className = el.className || '';
            return text === '>' || aria.toLowerCase().includes('next') || className.includes('chevron-right');
        });

        let clickedNext = false;
        for (const btn of nextBtns) {
            const clickable = btn.closest('button') || btn.closest('a') || btn;
            if (!clickable.disabled && !clickable.classList.contains('disabled') && !clickable.parentElement.classList.contains('disabled')) {
                // Ensure it's actually visible
                if (clickable.offsetWidth > 0 && clickable.offsetHeight > 0) {
                    clickable.click();
                    clickedNext = true;
                    await delay(1000);
                    break;
                }
            }
        }
        
        if (!clickedNext) {
            hasNextPage = false;
        }
    }

    const projects = Array.from(allTasksMap.values());
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

    # Step 2 – open My Tasks panel
    console.rule("[bold cyan]Step 2: Navigate to My Tasks[/bold cyan]")
    console.print("[dim]Navigating to 'My Tasks'…[/dim]")
    try:
        page.goto("https://cloudsufi.keka.com/#/me/timesheet/my-tasks", wait_until="networkidle")
        page.wait_for_timeout(3000) # Give UI time to load the table
    except Exception as e:
        console.print(f"[bold red]Could not navigate to 'My Tasks' page: {e}[/bold red]")
        sys.exit(1)

    # Step 3 – extract
    console.rule("[bold cyan]Step 3: Extracting Projects / Phases / Tasks[/bold cyan]")
    console.print("[dim]Reading table data...[/dim]")
    data = page.evaluate(_EXTRACT_JS)

    # Step 4 – save
    console.rule("[bold cyan]Step 4: Save Results[/bold cyan]")

    if "error" in data:
        console.print(f"[bold red]Extraction error: {data['error']}[/bold red]")
        sys.exit(1)

    projects = data.get("projects", [])
    if not projects:
        console.print(
            "[bold yellow]:warning:  No projects found. "
            "Ensure the Add Time Entry panel is open and has projects.[/bold yellow]"
        )
    else:
        console.print(f"[bold green]:white_check_mark:  Extracted {len(projects)} project(s).[/bold green]")

    with open(output, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)

    console.print(f"[bold blue]:floppy_disk:  Saved to [underline]{output}[/underline][/bold blue]")

    console.print("\n[bold]Summary:[/bold]")
    console.print(f"  [cyan]•[/cyan] [bold]Total Tasks Fetched:[/bold] {len(projects)}")
    
    # Optional: group by project for the console output
    proj_counts = {}
    for task in projects:
        p_name = task.get('project', 'Unknown')
        proj_counts[p_name] = proj_counts.get(p_name, 0) + 1
        
    for p_name, count in proj_counts.items():
        console.print(f"    - {p_name}: {count} tasks")

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
