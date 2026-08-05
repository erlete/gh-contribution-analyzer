"""File path classification for significance weighting."""

from pathlib import PurePosixPath

from gca.models import FileClass

CLASS_WEIGHTS: dict[FileClass, float] = {
    FileClass.CODE: 1.0,
    FileClass.TESTS: 0.7,
    FileClass.CONFIG: 0.5,
    FileClass.DOCS: 0.3,
    FileClass.GENERATED: 0.05,
}

_GENERATED_NAMES = {
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "uv.lock",
    "poetry.lock",
    "pipfile.lock",
    "cargo.lock",
    "composer.lock",
    "gemfile.lock",
    "go.sum",
    "flake.lock",
}
_GENERATED_DIRS = {
    "vendor",
    "vendors",
    "node_modules",
    "third_party",
    "dist",
    "build",
    "out",
    "__snapshots__",
    "migrations",
    ".yarn",
}
_GENERATED_SUFFIXES = (".min.js", ".min.css", ".map", ".snap", ".pb.go", "_pb2.py")
_DOC_EXTENSIONS = {".md", ".rst", ".adoc", ".txt"}
_DOC_DIRS = {"docs", "doc"}
_DOC_NAMES = {"license", "licence", "readme", "changelog", "authors", "notice"}
_CONFIG_EXTENSIONS = {
    ".yml",
    ".yaml",
    ".toml",
    ".ini",
    ".cfg",
    ".conf",
    ".json",
    ".env",
    ".properties",
    ".editorconfig",
}
_CONFIG_NAMES = {
    "dockerfile",
    "makefile",
    "justfile",
    ".gitignore",
    ".gitattributes",
    ".dockerignore",
}
_TEST_DIRS = {"tests", "test", "__tests__", "spec", "specs"}
_TEST_SUFFIXES = (
    "_test.py",
    "_test.go",
    "_test.rb",
    ".test.ts",
    ".test.tsx",
    ".test.js",
    ".test.jsx",
    ".spec.ts",
    ".spec.tsx",
    ".spec.js",
    ".spec.rb",
)


def classify_path(path: str) -> FileClass:
    pure = PurePosixPath(path.replace("\\", "/").lower())
    name = pure.name
    parts = set(pure.parts[:-1])

    if (
        name in _GENERATED_NAMES
        or parts & _GENERATED_DIRS
        or name.endswith(_GENERATED_SUFFIXES)
        or ".generated." in name
    ):
        return FileClass.GENERATED
    if parts & _TEST_DIRS or name.startswith("test_") or name.endswith(_TEST_SUFFIXES):
        return FileClass.TESTS
    if pure.suffix in _DOC_EXTENSIONS or parts & _DOC_DIRS or pure.stem in _DOC_NAMES:
        return FileClass.DOCS
    if pure.suffix in _CONFIG_EXTENSIONS or name in _CONFIG_NAMES:
        return FileClass.CONFIG
    return FileClass.CODE


def class_weight(file_class: FileClass) -> float:
    return CLASS_WEIGHTS[file_class]
