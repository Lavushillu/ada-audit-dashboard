"""FastAPI dashboard for ADA Audit initiative tracking."""
from __future__ import annotations
import os
import json
import time
from pathlib import Path
from typing import Annotated, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
import httpx

from jira_client import JiraClient

load_dotenv()

# ── config ────────────────────────────────────────────────────────────────────
SETTINGS_FILE = Path("settings.json")
KNOWN_TESTERS = ["l0p0c77", "s0s116y", "h0n01o6", "k0n042f"]

DEFAULT_SETTINGS = {
    "testers": KNOWN_TESTERS,
    "keyword": "ADA Audit",
    "jira_url": os.getenv("JIRA_URL", "https://jira.walmart.com"),
}

_settings_cache: Optional[dict] = None
_settings_mtime: float = 0.0

# Per-tester issue count cache (module-level, TTL-controlled)
_tester_counts: dict = {}   # {tester: (timestamp, count)}
_TESTER_COUNT_TTL = 300.0   # 5 minutes

# Tester display name cache (long TTL — names rarely change)
_tester_names: dict = {}    # {username: display_name}
_TESTER_NAME_TTL = 3600.0   # 1 hour

# Per-tester story points cache
_tester_pts: dict = {}      # {tester: (timestamp, total_points)}
_TESTER_PTS_TTL = 300.0     # 5 minutes


def load_settings() -> dict:
    global _settings_cache, _settings_mtime
    if not SETTINGS_FILE.exists():
        return DEFAULT_SETTINGS.copy()
    try:
        mtime = SETTINGS_FILE.stat().st_mtime
    except OSError:
        return DEFAULT_SETTINGS.copy()
    if _settings_cache is None or mtime != _settings_mtime:
        merged = {**DEFAULT_SETTINGS, **json.loads(SETTINGS_FILE.read_text())}
        # backward compat: old settings stored a single "tester" string
        if "tester" in merged and "testers" not in merged:
            merged["testers"] = [merged["tester"]]
        merged.pop("tester", None)
        _settings_cache = merged
        _settings_mtime = mtime
    return _settings_cache.copy()


def save_settings(data: dict):
    global _settings_cache
    tmp = SETTINGS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(SETTINGS_FILE)
    _settings_cache = None  # invalidate cache


def build_client(settings: dict) -> JiraClient:
    return JiraClient(
        base_url=settings["jira_url"],
        username=os.getenv("JIRA_USERNAME", ""),
        password=os.getenv("JIRA_PASSWORD", ""),
    )


def get_tester_count(client: JiraClient, settings: dict, tester: str) -> Optional[int]:
    """Return cached issue count for a single tester (fast maxResults=0 query)."""
    global _tester_counts
    now = time.time()
    cached = _tester_counts.get(tester)
    if cached and (now - cached[0]) < _TESTER_COUNT_TTL:
        return cached[1]
    try:
        cnt = client.count(make_jql(settings, tester=tester))
    except Exception:
        cnt = None
    _tester_counts[tester] = (now, cnt)
    return cnt


def get_tester_display_name(client: JiraClient, username: str) -> str:
    """Return cached display name for a Jira username (e.g. 'Jane Smith')."""
    global _tester_names
    now = time.time()
    cached = _tester_names.get(username)
    if cached and (now - cached[0]) < _TESTER_NAME_TTL:
        return cached[1]
    try:
        user = client.get_user(username)
        name = user.get("displayName") or username
    except Exception:
        name = username
    _tester_names[username] = (now, name)
    return name


def get_tester_points(client: JiraClient, settings: dict, tester: str) -> float:
    """Return cached total story points for a tester (uses client.search() cache)."""
    global _tester_pts
    now = time.time()
    cached = _tester_pts.get(tester)
    if cached and (now - cached[0]) < _TESTER_PTS_TTL:
        return cached[1]
    try:
        issues = client.search(make_jql(settings, tester=tester))
        pts = sum(i.get("points", 0) for i in issues)
    except Exception:
        pts = 0.0
    _tester_pts[tester] = (now, pts)
    return pts


_tester_platforms: dict = {}   # {tester: (timestamp, list[str])}
_TESTER_PLAT_TTL = 300.0

