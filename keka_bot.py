"""
keka_bot.py - Playwright automation for Keka timesheet UI.

Keka UI structure (verified from screenshots):
  - Inline spreadsheet grid, NOT modal-based
  - "+ Add Time Entry" TEXT LINK (not a button) opens inline panel
  - Panel has PROJECTS search (left) and PHASE/TASKS search (right)
  - After project+task are selected, a row is added to the grid
  - Clicking a "0:00" cell in a day column opens an inline time form
  - Fill start time, end time, comment → Save

Public API:
    KekaBot(page)
        .get_missing_days()         -> list[str]  (YYYY-MM-DD)
        .fill_entry(entry: dict)    -> bool
        .navigate_week(offset: int)
        .go_to_week_containing(date_str)
"""

import re
import time
from datetime import date, timedelta
from typing import Optional

from playwright.sync_api import Page, Locator, TimeoutError as PWTimeout

from utils import logger, console, take_screenshot, retry, cache_get, cache_set

# ──────────────────────────────────────────────
# Timeouts (ms)
# ──────────────────────────────────────────────
SHORT  = 5_000
MEDIUM = 15_000
LONG   = 30_000

KEKA_URL = "https://cloudsufi.keka.com/#/me/timesheet/all-timesheets"

# Weekday index -> abbreviated name in Keka headers
DAY_ABBR = {0: "MON", 1: "TUE", 2: "WED", 3: "THU", 4: "FRI", 5: "SAT", 6: "SUN"}


