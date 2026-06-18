# contribs

A visualization of my GitHub contributions across the **a1e** repositories,
built from the [`contrib-dataset`](tools/contrib-dataset/) tool and deployed as a
static site on **Netlify**.

## Layout

| Path | What |
|---|---|
| [`tools/contrib-dataset/`](tools/contrib-dataset/) | The data tool: `contrib.py` syncs PRs / comments / reviews from the GitHub API into a SQLite DB; `analyze.py` renders a self-contained HTML dashboard. |
| `public/index.html` | The generated dashboard Netlify serves. Self-contained — KPI tiles, six Chart.js charts, four tables, with the dataset baked in as inline JSON. (Chart.js loads from a CDN.) |
| `netlify.toml` | Netlify config — no build step, just publishes `public/`. |

## Deploy on Netlify

The dashboard is fully static, so there's nothing to build:

1. Connect this repo to Netlify (New site → Import from Git).
2. Netlify reads `netlify.toml`: **publish directory `public`**, no build command.
3. Deploy. The dashboard is served at the site root.

(Or drag-and-drop the `public/` folder onto the Netlify dashboard for a one-off deploy.)

## Regenerate the data

The committed dashboard is a point-in-time snapshot. To refresh it:

```bash
cd tools/contrib-dataset
export GH_TOKEN=<a GitHub token with repo read scope>
./contrib.py sync --include-received      # incremental; safe to re-run
./analyze.py --html ../../public/index.html
git commit -am "Refresh contribution dashboard"
```

See [`tools/contrib-dataset/README.md`](tools/contrib-dataset/README.md) for the full
tool documentation and [`tools/contrib-dataset/FINDINGS.md`](tools/contrib-dataset/FINDINGS.md)
for notes from the first pass.
