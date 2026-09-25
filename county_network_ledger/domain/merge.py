"""方案合并：取并集后按预算约束与跨期依赖做确定性取舍，已签承诺工程始终保留。

取舍规则（确定性）：
1. 某季度预算超支时，在未承诺工程中按“单位投资覆盖人口”从低到高逐个移除，
   并列工程按标识升序移除，直到不超支或只剩承诺工程；
2. 依赖工程被移除后，未承诺的下游工程级联移除；
3. 承诺工程永不移除，若因此仍超支，则由评估结果如实暴露。
"""

from __future__ import annotations

from .models import LedgerState
from .quarters import quarter_key


def _efficiency(state: LedgerState, pid: str) -> float:
    project = state.projects[pid]
    population = sum(state.grids[grid_id].population for grid_id in project.covers)
    return population / project.capex if project.capex > 0 else float("inf")


def merge_selections(state: LedgerState, selections: list[tuple[str, ...]]) -> tuple[list[str], list[dict]]:
    """合并多组已选工程，返回（合并结果，取舍记录）。"""
    merged: set[str] = set()
    for selection in selections:
        merged.update(selection)
    committed = state.committed_projects()
    drops: list[dict] = []

    by_quarter = state.budget_by_quarter()
    quarters = sorted({state.projects[pid].planned_quarter for pid in merged}, key=quarter_key)
    for quarter in quarters:
        batch = by_quarter.get(quarter)
        if batch is None:
            continue
        while True:
            members = [pid for pid in merged if state.projects[pid].planned_quarter == quarter]
            charged = sum(state.projects[pid].capex for pid in members)
            if charged <= batch.total:
                break
            droppable = [pid for pid in members if pid not in committed]
            if not droppable:
                break
            victim = min(droppable, key=lambda pid: (_efficiency(state, pid), pid))
            merged.discard(victim)
            drops.append(
                {
                    "project": victim,
                    "reason": f"合并后批次 {batch.id}（{quarter}）预算不足，按单位投资覆盖人口取舍",
                }
            )

    changed = True
    while changed:
        changed = False
        for pid in sorted(merged):
            if pid in committed:
                continue
            missing = [dep for dep in state.projects[pid].depends_on if dep not in merged]
            if missing:
                merged.discard(pid)
                drops.append(
                    {"project": pid, "reason": f"依赖工程 {'、'.join(missing)} 未保留在合并方案中"}
                )
                changed = True
    return sorted(merged), drops
