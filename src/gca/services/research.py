"""Research block computation.

A research is a stack of blocks; each block is one operation over
hand-picked people or repositories (compare, correlate, rank, evolution,
composition, overlap, handoff, review graph, volatility). Blocks store
configuration only; this module turns a block plus the current scope and
period into tables, chart specs and an AI-ready context, so results always
reflect the live data. Entity pickers already limit selection to relevant
persons, so no extra visibility filtering happens here beyond what the
stats layer applies.
"""

import logging
import math
from dataclasses import dataclass
from dataclasses import field as dc_field
from datetime import UTC, date, datetime, time, timedelta

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from gca.charts import ChartSpec, Series, palettes
from gca.models import Identity, Org, Person, PullRequest, Repo, Review
from gca.services import stats

log = logging.getLogger("gca.research")

METRIC_CHOICES: tuple[tuple[str, str], ...] = (
    ("commits", "Commits"),
    ("additions", "Lines added"),
    ("deletions", "Lines deleted"),
    ("churn", "Churn lines"),
    ("significance", "Significance"),
    ("prs_merged", "PRs merged"),
    ("reviews", "Reviews"),
)
_METRIC_LABELS = dict(METRIC_CHOICES)


# Slot vocabulary: "people"/"people_b"/"repos" are repeatable pickers,
# "person"/"person_b"/"repo"/"repo_b" single selects, "entity" switches
# between the people and repos pickers, "metric" picks the measured field.
@dataclass(frozen=True)
class OpDef:
    key: str
    label: str
    description: str
    slots: tuple[str, ...]


OPS: dict[str, OpDef] = {
    od.key: od
    for od in (
        OpDef(
            "compare_people",
            "Compare people",
            "Puts two or more people side by side for the selected period:"
            " every core metric in one table, plus their weekly significance"
            " curves on one chart. Needs at least two people.",
            ("people",),
        ),
        OpDef(
            "compare_repos",
            "Compare repositories",
            "Puts two or more repositories side by side for the selected"
            " period: every core metric in one table, plus their weekly"
            " significance curves on one chart. Needs at least two"
            " repositories.",
            ("repos",),
        ),
        OpDef(
            "compare_groups",
            "Compare two groups of people",
            "Compares two hand-picked groups of people as wholes. Metrics"
            " are summed within each group, and the chart shows each"
            " group's combined weekly significance.",
            ("people", "people_b"),
        ),
        OpDef(
            "versus_scope",
            "Person vs the rest of the scope",
            "Measures one person against everyone active in the current"
            " scope and period: their value per metric, the scope average,"
            " and how many standard deviations above or below that average"
            " they sit (the z-score).",
            ("person",),
        ),
        OpDef(
            "correlate_person_repo",
            "Correlate person with repository",
            "Shows how a person and a repository relate: presence (their"
            " share of the repository's work), focus (the share of their own"
            " work spent there), reviews given there, and their first and"
            " last activity in the period.",
            ("person", "repo"),
        ),
        OpDef(
            "correlate_people",
            "Correlate two people",
            "Looks for connections between two people: repositories both"
            " worked on, weeks both were active, reviews they exchanged,"
            " and how similar their work profiles are (0 means no overlap,"
            " 1 means an identical distribution across repositories).",
            ("person", "person_b"),
        ),
        OpDef(
            "correlate_repos",
            "Correlate two repositories",
            "Looks for connections between two repositories: contributors"
            " they share, people whose work shifted from one to the other"
            " within the period, and how tightly their weekly activity"
            " moves together (correlation from -1 to 1).",
            ("repo", "repo_b"),
        ),
        OpDef(
            "rank",
            "Rank a hand-picked set",
            "Builds a custom leaderboard from a hand-picked set of people"
            " or repositories, ordered by the metric you choose, with each"
            " entry's share of the group total.",
            ("entity", "people", "repos", "metric"),
        ),
        OpDef(
            "evolution",
            "Evolution over time",
            "Draws the weekly evolution of the chosen metric for each"
            " selected person or repository on one chart, like a race over"
            " the period.",
            ("entity", "people", "repos", "metric"),
        ),
        OpDef(
            "composition_repo",
            "Repository composition",
            "Breaks a repository's activity into stacked weekly bands per"
            " contributor, showing who carried the work at each point of"
            " the period.",
            ("repo",),
        ),
        OpDef(
            "composition_person",
            "Person's focus composition",
            "Breaks a person's activity into stacked weekly bands per"
            " repository, showing where their attention went at each point"
            " of the period.",
            ("person",),
        ),
        OpDef(
            "overlap_people",
            "Repositories shared by all",
            "Finds the repositories where every selected person was active"
            " in the period, with each person's significance there. Needs"
            " at least two people.",
            ("people",),
        ),
        OpDef(
            "overlap_repos",
            "People present in all",
            "Finds the people active in every selected repository in the"
            " period, with their significance in each. Needs at least two"
            " repositories.",
            ("repos",),
        ),
        OpDef(
            "handoff",
            "Handoff detection",
            "Detects work changing hands inside a repository: each"
            " contributor's share of the repository's significance in the"
            " first half of the period versus the second half, sorted by"
            " the size of the shift.",
            ("repo",),
        ),
        OpDef(
            "review_graph",
            "Review graph",
            "Cross-review matrix for a set of people: each cell counts the"
            " reviews the row person gave on the column person's pull"
            " requests in the period. Darker cells mean more reviews."
            " Needs at least two people.",
            ("people",),
        ),
        OpDef(
            "volatility",
            "Volatility",
            "Classifies each selected person or repository as steady or"
            " bursty from the variation of their weekly output (standard"
            " deviation divided by average). Below 0.6 reads steady, above"
            " 1.2 bursty.",
            ("entity", "people", "repos", "metric"),
        ),
    )
}

# Minimum sizes for the repeatable slots, where an op needs more than one.
_MIN_MULTI: dict[str, dict[str, int]] = {
    "compare_people": {"people": 2},
    "compare_repos": {"repos": 2},
    "compare_groups": {"people": 1, "people_b": 1},
    "overlap_people": {"people": 2},
    "overlap_repos": {"repos": 2},
    "review_graph": {"people": 2},
    "rank": {"people": 2, "repos": 2},
    "evolution": {"people": 1, "repos": 1},
    "volatility": {"people": 1, "repos": 1},
}


@dataclass
class Cell:
    text: str
    num: bool = False
    heat: float | None = None
    link: str | None = None


@dataclass
class BlockTable:
    title: str
    headers: list[tuple[str | None, ...]]
    rows: list[list[Cell]]


@dataclass
class BlockResult:
    op: str
    label: str
    sentence: str
    description: str
    tables: list[BlockTable] = dc_field(default_factory=list)
    charts: list[ChartSpec] = dc_field(default_factory=list)
    context: dict[str, object] = dc_field(default_factory=dict)
    error: str | None = None


@dataclass
class _Ctx:
    session: AsyncSession
    orgs: list[int]
    start: date
    end: date
    period_label: str
    all_time: bool
    block: dict[str, object]

    @property
    def period_mode(self) -> str:
        return "all time" if self.all_time else "window"


