"""事件回放：从事件流构造当前账本状态（纯函数，无 IO）。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from .entities import (
    BudgetBatch,
    Commitment,
    ExistingCapability,
    Grid,
    Project,
)
from . import events as ev


@dataclass(frozen=True)
class PlanRevision:
    """方案内容的一个历史版本（全量快照，便于比较与回退）。"""

    index: int
    label: str
    project_ids: tuple[str, ...]
    quarter: int
    note: str = ""


@dataclass
class PlanState:
    id: str
    name: str
    status: str = "工作副本"  # 工作副本 | 已冻结 | 已废弃
    project_ids: tuple[str, ...] = ()
    revisions: tuple[PlanRevision, ...] = ()
    created_q: int = 0
    frozen_q: int | None = None
    basis: str | None = None  # 比较/派生时的来源方案

    @property
    def revision_count(self) -> int:
        return len(self.revisions)


@dataclass
class ChangeOrderState:
    id: str
    plan_id: str
    kind: str  # add | remove
    project_ids: tuple[str, ...]
    reason: str
    proposer: str
    status: str = "待审批"  # 待审批 | 已批准 | 已驳回
    quarter: int = 0
    decided_q: int | None = None
    breach: bool = False  # 批准时是否被记录为承诺违约


@dataclass
class LedgerState:
    seq: int = 0
    grids: dict[str, Grid] = field(default_factory=dict)
    capabilities: list[ExistingCapability] = field(default_factory=list)
    projects: dict[str, Project] = field(default_factory=dict)
    budgets: dict[str, BudgetBatch] = field(default_factory=dict)
    commitments: dict[str, Commitment] = field(default_factory=dict)
    plans: dict[str, PlanState] = field(default_factory=dict)
    change_orders: dict[str, ChangeOrderState] = field(default_factory=dict)

    def commitment_share(self, project_id: str) -> float:
        """某工程已签署承诺的总分担比例（截断到 [0,1]）。"""
        total = sum(c.share for c in self.commitments.values() if c.project_id == project_id)
        return min(1.0, max(0.0, total))

    def selected_projects(self, plan: PlanState) -> list[Project]:
        return [self.projects[pid] for pid in plan.project_ids if pid in self.projects]


def _grid(payload: dict[str, Any]) -> Grid:
    return Grid(
        id=payload["id"],
        name=payload["name"],
        population=int(payload["population"]),
        x0=float(payload["x0"]),
        y0=float(payload["y0"]),
        x1=float(payload["x1"]),
        y1=float(payload["y1"]),
        industry=payload.get("industry"),
        blank=bool(payload.get("blank", False)),
    )


def _capability(payload: dict[str, Any]) -> ExistingCapability:
    return ExistingCapability(
        id=payload["id"], grid_id=payload["grid_id"], kind=payload["kind"],
        operator=payload["operator"],
    )


def _project(payload: dict[str, Any]) -> Project:
    return Project(
        id=payload["id"],
        name=payload["name"],
        kind=payload["kind"],
        covers=tuple(payload["covers"]),
        budget_id=payload["budget_id"],
        cost=float(payload["cost"]),
        annual_maintenance=float(payload["annual_maintenance"]),
        start_q=int(payload["start_q"]),
        end_q=int(payload["end_q"]),
        revenue=float(payload.get("revenue", 0.0)),
        deps=tuple(payload.get("deps", [])),
        shared_with=tuple(payload.get("shared_with", [])),
        proposer=payload.get("proposer", ""),
    )


def _budget(payload: dict[str, Any], version: int = 0) -> BudgetBatch:
    return BudgetBatch(
        id=payload["id"],
        name=payload["name"],
        amount=float(payload["amount"]),
        quarter=int(payload["quarter"]),
        version=version,
    )


def _commitment(payload: dict[str, Any]) -> Commitment:
    return Commitment(
        id=payload["id"],
        project_id=payload["project_id"],
        party=payload["party"],
        shared_by=payload["shared_by"],
        share=float(payload["share"]),
        signed_q=int(payload["signed_q"]),
        editable=bool(payload.get("editable", True)),
    )


def _next_plan_id(state: LedgerState) -> str:
    return f"FA-{len(state.plans) + 1:04d}"


def _next_co_id(state: LedgerState) -> str:
    return f"BG-{len(state.change_orders) + 1:04d}"


def apply(state: LedgerState, evt: dict[str, Any]) -> LedgerState:
    """把单条事件应用到状态（就地修改并返回同一对象）。"""
    name = evt["name"]
    p = evt["payload"]
    state.seq = int(evt["seq"])

    if name == ev.GRID_IMPORTED:
        state.grids[p["id"]] = _grid(p)
    elif name == ev.CAPABILITY_IMPORTED:
        state.capabilities.append(_capability(p))
    elif name == ev.PROJECT_IMPORTED:
        state.projects[p["id"]] = _project(p)
    elif name == ev.BUDGET_IMPORTED:
        state.budgets[p["id"]] = _budget(p, version=0)
    elif name == ev.COMMITMENT_SIGNED:
        state.commitments[p["id"]] = _commitment(p)

    elif name == ev.BUDGET_UPDATED:
        old = state.budgets[p["id"]]
        state.budgets[p["id"]] = BudgetBatch(
            id=old.id,
            name=p.get("name", old.name),
            amount=float(p["amount"]),
            quarter=int(p.get("quarter", old.quarter)),
            version=old.version + 1,
        )

    elif name == ev.PLAN_CREATED:
        plan = PlanState(
            id=p["plan_id"], name=p["name"], created_q=int(p["quarter"]),
            basis=p.get("basis"),
        )
        ids = tuple(p.get("project_ids", []))
        plan.project_ids = ids
        plan.revisions = (PlanRevision(0, "创建", ids, int(p["quarter"]), p.get("note", "")),)
        state.plans[plan.id] = plan

    elif name == ev.PLAN_PROJECTS_SET:
        plan = state.plans[p["plan_id"]]
        ids = tuple(p["project_ids"])
        plan.project_ids = ids
        idx = len(plan.revisions)
        plan.revisions = plan.revisions + (
            PlanRevision(idx, f"设置选入工程（{len(ids)} 项）", ids, int(p["quarter"]), p.get("note", "")),
        )

    elif name == ev.PLAN_MERGED:
        plan = state.plans[p["plan_id"]]
        ids = tuple(p["project_ids"])
        plan.project_ids = ids
        idx = len(plan.revisions)
        plan.revisions = plan.revisions + (
            PlanRevision(idx, f"合并方案 {p['from_plan']}", ids, int(p["quarter"]), p.get("note", "")),
        )

    elif name == ev.PLAN_ROLLED_BACK:
        plan = state.plans[p["plan_id"]]
        target = int(p["target_index"])
        ids = plan.revisions[target].project_ids
        plan.project_ids = ids
        idx = len(plan.revisions)
        plan.revisions = plan.revisions + (
            PlanRevision(idx, f"回退至第 {target + 1} 版", ids, int(p["quarter"]), p.get("note", "")),
        )

    elif name == ev.PLAN_FROZEN:
        plan = state.plans[p["plan_id"]]
        plan.status = "已冻结"
        plan.frozen_q = int(p["quarter"])
        # 冻结即锁定方案涉及的共建承诺：此后不得直接改/删
        locked = set(plan.project_ids)
        for cid, c in state.commitments.items():
            if c.project_id in locked:
                state.commitments[cid] = Commitment(
                    id=c.id, project_id=c.project_id, party=c.party,
                    shared_by=c.shared_by, share=c.share, signed_q=c.signed_q,
                    editable=False,
                )

    elif name == ev.PLAN_DISCARDED:
        state.plans[p["plan_id"]].status = "已废弃"

    elif name == ev.CHANGE_ORDER_PROPOSED:
        co = ChangeOrderState(
            id=p["co_id"], plan_id=p["plan_id"], kind=p["kind"],
            project_ids=tuple(p["project_ids"]), reason=p["reason"],
            proposer=p.get("proposer", ""), quarter=int(p["quarter"]),
        )
        state.change_orders[co.id] = co

    elif name == ev.CHANGE_ORDER_APPROVED:
        co = state.change_orders[p["co_id"]]
        plan = state.plans[co.plan_id]
        current = set(plan.project_ids)
        if co.kind == "add":
            current.update(co.project_ids)
        else:
            current.difference_update(co.project_ids)
        # 保持确定顺序：按候选工程导入顺序排序
        order = {pid: i for i, pid in enumerate(state.projects)}
        ids = tuple(sorted(current, key=lambda x: order.get(x, 1 << 30)))
        plan.project_ids = ids
        idx = len(plan.revisions)
        verb = "增补" if co.kind == "add" else "移除"
        label = f"变更单 {co.id}（{verb}：{'、'.join(co.project_ids)}）"
        plan.revisions = plan.revisions + (
            PlanRevision(idx, label, ids, int(p["quarter"]), f"变更理由：{co.reason}"),
        )
        co.status = "已批准"
        co.decided_q = int(p["quarter"])
        co.breach = bool(p.get("breach", False))

    elif name == ev.CHANGE_ORDER_REJECTED:
        co = state.change_orders[p["co_id"]]
        co.status = "已驳回"
        co.decided_q = int(p["quarter"])

    elif name == ev.LEDGER_RESET:
        state.seq = 0
        state.grids.clear()
        state.capabilities.clear()
        state.projects.clear()
        state.budgets.clear()
        state.commitments.clear()
        state.plans.clear()
        state.change_orders.clear()

    return state


def replay(events: Iterable[dict[str, Any]]) -> LedgerState:
    """从事件流回放得到完整状态。"""
    state = LedgerState()
    for evt in events:
        apply(state, evt)
    return state


def new_plan_id(state: LedgerState) -> str:
    return _next_plan_id(state)


def new_change_order_id(state: LedgerState) -> str:
    return _next_co_id(state)
