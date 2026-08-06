"""PDF rendering smoke test.

Skipped where the WeasyPrint system libraries (Pango) are missing, which is
the case on plain Windows hosts; CI and the container image have them.
"""

from datetime import date

import pytest

from gca.services.stats import DayPoint, PersonStat, RepoStat, Totals

pytestmark = pytest.mark.integration

try:
    import weasyprint  # noqa: F401
except Exception:  # OSError on hosts without Pango, not just ImportError
    pytest.skip("weasyprint system libraries unavailable", allow_module_level=True)


def test_overview_pdf_renders() -> None:
    from gca.reports.builder import render_pdf, trend_chart_svg

    series = [
        DayPoint(
            day=date(2026, 3, i + 1),
            commits=i,
            additions=i * 10,
            deletions=i,
            significance=float(i),
        )
        for i in range(10)
    ]
    person = PersonStat(person_id=1, display_name="Jane <script>alert(1)</script>")
    person.commits = 5
    person.significance = 12.5
    person.percentiles = {"significance": 0.9}
    repo = RepoStat(repo_id=1, name="core", org_login="acme")
    repo.commits = 5

    pdf = render_pdf(
        "overview.html",
        {
            "title": "Contribution overview, test",
            "period_label": "March 2026",
            "scope_label": "acme",
            "generated_at": "2026-03-31 00:15 UTC",
            "churn_window": 21,
            "totals": Totals(commits=5, additions=100, active_people=1),
            "people": [person],
            "repos": [repo],
            "chart": trend_chart_svg(series),
            "narrative": "A <b>test</b> narrative & sample.",
            "narrative_ai": True,
        },
    )
    assert pdf.startswith(b"%PDF")
    assert len(pdf) > 5000


def test_collection_pdf_renders_with_toc_and_sections() -> None:
    from gca.reports.builder import render_pdf

    jane = PersonStat(person_id=1, display_name="Jane Doe")
    jane.commits = 5
    jane.significance = 12.5
    jane.percentiles = {"significance": 0.9, "commits": 0.8}
    repo_totals = Totals(commits=9, additions=200, prs_merged=2, reviews=3)
    contributor = PersonStat(person_id=1, display_name="Jane Doe")
    contributor.commits = 9

    pdf = render_pdf(
        "collection.html",
        {
            "title": "Individual contributor report, test",
            "period_label": "March 2026",
            "scope_label": "acme",
            "generated_at": "2026-03-31 00:15 UTC",
            "churn_window": 21,
            "intro_line": "This document analyzes 2 subjects individually.",
            "totals": Totals(commits=14, additions=300, active_people=1),
            "narrative": "Overall narrative.",
            "narrative_ai": False,
            "population": 3,
            "sections": [
                {
                    "anchor": "p1",
                    "heading": "Jane Doe",
                    "kind": "person",
                    "me": jane,
                    "split": [],
                    "metric_rows": [("commits", "Commits", "5")],
                    "chart": None,
                    "narrative": "Jane's section narrative.",
                    "narrative_ai": True,
                },
                {
                    "anchor": "r1",
                    "heading": "acme/core",
                    "kind": "repo",
                    "totals": repo_totals,
                    "contributors": [contributor],
                    "chart": None,
                    "narrative": "Repo section narrative.",
                    "narrative_ai": False,
                },
            ],
        },
    )
    assert pdf.startswith(b"%PDF")
    assert len(pdf) > 5000
