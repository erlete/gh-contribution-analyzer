"""Per-commit significance scoring.

significance = log1p(sum(class_weight * (additions + 0.5 * deletions))), with
mechanical commits (mass reformats, bulk renames) down-weighted and merge
commits scored zero. PR and review activity is reported separately, never
folded into this score.
"""

import math
from collections.abc import Sequence

from gca.metrics.classify import class_weight, classify_path
from gca.sync.gitrepo import RawFile

MECHANICAL_TOTAL_THRESHOLD = 2000
MECHANICAL_SYMMETRY = 0.8
MECHANICAL_RENAME_FILES = 50
MECHANICAL_RENAME_SHARE = 0.9
MECHANICAL_FACTOR = 0.2


def is_mechanical(files: Sequence[RawFile]) -> bool:
    if not files:
        return False
    additions = sum(f.additions for f in files)
    deletions = sum(f.deletions for f in files)
    total = additions + deletions
    if (
        total >= MECHANICAL_TOTAL_THRESHOLD
        and min(additions, deletions) / max(additions, deletions, 1)
        >= MECHANICAL_SYMMETRY
    ):
        return True
    pure_renames = sum(
        1 for f in files if f.old_path and f.additions == 0 and f.deletions == 0
    )
    return (
        len(files) >= MECHANICAL_RENAME_FILES
        and pure_renames / len(files) >= MECHANICAL_RENAME_SHARE
    )


def score_commit(files: Sequence[RawFile], *, is_merge: bool) -> tuple[float, bool]:
    """Return (significance, mechanical flag)."""
    if is_merge or not files:
        return 0.0, False
    mechanical = is_mechanical(files)
    mass = sum(
        class_weight(classify_path(f.path)) * (f.additions + 0.5 * f.deletions)
        for f in files
    )
    if mechanical:
        mass *= MECHANICAL_FACTOR
    return round(math.log1p(mass), 4), mechanical
