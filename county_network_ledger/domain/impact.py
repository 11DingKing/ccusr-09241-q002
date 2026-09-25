"""方案影响评估：基本覆盖、重点产业、长期运维成本、预算与跨期依赖约束。

评估结果是纯函数输出：同一账本状态与同一组已选工程必然得到同一结果，
为方案比较、合并取舍与确定性报告提供统一依据。
"""

from __future__ import annotations

from .errors import NotFoundError
from .models import LedgerState
from .quarters import quarter_key

OM_HORIZON_YEARS = 10
_EPSILON = 1e-9


def money(value: float) -> float:
    """金额统一保留两位小数，保证输出确定性。"""
    return round(float(value), 2)


def ratio(part: float, whole: float) -> float:
    """比例统一保留四位小数，分母为零时返回 0。"""
    return round(part / whole, 4) if whole else 0.0


def evaluate_selection(state: LedgerState, selected: tuple[str, ...] | list[str]) -> dict:
    """评估一组已选工程对覆盖、产业、成本、预算与依赖的影响。"""
    selected = tuple(sorted(dict.fromkeys(selected)))
    unknown = [pid for pid in selected if pid not in state.projects]
    if unknown:
        raise NotFoundError(f"候选工程不存在：{'、'.join(unknown)}")

    covered_existing: set[str] = set()
    for capability in state.capabilities.values():
        covered_existing.update(capability.covers)
    covered_selected: set[str] = set()
    for pid in selected:
        covered_selected.update(state.projects[pid].covers)
    covered = covered_existing | covered_selected

    population_total = sum(grid.population for grid in state.grids.values())
    population_covered = sum(state.grids[grid_id].population for grid_id in covered)
    coverage = {
        "population_total": population_total,
        "population_covered": population_covered,
        "population_ratio": ratio(population_covered, population_total),
        "grids_total": len(state.grids),
        "grids_covered": len(covered),
        "grids_covered_existing": len(covered_existing),
        "grids_covered_selected": len(covered_selected),
    }

    industry_names = sorted({tag for grid in state.grids.values() for tag in grid.industries})
    industries = []
    for name in industry_names:
        members = [grid for grid in state.grids.values() if name in grid.industries]
        total = sum(grid.population for grid in members)
        hit = sum(grid.population for grid in members if grid.id in covered)
        industries.append(
            {"name": name, "population_total": total, "population_covered": hit, "ratio": ratio(hit, total)}
        )

    capex = money(sum(state.projects[pid].capex for pid in selected))
    om_selected = money(sum(state.projects[pid].om_annual for pid in selected))
    om_existing = money(sum(capability.om_annual for capability in state.capabilities.values()))
    cost = {
        "capex": capex,
        "om_annual_selected": om_selected,
        "om_annual_existing": om_existing,
        "om_horizon_years": OM_HORIZON_YEARS,
        "tco_selected": money(capex + om_selected * OM_HORIZON_YEARS),
        "tco_total": money(capex + (om_selected + om_existing) * OM_HORIZON_YEARS),
    }

    by_quarter = state.budget_by_quarter()
    charged: dict[str, float] = {}
    for pid in selected:
        quarter = state.projects[pid].planned_quarter
        charged[quarter] = charged.get(quarter, 0.0) + state.projects[pid].capex
    budgets = []
    for quarter in sorted(by_quarter, key=quarter_key):
        batch = by_quarter[quarter]
        used = money(charged.get(quarter, 0.0))
        remaining = money(batch.total - used)
        budgets.append(
            {
                "batch": batch.id,
                "name": batch.name,
                "quarter": quarter,
                "total": money(batch.total),
                "charged": used,
                "remaining": remaining,
                "over": remaining < 0,
            }
        )

    dependency_violations = []
    for pid in selected:
        project = state.projects[pid]
        for dep in project.depends_on:
            if dep not in selected:
                dependency_violations.append(
                    {
                        "project": pid,
                        "dependency": dep,
                        "kind": "missing",
                        "detail": f"工程 {pid} 依赖 {dep}，但 {dep} 未纳入方案",
                    }
                )
            else:
                dep_project = state.projects[dep]
                if quarter_key(dep_project.planned_quarter) > quarter_key(project.planned_quarter):
                    dependency_violations.append(
                        {
                            "project": pid,
                            "dependency": dep,
                            "kind": "order",
                            "detail": (
                                f"工程 {pid} 计划 {project.planned_quarter}，"
                                f"早于其依赖 {dep} 的计划 {dep_project.planned_quarter}"
                            ),
                        }
                    )

    budget_violations = [
        {
            "batch": item["batch"],
            "quarter": item["quarter"],
            "charged": item["charged"],
            "total": item["total"],
            "detail": f"批次 {item['batch']}（{item['quarter']}）已计 {item['charged']} 万元，超出总额 {item['total']} 万元",
        }
        for item in budgets
        if item["over"]
    ]
    missing_batches = [
        {"project": pid, "quarter": state.projects[pid].planned_quarter}
        for pid in selected
        if state.projects[pid].planned_quarter not in by_quarter
    ]

    candidate_covered: set[str] = set()
    for project in state.projects.values():
        candidate_covered.update(project.covers)
    uncovered = []
    for grid in state.grids.values():
        if grid.id in covered:
            continue
        uncovered.append(
            {
                "grid": grid.id,
                "name": grid.name,
                "population": grid.population,
                "industries": list(grid.industries),
                "blank": grid.id not in candidate_covered,
            }
        )
    uncovered.sort(key=lambda entry: (-entry["population"], entry["grid"]))

    excluded = _explain_excluded(state, selected, covered, by_quarter, charged)

    return {
        "selected": list(selected),
        "coverage": coverage,
        "industries": industries,
        "cost": cost,
        "budgets": budgets,
        "violations": {
            "dependencies": dependency_violations,
            "budget": budget_violations,
            "missing_batches": missing_batches,
        },
        "has_violations": bool(dependency_violations or budget_violations or missing_batches),
        "uncovered": uncovered,
        "excluded": excluded,
    }


