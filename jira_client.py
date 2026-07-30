"""Jira REST API v2 client — SSO cookie auth with PAT/basic fallback."""
from __future__ import annotations
import time
import httpx
try:
    import browser_cookie3 as _browser_cookie3
    _HAS_BROWSER_COOKIE3 = True
except ImportError:
    _browser_cookie3 = None  # type: ignore
    _HAS_BROWSER_COOKIE3 = False
from urllib.parse import urlparse
from typing import Any, Optional, Tuple

FIELDS = "summary,status,assignee,priority,customfield_10002,customfield_10007,created,issuelinks"
PAGE_SIZE = 100
CACHE_TTL = 300  # 5 minutes


def _chrome_profiles(preferred_email: str = "") -> list[str]:  # noqa: ARG001
    """Return Chrome cookie file paths, preferred_email profile first."""
    import os, glob, json
    base = os.path.expanduser("~/Library/Application Support/Google/Chrome")
    preferred, others = [], []
    for cookie_file in glob.glob(f"{base}/*/Cookies"):
        profile_dir = os.path.dirname(cookie_file)
        prefs_file = os.path.join(profile_dir, "Preferences")
        email = ""
        try:
            data = json.load(open(prefs_file))
            email = (data.get("account_info") or [{}])[0].get("email", "")
        except Exception:
            pass
        if preferred_email and email.lower() == preferred_email.lower():
            preferred.append(cookie_file)
        else:
            others.append(cookie_file)
    return preferred + others


def _extract_cookies(jira_url: str, preferred_email: str = "lavanya.p@walmart.com") -> dict[str, str]:
    """Pull Jira session cookies from Chrome (preferred profile first) or other browsers.
    Returns empty dict when running in a container (browser_cookie3 not available).
    """
    if not _HAS_BROWSER_COOKIE3:
        return {}

    domain = urlparse(jira_url).hostname or ""
    cookies: dict[str, str] = {}

    # Try every Chrome profile, preferred email first
    for cookie_file in _chrome_profiles(preferred_email):
        try:
            jar = _browser_cookie3.chrome(domain_name=domain, cookie_file=cookie_file)
            found = {c.name: c.value for c in jar if domain in (c.domain or "")}
            if found:
                return found
        except Exception:
            continue

    # Fallback to other browsers
    for loader in (_browser_cookie3.safari, _browser_cookie3.firefox, _browser_cookie3.edge):
        try:
            jar = loader(domain_name=domain)
            found = {c.name: c.value for c in jar if domain in (c.domain or "")}
            if found:
                cookies = found
                break
        except Exception:
            continue

    return cookies