def validate_block(block: dict[str, object]) -> str | None:
    """Return a user-facing problem statement, or None when valid."""
    op = OPS.get(str(block.get("op", "")))
    if op is None:
        return "Unknown operation."
    mins = _MIN_MULTI.get(op.key, {})
    slots = list(op.slots)
    if "entity" in slots:
        entity = str(block.get("entity", ""))
        if entity not in ("people", "repos"):
            return "Pick whether the block works on people or repositories."
        slots = [s for s in slots if s not in ("people", "repos", "entity")]
        slots.append(entity)
    for slot in slots:
        if slot in ("people", "people_b", "repos"):
            values = block.get(slot) or []
            needed = mins.get(slot, 1)
            if not isinstance(values, list) or len(values) < needed:
                noun = "repositories" if slot == "repos" else "people"
                return f"This operation needs at least {needed} {noun}."
        elif slot in ("person", "person_b", "repo", "repo_b"):
            if not isinstance(block.get(slot), int):
                noun = "repository" if slot.startswith("repo") else "person"
                return f"This operation needs a {noun} selected."
        elif slot == "metric" and block.get("metric") not in _METRIC_LABELS:
            return "Pick a valid metric."
    if op.key in ("correlate_people",) and block.get("person") == block.get("person_b"):
        return "Pick two different people."
    if op.key in ("correlate_repos",) and block.get("repo") == block.get("repo_b"):
        return "Pick two different repositories."
    return None


async def run_block(
    session: AsyncSession,
    block: dict[str, object],
    *,
    orgs: list[int],
    start: date,
    end: date,
    period_label: str,
    all_time: bool,
) -> BlockResult:
    op = OPS.get(str(block.get("op", "")))
    if op is None:
        return BlockResult(
            op=str(block.get("op", "")),
            label="Unknown operation",
            sentence="Unknown operation",
            description="",
            error="This block uses an operation this version does not know.",
        )
    problem = validate_block(block)
    if problem is not None:
        return BlockResult(
            op=op.key,
            label=op.label,
            sentence=op.label,
            description=op.description,
            error=problem,
        )
    ctx = _Ctx(
        session=session,
        orgs=orgs,
        start=start,
        end=end,
        period_label=period_label,
        all_time=all_time,
        block=block,
    )
    try:
        return await _HANDLERS[op.key](ctx)
    except Exception:
        log.exception("research block %s failed", op.key)
        return BlockResult(
            op=op.key,
            label=op.label,
            sentence=op.label,
            description=op.description,
            error="This block failed to compute; the entities may no longer exist.",
        )


# ---- shared helpers -------------------------------------------------------


def _ids(block: dict[str, object], key: str) -> list[int]:
    values = block.get(key)
    if not isinstance(values, list):
        return []
    return [int(v) for v in values]


def _id(block: dict[str, object], key: str) -> int:
    """A single entity id slot; validate_block guarantees it exists."""
    value = block.get(key)
    if not isinstance(value, int):
        raise ValueError(f"block slot {key} must carry an entity id")
    return value


def _fmt(value: float, metric: str = "commits") -> str:
    if metric in ("significance",):
        return f"{value:,.1f}"
    return f"{int(round(value)):,}"


def _pct(ratio: float) -> str:
    return f"{round(ratio * 100)}%"


def _weeks(start: date, end: date) -> list[date]:
    first = start - timedelta(days=start.weekday())
    out = []
    week = first
    while week < end:
        out.append(week)
        week += timedelta(days=7)
    return out


def _series(
    weekly: dict[date, dict[str, float]], weeks: list[date], metric: str
) -> list[float]:
    return [round(weekly.get(w, {}).get(metric, 0.0), 2) for w in weeks]


async def _person_names(session: AsyncSession, ids: list[int]) -> dict[int, str]:
    if not ids:
        return {}
    rows = (
        await session.execute(
            sa.select(Person.id, Person.display_name).where(Person.id.in_(ids))
        )
    ).all()
    found = {row.id: row.display_name for row in rows}
    return {i: found.get(i, f"person {i}") for i in ids}


async def _repo_names(session: AsyncSession, ids: list[int]) -> dict[int, str]:
    if not ids:
        return {}
    rows = (
        await session.execute(
            sa.select(Repo.id, Repo.name, Org.login)
            .join(Org, Repo.org_id == Org.id)
            .where(Repo.id.in_(ids))
        )
    ).all()
    found = {row.id: f"{row.login}/{row.name}" for row in rows}
    return {i: found.get(i, f"repo {i}") for i in ids}


def _mean_std(values: list[float]) -> tuple[float, float]:
    if not values:
        return 0.0, 0.0
    mean = sum(values) / len(values)
    var = sum((v - mean) ** 2 for v in values) / len(values)
    return mean, var**0.5


def _pearson(a: list[float], b: list[float]) -> float:
    if len(a) != len(b) or len(a) < 2:
        return 0.0
    mean_a, std_a = _mean_std(a)
    mean_b, std_b = _mean_std(b)
    if std_a == 0 or std_b == 0:
        return 0.0
    cov = sum((x - mean_a) * (y - mean_b) for x, y in zip(a, b, strict=True))
    return round(cov / (len(a) * std_a * std_b), 2)


def _cosine(a: dict[int, float], b: dict[int, float]) -> float:
    keys = set(a) | set(b)
    if not keys:
        return 0.0
    dot = sum(a.get(k, 0.0) * b.get(k, 0.0) for k in keys)
    norm_a = math.sqrt(sum(v**2 for v in a.values()))
    norm_b = math.sqrt(sum(v**2 for v in b.values()))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return round(dot / (norm_a * norm_b), 2)


def _weekly_line_chart(
    names: list[str],
    series_values: list[list[float]],
    weeks: list[date],
    *,
    axis: str,
    description: str,
    stacked: bool = False,
) -> ChartSpec:
    return ChartSpec(
        kind="bar" if stacked else "line",
        labels=[w.isoformat() for w in weeks],
        series=[
            Series(
                name=name,
                values=values,
                kind="bar" if stacked else "line",
                stack="composition" if stacked else None,
            )
            for name, values in zip(names, series_values, strict=True)
        ],
        axes=[axis],
        description=description,
        zoom=len(weeks) > 30,
        palette="wide",
    )


async def _review_pairs(ctx: _Ctx, person_ids: list[int]) -> dict[tuple[int, int], int]:
    """Reviews (reviewer person, PR author person) -> count in the period."""
    reviewer = aliased(Identity)
    author = aliased(Identity)
    start_dt = datetime.combine(ctx.start, time.min, tzinfo=UTC)
    end_dt = datetime.combine(ctx.end, time.min, tzinfo=UTC)
    q = (
        sa.select(
            reviewer.person_id.label("reviewer_person"),
            author.person_id.label("author_person"),
            sa.func.count(Review.id).label("n"),
        )
        .join(PullRequest, Review.pull_request_id == PullRequest.id)
        .join(Repo, PullRequest.repo_id == Repo.id)
        .join(reviewer, Review.reviewer_identity_id == reviewer.id)
        .join(author, PullRequest.author_identity_id == author.id)
        .where(
            Repo.included.is_(True),
            Review.submitted_at >= start_dt,
            Review.submitted_at < end_dt,
            reviewer.person_id.in_(person_ids),
            author.person_id.in_(person_ids),
        )
        .group_by(reviewer.person_id, author.person_id)
    )
    if ctx.orgs:
        q = q.where(Repo.org_id.in_(ctx.orgs))
    return {
        (row.reviewer_person, row.author_person): int(row.n)
        for row in (await ctx.session.execute(q)).all()
    }


