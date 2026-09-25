"""领域模型：网格、现有能力、候选工程、预算批次、共建承诺、方案与版本化账本。

账本（LedgerState）把五类资料纳入同一版本化容器：每次变更都会递增版本号并
追加事件，方案（Plan）自身再维护一条只增不改的修订链，从而支持比较、合并与
回退，且任何修订都不得破坏已签订的共建承诺。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .errors import ValidationError
from .quarters import is_valid_quarter, quarter_key

PLAN_DRAFT = "draft"
PLAN_FROZEN = "frozen"

ORIGIN_CREATE = "create"
ORIGIN_MANUAL = "manual"
ORIGIN_MERGE = "merge"
ORIGIN_ROLLBACK = "rollback"
ORIGIN_CHANGE_ORDER = "change_order"

_IMPORT_KEYS = {"ledger_id", "name", "grids", "capabilities", "projects", "budgets", "commitments"}


def _is_num(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


@dataclass(frozen=True)
class Grid:
    """网格：人口与重点产业标签的最小空间单元。"""

    id: str
    name: str
    population: int
    industries: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "population": self.population,
            "industries": list(self.industries),
        }

    @staticmethod
    def from_dict(data: dict) -> "Grid":
        return Grid(
            id=data["id"],
            name=data["name"],
            population=data["population"],
            industries=tuple(data.get("industries", [])),
        )


@dataclass(frozen=True)
class Capability:
    """现有能力：已投运的站点或光纤及其覆盖网格。"""

    id: str
    kind: str
    covers: tuple[str, ...]
    since: str
    om_annual: float

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind,
            "covers": list(self.covers),
            "since": self.since,
            "om_annual": self.om_annual,
        }

    @staticmethod
    def from_dict(data: dict) -> "Capability":
        return Capability(
            id=data["id"],
            kind=data["kind"],
            covers=tuple(data["covers"]),
            since=data["since"],
            om_annual=data["om_annual"],
        )


@dataclass(frozen=True)
class Project:
    """候选工程：运营商、园区或乡镇申报的站点/光纤建设建议。"""

    id: str
    proposer: str
    kind: str
    covers: tuple[str, ...]
    capex: float
    om_annual: float
    declared_quarter: str
    planned_quarter: str
    depends_on: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "proposer": self.proposer,
            "kind": self.kind,
            "covers": list(self.covers),
            "capex": self.capex,
            "om_annual": self.om_annual,
            "declared_quarter": self.declared_quarter,
            "planned_quarter": self.planned_quarter,
            "depends_on": list(self.depends_on),
        }

    @staticmethod
    def from_dict(data: dict) -> "Project":
        return Project(
            id=data["id"],
            proposer=data["proposer"],
            kind=data["kind"],
            covers=tuple(data["covers"]),
            capex=data["capex"],
            om_annual=data["om_annual"],
            declared_quarter=data["declared_quarter"],
            planned_quarter=data["planned_quarter"],
            depends_on=tuple(data.get("depends_on", [])),
        )


@dataclass
class BudgetBatch:
    """预算批次：某季度可用的建设资金总额，version 用于乐观并发控制。"""

    id: str
    name: str
    quarter: str
    total: float
    version: int = 1

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "quarter": self.quarter,
            "total": self.total,
            "version": self.version,
        }

    @staticmethod
    def from_dict(data: dict) -> "BudgetBatch":
        return BudgetBatch(
            id=data["id"],
            name=data["name"],
            quarter=data["quarter"],
            total=data["total"],
            version=data.get("version", 1),
        )


@dataclass(frozen=True)
class Commitment:
    """共建承诺：已签订、不得被方案操作破坏的工程约定。"""

    id: str
    project: str
    partner: str
    signed_quarter: str
    note: str = ""

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "project": self.project,
            "partner": self.partner,
            "signed_quarter": self.signed_quarter,
            "note": self.note,
        }

    @staticmethod
    def from_dict(data: dict) -> "Commitment":
        return Commitment(
            id=data["id"],
            project=data["project"],
            partner=data["partner"],
            signed_quarter=data["signed_quarter"],
            note=data.get("note", ""),
        )


@dataclass(frozen=True)
class PlanRevision:
    """方案的一次修订，追加后不可变，drops 记录合并时的取舍。"""

    rev: int
    selected: tuple[str, ...]
    note: str
    origin: str
    at: str
    change_order: str | None = None
    drops: tuple[dict, ...] = ()

    def to_dict(self) -> dict:
        return {
            "rev": self.rev,
            "selected": list(self.selected),
            "note": self.note,
            "origin": self.origin,
            "at": self.at,
            "change_order": self.change_order,
            "drops": [dict(drop) for drop in self.drops],
        }

    @staticmethod
    def from_dict(data: dict) -> "PlanRevision":
        return PlanRevision(
            rev=data["rev"],
            selected=tuple(data["selected"]),
            note=data["note"],
            origin=data["origin"],
            at=data["at"],
            change_order=data.get("change_order"),
            drops=tuple(dict(drop) for drop in data.get("drops", [])),
        )


@dataclass
class Plan:
    """规划方案：状态（草稿/冻结）加只增不改的修订链。"""

    id: str
    name: str
    status: str
    revisions: list[PlanRevision]

    @property
    def current(self) -> PlanRevision:
        return self.revisions[-1]

    @property
    def revision(self) -> int:
        return self.current.rev

    @property
    def selected(self) -> tuple[str, ...]:
        return self.current.selected

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "status": self.status,
            "revisions": [rev.to_dict() for rev in self.revisions],
        }

    @staticmethod
    def from_dict(data: dict) -> "Plan":
        return Plan(
            id=data["id"],
            name=data["name"],
            status=data["status"],
            revisions=[PlanRevision.from_dict(item) for item in data["revisions"]],
        )


@dataclass(frozen=True)
class ChangeOrder:
    """留痕变更单：冻结方案唯一的修订途径。"""

    id: str
    plan: str
    base_rev: int
    add: tuple[str, ...]
    remove: tuple[str, ...]
    reason: str
    author: str
    at: str

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "plan": self.plan,
            "base_rev": self.base_rev,
            "add": list(self.add),
            "remove": list(self.remove),
            "reason": self.reason,
            "author": self.author,
            "at": self.at,
        }

    @staticmethod
    def from_dict(data: dict) -> "ChangeOrder":
        return ChangeOrder(
            id=data["id"],
            plan=data["plan"],
            base_rev=data["base_rev"],
            add=tuple(data["add"]),
            remove=tuple(data["remove"]),
            reason=data["reason"],
            author=data["author"],
            at=data["at"],
        )


@dataclass
class LedgerState:
    """版本化账本：五类资料加方案、变更单与事件流。"""

    ledger_id: str
    name: str
    version: int = 0
    counters: dict[str, int] = field(default_factory=dict)
    grids: dict[str, Grid] = field(default_factory=dict)
    capabilities: dict[str, Capability] = field(default_factory=dict)
    projects: dict[str, Project] = field(default_factory=dict)
    budgets: dict[str, BudgetBatch] = field(default_factory=dict)
    commitments: dict[str, Commitment] = field(default_factory=dict)
    plans: dict[str, Plan] = field(default_factory=dict)
    change_orders: dict[str, ChangeOrder] = field(default_factory=dict)
    events: list[dict] = field(default_factory=list)

    def next_id(self, kind: str) -> str:
        value = self.counters.get(kind, 0) + 1
        self.counters[kind] = value
        return f"{kind.upper()}-{value:04d}"

    def record(self, op: str, detail: str, at: str) -> None:
        self.events.append({"version": self.version, "op": op, "detail": detail, "at": at})

    def committed_projects(self) -> set[str]:
        return {item.project for item in self.commitments.values()}

    def budget_by_quarter(self) -> dict[str, BudgetBatch]:
        return {batch.quarter: batch for batch in self.budgets.values()}

    def to_dict(self) -> dict:
        return {
            "ledger_id": self.ledger_id,
            "name": self.name,
            "version": self.version,
            "counters": dict(self.counters),
            "grids": [self.grids[key].to_dict() for key in sorted(self.grids)],
            "capabilities": [self.capabilities[key].to_dict() for key in sorted(self.capabilities)],
            "projects": [self.projects[key].to_dict() for key in sorted(self.projects)],
            "budgets": [self.budgets[key].to_dict() for key in sorted(self.budgets)],
            "commitments": [self.commitments[key].to_dict() for key in sorted(self.commitments)],
            "plans": [self.plans[key].to_dict() for key in sorted(self.plans)],
            "change_orders": [self.change_orders[key].to_dict() for key in sorted(self.change_orders)],
            "events": [dict(event) for event in self.events],
        }

    @staticmethod
    def from_dict(data: dict) -> "LedgerState":
        state = LedgerState(
            ledger_id=data["ledger_id"],
            name=data.get("name", data["ledger_id"]),
            version=data.get("version", 0),
            counters=dict(data.get("counters", {})),
        )
        for item in data.get("grids", []):
            grid = Grid.from_dict(item)
            state.grids[grid.id] = grid
        for item in data.get("capabilities", []):
            capability = Capability.from_dict(item)
            state.capabilities[capability.id] = capability
        for item in data.get("projects", []):
            project = Project.from_dict(item)
            state.projects[project.id] = project
        for item in data.get("budgets", []):
            batch = BudgetBatch.from_dict(item)
            state.budgets[batch.id] = batch
        for item in data.get("commitments", []):
            commitment = Commitment.from_dict(item)
            state.commitments[commitment.id] = commitment
        for item in data.get("plans", []):
            plan = Plan.from_dict(item)
            state.plans[plan.id] = plan
        for item in data.get("change_orders", []):
            order = ChangeOrder.from_dict(item)
            state.change_orders[order.id] = order
        state.events = [dict(event) for event in data.get("events", [])]
        return state


def build_state_from_import(raw: dict) -> LedgerState:
    """把导入的 JSON 台账校验并构建为账本状态，问题一次性收集后抛出。"""
    problems: list[str] = []
    if not isinstance(raw, dict):
        raise ValidationError("台账文件必须是 JSON 对象")

    for key in sorted(set(raw) - _IMPORT_KEYS):
        problems.append(f"未知字段：{key}")

    ledger_id = raw.get("ledger_id")
    if not isinstance(ledger_id, str) or not ledger_id.strip():
        problems.append("ledger_id 必须是非空字符串")
        ledger_id = "unknown"
    name = raw.get("name", ledger_id)
    if not isinstance(name, str) or not name.strip():
        problems.append("name 必须是非空字符串")
        name = ledger_id

    grids = _parse_grids(raw.get("grids"), problems)
    capabilities = _parse_capabilities(raw.get("capabilities", []), grids, problems)
    projects = _parse_projects(raw.get("projects", []), grids, problems)
    budgets = _parse_budgets(raw.get("budgets", []), problems)
    commitments = _parse_commitments(raw.get("commitments", []), projects, problems)

    if problems:
        raise ValidationError(problems)
    return LedgerState(
        ledger_id=ledger_id.strip(),
        name=name.strip(),
        grids=grids,
        capabilities=capabilities,
        projects=projects,
        budgets=budgets,
        commitments=commitments,
    )


def _require_list(value: object, section: str, problems: list[str]) -> list:
    if value is None:
        return []
    if not isinstance(value, list):
        problems.append(f"{section} 必须是数组")
        return []
    return value


def _check_id(item: object, section: str, index: int, seen: set[str], problems: list[str]) -> str | None:
    prefix = f"{section}[{index}]"
    if not isinstance(item, dict):
        problems.append(f"{prefix} 必须是对象")
        return None
    item_id = item.get("id")
    if not isinstance(item_id, str) or not item_id.strip():
        problems.append(f"{prefix}.id 必须是非空字符串")
        return None
    if item_id in seen:
        problems.append(f"{prefix}.id 重复：{item_id}")
        return None
    seen.add(item_id)
    return item_id


def _check_covers(item: dict, prefix: str, grids: dict[str, Grid], problems: list[str]) -> tuple[str, ...]:
    covers = item.get("covers")
    if not isinstance(covers, list) or not covers:
        problems.append(f"{prefix}.covers 必须是非空数组")
        return ()
    result: list[str] = []
    for grid_id in covers:
        if not isinstance(grid_id, str) or grid_id not in grids:
            problems.append(f"{prefix}.covers 引用了不存在的网格：{grid_id!r}")
        elif grid_id in result:
            problems.append(f"{prefix}.covers 内网格重复：{grid_id}")
        else:
            result.append(grid_id)
    return tuple(result)


def _check_money(item: dict, field_name: str, prefix: str, problems: list[str]) -> float:
    value = item.get(field_name)
    if not _is_num(value) or value < 0:
        problems.append(f"{prefix}.{field_name} 必须是非负数值")
        return 0.0
    return float(value)


def _parse_grids(raw: object, problems: list[str]) -> dict[str, Grid]:
    items = _require_list(raw, "grids", problems)
    if raw is not None and isinstance(raw, list) and not raw:
        problems.append("grids 不能为空，账本至少需要一个网格")
    grids: dict[str, Grid] = {}
    seen: set[str] = set()
    for index, item in enumerate(items):
        prefix = f"grids[{index}]"
        item_id = _check_id(item, "grids", index, seen, problems)
        if item_id is None:
            continue
        name = item.get("name")
        if not isinstance(name, str) or not name.strip():
            problems.append(f"{prefix}.name 必须是非空字符串")
            name = item_id
        population = item.get("population")
        if not isinstance(population, int) or isinstance(population, bool) or population < 0:
            problems.append(f"{prefix}.population 必须是非负整数")
            population = 0
        industries = item.get("industries", [])
        if not isinstance(industries, list) or any(not isinstance(tag, str) for tag in industries):
            problems.append(f"{prefix}.industries 必须是字符串数组")
            industries = []
        grids[item_id] = Grid(id=item_id, name=name.strip(), population=population, industries=tuple(industries))
    return grids


def _parse_capabilities(raw: object, grids: dict[str, Grid], problems: list[str]) -> dict[str, Capability]:
    items = _require_list(raw, "capabilities", problems)
    capabilities: dict[str, Capability] = {}
    seen: set[str] = set()
    for index, item in enumerate(items):
        prefix = f"capabilities[{index}]"
        item_id = _check_id(item, "capabilities", index, seen, problems)
        if item_id is None:
            continue
        kind = item.get("kind")
        if not isinstance(kind, str) or not kind.strip():
            problems.append(f"{prefix}.kind 必须是非空字符串")
            kind = "unknown"
        covers = _check_covers(item, prefix, grids, problems)
        since = item.get("since")
        if not is_valid_quarter(since):
            problems.append(f"{prefix}.since 季度格式非法：{since!r}")
            since = "1970Q1"
        om_annual = _check_money(item, "om_annual", prefix, problems)
        capabilities[item_id] = Capability(id=item_id, kind=kind.strip(), covers=covers, since=since, om_annual=om_annual)
    return capabilities


def _parse_projects(raw: object, grids: dict[str, Grid], problems: list[str]) -> dict[str, Project]:
    items = _require_list(raw, "projects", problems)
    projects: dict[str, Project] = {}
    seen: set[str] = set()
    for index, item in enumerate(items):
        prefix = f"projects[{index}]"
        item_id = _check_id(item, "projects", index, seen, problems)
        if item_id is None:
            continue
        proposer = item.get("proposer")
        if not isinstance(proposer, str) or not proposer.strip():
            problems.append(f"{prefix}.proposer 必须是非空字符串")
            proposer = "unknown"
        kind = item.get("kind")
        if not isinstance(kind, str) or not kind.strip():
            problems.append(f"{prefix}.kind 必须是非空字符串")
            kind = "unknown"
        covers = _check_covers(item, prefix, grids, problems)
        capex = _check_money(item, "capex", prefix, problems)
        om_annual = _check_money(item, "om_annual", prefix, problems)
        declared = item.get("declared_quarter")
        if not is_valid_quarter(declared):
            problems.append(f"{prefix}.declared_quarter 季度格式非法：{declared!r}")
            declared = "1970Q1"
        planned = item.get("planned_quarter")
        if not is_valid_quarter(planned):
            problems.append(f"{prefix}.planned_quarter 季度格式非法：{planned!r}")
            planned = "1970Q1"
        if is_valid_quarter(declared) and is_valid_quarter(planned) and quarter_key(planned) < quarter_key(declared):
            problems.append(f"{prefix}.planned_quarter 早于 declared_quarter：{planned} < {declared}")
        depends_on = item.get("depends_on", [])
        if not isinstance(depends_on, list) or any(not isinstance(dep, str) for dep in depends_on):
            problems.append(f"{prefix}.depends_on 必须是字符串数组")
            depends_on = []
        projects[item_id] = Project(
            id=item_id,
            proposer=proposer.strip(),
            kind=kind.strip(),
            covers=covers,
            capex=capex,
            om_annual=om_annual,
            declared_quarter=declared,
            planned_quarter=planned,
            depends_on=tuple(dict.fromkeys(depends_on)),
        )
    for project in projects.values():
        for dep in project.depends_on:
            if dep == project.id:
                problems.append(f"工程 {project.id} 不能依赖自身")
            elif dep not in projects:
                problems.append(f"工程 {project.id} 依赖了不存在的工程：{dep}")
    _check_dependency_cycles(projects, problems)
    return projects


def _check_dependency_cycles(projects: dict[str, Project], problems: list[str]) -> None:
    visiting: set[str] = set()
    done: set[str] = set()

    def visit(pid: str, path: list[str]) -> None:
        if pid in done or pid in visiting:
            return
        visiting.add(pid)
        for dep in projects[pid].depends_on:
            if dep not in projects:
                continue
            if dep in visiting or dep == pid:
                cycle = " -> ".join(path + [pid, dep])
                problems.append(f"工程依赖存在环：{cycle}")
            else:
                visit(dep, path + [pid])
        visiting.discard(pid)
        done.add(pid)

    for pid in sorted(projects):
        visit(pid, [])


def _parse_budgets(raw: object, problems: list[str]) -> dict[str, BudgetBatch]:
    items = _require_list(raw, "budgets", problems)
    budgets: dict[str, BudgetBatch] = {}
    seen: set[str] = set()
    quarters: set[str] = set()
    for index, item in enumerate(items):
        prefix = f"budgets[{index}]"
        item_id = _check_id(item, "budgets", index, seen, problems)
        if item_id is None:
            continue
        name = item.get("name")
        if not isinstance(name, str) or not name.strip():
            problems.append(f"{prefix}.name 必须是非空字符串")
            name = item_id
        quarter = item.get("quarter")
        if not is_valid_quarter(quarter):
            problems.append(f"{prefix}.quarter 季度格式非法：{quarter!r}")
            quarter = "1970Q1"
        elif quarter in quarters:
            problems.append(f"{prefix}.quarter 重复：{quarter}（同一季度只能有一个预算批次）")
        quarters.add(quarter)
        total = _check_money(item, "total", prefix, problems)
        budgets[item_id] = BudgetBatch(id=item_id, name=name.strip(), quarter=quarter, total=total)
    return budgets


def _parse_commitments(raw: object, projects: dict[str, Project], problems: list[str]) -> dict[str, Commitment]:
    items = _require_list(raw, "commitments", problems)
    commitments: dict[str, Commitment] = {}
    seen: set[str] = set()
    for index, item in enumerate(items):
        prefix = f"commitments[{index}]"
        item_id = _check_id(item, "commitments", index, seen, problems)
        if item_id is None:
            continue
        project = item.get("project")
        if not isinstance(project, str) or project not in projects:
            problems.append(f"{prefix}.project 引用了不存在的工程：{project!r}")
            project = ""
        partner = item.get("partner")
        if not isinstance(partner, str) or not partner.strip():
            problems.append(f"{prefix}.partner 必须是非空字符串")
            partner = "unknown"
        signed = item.get("signed_quarter")
        if not is_valid_quarter(signed):
            problems.append(f"{prefix}.signed_quarter 季度格式非法：{signed!r}")
            signed = "1970Q1"
        note = item.get("note", "")
        if not isinstance(note, str):
            problems.append(f"{prefix}.note 必须是字符串")
            note = ""
        commitments[item_id] = Commitment(
            id=item_id, project=project, partner=partner.strip(), signed_quarter=signed, note=note
        )
    return commitments