def get_tester_platforms(client: JiraClient, settings: dict, tester: str) -> list:
    """Return sorted list of platforms a tester has worked on (cached)."""
    global _tester_platforms
    now = time.time()
    cached = _tester_platforms.get(tester)
    if cached and (now - cached[0]) < _TESTER_PLAT_TTL:
        return cached[1]
    try:
        issues = client.search(make_jql(settings, tester=tester))
        platforms = sorted(set(get_platform(i["summary"]) for i in issues) - {"Other"})
    except Exception:
        platforms = []
    _tester_platforms[tester] = (now, platforms)
    return platforms


# Per-tester unique initiative count cache
_tester_unique_counts: dict = {}   # {tester: (timestamp, count)}
_TESTER_UNIQUE_TTL = 300.0

def get_tester_unique_count(client: JiraClient, settings: dict, tester: str) -> Optional[int]:
    """Return cached count of unique initiative names for a tester."""
    global _tester_unique_counts
    now = time.time()
    cached = _tester_unique_counts.get(tester)
    if cached and (now - cached[0]) < _TESTER_UNIQUE_TTL:
        return cached[1]
    try:
        issues = client.search(make_jql(settings, tester=tester))
        count = len(set(get_initiative_name(i["summary"]) for i in issues))
    except Exception:
        count = None
    _tester_unique_counts[tester] = (now, count)
    return count


_PLATFORM_ORDER = {"iOS": 0, "Android": 1, "Web": 2, "Mobile Web": 3, "Other": 4}

# ── date helpers (Walmart fiscal year: starts Feb 1) ─────────────────────────
def get_fy_label(date_str: str) -> str:
    """Return 'YYYY-YYYY+1' fiscal year label for a YYYY-MM-DD date string."""
    if not date_str or len(date_str) < 7:
        return ""
    try:
        year, month = int(date_str[:4]), int(date_str[5:7])
    except ValueError:
        return ""
    # FY starts Feb: Jan dates belong to the previous FY
    fy_start = year if month >= 2 else year - 1
    return f"{fy_start}-{fy_start + 1}"


def get_fq_label(date_str: str) -> str:
    """Return Walmart fiscal quarter ('Q1'–'Q4') for a YYYY-MM-DD date string.
    Walmart FY starts Feb 1: Q1=Feb-Apr, Q2=May-Jul, Q3=Aug-Oct, Q4=Nov-Jan.
    """
    if not date_str or len(date_str) < 7:
        return ""
    try:
        month = int(date_str[5:7])
    except ValueError:
        return ""
    if 2 <= month <= 4:
        return "Q1"
    elif 5 <= month <= 7:
        return "Q2"
    elif 8 <= month <= 10:
        return "Q3"
    else:  # Nov, Dec, Jan
        return "Q4"



def group_issues(issues: list[dict]) -> list[dict]:
    """Group flat issues by initiative name; each group entry holds aggregated data."""
    from collections import defaultdict, Counter as Ctr
    groups: dict[str, list] = defaultdict(list)
    for i in issues:
        groups[get_initiative_name(i["summary"])].append(i)

    result = []
    for name, grp in groups.items():
        platforms = sorted(
            set(get_platform(i["summary"]) for i in grp),
            key=lambda p: _PLATFORM_ORDER.get(p, 5),
        )
        statuses = dict(Ctr(i["status"] for i in grp))
        dominant = max(statuses, key=statuses.get)
        points = int(sum(i["points"] for i in grp))
        assignees = list(dict.fromkeys(
            i["assignee"] for i in grp
            if i["assignee"] and i["assignee"] != "Unassigned"
        ))
        epic = next((i["epic"] for i in grp if i["epic"]), "")
        bug_count = sum(i.get("bug_count", 0) for i in grp)
        sorted_grp = sorted(grp, key=lambda i: _PLATFORM_ORDER.get(get_platform(i["summary"]), 5))
        plat_str = " | ".join(platforms) if platforms else "Other"
        label = f"ADA Audit | {plat_str} | {name}"
        # Earliest non-empty assigned date across the group
        dates = sorted(d for d in (i.get("assigned_date", "") for i in grp) if d)
        assigned_date = dates[0] if dates else ""
        result.append({
            "name": name,
            "label": label,
            "platforms": platforms,
            "issues": sorted_grp,
            "statuses": statuses,
            "dominant_status": dominant,
            "points": points,
            "assignees": assignees,
            "epic": epic,
            "assigned_date": assigned_date,
            "bug_count": bug_count,
        })
    return result