def _base_context(ctx: _Ctx, op: OpDef, facts: list[str]) -> dict[str, object]:
    return {
        "op": op.label,
        "period": ctx.period_label,
        "period_mode": ctx.period_mode,
        "facts": facts,
    }


# ---- handlers -------------------------------------------------------------


async def _compare_people(ctx: _Ctx) -> BlockResult:
    op = OPS["compare_people"]
    ids = _ids(ctx.block, "people")
    names = await _person_names(ctx.session, ids)
    board = await stats.person_leaderboard(
        ctx.session, orgs=ctx.orgs, start=ctx.start, end=ctx.end, person_ids=ids
    )
    by_id = {s.person_id: s for s in board}
    metrics = [m for m, _ in METRIC_CHOICES] + ["prs_opened"]
    rows = []
    for metric, label in METRIC_CHOICES:
        cells = [Cell(label)]
        for pid in ids:
            value = getattr(by_id.get(pid), metric, 0) or 0
            cells.append(Cell(_fmt(float(value), metric), num=True))
        rows.append(cells)
    ratio_cells = [Cell("Churn ratio")]
    for pid in ids:
        stat = by_id.get(pid)
        ratio_cells.append(Cell(_pct(stat.churn_ratio if stat else 0.0), num=True))
    rows.append(ratio_cells)
    headers: list[tuple[str | None, ...]] = [("Metric",)] + [
        (names[pid], "num") for pid in ids
    ]
    weekly = await stats.weekly_by_entity(
        ctx.session,
        orgs=ctx.orgs,
        start=ctx.start,
        end=ctx.end,
        by="person",
        person_ids=ids,
    )
    weeks = _weeks(ctx.start, ctx.end)
    chart = _weekly_line_chart(
        [names[pid] for pid in ids],
        [_series(weekly.get(pid, {}), weeks, "significance") for pid in ids],
        weeks,
        axis="Weekly significance",
        description=f"Weekly significance per person, {ctx.period_label}",
    )
    ranked = sorted(
        ids,
        key=lambda p: getattr(by_id.get(p), "significance", 0.0) or 0.0,
        reverse=True,
    )
    facts = [
        f"{names[ranked[0]]} leads by significance"
        f" ({_fmt(getattr(by_id.get(ranked[0]), 'significance', 0.0) or 0.0, 'significance')})"
        f" among {', '.join(names[p] for p in ids)}."
    ]
    people_ctx = []
    for pid in ids:
        stat = by_id.get(pid)
        entry: dict[str, object] = {"name": names[pid]}
        for metric in metrics:
            entry[metric] = round(float(getattr(stat, metric, 0) or 0), 1)
        entry["churn_ratio"] = round(stat.churn_ratio, 3) if stat else 0.0
        people_ctx.append(entry)
    context = _base_context(ctx, op, facts)
    context["people"] = people_ctx
    sentence = "Compare " + " vs ".join(names[pid] for pid in ids)
    return BlockResult(
        op=op.key,
        label=op.label,
        sentence=sentence,
        description=op.description,
        tables=[BlockTable("Side by side", headers, rows)],
        charts=[chart],
        context=context,
    )


async def _compare_repos(ctx: _Ctx) -> BlockResult:
    op = OPS["compare_repos"]
    ids = _ids(ctx.block, "repos")
    names = await _repo_names(ctx.session, ids)
    board = await stats.repo_leaderboard(
        ctx.session, orgs=ctx.orgs, start=ctx.start, end=ctx.end, repo_ids=ids
    )
    by_id = {s.repo_id: s for s in board}
    rows = []
    for metric, label in METRIC_CHOICES:
        cells = [Cell(label)]
        for rid in ids:
            value = getattr(by_id.get(rid), metric, 0) or 0
            cells.append(Cell(_fmt(float(value), metric), num=True))
        rows.append(cells)
    contributor_cells = [Cell("Contributors")]
    for rid in ids:
        stat = by_id.get(rid)
        contributor_cells.append(Cell(_fmt(stat.contributors if stat else 0), num=True))
    rows.append(contributor_cells)
    headers: list[tuple[str | None, ...]] = [("Metric",)] + [
        (names[rid], "num") for rid in ids
    ]
    weekly = await stats.weekly_by_entity(
        ctx.session,
        orgs=ctx.orgs,
        start=ctx.start,
        end=ctx.end,
        by="repo",
        repo_ids=ids,
    )
    weeks = _weeks(ctx.start, ctx.end)
    chart = _weekly_line_chart(
        [names[rid] for rid in ids],
        [_series(weekly.get(rid, {}), weeks, "significance") for rid in ids],
        weeks,
        axis="Weekly significance",
        description=f"Weekly significance per repository, {ctx.period_label}",
    )
    ranked = sorted(
        ids,
        key=lambda r: getattr(by_id.get(r), "significance", 0.0) or 0.0,
        reverse=True,
    )
    facts = [
        f"{names[ranked[0]]} leads by significance among the compared repositories."
    ]
    repos_ctx = []
    for rid in ids:
        stat = by_id.get(rid)
        entry: dict[str, object] = {"name": names[rid]}
        for metric, _label in METRIC_CHOICES:
            entry[metric] = round(float(getattr(stat, metric, 0) or 0), 1)
        entry["contributors"] = stat.contributors if stat else 0
        repos_ctx.append(entry)
    context = _base_context(ctx, op, facts)
    context["repos"] = repos_ctx
    sentence = "Compare " + " vs ".join(names[rid] for rid in ids)
    return BlockResult(
        op=op.key,
        label=op.label,
        sentence=sentence,
        description=op.description,
        tables=[BlockTable("Side by side", headers, rows)],
        charts=[chart],
        context=context,
    )


async def _compare_groups(ctx: _Ctx) -> BlockResult:
    op = OPS["compare_groups"]
    group_a = _ids(ctx.block, "people")
    group_b = _ids(ctx.block, "people_b")
    names = await _person_names(ctx.session, group_a + group_b)

    async def group_sums(ids: list[int]) -> dict[str, float]:
        board = await stats.person_leaderboard(
            ctx.session, orgs=ctx.orgs, start=ctx.start, end=ctx.end, person_ids=ids
        )
        sums = dict.fromkeys([m for m, _ in METRIC_CHOICES], 0.0)
        for stat in board:
            for metric in sums:
                sums[metric] += float(getattr(stat, metric, 0) or 0)
        return sums

    sums_a = await group_sums(group_a)
    sums_b = await group_sums(group_b)
    label_a = f"Group A ({len(group_a)})"
    label_b = f"Group B ({len(group_b)})"
    rows = []
    for metric, label in METRIC_CHOICES:
        rows.append(
            [
                Cell(label),
                Cell(_fmt(sums_a[metric], metric), num=True),
                Cell(_fmt(sums_b[metric], metric), num=True),
            ]
        )
    weekly = await stats.weekly_by_entity(
        ctx.session,
        orgs=ctx.orgs,
        start=ctx.start,
        end=ctx.end,
        by="person",
        person_ids=group_a + group_b,
    )
    weeks = _weeks(ctx.start, ctx.end)

    def group_series(ids: list[int]) -> list[float]:
        out = []
        for week in weeks:
            out.append(
                round(
                    sum(
                        weekly.get(pid, {}).get(week, {}).get("significance", 0.0)
                        for pid in ids
                    ),
                    2,
                )
            )
        return out

    chart = _weekly_line_chart(
        [label_a, label_b],
        [group_series(group_a), group_series(group_b)],
        weeks,
        axis="Weekly significance",
        description=f"Combined weekly significance per group, {ctx.period_label}",
    )
    leader = label_a if sums_a["significance"] >= sums_b["significance"] else label_b
    facts = [
        f"{leader} carries more significance"
        f" ({_fmt(sums_a['significance'], 'significance')} vs"
        f" {_fmt(sums_b['significance'], 'significance')}).",
    ]
    context = _base_context(ctx, op, facts)
    context["group_a"] = {
        "members": [names[p] for p in group_a],
        **{m: round(v, 1) for m, v in sums_a.items()},
    }
    context["group_b"] = {
        "members": [names[p] for p in group_b],
        **{m: round(v, 1) for m, v in sums_b.items()},
    }
    sentence = (
        f"Compare group A ({', '.join(names[p] for p in group_a)}) vs"
        f" group B ({', '.join(names[p] for p in group_b)})"
    )
    return BlockResult(
        op=op.key,
        label=op.label,
        sentence=sentence,
        description=op.description,
        tables=[
            BlockTable(
                "Group totals",
                [("Metric",), (label_a, "num"), (label_b, "num")],
                rows,
            )
        ],
        charts=[chart],
        context=context,
    )


