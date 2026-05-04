import json
import os
import re
import sys
import traceback
import difflib
from datetime import datetime

from browser import connect_to_chrome, shutdown_browser
from keka_bot import KekaBot
from utils import logger, console, print_banner, load_env

def normalize(s):
    """Normalize string for robust matching."""
    return re.sub(r'[^a-z0-9]', '', str(s).lower())

def get_task_mapping():
    """
    Builds a mapping from normalized task name to (project_name, phase_name, task_name).
    Checks both output.json and fetched.json for maximum coverage.
    """
    mapping = {}
    
    # Check output.json (legacy fallback)
    if os.path.exists("output.json"):
        try:
            with open("output.json", "r", encoding="utf-8") as f:
                data = json.load(f)
                for p in data.get("projects", []):
                    p_name = p.get("name")
                    for t in p.get("tasks", []):
                        t_name = t.get("name")
                        mapping[normalize(t_name)] = (p_name, None, t_name)
                        for st in t.get("subtasks", []):
                            mapping[normalize(st)] = (p_name, None, st)
        except Exception as e:
            console.print(f"[yellow]Warning: Could not read output.json: {e}[/yellow]")

    # Check fetched.json (primary source with hierarchy)
    if os.path.exists("fetched.json"):
        try:
            with open("fetched.json", "r", encoding="utf-8") as f:
                data = json.load(f)
                for p in data.get("projects", []):
                    p_name = p.get("project")
                    for phase in p.get("phases", []):
                        phase_name = phase.get("phase")
                        for t in phase.get("tasks", []):
                            mapping[normalize(t)] = (p_name, phase_name, t)
                    for st in p.get("standalone_tasks", []):
                        mapping[normalize(st)] = (p_name, None, st)
        except Exception as e:
            console.print(f"[yellow]Warning: Could not read fetched.json: {e}[/yellow]")
            
    return mapping

def find_project_and_task(input_task, mapping):
    """Find the corresponding project, phase, and UI task name using fuzzy matching if needed."""
    norm_input = normalize(input_task)
    
    # Exact robust match
    if norm_input in mapping:
        return mapping[norm_input]
    
    # Fuzzy match
    best_match = None
    best_ratio = 0
    for key, val in mapping.items():
        ratio = difflib.SequenceMatcher(None, norm_input, key).ratio()
        if ratio > best_ratio:
            best_ratio = ratio
            best_match = val
            
    if best_ratio > 0.8:
        return best_match
        
    return None, None, None

def calculate_times(duration: float):
    """Calculates a standard 09:00 start time and corresponding end time."""
    start_time = "09:00"
    end_hour = int(9 + duration)
    end_minute = int(round((duration * 60) % 60))
    
    # Simple clamp to avoid overflowing 24 hours format
    if end_hour >= 24:
        end_hour = 23
        end_minute = 59
        
    end_time = f"{end_hour:02d}:{end_minute:02d}"
    return start_time, end_time

def main():
    load_env()
    print_banner()
    
    console.rule("[bold cyan]Step 1: Reading configurations[/bold cyan]")
    
    if not os.path.exists("input.json"):
        console.print("[bold red]Error: input.json not found in the current directory.[/bold red]")
        sys.exit(1)
        
    try:
        with open("input.json", "r", encoding="utf-8") as f:
            input_data = json.load(f)
    except Exception as e:
        console.print(f"[bold red]Error reading input.json: {e}[/bold red]")
        sys.exit(1)
        
    tasks = input_data.get("tasks", [])
    if not tasks:
        console.print("[yellow]No tasks found in input.json[/yellow]")
        return
        
    mapping = get_task_mapping()
    if not mapping:
        console.print("[bold red]Could not load project mappings from output.json or fetched.json. Please ensure definitions exist.[/bold red]")
        sys.exit(1)
        
    entries_to_fill = []
    
    console.print(f"Found {len(tasks)} tasks in input.json. Mapping to Keka counterparts...")
    for t in tasks:
        task_name = t.get("task")
        date_str = t.get("date")
        duration = t.get("duration_hours", 8)
        
        project, phase, mapped_task = find_project_and_task(task_name, mapping)
        if not project:
            console.print(f"[bold red]Could not find matching Keka project for task: '{task_name}'[/bold red]")
            continue
            
        start_time, end_time = calculate_times(float(duration))
        
        entry = {
            "date": date_str,
            "project": project,
            "phase": phase,
            "task": mapped_task,
            "hours": float(duration),
            "comment": "Worked on tasks",
            "start_time": start_time,
            "end_time": end_time
        }
        entries_to_fill.append(entry)
        phase_str = f" (Phase: {phase})" if phase else ""
        console.print(f"[green]*[/green] Mapped [bold]{task_name}[/bold] -> Project: [cyan]{project}[/cyan]{phase_str}, Task: [cyan]{mapped_task}[/cyan]")

    if not entries_to_fill:
        console.print("[bold red]No valid mapped entries to fill. Exiting.[/bold red]")
        sys.exit(1)

    console.rule("[bold cyan]Step 2: Expected Action Plan[/bold cyan]")
    for e in entries_to_fill:
        phase_part = f" [{e['phase']}]" if e['phase'] else ""
        console.print(f" - [yellow]{e['date']}[/yellow]: [cyan]{e['hours']}h[/cyan] ({e['start_time']}-{e['end_time']}) on [magenta]{e['project']}[/magenta]{phase_part} -> {e['task']}")
    
    # We could prompt the user for confirmation here, but typically an automated batch fill runs directly.
    # To be safe, we will simply execute it, as requested "Create a fill.py that checks output.json [...] then fills".
    
    console.rule("[bold cyan]Step 3: Executing Automation[/bold cyan]")
    browser = None
    try:
        browser, ctx, page = connect_to_chrome()
        bot = KekaBot(page)
        
        for idx, entry in enumerate(entries_to_fill, 1):
            console.rule(f"Filling Entry {idx}/{len(entries_to_fill)}: {entry['date']}")
            
            # Navigate to the target week
            bot.go_to_week_containing(entry['date'])
            
            # Execute fill operation
            success = bot.fill_entry(entry)
            
            if success:
                console.print(f"[bold green]Successfully filled timesheet for {entry['date']}![/bold green]")
            else:
                console.print(f"[bold red]Failed to fill timesheet for {entry['date']}.[/bold red]")
                
    except Exception as e:
        console.print(f"[bold red]An unexpected error occurred during browser manipulation: {e}[/bold red]")
        logger.error(traceback.format_exc())
    finally:
        if browser is not None:
            try:
                browser.close()
            except Exception:
                pass
        shutdown_browser()
        
    console.print("\n[bold green]Automation batch run complete.[/bold green]")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted by user. Exiting...[/yellow]")
        sys.exit(0)
