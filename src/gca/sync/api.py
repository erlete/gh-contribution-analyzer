"""GitHub API client.

GraphQL is the primary transport (fine-grained PATs support it with
resource-owner matching). A REST fallback activates automatically if GraphQL
rejects the token, at the cost of one extra request per pull request for
detail stats. Incremental PR sync uses an updated-at watermark instead of
cursors so late merges and state changes are always picked up.
"""

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime

import httpx2

API_ROOT = "https://api.github.com"
_PAGE = 100
_PR_PAGE = 50
_SECONDARY_SLEEP_CAP = 120.0


class GitHubError(RuntimeError):
    pass


class AuthError(GitHubError):
    pass


class NotAnOrgError(GitHubError):
    pass


class RateLimitError(GitHubError):
    def __init__(self, message: str, reset_at: datetime | None = None) -> None:
        super().__init__(message)
        self.reset_at = reset_at


@dataclass
class OrgInfo:
    login: str
    name: str
    node_id: str
    avatar_url: str


@dataclass
class MemberInfo:
    login: str
    node_id: str


@dataclass
class RepoInfo:
    name: str
    node_id: str
    default_branch: str
    is_private: bool
    is_archived: bool
    is_fork: bool


@dataclass
class ReviewInfo:
    node_id: str
    login: str
    author_node_id: str | None
    state: str
    submitted_at: datetime | None


@dataclass
class PRInfo:
    number: int
    node_id: str
    author_login: str | None
    author_node_id: str | None
    title: str
    state: str
    created_at: datetime
    updated_at: datetime
    merged_at: datetime | None
    closed_at: datetime | None
    additions: int
    deletions: int
    changed_files: int
    reviews: list[ReviewInfo] = field(default_factory=list)


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _reset_from_headers(headers: httpx2.Headers) -> datetime | None:
    raw = headers.get("x-ratelimit-reset")
    if raw and raw.isdigit():
        return datetime.fromtimestamp(int(raw), tz=UTC)
    return None


_ORG_QUERY = """
query($login: String!) {
  organization(login: $login) { login name id avatarUrl }
  repositoryOwner(login: $login) { __typename }
}
"""

_REPOS_QUERY = """
query($login: String!, $cursor: String) {
  organization(login: $login) {
    repositories(first: 100, after: $cursor, orderBy: {field: NAME, direction: ASC}) {
      nodes {
        name
        id
        isPrivate
        isArchived
        isFork
        defaultBranchRef { name }
      }
      pageInfo { hasNextPage endCursor }
    }
  }
}
"""

_MEMBERS_QUERY = """
query($login: String!, $cursor: String) {
  organization(login: $login) {
    membersWithRole(first: 100, after: $cursor) {
      nodes { login id }
      pageInfo { hasNextPage endCursor }
    }
  }
}
"""

_PRS_QUERY = """
query($owner: String!, $name: String!, $cursor: String) {
  repository(owner: $owner, name: $name) {
    pullRequests(
      first: 50,
      after: $cursor,
      orderBy: {field: UPDATED_AT, direction: DESC},
      states: [OPEN, CLOSED, MERGED]
    ) {
      nodes {
        number
        id
        title
        state
        createdAt
        updatedAt
        mergedAt
        closedAt
        additions
        deletions
        changedFiles
        author { login ... on User { id } }
        reviews(first: 100) {
          nodes {
            id
            state
            submittedAt
            author { login ... on User { id } }
          }
        }
      }
      pageInfo { hasNextPage endCursor }
    }
  }
}
"""


