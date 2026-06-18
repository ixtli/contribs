# Findings — first pass (2026-06-17)

Built and run from a Claude-Code-on-the-web session whose GitHub access was
**scoped to `attentive-mobile/a1e-envs` only**. That constrained what could be
gathered here; the numbers below are a *partial* baseline. Re-run `contrib.py`
with full credentials to produce the complete, line-stat-inclusive dataset.

## Access constraints observed in-session

| Capability | Result |
|---|---|
| Org-wide PR **search** (`search_pull_requests`) | Works for repos the token is installed on |
| Repo-scoped reads (`pulls/{n}`, `list_commits`, `get_commit`) | **Denied** for every repo except `a1e-envs` → **no line stats obtainable** |
| `git clone` via session proxy | **Denied** ("repository not authorized") for all sub-repos |
| `api.github.com` direct | **403** (only the MCP proxy can reach it); `gh` CLI not installed |

The session's token (a GitHub App install) could **search 5 of 8** repos.
`a1ec-frontend`, `butler`, and `pontifex` returned a stable `422` — the app is
not installed on them — so no data for those three.

## Partial PR baseline (author `ixtli`, 5 accessible repos)

PR **counts and timing only** — additions/deletions were not reachable here.

| Repo | PRs | Merged | Open | Closed-unmerged | First | Last merge |
|---|--:|--:|--:|--:|---|---|
| a1ec | 195 | 190 | 1 | 4 | 2026-04-14 | 2026-06-17 |
| a1ec-cli | 81 | 81 | 0 | 0 | 2026-04-14 | 2026-06-16 |
| a1ee-terraform | 27 | 27 | 0 | 0 | 2026-04-10 | 2026-06-09 |
| a1e-docker-images | 12 | 12 | 0 | 0 | 2026-04-10 | 2026-06-16 |
| a1e-envs | 33 | 32 | 0 | 1 | 2026-04-10 | 2026-06-15 |
| **Total (accessible)** | **348** | **342** | **1** | **5** | **2026-04-10** | **2026-06-17** |
| a1ec-frontend | — | — | — | — | inaccessible (app not installed) | |
| butler | — | — | — | — | inaccessible (app not installed) | |
| pontifex | — | — | — | — | inaccessible (app not installed) | |

### Merged PRs by month (5 accessible repos)

| Month | a1ec | a1ec-cli | terraform | docker | a1e-envs | **Total** |
|---|--:|--:|--:|--:|--:|--:|
| 2026-04 | 37 | 7 | 12 | 5 | 4 | **65** |
| 2026-05 | 98 | 64 | 10 | 0 | 16 | **188** |
| 2026-06 | 55 | 10 | 5 | 7 | 12 | **89** |

Project work began **2026-04-10**. Not captured this pass: **line counts**
(additions/deletions), **comments/reviews authored**, and the **three
inaccessible repos**.

## To complete the dataset

```bash
# with `gh` authenticated (or GITHUB_TOKEN set) and access to all 8 repos:
./contrib.py sync --include-received
./contrib.py report
./contrib.py export
```
