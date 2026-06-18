# CLAUDE.md

Development guide for this repo — read this first when resuming work in a new session.

## What this is

A dashboard of my (`ixtli`) GitHub contributions across **attentive-mobile** repos,
deployed as a static site on **GitHub Pages**.

**Live site:** https://ixtli.github.io/contribs/ *(goes live once Pages is enabled —
Settings → Pages → Source: GitHub Actions — and the workflow has run on `main`).*

Two halves:

1. **Data tool** (`tools/contrib-dataset/`) — Python, stdlib-only. Syncs the GitHub
   API into a local SQLite DB and can render an offline report.
2. **Dashboard SPA** (`public/index.html`) — loads SQLite in the browser via WASM
   (sql.js) and queries the committed DB live. No build, no server, no token at view time.

## Architecture / data flow

```
GitHub API ──contrib.py sync──▶ contrib.db (SQLite, committed)
                                     │
              ┌──────────────────────┴───────────────────────┐
              ▼                                                ▼
  public/index.html (sql.js/WASM,                  analyze.py (offline CLI
  queries the DB in the browser)                   report; optional --html)
              │
              ▼
  .github/workflows/pages.yml  ──▶  GitHub Pages
  (copies index.html + contrib.db into the artifact, deploys; no Python at deploy)
```

The DB **is the source of truth and is committed** (`tools/contrib-dataset/contrib.db`,
~550 KB). Refresh = re-sync locally, commit the new DB, push → Pages redeploys.

## Key files

| Path | Role |
|---|---|
| `tools/contrib-dataset/contrib.py` | Sync/export/report CLI. Writes `contrib.db`. |
| `tools/contrib-dataset/analyze.py` | Offline CLI report; `--html` emits a self-contained dashboard (data baked in — a different rendering path from the live SPA). |
| `tools/contrib-dataset/queries.sql` | Reference SQL (mirrors the analyses). |
| `tools/contrib-dataset/repos.txt` | Repo list the sync runs over (`@repos.txt`). |
| `tools/contrib-dataset/contrib.db` | The committed dataset. |
| `public/index.html` | The deployed SPA (sql.js + Chart.js from CDN). |
| `.github/workflows/pages.yml` | Pages deploy (assemble + publish). |

## Common workflows

```bash
cd tools/contrib-dataset

# Refresh data (needs a token; incremental via the sync_state cursor table — safe to re-run)
export GH_TOKEN=<github token, repo read scope>
./contrib.py sync --repos @repos.txt --since 2026-01-01 --verbose

# Terminal report (no browser, no deps)
./analyze.py --db contrib.db

# Preview the SPA locally (the DB must sit next to index.html and be served over HTTP)
cp contrib.db ../../public/ && python3 -m http.server -d ../../public 8000
#   → http://localhost:8000   (public/contrib.db is gitignored — it's a copy)

# Commit a refreshed dataset (this is what triggers a redeploy)
git commit -am "Refresh contribution data" && git push
```

Deploy is automatic on push to `main` (paths: `public/**`, the DB, the workflow), or
run manually: **Actions → Deploy dashboard to GitHub Pages → Run workflow**.
One-time setup: **Settings → Pages → Source: GitHub Actions**.

## Design decisions & constraints (don't regress these)

- **Metadata only — no free text.** The schema deliberately omits PR **titles**,
  comment/review **bodies**, and file **paths**. Only repo, PR number, state,
  timestamps, line/comment/review counts, and review decisions are stored. This is
  what makes a public Pages site safe. If you add columns, keep this rule.
- **Scope:** PRs **created since 2026-01-01** in the attentive-mobile repos listed in
  `repos.txt`. Change scope via `--since` / `repos.txt`, then re-sync from a fresh DB
  (`rm contrib.db`) if you want the cursors reset.
- **`ixtli/contribs` is private; the Pages site is public.** Pages on a private repo
  needs a paid GitHub plan. Safe because of the metadata-only rule above.
- **`received` comments are not collected** (the `--include-received` flag exists but
  is off). `direction='received'` is therefore empty by design.
- **sql.js parity:** the SPA runs the *same* SQLite engine as `analyze.py`, so any query
  that works in Python's `sqlite3` (incl. window functions, `strftime`, `julianday`)
  works in the browser. Validate new queries against `contrib.db` before shipping.

## Gotchas / history

- **Monorepo comment pagination (HTTP 422):** GitHub caps deep pagination on
  high-volume repos (e.g. `attentive-mobile/code`). `contrib.py` handles the 422 by
  stopping that resource's pagination gracefully instead of aborting the whole sync
  (commit `a51ee71`). Don't "fix" this by removing the guard.
- **Token is ephemeral.** Sessions on the web use a short-lived `GH_TOKEN`; it is never
  committed. The deploy needs **no** token (the DB is already committed).
- **Two rendering paths exist:** the live WASM SPA (`public/index.html`, deployed) and
  `analyze.py --html` (offline, self-contained). They share styling/queries but are
  separate. The SPA is the deployed one.

## Current data snapshot (last sync 2026-06-18)

726 PRs (693 merged, 7 open) · 1,192 comments/reviews given · 19 repos ·
period 2026-01-06 → 2026-06-17.

## Likely next steps

- Add filtering/interactivity to the SPA (it already has the full DB client-side — e.g.
  a repo or date-range selector that re-runs the queries).
- Broaden scope (more repos / earlier `--since`) — update `repos.txt` and re-sync.
- The `repos` table records reachability; useful if access expands.
