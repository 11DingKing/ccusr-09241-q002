"""重复建设识别：空间维度（同一网格被多方申报）与时间维度（跨季度重复申报）。

- 空间重复：两个候选工程覆盖同一网格，或候选工程覆盖现有能力已覆盖的网格；
- 时间重复：同一网格在不同申报季度被重复申报，或在现有能力投运之后仍被申报。
"""

from __future__ import annotations

from .models import LedgerState
from .quarters import quarter_key


def detect_duplicates(state: LedgerState) -> dict:
    projects = [state.projects[key] for key in sorted(state.projects)]
    capabilities = [state.capabilities[key] for key in sorted(state.capabilities)]

    spatial: list[dict] = []
    for left in range(len(projects)):
        for right in range(left + 1, len(projects)):
            first, second = projects[left], projects[right]
            shared = sorted(set(first.covers) & set(second.covers))
            if shared:
                spatial.append(
                    {
                        "kind": "project_pair",
                        "projects": [first.id, second.id],
                        "proposers": [first.proposer, second.proposer],
                        "grids": shared,
                        "declared_quarters": [first.declared_quarter, second.declared_quarter],
                    }
                )
    for project in projects:
        covered = set(project.covers)
        for capability in capabilities:
            shared = sorted(covered & set(capability.covers))
            if shared:
                spatial.append(
                    {
                        "kind": "project_vs_capability",
                        "project": project.id,
                        "capability": capability.id,
                        "grids": shared,
                        "capability_since": capability.since,
                        "declared_quarter": project.declared_quarter,
                    }
                )

    by_grid: dict[str, list] = {}
    for project in projects:
        for grid_id in project.covers:
            by_grid.setdefault(grid_id, []).append(project)

    temporal: list[dict] = []
    for grid_id in sorted(by_grid):
        declared = by_grid[grid_id]
        quarters = sorted({item.declared_quarter for item in declared}, key=quarter_key)
        if len(quarters) > 1:
            temporal.append(
                {
                    "kind": "redeclared_grid",
                    "grid": grid_id,
                    "declarations": [
                        {
                            "quarter": quarter,
                            "projects": sorted(item.id for item in declared if item.declared_quarter == quarter),
                        }
                        for quarter in quarters
                    ],
                }
            )
    for capability in capabilities:
        for grid_id in sorted(capability.covers):
            later = [
                item
                for item in by_grid.get(grid_id, [])
                if quarter_key(item.declared_quarter) >= quarter_key(capability.since)
            ]
            if later:
                temporal.append(
                    {
                        "kind": "redeclared_after_capability",
                        "grid": grid_id,
                        "capability": capability.id,
                        "capability_since": capability.since,
                        "projects": [
                            {"project": item.id, "declared_quarter": item.declared_quarter}
                            for item in sorted(later, key=lambda entry: entry.id)
                        ],
                    }
                )
    return {"spatial": spatial, "temporal": temporal}
