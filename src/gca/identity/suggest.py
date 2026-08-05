"""Merge suggestion scoring and generation.

Signals, strongest first:
- identical email on both persons
- GitHub noreply email whose embedded login matches the other person's login
- identical email local part (ignoring generic mailbox names)
- normalized full-name similarity
- login equal to a name with spaces removed
"""

import re
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
    return list(views.values())


async def generate(
    session: AsyncSession, threshold: float = SUGGESTION_THRESHOLD
) -> int:
    """Insert pending suggestions for every scoring pair. Returns new count."""
    views = await load_person_views(session)
    existing_pairs = {
        (row.person_a_id, row.person_b_id)
        for row in (
            await session.execute(
                sa.select(MergeSuggestion.person_a_id, MergeSuggestion.person_b_id)
            )
        ).all()
    }
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
    return created
