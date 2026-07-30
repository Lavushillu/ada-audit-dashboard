# ADA Audit Initiative Dashboard

A live Jira dashboard for tracking ADA (Accessibility) Audit initiatives on the CEACCESS project. Built with FastAPI, HTMX, and Tailwind CSS. Authenticates automatically using your existing browser SSO session — no tokens or passwords needed.

---

## Features

- **Live Jira data** — fetches all issues matching your filter in real time, paginating through all results automatically
- **SSO authentication** — reads your active Jira session from Safari, Chrome, Firefox, or Edge; no manual credentials required
- **5-minute cache** with a one-click **Refresh** button to force a re-fetch
- **Status breakdown** — Done / Ready for Review / Work in Progress / Backlog
- **Platform breakdown** — iOS / Android / Web / Mobile Web
- **Story points total** — pulled from the real Jira field
- **Live HTMX filters** — filter by status, platform, or assignee; full-text search across issue keys and summaries; no page reload
- **Change User** — swap the Jira Tester username from the header button; the dashboard reloads with that user's issues instantly
- **Clickable Jira links** — every issue key and epic link opens directly in Jira

---

## Prerequisites

- Python 3.11+
- [`uv`](https://github.com/astral-sh/uv) (fast Python package manager)
- Access to Walmart VPN or Eagle WiFi
- Logged into **https://jira.walmart.com** in your browser (Safari, Chrome, or Firefox)

---

## Setup

### 1. Clone or extract the project

```bash
cd ada-audit-dashboard
```

### 2. Create the virtual environment and install dependencies

```bash
uv venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate

uv pip install -r requirements.txt \
  --index-url https://pypi.ci.artifacts.walmart.com/artifactory/api/pypi/external-pypi/simple \
  --allow-insecure-host pypi.ci.artifacts.walmart.com
```

### 3. Configure environment (optional)

Copy the example env file:

```bash
cp .env.example .env
```

Edit `.env` to set your Jira URL and default tester username:

```env
JIRA_URL=https://jira.walmart.com
JIRA_TESTER=l0p0c77
```

> **Note:** No password is needed. The app reads your active Jira session cookie from your browser automatically.

### 4. Make sure you are logged into Jira in your browser

Go to https://jira.walmart.com and sign in via SSO. That's it.

### 5. Run the server

```bash
source .venv/bin/activate
uvicorn main:app --reload --port 8765
```

Open **http://localhost:8765** in your browser.

---

## How Authentication Works

The app uses `browser-cookie3` to read the active Jira session cookie from your local browser profile. It tries browsers in this order:

1. Safari
2. Chrome
3. Firefox
4. Edge

No credentials are stored anywhere. If your session expires, just log back into Jira in your browser and click **Refresh** on the dashboard.

---

## Changing the Tracked User

Click the **Change User** button in the top-right header. A modal lets you update:

| Field | Description |
|---|---|
| Jira Tester Username | The associate ID to filter by (e.g. `l0p0c77`) |
| Keyword Filter | Summary keyword (default: `ADA Audit`) |
| Jira Base URL | Your Jira instance (default: `https://jira.walmart.com`) |

Settings are saved to `settings.json` locally. Click **Save & Reload** and the dashboard immediately fetches data for the new user.

---

## Project Structure

```
ada-audit-dashboard/
├── main.py             # FastAPI app — routes, aggregation, settings
├── jira_client.py      # Jira REST API v2 client with SSO cookie auth and pagination
├── templates/
│   ├── index.html      # Main page shell (header, settings modal, HTMX loader)
│   └── _issues.html    # HTMX partial — stat cards, charts, filters, issue table
├── .env.example        # Environment variable template
├── requirements.txt    # Python dependencies
└── README.md           # This file
```

---

## API Endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/` | Main dashboard page |
| `GET` | `/api/issues` | Fetch and render issues (supports `?status=`, `?platform=`, `?assignee=`, `?q=`, `?force=true`) |
| `GET` | `/api/auth-test` | Check SSO auth status — returns logged-in user info |
| `POST` | `/api/settings` | Save dashboard settings (tester, keyword, jira_url) |

---

## Jira Fields Used

| Field | Jira Custom Field ID |
|---|---|
| Story Points | `customfield_10002` |
| Epic Link | `customfield_10007` |

> These are specific to `jira.walmart.com`. If you point this at a different Jira instance, field IDs may differ.

---

## Tech Stack

| Layer | Technology |
|---|---|
| Backend | Python 3.11 + FastAPI |
| Templates | Jinja2 |
| Frontend | HTMX + Tailwind CSS (CDN) |
| Charts | Chart.js |
| HTTP client | httpx |
| Cookie extraction | browser-cookie3 |
| Jira | REST API v2 |

---

## Troubleshooting

**Dashboard shows "Jira error: 403"**
- Make sure you are logged into Jira in your browser
- Try opening https://jira.walmart.com, log in, then hit **Refresh** on the dashboard

**Story points show 0**
- This means the Jira instance uses a different custom field for story points
- Run `GET /api/auth-test` to confirm auth is working, then check your Jira field configuration

**No issues returned**
- Verify the Tester username is correct via **Change User**
- Confirm the JQL filter shown in the dashboard footer returns results in Jira directly

---

## License

Internal Walmart use only.
