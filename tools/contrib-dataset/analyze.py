#!/usr/bin/env python3
"""analyze.py — run key queries against contrib.db and print a dashboard.

Usage
-----
  ./analyze.py                   # summary dashboard, text tables
  ./analyze.py --plot            # add matplotlib charts (requires matplotlib)
  ./analyze.py --db PATH         # point at a different database file
  ./analyze.py --output FILE     # write JSON results alongside the terminal output

No mandatory deps: pure sqlite3 + stdlib. matplotlib is optional (--plot).
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from collections import defaultdict
from typing import Any

# ─────────────────────────────────────────────────────────────────────────────
# helpers
# ─────────────────────────────────────────────────────────────────────────────

def _db(path: str) -> sqlite3.Connection:
    if not os.path.exists(path):
        sys.exit(f"Database not found: {path}\nRun ./contrib.py sync first.")
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def _rows(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
    return conn.execute(sql, params).fetchall()


def _print_table(title: str, rows: list[sqlite3.Row], keys: list[str] | None = None) -> None:
    if not rows:
        print(f"\n── {title} ──\n  (no data)\n")
        return
    if keys is None:
        keys = list(rows[0].keys())
    col_w = {k: max(len(k), max(len(str(r[k] or "")) for r in rows)) for k in keys}
    header = "  ".join(k.ljust(col_w[k]) for k in keys)
    sep = "  ".join("─" * col_w[k] for k in keys)
    print(f"\n── {title} ──")
    print(header)
    print(sep)
    for r in rows:
        print("  ".join(str(r[k] or "").ljust(col_w[k]) for k in keys))
    print()


# ─────────────────────────────────────────────────────────────────────────────
# individual analyses
# ─────────────────────────────────────────────────────────────────────────────

def overview(conn: sqlite3.Connection) -> dict[str, Any]:
    row = _rows(conn, """
        SELECT
            (SELECT COUNT(*) FROM prs)                          AS total_prs,
            (SELECT COUNT(*) FROM prs WHERE merged=1)           AS merged_prs,
            (SELECT COUNT(*) FROM prs WHERE state='open')       AS open_prs,
            (SELECT COALESCE(SUM(additions),0) FROM prs)        AS total_additions,
            (SELECT COALESCE(SUM(deletions),0) FROM prs)        AS total_deletions,
            (SELECT COUNT(*) FROM comments)                     AS total_comments,
            (SELECT COUNT(*) FROM comments WHERE direction='given')     AS given,
            (SELECT COUNT(*) FROM comments WHERE direction='received')  AS received,
            (SELECT MIN(created_at) FROM prs)                   AS project_start,
            (SELECT MAX(COALESCE(merged_at,closed_at)) FROM prs) AS last_activity
    """)[0]

    d = dict(row)
    total = d["total_additions"] + d["total_deletions"]
    print("\n══════════════════════════════════════════════════════")
    print("  Contribution Dataset — Overview")
    print("══════════════════════════════════════════════════════")
    print(f"  PRs          {d['total_prs']:>6}  ({d['merged_prs']} merged, {d['open_prs']} open)")
    print(f"  Lines added  {d['total_additions']:>6,}")
    print(f"  Lines deleted{d['total_deletions']:>6,}")
    print(f"  Total churn  {total:>6,}")
    print(f"  Comments     {d['total_comments']:>6}  ({d['given']} given, {d['received']} received)")
    print(f"  Period       {(d['project_start'] or '?')[:10]}  →  {(d['last_activity'] or '?')[:10]}")
    print()
    return d


def monthly_prs(conn: sqlite3.Connection) -> list[dict]:
    rows = _rows(conn, """
        SELECT
            strftime('%Y-%m', merged_at)                AS month,
            COUNT(*)                                    AS prs,
            COALESCE(SUM(additions),0)                  AS added,
            COALESCE(SUM(deletions),0)                  AS deleted,
            COALESCE(SUM(additions+deletions),0)        AS churn
        FROM prs
        WHERE merged=1 AND merged_at IS NOT NULL
        GROUP BY 1 ORDER BY 1
    """)
    _print_table("Merged PRs by Month", rows, ["month","prs","added","deleted","churn"])
    return [dict(r) for r in rows]


def per_repo(conn: sqlite3.Connection) -> list[dict]:
    rows = _rows(conn, """
        SELECT
            repo,
            COUNT(*)                            AS prs,
            SUM(merged)                         AS merged,
            COALESCE(SUM(additions),0)          AS added,
            COALESCE(SUM(deletions),0)          AS deleted
        FROM prs
        GROUP BY repo ORDER BY merged DESC
    """)
    _print_table("Per-Repo Breakdown", rows, ["repo","prs","merged","added","deleted"])
    return [dict(r) for r in rows]


def comment_activity(conn: sqlite3.Connection) -> dict[str, Any]:
    by_kind = _rows(conn, """
        SELECT kind, COUNT(*) AS cnt
        FROM comments WHERE direction='given'
        GROUP BY kind ORDER BY cnt DESC
    """)
    by_month = _rows(conn, """
        SELECT strftime('%Y-%m', created_at) AS month,
               COUNT(*) AS comments_given
        FROM comments WHERE direction='given'
        GROUP BY 1 ORDER BY 1
    """)
    review_decisions = _rows(conn, """
        SELECT state, COUNT(*) AS cnt
        FROM comments
        WHERE kind='review' AND direction='given' AND state IS NOT NULL
        GROUP BY state ORDER BY cnt DESC
    """)

    _print_table("Comments Given — by Kind", by_kind, ["kind","cnt"])
    _print_table("Comments Given — by Month", by_month, ["month","comments_given"])
    if review_decisions:
        _print_table("Review Decisions Submitted", review_decisions, ["state","cnt"])

    return {
        "by_kind": [dict(r) for r in by_kind],
        "by_month": [dict(r) for r in by_month],
        "review_decisions": [dict(r) for r in review_decisions],
    }


def pr_size_distribution(conn: sqlite3.Connection) -> list[dict]:
    rows = _rows(conn, """
        SELECT
            CASE
                WHEN additions+deletions <=   50 THEN 'XS (≤50)'
                WHEN additions+deletions <=  200 THEN 'S  (≤200)'
                WHEN additions+deletions <=  500 THEN 'M  (≤500)'
                WHEN additions+deletions <= 2000 THEN 'L  (≤2000)'
                ELSE                                  'XL (>2000)'
            END                                 AS bucket,
            COUNT(*)                            AS prs,
            ROUND(AVG(additions+deletions))     AS avg_churn
        FROM prs
        WHERE merged=1 AND additions IS NOT NULL
        GROUP BY 1 ORDER BY 1
    """)
    _print_table("PR Size Distribution (merged)", rows, ["bucket","prs","avg_churn"])
    return [dict(r) for r in rows]


def velocity(conn: sqlite3.Connection) -> dict[str, Any]:
    row = _rows(conn, """
        WITH weeks AS (
            SELECT
                CAST(julianday(merged_at) - julianday(MIN(merged_at) OVER ()) AS INTEGER)/7+1 AS wk,
                additions, deletions
            FROM prs WHERE merged=1 AND merged_at IS NOT NULL
        )
        SELECT
            ROUND(AVG(c),1)   AS avg_prs_per_week,
            ROUND(AVG(a),0)   AS avg_additions_per_week,
            ROUND(AVG(d),0)   AS avg_deletions_per_week
        FROM (
            SELECT wk, COUNT(*) c, COALESCE(SUM(additions),0) a, COALESCE(SUM(deletions),0) d
            FROM weeks GROUP BY wk
        )
    """)
    if row:
        r = dict(row[0])
        print("── Weekly Velocity ──")
        print(f"  PRs/week          {r.get('avg_prs_per_week', '—')}")
        print(f"  Additions/week    {r.get('avg_additions_per_week', '—')}")
        print(f"  Deletions/week    {r.get('avg_deletions_per_week', '—')}")
        print()
        return r
    return {}


def top_prs(conn: sqlite3.Connection, n: int = 10) -> list[dict]:
    rows = _rows(conn, """
        SELECT repo, number, title,
               additions+deletions AS churn,
               strftime('%Y-%m-%d', merged_at) AS merged
        FROM prs
        WHERE merged=1 AND additions IS NOT NULL
        ORDER BY churn DESC LIMIT ?
    """, (n,))
    _print_table(f"Top {n} PRs by Churn", rows, ["repo","number","churn","merged","title"])
    return [dict(r) for r in rows]


def cycle_time(conn: sqlite3.Connection) -> list[dict]:
    rows = _rows(conn, """
        SELECT repo,
               COUNT(*) AS prs,
               ROUND(AVG(julianday(merged_at)-julianday(created_at)),1) AS avg_days,
               ROUND(MIN(julianday(merged_at)-julianday(created_at)),1) AS min_days,
               ROUND(MAX(julianday(merged_at)-julianday(created_at)),1) AS max_days
        FROM prs
        WHERE merged=1 AND merged_at IS NOT NULL AND created_at IS NOT NULL
        GROUP BY repo ORDER BY avg_days
    """)
    _print_table("PR Cycle Time (days open before merge)", rows,
                 ["repo","prs","avg_days","min_days","max_days"])
    return [dict(r) for r in rows]


# ─────────────────────────────────────────────────────────────────────────────
# optional matplotlib charts
# ─────────────────────────────────────────────────────────────────────────────

def _plot(monthly: list[dict], comment_months: list[dict]) -> None:
    try:
        import matplotlib.pyplot as plt
        import matplotlib.ticker as mtick
    except ImportError:
        print("matplotlib not installed — skipping charts. pip install matplotlib")
        return

    months_pr   = [r["month"] for r in monthly]
    prs         = [r["prs"] for r in monthly]
    added       = [r["added"] for r in monthly]
    deleted     = [r["deleted"] for r in monthly]
    cm_months   = [r["month"] for r in comment_months]
    comments    = [r["comments_given"] for r in comment_months]

    fig, axes = plt.subplots(3, 1, figsize=(12, 10))
    fig.suptitle("Contribution Dataset", fontsize=14, fontweight="bold")

    # chart 1: PRs per month
    ax = axes[0]
    ax.bar(months_pr, prs, color="steelblue")
    ax.set_title("Merged PRs per Month")
    ax.set_ylabel("PRs")
    ax.tick_params(axis="x", rotation=45)

    # chart 2: line churn per month
    ax = axes[1]
    ax.bar(months_pr, added, label="Added", color="forestgreen")
    ax.bar(months_pr, [-d for d in deleted], label="Deleted", color="tomato")
    ax.set_title("Line Churn per Month")
    ax.set_ylabel("Lines")
    ax.yaxis.set_major_formatter(mtick.FuncFormatter(lambda x, _: f"{abs(int(x)):,}"))
    ax.legend()
    ax.tick_params(axis="x", rotation=45)

    # chart 3: comments given per month
    ax = axes[2]
    ax.bar(cm_months, comments, color="mediumpurple")
    ax.set_title("Comments Given per Month")
    ax.set_ylabel("Comments")
    ax.tick_params(axis="x", rotation=45)

    plt.tight_layout()
    out = "out/charts.png"
    os.makedirs("out", exist_ok=True)
    plt.savefig(out, dpi=150, bbox_inches="tight")
    print(f"Charts saved → {out}")
    plt.show()


# ─────────────────────────────────────────────────────────────────────────────
# self-contained HTML dashboard
# ─────────────────────────────────────────────────────────────────────────────

_HTML_TEMPLATE = """\
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Contribution Dashboard</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4/dist/chart.umd.min.js"></script>
<style>
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: system-ui, sans-serif; background: #0f1117; color: #e2e8f0;
         padding: 1.5rem; }
  h1  { font-size: 1.4rem; font-weight: 700; margin-bottom: 0.25rem; }
  .subtitle { color: #94a3b8; font-size: 0.85rem; margin-bottom: 2rem; }
  .grid { display: grid; gap: 1.25rem;
          grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); }
  .card { background: #1e2330; border-radius: 10px; padding: 1.25rem; }
  .card h2 { font-size: 0.75rem; text-transform: uppercase; letter-spacing: .06em;
             color: #64748b; margin-bottom: 0.75rem; }
  .stat-big { font-size: 2.4rem; font-weight: 700; line-height: 1; }
  .stat-sub { font-size: 0.8rem; color: #94a3b8; margin-top: 0.2rem; }
  .chart-card { background: #1e2330; border-radius: 10px; padding: 1.25rem; }
  .chart-card h2 { font-size: 0.75rem; text-transform: uppercase; letter-spacing: .06em;
                   color: #64748b; margin-bottom: 0.75rem; }
  .charts { display: grid; gap: 1.25rem;
            grid-template-columns: repeat(auto-fill, minmax(460px, 1fr));
            margin-top: 1.25rem; }
  table { width: 100%; border-collapse: collapse; font-size: 0.82rem; margin-top: 1.25rem; }
  th { text-align: left; color: #64748b; padding: 0.35rem 0.5rem;
       border-bottom: 1px solid #2d3748; font-weight: 500; }
  td { padding: 0.35rem 0.5rem; border-bottom: 1px solid #1a2032; }
  tr:last-child td { border-bottom: none; }
  td.num { text-align: right; font-variant-numeric: tabular-nums; }
  .pill { display: inline-block; padding: 0.1rem 0.45rem; border-radius: 9999px;
          font-size: 0.7rem; font-weight: 600; background: #1e3a5f; color: #60a5fa; }
  canvas { max-height: 260px; }
</style>
</head>
<body>
<h1>Contribution Dashboard</h1>
<p class="subtitle">
  Period: <strong>__PERIOD__</strong> &nbsp;·&nbsp;
  Generated: <strong>__GENERATED__</strong>
</p>

<!-- KPI row -->
<div class="grid">
  <div class="card">
    <h2>Merged PRs</h2>
    <div class="stat-big">__MERGED_PRS__</div>
    <div class="stat-sub">of __TOTAL_PRS__ total &nbsp;·&nbsp; __OPEN_PRS__ open</div>
  </div>
  <div class="card">
    <h2>Lines Added</h2>
    <div class="stat-big" style="color:#4ade80">+__LINES_ADDED__</div>
    <div class="stat-sub">__LINES_DELETED__ deleted &nbsp;·&nbsp; __CHURN__ total churn</div>
  </div>
  <div class="card">
    <h2>Comments Given</h2>
    <div class="stat-big" style="color:#a78bfa">__COMMENTS_GIVEN__</div>
    <div class="stat-sub">__COMMENTS_RECEIVED__ received from others</div>
  </div>
  <div class="card">
    <h2>Weekly Velocity</h2>
    <div class="stat-big">__PRS_PER_WEEK__</div>
    <div class="stat-sub">PRs/week &nbsp;·&nbsp; __ADDS_PER_WEEK__ lines added/week</div>
  </div>
</div>

<!-- Charts -->
<div class="charts">
  <div class="chart-card">
    <h2>Merged PRs per Month</h2>
    <canvas id="cPRs"></canvas>
  </div>
  <div class="chart-card">
    <h2>Line Churn per Month</h2>
    <canvas id="cChurn"></canvas>
  </div>
  <div class="chart-card">
    <h2>Comments Given per Month</h2>
    <canvas id="cComments"></canvas>
  </div>
  <div class="chart-card">
    <h2>PRs by Repo</h2>
    <canvas id="cRepos"></canvas>
  </div>
  <div class="chart-card">
    <h2>PR Size Distribution</h2>
    <canvas id="cSizes"></canvas>
  </div>
  <div class="chart-card">
    <h2>Cumulative Merged PRs</h2>
    <canvas id="cCumulative"></canvas>
  </div>
</div>

<!-- Tables -->
<div class="charts">
  <div class="chart-card">
    <h2>Per-Repo Breakdown</h2>
    <table id="tRepos"></table>
  </div>
  <div class="chart-card">
    <h2>Top PRs by Churn</h2>
    <table id="tTopPRs"></table>
  </div>
  <div class="chart-card">
    <h2>Cycle Time (days open before merge)</h2>
    <table id="tCycle"></table>
  </div>
  <div class="chart-card">
    <h2>Review Decisions Submitted</h2>
    <table id="tReviews"></table>
  </div>
</div>

<script>
const DATA = __JSON_DATA__;

// ── chart helpers ────────────────────────────────────────────────────────────
const FONT = { color: '#94a3b8', family: 'system-ui, sans-serif', size: 11 };
Chart.defaults.color = '#94a3b8';
Chart.defaults.borderColor = '#2d3748';

function bar(id, labels, datasets, opts = {}) {
  new Chart(document.getElementById(id), {
    type: 'bar',
    data: { labels, datasets },
    options: {
      responsive: true, maintainAspectRatio: true,
      plugins: { legend: { labels: { font: FONT } } },
      scales: {
        x: { ticks: { font: FONT, maxRotation: 45 }, grid: { color: '#2d3748' } },
        y: { ticks: { font: FONT, callback: v => v.toLocaleString() },
             grid: { color: '#2d3748' }, ...( opts.yOpts || {}) },
      },
      ...( opts.extra || {}),
    },
  });
}

function doughnut(id, labels, data, colors) {
  new Chart(document.getElementById(id), {
    type: 'doughnut',
    data: { labels, datasets: [{ data, backgroundColor: colors, borderWidth: 1,
                                  borderColor: '#1e2330' }] },
    options: {
      responsive: true, maintainAspectRatio: true,
      plugins: { legend: { position: 'right', labels: { font: FONT, boxWidth: 12 } } },
    },
  });
}

function line(id, labels, datasets) {
  new Chart(document.getElementById(id), {
    type: 'line',
    data: { labels, datasets },
    options: {
      responsive: true, maintainAspectRatio: true,
      plugins: { legend: { labels: { font: FONT } } },
      scales: {
        x: { ticks: { font: FONT, maxRotation: 45 }, grid: { color: '#2d3748' } },
        y: { ticks: { font: FONT, callback: v => v.toLocaleString() },
             grid: { color: '#2d3748' } },
      },
    },
  });
}

function table(id, cols, rows, numCols = []) {
  const el = document.getElementById(id);
  el.innerHTML = '<thead><tr>' +
    cols.map(c => `<th>${c}</th>`).join('') +
    '</tr></thead>';
  const body = document.createElement('tbody');
  rows.forEach(r => {
    const tr = document.createElement('tr');
    cols.forEach((c, i) => {
      const td = document.createElement('td');
      const val = r[c.toLowerCase().replace(/ /g,'_')] ?? r[Object.keys(r)[i]] ?? '';
      td.textContent = typeof val === 'number' ? val.toLocaleString() : val;
      if (numCols.includes(i)) td.className = 'num';
      tr.appendChild(td);
    });
    body.appendChild(tr);
  });
  el.appendChild(body);
}

// ── monthly PR chart ─────────────────────────────────────────────────────────
const mp = DATA.monthly_prs;
bar('cPRs',
  mp.map(r => r.month),
  [{ label: 'Merged PRs', data: mp.map(r => r.prs),
     backgroundColor: '#3b82f6', borderRadius: 3 }]
);

// ── churn chart (stacked adds/deletes) ───────────────────────────────────────
bar('cChurn',
  mp.map(r => r.month),
  [
    { label: 'Added',   data: mp.map(r => r.added),   backgroundColor: '#4ade80', borderRadius: 3 },
    { label: 'Deleted', data: mp.map(r => r.deleted), backgroundColor: '#f87171', borderRadius: 3 },
  ],
  { extra: { scales: { x: { stacked: true }, y: { stacked: true,
      ticks: { font: FONT, callback: v => v.toLocaleString() }, grid: { color: '#2d3748' } } } } }
);

// ── comments by month ────────────────────────────────────────────────────────
const cm = DATA.comment_activity.by_month;
bar('cComments',
  cm.map(r => r.month),
  [{ label: 'Comments given', data: cm.map(r => r.comments_given),
     backgroundColor: '#a78bfa', borderRadius: 3 }]
);

// ── PRs by repo ──────────────────────────────────────────────────────────────
const repoColors = ['#3b82f6','#06b6d4','#8b5cf6','#f59e0b','#10b981','#f43f5e','#64748b','#e879f9'];
const pr = DATA.per_repo;
bar('cRepos',
  pr.map(r => r.repo.split('/').pop()),
  [{ label: 'Merged', data: pr.map(r => r.merged),
     backgroundColor: pr.map((_, i) => repoColors[i % repoColors.length]), borderRadius: 3 }]
);

// ── size distribution ────────────────────────────────────────────────────────
const sd = DATA.size_distribution;
doughnut('cSizes',
  sd.map(r => r.bucket),
  sd.map(r => r.prs),
  ['#3b82f6','#06b6d4','#8b5cf6','#f59e0b','#f43f5e']
);

// ── cumulative PRs line ──────────────────────────────────────────────────────
let cum = 0;
line('cCumulative',
  mp.map(r => r.month),
  [{ label: 'Cumulative merged PRs',
     data: mp.map(r => { cum += r.prs; return cum; }),
     borderColor: '#38bdf8', backgroundColor: 'rgba(56,189,248,0.12)',
     fill: true, tension: 0.3, pointRadius: 3 }]
);

// ── tables ───────────────────────────────────────────────────────────────────
table('tRepos',
  ['Repo','PRs','Merged','Added','Deleted'],
  DATA.per_repo.map(r => ({ ...r, repo: r.repo.split('/').pop() })),
  [1,2,3,4]
);
table('tTopPRs',
  ['Repo','#','Churn','Merged','Title'],
  DATA.top_prs.map(r => ({
    repo: r.repo.split('/').pop(), '#': r.number,
    churn: r.churn, merged: r.merged, title: (r.title || '').slice(0, 60)
  })),
  [1,2]
);
table('tCycle',
  ['Repo','PRs','Avg days','Min','Max'],
  DATA.cycle_time.map(r => ({ ...r, repo: r.repo.split('/').pop() })),
  [1,2,3,4]
);
const rd = DATA.comment_activity.review_decisions || [];
table('tReviews', ['Decision','Count'], rd.map(r => ({ decision: r.state, count: r.cnt })), [1]);
</script>
</body>
</html>
"""


def _emit_html(results: dict[str, Any], path: str) -> None:
    import datetime

    ov = results["overview"]
    vel = results.get("velocity") or {}
    period = f"{(ov.get('project_start') or '?')[:10]}  →  {(ov.get('last_activity') or '?')[:10]}"
    total = ov.get("total_additions", 0) + ov.get("total_deletions", 0)

    def fmt(n: Any) -> str:
        if n is None:
            return "—"
        try:
            return f"{int(n):,}"
        except (TypeError, ValueError):
            return str(n)

    html = _HTML_TEMPLATE
    html = html.replace("__PERIOD__",            period)
    html = html.replace("__GENERATED__",         datetime.date.today().isoformat())
    html = html.replace("__MERGED_PRS__",        fmt(ov.get("merged_prs")))
    html = html.replace("__TOTAL_PRS__",         fmt(ov.get("total_prs")))
    html = html.replace("__OPEN_PRS__",          fmt(ov.get("open_prs")))
    html = html.replace("__LINES_ADDED__",       fmt(ov.get("total_additions")))
    html = html.replace("__LINES_DELETED__",     fmt(ov.get("total_deletions")))
    html = html.replace("__CHURN__",             fmt(total))
    html = html.replace("__COMMENTS_GIVEN__",    fmt(ov.get("given")))
    html = html.replace("__COMMENTS_RECEIVED__", fmt(ov.get("received")))
    html = html.replace("__PRS_PER_WEEK__",      fmt(vel.get("avg_prs_per_week")))
    html = html.replace("__ADDS_PER_WEEK__",     fmt(vel.get("avg_additions_per_week")))
    html = html.replace("__JSON_DATA__",         json.dumps(results, default=str))

    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as f:
        f.write(html)
    print(f"Dashboard written → {path}  (open in any browser, no server needed)")


# ─────────────────────────────────────────────────────────────────────────────
# main
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(description="Contribution dataset analysis dashboard")
    ap.add_argument("--db",     default="contrib.db", metavar="PATH",
                    help="SQLite database (default: contrib.db)")
    ap.add_argument("--plot",   action="store_true",
                    help="Render matplotlib charts (requires matplotlib)")
    ap.add_argument("--output", metavar="FILE",
                    help="Write JSON results to FILE")
    ap.add_argument("--top",    type=int, default=10, metavar="N",
                    help="How many top PRs to show (default: 10)")
    ap.add_argument("--html",   metavar="FILE",
                    help="Write a self-contained HTML dashboard to FILE (no server needed)")
    args = ap.parse_args()

    conn = _db(args.db)

    results: dict[str, Any] = {}
    results["overview"]          = overview(conn)
    results["monthly_prs"]       = monthly_prs(conn)
    results["per_repo"]          = per_repo(conn)
    results["comment_activity"]  = comment_activity(conn)
    results["size_distribution"] = pr_size_distribution(conn)
    results["velocity"]          = velocity(conn)
    results["top_prs"]           = top_prs(conn, n=args.top)
    results["cycle_time"]        = cycle_time(conn)

    if args.output:
        os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
        with open(args.output, "w") as f:
            json.dump(results, f, indent=2, default=str)
        print(f"Results written → {args.output}")

    if args.plot:
        _plot(results["monthly_prs"], results["comment_activity"]["by_month"])

    if args.html:
        _emit_html(results, args.html)

    conn.close()


if __name__ == "__main__":
    main()
