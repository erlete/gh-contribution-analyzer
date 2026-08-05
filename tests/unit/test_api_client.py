import json
from datetime import UTC, datetime
from typing import Any

import httpx2
import pytest

from gca.sync.api import (
    GitHubClient,
    NotAnOrgError,
    RateLimitError,
)


def _graphql_response(data: dict[str, Any]) -> httpx2.Response:
    return httpx2.Response(200, json={"data": data})


def _client(handler: Any) -> GitHubClient:
    return GitHubClient("token", transport=httpx2.MockTransport(handler))


async def test_validate_org_success() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        assert request.url.path == "/graphql"
        assert request.headers["authorization"] == "Bearer token"
        return _graphql_response(
            {
                "organization": {
                    "login": "acme",
                    "name": "Acme Corp",
                    "id": "O_1",
                    "avatarUrl": "https://a/x.png",
                },
                "repositoryOwner": {"__typename": "Organization"},
            }
        )

    async with _client(handler) as client:
        info = await client.validate_org("acme")
    assert info.name == "Acme Corp"
    assert info.node_id == "O_1"


async def test_validate_org_rejects_users() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return _graphql_response(
            {"organization": None, "repositoryOwner": {"__typename": "User"}}
        )

    async with _client(handler) as client:
        with pytest.raises(NotAnOrgError):
            await client.validate_org("someuser")


async def test_validate_org_falls_back_to_rest() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.path == "/graphql":
            return httpx2.Response(401, json={"message": "bad credentials"})
        if request.url.path == "/orgs/acme":
            return httpx2.Response(
                200, json={"login": "acme", "name": "Acme", "node_id": "O_1"}
            )
        raise AssertionError(f"unexpected {request.url.path}")

    async with _client(handler) as client:
        info = await client.validate_org("acme")
        assert client.use_rest is True
    assert info.login == "acme"


async def test_list_repos_paginates() -> None:
    calls: list[str | None] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content)
        cursor = body["variables"].get("cursor")
        calls.append(cursor)
        if cursor is None:
            return _graphql_response(
                {
                    "organization": {
                        "repositories": {
                            "nodes": [
                                {
                                    "name": "alpha",
                                    "id": "R_1",
                                    "isPrivate": True,
                                    "isArchived": False,
                                    "isFork": False,
                                    "defaultBranchRef": {"name": "main"},
                                }
                            ],
                            "pageInfo": {"hasNextPage": True, "endCursor": "C1"},
                        }
                    }
                }
            )
        return _graphql_response(
            {
                "organization": {
                    "repositories": {
                        "nodes": [
                            {
                                "name": "beta",
                                "id": "R_2",
                                "isPrivate": False,
                                "isArchived": True,
                                "isFork": False,
                                "defaultBranchRef": None,
                            }
                        ],
                        "pageInfo": {"hasNextPage": False, "endCursor": None},
                    }
                }
            }
        )

    async with _client(handler) as client:
        repos = await client.list_repos("acme")
    assert [r.name for r in repos] == ["alpha", "beta"]
    assert repos[1].default_branch == "main"
    assert calls == [None, "C1"]


def _pr_node(number: int, updated: str) -> dict[str, Any]:
    return {
        "number": number,
        "id": f"PR_{number}",
        "title": f"pr {number}",
        "state": "MERGED",
        "createdAt": "2024-01-01T00:00:00Z",
        "updatedAt": updated,
        "mergedAt": "2024-01-02T00:00:00Z",
        "closedAt": "2024-01-02T00:00:00Z",
        "additions": 10,
        "deletions": 2,
        "changedFiles": 1,
        "author": {"login": "alice", "id": "U_1"},
        "reviews": {
            "nodes": [
                {
                    "id": f"RV_{number}",
                    "state": "APPROVED",
                    "submittedAt": "2024-01-01T12:00:00Z",
                    "author": {"login": "bob", "id": "U_2"},
                }
            ]
        },
    }


