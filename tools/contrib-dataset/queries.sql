-- contrib-dataset: experiment queries
-- Run against contrib.db with: sqlite3 -column -header contrib.db < queries.sql
-- Or copy individual blocks into any SQLite client.
--
-- Assumes you've run: ./contrib.py sync [--include-received]

-- ─────────────────────────────────────────────────────────────────────────────
-- 1. OVERVIEW
-- ─────────────────────────────────────────────────────────────────────────────

-- How much is in the DB right now?
SELECT
    (SELECT COUNT(*) FROM prs)                     AS total_prs,
    (SELECT COUNT(*) FROM prs WHERE merged = 1)    AS merged_prs,
    (SELECT COALESCE(SUM(additions),0) FROM prs)   AS total_additions,
    (SELECT COALESCE(SUM(deletions),0) FROM prs)   AS total_deletions,
    (SELECT COUNT(*) FROM comments)                AS total_comments,
    (SELECT COUNT(*) FROM comments WHERE direction = 'given')    AS comments_given,
    (SELECT COUNT(*) FROM comments WHERE direction = 'received') AS comments_received;

-- ─────────────────────────────────────────────────────────────────────────────
-- 2. MONTHLY PR THROUGHPUT
-- ─────────────────────────────────────────────────────────────────────────────

-- Merged PRs per month, with line churn totals
SELECT
    strftime('%Y-%m', merged_at)                   AS month,
    COUNT(*)                                       AS prs_merged,
    COALESCE(SUM(additions), 0)                    AS lines_added,
    COALESCE(SUM(deletions), 0)                    AS lines_deleted,
    COALESCE(SUM(additions + deletions), 0)        AS total_churn,
    COALESCE(SUM(changed_files), 0)                AS files_touched
FROM prs
WHERE merged = 1
  AND merged_at IS NOT NULL
GROUP BY 1
ORDER BY 1;

-- ─────────────────────────────────────────────────────────────────────────────
-- 3. PER-REPO BREAKDOWN
-- ─────────────────────────────────────────────────────────────────────────────

SELECT
    repo,
    COUNT(*)                                AS total_prs,
    SUM(merged)                             AS merged,
    COALESCE(SUM(additions), 0)             AS additions,
    COALESCE(SUM(deletions), 0)             AS deletions,
    MIN(created_at)                         AS first_pr,
    MAX(COALESCE(merged_at, closed_at))     AS last_activity
FROM prs
GROUP BY repo
ORDER BY merged DESC;

-- ─────────────────────────────────────────────────────────────────────────────
-- 4. COMMENT ACTIVITY
-- ─────────────────────────────────────────────────────────────────────────────

-- Comments authored per month, by kind (feedback you GIVE)
SELECT
    strftime('%Y-%m', created_at)           AS month,
    kind,
    COUNT(*)                                AS count
FROM comments
WHERE direction = 'given'
GROUP BY 1, 2
ORDER BY 1, 2;

-- Review decisions (APPROVED / CHANGES_REQUESTED / COMMENTED) you submitted
SELECT
    state,
    COUNT(*)                                AS count,
    MIN(created_at)                         AS first,
    MAX(created_at)                         AS last
FROM comments
WHERE kind = 'review'
  AND direction = 'given'
  AND state IS NOT NULL
GROUP BY 1
ORDER BY 2 DESC;

-- Repos where you gave the most feedback
SELECT
    repo,
    SUM(CASE WHEN kind = 'issue_comment'   THEN 1 ELSE 0 END) AS issue_comments,
    SUM(CASE WHEN kind = 'review_comment'  THEN 1 ELSE 0 END) AS review_comments,
    SUM(CASE WHEN kind = 'review'          THEN 1 ELSE 0 END) AS reviews,
    COUNT(*)                                                   AS total
FROM comments
WHERE direction = 'given'
GROUP BY repo
ORDER BY total DESC;

-- ─────────────────────────────────────────────────────────────────────────────
-- 5. REVIEW FEEDBACK RECEIVED (requires --include-received)
-- ─────────────────────────────────────────────────────────────────────────────

SELECT
    strftime('%Y-%m', created_at)           AS month,
    kind,
    COUNT(*)                                AS received
FROM comments
WHERE direction = 'received'
GROUP BY 1, 2
ORDER BY 1, 2;

-- ─────────────────────────────────────────────────────────────────────────────
-- 6. PR SIZE DISTRIBUTION
-- ─────────────────────────────────────────────────────────────────────────────