async def _versus_scope(ctx: _Ctx) -> BlockResult:
    op = OPS["versus_scope"]
    pid = _id(ctx.block, "person")
    names = await _person_names(ctx.session, [pid])
    board = await stats.person_leaderboard(
        ctx.session, orgs=ctx.orgs, start=ctx.start, end=ctx.end
    )
    me = next((s for s in board if s.person_id == pid), None)
    rows = []
    z_scores: dict[str, float] = {}
    for metric, label in METRIC_CHOICES:
        values = [float(getattr(s, metric, 0) or 0) for s in board]
        mean, std = _mean_std(values)
        mine = float(getattr(me, metric, 0) or 0) if me else 0.0
        z = (mine - mean) / std if std > 0 else 0.0
        z_scores[metric] = round(z, 2)
        rows.append(
            [
                Cell(label),
                Cell(_fmt(mine, metric), num=True),
                Cell(_fmt(mean, "significance"), num=True),
                Cell(f"{z:+.2f}", num=True),
            ]
        )
    sig_z = z_scores.get("significance", 0.0)
    position = "above" if sig_z > 0 else "below" if sig_z < 0 else "at"
    facts = [
        f"{names[pid]} sits {abs(sig_z):.1f} standard deviations {position} the"
        f" average active contributor by significance, in a population of"
        f" {len(board)}."
    ]
    if me is not None:
        rank = board.index(me) + 1
        facts.append(f"That is rank {rank} of {len(board)} by significance.")
    context = _base_context(ctx, op, facts)
    context["person"] = names[pid]
    context["population"] = len(board)
    context["z_scores"] = z_scores
    return BlockResult(
        op=op.key,
        label=op.label,
        sentence=f"{names[pid]} vs the rest of the scope",
        description=op.description,
        tables=[
            BlockTable(
                "Standing against the scope average",
                [
                    ("Metric",),
                    (names[pid], "num"),
                    ("Scope average", "num"),
                    ("Z-score", "num"),
                ],
                rows,
            )
        ],
        context=context,
    )


async def _correlate_person_repo(ctx: _Ctx) -> BlockResult:
    op = OPS["correlate_person_repo"]
    pid = _id(ctx.block, "person")
    rid = _id(ctx.block, "repo")
    person_name = (await _person_names(ctx.session, [pid]))[pid]
    repo_name = (await _repo_names(ctx.session, [rid]))[rid]
    in_repo = await stats.weekly_by_entity(
        ctx.session,
        orgs=ctx.orgs,
        start=ctx.start,
        end=ctx.end,
        by="person",
        person_ids=[pid],
        repo_ids=[rid],
    )
    w_both = in_repo.get(pid, {})
    w_repo = (
        await stats.weekly_by_entity(
            ctx.session,
            orgs=ctx.orgs,
            start=ctx.start,
            end=ctx.end,
            by="repo",
            repo_ids=[rid],
        )
    ).get(rid, {})
    w_person = (
        await stats.weekly_by_entity(
            ctx.session,
            orgs=ctx.orgs,
            start=ctx.start,
            end=ctx.end,
            by="person",
            person_ids=[pid],
        )
    ).get(pid, {})
    weeks = _weeks(ctx.start, ctx.end)

    def share_series(
        parts: dict[date, dict[str, float]], wholes: dict[date, dict[str, float]]
    ) -> list[float]:
        out = []
        for week in weeks:
            whole = wholes.get(week, {}).get("significance", 0.0)
            part = parts.get(week, {}).get("significance", 0.0)
            out.append(round(part / whole * 100, 1) if whole > 0 else 0.0)
        return out

    total_both = sum(w.get("significance", 0.0) for w in w_both.values())
    total_repo = sum(w.get("significance", 0.0) for w in w_repo.values())
    total_person = sum(w.get("significance", 0.0) for w in w_person.values())
    presence = total_both / total_repo if total_repo > 0 else 0.0
    focus = total_both / total_person if total_person > 0 else 0.0
    days = await stats.timeseries(
        ctx.session,
        orgs=ctx.orgs,
        start=ctx.start,
        end=ctx.end,
        person_ids=[pid],
        repo_ids=[rid],
    )
    first = days[0].day.isoformat() if days else "never"
    last = days[-1].day.isoformat() if days else "never"
    split = await stats.person_leaderboard(
        ctx.session,
        orgs=ctx.orgs,
        start=ctx.start,
        end=ctx.end,
        person_ids=[pid],
        repo_ids=[rid],
    )
    mine = split[0] if split else None
    rows = [
        [
            Cell("Presence (share of the repository's significance)"),
            Cell(_pct(presence), num=True),
        ],
        [Cell("Focus (share of the person's own work)"), Cell(_pct(focus), num=True)],
        [Cell("Commits there"), Cell(_fmt(mine.commits if mine else 0), num=True)],
        [
            Cell("Significance there"),
            Cell(_fmt(mine.significance if mine else 0.0, "significance"), num=True),
        ],
        [
            Cell("Reviews given there"),
            Cell(_fmt(mine.reviews if mine else 0), num=True),
        ],
        [Cell("First activity in the period"), Cell(first, num=True)],
        [Cell("Last activity in the period"), Cell(last, num=True)],
    ]
    chart = ChartSpec(
        kind="line",
        labels=[w.isoformat() for w in weeks],
        series=[
            Series(name="Presence %", values=share_series(w_both, w_repo)),
            Series(name="Focus %", values=share_series(w_both, w_person)),
        ],
        axes=["Share %"],
        description=(
            f"Weekly presence and focus of {person_name} in {repo_name},"
            f" {ctx.period_label}"
        ),
        zoom=len(weeks) > 30,
        palette="wide",
    )
    facts = [
        f"{person_name} produced {_pct(presence)} of {repo_name}'s significance"
        f" and spent {_pct(focus)} of their own work there.",
        f"First and last activity there: {first} to {last}.",
    ]
    context = _base_context(ctx, op, facts)
    context.update(
        {
            "person": person_name,
            "repo": repo_name,
            "presence_share": round(presence, 3),
            "focus_share": round(focus, 3),
            "reviews_there": mine.reviews if mine else 0,
        }
    )
    return BlockResult(
        op=op.key,
        label=op.label,
        sentence=f"Correlate {person_name} with {repo_name}",
        description=op.description,
        tables=[
            BlockTable(
                "Relationship in the period",
                [("Fact",), ("Value", "num")],
                rows,
            )
        ],
        charts=[chart],
        context=context,
    )