async def test_pull_requests_watermark_stop() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return _graphql_response(
            {
                "repository": {
                    "pullRequests": {
                        "nodes": [
                            _pr_node(2, "2024-06-01T00:00:00Z"),
                            _pr_node(1, "2024-01-05T00:00:00Z"),
                        ],
                        "pageInfo": {"hasNextPage": True, "endCursor": "X"},
                    }
                }
            }
        )

    since = datetime(2024, 3, 1, tzinfo=UTC)
    async with _client(handler) as client:
        prs = await client.pull_requests_since("acme", "alpha", since)
    assert [p.number for p in prs] == [2]
    assert prs[0].reviews[0].login == "bob"
    assert prs[0].state == "merged"


async def test_rate_limit_exhausted() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            403,
            headers={
                "x-ratelimit-remaining": "0",
                "x-ratelimit-reset": "1750000000",
            },
            json={"message": "rate limited"},
        )

    async with _client(handler) as client:
        with pytest.raises(RateLimitError) as excinfo:
            await client.validate_org("acme")
    assert excinfo.value.reset_at is not None
    assert excinfo.value.reset_at.tzinfo is not None


async def test_org_members_paginates() -> None:
    calls = 0

    def handler(request: httpx2.Request) -> httpx2.Response:
        nonlocal calls
        calls += 1
        body = json.loads(request.content)
        cursor = body["variables"].get("cursor")
        if cursor is None:
            return _graphql_response(
                {
                    "organization": {
                        "membersWithRole": {
                            "nodes": [{"login": "jane", "id": "U_1"}],
                            "pageInfo": {"hasNextPage": True, "endCursor": "C1"},
                        }
                    }
                }
            )
        return _graphql_response(
            {
                "organization": {
                    "membersWithRole": {
                        "nodes": [{"login": "bob", "id": "U_2"}],
                        "pageInfo": {"hasNextPage": False, "endCursor": None},
                    }
                }
            }
        )

    async with _client(handler) as client:
        members = await client.org_members("acme")
    assert calls == 2
    assert [(m.login, m.node_id) for m in members] == [
        ("jane", "U_1"),
        ("bob", "U_2"),
    ]


async def test_org_members_permission_error_is_explicit() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return _graphql_response({"organization": {"membersWithRole": None}})

    from gca.sync.api import GitHubError

    async with _client(handler) as client:
        with pytest.raises(GitHubError, match="Members read permission"):
            await client.org_members("acme")


async def test_commit_authors_batches_and_resolves() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content)
        query = body["query"]
        assert 'c0: repository(owner: $owner, name: "core")' in query
        assert 'c1: repository(owner: $owner, name: "web")' in query
        return _graphql_response(
            {
                "c0": {
                    "object": {"author": {"user": {"login": "erlete-dlt", "id": "U_1"}}}
                },
                "c1": {"object": {"author": {"user": None}}},
            }
        )

    async with _client(handler) as client:
        resolved = await client.commit_authors(
            "acme",
            [
                ("paulo@corp.com", "core", "a" * 40),
                ("ghost@ext.org", "web", "b" * 40),
            ],
        )
    assert resolved == {"paulo@corp.com": ("erlete-dlt", "U_1")}


async def test_commit_authors_skips_invalid_lookups() -> None:
    async with _client(lambda request: pytest.fail("no request expected")) as client:
        resolved = await client.commit_authors(
            "acme",
            [
                ("x@y.z", 'bad"name', "a" * 40),
                ("x2@y.z", "core", "not-a-sha"),
            ],
        )
    assert resolved == {}


async def test_commit_authors_rest_fallback() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        assert request.url.path == "/repos/acme/core/commits/" + "a" * 40
        return httpx2.Response(
            200, json={"author": {"login": "erlete-dlt", "node_id": "U_1"}}
        )

    async with _client(handler) as client:
        client.use_rest = True
        resolved = await client.commit_authors(
            "acme", [("paulo@corp.com", "core", "a" * 40)]
        )
    assert resolved == {"paulo@corp.com": ("erlete-dlt", "U_1")}