-- Bucket merged PRs by total churn (adds + deletes)
SELECT
    CASE
        WHEN additions + deletions <=  50  THEN '  XS (≤50)'
        WHEN additions + deletions <= 200  THEN '   S (≤200)'
        WHEN additions + deletions <= 500  THEN '   M (≤500)'
        WHEN additions + deletions <= 2000 THEN '   L (≤2000)'
        ELSE                                    '  XL (>2000)'
    END                                     AS size_bucket,
    COUNT(*)                                AS prs,
    ROUND(AVG(additions + deletions))       AS avg_churn
FROM prs
WHERE merged = 1
  AND additions IS NOT NULL
GROUP BY 1
ORDER BY 1;

-- ─────────────────────────────────────────────────────────────────────────────
-- 7. THROUGHPUT OVER TIME (running totals)
-- ─────────────────────────────────────────────────────────────────────────────

-- Cumulative merged PRs and lines added, ordered by merge date
SELECT
    strftime('%Y-%m', merged_at)            AS month,
    COUNT(*)                                AS prs_this_month,
    SUM(COUNT(*)) OVER (ORDER BY strftime('%Y-%m', merged_at)) AS cumulative_prs,
    COALESCE(SUM(additions), 0)             AS additions_this_month,
    SUM(COALESCE(SUM(additions), 0)) OVER (ORDER BY strftime('%Y-%m', merged_at)) AS cumulative_additions
FROM prs
WHERE merged = 1
  AND merged_at IS NOT NULL
GROUP BY 1
ORDER BY 1;

-- ─────────────────────────────────────────────────────────────────────────────
-- 8. VELOCITY METRICS
-- ─────────────────────────────────────────────────────────────────────────────

-- Average days a PR was open before merge (cycle time proxy)
SELECT
    repo,
    COUNT(*)                                                AS merged_prs,
    ROUND(AVG(
        julianday(merged_at) - julianday(created_at)
    ), 1)                                                   AS avg_days_open,
    ROUND(MIN(julianday(merged_at) - julianday(created_at)), 1) AS min_days,
    ROUND(MAX(julianday(merged_at) - julianday(created_at)), 1) AS max_days
FROM prs
WHERE merged = 1
  AND merged_at IS NOT NULL
  AND created_at IS NOT NULL
GROUP BY repo
ORDER BY avg_days_open;

-- Overall summary: PRs per week, lines per week since project start
WITH weeks AS (
    SELECT
        CAST(julianday(merged_at) - julianday(MIN(merged_at) OVER ()) AS INTEGER) / 7 + 1 AS week_num,
        additions, deletions
    FROM prs
    WHERE merged = 1 AND merged_at IS NOT NULL
)
SELECT
    ROUND(AVG(prs_in_week), 1)           AS avg_prs_per_week,
    ROUND(AVG(adds_in_week), 0)          AS avg_additions_per_week,
    ROUND(AVG(dels_in_week), 0)          AS avg_deletions_per_week
FROM (
    SELECT
        week_num,
        COUNT(*)                         AS prs_in_week,
        COALESCE(SUM(additions), 0)      AS adds_in_week,
        COALESCE(SUM(deletions), 0)      AS dels_in_week
    FROM weeks
    GROUP BY week_num
);

-- ─────────────────────────────────────────────────────────────────────────────
-- 9. TOP PRs
-- ─────────────────────────────────────────────────────────────────────────────

-- Largest PRs by churn
SELECT
    repo,
    number,
    title,
    additions,
    deletions,
    additions + deletions               AS churn,
    changed_files,
    strftime('%Y-%m-%d', merged_at)     AS merged
FROM prs
WHERE merged = 1
  AND additions IS NOT NULL
ORDER BY churn DESC
LIMIT 20;

-- Most-discussed PRs (by comment count from GitHub's PR object)
SELECT
    repo,
    number,
    title,
    comments                            AS pr_comments,
    review_comments,
    comments + review_comments          AS total_discussion,
    strftime('%Y-%m-%d', merged_at)     AS merged
FROM prs
WHERE merged = 1
ORDER BY total_discussion DESC
LIMIT 20;

-- ─────────────────────────────────────────────────────────────────────────────
-- 10. SYNC STATE (what's been collected so far)
-- ─────────────────────────────────────────────────────────────────────────────

SELECT repo, accessible, last_error FROM repos ORDER BY repo;

SELECT repo, resource, last_since FROM sync_state ORDER BY repo, resource;