async def _correlate_people(ctx: _Ctx) -> BlockResult:
    op = OPS["correlate_people"]
    a = _id(ctx.block, "person")
    b = _id(ctx.block, "person_b")
    names = await _person_names(ctx.session, [a, b])
    split_a = {
        s.repo_id: s
        for s in await stats.person_repo_split(
            ctx.session, person_id=a, orgs=ctx.orgs, start=ctx.start, end=ctx.end
        )
    }
    split_b = {
        s.repo_id: s
        for s in await stats.person_repo_split(
            ctx.session, person_id=b, orgs=ctx.orgs, start=ctx.start, end=ctx.end
        )
    }
    shared = sorted(
        set(split_a) & set(split_b),
        key=lambda r: split_a[r].significance + split_b[r].significance,
        reverse=True,
    )
    shared_rows = [
        [
            Cell(
                f"{split_a[r].org_login}/{split_a[r].name}",
                link=f"/repos/{r}",
            ),
            Cell(_fmt(split_a[r].significance, "significance"), num=True),
            Cell(_fmt(split_b[r].significance, "significance"), num=True),
        ]
        for r in shared
    ]
    weekly = await stats.weekly_by_entity(
        ctx.session,
        orgs=ctx.orgs,
        start=ctx.start,
        end=ctx.end,
        by="person",
        person_ids=[a, b],
    )
    weeks_a = {
        w for w, v in weekly.get(a, {}).items() if any(x > 0 for x in v.values())
    }
    weeks_b = {
        w for w, v in weekly.get(b, {}).items() if any(x > 0 for x in v.values())
    }
    both = len(weeks_a & weeks_b)
    either = len(weeks_a | weeks_b)
    reviews = await _review_pairs(ctx, [a, b])
    a_on_b = reviews.get((a, b), 0)
    b_on_a = reviews.get((b, a), 0)
    similarity = _cosine(
        {r: s.significance for r, s in split_a.items()},
        {r: s.significance for r, s in split_b.items()},
    )
    facts_rows = [
        [Cell("Repositories both worked on"), Cell(str(len(shared)), num=True)],
        [
            Cell("Weeks both were active"),
            Cell(f"{both} of {either}" if either else "0", num=True),
        ],
        [
            Cell(f"Reviews {names[a]} gave on {names[b]}'s pull requests"),
            Cell(str(a_on_b), num=True),
        ],
        [
            Cell(f"Reviews {names[b]} gave on {names[a]}'s pull requests"),
            Cell(str(b_on_a), num=True),
        ],
        [Cell("Work-profile similarity (0 to 1)"), Cell(f"{similarity:.2f}", num=True)],
    ]
    weeks = _weeks(ctx.start, ctx.end)
    chart = _weekly_line_chart(
        [names[a], names[b]],
        [
            _series(weekly.get(a, {}), weeks, "significance"),
            _series(weekly.get(b, {}), weeks, "significance"),
        ],
        weeks,
        axis="Weekly significance",
        description=f"Weekly significance of both people, {ctx.period_label}",
    )
    facts = [
        f"{names[a]} and {names[b]} shared {len(shared)} repositories and were"
        f" both active in {both} of {either} weeks.",
        f"Review exchange: {a_on_b} one way, {b_on_a} the other.",
        f"Work-profile similarity is {similarity:.2f} on a 0 to 1 scale.",
    ]
    context = _base_context(ctx, op, facts)
    context.update(
        {
            "person_a": names[a],
            "person_b": names[b],
            "shared_repos": [
                f"{split_a[r].org_login}/{split_a[r].name}" for r in shared[:10]
            ],
            "overlapping_weeks": both,
            "active_weeks_union": either,
            "reviews_a_on_b": a_on_b,
            "reviews_b_on_a": b_on_a,
            "profile_similarity": similarity,
        }
    )
    return BlockResult(
        op=op.key,
        label=op.label,
        sentence=f"Correlate {names[a]} with {names[b]}",
        description=op.description,
        tables=[
            BlockTable("Connection facts", [("Fact",), ("Value", "num")], facts_rows),
            BlockTable(
                "Shared repositories",
                [
                    ("Repo",),
                    (names[a], "num", "significance"),
                    (names[b], "num", "significance"),
                ],
                shared_rows,
            ),
        ],
        charts=[chart],
        context=context,
    )


async def _correlate_repos(ctx: _Ctx) -> BlockResult:
    op = OPS["correlate_repos"]
    a = _id(ctx.block, "repo")
    b = _id(ctx.block, "repo_b")
    names = await _repo_names(ctx.session, [a, b])
    board_a = {
        s.person_id: s
        for s in await stats.person_leaderboard(
            ctx.session, orgs=ctx.orgs, start=ctx.start, end=ctx.end, repo_ids=[a]
        )
    }
    board_b = {
        s.person_id: s
        for s in await stats.person_leaderboard(
            ctx.session, orgs=ctx.orgs, start=ctx.start, end=ctx.end, repo_ids=[b]
        )
    }
    shared = sorted(
        set(board_a) & set(board_b),
        key=lambda p: board_a[p].significance + board_b[p].significance,
        reverse=True,
    )
    shared_rows = [
        [
            Cell(board_a[p].display_name, link=f"/people/{p}"),
            Cell(_fmt(board_a[p].significance, "significance"), num=True),
            Cell(_fmt(board_b[p].significance, "significance"), num=True),
        ]
        for p in shared
    ]
    weekly = await stats.weekly_by_entity(
        ctx.session,
        orgs=ctx.orgs,
        start=ctx.start,
        end=ctx.end,
        by="repo",
        repo_ids=[a, b],
    )
    weeks = _weeks(ctx.start, ctx.end)
    series_a = _series(weekly.get(a, {}), weeks, "significance")
    series_b = _series(weekly.get(b, {}), weeks, "significance")
    coupling = _pearson(series_a, series_b)
    mid = ctx.start + (ctx.end - ctx.start) / 2
    movers_rows = []
    movers_ctx = []
    for pid in shared:
        halves = []
        for h_start, h_end in ((ctx.start, mid), (mid, ctx.end)):
            sig = {}
            for rid in (a, b):
                board = await stats.person_leaderboard(
                    ctx.session,
                    orgs=ctx.orgs,
                    start=h_start,
                    end=h_end,
                    person_ids=[pid],
                    repo_ids=[rid],
                )
                sig[rid] = board[0].significance if board else 0.0
            total = sig[a] + sig[b]
            halves.append(sig[b] / total if total > 0 else None)
        first_half, second_half = halves
        if first_half is None or second_half is None:
            continue
        shift = second_half - first_half
        if abs(shift) >= 0.25:
            direction = names[b] if shift > 0 else names[a]
            movers_rows.append(
                [
                    Cell(board_a[pid].display_name, link=f"/people/{pid}"),
                    Cell(_pct(first_half), num=True),
                    Cell(_pct(second_half), num=True),
                    Cell(f"toward {direction}"),
                ]
            )
            movers_ctx.append(
                {
                    "name": board_a[pid].display_name,
                    "moved_toward": direction,
                    "share_shift_pct": round(shift * 100),
                }
            )
    chart = _weekly_line_chart(
        [names[a], names[b]],
        [series_a, series_b],
        weeks,
        axis="Weekly significance",
        description=f"Weekly significance of both repositories, {ctx.period_label}",
    )
    facts = [
        f"{names[a]} and {names[b]} share {len(shared)} contributors and their"
        f" weekly activity correlates at {coupling:+.2f}.",
    ]
    if movers_ctx:
        facts.append(
            f"{len(movers_ctx)} shared contributors shifted a quarter or more of"
            " their combined work between the two within the period."
        )
    context = _base_context(ctx, op, facts)
    context.update(
        {
            "repo_a": names[a],
            "repo_b": names[b],
            "shared_contributors": [board_a[p].display_name for p in shared[:10]],
            "activity_coupling": coupling,
            "movers": movers_ctx,
        }
    )
    return BlockResult(
        op=op.key,
        label=op.label,
        sentence=f"Correlate {names[a]} with {names[b]}",
        description=op.description,
        tables=[
            BlockTable(
                "Shared contributors",
                [
                    ("Person",),
                    (names[a], "num", "significance"),
                    (names[b], "num", "significance"),
                ],
                shared_rows,
            ),
            BlockTable(
                "Work migration between the two",
                [
                    ("Person",),
                    (f"Share in {names[b]}, first half", "num"),
                    (f"Share in {names[b]}, second half", "num"),
                    ("Moved",),
                ],
                movers_rows,
            ),
        ],
        charts=[chart],
        context=context,
    )


