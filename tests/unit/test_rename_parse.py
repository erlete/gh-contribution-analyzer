from gca.sync.gitrepo import parse_rename


def test_plain_path() -> None:
    assert parse_rename("src/app.py") == ("src/app.py", None)


def test_brace_rename() -> None:
    assert parse_rename("src/{old => new}/app.py") == (
        "src/new/app.py",
        "src/old/app.py",
    )


def test_brace_rename_file() -> None:
    assert parse_rename("src/{a.py => b.py}") == ("src/b.py", "src/a.py")


def test_brace_rename_to_root() -> None:
    assert parse_rename("{src => }/app.py") == ("app.py", "src/app.py")


def test_bare_rename() -> None:
    assert parse_rename("app.py => core.py") == ("core.py", "app.py")
