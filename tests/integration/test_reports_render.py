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
    from gca.reports.builder import render_pdf, trend_chart_data_uri

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
            "chart": trend_chart_data_uri(series),
            "narrative": "A <b>test</b> narrative & sample.",
        },
    )
    assert pdf.startswith(b"%PDF")
    assert len(pdf) > 5000
