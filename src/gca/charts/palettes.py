"""GENERATED FILE - DO NOT EDIT.

Source: design/tokens.json (regenerate with `python design/build_tokens.py`).
"""

CATEGORICAL: dict[int, list[str]] = {1: ['#d4bbff'], 2: ['#8a3ffc', '#08bdba'], 3: ['#8a3ffc', '#08bdba', '#bae6ff'], 4: ['#8a3ffc', '#08bdba', '#bae6ff', '#4589ff'], 5: ['#8a3ffc', '#08bdba', '#bae6ff', '#4589ff', '#ff7eb6'], 14: ['#8a3ffc', '#33b1ff', '#007d79', '#ff7eb6', '#fa4d56', '#fff1f1', '#6fdc8c', '#4589ff', '#d02670', '#d2a106', '#08bdba', '#bae6ff', '#ba4e00', '#d4bbff']}

SEQUENTIAL_BLUE: list[str] = ['#001141', '#001d6c', '#002d9c', '#0043ce', '#0f62fe', '#4589ff', '#78a9ff', '#a6c8ff', '#d0e2ff', '#edf5ff', '#ffffff']

DIVERGENT: list[str] = ['#fff1f1', '#ffd7d9', '#ffb3b8', '#ff8389', '#fa4d56', '#da1e28', '#a2191f', '#750e13', '#ffffff', '#003a6d', '#00539a', '#0072c3', '#1192e8', '#33b1ff', '#82cfff', '#bae6ff', '#e5f6ff']

AXIS = '#8d8d8d'
GRID = '#525252'
TICK_LABEL = '#c6c6c6'
TITLE = '#f4f4f4'
LEGEND_TEXT = '#c6c6c6'


def categorical_for(count: int) -> list[str]:
    """The palette engineered for this many data groups, in order."""
    for size in sorted(CATEGORICAL):
        if count <= size:
            return CATEGORICAL[size][:count] if size == 14 else CATEGORICAL[size]
    return CATEGORICAL[max(CATEGORICAL)]
