"""Merge suggestion scoring and generation.

Signals, strongest first:
- identical email on both persons
- GitHub noreply email whose embedded login matches the other person's login
- identical email local part (ignoring generic mailbox names)
- normalized full-name similarity
- login equal to a name with spaces removed
"""

import re
from collections import Counter
from dataclasses import dataclass, field
from difflib import SequenceMatcher

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from gca.identity.resolver import normalize_text
from gca.models import Identity, MergeSuggestion, Person

_NOREPLY_RE = re.compile(r"^(?:\d+\+)?([a-z0-9-]+)@users\.noreply\.github\.com$")
_GENERIC_LOCALPARTS = {
    "admin",
    "contact",
    "dev",
    "developer",
    "hello",
    "info",
    "mail",
    "me",
    "noreply",
    "no-reply",
    "root",
    "support",
    "team",
    "test",
}
SUGGESTION_THRESHOLD = 0.55
SHARED_NAME_OWNER_CAP = 2


@dataclass
class PersonView:
    id: int
    display_name: str
    names: set[str] = field(default_factory=set)
    emails: set[str] = field(default_factory=set)
    logins: set[str] = field(default_factory=set)


def _noreply_login(email: str) -> str | None:
    match = _NOREPLY_RE.match(email)
    return match.group(1) if match else None


def _local_part(email: str) -> str:
    return email.split("@", 1)[0] if "@" in email else ""


def _name_similarity(a: PersonView, b: PersonView) -> float:
    best = 0.0
    for name_a in a.names:
        for name_b in b.names:
            tokens_a = " ".join(sorted(name_a.split()))
            tokens_b = " ".join(sorted(name_b.split()))
            if not tokens_a or not tokens_b:
                continue
            best = max(best, SequenceMatcher(None, tokens_a, tokens_b).ratio())
    return best


def score_pair(a: PersonView, b: PersonView) -> tuple[float, list[str]]:
    contributions: list[tuple[float, str]] = []

    shared_emails = {e for e in a.emails & b.emails if e and _noreply_login(e) is None}
    for email in sorted(shared_emails):
        contributions.append((1.0, f"shared email {email}"))

    for view, other in ((a, b), (b, a)):
        for email in sorted(view.emails):
            login = _noreply_login(email)
            if login and login in other.logins:
                contributions.append((0.95, f"noreply email matches login {login}"))

    locals_a = {
        _local_part(e)
        for e in a.emails
        if _noreply_login(e) is None and len(_local_part(e)) >= 4
    }
    locals_b = {
        _local_part(e)
        for e in b.emails
        if _noreply_login(e) is None and len(_local_part(e)) >= 4
    }
    for lp in sorted((locals_a & locals_b) - _GENERIC_LOCALPARTS):
        contributions.append((0.6, f"shared email local part {lp}"))

    similarity = _name_similarity(a, b)
    if similarity >= 0.8:
        contributions.append((0.5 * similarity, f"similar names ({similarity:.2f})"))

    squashed_names_a = {n.replace(" ", "") for n in a.names}
    squashed_names_b = {n.replace(" ", "") for n in b.names}
    if (a.logins & squashed_names_b) or (b.logins & squashed_names_a):
        contributions.append((0.4, "login matches name"))

    if not contributions:
        return 0.0, []
    score = 1.0
    for value, _ in contributions:
        score *= 1.0 - value
    score = 1.0 - score
    reasons = [reason for _, reason in sorted(contributions, reverse=True)][:5]
    return min(score, 1.0), reasons


