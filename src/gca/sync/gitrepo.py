"""Blobless bare mirror management and history extraction.

Clones are cache, never source of truth. Tokens reach git only through the
askpass helper via process environment; they are never embedded in URLs or
written to disk. Known parsing limitation, documented: file paths containing
" => " or newlines are not supported by the rename parser.
"""

import os
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

_RECORD_SEP = "\x01"
_FIELD_SEP = "\x02"


class GitError(RuntimeError):
    def __init__(self, message: str, stderr: str = "") -> None:
        super().__init__(f"{message}: {stderr.strip()}" if stderr else message)
        self.stderr = stderr


@dataclass
class RawFile:
    path: str
    old_path: str | None
    additions: int
    deletions: int


@dataclass
class RawCommit:
    oid: str
    parents: tuple[str, ...]
    author_name: str
    author_email: str
    authored_at: datetime
    committed_at: datetime
    subject: str
    files: list[RawFile] = field(default_factory=list)

    @property
    def is_merge(self) -> bool:
        return len(self.parents) > 1


def parse_rename(raw: str) -> tuple[str, str | None]:
    """Return (new_path, old_path) from a numstat rename path."""
    if "{" in raw and " => " in raw and "}" in raw:
        prefix, rest = raw.split("{", 1)
        inner, suffix = rest.split("}", 1)
        old_part, new_part = inner.split(" => ", 1)
        old = f"{prefix}{old_part}{suffix}".replace("//", "/").lstrip("/")
        new = f"{prefix}{new_part}{suffix}".replace("//", "/").lstrip("/")
        return new, old
    if " => " in raw:
        old, new = raw.split(" => ", 1)
        return new, old
    return raw, None


class GitMirror:
    def __init__(self, base_dir: str | Path, org_login: str, repo_name: str) -> None:
        self.path = Path(base_dir) / org_login.lower() / f"{repo_name.lower()}.git"

    def exists(self) -> bool:
        return (self.path / "HEAD").exists()

    def _run(
        self,
        *args: str,
        token: str | None = None,
        cwd: Path | None = None,
    ) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env["GIT_TERMINAL_PROMPT"] = "0"
        env["LC_ALL"] = "C.UTF-8"
        if token:
            env["GCA_GIT_TOKEN"] = token
            env.setdefault("GIT_ASKPASS", "/usr/local/bin/gca-askpass")
        else:
            env.pop("GCA_GIT_TOKEN", None)
        result = subprocess.run(
            ["git", "-c", "core.quotepath=off", *args],
            cwd=str(cwd) if cwd else None,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if result.returncode != 0:
            raise GitError(f"git {args[0]} failed", result.stderr)
        return result

    def clone_or_fetch(self, url: str, token: str | None = None) -> None:
        """Clone if missing, otherwise incremental fetch."""
        if self.exists():
            self.fetch(token)
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._run(
            "clone", "--bare", "--filter=blob:none", url, str(self.path), token=token
        )
        self._run(
            "config",
            "remote.origin.fetch",
            "+refs/heads/*:refs/heads/*",
            cwd=self.path,
        )

    def fetch(self, token: str | None = None) -> None:
        self._run("fetch", "origin", "--prune", token=token, cwd=self.path)

    def branch_tip(self, branch: str) -> str | None:
        try:
            result = self._run(
                "rev-parse", "--verify", f"refs/heads/{branch}", cwd=self.path
            )
        except GitError:
            return None
        return result.stdout.strip() or None

    def log_numstat(self, branch: str, since_oid: str | None = None) -> list[RawCommit]:
        """Commits reachable from `branch`, oldest first, with per-file stats."""
        target = f"refs/heads/{branch}"
        spec = f"{since_oid}..{target}" if since_oid else target
        pretty = (
            f"--pretty=format:{_RECORD_SEP}%H{_FIELD_SEP}%P{_FIELD_SEP}%an"
            f"{_FIELD_SEP}%ae{_FIELD_SEP}%aI{_FIELD_SEP}%cI{_FIELD_SEP}%s"
        )
        result = self._run(
            "log",
            "--reverse",
            "--numstat",
            "-M",
            pretty,
            spec,
            cwd=self.path,
        )
        return self._parse_log(result.stdout)

    @staticmethod
    def _parse_log(output: str) -> list[RawCommit]:
        commits: list[RawCommit] = []
        for record in output.split(_RECORD_SEP):
            record = record.strip("\n")
            if not record:
                continue
            lines = record.split("\n")
            header = lines[0].split(_FIELD_SEP)
            if len(header) != 7:
                continue
            oid, parents, name, email, authored, committed, subject = header
            commit = RawCommit(
                oid=oid,
                parents=tuple(p for p in parents.split(" ") if p),
                author_name=name,
                author_email=email,
                authored_at=datetime.fromisoformat(authored),
                committed_at=datetime.fromisoformat(committed),
                subject=subject[:300],
            )
            for line in lines[1:]:
                if not line.strip():
                    continue
                parts = line.split("\t", 2)
                if len(parts) != 3:
                    continue
                adds_raw, dels_raw, raw_path = parts
                additions = int(adds_raw) if adds_raw.isdigit() else 0
                deletions = int(dels_raw) if dels_raw.isdigit() else 0
                new_path, old_path = parse_rename(raw_path)
                commit.files.append(
                    RawFile(
                        path=new_path,
                        old_path=old_path,
                        additions=additions,
                        deletions=deletions,
                    )
                )
            commits.append(commit)
        return commits

    def repack(self) -> None:
        """Drop lazily fetched blob bodies, returning the clone to slim state."""
        self._run("repack", "-a", "-d", "--filter=blob:none", cwd=self.path)

    def remove(self) -> None:
        if self.path.exists():
            shutil.rmtree(self.path, ignore_errors=True)