def make_jql(settings: dict, tester: str = "") -> str:
    """Build JQL. If tester is given, query only that tester; otherwise all testers."""
    keyword = settings["keyword"].replace('"', "").strip()
    if tester:
        t = tester.replace('"', "").replace("'", "").strip()
        return f'Tester = "{t}" AND summary ~ "{keyword}"'
    testers = settings.get("testers", KNOWN_TESTERS)
    tester_list = ", ".join(
        f'"{t.replace(chr(34),"").replace(chr(39),"").strip()}"' for t in testers
    )
    return f"Tester in ({tester_list}) AND summary ~ \"{keyword}\""


# ── helpers ───────────────────────────────────────────────────────────────────
_PLATFORM_SEGMENTS = ("IOS", "ANDROID", "MWEB", "DWEB", "RWEB", "WEB")

def get_initiative_name(summary: str) -> str:
    """Strip 'ADA Audit | ' prefix and platform segment → core initiative name.
    e.g. 'ADA Audit | iOS | Savings on Hub' → 'Savings on Hub'
    """
    s = summary.strip()
    # Strip ADA prefix variants
    for prefix in ("ADA Audit | ", "ADA | ", "ADA| "):
        if s.upper().startswith(prefix.upper()):
            s = s[len(prefix):]
            break
    # Strip the first pipe-delimited segment if it's a platform keyword
    if " | " in s:
        first, rest = s.split(" | ", 1)
        if any(pat in first.upper() for pat in _PLATFORM_SEGMENTS):
            s = rest
    return s.strip()


def get_platform(summary: str) -> str:
    u = summary.upper()
    if "MWEB" in u:
        return "Mobile Web"
    if any(x in u for x in ("DWEB", "RWEB", "WEB")):
        return "Web"
    if "IOS" in u:
        return "iOS"
    if "ANDROID" in u:
        return "Android"
    return "Other"


def aggregate(issues: list[dict]) -> dict:
    statuses = {}
    platforms = {}
    assignees = {}
    total_pts = 0.0

    for i in issues:
        statuses[i["status"]] = statuses.get(i["status"], 0) + 1
        p = get_platform(i["summary"])
        platforms[p] = platforms.get(p, 0) + 1
        a = i["assignee"]
        assignees[a] = assignees.get(a, 0) + 1
        total_pts += i["points"]

    done = statuses.get("Done", 0)
    pct_done = round(done / len(issues) * 100) if issues else 0

    return {
        "total": len(issues),
        "statuses": statuses,
        "platforms": platforms,
        "assignees": dict(sorted(assignees.items(), key=lambda x: -x[1])),
        "total_pts": int(total_pts),
        "done": done,
        "pct_done": pct_done,
    }


# ── app ───────────────────────────────────────────────────────────────────────
app = FastAPI(title="ADA Initiatives")
templates = Jinja2Templates(directory="templates")

# Single shared client (recreated on settings change)
_client: Optional[JiraClient] = None
_settings: dict = {}


def get_client() -> JiraClient:
    global _client, _settings
    if _client is None:
        _settings = load_settings()
        _client = build_client(_settings)
    return _client


_current_user_cache: Optional[dict] = None
_current_user_ts: float = 0.0

def get_current_user() -> dict:
    """Return logged-in Jira user info, cached for 1 hour."""
    global _current_user_cache, _current_user_ts
    now = time.time()
    if _current_user_cache and (now - _current_user_ts) < 3600:
        return _current_user_cache
    try:
        info = get_client().whoami()
        _current_user_cache = {
            "displayName": info.get("displayName", ""),
            "name": info.get("name", ""),
        }
    except Exception:
        _current_user_cache = {"displayName": "", "name": ""}
    _current_user_ts = now
    return _current_user_cache