class GitHubClient:
    def __init__(
        self,
        token: str,
        *,
        base_url: str = API_ROOT,
        transport: httpx2.AsyncBaseTransport | None = None,
    ) -> None:
        self._client = httpx2.AsyncClient(
            base_url=base_url,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "gh-contribution-analyzer",
            },
            timeout=30.0,
            transport=transport,
        )
        self.use_rest = False
        self.rate_snapshot: dict[str, str] = {}

    async def close(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> GitHubClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    def _note_rate(self, response: httpx2.Response) -> None:
        for key in ("x-ratelimit-remaining", "x-ratelimit-limit", "x-ratelimit-reset"):
            value = response.headers.get(key)
            if value:
                self.rate_snapshot[key] = value

    async def _request(
        self, method: str, url: str, *, json: object | None = None, retry: int = 2
    ) -> httpx2.Response:
        for attempt in range(retry + 1):
            response = await self._client.request(method, url, json=json)
            self._note_rate(response)
            if response.status_code in (502, 503, 504) and attempt < retry:
                await asyncio.sleep(2.0 * (attempt + 1))
                continue
            if response.status_code == 401:
                raise AuthError("github rejected the token (401)")
            if response.status_code in (403, 429):
                remaining = response.headers.get("x-ratelimit-remaining")
                if remaining == "0":
                    raise RateLimitError(
                        "primary rate limit exhausted",
                        _reset_from_headers(response.headers),
                    )
                retry_after = response.headers.get("retry-after")
                if retry_after and attempt < retry:
                    await asyncio.sleep(min(float(retry_after), _SECONDARY_SLEEP_CAP))
                    continue
                raise GitHubError(
                    f"github request forbidden ({response.status_code}): "
                    f"{response.text[:200]}"
                )
            return response
        return response

    async def graphql(self, query: str, variables: dict[str, object]) -> dict:  # type: ignore[type-arg]
        response = await self._request(
            "POST", "/graphql", json={"query": query, "variables": variables}
        )
        if response.status_code != 200:
            raise GitHubError(f"graphql http {response.status_code}")
        payload = response.json()
        errors = payload.get("errors") or []
        for error in errors:
            etype = error.get("type", "")
            if etype == "RATE_LIMITED":
                raise RateLimitError(
                    "graphql rate limit exhausted",
                    _reset_from_headers(response.headers),
                )
            if etype in ("FORBIDDEN", "INSUFFICIENT_SCOPES"):
                raise AuthError(error.get("message", "graphql forbidden"))
        data = payload.get("data")
        if data is None:
            messages = "; ".join(e.get("message", "?") for e in errors)
            raise GitHubError(f"graphql error: {messages or 'no data'}")
        return dict(data)

    # -- validation and discovery ------------------------------------------

    async def validate_org(self, login: str) -> OrgInfo:
        try:
            data = await self.graphql(_ORG_QUERY, {"login": login})
        except AuthError:
            self.use_rest = True
            return await self._validate_org_rest(login)
        org = data.get("organization")
        if org:
            return OrgInfo(
                login=org["login"],
                name=org.get("name") or org["login"],
                node_id=org["id"],
                avatar_url=org.get("avatarUrl") or "",
            )
        owner = data.get("repositoryOwner")
        if owner and owner.get("__typename") == "User":
            raise NotAnOrgError(
                f"{login} is a user account; only organizations are supported"
            )
        raise GitHubError(
            f"organization {login} not found or not visible to this token"
        )

    async def _validate_org_rest(self, login: str) -> OrgInfo:
        response = await self._request("GET", f"/orgs/{login}")
        if response.status_code == 404:
            user = await self._request("GET", f"/users/{login}")
            if user.status_code == 200 and user.json().get("type") == "User":
                raise NotAnOrgError(
                    f"{login} is a user account; only organizations are supported"
                )
            raise GitHubError(
                f"organization {login} not found or not visible to this token"
            )
        if response.status_code != 200:
            raise GitHubError(f"org lookup failed ({response.status_code})")
        data = response.json()
        return OrgInfo(
            login=data["login"],
            name=data.get("name") or data["login"],
            node_id=data.get("node_id", ""),
            avatar_url=data.get("avatar_url", ""),
        )

    async def list_repos(self, login: str) -> list[RepoInfo]:
        if self.use_rest:
            return await self._list_repos_rest(login)
        repos: list[RepoInfo] = []
        cursor: str | None = None
        while True:
            data = await self.graphql(_REPOS_QUERY, {"login": login, "cursor": cursor})
            org = data.get("organization")
            if not org:
                raise GitHubError(f"organization {login} vanished during discovery")
            block = org["repositories"]
            for node in block["nodes"]:
                default_ref = node.get("defaultBranchRef") or {}
                repos.append(
                    RepoInfo(
                        name=node["name"],
                        node_id=node["id"],
                        default_branch=default_ref.get("name") or "main",
                        is_private=node["isPrivate"],
                        is_archived=node["isArchived"],
                        is_fork=node["isFork"],
                    )
                )
            info = block["pageInfo"]
            if not info["hasNextPage"]:
                return repos
            cursor = info["endCursor"]

    async def _list_repos_rest(self, login: str) -> list[RepoInfo]:
        repos: list[RepoInfo] = []
        page = 1
        while True:
            response = await self._request(
                "GET", f"/orgs/{login}/repos?per_page={_PAGE}&type=all&page={page}"
            )
            if response.status_code != 200:
                raise GitHubError(f"repo listing failed ({response.status_code})")
            batch = response.json()
            for item in batch:
                repos.append(
                    RepoInfo(
                        name=item["name"],
                        node_id=item.get("node_id", ""),
                        default_branch=item.get("default_branch") or "main",
                        is_private=item.get("private", False),
                        is_archived=item.get("archived", False),
                        is_fork=item.get("fork", False),
                    )
                )
            if len(batch) < _PAGE:
                return repos
            page += 1

    # -- members ------------------------------------------------------------

    _MEMBERS_FORBIDDEN = (
        "member listing forbidden: the token needs the organization Members "
        "read permission"
    )

    async def org_members(self, login: str) -> list[MemberInfo]:
        if self.use_rest:
            return await self._org_members_rest(login)
        members: list[MemberInfo] = []
        cursor: str | None = None
        while True:
            try:
                data = await self.graphql(
                    _MEMBERS_QUERY, {"login": login, "cursor": cursor}
                )
            except AuthError as exc:
                raise GitHubError(self._MEMBERS_FORBIDDEN) from exc
            org = data.get("organization")
            if not org:
                raise GitHubError(
                    f"organization {login} not visible for member listing"
                )
            block = org.get("membersWithRole")
            if block is None:
                raise GitHubError(self._MEMBERS_FORBIDDEN)
            for node in block["nodes"]:
                if node:
                    members.append(
                        MemberInfo(login=node["login"], node_id=node.get("id", ""))
                    )
            info = block["pageInfo"]
            if not info["hasNextPage"]:
                return members
            cursor = info["endCursor"]

    async def _org_members_rest(self, login: str) -> list[MemberInfo]:
        members: list[MemberInfo] = []
        page = 1
        while True:
            response = await self._request(
                "GET", f"/orgs/{login}/members?per_page={_PAGE}&page={page}"
            )
            if response.status_code != 200:
                raise GitHubError(
                    f"{self._MEMBERS_FORBIDDEN} (http {response.status_code})"
                )
            batch = response.json()
            for item in batch:
                members.append(
                    MemberInfo(login=item["login"], node_id=item.get("node_id", ""))
                )
            if len(batch) < _PAGE:
                return members
            page += 1

    # -- pull requests ------------------------------------------------------

    async def pull_requests_since(
        self, owner: str, repo: str, since: datetime | None
    ) -> list[PRInfo]:
        """PRs updated after `since`, newest-updated first.

        Repos with very heavy PR payloads can make the GraphQL resolver time
        out with a 502; those fall back to REST for this call only.
        """
        if self.use_rest:
            return await self._pull_requests_since_rest(owner, repo, since)
        try:
            return await self._pull_requests_since_graphql(owner, repo, since)
        except GitHubError as exc:
            if "502" in str(exc) or "504" in str(exc):
                return await self._pull_requests_since_rest(owner, repo, since)
            raise

    async def _pull_requests_since_graphql(
        self, owner: str, repo: str, since: datetime | None
    ) -> list[PRInfo]:
        result: list[PRInfo] = []
        cursor: str | None = None
        while True:
            data = await self.graphql(
                _PRS_QUERY, {"owner": owner, "name": repo, "cursor": cursor}
            )
            repository = data.get("repository")
            if not repository:
                return result
            block = repository["pullRequests"]
            for node in block["nodes"]:
                updated = _parse_dt(node["updatedAt"])
                if since is not None and updated is not None and updated <= since:
                    return result
                author = node.get("author") or {}
                created = _parse_dt(node["createdAt"])
                if created is None or updated is None:
                    continue
                reviews = [
                    ReviewInfo(
                        node_id=r["id"],
                        login=(r.get("author") or {}).get("login") or "",
                        author_node_id=(r.get("author") or {}).get("id"),
                        state=r.get("state", ""),
                        submitted_at=_parse_dt(r.get("submittedAt")),
                    )
                    for r in (node.get("reviews") or {}).get("nodes", [])
                ]
                result.append(
                    PRInfo(
                        number=node["number"],
                        node_id=node["id"],
                        author_login=author.get("login"),
                        author_node_id=author.get("id"),
                        title=node.get("title", ""),
                        state=node["state"].lower(),
                        created_at=created,
                        updated_at=updated,
                        merged_at=_parse_dt(node.get("mergedAt")),
                        closed_at=_parse_dt(node.get("closedAt")),
                        additions=node.get("additions", 0),
                        deletions=node.get("deletions", 0),
                        changed_files=node.get("changedFiles", 0),
                        reviews=reviews,
                    )
                )
            info = block["pageInfo"]
            if not info["hasNextPage"]:
                return result
            cursor = info["endCursor"]

    async def _pull_requests_since_rest(
        self, owner: str, repo: str, since: datetime | None
    ) -> list[PRInfo]:
        result: list[PRInfo] = []
        page = 1
        while True:
            response = await self._request(
                "GET",
                f"/repos/{owner}/{repo}/pulls"
                f"?state=all&sort=updated&direction=desc&per_page={_PAGE}&page={page}",
            )
            if response.status_code != 200:
                raise GitHubError(f"pr listing failed ({response.status_code})")
            batch = response.json()
            for item in batch:
                updated = _parse_dt(item.get("updated_at"))
                if since is not None and updated is not None and updated <= since:
                    return result
                detail_resp = await self._request(
                    "GET", f"/repos/{owner}/{repo}/pulls/{item['number']}"
                )
                if detail_resp.status_code != 200:
                    raise GitHubError(
                        f"pr detail failed ({detail_resp.status_code}) for #{item['number']}"
                    )
                detail = detail_resp.json()
                reviews_resp = await self._request(
                    "GET",
                    f"/repos/{owner}/{repo}/pulls/{item['number']}/reviews"
                    f"?per_page={_PAGE}",
                )
                reviews_raw = (
                    reviews_resp.json() if reviews_resp.status_code == 200 else []
                )
                author = item.get("user") or {}
                created = _parse_dt(item.get("created_at"))
                if created is None or updated is None:
                    continue
                merged_at = _parse_dt(item.get("merged_at"))
                state = "merged" if merged_at else item.get("state", "open")
                result.append(
                    PRInfo(
                        number=item["number"],
                        node_id=item.get("node_id", ""),
                        author_login=author.get("login"),
                        author_node_id=author.get("node_id"),
                        title=item.get("title", ""),
                        state=state,
                        created_at=created,
                        updated_at=updated,
                        merged_at=merged_at,
                        closed_at=_parse_dt(item.get("closed_at")),
                        additions=detail.get("additions", 0),
                        deletions=detail.get("deletions", 0),
                        changed_files=detail.get("changed_files", 0),
                        reviews=[
                            ReviewInfo(
                                node_id=r.get("node_id", ""),
                                login=(r.get("user") or {}).get("login") or "",
                                author_node_id=(r.get("user") or {}).get("node_id"),
                                state=r.get("state", ""),
                                submitted_at=_parse_dt(r.get("submitted_at")),
                            )
                            for r in reviews_raw
                        ],
                    )
                )
            if len(batch) < _PAGE:
                return result
            page += 1
