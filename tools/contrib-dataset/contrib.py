#!/usr/bin/env python3
"""contrib.py — assemble a personal contribution dataset across GitHub repos.

Captures, per repo and per author:
  * pull requests authored (state, merged, timestamps, line stats, change size)
  * comments authored (issue comments + PR review comments) — the feedback you GIVE
  * reviews submitted (APPROVED / CHANGES_REQUESTED / COMMENTED — decision only)
  * optionally, comments RECEIVED on your PRs (feedback others give you)

Design goals
------------
* Re-runnable: data lands in a SQLite DB via UPSERT, so re-runs are incremental
  and idempotent. A per-(repo, resource) high-water-mark in `sync_state` means
  each run only fetches what changed since the last run.
* Scales to massive repos: repo-wide comment endpoints are walked with `since`
  + page pagination; PR search auto-splits date windows when it would exceed the
  Search API's 1000-result ceiling.
* No third-party deps: standard library only. Auth via the `gh` CLI if present,
  else a token in $GITHUB_TOKEN / $GH_TOKEN.

Usage
-----
  # one-time / recurring full sync of the default a1e repo set for `ixtli`
  ./contrib.py sync

  # a single massive repo, only data updated since a date, skipping nothing
  ./contrib.py sync --repos attentive-mobile/<bigrepo> --since 2023-01-01

  # faster sync that skips the per-PR line-stat fetch (1 call per PR)
  ./contrib.py sync --no-line-stats

  # also capture comments other people left on your PRs
  ./contrib.py sync --include-received

  # export tidy CSVs + a summary.json for experiments
  ./contrib.py export

  # print a human-readable summary
  ./contrib.py report

Run `./contrib.py <command> --help` for the full flag list.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict

API = "https://api.github.com"

# Default a1e repo set (the super-repo + its sub-repos). Override with --repos.
DEFAULT_REPOS = [
    "attentive-mobile/a1e-envs",
    "attentive-mobile/a1ec",
    "attentive-mobile/a1ec-cli",
    "attentive-mobile/a1ec-frontend",
    "attentive-mobile/a1ee-terraform",
    "attentive-mobile/a1e-docker-images",
    "attentive-mobile/butler",
    "attentive-mobile/pontifex",
]
DEFAULT_AUTHORS = ["ixtli"]


# --------------------------------------------------------------------------- #
# GitHub client (gh CLI preferred, token fallback)                            #
# --------------------------------------------------------------------------- #
class Inaccessible(Exception):
    """Repo/resource cannot be read by the current credential (404/422)."""


class GitHub:
    def __init__(self, verbose: bool = False):
        self.verbose = verbose
        self.use_gh = shutil.which("gh") is not None and self._gh_authed()
        self.token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
        if not self.use_gh and not self.token:
            sys.exit(
                "No GitHub credential found. Either install & `gh auth login`, "
                "or set GITHUB_TOKEN / GH_TOKEN."
            )
        self._log(f"auth: {'gh CLI' if self.use_gh else 'token'}")

    @staticmethod
    def _gh_authed() -> bool:
        try:
            r = subprocess.run(
                ["gh", "auth", "status"], capture_output=True, text=True
            )
            return r.returncode == 0
        except Exception:
            return False

    def _log(self, msg: str) -> None:
        if self.verbose:
            print(f"  [gh] {msg}", file=sys.stderr)

    def get(self, path: str, params: dict | None = None) -> object:
        """GET a single API path. `path` may be absolute or relative to API root."""
        url = path if path.startswith("http") else f"{API}/{path.lstrip('/')}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        return self._request(url)

    def _request(self, url: str, _tries: int = 0) -> object:
        if self.use_gh:
            return self._request_gh(url, _tries)
        return self._request_token(url, _tries)

    def _request_gh(self, url: str, tries: int) -> object:
        path = url[len(API):] if url.startswith(API) else url
        proc = subprocess.run(
            ["gh", "api", "-H", "Accept: application/vnd.github+json", path],
            capture_output=True,
            text=True,
        )
        if proc.returncode == 0:
            return json.loads(proc.stdout) if proc.stdout.strip() else {}
        err = proc.stderr.lower()
        if "422" in err or "404" in err or "not found" in err:
            raise Inaccessible(proc.stderr.strip())
        if ("rate limit" in err or "secondary" in err or "403" in err) and tries < 6:
            wait = 2 ** (tries + 2)
            self._log(f"rate-limited, sleeping {wait}s")
            time.sleep(wait)
            return self._request_gh(url, tries + 1)
        raise RuntimeError(f"gh api failed for {path}: {proc.stderr.strip()}")

    def _request_token(self, url: str, tries: int) -> object:
        req = urllib.request.Request(url)
        req.add_header("Authorization", f"Bearer {self.token}")
        req.add_header("Accept", "application/vnd.github+json")
        req.add_header("User-Agent", "contrib-dataset")
        try:
            with urllib.request.urlopen(req) as resp:
                remaining = resp.headers.get("X-RateLimit-Remaining")
                if remaining is not None and int(remaining) < 3:
                    reset = int(resp.headers.get("X-RateLimit-Reset", "0"))
                    wait = max(0, reset - int(time.time())) + 1
                    self._log(f"rate budget low, sleeping {wait}s")
                    time.sleep(min(wait, 90))
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            if e.code in (404, 422):
                raise Inaccessible(f"HTTP {e.code} for {url}")
            if e.code in (403, 429) and tries < 6:
                reset = int(e.headers.get("X-RateLimit-Reset", "0"))
                wait = max(2 ** (tries + 2), reset - int(time.time()) + 1)
                self._log(f"HTTP {e.code}, sleeping {min(wait, 120)}s")
                time.sleep(min(wait, 120))
                return self._request_token(url, tries + 1)
            raise RuntimeError(f"HTTP {e.code} for {url}: {e.read().decode()[:200]}")

    def paginate(self, path: str, params: dict | None = None):
        """Yield items across pages (page-based; stops on a short page)."""
        params = dict(params or {})
        params.setdefault("per_page", 100)
        page = 1
        while True:
            params["page"] = page
            batch = self.get(path, params)
            if not isinstance(batch, list):
                raise RuntimeError(f"expected a list from {path}, got {type(batch)}")
            yield from batch
            if len(batch) < params["per_page"]:
                return
            page += 1

    def search_issues(self, query: str):
        """Yield search hits, auto-splitting date windows past the 1000 cap.

        The query must already contain a `created:LO..HI` range; we recurse by
        halving that window whenever total_count would exceed what pagination
        can return (1000).
        """
        first = self.get("search/issues", {"q": query, "per_page": 1, "page": 1})
        total = first.get("total_count", 0)
        if total <= 1000:
            yield from self._search_window(query)
            return
        lo, hi = _extract_created_range(query)
        if (hi - lo).days <= 1:
            # Can't split further; take the first 1000 and warn.
            print(f"  ! >1000 results in a 1-day window; truncating: {query}",
                  file=sys.stderr)
            yield from self._search_window(query)
            return
        mid = lo + (hi - lo) / 2
        base = _strip_created_range(query)
        yield from self.search_issues(
            f"{base} created:{lo.date()}..{mid.date()}")
        yield from self.search_issues(
            f"{base} created:{(mid + dt.timedelta(days=1)).date()}..{hi.date()}")

    def _search_window(self, query: str):
        page = 1
        while True:
            res = self.get(
                "search/issues",
                {"q": query, "per_page": 100, "page": page,
                 "sort": "created", "order": "asc"},
            )
            items = res.get("items", [])
            yield from items
            if len(items) < 100 or page * 100 >= res.get("total_count", 0):
                return
            page += 1


def _extract_created_range(query: str):
    for tok in query.split():
        if tok.startswith("created:") and ".." in tok:
            lo, hi = tok[len("created:"):].split("..")
            return _parse_day(lo), _parse_day(hi)
    raise ValueError(f"no created: range in query: {query}")


def _strip_created_range(query: str) -> str:
    return " ".join(t for t in query.split() if not t.startswith("created:"))


def _parse_day(s: str) -> dt.datetime:
    return dt.datetime.strptime(s, "%Y-%m-%d")


# --------------------------------------------------------------------------- #
# SQLite store                                                                #
# --------------------------------------------------------------------------- #
SCHEMA = """
CREATE TABLE IF NOT EXISTS repos (
  repo TEXT PRIMARY KEY,
  accessible INTEGER,
  note TEXT,
  last_checked TEXT
);
CREATE TABLE IF NOT EXISTS prs (
  repo TEXT, number INTEGER, state TEXT, draft INTEGER,
  merged INTEGER, author TEXT, created_at TEXT, updated_at TEXT,
  merged_at TEXT, closed_at TEXT,
  additions INTEGER, deletions INTEGER, changed_files INTEGER,
  comments INTEGER, review_comments INTEGER, url TEXT,
  PRIMARY KEY (repo, number)
);
-- Behavioral metadata only: no titles, comment/review bodies, or file paths
-- are recorded — just how much, when, and what kind of activity.
CREATE TABLE IF NOT EXISTS comments (
  kind TEXT,            -- issue_comment | review_comment | review
  id INTEGER,           -- GitHub comment/review id
  repo TEXT, pr_number INTEGER,
  author TEXT,          -- comment author
  target_author TEXT,   -- author of the PR the comment is on (if known)
  direction TEXT,       -- given | received
  state TEXT,           -- state used for reviews (APPROVED/...)
  in_reply_to INTEGER,
  created_at TEXT, updated_at TEXT, url TEXT,
  PRIMARY KEY (kind, id)
);
CREATE TABLE IF NOT EXISTS sync_state (
  repo TEXT, resource TEXT, last_since TEXT,
  PRIMARY KEY (repo, resource)
);
CREATE INDEX IF NOT EXISTS idx_comments_repo_author ON comments(repo, author);
CREATE INDEX IF NOT EXISTS idx_prs_author ON prs(author);
"""


def db_open(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    return conn


def upsert(conn, table, row: dict, pk: list[str]) -> None:
    cols = list(row)
    placeholders = ",".join("?" for _ in cols)
    updates = ",".join(f"{c}=excluded.{c}" for c in cols if c not in pk)
    sql = (
        f"INSERT INTO {table} ({','.join(cols)}) VALUES ({placeholders}) "
        f"ON CONFLICT ({','.join(pk)}) DO UPDATE SET {updates}"
    )
    conn.execute(sql, [row[c] for c in cols])


def get_since(conn, repo, resource, default):
    row = conn.execute(
        "SELECT last_since FROM sync_state WHERE repo=? AND resource=?",
        (repo, resource),
    ).fetchone()
    return row[0] if row and row[0] else default


def set_since(conn, repo, resource, value):
    upsert(conn, "sync_state",
           {"repo": repo, "resource": resource, "last_since": value},
           ["repo", "resource"])


# --------------------------------------------------------------------------- #
# Sync                                                                        #
# --------------------------------------------------------------------------- #
def is_pr_comment(c: dict) -> bool:
    """Issue-comment endpoint mixes PR & issue comments; PRs use /pull/ in html_url."""
    return "/pull/" in (c.get("html_url") or "")


def pr_number_from_url(url: str) -> int | None:
    # .../pulls/123  or  .../issues/123
    try:
        return int(url.rstrip("/").split("/")[-1])
    except (ValueError, AttributeError):
        return None


def sync(args) -> None:
    gh = GitHub(verbose=args.verbose)
    conn = db_open(args.db)
    authors = {a.lower() for a in args.authors}
    now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    resources = set(args.resources.split(","))

    for repo in args.repos:
        owner, name = repo.split("/")
        print(f"== {repo} ==")
        try:
            # cheap reachability probe
            gh.get(f"repos/{repo}")
        except Inaccessible as e:
            print(f"  inaccessible: {e}")
            upsert(conn, "repos",
                   {"repo": repo, "accessible": 0, "note": str(e)[:200],
                    "last_checked": now}, ["repo"])
            conn.commit()
            continue
        upsert(conn, "repos",
               {"repo": repo, "accessible": 1, "note": "", "last_checked": now},
               ["repo"])

        if "prs" in resources:
            _sync_prs(gh, conn, repo, owner, name, authors, args)
        if "comments" in resources:
            _sync_comments(gh, conn, repo, owner, name, authors, args)
        if "reviews" in resources:
            _sync_reviews(gh, conn, repo, authors, args)
        conn.commit()

    conn.commit()
    conn.close()
    print(f"\nSynced into {args.db}. Run `export` or `report` next.")


def _sync_prs(gh, conn, repo, owner, name, authors, args):
    count = 0
    for author in authors:
        since = get_since(conn, repo, f"prs:{author}", args.since)
        q = (f"repo:{repo} is:pr author:{author} "
             f"created:{since}..{dt.date.today()}")
        for hit in gh.search_issues(q):
            number = hit["number"]
            row = {
                "repo": repo, "number": number,
                "state": hit["state"], "draft": int(hit.get("draft", False)),
                "merged": int(bool((hit.get("pull_request") or {}).get("merged_at"))),
                "author": hit["user"]["login"],
                "created_at": hit["created_at"], "updated_at": hit["updated_at"],
                "merged_at": (hit.get("pull_request") or {}).get("merged_at"),
                "closed_at": hit.get("closed_at"),
                "additions": None, "deletions": None, "changed_files": None,
                "comments": hit.get("comments"), "review_comments": None,
                "url": hit["html_url"],
            }
            if not args.no_line_stats:
                try:
                    d = gh.get(f"repos/{repo}/pulls/{number}")
                    row.update(
                        merged=int(bool(d.get("merged_at"))),
                        merged_at=d.get("merged_at"),
                        additions=d.get("additions"),
                        deletions=d.get("deletions"),
                        changed_files=d.get("changed_files"),
                        review_comments=d.get("review_comments"),
                    )
                except Inaccessible:
                    pass
            upsert(conn, "prs", row, ["repo", "number"])
            count += 1
        set_since(conn, repo, f"prs:{author}", str(dt.date.today()))
    print(f"  prs: upserted {count}")


def _sync_comments(gh, conn, repo, owner, name, authors, args):
    """Repo-wide issue + review comments authored by our authors (feedback given).

    Big/monorepo comment endpoints cap deep pagination (HTTP 422 "pagination is
    limited for this resource"). We page within a window sorted by `updated asc`;
    when the cap is hit, we advance the `since` cursor to the newest comment seen
    and resume from page 1 — so we never page too deep, yet still walk the whole
    history. UPSERT-by-id makes the overlapping window boundary idempotent. A
    genuinely unreadable endpoint (window never advances) is skipped, not fatal.
    """
    given = 0

    def handle(c, kind):
        nonlocal given
        if (c.get("user") or {}).get("login", "").lower() not in authors:
            return
        if kind == "issue_comment":
            if not is_pr_comment(c):
                return
            num = pr_number_from_url(c.get("html_url", "").split("#")[0])
            reply = None
        else:
            num = pr_number_from_url(c.get("pull_request_url", ""))
            reply = c.get("in_reply_to_id")
        upsert(conn, "comments", {
            "kind": kind, "id": c["id"], "repo": repo, "pr_number": num,
            "author": c["user"]["login"], "target_author": None,
            "direction": "given", "state": None,
            "in_reply_to": reply,
            "created_at": c.get("created_at"), "updated_at": c.get("updated_at"),
            "url": c.get("html_url"),
        }, ["kind", "id"])
        given += 1

    for endpoint, kind in (("issues/comments", "issue_comment"),
                           ("pulls/comments", "review_comment")):
        cursor = _iso(get_since(conn, repo, f"comments:{kind}", args.since))
        newest = cursor
        while True:
            window_newest = cursor
            capped = False
            page = 1
            try:
                while True:
                    batch = gh.get(f"repos/{repo}/{endpoint}",
                                   {"since": cursor, "sort": "updated",
                                    "direction": "asc", "per_page": 100, "page": page})
                    if not isinstance(batch, list):
                        raise RuntimeError(
                            f"expected list from {endpoint}, got {type(batch)}")
                    for c in batch:
                        u = c.get("updated_at") or ""
                        if u > window_newest:
                            window_newest = u
                        if u > newest:
                            newest = u
                        handle(c, kind)
                    if len(batch) < 100:
                        break
                    page += 1
            except Inaccessible:
                capped = True
            if capped and window_newest > cursor:
                cursor = window_newest      # slide window forward; depth resets
                continue
            break
        if newest:
            set_since(conn, repo, f"comments:{kind}", newest[:10])
    print(f"  comments (given): upserted {given}")

    if args.include_received:
        _sync_received(gh, conn, repo, authors, args)


def _sync_received(gh, conn, repo, authors, args):
    """Comments OTHERS left on PRs authored by our authors (feedback received)."""
    received = 0
    for author in authors:
        q = f"repo:{repo} is:pr author:{author} created:{args.since}..{dt.date.today()}"
        for hit in gh.search_issues(q):
            num = hit["number"]
            for endpoint, kind in ((f"issues/{num}/comments", "issue_comment"),
                                   (f"pulls/{num}/comments", "review_comment")):
                try:
                    for c in gh.paginate(f"repos/{repo}/{endpoint}"):
                        if (c.get("user") or {}).get("login", "").lower() in authors:
                            continue  # skip self-comments; those are 'given'
                        upsert(conn, "comments", {
                            "kind": kind, "id": c["id"], "repo": repo,
                            "pr_number": num, "author": c["user"]["login"],
                            "target_author": hit["user"]["login"],
                            "direction": "received", "state": None,
                            "in_reply_to": c.get("in_reply_to_id"),
                            "created_at": c.get("created_at"),
                            "updated_at": c.get("updated_at"),
                            "url": c.get("html_url"),
                        }, ["kind", "id"])
                        received += 1
                except Inaccessible:
                    pass
    print(f"  comments (received): upserted {received}")


def _sync_reviews(gh, conn, repo, authors, args):
    """PR reviews (approve/request-changes/comment summaries) submitted by authors."""
    count = 0
    for author in authors:
        q = (f"repo:{repo} is:pr reviewed-by:{author} "
             f"created:{args.since}..{dt.date.today()}")
        for hit in gh.search_issues(q):
            num = hit["number"]
            try:
                for r in gh.paginate(f"repos/{repo}/pulls/{num}/reviews"):
                    if (r.get("user") or {}).get("login", "").lower() not in authors:
                        continue
                    upsert(conn, "comments", {
                        "kind": "review", "id": r["id"], "repo": repo,
                        "pr_number": num, "author": r["user"]["login"],
                        "target_author": hit["user"]["login"], "direction": "given",
                        "state": r.get("state"),
                        "in_reply_to": None,
                        "created_at": r.get("submitted_at"),
                        "updated_at": r.get("submitted_at"),
                        "url": r.get("html_url"),
                    }, ["kind", "id"])
                    count += 1
            except Inaccessible:
                pass
    print(f"  reviews: upserted {count}")


def _iso(day: str) -> str:
    return f"{day}T00:00:00Z" if len(day) == 10 else day


# --------------------------------------------------------------------------- #
# Export & report                                                             #
# --------------------------------------------------------------------------- #
def export(args) -> None:
    conn = db_open(args.db)
    os.makedirs(args.out, exist_ok=True)
    _dump_csv(conn, "prs", os.path.join(args.out, "prs.csv"))
    _dump_csv(conn, "comments", os.path.join(args.out, "comments.csv"))
    summary = _summary(conn)
    with open(os.path.join(args.out, "summary.json"), "w") as f:
        json.dump(summary, f, indent=2)
    print(f"Wrote prs.csv, comments.csv, summary.json to {args.out}/")
    conn.close()


def _dump_csv(conn, table, path) -> None:
    cur = conn.execute(f"SELECT * FROM {table}")
    cols = [d[0] for d in cur.description]
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(cols)
        w.writerows(cur.fetchall())


def _summary(conn) -> dict:
    out = {"generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
           "repos": {}, "totals": {}, "monthly": {}}
    repo_rows = conn.execute("""
        SELECT repo,
               COUNT(*),
               SUM(merged),
               SUM(state='open'),
               SUM(state='closed' AND merged=0),
               COALESCE(SUM(additions),0), COALESCE(SUM(deletions),0),
               MIN(created_at), MAX(COALESCE(merged_at, created_at))
        FROM prs GROUP BY repo""").fetchall()
    t_pr = t_merged = t_add = t_del = 0
    for (repo, n, merged, opn, closed_unmerged, add, dele, first, last) in repo_rows:
        out["repos"][repo] = {
            "prs_total": n, "prs_merged": merged or 0, "prs_open": opn or 0,
            "prs_closed_unmerged": closed_unmerged or 0,
            "additions": add, "deletions": dele,
            "first_pr": first, "last_pr": last,
        }
        t_pr += n
        t_merged += merged or 0
        t_add += add
        t_del += dele
    # comments by direction & kind
    cmt = conn.execute(
        "SELECT direction, kind, COUNT(*) FROM comments GROUP BY direction, kind"
    ).fetchall()
    comment_counts = {f"{d}_{k}": c for (d, k, c) in cmt}
    out["totals"] = {
        "prs": t_pr, "prs_merged": t_merged,
        "additions": t_add, "deletions": t_del,
        "net_lines": t_add - t_del,
        "comments_total": sum(c for *_x, c in cmt),
        **comment_counts,
    }
    # monthly histogram of merged PRs + lines
    monthly = defaultdict(lambda: {"prs_merged": 0, "additions": 0, "deletions": 0})
    for (m, n, add, dele) in conn.execute("""
        SELECT substr(merged_at,1,7), COUNT(*),
               COALESCE(SUM(additions),0), COALESCE(SUM(deletions),0)
        FROM prs WHERE merged=1 AND merged_at IS NOT NULL
        GROUP BY substr(merged_at,1,7)""").fetchall():
        monthly[m] = {"prs_merged": n, "additions": add, "deletions": dele}
    for (m, c) in conn.execute("""
        SELECT substr(created_at,1,7), COUNT(*) FROM comments
        WHERE direction='given' GROUP BY substr(created_at,1,7)""").fetchall():
        monthly[m]["comments_given"] = c
    out["monthly"] = dict(sorted(monthly.items()))
    return out


def report(args) -> None:
    conn = db_open(args.db)
    s = _summary(conn)
    t = s["totals"]
    print("Contribution summary")
    print("=" * 60)
    print(f"PRs (total/merged):   {t['prs']} / {t['prs_merged']}")
    print(f"Lines (+/-/net):      +{t['additions']} / -{t['deletions']} "
          f"/ {t['net_lines']:+}")
    print(f"Comments (total):     {t['comments_total']}")
    for k in sorted(k for k in t if k.endswith('_comment') or k.endswith('_review')):
        print(f"    {k:28} {t[k]}")
    print("\nPer repo:")
    for repo, r in s["repos"].items():
        print(f"  {repo:34} PRs {r['prs_total']:>4}  merged {r['prs_merged']:>4}  "
              f"+{r['additions']:>7} -{r['deletions']:>7}  "
              f"[{r['first_pr'] or '?'} .. {r['last_pr'] or '?'}]")
    inacc = conn.execute(
        "SELECT repo, note FROM repos WHERE accessible=0").fetchall()
    if inacc:
        print("\nInaccessible repos (no credential access):")
        for repo, note in inacc:
            print(f"  {repo}: {note}")
    conn.close()


# --------------------------------------------------------------------------- #
# CLI                                                                         #
# --------------------------------------------------------------------------- #
def main() -> None:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp):
        sp.add_argument("--db", default="contrib.db", help="SQLite DB path")

    sp = sub.add_parser("sync", help="fetch/refresh data into the DB (incremental)")
    common(sp)
    sp.add_argument("--repos", default=",".join(DEFAULT_REPOS),
                    help="comma-separated owner/name list, or @file")
    sp.add_argument("--authors", default=",".join(DEFAULT_AUTHORS),
                    help="comma-separated GitHub logins")
    sp.add_argument("--since", default="2020-01-01",
                    help="earliest date to consider on first sync (YYYY-MM-DD)")
    sp.add_argument("--resources", default="prs,comments,reviews",
                    help="which of prs,comments,reviews to sync")
    sp.add_argument("--no-line-stats", action="store_true",
                    help="skip per-PR additions/deletions fetch (1 call/PR)")
    sp.add_argument("--include-received", action="store_true",
                    help="also capture comments others left on your PRs")
    sp.add_argument("--verbose", action="store_true")
    sp.set_defaults(func=sync)

    sp = sub.add_parser("export", help="write CSVs + summary.json")
    common(sp)
    sp.add_argument("--out", default="out", help="output directory")
    sp.set_defaults(func=export)

    sp = sub.add_parser("report", help="print a human-readable summary")
    common(sp)
    sp.set_defaults(func=report)

    args = p.parse_args()
    if getattr(args, "repos", None):
        if args.repos.startswith("@"):
            with open(args.repos[1:]) as f:
                args.repos = [ln.strip() for ln in f if ln.strip()
                              and not ln.startswith("#")]
        else:
            args.repos = [r.strip() for r in args.repos.split(",") if r.strip()]
    if getattr(args, "authors", None):
        args.authors = [a.strip() for a in args.authors.split(",") if a.strip()]
    args.func(args)


if __name__ == "__main__":
    main()
