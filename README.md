# contribs

A visualization of my GitHub contributions across the **attentive-mobile**
repositories, deployed as a static site on **GitHub Pages**.

The dashboard is a single-page app that loads **SQLite in the browser via WASM**
([sql.js](https://sql.js.org/)) and queries the committed dataset directly —
no build step, no server, no API token at view time. The data is collected by the
[`contrib-dataset`](tools/contrib-dataset/) tool.

Current scope: pull requests authored by `ixtli` in attentive-mobile repos,
**created this year (since 2026-01-01)** — see
[`tools/contrib-dataset/repos.txt`](tools/contrib-dataset/repos.txt) for the repo list.
The dataset is **metadata only** — repo, PR number, state, timestamps, line/comment/review
counts and decisions. No PR titles, comment/review bodies, or file paths are stored, so
the dashboard is safe to serve publicly.

## Layout

| Path | What |
|---|---|
| [`public/index.html`](public/index.html) | The dashboard SPA. Loads sql.js + Chart.js from a CDN, `fetch()`es `contrib.db`, runs the aggregate SQL client-side, and renders KPIs, six charts, and four tables. |
| `tools/contrib-dataset/contrib.db` | The committed SQLite dataset the SPA queries. **Source of truth.** |
| [`tools/contrib-dataset/`](tools/contrib-dataset/) | The data tool: `contrib.py` syncs PRs / comments / reviews from the GitHub API into SQLite; `analyze.py` is an offline CLI report (and can emit a self-contained HTML dashboard). |
| `tools/contrib-dataset/repos.txt` | The attentive-mobile repo list the sync runs over. |
| [`.github/workflows/pages.yml`](.github/workflows/pages.yml) | Assembles the SPA + `contrib.db` and deploys to GitHub Pages. |

## Deploy on GitHub Pages

1. In the repo: **Settings → Pages → Build and deployment → Source: GitHub Actions**.
2. The [`pages.yml`](.github/workflows/pages.yml) workflow runs on every push to `main`
   (and can be run manually via **Actions → Deploy dashboard to GitHub Pages → Run workflow**).
   It copies `public/index.html` and `tools/contrib-dataset/contrib.db` into the published
   site and deploys — no Python, no token, no network at deploy time.
3. The dashboard is served at the Pages URL.

> Pages on a private repo requires a paid GitHub plan, and the published site is public.
> That's fine here — the dataset contains no titles/bodies/paths, only counts and trends.

## Preview locally

The SPA needs `contrib.db` served next to it over HTTP (a `file://` open won't fetch the DB):

```bash
cp tools/contrib-dataset/contrib.db public/
python3 -m http.server -d public 8000   # then open http://localhost:8000
```

## Refresh the data (manual)

Re-sync with a token, then commit the updated DB — the push redeploys Pages:

```bash
cd tools/contrib-dataset
export GH_TOKEN=<a GitHub token with repo read scope>
./contrib.py sync --repos @repos.txt --since 2026-01-01   # incremental; safe to re-run
git commit -am "Refresh contribution data" && git push
```

To see the numbers in the terminal (no browser): `./analyze.py --db contrib.db`.

See [`tools/contrib-dataset/README.md`](tools/contrib-dataset/README.md) for full tool
docs and [`tools/contrib-dataset/FINDINGS.md`](tools/contrib-dataset/FINDINGS.md) for
notes from the original (access-limited) first pass.
