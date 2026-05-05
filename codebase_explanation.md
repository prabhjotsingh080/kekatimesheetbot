# Keka Timesheet Automation - Codebase Explanation

This document provides a comprehensive explanation of the purpose and working of all files within the `keka_automation` directory. Together, these files form an automation tool that translates natural language input into timesheet entries, fetches task definitions from Keka, and automatically fills timesheets via Playwright.

---

## 🚀 The Pipeline Orchestrator

### 1. `main.py`
**Purpose:** The central entry point that orchestrates the entire automation pipeline.
**Working:**
- Executes four sequential steps:
  1. **Launch Browser:** Runs `launch_chrome.bat` to spawn a Chrome instance with remote debugging enabled.
  2. **User Login:** Pauses execution and waits for the user to confirm they have logged into Keka and navigated to the Timesheet page.
  3. **Input Generation:** Triggers `llmreader.py` to convert natural language time logs into `input.json`.
  4. **Data Fetching:** Triggers `fetch.py` to scrape Keka for available projects, phases, and tasks, saving them to `fetched.json`.
  5. **Timesheet Filling:** Triggers `fill.py` to orchestrate Playwright for entering the mapped tasks into Keka.

---

## 🧠 AI Integration

### 2. `llmreader.py`
**Purpose:** Uses Google's Gemini LLM to parse natural language descriptions of time spent into structured JSON data.
**Working:**
- Accepts time-tracking logs from standard input or arguments.
- Connects to the Gemini API (`gemini-2.5-flash`) using the `GEMINI_API_KEY` defined in `.env`.
- Intelligently resolves relative/absolute dates (e.g., "9th march" to "2026-03-09") and extracts task names and durations.
- Generates `input.json` containing a list of `tasks` with exact dates and hours. If no input is given, it falls back to the existing `input.json` to allow rerunning previous executions.

---

## 🐍 Core Automation Scripts

### 3. `fetch.py`
**Purpose:** Scrapes all available projects, phases, and tasks from Keka to create a reference mapping.
**Working:**
- Connects to the running Chrome browser session over the remote debugging port.
- Opens the "Add Time Entry" panel in Keka.
- Uses complex injected JavaScript to iterate through every project, expanding every phase to discover all nested tasks and standalone tasks.
- Saves the extracted hierarchy into `fetched.json`.

### 4. `fill.py`
**Purpose:** The core script that maps input tasks and drives the browser to log the timesheet entries.
**Working:**
- Reads your worked tasks from `input.json`.
- Uses fuzzy matching (via `difflib`) to map your informal task names to the official Keka project, phase, and task hierarchies found in `fetched.json`.
- Calculates generic start (09:00) and end times based on the task duration.
- Orchestrates `KekaBot` to navigate the Timesheet interface, create rows for each task, and fill the exact hours for the correct dates.

### 5. `keka_bot.py`
**Purpose:** Encapsulates the Playwright UI navigation into logical actions customized for Keka's specific DOM structure.
**Working:**
- Contains the `KekaBot` class, abstracting low-level Playwright calls.
- `navigate_week()` and `go_to_week_containing()`: Manages calendar navigation to locate the target week.
- `fill_entry()`: The primary method that selects a project/phase/task from the sidebar using JS execution or fallback locators, locates the specific day-cell intersection in the Keka grid, clicks it, and fills the time popup.
- Uses robust DOM traversal in JavaScript instead of fragile coordinates.

### 6. `browser.py`
**Purpose:** Manages the Playwright connection to a local browser.
**Working:**
- Links Playwright to a **pre-existing, logged-in Chrome browser** via remote debugging (`localhost:9222`).
- This bypasses SSO (Single Sign-On) and Multi-Factor Authentication issues entirely by piggybacking on an active session.

### 7. `utils.py`
**Purpose:** Shared utility functions utilized across the automation scripts.
**Working:**
- `logger` and `console`: Handles formatted console output using the `rich` library.
- `load_env`: Loads environment variables securely.
- `@retry`: A decorator to retry flaky UI operations automatically.
- Contains helper functions like `take_screenshot` for debugging failures, and simple caching wrappers.

---

## 🛠️ Batch Scripts (Windows Ecosystem)

### 8. `launch_chrome.bat`
**Purpose:** Opens a dedicated Chrome window with remote debugging enabled (`--remote-debugging-port=9222`) pointing to a custom user data directory (`C:\ChromeDebug`).

### 9. `setup.bat` (if applicable)
**Purpose:** Initializes the virtual environment, installs Python dependencies from `requirements.txt`, and prepares Playwright binaries.

---

## 📄 Key Data Files

- **`input.json`**: The structured output from `llmreader.py`. Contains exactly what tasks you worked on, the date, and the duration. Read by `fill.py`.
- **`fetched.json`**: A complete dictionary mapping of all Keka projects, phases, and tasks scraped by `fetch.py`. Read by `fill.py` for name matching.
- **`.env`**: Stores secret configurations such as `GEMINI_API_KEY`.
- **`README.md`**: Provides high-level usage instructions and setup guide for the tool.