async def load_person_views(session: AsyncSession) -> list[PersonView]:
    rows = (
        await session.execute(
            sa.select(
                Person.id,
                Person.display_name,
                Identity.name_norm,
                Identity.email_norm,
                Identity.login_norm,
            ).join(Identity, Identity.person_id == Person.id)
        )
    ).all()
    views: dict[int, PersonView] = {}
    for row in rows:
        view = views.setdefault(
            row.id, PersonView(id=row.id, display_name=row.display_name)
        )
        if row.name_norm:
            view.names.add(row.name_norm)
        if row.email_norm:
            view.emails.add(row.email_norm)
        if row.login_norm:
            view.logins.add(row.login_norm)
    for view in views.values():
        if normalize_text(view.display_name):
            view.names.add(normalize_text(view.display_name))
    result = list(views.values())
    _prune_shared_names(result)
    return result


def _prune_shared_names(views: list[PersonView]) -> None:
    """Drop names carried by many persons from scoring.

    A name present on more than SHARED_NAME_OWNER_CAP persons is machine or
    shared-account naming (a bot author, a service login absorbed into
    several real people), not identity evidence. Keeping it would pair every
    carrier with every other carrier even though the accounts are
    independent. Emails are never pruned: one human committing under several
    name variants legitimately shares one email across many persons, and
    that signal must keep working."""
    owners: Counter[str] = Counter()
    for view in views:
        owners.update(view.names)
    for view in views:
        view.names = {n for n in view.names if owners[n] <= SHARED_NAME_OWNER_CAP}


async def _existing_pairs(session: AsyncSession) -> set[tuple[int, int]]:
    return {
        (row.person_a_id, row.person_b_id)
        for row in (
            await session.execute(
                sa.select(MergeSuggestion.person_a_id, MergeSuggestion.person_b_id)
            )
        ).all()
    }


async def generate(
    session: AsyncSession, threshold: float = SUGGESTION_THRESHOLD
) -> tuple[int, int]:
    """Full scan: revalidate every pending suggestion against current data,
    then insert new pairs that score. Returns (created, removed). Dismissed
    suggestions are never resurrected and never deleted."""
    views = await load_person_views(session)
    by_id = {v.id: v for v in views}
    removed = 0
    pending = (
        (
            await session.execute(
                sa.select(MergeSuggestion).where(MergeSuggestion.status == "pending")
            )
        )
        .scalars()
        .all()
    )
    for row in pending:
        a = by_id.get(row.person_a_id)
        b = by_id.get(row.person_b_id)
        score, reasons = score_pair(a, b) if a and b else (0.0, [])
        if score < threshold:
            await session.delete(row)
            removed += 1
        else:
            row.score = round(score, 4)
            row.reasons = reasons
    await session.flush()
    existing_pairs = await _existing_pairs(session)
    created = 0
    for i, a in enumerate(views):
        for b in views[i + 1 :]:
            low, high = sorted((a.id, b.id))
            if (low, high) in existing_pairs:
                continue
            score, reasons = score_pair(a, b)
            if score < threshold:
                continue
            session.add(
                MergeSuggestion(
                    person_a_id=low,
                    person_b_id=high,
                    score=round(score, 4),
                    reasons=reasons,
                )
            )
            existing_pairs.add((low, high))
            created += 1
    await session.flush()
    return created, removed


async def generate_for_person(
    session: AsyncSession, person_id: int, threshold: float = SUGGESTION_THRESHOLD
) -> int:
    """Insert pending suggestions pairing one person against everyone else.

    Merges delete every suggestion that referenced the merged persons, so the
    surviving person is rescored right away: without this, a related pair
    (for example a second identity of the same human) would stay invisible
    until the next full scan.
    """
    views = await load_person_views(session)
    me = next((v for v in views if v.id == person_id), None)
    if me is None:
        return 0
    existing_pairs = await _existing_pairs(session)
    created = 0
    for other in views:
        if other.id == me.id:
            continue
        low, high = sorted((me.id, other.id))
        if (low, high) in existing_pairs:
            continue
        score, reasons = score_pair(me, other)
        if score < threshold:
            continue
        session.add(
            MergeSuggestion(
                person_a_id=low,
                person_b_id=high,
                score=round(score, 4),
                reasons=reasons,
            )
        )
        existing_pairs.add((low, high))
        created += 1
    await session.flush()
    return created
