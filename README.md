# 🤖 Keka Timesheet Automation

A **production-ready** CLI tool that automates filling Keka timesheets using:

- **Playwright** → attaches to your running Chrome (CDP), no login needed
- **Gemini AI** → converts natural language → structured CSV
- **Rich CLI** → colored logs, progress, confirmation prompts

---

## 📁 Project Structure

```
keka_automation/
├── cli.py            # CLI entry point (typer)
├── browser.py        # Chrome CDP connection
├── keka_bot.py       # Keka UI automation (Playwright)
├── gemini_parser.py  # Gemini API → CSV parsing
├── utils.py          # Logging, cache, screenshots
├── requirements.txt  # Python dependencies
└── README.md
```

---

## ⚙️ Setup

### Option A — uv (recommended, fastest)

```bash
# Install uv if you don't have it
pip install uv

# Create virtual env + install deps
uv venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # macOS/Linux

uv pip install -r requirements.txt

# Install Playwright browsers (Chromium)
playwright install chromium
```

### Option B — pip

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium
```

---

## 🔐 Environment Variables

Create a `.env` file in the project root:

```env
GEMINI_API_KEY=your_gemini_api_key_here
```

Or export directly:

```bash
# Windows PowerShell
$env:GEMINI_API_KEY = "your_key_here"

# Windows CMD
set GEMINI_API_KEY=your_key_here

# macOS/Linux
export GEMINI_API_KEY=your_key_here
```

Get a free Gemini API key: https://aistudio.google.com/app/apikey

---

## 🌐 Step 1 — Launch Chrome with Remote Debugging

**Windows (PowerShell):**

```powershell
& "C:\Program Files\Google\Chrome\Application\chrome.exe" `
  --remote-debugging-port=9222 `
  --user-data-dir="C:\ChromeDebugProfile"
```

**Windows (CMD):**

```cmd
"C:\Program Files\Google\Chrome\Application\chrome.exe" ^
  --remote-debugging-port=9222 ^
  --user-data-dir=C:\ChromeDebugProfile
```

**macOS:**

```bash
/Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome \
  --remote-debugging-port=9222 \
  --user-data-dir=/tmp/ChromeDebug
```

> ⚠️ Use `--user-data-dir` to keep your login session separate from your main Chrome profile.

**Step 1b — Log in to Keka in that Chrome window:**

Navigate to: https://cloudsufi.keka.com  
Log in normally. Your session will be reused by the tool.

---

## 🚀 CLI Usage

### 1. Parse only (no browser, just test Gemini)

```bash
python cli.py parse-only --input "Worked on AI Agents Mon-Wed 8h, Thu 6h on testing"
```

### 2. Dry run (connect to Keka, show what would be filled)

```bash
python cli.py dry-run --input "Worked on GCP + AI Agents Mon-Wed 8 hours, Thu 6 hours on testing"
```

### 3. Fill timesheet ✅

```bash
python cli.py fill --input "Worked on AI Agents Mon-Wed 8h, Thu 6h on testing"
```

### 4. Fill without confirmation prompt

```bash
python cli.py fill --input "Worked on AI project all week 8h" --yes
```

### 5. Fill last week's timesheet

```bash
python cli.py fill --input "Worked on DataPipeline Mon-Thu 8h, Fri 4h on code review" --week-offset -1
```

### 6. Fill only detected missing days

```bash
python cli.py fill --input "Worked on ProjectX Mon-Fri 8h" --missing-only
```

### 7. Debug / inspect Keka page

```bash
python cli.py inspect
```

### 8. Custom Chrome port

```bash
python cli.py fill --input "..." --port 9223
```

---

## 💬 Natural Language Examples

| Input | Result |
|-------|--------|
| `"Worked on AI Agents Mon-Wed 8h, Thu 6h on testing"` | 4 entries, Mon–Thu |
| `"GCP + Cloud infra all week 8 hours daily"` | 5 entries Mon–Fri, 8h each |
| `"ProjectX Mon 7.5h, Tue 8h, Wed 6h code review"` | 3 entries with varied hours |
| `"Testing and QA Thu-Fri 5 hours"` | 2 entries Thu, Fri |

---

## 📊 Sample Gemini Output (CSV)

Input:
```
Worked on GCP + AI Agents Mon-Wed 8 hours, Thu 6 hours on testing
```

Generated CSV:
```csv
date,project,task,hours
2026-04-07,GCP + AI Agents,Development,8
2026-04-08,GCP + AI Agents,Development,8
2026-04-09,GCP + AI Agents,Development,8
2026-04-10,GCP + AI Agents,Testing,6
```

---

## ❌ Error Handling

| Error | Cause | Fix |
|-------|-------|-----|
| `Chrome not found on port 9222` | Chrome not running with `--remote-debugging-port` | Launch Chrome as shown above |
| `GEMINI_API_KEY not set` | Missing env variable | Set `GEMINI_API_KEY` in `.env` |
| `Could not find '+ Add Time Entry' button` | Keka UI changed / not loaded | Run `inspect` command, check screenshot |
| `Gemini returned no parseable entries` | Vague input | Use more specific input with day names |
| Screenshot in `screenshots/` folder | Any fill step failed | Check screenshot for UI state |

---

## 🧠 Smart Features

- **Project/Task caching** — last used project + task cached in `.keka_cache.json`, used as AI hint
- **Retry on failure** — each UI action retried up to 3 times
- **Screenshot on failure** — every failed fill saves a PNG to `screenshots/`
- **Colored CLI output** — Rich-powered with icons, tables, panels
- **Week navigation** — `--week-offset` for past/future weeks
- **Dry-run mode** — preview without touching Keka

---

## 🔒 Security Notes

- Your Chrome session is **never touched for login** — you log in manually
- The `.env` file is git-ignored by default (add it yourself)
- No credentials are stored by this tool
- Gemini API key is only sent to Google's API endpoint

---

## 🐛 Troubleshooting

**Q: Chrome attached but Keka not loading?**  
A: Run `python cli.py inspect` and check the screenshot. Make sure you're logged in.

**Q: Dropdown selection fails?**  
A: Keka's React dropdowns can be sensitive. The tool tries multiple selector strategies. If it fails, check the screenshot and open an issue.

**Q: Hours not saving?**  
A: Some Keka instances require clicking a specific day column cell before the entry form opens. The tool handles both modal-based and column-based UIs.

**Q: Wrong week detected?**  
A: Use `--week-offset` to manually navigate, e.g. `--week-offset -1` for last week.
