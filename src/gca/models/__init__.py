"""SQLAlchemy models. Importing this package registers every table on Base."""

from gca.models.activity import Commit, CommitFile, FileClass, PullRequest, Review
from gca.models.ops import (
    Insight,
    JobLedger,
    Recipient,
    Report,
    ReportSchedule,
    Setting,
    SyncRun,
    schedule_recipients,
)
from gca.models.org import FilterMode, Org, OrgCredential, PersonFilter, RepoFilter
from gca.models.person import Identity, IdentityKind, MergeSuggestion, Person
from gca.models.repo import CloneStatus, Repo
from gca.models.rollup import PersonRepoDayStats

__all__ = [
    "Commit",
    "CommitFile",
    "FileClass",
    "PullRequest",
    "Review",
    "FilterMode",
    "Org",
    "OrgCredential",
    "PersonFilter",
    "RepoFilter",
    "Insight",
    "JobLedger",
    "Recipient",
    "Report",
    "ReportSchedule",
    "Setting",
    "SyncRun",
    "schedule_recipients",
    "Identity",
    "IdentityKind",
    "MergeSuggestion",
    "Person",
    "CloneStatus",
    "Repo",
    "PersonRepoDayStats",
]
