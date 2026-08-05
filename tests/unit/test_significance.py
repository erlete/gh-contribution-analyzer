import math

from gca.metrics.significance import is_mechanical, score_commit
from gca.sync.gitrepo import RawFile


def _f(path: str, add: int, dele: int, old: str | None = None) -> RawFile:
    return RawFile(path=path, old_path=old, additions=add, deletions=dele)


def test_merge_scores_zero() -> None:
    score, mechanical = score_commit([_f("a.py", 100, 0)], is_merge=True)
    assert score == 0.0
    assert mechanical is False


def test_code_beats_docs_at_same_size() -> None:
    code, _ = score_commit([_f("a.py", 100, 0)], is_merge=False)
    docs, _ = score_commit([_f("a.md", 100, 0)], is_merge=False)
    assert code > docs > 0


def test_log_damping() -> None:
    small, _ = score_commit([_f("a.py", 100, 0)], is_merge=False)
    huge, _ = score_commit([_f("a.py", 10000, 0)], is_merge=False)
    assert huge < small * 3
    assert math.isclose(small, math.log1p(100), rel_tol=1e-3)


def test_mechanical_reformat_detected() -> None:
    files = [_f("a.py", 1500, 1400)]
    assert is_mechanical(files) is True
    score, mechanical = score_commit(files, is_merge=False)
    assert mechanical is True
    plain, _ = score_commit([_f("a.py", 150, 5)], is_merge=False)
    assert score < math.log1p((1500 + 700) * 0.2) + 0.01


def test_mass_rename_detected() -> None:
    files = [_f(f"new/{i}.py", 0, 0, old=f"old/{i}.py") for i in range(60)]
    assert is_mechanical(files) is True


def test_generated_files_near_zero() -> None:
    lock, _ = score_commit([_f("uv.lock", 4000, 3000)], is_merge=False)
    code, _ = score_commit([_f("a.py", 4000, 3000)], is_merge=False)
    assert lock < code
    assert math.isclose(lock, math.log1p(0.05 * (4000 + 1500)), rel_tol=1e-3)
