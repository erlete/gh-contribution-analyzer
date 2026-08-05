from gca.ai.fallback import fallback_text


def test_dashboard_with_activity() -> None:
    text = fallback_text(
        "dashboard",
        {
            "period": "Last 30 days",
            "orgs": "acme",
            "totals": {
                "commits": 120,
                "additions": 4000,
                "deletions": 900,
                "churn_ratio": 0.12,
                "active_people": 6,
                "active_repos": 4,
                "prs_opened": 30,
                "prs_merged": 25,
                "reviews": 40,
            },
            "top_contributors": [
                {"name": "Jane", "commits": 50, "significance": 210.5}
            ],
            "top_repos": [{"name": "acme/api", "commits": 70, "contributors": 5}],
        },
    )
    assert "120" in text
    assert "Jane" in text
    assert "acme/api" in text
    assert "12%" in text


def test_dashboard_no_activity() -> None:
    text = fallback_text(
        "dashboard",
        {"period": "Last 7 days", "orgs": "acme", "totals": {"commits": 0}},
    )
    assert "No recorded activity" in text


def test_person_with_activity() -> None:
    text = fallback_text(
        "person",
        {
            "period": "Last 30 days",
            "person": "Jane Doe",
            "metrics": {
                "commits": 41,
                "additions": 1200,
                "deletions": 300,
                "churn_ratio": 0.08,
                "self_churn": 60,
                "cross_churn": 36,
                "prs_opened": 9,
                "prs_merged": 8,
                "reviews": 12,
            },
            "percentiles": {"significance": 0.93},
            "rank_by_significance": 2,
            "population": 25,
            "top_repos": [
                {"name": "acme/api", "significance": 88.1},
                {"name": "acme/web", "significance": 12.0},
            ],
        },
    )
    assert "Jane Doe" in text
    assert "2 of 25" in text
    assert "P93" in text
    assert "acme/api" in text
    assert "plus 1 more" in text


def test_person_no_activity() -> None:
    text = fallback_text(
        "person",
        {"period": "Last 7 days", "person": "Quiet Person", "metrics": {}},
    )
    assert "no recorded activity" in text


def test_repo_with_activity() -> None:
    text = fallback_text(
        "repo",
        {
            "period": "Last 90 days",
            "repo": "acme/api",
            "totals": {
                "commits": 300,
                "additions": 9000,
                "deletions": 2500,
                "churn_ratio": 0.2,
                "prs_merged": 45,
                "reviews": 80,
            },
            "contributor_count": 7,
            "top_contributors": [
                {"name": "Jane", "commits": 120, "significance": 300.2}
            ],
        },
    )
    assert "acme/api" in text
    assert "300" in text
    assert "7 contributors" in text
    assert "Jane" in text


def test_repo_no_activity() -> None:
    text = fallback_text(
        "repo",
        {"period": "Last 7 days", "repo": "acme/quiet", "totals": {}},
    )
    assert "no activity" in text


def test_unknown_kind_defaults_to_dashboard() -> None:
    text = fallback_text("nonsense", {"totals": {"commits": 0}})
    assert "No recorded activity" in text


def test_all_time_dashboard_never_mentions_previous_period() -> None:
    """All-time contexts carry no comparison keys; the statement talks about
    the whole history and never about a previous period."""
    text = fallback_text(
        "dashboard",
        {
            "period": "All time",
            "period_mode": "all time",
            "orgs": "acme",
            "totals": {
                "commits": 5000,
                "additions": 90000,
                "deletions": 40000,
                "churn_ratio": 0.15,
                "active_people": 40,
                "active_repos": 30,
                "prs_opened": 700,
                "prs_merged": 650,
                "reviews": 900,
            },
        },
    )
    assert "entire recorded history" in text
    assert "previous period" not in text


def test_all_time_person_never_mentions_previous_period() -> None:
    text = fallback_text(
        "person",
        {
            "period": "All time",
            "period_mode": "all time",
            "person": "Jane Doe",
            "metrics": {"commits": 900, "additions": 1000, "deletions": 100},
        },
    )
    assert "entire recorded history" in text
    assert "previous period" not in text