def _explain_excluded(
    state: LedgerState,
    selected: tuple[str, ...],
    covered: set[str],
    by_quarter: dict,
    charged: dict[str, float],
) -> list[dict]:
    """为每个未入选工程给出确定性的取舍理由。"""
    remaining = {quarter: by_quarter[quarter].total - charged.get(quarter, 0.0) for quarter in by_quarter}
    excluded = []
    for pid in sorted(state.projects):
        if pid in selected:
            continue
        project = state.projects[pid]
        reasons: list[str] = []
        overlap = sorted(set(project.covers) & covered)
        if overlap:
            labels = []
            for other in selected:
                if set(state.projects[other].covers) & set(overlap):
                    labels.append(f"{other}（已选）")
            for cid in sorted(state.capabilities):
                if set(state.capabilities[cid].covers) & set(overlap):
                    labels.append(f"{cid}（现有能力）")
            reasons.append(f"重复覆盖：网格 {'、'.join(overlap)} 已由 {'、'.join(labels)}覆盖")
        batch = by_quarter.get(project.planned_quarter)
        if batch is None:
            reasons.append(f"缺少 {project.planned_quarter} 季度预算批次")
        else:
            left = remaining[project.planned_quarter]
            if project.capex > left + _EPSILON:
                reasons.append(f"预算不足：批次 {batch.id} 剩余 {money(left)} 万元，工程需 {money(project.capex)} 万元")
        unmet = [dep for dep in project.depends_on if dep not in selected]
        if unmet:
            reasons.append(f"依赖工程未纳入：{'、'.join(unmet)}")
        late = [
            dep
            for dep in project.depends_on
            if dep in selected
            and quarter_key(state.projects[dep].planned_quarter) > quarter_key(project.planned_quarter)
        ]
        if late:
            reasons.append(f"依赖工程时序冲突：{'、'.join(late)} 计划季度晚于本工程")
        if not reasons:
            reasons.append("未被选用")
        excluded.append({"project": pid, "proposer": project.proposer, "reasons": reasons})
    return excluded
