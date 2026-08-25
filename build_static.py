"""Build a fully interactive static HTML dashboard with client-side filtering.

Can run standalone (fetches Jira directly) or against the local dev server.
"""
import os, json, sys
from datetime import datetime

# ── Fetch data ────────────────────────────────────────────────────────────────
USE_SERVER = os.getenv("USE_SERVER", "0") == "1"

if USE_SERVER:
    import httpx
    BASE = "http://127.0.0.1:8000"
    client = httpx.Client(proxies={}, timeout=120)
    print("Fetching Jira data from local server...")
    data = client.get(f"{BASE}/api/data.json").json()
    issues      = data["issues"]
    grouped     = data["grouped"]
    agg         = data["agg"]
    jira_url    = data["jira_url"]
    tester_names = data["tester_names"]
else:
    # Standalone: import app functions directly
    print("Fetching Jira data directly...")
    sys.path.insert(0, os.path.dirname(__file__))
    from dotenv import load_dotenv
    load_dotenv()
    from main import (
        load_settings, build_client, make_jql, group_issues,
        aggregate, get_tester_display_name, get_fq_label, get_fy_label,
    )
    settings     = load_settings()
    jira_url     = settings["jira_url"]
    client_jira  = build_client(settings)
    jql          = make_jql(settings)
    print(f"  JQL: {jql}")
    raw_issues   = client_jira.search(jql)
    issues       = raw_issues
    grouped      = group_issues(raw_issues)
    agg          = aggregate(raw_issues)
    testers      = settings.get("testers", [])
    tester_names = {t: get_tester_display_name(client_jira, t) for t in testers}
    print(f"  Fetched {len(issues)} issues, {len(grouped)} groups")
exported_at = datetime.now().strftime("%b %d, %Y %I:%M %p")

# Collect filter options
all_statuses = sorted(set(i["status"] for i in issues))
all_platforms = sorted(set(
    ("Mobile Web" if "MWEB" in i["summary"].upper() else
     "Web" if any(x in i["summary"].upper() for x in ("DWEB","RWEB","WEB")) else
     "iOS" if "IOS" in i["summary"].upper() else
     "Android" if "ANDROID" in i["summary"].upper() else "Other")
    for i in issues
))
all_assignees = sorted(set(i["assignee"] for i in issues if i["assignee"] != "Unassigned"))
all_testers = sorted(tester_names.keys(), key=lambda t: (tester_names.get(t) or t).lower())

def fy(d):
    if not d or len(d) < 7: return ""
    y, m = int(d[:4]), int(d[5:7])
    s = y if m >= 2 else y - 1
    return f"{s}-{s+1}"

def fq(d):
    if not d or len(d) < 7: return ""
    m = int(d[5:7])
    return "Q1" if 2<=m<=4 else "Q2" if 5<=m<=7 else "Q3" if 8<=m<=10 else "Q4"

all_fy = sorted(set(fy(i.get("assigned_date","")) for i in issues if i.get("assigned_date")), reverse=True)