async def _rank(ctx: _Ctx) -> BlockResult:
    op = OPS["rank"]
    entity = str(ctx.block["entity"])
    metric = str(ctx.block["metric"])
    metric_label = _METRIC_LABELS[metric]
    if entity == "people":
        ids = _ids(ctx.block, "people")
        names = await _person_names(ctx.session, ids)
        board = await stats.person_leaderboard(
            ctx.session, orgs=ctx.orgs, start=ctx.start, end=ctx.end, person_ids=ids
        )
        values = {s.person_id: float(getattr(s, metric, 0) or 0) for s in board}
        link_base = "/people/"
    else:
        ids = _ids(ctx.block, "repos")
        names = await _repo_names(ctx.session, ids)
        board_r = await stats.repo_leaderboard(
            ctx.session, orgs=ctx.orgs, start=ctx.start, end=ctx.end, repo_ids=ids
        )
        values = {s.repo_id: float(getattr(s, metric, 0) or 0) for s in board_r}
        link_base = "/repos/"
    ordered = sorted(ids, key=lambda i: values.get(i, 0.0), reverse=True)
    total = sum(values.get(i, 0.0) for i in ids)
    rows = [
        [
            Cell(str(position + 1), num=True),
            Cell(names[i], link=f"{link_base}{i}"),
            Cell(_fmt(values.get(i, 0.0), metric), num=True),
            Cell(_pct(values.get(i, 0.0) / total) if total > 0 else "0%", num=True),
        ]
        for position, i in enumerate(ordered)
    ]
    sequence = palettes.CATEGORICAL[max(palettes.CATEGORICAL)]
    chart = ChartSpec(
        kind="hbar",
        labels=[
            names[i] if len(names[i]) <= 22 else names[i][:21] + "…" for i in ordered
        ],
        series=[
            Series(
                name=metric_label,
                values=[round(values.get(i, 0.0), 1) for i in ordered],
                kind="bar",
                item_colors=[
                    sequence[position % len(sequence)]
                    for position in range(len(ordered))
                ],
            )
        ],
        axes=[metric_label],
        description=f"Custom ranking by {metric_label.lower()}, {ctx.period_label}",
        palette="wide",
    )
    facts = [
        f"{names[ordered[0]]} tops this hand-picked set by"
        f" {metric_label.lower()} with"
        f" {_pct(values.get(ordered[0], 0.0) / total) if total > 0 else '0%'}"
        " of the group total."
    ]
    context = _base_context(ctx, op, facts)
    context["metric"] = metric_label
    context["ranking"] = [
        {"name": names[i], "value": round(values.get(i, 0.0), 1)} for i in ordered
    ]
    return BlockResult(
        op=op.key,
        label=op.label,
        sentence=f"Rank {len(ids)} {'people' if entity == 'people' else 'repositories'} by {metric_label.lower()}",
        description=op.description,
        tables=[
            BlockTable(
                f"Ranking by {metric_label.lower()}",
                [
                    ("Rank", "num"),
                    ("Person" if entity == "people" else "Repo",),
                    (metric_label, "num"),
                    ("Share of group", "num"),
                ],
                rows,
            )
        ],
        charts=[chart],
        context=context,
    )


async def _evolution(ctx: _Ctx) -> BlockResult:
    op = OPS["evolution"]
    entity = str(ctx.block["entity"])
    metric = str(ctx.block["metric"])
    metric_label = _METRIC_LABELS[metric]
    if entity == "people":
        ids = _ids(ctx.block, "people")
        names = await _person_names(ctx.session, ids)
        weekly = await stats.weekly_by_entity(
            ctx.session,
            orgs=ctx.orgs,
            start=ctx.start,
            end=ctx.end,
            by="person",
            person_ids=ids,
        )
    else:
        ids = _ids(ctx.block, "repos")
        names = await _repo_names(ctx.session, ids)
        weekly = await stats.weekly_by_entity(
            ctx.session,
            orgs=ctx.orgs,
            start=ctx.start,
            end=ctx.end,
            by="repo",
            repo_ids=ids,
        )
    weeks = _weeks(ctx.start, ctx.end)
    chart = _weekly_line_chart(
        [names[i] for i in ids],
        [_series(weekly.get(i, {}), weeks, metric) for i in ids],
        weeks,
        axis=f"Weekly {metric_label.lower()}",
        description=f"Weekly {metric_label.lower()} evolution, {ctx.period_label}",
    )
    totals = {
        i: sum(v.get(metric, 0.0) for v in weekly.get(i, {}).values()) for i in ids
    }
    leader = max(ids, key=lambda i: totals.get(i, 0.0))
    facts = [
        f"{names[leader]} accumulated the most {metric_label.lower()}"
        f" ({_fmt(totals[leader], metric)}) across the period."
    ]
    context = _base_context(ctx, op, facts)
    context["metric"] = metric_label
    context["totals"] = {names[i]: round(totals.get(i, 0.0), 1) for i in ids}
    return BlockResult(
        op=op.key,
        label=op.label,
        sentence=f"Evolution of {metric_label.lower()} for {', '.join(names[i] for i in ids)}",
        description=op.description,
        charts=[chart],
        context=context,
    )


