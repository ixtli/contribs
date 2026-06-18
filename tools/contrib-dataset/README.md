# contrib-dataset

A small, dependency-free tool that assembles a **personal contribution dataset**
across a set of GitHub repos — your pull requests, the line churn they carried,
and the **comments and reviews you author** (the feedback you give) — into a
SQLite database you can re-sync over time and run experiments against.

It was built to account for one person's work across the a1e repos, but it takes
an arbitrary repo list and author list, so it scales to a single **massive**
code repo just as well.

## Why SQLite

- **Re-runnable / incremental.** Every record is written with an `UPSERT`, and a
  per-`(repo, resource)` high-water-mark lives in a `sync_state` table. Re-running
  `sync` only fetches what changed since last time (`since` on comment endpoints,
  a `created:` date window on PR search), then merges it in. Safe to run on a cron.
- **Queryable for experiments.** `sqlite3 contrib.db` and write SQL, or `export`
  to CSV / `summary.json`. No data-frame library required to get started.

## What it captures

| Table | Rows | Notes |
|---|---|---|
| `prs` | PRs **authored** by an author | state, draft, merged, created/updated/merged/closed, `additions`/`deletions`/`changed_files`, comment counts, url |
| `comments` | comments + reviews | `kind` ∈ `issue_comment` \| `review_comment` \| `review`; `direction` ∈ `given` \| `received` |
| `repos` | per-repo reachability | so an inaccessible repo is recorded, not silently dropped |
| `sync_state` | incremental cursors | `(repo, resource) → last_since` |

"Comments given" are collected **repo-wide** (every issue/PR comment and review
authored by you, on anyone's PR) — that is the primary signal of feedback you
provide as a reviewer. `--include-received` additionally pulls comments **others**
left on **your** PRs.

## Auth

No third-party packages. Credentials, in priority order:

1. **`gh` CLI** if installed and `gh auth status` is green (recommended — handles
   token refresh and SSO).
2. **`GITHUB_TOKEN`** / **`GH_TOKEN`** env var (a PAT with `repo` read scope, or a
   fine-grained token with *Pull requests: read* + *Contents: read* on the repos).

## Usage

```bash
# Default: the a1e repo set, author `ixtli`, everything (prs + comments + reviews)
./contrib.py sync

# A single huge repo, only history since a date
./contrib.py sync --repos attentive-mobile/<bigrepo> --authors ixtli --since 2023-01-01

# Faster: skip the per-PR line-stat call (1 API call per PR). Line columns stay NULL.
./contrib.py sync --no-line-stats

# Also capture feedback you received on your PRs
./contrib.py sync --include-received

# Limit to specific resources
./contrib.py sync --resources prs,comments

# Repo list from a file (one owner/name per line, # comments allowed)
./contrib.py sync --repos @repos.txt

# Export tidy artifacts for analysis, and print a summary
./contrib.py export        # → out/prs.csv, out/comments.csv, out/summary.json
./contrib.py report
```

All commands take `--db PATH` (default `contrib.db`).

## Analysis and visualization

`analyze.py` runs the full analysis suite and prints text tables:

```bash
./analyze.py                        # dashboard to stdout
./analyze.py --plot                 # + matplotlib charts → out/charts.png (optional dep)
./analyze.py --output out/data.json # also write JSON
```

Generate a **self-contained HTML dashboard** — no server needed, just open the file in a browser:

```bash
./analyze.py --html out/dashboard.html
open out/dashboard.html             # macOS
xdg-open out/dashboard.html        # Linux
```

The dashboard includes KPI tiles (merged PRs, lines added/deleted, comments given, weekly velocity),
six Chart.js charts (monthly PRs, stacked line churn, comments per month, PRs by repo, size
distribution, cumulative PRs), and four sortable tables (per-repo breakdown, top PRs by churn,
cycle time, review decisions). Requires an internet connection to load Chart.js from CDN; everything
else is self-contained.

`queries.sql` contains 10 standalone SQL blocks you can paste into any SQLite client:

```bash
sqlite3 -column -header contrib.db < queries.sql
```

## What the numbers mean

- **PR `additions`/`deletions`** come straight from the GitHub PR object — the
  diff size of the merged change, the cleanest "lines added/removed" measure for
  accounting work, and it attributes squashed/co-authored PRs to the PR author.
- This is **not** the same as `git log --author --numstat` totals, which count
  every authored commit (including commits never landed via a PR, and double-counts
  rebased/cherry-picked work). If you want commit-level git attribution as a second
  lens, clone the repo and run
  `git log --author=<you> --no-merges --numstat` — a future `--source git` mode is
  the natural place to fold that in.

## Scaling notes (for the massive repo)

- PR discovery uses the Search API, which caps at 1000 results per query;
  `search_issues` **auto-splits the `created:` date window** recursively when a
  window would exceed that, so total authored-PR count is unbounded.
- Repo-wide comment endpoints (`/issues/comments`, `/pulls/comments`) are walked
  with `since` + pagination — O(new comments) per re-sync, not O(all comments).
- Rate limits (core + the stricter 30/min search budget) are handled with backoff;
  with a token the client watches `X-RateLimit-Remaining` and sleeps to the reset.
- The per-PR line-stat fetch is the main cost on a huge repo (1 call/PR). Use
  `--no-line-stats` for a quick structural pass, then a full pass overnight.

## Known limitation in Claude-Code-on-the-web sessions

This tool could not be fully exercised from the web session it was built in: that
session's GitHub access is **scoped to a single repo**, the git proxy refuses to
clone the others, and `api.github.com` is unreachable directly. Run it where you
have real credentials (your laptop with `gh`, or CI with a token). See
[`FINDINGS.md`](FINDINGS.md) for the partial data that *was* gathered and the
exact access constraints.
