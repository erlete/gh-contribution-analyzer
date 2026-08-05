"""Deterministic insight statements when AI is disabled or failing.

Each function turns the same context dict that would feed the AI prompt into
short factual prose, so every insight area always renders with the same
structure: AI text when available, these statements otherwise.
"""

from typing import Any


def _num(value: object) -> int:
    try:
        return int(value)  # type: ignore[call-overload, no-any-return]
    except TypeError, ValueError:
        return 0


def _pct(ratio: object) -> str:
    try:
        return f"{round(float(ratio) * 100)}%"  # type: ignore[arg-type]
    except TypeError, ValueError:
        return "0%"


def _dashboard(context: dict[str, Any]) -> str:
    totals = context.get("totals") or {}
    period = context.get("period", "the selected period")
    orgs = context.get("orgs", "the selected orgs")
    commits = _num(totals.get("commits"))
    if commits == 0:
        return f"No recorded activity for {orgs} in {period.lower()}."
    parts = [
        f"{commits:,} commits from {_num(totals.get('active_people')):,} "
        f"contributors touched {_num(totals.get('active_repos')):,} "
        f"repositories in {period.lower()} across {orgs}.",
        f"{_num(totals.get('additions')):,} lines were added and "
        f"{_num(totals.get('deletions')):,} removed, with a churn ratio of "
        f"{_pct(totals.get('churn_ratio'))}.",
    ]
    top = context.get("top_contributors") or []
    if top:
        leader = top[0]
        parts.append(
            f"{leader.get('name')} leads by significance "
            f"({leader.get('significance')}) with "
            f"{_num(leader.get('commits')):,} commits."
        )
    repos = context.get("top_repos") or []
    if repos:
        busiest = repos[0]
        parts.append(
            f"The busiest repository is {busiest.get('name')} "
            f"({_num(busiest.get('commits')):,} commits, "
            f"{_num(busiest.get('contributors')):,} contributors)."
        )
    parts.append(
        f"Pull requests: {_num(totals.get('prs_opened')):,} opened, "
        f"{_num(totals.get('prs_merged')):,} merged, "
        f"{_num(totals.get('reviews')):,} reviews given."
    )
    return " ".join(parts)


def _person(context: dict[str, Any]) -> str:
    metrics = context.get("metrics") or {}
    name = context.get("person", "This person")
    period = context.get("period", "the selected period")
    commits = _num(metrics.get("commits"))
    if commits == 0 and _num(metrics.get("reviews")) == 0:
        return f"{name} has no recorded activity in {period.lower()}."
    parts = [
        f"{name} authored {commits:,} commits "
        f"({_num(metrics.get('additions')):,} lines added, "
        f"{_num(metrics.get('deletions')):,} removed) in {period.lower()}."
    ]
    rank = context.get("rank_by_significance")
    population = _num(context.get("population"))
    percentiles = context.get("percentiles") or {}
    if rank and population:
        parts.append(
            f"That ranks {rank} of {population} by significance "
            f"(P{_pct(percentiles.get('significance', 0)).rstrip('%')})."
        )
    parts.append(
        f"Churn ratio is {_pct(metrics.get('churn_ratio'))} "
        f"({_num(metrics.get('self_churn')):,} self, "
        f"{_num(metrics.get('cross_churn')):,} cross rewrites)."
    )
    repos = context.get("top_repos") or []
    if repos:
        busiest = repos[0]
        extra = len(repos) - 1
        suffix = f" plus {extra} more" if extra > 0 else ""
        parts.append(
            f"Most of the work landed in {busiest.get('name')} "
            f"(significance {busiest.get('significance')}){suffix}."
        )
    parts.append(
        f"{_num(metrics.get('prs_merged')):,} of "
        f"{_num(metrics.get('prs_opened')):,} pull requests merged and "
        f"{_num(metrics.get('reviews')):,} reviews given."
    )
    return " ".join(parts)


def _repo(context: dict[str, Any]) -> str:
    totals = context.get("totals") or {}
    repo = context.get("repo", "This repository")
    period = context.get("period", "the selected period")
    commits = _num(totals.get("commits"))
    if commits == 0:
        return f"{repo} shows no activity in {period.lower()}."
    parts = [
        f"{repo} received {commits:,} commits from "
        f"{_num(context.get('contributor_count')):,} contributors in "
        f"{period.lower()}: {_num(totals.get('additions')):,} lines added, "
        f"{_num(totals.get('deletions')):,} removed "
        f"(churn {_pct(totals.get('churn_ratio'))})."
    ]
    top = context.get("top_contributors") or []
    if top:
        leader = top[0]
        parts.append(
            f"{leader.get('name')} contributed the largest share "
            f"(significance {leader.get('significance')}, "
            f"{_num(leader.get('commits')):,} commits)."
        )
    parts.append(
        f"{_num(totals.get('prs_merged')):,} pull requests merged and "
        f"{_num(totals.get('reviews')):,} reviews recorded."
    )
    return " ".join(parts)


_GENERATORS = {
    "dashboard": _dashboard,
    "person": _person,
    "repo": _repo,
}


def fallback_text(kind: str, context: dict[str, Any]) -> str:
    """Build the data-parsing statement for an area. `kind` picks the context
    shape: dashboard, person or repo."""
    generator = _GENERATORS.get(kind, _dashboard)
    return generator(context)