async def _composition(ctx: _Ctx, *, op_key: str, subject_kind: str) -> BlockResult:
    op = OPS[op_key]
    if subject_kind == "repo":
        rid = _id(ctx.block, "repo")
        subject = (await _repo_names(ctx.session, [rid]))[rid]
        weekly = await stats.weekly_by_entity(
            ctx.session,
            orgs=ctx.orgs,
            start=ctx.start,
            end=ctx.end,
            by="person",
            repo_ids=[rid],
        )
        names = await _person_names(ctx.session, list(weekly))
        band_link = "/people/"
    else:
        pid = _id(ctx.block, "person")
        subject = (await _person_names(ctx.session, [pid]))[pid]
        weekly = await stats.weekly_by_entity(
            ctx.session,
            orgs=ctx.orgs,
            start=ctx.start,
            end=ctx.end,
            by="repo",
            person_ids=[pid],
        )
        names = await _repo_names(ctx.session, list(weekly))
        band_link = "/repos/"
    totals = {
        i: sum(v.get("significance", 0.0) for v in w.values())
        for i, w in weekly.items()
    }
    ranked = sorted(totals, key=lambda i: totals[i], reverse=True)
    top = ranked[:8]
    rest = ranked[8:]
    weeks = _weeks(ctx.start, ctx.end)
    series_names = [names[i] for i in top] + (["Others"] if rest else [])
    series_values = [_series(weekly.get(i, {}), weeks, "significance") for i in top]
    if rest:
        series_values.append(
            [
                round(
                    sum(
                        weekly.get(i, {}).get(w, {}).get("significance", 0.0)
                        for i in rest
                    ),
                    2,
                )
                for w in weeks
            ]
        )
    chart = _weekly_line_chart(
        series_names,
        series_values,
        weeks,
        axis="Weekly significance",
        description=f"Weekly composition of {subject}, {ctx.period_label}",
        stacked=True,
    )
    grand_total = sum(totals.values())
    rows = [
        [
            Cell(names[i], link=f"{band_link}{i}"),
            Cell(_fmt(totals[i], "significance"), num=True),
            Cell(_pct(totals[i] / grand_total) if grand_total > 0 else "0%", num=True),
        ]
        for i in ranked
    ]
    facts = []
    if ranked:
        share = totals[ranked[0]] / grand_total if grand_total > 0 else 0.0
        noun = "contributor" if subject_kind == "repo" else "repository"
        facts.append(
            f"The largest {noun}, {names[ranked[0]]}, carries {_pct(share)} of"
            f" {subject}'s significance across {len(ranked)} in total."
        )
    context = _base_context(ctx, op, facts)
    context["subject"] = subject
    context["bands"] = [
        {"name": names[i], "significance": round(totals[i], 1)} for i in ranked[:10]
    ]
    return BlockResult(
        op=op.key,
        label=op.label,
        sentence=f"Composition of {subject}",
        description=op.description,
        tables=[
            BlockTable(
                "Totals per band",
                [
                    ("Person" if subject_kind == "repo" else "Repo",),
                    ("Significance", "num"),
                    ("Share", "num"),
                ],
                rows,
            )
        ],
        charts=[chart],
        context=context,
    )


async def _composition_repo(ctx: _Ctx) -> BlockResult:
    return await _composition(ctx, op_key="composition_repo", subject_kind="repo")


async def _composition_person(ctx: _Ctx) -> BlockResult:
    return await _composition(ctx, op_key="composition_person", subject_kind="person")


async def _overlap_people(ctx: _Ctx) -> BlockResult:
    op = OPS["overlap_people"]
    ids = _ids(ctx.block, "people")
    names = await _person_names(ctx.session, ids)
    splits = {}
    for pid in ids:
        splits[pid] = {
            s.repo_id: s
            for s in await stats.person_repo_split(
                ctx.session, person_id=pid, orgs=ctx.orgs, start=ctx.start, end=ctx.end
            )
        }
    common = set.intersection(*(set(s) for s in splits.values())) if splits else set()
    ordered = sorted(
        common,
        key=lambda r: sum(splits[p][r].significance for p in ids),
        reverse=True,
    )
    rows = [
        [
            Cell(
                f"{splits[ids[0]][r].org_login}/{splits[ids[0]][r].name}",
                link=f"/repos/{r}",
            )
        ]
        + [Cell(_fmt(splits[p][r].significance, "significance"), num=True) for p in ids]
        for r in ordered
    ]
    facts = [
        f"{len(common)} repositories saw activity from every one of"
        f" {', '.join(names[p] for p in ids)}."
    ]
    context = _base_context(ctx, op, facts)
    context["people"] = [names[p] for p in ids]
    context["common_repos"] = [
        f"{splits[ids[0]][r].org_login}/{splits[ids[0]][r].name}" for r in ordered[:10]
    ]
    return BlockResult(
        op=op.key,
        label=op.label,
        sentence=f"Repositories shared by {', '.join(names[p] for p in ids)}",
        description=op.description,
        tables=[
            BlockTable(
                "Repositories where all were active",
                [("Repo",)] + [(names[p], "num", "significance") for p in ids],
                rows,
            )
        ],
        context=context,
    )


async def _overlap_repos(ctx: _Ctx) -> BlockResult:
    op = OPS["overlap_repos"]
    ids = _ids(ctx.block, "repos")
    names = await _repo_names(ctx.session, ids)
    boards = {}
    for rid in ids:
        boards[rid] = {
            s.person_id: s
            for s in await stats.person_leaderboard(
                ctx.session, orgs=ctx.orgs, start=ctx.start, end=ctx.end, repo_ids=[rid]
            )
        }
    common = set.intersection(*(set(b) for b in boards.values())) if boards else set()
    ordered = sorted(
        common,
        key=lambda p: sum(boards[r][p].significance for r in ids),
        reverse=True,
    )
    rows = [
        [Cell(boards[ids[0]][p].display_name, link=f"/people/{p}")]
        + [Cell(_fmt(boards[r][p].significance, "significance"), num=True) for r in ids]
        for p in ordered
    ]
    facts = [
        f"{len(common)} people were active in every one of"
        f" {', '.join(names[r] for r in ids)}."
    ]
    context = _base_context(ctx, op, facts)
    context["repos"] = [names[r] for r in ids]
    context["common_people"] = [boards[ids[0]][p].display_name for p in ordered[:10]]
    return BlockResult(
        op=op.key,
        label=op.label,
        sentence=f"People present in {', '.join(names[r] for r in ids)}",
        description=op.description,
        tables=[
            BlockTable(
                "People active in all of them",
                [("Person",)] + [(names[r], "num", "significance") for r in ids],
                rows,
            )
        ],
        context=context,
    )


