"""Wide-picture context builders for insight generation.

One builder per insight area. The returned dicts are the single source both
for the AI prompt and for the deterministic fallback statements, so the two
always describe the same numbers. Keys are stable on purpose: they feed the
insight cache key.

Every context carries the current period, the previous period of equal
length, deltas between the two, a weekly activity arc, and population
dynamics (who arrived, who went quiet, how concentrated the work is), so
the model can evaluate rather than paraphrase.
"""

from datetime import date, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from gca.services import stats
from gca.services.stats import DayPoint, PersonStat, Totals


def _round_ratio(value: float) -> float:
    return round(value, 3)


def previous_range(start: date, end: date) -> tuple[date, date]:
    """The window of equal length immediately before [start, end]."""
    span = end - start
    prev_end = start - timedelta(days=1)
    return prev_end - span, prev_end


def _pct_change(current: float, previous: float) -> float | None:
    """Percent change vs the previous period, None when there is no base."""
    if not previous:
        return None
    return round((current - previous) / previous * 100, 1)


def _weekly(points: list[DayPoint]) -> list[dict[str, object]]:
    """Aggregate the day series into ISO weeks for a compact trend arc."""
    commits: dict[str, int] = {}
    significance: dict[str, float] = {}
    for point in points:
        iso = point.day.isocalendar()
        key = f"{iso.year}-W{iso.week:02d}"
        commits[key] = commits.get(key, 0) + point.commits
        significance[key] = significance.get(key, 0.0) + point.significance
    return [
        {
            "week": key,
            "commits": commits[key],
            "significance": round(significance[key], 1),
        }
        for key in sorted(commits)
    ]


def _totals_dict(totals: Totals, *, with_repos: bool = True) -> dict[str, object]:
    data: dict[str, object] = {
        "commits": totals.commits,
        "additions": totals.additions,
        "deletions": totals.deletions,
        "churn_lines": totals.churn,
        "churn_ratio": _round_ratio(totals.churn_ratio),
        "significance": round(totals.significance, 1),
        "prs_opened": totals.prs_opened,
        "prs_merged": totals.prs_merged,
        "reviews": totals.reviews,
        "active_people": totals.active_people,
    }
    if with_repos:
        data["active_repos"] = totals.active_repos
    return data


def _totals_deltas(current: Totals, previous: Totals) -> dict[str, object]:
    return {
        "commits_pct": _pct_change(current.commits, previous.commits),
        "significance_pct": _pct_change(current.significance, previous.significance),
        "prs_merged_pct": _pct_change(current.prs_merged, previous.prs_merged),
        "reviews_pct": _pct_change(current.reviews, previous.reviews),
        "active_people_change": current.active_people - previous.active_people,
    }


def _turnover(
    current: list[PersonStat], previous: list[PersonStat]
) -> dict[str, object]:
    """Who is active now but was not before, and the reverse."""
    now = {s.person_id: s.display_name for s in current}
    before = {s.person_id: s.display_name for s in previous}
    arrived = [name for pid, name in now.items() if pid not in before]
    departed = [name for pid, name in before.items() if pid not in now]
    return {
        "arrived_count": len(arrived),
        "arrived": sorted(arrived)[:5],
        "departed_count": len(departed),
        "departed": sorted(departed)[:5],
    }


def _concentration(board: list[PersonStat]) -> float | None:
    """Share of total significance carried by the top three people."""
    total = sum(s.significance for s in board)
    if not total:
        return None
    return _round_ratio(sum(s.significance for s in board[:3]) / total)


async def dashboard_context(
    session: AsyncSession,
    *,
    orgs: list[int],
    start: date,
    end: date,
    orgs_label: str,
    period_label: str,
    repo_ids: list[int] | None = None,
    person_ids: list[int] | None = None,
) -> dict[str, object]:
    prev_start, prev_end = previous_range(start, end)
    totals = await stats.totals(
        session,
        orgs=orgs,
        start=start,
        end=end,
        repo_ids=repo_ids,
        person_ids=person_ids,
    )
    prev_totals = await stats.totals(
        session,
        orgs=orgs,
        start=prev_start,
        end=prev_end,
        repo_ids=repo_ids,
        person_ids=person_ids,
    )
    people = await stats.person_leaderboard(
        session,
        orgs=orgs,
        start=start,
        end=end,
        repo_ids=repo_ids,
        person_ids=person_ids,
    )
    prev_people = await stats.person_leaderboard(
        session,
        orgs=orgs,
        start=prev_start,
        end=prev_end,
        repo_ids=repo_ids,
        person_ids=person_ids,
    )
    repos = await stats.repo_leaderboard(
        session,
        orgs=orgs,
        start=start,
        end=end,
        repo_ids=repo_ids,
        person_ids=person_ids,
    )
    series = await stats.timeseries(
        session,
        orgs=orgs,
        start=start,
        end=end,
        repo_ids=repo_ids,
        person_ids=person_ids,
    )
    return {
        "period": period_label,
        "previous_period": f"{prev_start.isoformat()} to {prev_end.isoformat()}",
        "orgs": orgs_label,
        "totals": _totals_dict(totals),
        "previous_totals": _totals_dict(prev_totals),
        "deltas_vs_previous": _totals_deltas(totals, prev_totals),
        "weekly_trend": _weekly(series),
        "top3_significance_share": _concentration(people),
        "people_turnover": _turnover(people, prev_people),
        "top_contributors": [
            {
                "name": p.display_name,
                "commits": p.commits,
                "significance": round(p.significance, 1),
                "churn_ratio": _round_ratio(p.churn_ratio),
                "reviews": p.reviews,
            }
            for p in people[:8]
        ],
        "top_repos": [
            {
                "name": f"{r.org_login}/{r.name}",
                "commits": r.commits,
                "significance": round(r.significance, 1),
                "prs_merged": r.prs_merged,
                "contributors": r.contributors,
            }
            for r in repos[:5]
        ],
    }