# ── routes ────────────────────────────────────────────────────────────────────
@app.get("/health")
async def health():
    """Liveness probe — process is alive."""
    return {"status": "UP"}


@app.get("/ready")
async def ready():
    """Readiness probe — app can serve traffic (Jira reachable)."""
    try:
        get_client().whoami()
        return {"status": "READY"}
    except Exception as e:
        from fastapi.responses import JSONResponse
        return JSONResponse({"status": "NOT_READY", "error": str(e)}, status_code=503)


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    settings = load_settings()
    current_user = get_current_user()
    return templates.TemplateResponse(request, "index.html", {
        "settings": settings,
        "current_user": current_user,
    })


@app.get("/api/issues", response_class=HTMLResponse)
async def get_issues(
    request: Request,
    status: str = "",
    platform: str = "",
    assignee: str = "",
    tester: str = "",
    q: str = "",
    fy: str = "",       # fiscal year filter e.g. "2025-2026"
    fq: str = "",       # fiscal quarter filter e.g. "Q1"
    page: int = 1,
    page_size: int = 20,
    sort: str = "key",
    sort_dir: str = "asc",
    force: bool = False,
):
    client = get_client()
    settings = load_settings()
    # Each tester selection gets its own JQL (and thus its own cache entry)
    jql = make_jql(settings, tester=tester)

    try:
        all_issues = client.search(jql, force=force)
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=502, detail=f"Jira error: {e.response.status_code}")
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))

    issues = all_issues
    if status:
        issues = [i for i in issues if i["status"] == status]
    if platform:
        issues = [i for i in issues if get_platform(i["summary"]) == platform]
    if assignee:
        issues = [i for i in issues if i["assignee"] == assignee]
    if q:
        ql = q.lower()
        issues = [i for i in issues if ql in i["summary"].lower() or ql in i["key"].lower()]
    if fy:
        issues = [i for i in issues if get_fy_label(i.get("assigned_date", "")) == fy]
    if fq:
        issues = [i for i in issues if get_fq_label(i.get("assigned_date", "")) == fq]

    # Collect distinct filter options from ALL issues (unfiltered) for the dropdowns
    all_fy = sorted(
        {get_fy_label(i.get("assigned_date", "")) for i in all_issues if i.get("assigned_date")},
        reverse=True,
    )

    sort_dir = "desc" if sort_dir == "desc" else "asc"

    # Group filtered issues by initiative name
    all_grouped = group_issues(issues)

    # Sort groups
    _group_sort_keys = {
        "key":           lambda g: g["name"].lower(),
        "summary":       lambda g: g["name"].lower(),
        "status":        lambda g: g["dominant_status"],
        "platform":      lambda g: (g["platforms"][0] if g["platforms"] else ""),
        "assignee":      lambda g: (g["assignees"][0] if g["assignees"] else "").lower(),
        "points":        lambda g: g["points"],
        "epic":          lambda g: g["epic"],
        "bug_count":     lambda g: g["bug_count"],
        "assigned_date": lambda g: max(
            (i.get("assigned_date", "") for i in g["issues"]), default=""
        ),
    }
    sort = sort if sort in _group_sort_keys else "key"
    all_grouped = sorted(all_grouped, key=_group_sort_keys[sort], reverse=(sort_dir == "desc"))

    page = max(1, page)
    page_size = max(1, min(100, page_size))
    total_count = len(all_grouped)
    total_pages = max(1, (total_count + page_size - 1) // page_size)
    page = min(page, total_pages)
    start = (page - 1) * page_size
    page_groups = all_grouped[start:start + page_size]

    age = client.cache_age(jql)
    agg = aggregate(all_issues)  # stats always reflect the selected tester scope

    # Count unique initiatives (unfiltered — for stat card)
    from collections import Counter
    initiative_counts = dict(Counter(get_initiative_name(i["summary"]) for i in all_issues))

    # Per-tester breakdown (module-level cached)
    testers = settings.get("testers", [])
    tester_breakdown = {t: get_tester_count(client, settings, t) for t in testers}
    tester_names = {t: get_tester_display_name(client, t) for t in testers}
    tester_pts = {t: get_tester_points(client, settings, t) for t in testers}
    tester_platforms = {t: get_tester_platforms(client, settings, t) for t in testers}
    tester_unique_counts = {t: get_tester_unique_count(client, settings, t) for t in testers}
    # Sort testers alphabetically by display name
    sorted_testers = sorted(testers, key=lambda t: (tester_names.get(t) or t).lower())

    return templates.TemplateResponse(
        request, "_issues.html",
        {
            "grouped_initiatives": page_groups,
            "agg": agg,
            "settings": settings,
            "cache_age": age,
            "get_platform": get_platform,
            "page": page,
            "page_size": page_size,
            "total_pages": total_pages,
            "total_count": total_count,
            "showing_from": start + 1 if total_count else 0,
            "showing_to": min(start + page_size, total_count) if total_count else 0,
            "sort": sort,
            "sort_dir": sort_dir,
            "cur_status": status,
            "cur_platform": platform,
            "cur_assignee": assignee,
            "cur_tester": tester,
            "cur_fy": fy,
            "all_fy": all_fy,
            "cur_fq": fq,
            "cur_q": q,
            "tester_breakdown": tester_breakdown,
            "tester_names": tester_names,
            "tester_pts": tester_pts,
            "sorted_testers": sorted_testers,
            "get_initiative_name": get_initiative_name,
            "initiative_counts": initiative_counts,
            "tester_platforms": tester_platforms,
            "tester_unique_counts": tester_unique_counts,
        },
    )


@app.post("/api/settings")
async def update_settings(
    testers_raw: Annotated[str, Form()],
    keyword: Annotated[str, Form()],
    jira_url: Annotated[str, Form()],
):
    global _client
    testers = [t.strip() for t in testers_raw.split(",") if t.strip()]
    if not testers:
        testers = KNOWN_TESTERS
    new = {"testers": testers, "keyword": keyword.strip(), "jira_url": jira_url.strip()}
    save_settings(new)
    _client = None          # force re-init with new settings
    _tester_counts.clear()        # invalidate per-tester counts
    _tester_pts.clear()           # invalidate per-tester points
    _tester_platforms.clear()     # invalidate per-tester platforms
    _tester_unique_counts.clear() # invalidate per-tester unique counts
    return RedirectResponse("/", status_code=303)


@app.get("/api/bugs/{key}", response_class=HTMLResponse)
async def get_issue_bugs(request: Request, key: str):
    """Lazy-load bugs linked to a given issue key."""
    client = get_client()
    settings = load_settings()
    bugs = []
    try:
        raw = client._get(f"issue/{key}", fields="issuelinks")
        for link in raw.get("fields", {}).get("issuelinks") or []:
            if (link.get("type") or {}).get("name") != "Bug":
                continue
            linked = link.get("outwardIssue") or link.get("inwardIssue") or {}
            if not linked:
                continue
            lf = linked.get("fields") or {}
            # Only include actual Bug issuetype — skip linked Stories, Epics, etc.
            if (lf.get("issuetype") or {}).get("name", "").lower() not in ("bug", "defect"):
                continue
            bugs.append({
                "key": linked.get("key", ""),
                "summary": lf.get("summary", ""),
                "status": (lf.get("status") or {}).get("name", ""),
                "priority": (lf.get("priority") or {}).get("name", ""),
            })
    except Exception:
        pass
    return templates.TemplateResponse(request, "_bugs.html", {
        "bugs": bugs,
        "issue_key": key,
        "settings": settings,
    })


@app.get("/api/auth-test")
async def auth_test():
    """Test Jira connectivity and report which auth method is active."""
    client = get_client()
    try:
        me = client.whoami()
        return JSONResponse({
            "ok": True,
            "auth_mode": client.auth_mode,
            "display_name": me.get("displayName"),
            "email": me.get("emailAddress"),
        })
    except httpx.HTTPStatusError as e:
        return JSONResponse({"ok": False, "error": f"HTTP {e.response.status_code}"}, status_code=502)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=502)


@app.get("/api/refresh", response_class=HTMLResponse)
async def force_refresh(request: Request):
    """Force a cache-busting refetch then redirect to issues partial."""
    return RedirectResponse("/api/issues?force=true", status_code=303)


