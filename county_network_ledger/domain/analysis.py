"""纯分析逻辑：重复建设识别、方案评估、未覆盖清单。

本模块不产生事件、不做 IO，输入状态与工程集合，输出确定结构，
便于在方案比较、报告导出与接口查询中复用。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .entities import (
    BASIC_THRESHOLD,
    FIBER_LEVEL,
    MAINTENANCE_QUARTERS,
    BudgetBatch,
    Grid,
    Project,
)
from .state import LedgerState, PlanState

EPS = 1e-9


@dataclass(frozen=True)
class DuplicatePair:
    """一对重复/冲突建设。"""

    project_a: str
    project_b: str
    grid_id: str
    spatial: bool       # 空间重叠（同网格或服务范围相交）
    temporal: bool      # 施工窗口时间重叠
    same_kind: bool     # 同等级重复（如两个 4G 站点服务同一网格）
    severity: str       # 空间+时间双重重复 | 空间重复 | 时序冲突提示

    @property
    def key(self) -> tuple:
        return (self.project_a, self.project_b, self.grid_id)


@dataclass
class BudgetUsage:
    budget_id: str
    name: str
    amount: float
    gross_cost: float = 0.0        # 工程毛成本合计
    shared_funding: float = 0.0    # 共建分担金额
    county_cost: float = 0.0       # 县级配套（占用预算额）
    project_ids: list[str] = field(default_factory=list)

    @property
    def remaining(self) -> float:
        return self.amount - self.county_cost

    @property
    def overrun(self) -> float:
        return max(0.0, self.county_cost - self.amount)


@dataclass
class DependencyIssue:
    project_id: str
    missing_dep: str
    reason: str  # 未选入 | 完工晚于依赖窗口


@dataclass
class PlanEvaluation:
    plan_id: str
    project_ids: tuple[str, ...]
    covered_grids: tuple[str, ...]              # 方案建成后达到基本覆盖
    fiber_grids: tuple[str, ...]                # 达到光纤覆盖
    uncovered: tuple[Grid, ...]                 # 仍未达基本覆盖的网格
    blank_uncovered: tuple[Grid, ...]           # 其中的空白网格
    uncovered_industry: tuple[Grid, ...]        # 其中承载重点产业的网格
    basic_population: int                       # 基本覆盖人口
    total_population: int
    industry_projects: tuple[str, ...]
    industry_population: int
    gross_cost: float
    shared_funding: float
    county_cost: float
    annual_maintenance: float
    maintenance_horizon_cost: float             # 10 年运维
    expected_annual_revenue: float
    budgets: dict[str, BudgetUsage]
    duplicates: tuple[DuplicatePair, ...]
    dependency_issues: tuple[DependencyIssue, ...]
    commitment_locked: tuple[str, ...]          # 方案中被承诺锁定的工程
    commitment_removed: tuple[str, ...]         # 相对承诺应建清单缺失的工程

    @property
    def basic_coverage_rate(self) -> float:
        if self.total_population == 0:
            return 1.0
        return round(self.basic_population / self.total_population, 4)

    @property
    def feasible(self) -> bool:
        return (
            not self.dependency_issues
            and all(b.overrun <= EPS for b in self.budgets.values())
        )

    def violations(self) -> list[str]:
        msgs = []
        for b in self.budgets.values():
            if b.overrun > EPS:
                msgs.append(f"预算批次 {b.name}（{b.budget_id}）超支 {round(b.overrun, 2)} 元")
        for d in self.dependency_issues:
            msgs.append(f"工程 {d.project_id} 的跨期依赖 {d.missing_dep}：{d.reason}")
        return msgs


def rectangles_overlap(a: Grid, b: Grid) -> bool:
    """两个矩形包围盒是否相交（边界相接算相交，坐标退化时退化为点/线判断）。"""
    return not (a.x1 < b.x0 - EPS or b.x1 < a.x0 - EPS
                or a.y1 < b.y0 - EPS or b.y1 < a.y0 - EPS)


def windows_overlap(a: Project, b: Project) -> bool:
    """施工窗口是否在时间上重叠。"""
    return not (a.end_q < b.start_q or b.end_q < a.start_q)


def find_duplicates(state: LedgerState, project_ids: tuple[str, ...] | list[str]) -> list[DuplicatePair]:
    """识别一组工程内部的空间/时间重复建设。

    - 同类工程（站点同等级，如两个 4G 站点；或两段光纤）服务同一网格：
      窗口重叠为"空间+时间双重重复"，窗口错开为"空间重复"（分期复建）；
    - 站点跨等级（4G 与 5G）窗口重叠：仅提示"时序冲突"，应共址而非重复征地；
    - 站点与光纤互为配套设施，不判为重复。
    """
    def is_fiber(kind: str) -> bool:
        return kind in ("光纤", "光纤延伸")

    projs = [state.projects[i] for i in project_ids if i in state.projects]
    pairs: list[DuplicatePair] = []
    for i in range(len(projs)):
        for j in range(i + 1, len(projs)):
            a, b = projs[i], projs[j]
            # 站点与光纤是配套关系，不构成重复
            if is_fiber(a.kind) != is_fiber(b.kind):
                continue
            common = sorted(set(a.covers) & set(b.covers))
            temporal = windows_overlap(a, b)
            same_kind = a.kind == b.kind
            # 跨等级站点仅在窗口重叠时提示共址协调
            if not same_kind and not temporal:
                continue
            for gid in common:
                if same_kind and temporal:
                    sev = "空间+时间双重重复"
                elif same_kind:
                    sev = "空间重复"
                elif temporal:
                    sev = "时序冲突提示"
                else:
                    continue
                pairs.append(DuplicatePair(
                    project_a=a.id, project_b=b.id, grid_id=gid,
                    spatial=True, temporal=temporal, same_kind=same_kind,
                    severity=sev,
                ))
    # 额外：候选工程与现有能力重复（存量已具备同服务能力仍重复申报）
    mobile_level: dict[str, int] = {}
    has_fiber: set[str] = set()
    from .entities import COVERAGE_LEVELS
    for cap in state.capabilities:
        if cap.kind in ("光纤",):
            has_fiber.add(cap.grid_id)
        else:
            lv = COVERAGE_LEVELS.get(cap.kind, 0)
            mobile_level[cap.grid_id] = max(mobile_level.get(cap.grid_id, 0), lv)
    for pr in projs:
        for gid in pr.covers:
            redundant = (
                (is_fiber(pr.kind) and gid in has_fiber)
                or (not is_fiber(pr.kind)
                    and mobile_level.get(gid, 0) >= pr.level)
            )
            if redundant:
                pairs.append(DuplicatePair(
                    project_a=pr.id, project_b=f"存量:{gid}", grid_id=gid,
                    spatial=True, temporal=False, same_kind=True,
                    severity="空间重复",
                ))
    pairs.sort(key=lambda d: d.key)
    return _dedup_pairs(pairs)


def _dedup_pairs(pairs: list[DuplicatePair]) -> list[DuplicatePair]:
    seen: set[tuple] = set()
    out = []
    for d in pairs:
        if d.key not in seen:
            seen.add(d.key)
            out.append(d)
    return out


def existing_levels(state: LedgerState) -> dict[str, int]:
    """各网格现有最高覆盖等级。"""
    levels: dict[str, int] = {}
    from .entities import COVERAGE_LEVELS
    for cap in state.capabilities:
        lv = COVERAGE_LEVELS.get(cap.kind, 0)
        levels[cap.grid_id] = max(levels.get(cap.grid_id, 0), lv)
    return levels


def check_dependencies(state: LedgerState, selected: tuple[str, ...]) -> list[DependencyIssue]:
    """校验跨期依赖：前置工程必须选入，且其完工季度不晚于本工程开工季度。"""
    chosen = set(selected)
    issues: list[DependencyIssue] = []
    for pid in selected:
        pr = state.projects.get(pid)
        if pr is None:
            continue
        for dep in pr.deps:
            dep_pr = state.projects.get(dep)
            if dep not in chosen:
                issues.append(DependencyIssue(pid, dep, "前置工程未选入"))
            elif dep_pr is not None and dep_pr.end_q > pr.start_q:
                issues.append(DependencyIssue(
                    pid, dep,
                    f"前置工程完工季度 {dep_pr.end_q} 晚于本工程开工季度 {pr.start_q}",
                ))
    issues.sort(key=lambda d: (d.project_id, d.missing_dep))
    return issues


def evaluate(state: LedgerState, plan_or_ids: PlanState | tuple[str, ...],
             plan_id: str = "(adhoc)") -> PlanEvaluation:
    """评估一组工程（或一个方案）的覆盖、产业、成本、预算、重复与依赖。"""
    if isinstance(plan_or_ids, PlanState):
        plan = plan_or_ids
        ids = tuple(plan.project_ids)
        plan_id = plan.id
    else:
        ids = tuple(plan_or_ids)

    selected = [state.projects[i] for i in ids if i in state.projects]
    levels = existing_levels(state)
    fiber: set[str] = {g for g, lv in levels.items() if lv >= FIBER_LEVEL}
    covered_after = dict(levels)
    industry_projects: set[str] = set()

    # 按开工季度排序后叠加能力，保证跨期工程不会提前提供覆盖
    for pr in sorted(selected, key=lambda p: (p.start_q, p.id)):
        for gid in pr.covers:
            covered_after[gid] = max(covered_after.get(gid, 0), pr.level)
            if pr.level >= FIBER_LEVEL:
                fiber.add(gid)
            g = state.grids.get(gid)
            if g is not None and g.industry:
                industry_projects.add(pr.id)

    covered_grids = tuple(sorted(
        gid for gid in state.grids if covered_after.get(gid, 0) >= BASIC_THRESHOLD
    ))
    fiber_grids = tuple(sorted(gid for gid in fiber if gid in state.grids))
    uncovered = tuple(state.grids[g] for g in sorted(state.grids)
                      if covered_after.get(g, 0) < BASIC_THRESHOLD)
    blank_uncovered = tuple(g for g in uncovered if g.blank)
    uncovered_industry = tuple(g for g in uncovered if g.industry)

    basic_pop = sum(state.grids[g].population for g in covered_grids)
    industry_pop = sum(
        g.population for g in state.grids.values()
        if g.industry and covered_after.get(g.id, 0) >= BASIC_THRESHOLD
    )
    total_pop = sum(g.population for g in state.grids.values())

    # 预算占用：共建分担按承诺比例折减县级配套
    budgets: dict[str, BudgetUsage] = {}
    gross = shared = county = 0.0
    annual_maint = revenue = 0.0
    for pr in selected:
        share = state.commitment_share(pr.id)
        pr_shared = pr.cost * share
        pr_county = pr.cost - pr_shared
        gross += pr.cost
        shared += pr_shared
        county += pr_county
        annual_maint += pr.annual_maintenance
        revenue += pr.revenue
        b = state.budgets.get(pr.budget_id)
        usage = budgets.setdefault(pr.budget_id, BudgetUsage(
            budget_id=pr.budget_id,
            name=b.name if b else pr.budget_id,
            amount=b.amount if b else 0.0,
        ))
        usage.gross_cost += pr.cost
        usage.shared_funding += pr_shared
        usage.county_cost += pr_county
        usage.project_ids.append(pr.id)
    for usage in budgets.values():
        usage.project_ids.sort()

    duplicates = tuple(find_duplicates(state, ids))
    dep_issues = tuple(check_dependencies(state, ids))

    # 承诺核对：全部已签承诺涉及的工程默认应纳入正式方案
    committed = {c.project_id for c in state.commitments.values()}
    chosen_set = set(ids)
    locked = tuple(sorted(committed & chosen_set))
    removed = tuple(sorted(committed - chosen_set))

    return PlanEvaluation(
        plan_id=plan_id,
        project_ids=tuple(sorted(ids)),
        covered_grids=covered_grids,
        fiber_grids=fiber_grids,
        uncovered=uncovered,
        blank_uncovered=blank_uncovered,
        uncovered_industry=uncovered_industry,
        basic_population=basic_pop,
        total_population=total_pop,
        industry_projects=tuple(sorted(industry_projects)),
        industry_population=industry_pop,
        gross_cost=round(gross, 2),
        shared_funding=round(shared, 2),
        county_cost=round(county, 2),
        annual_maintenance=round(annual_maint, 2),
        maintenance_horizon_cost=round(annual_maint * MAINTENANCE_QUARTERS / 4, 2),
        expected_annual_revenue=round(revenue, 2),
        budgets=budgets,
        duplicates=duplicates,
        dependency_issues=dep_issues,
        commitment_locked=locked,
        commitment_removed=removed,
    )


def compare(evals: list[PlanEvaluation]) -> list[dict]:
    """方案横向比较，按指标键排序输出确定行。"""
    rows = []
    for e in evals:
        rows.append({
            "plan_id": e.plan_id,
            "project_count": len(e.project_ids),
            "basic_coverage_rate": e.basic_coverage_rate,
            "basic_population": e.basic_population,
            "fiber_grid_count": len(e.fiber_grids),
            "industry_population": e.industry_population,
            "uncovered_count": len(e.uncovered),
            "blank_uncovered_count": len(e.blank_uncovered),
            "county_cost": e.county_cost,
            "shared_funding": e.shared_funding,
            "annual_maintenance": e.annual_maintenance,
            "maintenance_10y": e.maintenance_horizon_cost,
            "annual_revenue": e.expected_annual_revenue,
            "duplicate_count": len(e.duplicates),
            "dependency_issue_count": len(e.dependency_issues),
            "feasible": e.feasible,
        })
    rows.sort(key=lambda r: r["plan_id"])
    return rows
