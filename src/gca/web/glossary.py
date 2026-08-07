"""Plain-English explanations surfaced as info tooltips across the app.

Single source of truth: templates read these through the `glossary` Jinja
global, so a metric means the same thing on every card, table header and
panel. Copy targets readers with no engineering background; keep each entry
one or two short sentences and free of jargon the entry itself does not
explain.
"""

GLOSSARY: dict[str, str] = {
    "drilldown": (
        "Details opens the actual commits and pull requests behind this"
        " row's numbers, each linking to GitHub."
    ),
    "activity_calendar": (
        "One cell per day, darker means more significant work that day."
        " Shading compares days within the shown period only, so even a"
        " quiet stretch shows its busiest days. Periods longer than a year"
        " show the most recent twelve months."
    ),
    # Core metrics
    "commits": (
        "Number of commits, that is individual saved batches of code"
        " changes, authored in the selected period."
    ),
    "additions": "Total lines of code added in the selected period.",
    "deletions": "Total lines of code removed in the selected period.",
    "churn": (
        "Lines that were deleted soon after being written, within roughly"
        " three weeks. High churn means recent work is being rewritten or"
        " thrown away."
    ),
    "churn_ratio": (
        "Churned lines as a share of lines added. 20% means one in five"
        " freshly written lines was rewritten or removed within roughly"
        " three weeks."
    ),
    "self_churn": (
        "Churned lines where people rewrote or removed their own recent"
        " work. Usually normal iteration."
    ),
    "cross_churn": (
        "Churned lines where people rewrote or removed code somebody else"
        " wrote recently. Can signal rework or collaboration friction."
    ),
    "significance": (
        "Estimate of how much meaningful code a contribution carries. It"
        " grows with lines changed, weighs source code higher than"
        " configuration or generated files, discounts mass reformats and"
        " renames, and is scaled so a single huge commit cannot dominate."
    ),
    "prs_opened": "Pull requests, that is proposed sets of changes, opened in the selected period.",
    "prs_merged": "Pull requests accepted and merged into the codebase in the selected period.",
    "reviews": "Code reviews given on other people's pull requests in the selected period.",
    "percentile": (
        "Standing within everyone active in the current scope and period."
        " P75 means ahead of 75% of that population for the metric."
    ),
    "active_people": (
        "People with at least one recorded contribution in the selected"
        " period and organization scope."
    ),
    "active_repos": (
        "Repositories with at least one recorded contribution in the"
        " selected period and organization scope."
    ),
    "contributors": "How many different people contributed to this repository in the selected period.",
    # Panels and sections
    "trend": (
        "How activity evolved over the selected period: each point"
        " aggregates the work recorded on that date."
    ),
    "performance": (
        "The biggest movers against the previous period of the same"
        " length: the ten that gained the most and the ten that lost the"
        " most significance."
    ),
    "performance_all_time": (
        "All-time ranking by total significance. There is no previous"
        " period to compare against, so this shows the overall top"
        " contributors of recorded history."
    ),
    "percentile_position": (
        "Where this person stands among everyone active in the same scope"
        " and period, metric by metric. The bar fills with the share of"
        " the population they are ahead of."
    ),
    "per_repo_split": (
        "The same person's activity broken down by repository, so you can"
        " see where their work actually landed."
    ),
    "delta_vs_previous": (
        "Change compared with the previous period of the same length,"
        " immediately before the selected one."
    ),
    # Identity management
    "merge_suggestions": (
        "Pairs of entries that look like the same human under two names,"
        " for example a GitHub account and a bare git email. Merging"
        " credits all their activity to one person."
    ),
    "suggestion_why": (
        "The evidence behind the suggestion, such as a shared email,"
        " matching names, or a GitHub-verified commit."
    ),
    "suggestion_score": (
        "Confidence that both entries are the same person, from 0 to 1."
        " 0.95 and above comes from hard proof like a shared email;"
        " lower scores come from name similarity."
    ),
    "identities": (
        "The GitHub accounts and git author emails whose activity is"
        " credited to this person."
    ),
    "manual_merge": (
        "Combine two people into one when the automatic suggestions"
        " missed them. Pick who to keep; the other entry's identities and"
        " activity move onto the kept person."
    ),
    "unmerge": (
        "Detach one identity from this person into a separate person of"
        " its own, undoing a wrong merge."
    ),
    # Reports and operations
    "report_schedule": (
        "Which report flavors are generated and emailed automatically."
        " Each enabled combination runs at the end of its period."
    ),
    "report_recipients": "Everyone who receives scheduled and manually sent report emails.",
    "audit_log": (
        "Chronological record of everything the app and its operators"
        " did: syncs, merges, report runs, settings changes."
    ),
    "discovery": (
        "A discovery run asks GitHub for the organization's repositories"
        " and members, then fetches new commits, pull requests and"
        " reviews."
    ),
}