html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8"/>
  <meta name="viewport" content="width=device-width,initial-scale=1.0"/>
  <title>ADA Initiatives</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.3/dist/chart.umd.min.js"></script>
  <style>
    body{{font-family:"Helvetica Neue",Arial,sans-serif;background:#f5f5f5}}
    .chip{{display:inline-block;border-radius:9999px;padding:.15rem .65rem;font-size:.72rem;font-weight:700}}
    .chip-done{{background:#dcfce7;color:#166534}}
    .chip-rfr{{background:#dbeafe;color:#1e3a8a}}
    .chip-wip{{background:#fef9c3;color:#92400e}}
    .chip-backlog{{background:#f3f4f6;color:#4b5563}}
    .chip-platform{{background:#e0e7ff;color:#3730a3}}
    .wmt-btn{{background:#0053e2;color:#fff;border-radius:9999px;padding:.45rem 1.2rem;font-weight:600;font-size:.875rem;border:none;cursor:pointer}}
    .wmt-btn:hover{{background:#0041b5}}
    table{{width:100%;border-collapse:collapse}}
    th{{background:#f8fafc;padding:.6rem 1rem;text-align:left;font-size:.76rem;font-weight:700;text-transform:uppercase;letter-spacing:.04em;color:#374151;border-bottom:2px solid #e5e7eb}}
    td{{padding:.65rem 1rem;border-bottom:1px solid #f0f0f0;font-size:.85rem;vertical-align:middle}}
    tr:hover td{{background:#f8fafc}}
    select,input[type=text]{{border:1px solid #d1d5db;border-radius:.5rem;padding:.4rem .75rem;font-size:.85rem;outline:none;width:100%}}
    select:focus,input:focus{{border-color:#0053e2;box-shadow:0 0 0 2px rgba(0,83,226,.15)}}
    .progress-bar{{height:12px;background:linear-gradient(90deg,#2a8703,#4ade80);border-radius:9999px;transition:width .5s}}
    .badge-export{{background:#f0fdf4;color:#166534;border:1px solid #bbf7d0;border-radius:9999px;padding:.2rem .75rem;font-size:.72rem;font-weight:600}}
  </style>
</head>
<body>

<!-- HEADER -->
<header class="bg-white shadow-sm px-6 py-3 flex items-center justify-between mb-5 sticky top-0 z-10">
  <div class="flex items-center gap-3">
    <svg width="32" height="32" viewBox="0 0 50 50"><circle cx="25" cy="25" r="25" fill="#0053e2"/><text x="50%" y="56%" dominant-baseline="middle" text-anchor="middle" fill="white" font-size="22" font-family="Arial" font-weight="bold">W</text></svg>
    <div>
      <h1 class="text-xl font-black text-gray-900">ADA Initiatives</h1>
      <p class="text-xs text-gray-500">CEACCESS · Snapshot · A11y testers</p>
    </div>
  </div>
  <span class="badge-export">📸 Snapshot: {exported_at}</span>
</header>

<div class="max-w-screen-2xl mx-auto px-4">

<!-- STAT CARDS -->
<div id="stat-cards" class="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3 mb-5"></div>

<!-- PROGRESS BAR -->
<div class="bg-white rounded-xl shadow p-4 mb-5">
  <div class="flex justify-between items-center mb-2">
    <span class="font-semibold text-sm text-gray-700">Overall Completion</span>
    <span id="pct-label" class="font-bold text-sm" style="color:#2a8703"></span>
  </div>
  <div class="w-full bg-gray-200 rounded-full overflow-hidden" style="height:12px">
    <div id="progress-bar" class="progress-bar" style="width:0%"></div>
  </div>
  <p id="progress-sub" class="text-xs text-gray-600 mt-1"></p>
</div>

<!-- CHARTS -->
<div class="grid md:grid-cols-2 gap-4 mb-5">
  <div class="bg-white rounded-xl shadow p-4">
    <h2 class="font-semibold text-sm mb-3 text-gray-700">Status Breakdown</h2>
    <div style="height:220px"><canvas id="statusChart"></canvas></div>
  </div>
  <div class="bg-white rounded-xl shadow p-4">
    <h2 class="font-semibold text-sm mb-3 text-gray-700">Platform Breakdown</h2>
    <div style="height:220px"><canvas id="platformChart"></canvas></div>
  </div>
</div>

<!-- SEARCH -->
<div class="bg-white rounded-xl shadow p-4 flex flex-wrap gap-3 items-center mb-3">
  <input id="search" type="text" placeholder="Search by key or summary..." oninput="applyFilters()"/>
</div>

<!-- DATE FILTERS -->
<div class="bg-white rounded-xl shadow px-4 py-3 flex flex-wrap gap-3 items-center mb-3">
  <span class="text-xs font-semibold text-gray-600 uppercase tracking-wider">Date</span>
  <select id="fFY" onchange="applyFilters()" class="flex-1 min-w-[140px]">
    <option value="">All Years</option>
    {"".join(f'<option value="{y}">FY {y}</option>' for y in all_fy)}
  </select>
  <select id="fFQ" onchange="applyFilters()" class="flex-1 min-w-[150px]">
    <option value="">All Quarters</option>
    <option value="Q1">Q1 (Feb–Apr)</option>
    <option value="Q2">Q2 (May–Jul)</option>
    <option value="Q3">Q3 (Aug–Oct)</option>
    <option value="Q4">Q4 (Nov–Jan)</option>
  </select>
</div>

<!-- COLUMN FILTERS -->
<div class="bg-white rounded-xl shadow px-4 py-3 flex flex-wrap gap-3 items-center mb-4">
  <span class="text-xs font-semibold text-gray-600 uppercase tracking-wider">Filter by</span>
  <select id="fStatus" onchange="applyFilters()" class="flex-1 min-w-[160px]">
    <option value="">All Statuses</option>
    {"".join(f'<option value="{s}">{s}</option>' for s in all_statuses)}
  </select>
  <select id="fPlatform" onchange="applyFilters()" class="flex-1 min-w-[160px]">
    <option value="">All Platforms</option>
    {"".join(f'<option value="{p}">{p}</option>' for p in all_platforms)}
  </select>
  <select id="fAssignee" onchange="applyFilters()" class="flex-1 min-w-[180px]">
    <option value="">All Assignees</option>
    {"".join(f'<option value="{a}">{a}</option>' for a in all_assignees)}
  </select>
  <select id="fTester" onchange="applyFilters()" class="flex-1 min-w-[180px]">
    <option value="">All Testers</option>
    {"".join(f'<option value="{t}">{tester_names.get(t) or t} ({t})</option>' for t in all_testers)}
  </select>
  <a href="#" id="clearBtn" onclick="clearFilters();return false"
     class="text-xs text-gray-600 hover:text-red-500 ml-auto hidden">&#x2715; Clear all filters</a>
</div>

<!-- TABLE -->
<div class="bg-white rounded-xl shadow overflow-auto mb-4">
  <table>
    <thead>
      <tr>
        <th><button onclick="sortBy('name')" class="sort-btn">Initiative <span id="sort-name">⇅</span></button></th>
        <th><button onclick="sortBy('status')" class="sort-btn">Status <span id="sort-status">⇅</span></button></th>
        <th><button onclick="sortBy('platform')" class="sort-btn">Platform <span id="sort-platform">⇅</span></button></th>
        <th><button onclick="sortBy('assignee')" class="sort-btn">Assignee <span id="sort-assignee">⇅</span></button></th>
        <th><button onclick="sortBy('assigned_date')" class="sort-btn">Assigned <span id="sort-assigned_date">⇅</span></button></th>
        <th class="text-right"><button onclick="sortBy('points')" class="sort-btn" style="margin-left:auto">Pts <span id="sort-points">⇅</span></button></th>
        <th>Epic</th>
        <th class="text-right">Bugs</th>
      </tr>
    </thead>
    <tbody id="table-body"></tbody>
  </table>
  <div id="empty-msg" class="p-10 text-center text-gray-600 hidden">
    <p class="text-2xl mb-2">No initiatives found</p>
    <p class="text-sm">Try adjusting your filters.</p>
  </div>
</div>

<!-- PAGINATION -->
<div class="bg-white rounded-xl shadow px-4 py-3 flex flex-wrap items-center justify-between gap-3 mb-4">
  <span id="page-info" class="text-sm text-gray-700"></span>
  <div class="flex items-center gap-2">
    <button id="prevBtn" onclick="changePage(-1)" class="px-3 py-1 rounded border text-sm font-medium text-gray-600 hover:bg-gray-50 disabled:opacity-40" disabled>← Previous</button>
    <span id="page-label" class="text-sm font-semibold text-gray-700 px-2"></span>
    <button id="nextBtn" onclick="changePage(1)" class="px-3 py-1 rounded border text-sm font-medium text-gray-600 hover:bg-gray-50 disabled:opacity-40">Next →</button>
  </div>
  <div class="flex items-center gap-2">
    <span class="text-sm text-gray-700">Rows per page</span>
    <select onchange="pageSize=+this.value;currentPage=1;render()" class="border border-gray-200 rounded px-2 py-1 text-sm" style="width:auto">
      <option value="10">10</option>
      <option value="20" selected>20</option>
      <option value="50">50</option>
      <option value="100">100</option>
    </select>
  </div>
</div>

<footer class="text-center text-xs text-gray-600 pb-4">
  ADA Initiatives · CEACCESS · Static snapshot exported {exported_at}
</footer>

</div><!-- /max-w -->

<style>
.sort-btn{{background:none;border:none;cursor:pointer;color:inherit;font:inherit;text-transform:uppercase;letter-spacing:.04em;font-size:.76rem;font-weight:700;display:flex;align-items:center;gap:4px;padding:0}}
</style>

<script>
const ALL_ISSUES = {json.dumps(issues)};
const ALL_GROUPED = {json.dumps(grouped)};
const JIRA_URL = {json.dumps(jira_url)};
const TESTER_NAMES = {json.dumps(tester_names)};

const STATUS_CHIP = {{
  "Done":"chip-done","Ready for Review":"chip-rfr",
  "Work in Progress":"chip-wip","Backlog":"chip-backlog"
}};
const STATUS_COLOR = {{'Done':'#2a8703','Ready for Review':'#0053e2','Work in Progress':'#d97706','Backlog':'#6b7280'}};
const PLAT_COLORS = ['#0053e2','#ffc220','#2a8703','#ea1100','#7c3aed'];

let filtered = [...ALL_GROUPED];
let currentPage = 1;
let pageSize = 20;
let sortKey = 'name';
let sortDir = 'asc';

function getPlatform(s) {{
  const u = s.toUpperCase();
  if (u.includes('MWEB')) return 'Mobile Web';
  if (u.includes('DWEB')||u.includes('RWEB')||u.includes('WEB')) return 'Web';
  if (u.includes('IOS')) return 'iOS';
  if (u.includes('ANDROID')) return 'Android';
  return 'Other';
}}

function getFY(d) {{
  if (!d||d.length<7) return '';
  const y=+d.slice(0,4), m=+d.slice(5,7);
  const s = m>=2 ? y : y-1;
  return s+'-'+(s+1);
}}

function getFQ(d) {{
  if (!d||d.length<7) return '';
  const m=+d.slice(5,7);
  return m<=4&&m>=2?'Q1':m<=7?'Q2':m<=10?'Q3':'Q4';
}}

function applyFilters() {{
  const q = document.getElementById('search').value.toLowerCase();
  const st = document.getElementById('fStatus').value;
  const pl = document.getElementById('fPlatform').value;
  const as = document.getElementById('fAssignee').value;
  const te = document.getElementById('fTester').value;
  const fy = document.getElementById('fFY').value;
  const fq = document.getElementById('fFQ').value;

  // Filter individual issues first
  let issues = ALL_ISSUES.filter(i => {{
    if (q && !i.summary.toLowerCase().includes(q) && !i.key.toLowerCase().includes(q)) return false;
    if (st && i.status !== st) return false;
    if (pl && getPlatform(i.summary) !== pl) return false;
    if (as && i.assignee !== as) return false;
    if (te && !i.summary.includes('')) return true; // tester filter handled below
    if (fy && getFY(i.assigned_date||'') !== fy) return false;
    if (fq && getFQ(i.assigned_date||'') !== fq) return false;
    return true;
  }});

  // Tester filter: filter by which tester's JQL would include this
  // Since we don't have tester per-issue, filter grouped by tester
  let src = ALL_GROUPED;
  if (te) {{
    // rebuild groups from filtered issues that match tester name in assignees
    const tName = (TESTER_NAMES[te]||te).toLowerCase();
    issues = issues.filter(i => i.assignee && i.assignee.toLowerCase().includes(tName.split(' ')[0]));
  }}

  // Rebuild groups from filtered issues
  const groups = {{}};
  issues.forEach(i => {{
    const name = getInitiativeName(i.summary);
    if (!groups[name]) groups[name] = [];
    groups[name].push(i);
  }});

  filtered = Object.entries(groups).map(([name, grp]) => {{
    const platforms = [...new Set(grp.map(i=>getPlatform(i.summary)))];
    const statuses = {{}};
    grp.forEach(i => statuses[i.status] = (statuses[i.status]||0)+1);
    const dominant = Object.entries(statuses).sort((a,b)=>b[1]-a[1])[0]?.[0]||'';
    const points = grp.reduce((s,i)=>s+(i.points||0),0);
    const assignees = [...new Set(grp.map(i=>i.assignee).filter(a=>a&&a!=='Unassigned'))];
    const epic = grp.find(i=>i.epic)?.epic||'';
    const dates = grp.map(i=>i.assigned_date||'').filter(Boolean).sort();
    const assigned_date = dates[0]||'';
    const bug_count = grp.reduce((s,i)=>s+(i.bug_count||0),0);
    const label = `ADA Audit | ${{platforms.join(' | ')}} | ${{name}}`;
    return {{name,label,platforms,issues:grp,statuses,dominant_status:dominant,points:Math.round(points),assignees,epic,assigned_date,bug_count}};
  }});

  // Show/hide clear button
  const hasFilter = q||st||pl||as||te||fy||fq;
  document.getElementById('clearBtn').classList.toggle('hidden', !hasFilter);

  applySort();
  currentPage = 1;
  render();
  updateCharts(issues);
  updateStats(issues);
}}

function getInitiativeName(s) {{
  s = s.trim();
  for (const p of ['ADA Audit | ','ADA | ','ADA| ']) {{
    if (s.toUpperCase().startsWith(p.toUpperCase())) {{ s = s.slice(p.length); break; }}
  }}
  if (s.includes(' | ')) {{
    const [first, rest] = s.split(' | ');
    if (['IOS','ANDROID','MWEB','DWEB','RWEB','WEB'].some(x=>first.toUpperCase().includes(x))) s = rest;
  }}
  return s.trim();
}}

function clearFilters() {{
  ['search','fStatus','fPlatform','fAssignee','fTester','fFY','fFQ'].forEach(id => {{
    const el = document.getElementById(id);
    el.value = '';
  }});
  applyFilters();
}}

function sortBy(key) {{
  if (sortKey === key) sortDir = sortDir==='asc'?'desc':'asc';
  else {{ sortKey=key; sortDir='asc'; }}
  // Update sort indicators
  document.querySelectorAll('[id^=sort-]').forEach(el => el.textContent='⇅');
  const el = document.getElementById('sort-'+key);
  if (el) el.textContent = sortDir==='asc'?'▲':'▼';
  applySort();
  render();
}}

function applySort() {{
  filtered.sort((a,b) => {{
    let av, bv;
    switch(sortKey) {{
      case 'name': av=a.name.toLowerCase(); bv=b.name.toLowerCase(); break;
      case 'status': av=a.dominant_status; bv=b.dominant_status; break;
      case 'platform': av=a.platforms[0]||''; bv=b.platforms[0]||''; break;
      case 'assignee': av=(a.assignees[0]||'').toLowerCase(); bv=(b.assignees[0]||'').toLowerCase(); break;
      case 'assigned_date': av=a.assigned_date||''; bv=b.assigned_date||''; break;
      case 'points': av=a.points; bv=b.points; break;
      default: av=a.name.toLowerCase(); bv=b.name.toLowerCase();
    }}
    if (av<bv) return sortDir==='asc'?-1:1;
    if (av>bv) return sortDir==='asc'?1:-1;
    return 0;
  }});
}}

function changePage(dir) {{
  const total = Math.max(1, Math.ceil(filtered.length/pageSize));
  currentPage = Math.min(Math.max(1, currentPage+dir), total);
  render();
}}

function render() {{
  const total = filtered.length;
  const totalPages = Math.max(1, Math.ceil(total/pageSize));
  currentPage = Math.min(currentPage, totalPages);
  const start = (currentPage-1)*pageSize;
  const page = filtered.slice(start, start+pageSize);

  document.getElementById('page-info').innerHTML = total
    ? `Showing <strong>${{start+1}}</strong>–<strong>${{Math.min(start+pageSize,total)}}</strong> of <strong>${{total}}</strong> initiatives`
    : 'No initiatives found';
  document.getElementById('page-label').textContent = `Page ${{currentPage}} of ${{totalPages}}`;
  document.getElementById('prevBtn').disabled = currentPage<=1;
  document.getElementById('nextBtn').disabled = currentPage>=totalPages;

  const tbody = document.getElementById('table-body');
  const emptyMsg = document.getElementById('empty-msg');

  if (!page.length) {{
    tbody.innerHTML='';
    emptyMsg.classList.remove('hidden');
    return;
  }}
  emptyMsg.classList.add('hidden');

  tbody.innerHTML = page.map((g,idx) => {{
    const expandable = g.issues.length > 1;
    const gid = 'grp-'+idx;
    const statusChips = Object.entries(g.statuses).map(([st,cnt]) =>
      `<span class="chip ${{STATUS_CHIP[st]||'chip-backlog'}}" style="font-size:.68rem">${{st}}${{cnt>1?' ×'+cnt:''}}</span>`
    ).join(' ');
    const platChips = g.platforms.map(p =>
      `<span class="chip chip-platform" style="font-size:.68rem">${{p}}</span>`
    ).join(' ');
    const assigneeCell = g.assignees.length===0
      ? '<span class="text-gray-400">Unassigned</span>'
      : g.assignees.length===1 ? g.assignees[0]
      : `${{g.assignees[0]}} <span class="text-xs text-gray-400 ml-1">+${{g.assignees.length-1}}</span>`;
    const epicCell = g.epic
      ? `<a href="${{JIRA_URL}}/browse/${{g.epic}}" target="_blank" class="font-mono text-xs text-blue-600 hover:underline">${{g.epic}}</a>`
      : '<span class="text-gray-300">—</span>';
    const bugCell = g.bug_count
      ? `<span class="text-xs font-bold px-2 py-0.5 rounded-full" style="background:#fef3c7;color:#d97706">🐛 ${{g.bug_count}}</span>`
      : '<span class="text-gray-300 text-xs">—</span>';

    const mainRow = `<tr class="${{expandable?'cursor-pointer hover:bg-blue-50':''}}"
      ${{expandable?`onclick="toggle('${{gid}}')" id="${{gid}}-row" style="border-left:3px solid transparent"`:''}}>
      <td class="max-w-sm">
        <div class="flex items-start gap-2">
          ${{expandable?`<span id="${{gid}}-toggle" style="color:#0053e2;font-size:.75rem;min-width:14px;margin-top:2px;transition:transform .15s" class="select-none">▶</span>`:'<span style="min-width:14px;display:inline-block"></span>'}}
          <div>
            <span class="text-xs font-semibold text-gray-800">${{g.label.length>90?g.label.slice(0,90)+'…':g.label}}</span>
            ${{g.issues.length>1?`<span class="text-xs text-gray-400 ml-1">(${{g.issues.length}} tickets)</span>`:''}}
          </div>
        </div>
      </td>
      <td><div class="flex flex-wrap gap-1">${{statusChips}}</div></td>
      <td><div class="flex flex-wrap gap-1">${{platChips}}</div></td>
      <td class="text-gray-600 text-sm">${{assigneeCell}}</td>
      <td class="text-gray-700 text-xs font-mono">${{g.assigned_date||'—'}}</td>
      <td class="text-right font-bold text-gray-700">${{g.points}}</td>
      <td>${{epicCell}}</td>
      <td class="text-right">${{bugCell}}</td>
    </tr>`;

    let subRows = '';
    if (expandable) {{
      const rows = g.issues.map(i => {{
        const plat = getPlatform(i.summary);
        const chip = STATUS_CHIP[i.status]||'chip-backlog';
        const ibug = i.bug_count
          ? `<span class="text-xs font-bold px-2 py-0.5 rounded-full" style="background:#fef3c7;color:#d97706">🐛 ${{i.bug_count}}</span>`
          : '<span style="color:#d1d5db;font-size:.75rem">—</span>';
        return `<tr style="border-bottom:1px solid #e0e7ff">
          <td style="padding:.45rem .75rem"><span class="chip chip-platform" style="font-size:.68rem">${{plat}}</span></td>
          <td style="padding:.45rem .75rem"><a href="${{JIRA_URL}}/browse/${{i.key}}" target="_blank" class="font-mono text-xs font-bold hover:underline" style="color:#0053e2">${{i.key}}</a></td>
          <td style="padding:.45rem .75rem"><span class="chip ${{chip}}" style="font-size:.68rem">${{i.status}}</span></td>
          <td style="padding:.45rem .75rem;color:#4b5563">${{i.assignee}}</td>
          <td style="padding:.45rem .75rem;color:#6b7280;font-family:monospace">${{i.assigned_date||'—'}}</td>
          <td style="padding:.45rem .75rem;text-align:right;font-weight:700;color:#374151">${{Math.round(i.points||0)}}</td>
          <td style="padding:.45rem .75rem">${{i.epic?`<a href="${{JIRA_URL}}/browse/${{i.epic}}" target="_blank" class="font-mono text-xs text-blue-600 hover:underline">${{i.epic}}</a>`:'<span style="color:#d1d5db">—</span>'}}</td>
          <td style="padding:.45rem .75rem;text-align:right">${{ibug}}</td>
        </tr>`;
      }}).join('');

      subRows = `<tr id="${{gid}}" style="display:none">
        <td colspan="8" style="padding:0;background:#f8fafc;border-bottom:2px solid #e0e7ff">
          <table style="width:100%;font-size:.8rem;border-collapse:collapse">
            <thead><tr style="background:#e0e7ff">
              ${{['Platform','Ticket','Status','Assignee','Date','Pts','Epic','Bugs'].map(h=>`<th style="padding:.4rem .75rem;text-align:${{h==='Pts'||h==='Bugs'?'right':'left'}};font-size:.7rem;color:#3730a3;font-weight:700;text-transform:uppercase;letter-spacing:.04em">${{h}}</th>`).join('')}}
            </tr></thead>
            <tbody>${{rows}}</tbody>
          </table>
        </td>
      </tr>`;
    }}
    return mainRow + subRows;
  }}).join('');
}}

function toggle(id) {{
  const row = document.getElementById(id);
  const tog = document.getElementById(id+'-toggle');
  const par = document.getElementById(id+'-row');
  if (!row) return;
  const open = row.style.display !== 'none' && row.style.display !== '';
  row.style.display = open ? 'none' : 'table-row';
  if (tog) tog.style.transform = open ? '' : 'rotate(90deg)';
  if (par) par.style.borderLeftColor = open ? 'transparent' : '#0053e2';
}}

let statusChart, platformChart;
function updateCharts(issues) {{
  const statuses = {{}};
  const platforms = {{}};
  issues.forEach(i => {{
    statuses[i.status] = (statuses[i.status]||0)+1;
    const p = getPlatform(i.summary);
    platforms[p] = (platforms[p]||0)+1;
  }});

  if (statusChart) statusChart.destroy();
  statusChart = new Chart(document.getElementById('statusChart'), {{
    type:'doughnut',
    data:{{ labels:Object.keys(statuses), datasets:[{{data:Object.values(statuses),backgroundColor:Object.keys(statuses).map(k=>STATUS_COLOR[k]||'#999'),borderWidth:2}}] }},
    options:{{responsive:true,maintainAspectRatio:false,plugins:{{legend:{{position:'right',labels:{{boxWidth:12,font:{{size:11}}}}}}}}}}
  }});

  if (platformChart) platformChart.destroy();
  platformChart = new Chart(document.getElementById('platformChart'), {{
    type:'bar',
    data:{{ labels:Object.keys(platforms), datasets:[{{label:'Issues',data:Object.values(platforms),backgroundColor:PLAT_COLORS,borderRadius:6}}] }},
    options:{{responsive:true,maintainAspectRatio:false,plugins:{{legend:{{display:false}}}},scales:{{y:{{ticks:{{stepSize:5}},grid:{{color:'#f0f0f0'}}}}}}}}
  }});
}}

function updateStats(issues) {{
  const done = issues.filter(i=>i.status==='Done').length;
  const pct = issues.length ? Math.round(done/issues.length*100) : 0;
  const statuses = {{}};
  const pts = issues.reduce((s,i)=>s+(i.points||0),0);
  issues.forEach(i=>statuses[i.status]=(statuses[i.status]||0)+1);

  const initiatives = new Set(issues.map(i=>getInitiativeName(i.summary))).size;
  const cards = [
    ['Initiatives', initiatives, '#0053e2'],
    ['Total Tickets', issues.length, '#0e7490'],
    ['Done', done, '#2a8703'],
    ['Ready for Review', statuses['Ready for Review']||0, '#0053e2'],
    ['In Progress', statuses['Work in Progress']||0, '#d97706'],
    ['Story Points', Math.round(pts), '#7c3aed'],
  ];
  document.getElementById('stat-cards').innerHTML = cards.map(([l,v,c])=>
    `<div class="bg-white rounded-xl shadow p-4 text-center">
      <div class="text-3xl font-black" style="color:${{c}}">${{v}}</div>
      <div class="text-xs font-semibold text-gray-700 mt-1">${{l}}</div>
    </div>`
  ).join('');

  document.getElementById('pct-label').textContent = pct+'% Done';
  document.getElementById('progress-bar').style.width = pct+'%';
  document.getElementById('progress-sub').textContent = done+' of '+issues.length+' issues complete';
}}

// Init
applyFilters();
</script>
</body>
</html>"""

out = "index.html"
with open(out, "w", encoding="utf-8") as f:
    f.write(html)

print(f"\n✅ Built → {out}")
print(f"   Issues: {len(issues)} | Groups: {len(grouped)}")
print(f"   Size: {len(html):,} bytes")
