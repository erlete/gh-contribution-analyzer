# Metrics

Honest documentation of what is measured, how, and where the approximations are.

## Volume

Straight counts from ingested history:

- commits (merge commits carry zero line stats)
- lines added, lines deleted, files touched
- pull requests opened and merged
- reviews given

## Churn

Churn measures how quickly written code gets rewritten or deleted.

**Definition**: for each file in a commit, churned lines are

```
churn = min(deletions, lines added to the same path within the trailing 21-day window)
```

The window is 21 days by default and configurable. For renamed files the lookback
also covers the old path.

**Attribution**: churned lines split into self churn and cross churn proportionally
to who added the recent lines. If the commit author's person added 60 percent of
the lines added to that path in the window, 60 percent of the churn is self churn
and the rest is cross churn.

**How it is computed**: purely from stored numstat history (per-file addition and
deletion counts), with no extra git access.

**Limitations, stated plainly**: this is a file-level approximation. It does not
track individual lines, so it cannot know whether the deleted lines are literally
the ones recently added; deleting old code from a file that also received recent
additions still counts as churn up to the recent-addition cap. Treat churn as a
signal of rework pressure on a path, not as an exact line-provenance measure.

## Significance

A per-commit score that rewards meaningful work over raw line counts:

```
significance = log1p(sum over files of class_weight * (additions + 0.5 * deletions))
```

Deletions count at half weight; the logarithm compresses huge commits so a
10,000-line vendor drop cannot dwarf a week of careful work.

### File class weights

Each file is classified by path and extension heuristics (lockfiles, vendor and
build directories, test directories and suffixes, doc extensions, config
extensions):

| Class | Weight |
|---|---|
| code | 1.0 |
| tests | 0.7 |
| config | 0.5 |
| docs | 0.3 |
| generated or vendored | 0.05 |

### Mechanical commits

Commits that look like mass mechanical changes are down-weighted (their mass is
multiplied by 0.2) when either holds:

- total changed lines >= 2000 and additions and deletions are near-symmetric
  (min/max ratio >= 0.8), the shape of reformat and sweep commits
- at least 50 files changed and at least 90 percent of them are pure renames
  (no line changes)

### Merge commits

Merge commits always score zero and carry zero line stats.

PR and review activity is reported as its own dimension and is never folded into
significance.

## Percentiles

For any metric, period and org scope, a person's percentile is their
`percent_rank()` among the active population: every person with any activity in
that same period and scope. Percentiles are computed at query time, not stored, so
they always reflect the current data and the selected scope.

## Rollup architecture

`person_repo_day_stats` is the single aggregation table dashboards and reports
read. One row per person, repo and day, holding commit count, additions,
deletions, churn and its self/cross split, significance sum, PRs opened and
merged, and reviews.

- **Incremental**: during ingest, deltas are upserted additively per
  (person, repo, day), so syncs only touch the days they changed.
- **Recompute on merge and unmerge**: when persons are merged or unmerged, the
  affected persons' rollup rows are deleted and rebuilt from the base tables
  (commits, pull requests, reviews), so history is always attributed to the
  current identity mapping.