async def _handoff(ctx: _Ctx) -> BlockResult:
    op = OPS["handoff"]
    rid = _id(ctx.block, "repo")
    repo_name = (await _repo_names(ctx.session, [rid]))[rid]
    mid = ctx.start + (ctx.end - ctx.start) / 2
    halves = []
    for h_start, h_end in ((ctx.start, mid), (mid, ctx.end)):
        board = await stats.person_leaderboard(
            ctx.session, orgs=ctx.orgs, start=h_start, end=h_end, repo_ids=[rid]
        )
        total = sum(s.significance for s in board) or 1.0
        halves.append(
            {s.person_id: (s.display_name, s.significance / total) for s in board}
        )
    first, second = halves
    everyone = set(first) | set(second)
    shifts = []
    for pid in everyone:
        name = (first.get(pid) or second.get(pid))[0]  # type: ignore[index]
        share_1 = first.get(pid, (name, 0.0))[1]
        share_2 = second.get(pid, (name, 0.0))[1]
        shifts.append((pid, name, share_1, share_2, share_2 - share_1))
    shifts.sort(key=lambda s: abs(s[4]), reverse=True)
    rows = [
        [
            Cell(name, link=f"/people/{pid}"),
            Cell(_pct(share_1), num=True),
            Cell(_pct(share_2), num=True),
            Cell(f"{'+' if delta >= 0 else ''}{round(delta * 100)} pts", num=True),
        ]
        for pid, name, share_1, share_2, delta in shifts
    ]
    facts = []
    gainers = [s for s in shifts if s[4] > 0.15]
    losers = [s for s in shifts if s[4] < -0.15]
    if gainers or losers:
        if gainers:
            facts.append(
                f"{gainers[0][1]} picked up {round(gainers[0][4] * 100)} points of"
                f" {repo_name}'s work share between the halves of the period."
            )
        if losers:
            facts.append(
                f"{losers[0][1]} handed off {abs(round(losers[0][4] * 100))} points"
                " of work share."
            )
    else:
        facts.append(
            f"No contributor's share of {repo_name} moved more than 15 points"
            " between the halves of the period."
        )
    context = _base_context(ctx, op, facts)
    context["repo"] = repo_name
    context["shifts"] = [
        {"name": name, "share_shift_pts": round(delta * 100)}
        for _pid, name, _s1, _s2, delta in shifts[:10]
    ]
    return BlockResult(
        op=op.key,
        label=op.label,
        sentence=f"Handoff detection in {repo_name}",
        description=op.description,
        tables=[
            BlockTable(
                "Share of the repository's significance, first vs second half",
                [
                    ("Person",),
                    ("First half", "num"),
                    ("Second half", "num"),
                    ("Shift", "num"),
                ],
                rows,
            )
        ],
        context=context,
    )


async def _review_graph(ctx: _Ctx) -> BlockResult:
    op = OPS["review_graph"]
    ids = _ids(ctx.block, "people")
    names = await _person_names(ctx.session, ids)
    counts = await _review_pairs(ctx, ids)
    peak = max(counts.values(), default=0)
    rows = []
    for reviewer in ids:
        cells = [Cell(names[reviewer])]
        for author in ids:
            if reviewer == author:
                cells.append(Cell("·", num=True))
                continue
            n = counts.get((reviewer, author), 0)
            cells.append(Cell(str(n), num=True, heat=(n / peak) if peak > 0 else 0.0))
        rows.append(cells)
    total = sum(counts.values())
    facts = [f"{total} reviews were exchanged inside this set of {len(ids)} people."]
    if peak > 0:
        (top_reviewer, top_author), top_count = max(
            counts.items(), key=lambda kv: kv[1]
        )
        facts.append(
            f"The strongest edge is {names[top_reviewer]} reviewing"
            f" {names[top_author]} ({top_count} reviews)."
        )
    context = _base_context(ctx, op, facts)
    context["people"] = [names[p] for p in ids]
    context["edges"] = [
        {"reviewer": names[r], "author": names[a], "reviews": n}
        for (r, a), n in sorted(counts.items(), key=lambda kv: kv[1], reverse=True)[:15]
    ]
    return BlockResult(
        op=op.key,
        label=op.label,
        sentence=f"Review graph of {', '.join(names[p] for p in ids)}",
        description=op.description,
        tables=[
            BlockTable(
                "Reviews given (rows) on pull requests by (columns)",
                [("Reviewer",)] + [(names[p], "num") for p in ids],
                rows,
            )
        ],
        context=context,
    )


async def _volatility(ctx: _Ctx) -> BlockResult:
    op = OPS["volatility"]
    entity = str(ctx.block["entity"])
    metric = str(ctx.block["metric"])
    metric_label = _METRIC_LABELS[metric]
    if entity == "people":
        ids = _ids(ctx.block, "people")
        names = await _person_names(ctx.session, ids)
        weekly = await stats.weekly_by_entity(
            ctx.session,
            orgs=ctx.orgs,
            start=ctx.start,
            end=ctx.end,
            by="person",
            person_ids=ids,
        )
        link_base = "/people/"
    else:
        ids = _ids(ctx.block, "repos")
        names = await _repo_names(ctx.session, ids)
        weekly = await stats.weekly_by_entity(
            ctx.session,
            orgs=ctx.orgs,
            start=ctx.start,
            end=ctx.end,
            by="repo",
            repo_ids=ids,
        )
        link_base = "/repos/"
    weeks = _weeks(ctx.start, ctx.end)
    rows = []
    readings = {}
    for i in ids:
        values = _series(weekly.get(i, {}), weeks, metric)
        mean, std = _mean_std(values)
        cov = std / mean if mean > 0 else 0.0
        reading = (
            "no activity"
            if mean == 0
            else "steady"
            if cov < 0.6
            else "bursty"
            if cov > 1.2
            else "variable"
        )
        readings[names[i]] = {"variation": round(cov, 2), "reading": reading}
        rows.append(
            [
                Cell(names[i], link=f"{link_base}{i}"),
                Cell(_fmt(mean, metric), num=True),
                Cell(_fmt(std, "significance"), num=True),
                Cell(f"{cov:.2f}", num=True),
                Cell(reading),
            ]
        )
    chart = _weekly_line_chart(
        [names[i] for i in ids],
        [_series(weekly.get(i, {}), weeks, metric) for i in ids],
        weeks,
        axis=f"Weekly {metric_label.lower()}",
        description=f"Weekly {metric_label.lower()} rhythm, {ctx.period_label}",
    )
    bursty = [n for n, r in readings.items() if r["reading"] == "bursty"]
    steady = [n for n, r in readings.items() if r["reading"] == "steady"]
    facts = []
    if steady:
        facts.append(f"Steady output: {', '.join(steady)}.")
    if bursty:
        facts.append(f"Bursty output: {', '.join(bursty)}.")
    if not facts:
        facts.append("Every selected entity shows moderate weekly variation.")
    context = _base_context(ctx, op, facts)
    context["metric"] = metric_label
    context["readings"] = readings
    return BlockResult(
        op=op.key,
        label=op.label,
        sentence=f"Volatility of {metric_label.lower()} for {', '.join(names[i] for i in ids)}",
        description=op.description,
        tables=[
            BlockTable(
                "Weekly rhythm",
                [
                    ("Person" if entity == "people" else "Repo",),
                    ("Weekly average", "num"),
                    ("Weekly deviation", "num"),
                    ("Variation", "num"),
                    ("Reading",),
                ],
                rows,
            )
        ],
        charts=[chart],
        context=context,
    )


_HANDLERS = {
    "compare_people": _compare_people,
    "compare_repos": _compare_repos,
    "compare_groups": _compare_groups,
    "versus_scope": _versus_scope,
    "correlate_person_repo": _correlate_person_repo,
    "correlate_people": _correlate_people,
    "correlate_repos": _correlate_repos,
    "rank": _rank,
    "evolution": _evolution,
    "composition_repo": _composition_repo,
    "composition_person": _composition_person,
    "overlap_people": _overlap_people,
    "overlap_repos": _overlap_repos,
    "handoff": _handoff,
    "review_graph": _review_graph,
    "volatility": _volatility,
}
