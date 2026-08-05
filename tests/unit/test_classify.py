import pytest

from gca.metrics.classify import class_weight, classify_path
from gca.models import FileClass


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("src/gca/main.py", FileClass.CODE),
        ("lib/util.rs", FileClass.CODE),
        ("tests/unit/test_x.py", FileClass.TESTS),
        ("src/app/__tests__/thing.test.ts", FileClass.TESTS),
        ("pkg/handler_test.go", FileClass.TESTS),
        ("README.md", FileClass.DOCS),
        ("docs/guide/index.html", FileClass.DOCS),
        ("LICENSE", FileClass.DOCS),
        ("pyproject.toml", FileClass.CONFIG),
        (".github/workflows/ci.yml", FileClass.CONFIG),
        ("Dockerfile", FileClass.CONFIG),
        ("package-lock.json", FileClass.GENERATED),
        ("uv.lock", FileClass.GENERATED),
        ("vendor/lib/x.go", FileClass.GENERATED),
        ("web/static/app.min.js", FileClass.GENERATED),
        ("app/migrations/0001_initial.py", FileClass.GENERATED),
        ("proto/api_pb2.py", FileClass.GENERATED),
    ],
)
def test_classify(path: str, expected: FileClass) -> None:
    assert classify_path(path) == expected


def test_weights_ordering() -> None:
    assert (
        class_weight(FileClass.CODE)
        > class_weight(FileClass.TESTS)
        > class_weight(FileClass.CONFIG)
        > class_weight(FileClass.DOCS)
        > class_weight(FileClass.GENERATED)
    )
