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


def _delta_phrase(value: object, label: str) -> str | None:
    """'commits up 12% versus the previous period' from a percent delta."""
    try:
        pct = float(value)  # type: ignore[arg-type]
    except TypeError, ValueError:
        return None
    direction = "up" if pct >= 0 else "down"
    return f"{label} {direction} {abs(pct):g}% versus the previous period"


def _deltas_sentence(context: dict[str, Any]) -> str | None:
    deltas = context.get("deltas_vs_previous") or {}
    phrases = [
        p
        for p in (
            _delta_phrase(deltas.get("commits_pct"), "Commits are"),
            _delta_phrase(deltas.get("significance_pct"), "significance is"),
            _delta_phrase(deltas.get("reviews_pct"), "reviews are"),
        )
        if p
    ]
    if not phrases:
        return None
    return ", ".join(phrases) + "."


def _turnover_sentence(turnover: dict[str, Any] | None, noun: str) -> str | None:
    if not turnover:
        return None
    arrived = _num(turnover.get("arrived_count"))
    departed = _num(turnover.get("departed_count"))
    if not arrived and not departed:
        return None
    parts = []
    if arrived:
        names = ", ".join(turnover.get("arrived") or [])
        suffix = f" ({names})" if names else ""
        parts.append(f"{arrived} {noun} became active{suffix}")
    if departed:
        names = ", ".join(turnover.get("departed") or [])
        suffix = f" ({names})" if names else ""
        parts.append(f"{departed} went quiet{suffix}")
    return " and ".join(parts) + " compared with the previous period."


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
    deltas = _deltas_sentence(context)
    if deltas:
        parts.append(deltas)
    turnover = _turnover_sentence(context.get("people_turnover"), "people")
    if turnover:
        parts.append(turnover)
    share = context.get("top3_significance_share")
    if share is not None:
        parts.append(
            f"The top three contributors carry {_pct(share)} of total significance."
        )
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
    deltas = _deltas_sentence(context)
    if deltas:
        parts.append(deltas)
    rank = context.get("rank_by_significance")
    population = _num(context.get("population"))
    percentiles = context.get("percentiles") or {}
    if rank and population:
        movement = ""
        change = context.get("rank_change")
        if isinstance(change, int) and change:
            movement = (
                f", {'up' if change > 0 else 'down'} {abs(change)} "
                f"place{'s' if abs(change) != 1 else ''} versus the previous period"
            )
        parts.append(
            f"That ranks {rank} of {population} by significance "
            f"(P{_pct(percentiles.get('significance', 0)).rstrip('%')}){movement}."
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
    deltas = _deltas_sentence(context)
    if deltas:
        parts.append(deltas)
    turnover = _turnover_sentence(context.get("contributor_turnover"), "contributors")
    if turnover:
        parts.append(turnover)
    top = context.get("top_contributors") or []
    if top:
        leader = top[0]
        share = context.get("top_contributor_share")
        share_note = f", {_pct(share)} of the repository's total" if share else ""
        parts.append(
            f"{leader.get('name')} contributed the largest share "
            f"(significance {leader.get('significance')}, "
            f"{_num(leader.get('commits')):,} commits{share_note})."
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
