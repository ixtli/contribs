# contribs

A visualization of my GitHub contributions across the **attentive-mobile**
repositories, built with the [`contrib-dataset`](tools/contrib-dataset/) tool and
deployed as a static site on **Netlify**.

Current scope: pull requests authored by `ixtli` in attentive-mobile repos,
**created this year (since 2026-01-01)** — see
[`tools/contrib-dataset/repos.txt`](tools/contrib-dataset/repos.txt) for the repo list.

## Layout

| Path | What |
|---|---|
| [`tools/contrib-dataset/`](tools/contrib-dataset/) | The data tool: `contrib.py` syncs PRs / comments / reviews from the GitHub API into SQLite; `analyze.py` renders the dashboard. |
| `tools/contrib-dataset/contrib.db` | The committed SQLite dataset. **This is the source of truth that powers the build.** |
| `tools/contrib-dataset/repos.txt` | The attentive-mobile repo list the sync runs over. |
| `public/index.html` | The generated dashboard Netlify serves — KPI tiles, six Chart.js charts, four tables, dataset baked in as inline JSON. (Chart.js loads from a CDN.) |
| `netlify.toml` | Netlify config: rebuilds `public/index.html` from `contrib.db` with `analyze.py`, publishes `public/`. |

## Deploy on Netlify

1. Connect this repo to Netlify (New site → Import from Git).
2. Netlify reads `netlify.toml`: build command runs `analyze.py` against the
   committed `contrib.db` (pure stdlib Python — no token, no network needed),
   writing `public/index.html`; **publish directory is `public`**.
3. Deploy. The dashboard is served at the site root.

A pre-generated `public/index.html` is committed too, so a drag-and-drop deploy of
the `public/` folder (no build step) also works.

> The repo is private, but a Netlify site is public by default. The dashboard
> exposes repo names + PR titles + counts (no comment bodies). Use Netlify's
> password protection if that matters.

## Keep pulling fresh data (manual)

Re-sync with a token, then commit the updated DB — Netlify rebuilds on push:

```bash
cd tools/contrib-dataset
export GH_TOKEN=<a GitHub token with repo read scope>
./contrib.py sync --repos @repos.txt --since 2026-01-01   # incremental; safe to re-run
git commit -am "Refresh contribution data"
git push
```

To regenerate the dashboard locally without deploying:

```bash
./analyze.py --db contrib.db --html ../../public/index.html
```

See [`tools/contrib-dataset/README.md`](tools/contrib-dataset/README.md) for full tool
docs and [`tools/contrib-dataset/FINDINGS.md`](tools/contrib-dataset/FINDINGS.md) for
notes from the original (access-limited) first pass.