async def person_context(
    session: AsyncSession,
    *,
    person_id: int,
    display_name: str,
    orgs: list[int],
    start: date,
    end: date,
    orgs_label: str,
    period_label: str,
) -> dict[str, object]:
    prev_start, prev_end = previous_range(start, end)
    board = await stats.person_leaderboard(session, orgs=orgs, start=start, end=end)
    prev_board = await stats.person_leaderboard(
        session, orgs=orgs, start=prev_start, end=prev_end
    )
    me = next((s for s in board if s.person_id == person_id), None)
    prev_me = next((s for s in prev_board if s.person_id == person_id), None)
    rank = next((i + 1 for i, s in enumerate(board) if s.person_id == person_id), None)
    prev_rank = next(
        (i + 1 for i, s in enumerate(prev_board) if s.person_id == person_id), None
    )
    split = await stats.person_repo_split(
        session, person_id=person_id, orgs=orgs, start=start, end=end
    )
    series = await stats.timeseries(
        session, orgs=orgs, start=start, end=end, person_ids=[person_id]
    )
    total_significance = sum(s.significance for s in board)
    commits_now = me.commits if me else 0
    commits_prev = prev_me.commits if prev_me else 0
    significance_now = me.significance if me else 0.0
    significance_prev = prev_me.significance if prev_me else 0.0
    reviews_now = me.reviews if me else 0
    reviews_prev = prev_me.reviews if prev_me else 0
    metrics = {
        "commits": commits_now,
        "additions": me.additions if me else 0,
        "deletions": me.deletions if me else 0,
        "churn_lines": me.churn if me else 0,
        "churn_ratio": _round_ratio(me.churn_ratio) if me else 0.0,
        "self_churn": me.self_churn if me else 0,
        "cross_churn": me.cross_churn if me else 0,
        "significance": round(significance_now, 1),
        "prs_opened": me.prs_opened if me else 0,
        "prs_merged": me.prs_merged if me else 0,
        "reviews": reviews_now,
    }
    previous_metrics = {
        "commits": commits_prev,
        "significance": round(significance_prev, 1),
        "prs_merged": prev_me.prs_merged if prev_me else 0,
        "reviews": reviews_prev,
    }
    return {
        "period": period_label,
        "previous_period": f"{prev_start.isoformat()} to {prev_end.isoformat()}",
        "orgs": orgs_label,
        "person": display_name,
        "metrics": metrics,
        "previous_metrics": previous_metrics,
        "deltas_vs_previous": {
            "commits_pct": _pct_change(commits_now, commits_prev),
            "significance_pct": _pct_change(significance_now, significance_prev),
            "reviews_pct": _pct_change(reviews_now, reviews_prev),
        },
        "percentiles": {
            k: round(v, 3) for k, v in (me.percentiles if me else {}).items()
        },
        "rank_by_significance": rank,
        "previous_rank": prev_rank,
        "rank_change": (prev_rank - rank) if rank and prev_rank else None,
        "population": len(board),
        "share_of_total_significance": (
            _round_ratio(me.significance / total_significance)
            if me and total_significance
            else None
        ),
        "weekly_trend": _weekly(series),
        "top_repos": [
            {
                "name": f"{r.org_login}/{r.name}",
                "commits": r.commits,
                "significance": round(r.significance, 1),
                "churn_lines": r.churn,
            }
            for r in split[:5]
        ],
    }


async def repo_context(
    session: AsyncSession,
    *,
    repo_id: int,
    full_name: str,
    orgs: list[int],
    start: date,
    end: date,
    orgs_label: str,
    period_label: str,
) -> dict[str, object]:
    prev_start, prev_end = previous_range(start, end)
    totals = await stats.totals(
        session, orgs=[], start=start, end=end, repo_ids=[repo_id]
    )
    prev_totals = await stats.totals(
        session, orgs=[], start=prev_start, end=prev_end, repo_ids=[repo_id]
    )
    contributors = await stats.repo_contributors(
        session, repo_id=repo_id, orgs=orgs, start=start, end=end
    )
    prev_contributors = await stats.repo_contributors(
        session, repo_id=repo_id, orgs=orgs, start=prev_start, end=prev_end
    )
    series = await stats.timeseries(
        session, orgs=[], start=start, end=end, repo_ids=[repo_id]
    )
    total_significance = sum(c.significance for c in contributors)
    return {
        "period": period_label,
        "previous_period": f"{prev_start.isoformat()} to {prev_end.isoformat()}",
        "orgs": orgs_label,
        "repo": full_name,
        "totals": _totals_dict(totals, with_repos=False),
        "previous_totals": _totals_dict(prev_totals, with_repos=False),
        "deltas_vs_previous": _totals_deltas(totals, prev_totals),
        "weekly_trend": _weekly(series),
        "contributor_count": len(contributors),
        "previous_contributor_count": len(prev_contributors),
        "contributor_turnover": _turnover(contributors, prev_contributors),
        "top_contributor_share": (
            _round_ratio(contributors[0].significance / total_significance)
            if contributors and total_significance
            else None
        ),
        "top_contributors": [
            {
                "name": p.display_name,
                "commits": p.commits,
                "significance": round(p.significance, 1),
                "churn_ratio": _round_ratio(p.churn_ratio),
                "reviews": p.reviews,
            }
            for p in contributors[:5]
        ],
    }