class JiraClient:
    def __init__(self, base_url: str, username: str = "", password: str = ""):
        self.base_url = base_url.rstrip("/")
        self._username = username
        self._password = password
        self._cache: dict[str, tuple[float, Any]] = {}
        self._auth_mode: str = "unknown"

    # ── auth resolution (SSO cookies win, then Bearer, then Basic) ────────────
    def _resolve_auth(self) -> Tuple[dict, dict, Optional[tuple]]:
        """Returns (headers, cookies, basic_auth) — only one active at a time."""
        # 1. SSO browser cookies (highest priority)
        cookies = _extract_cookies(self.base_url)
        if cookies:
            self._auth_mode = "sso-cookie"
            return {}, cookies, None

        # 2. Long token → Bearer
        if self._password and len(self._password) > 20:
            self._auth_mode = "bearer"
            return {"Authorization": f"Bearer {self._password}"}, {}, None

        # 3. Short password → Basic
        if self._username and self._password:
            self._auth_mode = "basic"
            return {}, {}, (self._username, self._password)

        self._auth_mode = "none"
        return {}, {}, None

    # ── HTTP ──────────────────────────────────────────────────────────────────
    def _get(self, path: str, **params) -> dict:
        url = f"{self.base_url}/rest/api/2/{path}"
        headers, cookies, auth = self._resolve_auth()
        r = httpx.get(
            url,
            headers=headers,
            cookies=cookies,
            auth=auth,
            params=params,
            verify=False,
            timeout=20,
        )
        r.raise_for_status()
        return r.json()

    def _paginate(self, jql: str) -> list[dict]:
        issues, start = [], 0
        while True:
            data = self._get(
                "search",
                jql=jql,
                fields=FIELDS,
                expand="changelog",
                maxResults=PAGE_SIZE,
                startAt=start,
            )
            batch = data.get("issues", [])
            issues.extend(batch)
            start += len(batch)
            if start >= data.get("total", 0) or not batch:
                break
        return issues

    @staticmethod
    def _parse(raw: dict) -> dict:
        f = raw.get("fields", {})
        sp = f.get("customfield_10002")
        epic = f.get("customfield_10007") or ""

        # Date of assignment: latest "assignee" or "Tester" change in changelog,
        # falling back to the issue creation date.
        created_raw = f.get("created", "")
        assigned_date = created_raw[:10] if created_raw else ""
        changelog = raw.get("changelog", {})
        for history in sorted(
            changelog.get("histories", []),
            key=lambda h: h.get("created", ""),
            reverse=True,
        ):
            for item in history.get("items", []):
                if item.get("field") in ("assignee", "Tester"):
                    assigned_date = history.get("created", "")[:10]
                    break
            else:
                continue
            break

        # Count only actual Bug issuetype links (skip Stories, Epics, etc.)
        bug_count = 0
        for link in (f.get("issuelinks") or []):
            if (link.get("type") or {}).get("name") != "Bug":
                continue
            linked = link.get("outwardIssue") or link.get("inwardIssue") or {}
            lf = (linked.get("fields") or {})
            if (lf.get("issuetype") or {}).get("name", "").lower() in ("bug", "defect"):
                bug_count += 1

        return {
            "key": raw["key"],
            "summary": f.get("summary", ""),
            "status": f.get("status", {}).get("name", "Unknown"),
            "assignee": (f.get("assignee") or {}).get("displayName", "Unassigned"),
            "priority": (f.get("priority") or {}).get("name", ""),
            "points": float(sp) if sp else 0.0,
            "epic": epic,
            "assigned_date": assigned_date,
            "bug_count": bug_count,
        }

    # ── public ────────────────────────────────────────────────────────────────
    def count(self, jql: str) -> int:
        """Return issue count for a JQL query. Uses cache if available; otherwise
        fires a fast maxResults=0 search (no fields, no changelog)."""
        cached = self._cache.get(jql)
        if cached and (time.time() - cached[0]) < CACHE_TTL:
            return len(cached[1])
        url = f"{self.base_url}/rest/api/2/search"
        headers, cookies, auth = self._resolve_auth()
        r = httpx.get(
            url,
            headers=headers,
            cookies=cookies,
            auth=auth,
            params={"jql": jql, "maxResults": 0},
            verify=False,
            timeout=10,
        )
        r.raise_for_status()
        return r.json().get("total", 0)

    def get_user(self, username: str) -> dict:
        """Fetch Jira user info (displayName, emailAddress, etc.)."""
        return self._get("user", username=username)

    def whoami(self) -> dict:
        """Test auth — returns Jira user info."""
        return self._get("myself")

    def search(self, jql: str, force: bool = False) -> list[dict]:
        now = time.time()
        cached = self._cache.get(jql)
        if cached and not force and (now - cached[0]) < CACHE_TTL:
            return cached[1]

        raw = self._paginate(jql)
        parsed = [self._parse(i) for i in raw]
        self._cache[jql] = (now, parsed)
        return parsed

    def cache_age(self, jql: str) -> Optional[int]:
        cached = self._cache.get(jql)
        return int(time.time() - cached[0]) if cached else None

    @property
    def auth_mode(self) -> str:
        return self._auth_mode
