"""Wide-picture context builders for insight generation.

One builder per insight area. The returned dicts are the single source both
for the AI prompt and for the deterministic fallback statements, so the two
always describe the same numbers. Keys are stable on purpose: they feed the
insight cache key.
"""

from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from gca.services import stats


def _round_ratio(value: float) -> float:
    return round(value, 3)


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
    totals = await stats.totals(
        session,
        orgs=orgs,
        start=start,
        end=end,
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
    repos = await stats.repo_leaderboard(
        session,
        orgs=orgs,
        start=start,
        end=end,
        repo_ids=repo_ids,
        person_ids=person_ids,
    )
    return {
        "period": period_label,
        "orgs": orgs_label,
        "totals": {
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
            "active_repos": totals.active_repos,
        },
        "top_contributors": [
            {
                "name": p.display_name,
                "commits": p.commits,
                "significance": round(p.significance, 1),
                "churn_ratio": _round_ratio(p.churn_ratio),
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
    board = await stats.person_leaderboard(session, orgs=orgs, start=start, end=end)
    me = next((s for s in board if s.person_id == person_id), None)
    rank = next((i + 1 for i, s in enumerate(board) if s.person_id == person_id), None)
    split = await stats.person_repo_split(
        session, person_id=person_id, orgs=orgs, start=start, end=end
    )
    metrics = {
        "commits": me.commits if me else 0,
        "additions": me.additions if me else 0,
        "deletions": me.deletions if me else 0,
        "churn_lines": me.churn if me else 0,
        "churn_ratio": _round_ratio(me.churn_ratio) if me else 0.0,
        "self_churn": me.self_churn if me else 0,
        "cross_churn": me.cross_churn if me else 0,
        "significance": round(me.significance, 1) if me else 0.0,
        "prs_opened": me.prs_opened if me else 0,
        "prs_merged": me.prs_merged if me else 0,
        "reviews": me.reviews if me else 0,
    }
    return {
        "period": period_label,
        "orgs": orgs_label,
        "person": display_name,
        "metrics": metrics,
        "percentiles": {
            k: round(v, 3) for k, v in (me.percentiles if me else {}).items()
        },
        "rank_by_significance": rank,
        "population": len(board),
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
    totals = await stats.totals(
        session, orgs=[], start=start, end=end, repo_ids=[repo_id]
    )
    contributors = await stats.repo_contributors(
        session, repo_id=repo_id, orgs=orgs, start=start, end=end
    )
    return {
        "period": period_label,
        "orgs": orgs_label,
        "repo": full_name,
        "totals": {
            "commits": totals.commits,
            "additions": totals.additions,
            "deletions": totals.deletions,
            "churn_lines": totals.churn,
            "churn_ratio": _round_ratio(totals.churn_ratio),
            "significance": round(totals.significance, 1),
            "prs_opened": totals.prs_opened,
            "prs_merged": totals.prs_merged,
            "reviews": totals.reviews,
        },
        "contributor_count": len(contributors),
        "top_contributors": [
            {
                "name": p.display_name,
                "commits": p.commits,
                "significance": round(p.significance, 1),
                "churn_ratio": _round_ratio(p.churn_ratio),
            }
            for p in contributors[:5]
        ],
    }