class KekaBot:
    def __init__(self, page: Page):
        self.page = page
        self._ensure_on_timesheet()

    # ──────────────────────────────────────────
    # Navigation
    # ──────────────────────────────────────────
    def _ensure_on_timesheet(self):
        """Make sure the browser is on the All Timesheets page."""
        if "#/me/timesheet" not in self.page.url:
            logger.info("Navigating to timesheet …")
            self.page.goto(KEKA_URL, wait_until="domcontentloaded", timeout=LONG)
        
        # Verify the grid is actually loaded (look for 'Total hours/day')
        try:
            self.page.wait_for_selector("text=Total hours/day", timeout=MEDIUM, state="visible")
            logger.debug("Timesheet grid verified")
        except:
            logger.warning("Timesheet grid not found via text, trying URL refresh")
            self.page.goto(KEKA_URL, wait_until="networkidle", timeout=LONG)

        # Dismiss any notification popups
        self._dismiss_popups()

    def _dismiss_popups(self):
        """Dismiss 'Enable notifications' or other blocking overlays."""
        popup_dismiss_selectors = [
            "button:has-text('Not Now')",
            "button:has-text('Dismiss')",
            "button:has-text('Close')",
            "[aria-label='Close']",
            "button.close",
        ]
        for sel in popup_dismiss_selectors:
            try:
                btn = self.page.locator(sel).first
                if btn.is_visible(timeout=1_500):
                    btn.click()
                    logger.debug(f"Dismissed popup via: {sel}")
                    time.sleep(0.5)
            except Exception:
                pass

    def _wait_for_timesheet_grid(self):
        """Wait until the timesheet grid (with '+ Add Time Entry') is visible."""
        try:
            self.page.wait_for_selector(
                "text=Add Time Entry",
                timeout=MEDIUM,
                state="visible",
            )
            logger.debug("Timesheet grid ready")
        except PWTimeout:
            logger.debug("'Add Time Entry' not visible — page may need navigation")
            try:
                # Try navigating to timesheet if not already there
                if "#/me/timesheet" not in self.page.url:
                    self.page.goto(KEKA_URL, wait_until="networkidle", timeout=LONG)
                    self.page.wait_for_selector("text=Add Time Entry", timeout=MEDIUM)
            except Exception:
                pass
        time.sleep(0.5)

    def navigate_week(self, offset: int = 0):
        """Navigate forward/backward by `offset` weeks."""
        if offset == 0:
            return
        for _ in range(abs(offset)):
            try:
                if offset > 0:
                    # Click '>' (next week)
                    btn = self.page.locator("button[aria-label*='next' i], a[aria-label*='next' i]").first
                else:
                    # Click '<' (previous week)
                    btn = self.page.locator("button[aria-label*='previous' i], a[aria-label*='previous' i]").first

                if not btn.is_visible(timeout=SHORT):
                    # Fallback: find by position (first/second arrow near week date)
                    arrows = self.page.locator("button svg, a svg").all()
                    btn = arrows[0 if offset < 0 else 1] if len(arrows) >= 2 else arrows[0]

                btn.click(timeout=SHORT)
                self.page.wait_for_load_state("networkidle", timeout=MEDIUM)
                time.sleep(0.8)
            except Exception as e:
                logger.warning(f"Week navigation failed: {e}")

    def _get_displayed_week_monday(self) -> Optional[date]:
        """
        Fix #5 — Read the week currently displayed in the browser from the DOM.
        Keka renders the week range as text like "Apr 6 - Apr 12" or "06 Apr - 12 Apr".
        Returns the Monday of the displayed week, or None if it cannot be parsed.
        """
        try:
            text = self.page.evaluate("""
                () => {
                    // Look for the week-range text near navigation arrows
                    const candidates = [
                        ...document.querySelectorAll(
                            '[class*="week"], [class*="date-range"], [class*="period"], '
                            + '.toolbar, .header, [class*="nav"]'
                        )
                    ];
                    // Also scan th/thead for a date pattern
                    const ths = [...document.querySelectorAll('thead th, thead td')];
                    const all = [...candidates, ...ths];
                    const months = 'JAN FEB MAR APR MAY JUN JUL AUG SEP OCT NOV DEC';
                    for (const el of all) {
                        const txt = el.textContent.toUpperCase().replace(/\\s+/g, ' ').trim();
                        // Match patterns: "6 APR", "APR 6", "06 APR"
                        if (/\\d{1,2}\\s+[A-Z]{3}|[A-Z]{3}\\s+\\d{1,2}/.test(txt)) {
                            return txt;
                        }
                    }
                    // Fallback: grab all visible header text
                    const hdr = document.querySelector('.page-header, .week-header, [class*="toolbar"]');
                    return hdr ? hdr.textContent.trim() : null;
                }
            """)
            if not text:
                return None
            # Parse the first date number + month we can find in the text
            import re as _re
            MONTH_MAP = {
                "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
                "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12
            }
            upper = text.upper()
            # Try "6 APR", "APR 6", "06APR", "APR06" style (optional spaces)
            m = _re.search(r'(\d{1,2})\s*([A-Z]{3})', upper)
            if not m:
                m = _re.search(r'([A-Z]{3})\s*(\d{1,2})', upper)
                if m:
                    day, mon = int(m.group(2)), m.group(1)
                else:
                    return None
            else:
                day, mon = int(m.group(1)), m.group(2)
            month_num = MONTH_MAP.get(mon[:3])
            if not month_num:
                return None
            year = date.today().year
            displayed = date(year, month_num, day)
            # Return the Monday of that week
            return displayed - timedelta(days=displayed.weekday())
        except Exception as e:
            logger.debug(f"Could not parse displayed week from DOM: {e}")
            return None

    def go_to_week_containing(self, target_date: str):
        """
        Fix #5 — Navigate to the week containing target_date (YYYY-MM-DD).
        Anchors the navigation offset on the week the browser is CURRENTLY showing,
        not on today's date, so it works even when the bot left the page on a
        different week after a previous entry.
        """
        td = date.fromisoformat(target_date)
        target_monday = td - timedelta(days=td.weekday())

        # Try to read the currently-displayed week from the DOM first
        displayed_monday = self._get_displayed_week_monday()
        if displayed_monday is None:
            # Fall back to today as the anchor
            today = date.today()
            displayed_monday = today - timedelta(days=today.weekday())
            logger.debug("Could not read displayed week from DOM; falling back to today")

        delta_weeks = (target_monday - displayed_monday).days // 7
        if delta_weeks != 0:
            logger.info(
                f"Navigating {delta_weeks:+d} week(s) — displayed Monday: {displayed_monday}, "
                f"target Monday: {target_monday}"
            )
            self.navigate_week(delta_weeks)
        else:
            logger.info(f"Already on the correct week for {target_date}")

    # ──────────────────────────────────────────
    # Missing days detection
    # ──────────────────────────────────────────
    def get_current_week_dates(self) -> list[str]:
        """Return Mon–Fri dates (YYYY-MM-DD) for the currently displayed week."""
        today = date.today()
        monday = today - timedelta(days=today.weekday())
        return [(monday + timedelta(days=i)).isoformat() for i in range(5)]

    def get_missing_days(self, skip_weekends: bool = True) -> list[str]:
        """Return working days (Mon–Fri) that have no logged hours."""
        working_days = self.get_current_week_dates()
        logged = self._detect_logged_dates()
        missing = [d for d in working_days if d not in logged]
        logger.info(f"Working days: {working_days}")
        logger.info(f"Logged: {sorted(logged)}")
        logger.info(f"Missing: {missing}")
        return missing

    def _detect_logged_dates(self) -> set[str]:
        """
        Attempt to detect already-filled dates.
        Currently returns empty set (conservative) — UI is complex to parse.
        """
        return set()

    # ──────────────────────────────────────────
    # Main fill entry
    # ──────────────────────────────────────────
    def fill_entry(self, entry: dict) -> bool:
        """
        Fill one timesheet entry.
        entry = {date, project, task, hours, comment, start_time, end_time}
        """
        date_str    = entry["date"]
        project     = entry["project"]
        phase       = entry.get("phase")
        task        = entry.get("task", "")
        hours       = entry["hours"]
        comment     = entry.get("comment", "")
        start_time  = entry.get("start_time", "09:00")
        end_time    = entry.get("end_time", "17:00")

        phase_log = f" | {phase}" if phase else ""
        logger.info(
            f"[FILL] {date_str} | {project}{phase_log} | {task} | "
            f"{hours}h | {start_time}-{end_time}"
        )

        try:
            success = retry(
                lambda: self._fill_entry_attempt(
                    date_str, project, task, hours,
                    phase=phase,
                    comment=comment, start_time=start_time, end_time=end_time,
                ),
                retries=3,
                delay=3.0,
                label=f"fill_entry({date_str})",
            )
            if success:
                logger.info(f"[OK] Entry filled for {date_str}")
                cache_set("last_project", project)
                cache_set("last_task", task)
            return success
        except Exception as e:
            logger.error(f"[ERROR] Failed to fill {date_str}: {e}")
            take_screenshot(self.page, f"error_{date_str}")
            return False

    def _fill_entry_attempt(
        self,
        date_str: str,
        project: str,
        task: str,
        hours: float,
        phase: Optional[str] = None,
        comment: str = "",
        start_time: str = "09:00",
        end_time: str = "18:00",
    ) -> bool:
        """
        Refined attempt flow:
          1. Check if row exists in grid
          2. If NOT, Click '+ Add Time Entry' and Select Project/Phase/Task (sidebar)
          3. Click '0:00' cell in the project row for the date
          4. Fill time in the popup
          5. Save
        """
        self._ensure_on_timesheet()
        
        # Step 1: Check if row already exists
        row_exists = self.page.evaluate(f"""
            ([pName, tName]) => {{
                const normalize = s => String(s).toLowerCase().replace(/[^a-z0-9]/g, "");
                const rows = [...document.querySelectorAll('tr, .table-row')];
                const normP = normalize(pName);
                const normT = normalize(tName || "");

                return rows.some(tr => {{
                    const txt = tr.innerText || tr.textContent || "";
                    if (txt.includes('Total hours/day')) return false;
                    const normTxt = normalize(txt);

                    // Check for Project code AND Task name match
                    const hasP = normTxt.includes(normP);
                    const hasT = normT ? normTxt.includes(normT) : true;
                    
                    return hasP && hasT;
                }});
            }}
        """, [project, task])

        if not row_exists:
            logger.info(f"Row for {project} | {task} not found. Adding via sidebar...")
            # Step 2: Click Add Time Entry
            self._click_add_time_entry() 
            time.sleep(1.5)
            
            # Additional wait for the dropdown panel to be definitely there
            try:
                self.page.wait_for_selector(".dropdown-menu.show, bs-dropdown-container, .side-panel, .slider", timeout=3000, state="visible")
                logger.debug("Dropdown/Sidebar panel verified visible")
            except:
                logger.warning("Dropdown panel not found! Attempting click again...")
                self._click_add_time_entry()
                time.sleep(1.5)
            
            # Step 3: Select Project in sidebar
            self._select_project_in_sidebar(project)
            time.sleep(0.5)
            
            # Step 4: Select Phase (if any) and Task in sidebar
            self._select_task_in_sidebar(task, phase=phase)
            time.sleep(3.0) # Wait longer for row to appear in grid
            
            # Ensure sidebar is closed (sometimes it lingers and blocks clicks)
            self.page.keyboard.press("Escape")
            time.sleep(1.0)
        else:
            logger.info(f"Existing row for {project} | {task} found. Using it directly.")
        
        # Step 5: Click the date cell for the project row
        logger.debug(f"Targeting cell for {date_str} in row {project}|{task}")
        self._click_project_day_cell(project, task, date_str)
        
        # Wait for the popup/container to appear
        popup = None
        for attempt in range(6):
            popup = self._get_active_time_container()
            if popup: break
            
            logger.debug(f"Popup not found (attempt {attempt+1}/6). Retrying click...")
            self._click_project_day_cell(project, task, date_str)
            if attempt == 2:
                # Try a double click on the third attempt
                logger.debug("Attempting double-click fallback...")
                self._click_project_day_cell(project, task, date_str, double=True)
            time.sleep(1.0)
        
        if not popup:
            logger.warning(f"Time entry popup did not appear for {date_str} after multiple clicks.")
            take_screenshot(self.page, f"popup_fail_{date_str}")
            # Diagnostic: log what's on screen
            self.page.evaluate("""
                () => {
                    const visible = [...document.querySelectorAll('div, section, kk-timesheet-add-entry, bs-dropdown-container')]
                        .filter(el => el.offsetHeight > 50 && (el.classList.contains('show') || el.classList.contains('panel') || el.tagName.includes('KK-')))
                        .map(el => el.tagName + '.' + [...el.classList].join('.'));
                    console.log("Visible potential containers:", visible);
                }
            """)
            return False
            
        # Step 6: Fill time & comment
        self._fill_time_popup(start_time, end_time, comment, hours=hours, container=popup)
        
        # Step 7: Save
        self._save_time_popup(container=popup)
        return True

    def _select_project_in_sidebar(self, project_name: str):
        """Find and click project in the sidebar list. Adds search support and improved state detection."""
        # 1. Reset state (close global search)
        self.page.keyboard.press("Escape")
        time.sleep(0.2)

        # 2. Try identifying the Projects column
        col_selectors = [
             "div:has(label:has-text('PROJECTS'))",
             "div:has(span:has-text('PROJECTS'))",
             ".project-column",
             "div:has(h4:has-text('PROJECTS'))"
        ]
        p_col = None
        for sel in col_selectors:
            try:
                el = self.page.locator(sel).first
                if el.is_visible(timeout=1000):
                    p_col = el
                    break
            except: pass
        
        # 3. Use Search if column container found
        if p_col:
            try:
                search_box = p_col.locator('input[type="text"], input[placeholder*="Search"]').first
                if search_box.is_visible(timeout=1000):
                    search_box.clear()
                    search_box.fill(project_name)
                    time.sleep(0.5)
            except Exception as e:
                logger.debug(f"Project search failed: {e}")

        # 4. Find and click project (JS robust search)
        clicked = self.page.evaluate(f"""
            async ([name]) => {{
                const delay = ms => new Promise(res => setTimeout(res, ms));
                const normalize = s => String(s).toLowerCase().replace(/[^a-z0-9]/g, "");
                const searchName = normalize(name);

                const findCol = (title) => {{
                    const tags = 'label, .text-muted, span, small, h1, h2, h3, h4, h5, h6, .text-label, .text-secondary';
                    const labels = Array.from(document.querySelectorAll(tags));
                    let lb = labels.find(l => l.innerText.trim().toUpperCase() === title.toUpperCase());
                    if (!lb) lb = labels.find(l => l.innerText.trim().toUpperCase().includes(title.toUpperCase()));
                    if (!lb) return null;
                    
                    let curr = lb.parentElement;
                    while (curr && curr !== document.body) {{
                        if (curr.querySelectorAll('.cursor-pointer, li, p').length >= 1) return curr;
                        curr = curr.parentElement;
                    }}
                    return lb.parentElement;
                }};

                const pColContainer = findCol('PROJECTS');
                if (!pColContainer) return "NO_PROJECT_COLUMN";

                // Optimization: see if it is already selected
                const active = [...pColContainer.querySelectorAll('.active, .selected, [class*="active"], [class*="selected"]')];
                if (active.some(el => normalize(el.innerText).includes(searchName))) return "ALREADY_SELECTED";

                const pCol = pColContainer.querySelector('div[style*="overflow"], [class*="scroll"], .list-container') || pColContainer;
                let lastScroll = -1;
                while (pCol.scrollTop !== lastScroll) {{
                    const items = [...pCol.querySelectorAll('.cursor-pointer, li, p, div, span, .item')];
                    const target = items.find(i => 
                        normalize(i.innerText).includes(searchName) && 
                        i.offsetHeight > 0 && i.innerText.trim().length < 150
                    );
                    
                    if (target) {{ 
                        target.click(); 
                        return "CLICKED"; 
                    }}
                    
                    lastScroll = pCol.scrollTop;
                    pCol.scrollTop += 250;
                    await delay(300);
                }}
                
                return "NOT_FOUND_AFTER_SCROLL";
            }}
        """, [project_name])

        if clicked not in ["CLICKED", "ALREADY_SELECTED"]:
            try:
                self.page.locator(f"text={project_name}").first.click(timeout=SHORT)
                logger.debug(f"Selected project '{project_name}' via general text locator")
            except:
                 raise RuntimeError(f"Could not select project '{project_name}' in sidebar (Detail: {clicked})")
        else:
            logger.debug(f"Project selection: {clicked}")

    def _select_task_in_sidebar(self, task_name: str, phase: Optional[str] = None):
        """Find and click task in the task popover/sidebar. Supports Phase hierarchy and native search."""
        time.sleep(0.5)
        
        # 1. Search for Phase (if provided) or Task directly
        try:
             # Find the task column container
             t_col_header = self.page.locator("text=/PHASE.TASKS/i, text=/TASKS/i").first
             if t_col_header.is_visible(timeout=2000):
                 t_col_parent = t_col_header.locator("xpath=..")
                 search = t_col_parent.locator('input[type="text"], input[placeholder*="Search"]').first
                 if (search.is_visible(timeout=1000)):
                     search.clear()
                     # If phase is provided, search for PHASE first to expand it
                     search_term = phase if phase else task_name
                     search.fill(search_term)
                     time.sleep(0.8)
        except Exception as e:
            logger.debug(f"Task search setup error: {e}")

        # 2. Main selection logic (handles phase clicking)
        result_obj = self.page.evaluate(f"""
            async ([name, phName]) => {{
                const delay = ms => new Promise(res => setTimeout(res, ms));
                const normalize = s => String(s).toLowerCase().replace(/[^a-z0-9]/g, "");
                const targetName = normalize(name);
                const phaseName = phName ? normalize(phName) : null;
                const log = [];

                const findCol = (title) => {{
                    const containers = [
                        ...document.querySelectorAll('.dropdown-menu.show'),
                        ...document.querySelectorAll('bs-dropdown-container'),
                        ...document.querySelectorAll('.side-panel'),
                        ...document.querySelectorAll('[class*="panel" i]')
                    ].filter(el => el.offsetHeight > 50);
                    
                    const root = containers[0] || document;
                    const labels = Array.from(root.querySelectorAll('label, span, h4, strong, b, div.sticky'));
                    let lb = labels.find(l => l.innerText.trim().toUpperCase().includes(title.toUpperCase()));
                    if (!lb) return null;
                    
                    let curr = lb.parentElement;
                    while (curr && curr !== root && curr !== document.body) {{
                        if (curr.offsetHeight > 80 && (curr.classList.contains('overflow-y-auto') || curr.className.includes('column'))) 
                            return curr;
                        curr = curr.parentElement;
                    }}
                    return lb.parentElement;
                }};

                const tCol = findCol('PHASE/TASKS') || findCol('TASKS');
                if (!tCol) return {{ status: "NO_TASK_COLUMN", log }};

                const performSelection = async (isSecondAttempt = false) => {{
                    let lastScroll = -1;
                    let iterations = 0;
                    if (tCol.offsetHeight === 0) await delay(500);

                    while (tCol.scrollTop !== lastScroll && iterations < 30) {{
                        iterations++;
                        const items = [...tCol.querySelectorAll('.cursor-pointer, li, .item, p, div, span')];
                        const visibleItems = items.filter(i => {{
                            return i.innerText && i.innerText.trim().length > 0 && 
                                   (i.offsetHeight > 0 || i.getClientRects().length > 0);
                        }});
                        
                        log.push("Iter " + (isSecondAttempt ? "2-" : "") + iterations + ": " + visibleItems.length + " items");
                        if (iterations === 1) {{
                            log.push("Top 10 items: " + visibleItems.slice(0, 10).map(i => i.innerText.trim()).join(" | "));
                        }}
                        
                        // Priority 1: Match Task Name
                        const taskMatch = visibleItems.find(i => normalize(i.innerText) === targetName);
                        if (taskMatch) {{ taskMatch.click(); return "CLICKED_TASK"; }}

                        // Priority 2: Match Phase Name (if provided)
                        if (phaseName) {{
                             const phaseMatch = visibleItems.find(i => {{
                                const txt = normalize(i.innerText);
                                // Phase item should contain the phase name and not be too long
                                return txt.includes(phaseName) && i.innerText.length < 150;
                             }});
                             if (phaseMatch) {{
                                 const isExpanded = !!(phaseMatch.querySelector('[class*="down"], [class*="expanded"]') || phaseMatch.innerHTML.includes('down'));
                                 if (isExpanded) {{
                                     log.push("Phase " + phaseName + " already expanded.");
                                 }} else {{
                                     // Click chevron specifically if possible
                                     const chevron = phaseMatch.querySelector('[class*="chevron"], [class*="ki-"], [class*="arrow"]');
                                     if (chevron) {{
                                         chevron.click();
                                         log.push("Clicked chevron for phase: " + phaseName);
                                     }} else {{
                                         phaseMatch.click(); 
                                         log.push("Clicked phase text: " + phaseName);
                                     }}
                                     await delay(2000); // Wait longer for expansion
                                     return "EXPANDED_PHASE";
                                 }}
                             }}
                        }}

                        // Priority 3: Partial task match
                        const partialTask = visibleItems.find(i => {{
                            const txt = normalize(i.innerText);
                            return txt.includes(targetName) && 
                                   !(i.querySelector('[class*="chevron"], [class*="ki-"], [class*="arrow"]'));
                        }});
                        if (partialTask) {{ partialTask.click(); return "CLICKED_PARTIAL_TASK"; }}

                        lastScroll = tCol.scrollTop;
                        tCol.scrollTop += 300;
                        await delay(300);
                    }}
                    return "NOT_FOUND";
                }};

                let result = await performSelection();
                if (result === "EXPANDED_PHASE") {{
                    // Clear search to see hidden tasks
                    const root = tCol.closest('.dropdown-menu.show') || tCol.closest('bs-dropdown-container') || document;
                    const searchBox = root.querySelector('input');
                    if (searchBox) {{
                        searchBox.value = "";
                        searchBox.dispatchEvent(new Event('input', {{ bubbles: true }}));
                        await delay(500);
                    }}
                    // Second attempt: don't allow EXPANDED_PHASE again to avoid loops
                    result = await performSelection(true);
                    if (result === "EXPANDED_PHASE") result = "PHASE_ALREADY_EXPANDED_BUT_TASK_NOT_FOUND";
                }}
                
                return {{ status: result, log }};
            }}
        """, [task_name, phase])
        
        clicked = result_obj.get("status") or "UNKNOWN_ERROR"
        js_log = result_obj.get("log", [])
        for line in js_log:
            logger.debug(f"[JS-TASK] {str(line)}")
        
        logger.debug(f"Task selection result: {str(clicked)}")
        
        if clicked not in ["CLICKED_TASK", "CLICKED_PARTIAL_TASK"]:
            try:
                # Text fallback
                self.page.locator(f"text={task_name}").last.click(timeout=SHORT)
                logger.debug(f"Selected task via text fallback")
            except:
                raise RuntimeError(f"Could not select task '{task_name}' in sidebar (Detail: {clicked}, Phase: {phase})")

    def _click_project_day_cell(self, project_name: str, task_name: str, date_str: str, double: bool = False):
        """
        Table-structure approach: navigate the actual DOM table to find the
        correct <td> element at the column/row intersection and call element.click()
        directly in JS — no mouse coordinates.

        Raw page.mouse.click(x, y) sends a synthetic browser event that Angular's
        change-detection can misroute; calling .click() on the actual element fires
        the event on the correct component instance every time.

        Column resolution priority:
          1. header contains weekday-abbr + day-number + month-abbr  (e.g. "THU 9 APR")
          2. header contains weekday-abbr + day-number               (month omitted)
          3. header contains weekday-abbr alone                      (last resort)

        Cell resolution priority (once the column index is known):
          A. <td> / <th> inside the project row whose X-centre aligns with the header
          B. Any child element ([class*=cell], div, span) by X-centre alignment
          C. Fallback to _click_day_cell if nothing clickable is found
        """
        target = date.fromisoformat(date_str)
        target_abbr  = DAY_ABBR[target.weekday()]   # e.g. "THU"
        target_day   = target.day                   # e.g. 9
        target_month = target.strftime("%b").upper() # e.g. "APR"

        result = self.page.evaluate("""
            ([pName, tName, abbr, dayNum, monthAbbr]) => {
                const log = [];

                // ── 1. Locate the correct column header ──────────────────────
                const grid = document.querySelector(
                    '.timesheet-grid, .ts-grid, table.timesheet-table'
                ) || document;

                const allHeaders = [
                    ...grid.querySelectorAll('thead th, thead td, .day-header, .date-col')
                ].filter(h => h.offsetHeight > 0);

                log.push('Headers: ' + allHeaders.map(h => h.textContent.trim()).join(' | '));

                let colIdx = -1;

                // Priority 1: abbr + dayNum + month
                colIdx = allHeaders.findIndex(h => {
                    const t = h.textContent.toUpperCase().replace(/\\s+/g, ' ');
                    const dayRegex = new RegExp(`\\\\b0?${dayNum}\\\\b`);
                    return t.includes(abbr) && dayRegex.test(t) && t.includes(monthAbbr);
                });

                // Priority 2: abbr + dayNum
                if (colIdx === -1) {
                    colIdx = allHeaders.findIndex(h => {
                        const t = h.textContent.toUpperCase().replace(/\\s+/g, ' ');
                        const dayRegex = new RegExp(`\\\\b0?${dayNum}\\\\b`);
                        return t.includes(abbr) && dayRegex.test(t);
                    });
                }

                // Priority 3: abbr alone
                if (colIdx === -1) {
                    colIdx = allHeaders.findIndex(h => {
                        const t = h.textContent.toUpperCase().replace(/\\s+/g, ' ');
                        return t.includes(abbr);
                    });
                }

                if (colIdx === -1) {
                    return { error: 'HEADER_NOT_FOUND', abbr, dayNum, monthAbbr, log };
                }

                const targetHeader = allHeaders[colIdx];
                log.push('Col ' + colIdx + ': ' + targetHeader.textContent.trim());

                // ── 2. Locate the project row ─────────────────────────────────
                const normalize = s => String(s).toLowerCase().replace(/[^a-z0-9]/g, "");
                const normP = normalize(pName);
                const normT = normalize(tName || "");

                const rows = [
                    ...grid.querySelectorAll('tbody tr, .table-row')
                ].filter(r => r.offsetHeight > 0);

                const pRow = rows.find(tr => {
                    const txt = (tr.innerText || tr.textContent || '').toLowerCase();
                    if (txt.includes('total hours/day')) return false;
                    
                    const normTxt = normalize(txt);
                    const hasP = normTxt.includes(normP);
                    const hasT = normT ? normTxt.includes(normT) : true;
                    
                    return hasP && hasT;
                });

                if (!pRow) {
                    return { error: 'ROW_NOT_FOUND', pName, rowCount: rows.length, log };
                }
                log.push('Row found: ' + (pRow.innerText || '').substring(0, 60).replace(/\\n/g, ' '));

                // ── 3. Find the exact cell and click it (no mouse coordinates) ─
                const headerMidX = targetHeader.getBoundingClientRect().left
                                 + targetHeader.getBoundingClientRect().width / 2;

                // Before clicking, if there is an active input/textarea, try to blur it or escape
                const active = document.activeElement;
                if (active && (active.tagName === 'INPUT' || active.tagName === 'TEXTAREA')) {
                    active.blur();
                }

                // Strategy A – <td>/<th> cells aligned to the header column
                const tds = [...pRow.querySelectorAll('td, th')].filter(
                    el => el.offsetWidth > 0 && el.offsetHeight > 0
                );
                let bestTd = null, bestTdDist = Infinity;
                for (const td of tds) {
                    const r    = td.getBoundingClientRect();
                    const midX = r.left + r.width / 2;
                    const dist = Math.abs(midX - headerMidX);
                    if (dist < bestTdDist) { bestTdDist = dist; bestTd = td; }
                }
                if (bestTd && bestTdDist < 60) {
                    // 1. JS-based click events
                    bestTd.focus();
                    bestTd.click();
                    bestTd.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true }));
                    bestTd.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, cancelable: true }));
                    bestTd.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));
                    
                    // 2. Return coordinates for a potential mouse-click fallback in Python
                    const rect = bestTd.getBoundingClientRect();
                    return {
                        success: true, method: 'td_x_align',
                        dist: bestTdDist.toFixed(1),
                        headerText: targetHeader.textContent.trim(),
                        x: rect.left + rect.width / 2,
                        y: rect.top + rect.height / 2,
                        log
                    };
                }

                // Strategy B – any visible child element aligned to the header column
                const children = [...pRow.querySelectorAll(
                    '[class*="cell"], [class*="day"], [class*="col"], span, div'
                )].filter(el => el.offsetWidth > 5 && el.offsetHeight > 0);

                let bestEl = null, bestElDist = Infinity;
                for (const el of children) {
                    const r    = el.getBoundingClientRect();
                    const midX = r.left + r.width / 2;
                    const dist = Math.abs(midX - headerMidX);
                    if (dist < bestElDist) { bestElDist = dist; bestEl = el; }
                }
                if (bestEl && bestElDist < 60) {
                    bestEl.focus();
                    bestEl.click();
                    bestEl.dispatchEvent(new MouseEvent('click', { bubbles: true }));
                    
                    return {
                        success: true, method: 'child_x_align',
                        dist: bestElDist.toFixed(1),
                        tag: bestEl.tagName,
                        headerText: targetHeader.textContent.trim(), log
                    };
                }

                return { error: 'CELL_NOT_FOUND', colIdx, tdCount: tds.length, log };
            }
        """, [project_name, task_name, target_abbr, target_day, target_month])

        if isinstance(result, dict) and result.get("success"):
            logger.debug(
                f"Day cell clicked via '{result.get('method')}' "
                f"(dist={result.get('dist')}px, x={result.get('x')}, y={result.get('y')}) "
                f"header='{result.get('headerText')}'"
            )
            # Mouse-click fallback if JS didn't trigger enough
            if "x" in result and "y" in result:
                try:
                    if double:
                        self.page.mouse.dblclick(result["x"], result["y"])
                    else:
                        self.page.mouse.click(result["x"], result["y"])
                    time.sleep(0.2)
                except: pass
            return True
        else:
            logger.warning(f"Project day cell click failed: {result}")
            self._click_day_cell(date_str)
            return False

    # ── UI interaction helpers ─────────────────

    def _click_add_time_entry(self):
        """Click the GLOBAL '+ Add Time Entry' button to open the sidebar/dropdown."""
        # Check if already open
        is_open = self.page.evaluate("""
            () => !!document.querySelector('.dropdown-menu.show, bs-dropdown-container, .side-panel')
        """)
        if is_open:
            logger.debug("Panel/Dropdown already open")
            return

        selectors = [
            "button:has-text('Add Time Entry')",
            "a:has-text('Add Time Entry')",
            ".timesheet-grid-header button:has-text('Add Time Entry')",
            "header button:has-text('Add Time Entry')",
            "button.btn-primary:has-text('Add Time Entry')",
            "text=Add Time Entry"
        ]
        for sel in selectors:
            try:
                btn = self.page.locator(sel).first
                if btn.is_visible(timeout=1000):
                    btn.click()
                    logger.debug(f"Clicked 'Add Time Entry' via {sel}")
                    return
            except: pass
        
        # JS Fallback
        self.page.evaluate("""
            () => {
                const btn = [...document.querySelectorAll('a, button, span')].find(el => 
                    el.textContent.includes('Add Time Entry') && el.offsetHeight > 0
                );
                if (btn) btn.click();
            }
        """)

    def _select_project(self, project_name: str):
        """Select project from the panel. Tries search input first, then direct container click."""
        try:
            # 1. Try search if input is found
            proj_input = self._get_project_input()
            if proj_input:
                proj_input.click()
                proj_input.fill("")
                time.sleep(0.3)
                proj_input.type(project_name, delay=50)
                time.sleep(1.0)
        except Exception:
            logger.debug(f"Project search input not found, searching directly for '{project_name}'")

        # 2. Click the project option in the PROJECTS list
        # We look for a container that has text 'PROJECTS' and click the project name inside it
        clicked = self.page.evaluate(f"""
            (name) => {{
                const containers = [...document.querySelectorAll('div, section')].filter(el => 
                    el.textContent.includes('PROJECTS') && el.offsetHeight > 0
                );
                for (const cont of containers) {{
                    const items = [...cont.querySelectorAll('.cursor-pointer, li, div, p')];
                    const target = items.find(i => 
                        i.textContent.toLowerCase().includes(name.toLowerCase()) && 
                        i.offsetHeight > 0
                    );
                    if (target) {{
                        target.click();
                        return true;
                    }}
                }}
                return false;
            }}
        """, project_name)

        if not clicked:
            # Last resort: text selector
            self.page.locator(f"text={project_name}").first.click(timeout=SHORT)
            logger.debug(f"Selected project '{project_name}' via text selector")
        else:
            logger.debug(f"Selected project '{project_name}' via container JS")

    def _get_project_input(self) -> Locator:
        """Find the PROJECTS search input in the inline panel."""
        # Strategy 1: Look for "PROJECTS" label/header and find the input sibling
        try:
            # Try to find the container that has 'PROJECTS' and an input
            container_selectors = [
                "div:has(label:has-text('PROJECTS'))",
                "div:has(span:has-text('PROJECTS'))",
                "div:has(h4:has-text('PROJECTS'))",
            ]
            for cs in container_selectors:
                cont = self.page.locator(cs).first
                if cont.is_visible(timeout=1000):
                    inp = cont.locator("input").first
                    if inp.is_visible(timeout=500):
                        return inp
        except:
            pass

        # Strategy 2: Direct placeholder or attribute search
        selectors = [
            "input[placeholder='Search']",
            "input[placeholder*='Search']:visible",
            "input[placeholder*='project' i]",
            "[class*='project'] input",
        ]
        for sel in selectors:
            try:
                els = self.page.locator(sel).all()
                for el in els:
                    if el.is_visible(timeout=1500):
                        return el
            except Exception:
                continue
        
        # Strategy 3: Just return the first visible input in the sidebar if others fail
        try:
            sidebar = self.page.locator("[class*='slider'], [class*='panel'], .side-panel").first
            if sidebar.is_visible(timeout=1000):
                inp = sidebar.locator("input").first
                if inp.is_visible(timeout=500):
                    return inp
        except:
            pass

        raise RuntimeError("Cannot find project search input")

    def _click_project_option(self, project_name: str):
        """Click the project matching `project_name` in the inline dropdown."""
        keywords = [w.lower() for w in project_name.lower().split() if len(w) > 2]

        # Try multiple approaches to find the list item
        option_selectors = [
            f"text={project_name}",
            f"[class*='item']:has-text('{project_name}')",
            f"li:has-text('{project_name}')",
            f"[role='option']:has-text('{project_name}')",
            f"div:has-text('{project_name}')",
        ]

        for sel in option_selectors:
            try:
                opts = self.page.locator(sel).all()
                for opt in opts:
                    if not opt.is_visible(timeout=800):
                        continue
                    text = opt.inner_text(timeout=500).lower()
                    # Must contain key parts of the project name
                    if any(kw in text for kw in keywords):
                        opt.click()
                        logger.debug(f"Selected project '{project_name}'")
                        return
            except Exception:
                continue

        # Keyboard fallback
        logger.warning(f"Could not click project option '{project_name}', using ArrowDown+Enter")
        self.page.keyboard.press("ArrowDown")
        time.sleep(0.3)
        self.page.keyboard.press("Enter")

    def _select_task(self, task_name: str):
        """Select task from the panel. Pick first if name doesn't match."""
        try:
            task_input = self._get_task_input()
            if task_input:
                task_input.click()
                task_input.fill("")
                if task_name:
                    task_input.type(task_name, delay=50)
                time.sleep(1.0)
        except Exception:
            logger.debug("Task search input not found, searching directly in container")

        # Click the task option
        clicked = self.page.evaluate(f"""
            (name) => {{
                const containers = [...document.querySelectorAll('div, section')].filter(el => 
                    (el.textContent.includes('PHASE/TASKS') || el.textContent.includes('TASKS')) && 
                    el.offsetHeight > 0
                );
                for (const cont of containers) {{
                    const items = [...cont.querySelectorAll('.cursor-pointer, li, div, p')];
                    // If name provided, try to match it
                    if (name) {{
                        const target = items.find(i => 
                            i.textContent.toLowerCase().includes(name.toLowerCase()) && 
                            i.offsetHeight > 0 && i.textContent.length < 100
                        );
                        if (target) {{ target.click(); return true; }}
                    }}
                    // Otherwise pick first clickable item that isn't search
                    const first = items.find(i => 
                        i.offsetHeight > 0 && i.textContent.trim().length > 2 && 
                        !i.textContent.includes('Search') && !i.querySelector('input')
                    );
                    if (first) {{ first.click(); return true; }}
                }}
                return false;
            }}
        """, task_name)

        if not clicked:
             # Final fallback: pick ANY clickable option in the right side of the panel
             logger.warning("Could not select task via container, using fallback")
             self._click_task_option(task_name)

    def _get_task_input(self) -> Locator:
        """Find the PHASE/TASKS search input in the inline panel."""
        # Strategy 1: Look for "PHASE/TASKS" label/header and find the input sibling
        try:
            container_selectors = [
                "div:has(label:has-text('PHASE/TASKS'))",
                "div:has(span:has-text('PHASE/TASKS'))",
                "div:has(label:has-text('TASKS'))",
            ]
            for cs in container_selectors:
                cont = self.page.locator(cs).first
                if cont.is_visible(timeout=1000):
                    inp = cont.locator("input").first
                    if inp.is_visible(timeout=500):
                        return inp
        except:
            pass

        # Strategy 2: Original selectors
        selectors = [
            "input[placeholder='Search phase/task']",
            "input[placeholder*='phase' i]",
            "input[placeholder*='task' i]",
        ]
        for sel in selectors:
            try:
                el = self.page.locator(sel).first
                if el.is_visible(timeout=3000):
                    return el
            except Exception:
                continue

        # Fallback: second visible "Search" input
        try:
            inputs = self.page.locator("input[placeholder='Search']").all()
            visible = [i for i in inputs if i.is_visible(timeout=500)]
            if len(visible) >= 2:
                return visible[1]
            if len(visible) == 1:
                return visible[0]
        except Exception:
            pass

        raise RuntimeError("Cannot find task (phase/task) search input")

    def _click_task_option(self, task_name: str):
        """Click a task from the PHASE/TASKS panel. Always picks one."""
        # Try to match task_name first
        if task_name:
            keywords = [w.lower() for w in task_name.lower().split() if len(w) > 2]
            option_selectors = [
                f"[class*='item']:has-text('{task_name}')",
                f"li:has-text('{task_name}')",
                f"[role='option']:has-text('{task_name}')",
                f"text={task_name}",
            ]
            for sel in option_selectors:
                try:
                    opts = self.page.locator(sel).all()
                    for opt in opts:
                        if not opt.is_visible(timeout=800):
                            continue
                        text = opt.inner_text(timeout=500).lower()
                        if any(kw in text for kw in keywords):
                            opt.click()
                            logger.debug(f"Selected task '{task_name}'")
                            return
                except Exception:
                    continue

        # Fallback: click first available task item (not "No Tasks found")
        try:
            # Tasks appear as items in the right panel
            all_options = self.page.locator(
                "[class*='item'], [role='option'], li"
            ).all()
            for opt in all_options:
                try:
                    if not opt.is_visible(timeout=500):
                        continue
                    text = opt.inner_text(timeout=300).strip()
                    if text and "no tasks" not in text.lower() and len(text) > 1:
                        opt.click()
                        logger.debug(f"Selected first available task: {text[:60]}")
                        return
                except Exception:
                    continue
        except Exception:
            pass

        # Last resort: keyboard
        logger.warning("Using ArrowDown+Enter to select task")
        self.page.keyboard.press("ArrowDown")
        time.sleep(0.3)
        self.page.keyboard.press("Enter")

    def _click_total_day_cell(self, date_str: str):
        """
        Click the '0:00' cell (or existing time) in the 'Total hours/day' row.
        This often triggers the 'Add Time Entry' panel for that specific day.
        """
        target = date.fromisoformat(date_str)
        target_day   = target.day
        target_month = target.strftime("%b").upper()
        target_abbr  = DAY_ABBR[target.weekday()]

        # Strategy 1: Precise JS alignment within the Total row
        clicked = self.page.evaluate("""
            ([day, month, abbr]) => {
                // 1. Find the column headers to determine X position
                const headers = [...document.querySelectorAll('th, .date-col, thead td, thead th')];
                let targetColHeader = null;
                for (const h of headers) {
                    const txt = h.textContent.toUpperCase();
                    if ((txt.includes(day.toString()) && txt.includes(month)) || txt.includes(abbr)) {
                        targetColHeader = h;
                        break;
                    }
                }
                if (!targetColHeader) return false;
                const headerRect = targetColHeader.getBoundingClientRect();
                const headerMidX = headerRect.left + headerRect.width / 2;

                // 2. Find the row that contains "Total hours/day"
                const rows = [...document.querySelectorAll('tr')];
                const totalRow = rows.find(tr => tr.textContent.includes('Total hours/day'));
                if (!totalRow) return false;

                // 3. Find cells in that row and click the one aligned with the header
                const cells = [...totalRow.querySelectorAll('td, .bg-hover')];
                for (const cell of cells) {
                    const rect = cell.getBoundingClientRect();
                    const cellMidX = rect.left + rect.width / 2;
                    if (Math.abs(cellMidX - headerMidX) < 40) {
                        cell.click();
                        return true;
                    }
                }
                
                // Fallback: if no alignment, try clicking the cell that contains '0:00' in that row
                const zeroCell = [...totalRow.querySelectorAll('td, p')].find(el => el.textContent.trim() === '0:00');
                if (zeroCell) {
                    zeroCell.click();
                    return true;
                }

                return false;
            }
        """, [target_day, target_month, target_abbr])

        if clicked:
            return

        # Fallback to general day cell click if special "Total" click failed
        logger.warning(f"Could not specifically click Total row cell for {date_str}, trying general fallback")
        self._click_day_cell(date_str)

    def _click_day_cell(self, date_str: str):
        """
        Generic fallback to click the '0:00' cell under the correct day column.
        """
        target = date.fromisoformat(date_str)
        target_day   = target.day
        target_month = target.strftime("%b").upper()
        target_abbr  = DAY_ABBR[target.weekday()]

        # Strategy 1: JS-based column index → 0:00 cell matching
        clicked = self._js_click_day_cell(target_day, target_month, target_abbr)
        if clicked:
            return

        # Strategy 2: Find column header and click at same X position
        clicked = self._positional_click_day_cell(target_day, target_month, target_abbr)
        if clicked:
            return
    def _js_click_day_cell(self, day: int, month: str, abbr: str) -> bool:
        """
        Fallback: find the 0:00 cell for the target day column and click its
        nearest <td> ancestor (not the raw text-node parent), so Angular receives
        the click on a proper table-cell component.

        Header resolution priority: dayNum+month > dayNum+abbr > abbr alone.
        Cell resolution: X-centre alignment to the header only (no index maths).
        """
        try:
            result = self.page.evaluate(f"""
                () => {{
                    const dayNum  = {day};
                    const month   = '{month}';
                    const dayAbbr = '{abbr}';

                    const headers = [
                        ...document.querySelectorAll(
                            'th, [class*="day-header"], [class*="date-col"], thead td'
                        )
                    ].filter(h => h.offsetHeight > 0);

                    let targetHeaderRect = null;

                    // Priority 1: dayNum + month
                    for (const h of headers) {{
                        const t = h.textContent.toUpperCase().replace(/\\s+/g, ' ').trim();
                        const dayRegex = new RegExp(`\\\\b0?${dayNum}\\\\b`);
                        if (dayRegex.test(t) && t.includes(month)) {{
                            targetHeaderRect = h.getBoundingClientRect();
                            break;
                        }}
                    }}
                    // Priority 2: dayNum + abbr
                    if (!targetHeaderRect) {{
                        for (const h of headers) {{
                            const t = h.textContent.toUpperCase().replace(/\\s+/g, ' ').trim();
                            const dayRegex = new RegExp(`\\\\b0?${dayNum}\\\\b`);
                            if (dayRegex.test(t) && t.includes(dayAbbr)) {{
                                targetHeaderRect = h.getBoundingClientRect();
                                break;
                            }}
                        }}
                    }}
                    // Priority 3: abbr alone
                    if (!targetHeaderRect) {{
                        for (const h of headers) {{
                            const t = h.textContent.toUpperCase().replace(/\\s+/g, ' ').trim();
                            if (t.includes(dayAbbr)) {{
                                targetHeaderRect = h.getBoundingClientRect();
                                break;
                            }}
                        }}
                    }}
                    if (!targetHeaderRect) return false;

                    const headerMidX = targetHeaderRect.left + targetHeaderRect.width / 2;

                    // Collect all 0:00 text nodes and walk up to the nearest <td>
                    const findTdAncestor = (el) => {{
                        let cur = el;
                        while (cur && cur !== document.body) {{
                            if (cur.tagName === 'TD' || cur.tagName === 'TH') return cur;
                            cur = cur.parentElement;
                        }}
                        return el; // return self if no <td> ancestor found
                    }};

                    const walker = document.createTreeWalker(
                        document.body, NodeFilter.SHOW_TEXT,
                        {{ acceptNode: n => n.textContent.trim() === '0:00'
                            ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_REJECT }}
                    );
                    const zeroCells = [];
                    let node;
                    while ((node = walker.nextNode())) {{
                        zeroCells.push(findTdAncestor(node.parentElement));
                    }}
                    if (!zeroCells.length) {{
                        const direct = [...document.querySelectorAll('*')].filter(
                            el => el.children.length === 0 && el.textContent.trim() === '0:00'
                        );
                        zeroCells.push(...direct.map(findTdAncestor));
                    }}

                    // Click the cell whose X-centre aligns with the target header
                    for (const cell of zeroCells) {{
                        const r = cell.getBoundingClientRect();
                        if (!r.width) continue;
                        if (Math.abs((r.left + r.width / 2) - headerMidX) < 40) {{
                            cell.click();
                            return true;
                        }}
                    }}
                    return false;
                }}
            """)
            return bool(result)
        except Exception as e:
            logger.debug(f"JS day cell click failed: {e}")
            return False

    def _positional_click_day_cell(self, day: int, month: str, abbr: str) -> bool:
        """Find column header by text, then click a 0:00 cell in the same X range."""
        try:
            # Find the column header element
            header_sel = (
                f"th:has-text('{abbr}'), "
                f"[class*='header']:has-text('{abbr}'), "
                f"td:has-text('{abbr}')"
            )
            headers = self.page.locator(header_sel).all()
            target_header = None
            for h in headers:
                try:
                    txt = h.inner_text(timeout=300).upper()
                    if abbr in txt:
                        target_header = h
                        break
                except Exception:
                    pass

            if not target_header:
                return False

            header_box = target_header.bounding_box()
            if not header_box:
                return False

            # Find all 0:00 cells and click the one closest in X to this header
            zero_cells = self.page.locator("text=0:00").all()
            best = None
            best_dist = float("inf")
            for c in zero_cells:
                try:
                    if not c.is_visible(timeout=300):
                        continue
                    box = c.bounding_box()
                    if not box:
                        continue
                    cell_mid_x = box["x"] + box["width"] / 2
                    header_mid_x = header_box["x"] + header_box["width"] / 2
                    dist = abs(cell_mid_x - header_mid_x)
                    if dist < best_dist:
                        best_dist = dist
                        best = c
                except Exception:
                    pass

            # Fix #4 — tight tolerance (≤35px) to avoid clicking an adjacent column
            if best and best_dist < 35:
                best.click()
                return True

        except Exception as e:
            logger.debug(f"Positional cell click failed: {e}")
        return False

    # ── Time entry popup (after clicking 0:00 cell) ──────────────────────────

    def _get_active_time_container(self) -> Locator:
        """Find the active container (panel, slider, or row) for time entry."""
        selectors = [
            "kk-timesheet-add-entry",
            "kk-time-entry-popover",
            ".side-panel",
            ".popover",
            "bs-dropdown-container",
            ".dropdown-menu.show",
            "[class*='slider']",
            ".time-entry-row",
            ".modal-content",
            ".modal-body"
        ]
        
        # Try to find a visible container that has a Save button or time inputs or a comment box
        for sel in selectors:
            try:
                # We use .last because sometimes old panels linger in DOM, new one is last
                # Relaxed filter: just check if it's a popover/panel with useful inputs
                cont = self.page.locator(sel).filter(
                    has=self.page.locator("button:has-text('Save'), button:has-text('Update'), textarea, input[formcontrolname*='Time'], input[placeholder*='0:00']")
                ).last
                if cont.is_visible(timeout=800):
                    logger.debug(f"Active time container found: {sel}")
                    return cont
            except:
                continue
        
        # Final check: is there any visible time input or textarea on the page?
        try:
            # Check for popovers that might not match the above selectors
            popover = self.page.locator(".popover, .ngx-popover, [class*='popover']").filter(has=self.page.locator("textarea, input")).last
            if popover.is_visible(timeout=500):
                logger.debug("Found generic popover via class")
                return popover

            inputs = self.page.locator("input[formcontrolname*='startTime'], input[formcontrolname*='endTime'], kk-hour-picker input, textarea[placeholder*='comment']").all()
            for inp in inputs:
                if inp.is_visible(timeout=200):
                    logger.debug("Found visible time/comment input, using page as scope")
                    return self.page
        except: pass
        
        # Fallback: don't return page scope, return None to indicate failure
        return None

    def _fill_time_popup(self, start_time: str, end_time: str, comment: str, hours: float = None, container: Optional[Locator] = None):
        """
        After clicking a day's 0:00 cell, Keka shows an explicit time-entry
        row/popup with start time, end time, and comment fields.
        """
        scope = container if container is not None else self._get_active_time_container()
        if scope is None:
            logger.debug("Cannot fill time: no active container found")
            return
        
        # Wait for any animation to finish
        time.sleep(1.0)
        
        has_time_fields = False
        try:
            # Set start time
            self._set_time_field("start", start_time, container=scope)
            time.sleep(0.3)

            # Set end time
            self._set_time_field("end", end_time, container=scope)
            time.sleep(0.3)
            has_time_fields = True
        except Exception as e:
            logger.debug(f"Could not set start/end time: {e}")

        # If start/end fields were missing, try hours field
        if not has_time_fields and hours is not None:
            try:
                self._set_hours_field(hours, container=scope)
                time.sleep(0.3)
                has_time_fields = True
            except Exception as e:
                logger.debug(f"Could not set hours field in container: {e}")

        # If still no time fields found, it might be an inline cell input
        if not has_time_fields and hours is not None:
            try:
                logger.debug("Attempting to fill hours in active element (inline cell)...")
                # Keka often makes the cell itself an input when clicked
                self.page.evaluate(f"""
                    (h) => {{
                        const el = document.activeElement;
                        if (el && (el.tagName === 'INPUT' || el.contentEditable === 'true')) {{
                            el.value = String(h);
                            el.innerText = String(h);
                            el.dispatchEvent(new Event('input', {{ bubbles: true }}));
                            el.dispatchEvent(new Event('change', {{ bubbles: true }}));
                            el.blur(); // Blur to trigger save/validation
                        }}
                    }}
                """, hours)
                time.sleep(0.5)
            except Exception as e:
                logger.debug(f"Inline fill failed: {e}")

        # Set comment
        if comment:
            self._enter_comment(comment, container=scope)
            time.sleep(0.2)

    def _set_hours_field(self, hours: float, container: Optional[Locator] = None):
        """Set the hours field if start/end time aren't available."""
        scope = container if container is not None else self.page
        
        selectors = [
            "input[formcontrolname*='hours' i]",
            "input[placeholder*='0:00']",
            "input[placeholder*='hours' i]",
            "input[type='number']",
            "input[name*='hours' i]",
            "kk-hour-picker input",
        ]
        for sel in selectors:
            try:
                inp = scope.locator(sel).first
                if inp.is_visible(timeout=1000):
                    inp.click()
                    inp.press("Control+a")
                    inp.press("Backspace")
                    val = str(hours)
                    inp.fill(val)
                    inp.press("Enter")
                    logger.debug(f"Hours set to {val} via: {sel}")
                    return
            except:
                continue
        raise RuntimeError(f"Could not find hours field for entry ({hours}h)")

    def _set_time_field(self, field_type: str, time_value: str, container: Optional[Locator] = None):
        """
        Set a start ('start') or end ('end') time field.
        """
        scope = container if container is not None else self.page
        
        label_keywords = {
            "start": ["Start Time", "Start", "From"],
            "end":   ["End Time",   "End",   "To"],
        }

        for label in label_keywords.get(field_type, []):
            try:
                # 1. Direct label relationship
                inp = scope.locator(f"label:has-text('{label}')").locator("xpath=./following-sibling::input | ./following-sibling::div//input").first
                if inp.is_visible(timeout=500):
                    self._type_time(inp, time_value)
                    return

                # 2. Container-based
                cont = scope.locator(f"div:has(label:has-text('{label}')):visible, [class*='field']:has(label:has-text('{label}'))").first
                if cont.is_visible(timeout=500):
                    inp = cont.locator("input:visible").first
                    if inp.is_visible(timeout=500):
                        self._type_time(inp, time_value)
                        return

                # 3. Simple text label match
                inp = scope.locator(f"text={label}").locator("xpath=..//input").first
                if inp.is_visible(timeout=500):
                    self._type_time(inp, time_value)
                    return
            except:
                pass

        # 4. Attribute-based selectors
        attr_selectors = {
            "start": [
                "kk-time-picker[formcontrolname*='startTime' i] input",
                "input[formcontrolname*='startTime' i]",
                "input[placeholder*='Start' i]",
                "input[name*='start' i]",
            ],
            "end": [
                "kk-time-picker[formcontrolname*='endTime' i] input",
                "input[formcontrolname*='endTime' i]",
                "input[placeholder*='End' i]",
                "input[name*='end' i]",
            ],
        }
        for sel in attr_selectors.get(field_type, []):
            try:
                inp = scope.locator(sel).first
                if inp.is_visible(timeout=1000):
                    self._type_time(inp, time_value)
                    return
            except:
                continue

        # 5. Positional fallback within the scope
        try:
            visible_inputs = scope.locator("input[type='time'], input[placeholder*=':']").all()
            visible = [i for i in visible_inputs if i.is_visible(timeout=300)]
            idx = 0 if field_type == "start" else 1
            if len(visible) > idx:
                self._type_time(visible[idx], time_value)
                return
        except:
            pass

        raise RuntimeError(f"Could not set {field_type} time ({time_value})")

    def _type_time(self, inp: Locator, time_value: str):
        """Clear and fill a time input with extra robustness."""
        inp.scroll_into_view_if_needed()
        inp.click()
        time.sleep(0.3)
        
        # Select all and delete to clear any existing masked value
        inp.press("Control+a")
        inp.press("Backspace")
        time.sleep(0.1)
        
        # Fill the value
        inp.fill(time_value)
        
        # Some Keka inputs need an Enter or Blur to register
        inp.press("Enter")
        time.sleep(0.2)
        
        # Final check: if value is still empty, try typing character by character
        try:
            val = inp.input_value()
            if not val or val == "00:00":
                inp.click()
                inp.type(time_value, delay=100)
        except: pass

    def _enter_comment(self, comment: str, container: Optional[Locator] = None):
        """Enter comment in any visible textarea or comment input."""
        scope = container if container is not None else self.page
        
        selectors = [
            "textarea",
            "textarea[formcontrolname*='comment' i]",
            "textarea[formcontrolname*='note' i]",
            "input[placeholder*='comment' i]",
            "input[placeholder*='note' i]",
            "input[placeholder*='description' i]",
            "textarea[placeholder*='Add' i]",
            "textarea[placeholder*='Write' i]",
            "textarea[placeholder*='description' i]",
        ]
        for sel in selectors:
            try:
                el = scope.locator(sel).first
                if el.is_visible(timeout=1_000):
                    el.click()
                    el.press("Control+a")
                    el.press("Backspace")
                    el.fill(comment)
                    logger.debug(f"Comment entered via: {sel}")
                    return
            except Exception:
                continue
        logger.debug("Could not find comment field (non-fatal)")

    def _save_time_popup(self, container: Optional[Locator] = None):
        """Click the Save button inside the time entry popup / slider."""
        scope = container if container is not None else self._get_active_time_container()
        if scope is None:
            logger.debug("Cannot save time: no active container found")
            return False
        
        # Prioritize buttons inside the scope
        panel_selectors = [
            "button:has-text('Save')",
            "button:has-text('Update')",
            "kk-footer button:has-text('Save')",
            "footer button:has-text('Save')",
            "button:has-text('Save'):visible",
        ]
        
        for sel in panel_selectors:
            try:
                btn = scope.locator(sel).first
                if btn.is_visible(timeout=2000):
                    btn.click()
                    logger.debug(f"Save clicked via scoped selector: {sel}")
                    self._wait_for_toast()
                    return
            except Exception:
                continue

        # General selectors but restricted to smaller buttons
        selectors = [
            "button:has-text('Save')",
            "button:has-text('Submit')",
            "button:has-text('Update')",
            "button[type='submit']",
        ]
        for sel in selectors:
            try:
                btns = self.page.locator(sel).all()
                for btn in btns:
                    if btn.is_visible(timeout=500):
                        # Avoid the huge global save button if it's visible
                        box = btn.bounding_box()
                        if box and box['width'] < 250: 
                            btn.click()
                            logger.debug(f"Save clicked via general selector: {sel}")
                            self._wait_for_toast()
                            return
            except Exception:
                continue

        logger.warning("Could not find entries-specific Save button — trying global Save button")
        try:
            global_save = self.page.locator(".timesheet-footer button:has-text('Save'), .page-footer button:has-text('Save'), button#save-timesheet").first
            if global_save.is_visible(timeout=1000):
                global_save.click()
                logger.debug("Clicked global Save button")
                self._wait_for_toast()
                return True
        except: pass

        self.page.keyboard.press("Enter")
        time.sleep(1.5)
        return True # Assume success if we reached here

    def _wait_for_toast(self):
        """Wait for a success toast/indicator."""
        try:
            self.page.wait_for_selector(
                "[class*='toast'], [class*='success'], [class*='snack'], text=successfully",
                state="visible",
                timeout=4000,
            )
            time.sleep(0.5)
        except:
            pass

    # ── Legacy ────────────────────────────────
    def fill_hours_in_column(self, date_str: str, hours: float) -> bool:
        """Legacy stub — not used in current flow."""
        return False
