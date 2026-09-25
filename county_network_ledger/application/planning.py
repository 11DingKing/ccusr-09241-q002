"""规划应用服务：方案全生命周期、预算并发控制、冻结后变更单。

所有写操作：
- 显式传入业务季度（保证确定性，时间端口只负责信封记录时间）；
- 在 EventStore 事务（文件锁）内完成"读状态→校验→暂存事件"，
  并发修改同一预算时通过版本号拒绝，绝不静默覆盖；
- 工作副本可自由比较/合并/回退；正式方案冻结后只能经变更单修订。
"""
from __future__ import annotations

from typing import Any, Sequence

from ..domain import events as ev
from ..domain.analysis import (
    PlanEvaluation,
    compare,
    evaluate,
    find_duplicates,
)
from ..domain.errors import (
    CommitmentBreachError,
    ConcurrentModificationError,
    NotFoundError,
    PlanFrozenError,
    RejectedChangeOrderError,
    ValidationError,
)
from ..domain.state import LedgerState, new_change_order_id, new_plan_id
from ..persistence.event_store import EventStore

_IMPORT_ORDER_KEY = 1 << 30


def _require(cond: bool, msg: str) -> None:
    if not cond:
        raise ValidationError(msg)


def _ordered(state: LedgerState, ids: set[str] | tuple[str, ...]) -> tuple[str, ...]:
    """按候选工程导入顺序稳定排序。"""
    order = {pid: i for i, pid in enumerate(state.projects)}
    return tuple(sorted(ids, key=lambda x: (order.get(x, _IMPORT_ORDER_KEY), x)))


def _get_plan(state: LedgerState, plan_id: str):
    plan = state.plans.get(plan_id)
    if plan is None:
        raise NotFoundError(f"方案不存在：{plan_id}")
    return plan


def _guard_projects_exist(state: LedgerState, ids: Sequence[str]) -> None:
    missing = [i for i in ids if i not in state.projects]
    if missing:
        raise ValidationError(f"引用了不存在的候选工程：{'、'.join(missing)}")


class PlanningService:
    def __init__(self, store: EventStore) -> None:
        self.store = store

    # ============ 查询（只读，不加锁也可复算） ============

    def state(self) -> LedgerState:
        return self.store.state()

    def evaluate_plan(self, plan_id: str) -> PlanEvaluation:
        state = self.state()
        return evaluate(state, _get_plan(state, plan_id))

    def evaluate_ids(self, project_ids: Sequence[str]) -> PlanEvaluation:
        state = self.state()
        _guard_projects_exist(state, project_ids)
        return evaluate(state, tuple(project_ids))

    def compare_plans(self, plan_ids: Sequence[str]) -> list[dict[str, Any]]:
        state = self.state()
        evals = [evaluate(state, _get_plan(state, pid)) for pid in plan_ids]
        return compare(evals)

    def duplicates_overall(self) -> list[dict[str, Any]]:
        """跨申报方扫描：全部候选工程之间的空间/时间重复（季度汇总查重用）。"""
        state = self.state()
        out = []
        for d in find_duplicates(state, tuple(state.projects)):
            out.append({
                "project_a": d.project_a, "project_b": d.project_b,
                "grid_id": d.grid_id, "severity": d.severity,
                "spatial": d.spatial, "temporal": d.temporal,
                "same_kind": d.same_kind,
                "proposer_a": state.projects.get(d.project_a).proposer
                    if d.project_a in state.projects else "(存量)",
                "proposer_b": state.projects.get(d.project_b).proposer
                    if d.project_b in state.projects else "(存量)",
            })
        return out

    def suggest_for_uncovered(self, plan_id: str) -> list[dict[str, Any]]:
        """针对未覆盖网格给出候选工程建议（偏向低成本、高新增覆盖人口）。

        用于发现"收益较低无人申报覆盖"的偏远村落：按每万元新增覆盖人口
        降序排列，输出确定顺序；不自动改方案，仅供规划人员取舍。
        """
        state = self.state()
        plan = _get_plan(state, plan_id)
        base = evaluate(state, plan)
        covered = set(base.covered_grids)
        suggestions = []
        for pid, pr in state.projects.items():
            if pid in plan.project_ids:
                continue
            gain = sum(state.grids[g].population for g in pr.covers
                       if g in state.grids and g not in covered)
            gain_grids = [g for g in pr.covers if g in state.grids and g not in covered]
            if not gain_grids:
                continue
            cost = pr.cost * (1 - state.commitment_share(pid))
            efficiency = gain / cost if cost > 0 else float(gain)
            suggestions.append({
                "project_id": pid, "name": pr.name, "kind": pr.kind,
                "newly_covered_grids": tuple(sorted(gain_grids)),
                "newly_covered_population": gain,
                "county_cost": round(cost, 2),
                "annual_revenue": pr.revenue,
                "people_per_10k_yuan": round(efficiency * 10000, 4),
                "proposer": pr.proposer,
            })
        suggestions.sort(key=lambda r: (-r["people_per_10k_yuan"], r["project_id"]))
        return suggestions

    # ============ 预算批次（乐观并发） ============

    def update_budget(self, budget_id: str, amount: float, quarter: int,
                      expected_version: int) -> dict[str, Any]:
        """调整预算批次额度。

        ``expected_version`` 为调用方读到的版本号；事务内复核，
        版本不符抛 ConcurrentModificationError，调用方须刷新后重试，
        从而避免两人同时缩减/追加预算时静默覆盖。
        """
        _require(amount >= 0, "预算额度必须为非负数")
        with self.store.transaction() as tx:
            budget = tx.state.budgets.get(budget_id)
            if budget is None:
                raise NotFoundError(f"预算批次不存在：{budget_id}")
            if budget.version != expected_version:
                raise ConcurrentModificationError(
                    f"预算批次 {budget_id}", expected_version, budget.version)
            tx.add(ev.BUDGET_UPDATED, {
                "id": budget_id, "name": budget.name,
                "amount": float(amount), "quarter": int(quarter),
            })
            applied_version = budget.version + 1
        return {"budget_id": budget_id, "version": applied_version, "amount": amount}

    # ============ 方案工作副本 ============

    def create_plan(self, name: str, quarter: int,
                    project_ids: Sequence[str] = (), *,
                    basis: str | None = None, note: str = "") -> str:
        _require(name.strip(), "方案名称不能为空")
        _require(bool(note.strip()), "创建方案必须填写取舍理由/编制说明（note）")
        with self.store.transaction() as tx:
            if basis is not None and basis not in tx.state.plans:
                raise NotFoundError(f"来源方案不存在：{basis}")
            _guard_projects_exist(tx.state, project_ids)
            plan_id = new_plan_id(tx.state)
            tx.add(ev.PLAN_CREATED, {
                "plan_id": plan_id, "name": name, "quarter": int(quarter),
                "project_ids": list(_ordered(tx.state, set(project_ids))),
                "basis": basis, "note": note,
            })
        return plan_id

    def set_projects(self, plan_id: str, project_ids: Sequence[str],
                     quarter: int, note: str) -> None:
        """全量设置工作副本选入工程（历史版本自动保留，可回退）。"""
        _require(bool(note.strip()), "调整方案必须填写取舍理由（note）")
        with self.store.transaction() as tx:
            plan = _get_plan(tx.state, plan_id)
            if plan.status == "已冻结":
                raise PlanFrozenError(
                    f"方案 {plan_id} 已冻结，不能直接调整；请提交变更单（change-order）")
            if plan.status == "已废弃":
                raise ValidationError(f"方案 {plan_id} 已废弃，不可修改")
            _guard_projects_exist(tx.state, project_ids)
            tx.add(ev.PLAN_PROJECTS_SET, {
                "plan_id": plan_id, "quarter": int(quarter),
                "project_ids": list(_ordered(tx.state, set(project_ids))),
                "note": note,
            })

    def merge_plan(self, target_id: str, source_id: str, quarter: int,
                   note: str, exclude: Sequence[str] = ()) -> None:
        """将来源方案的工程并入工作副本（并集去重），不改动来源方案。

        合并是可回退的一次修订；被合并方案若已冻结，其承诺锁定工程
        同样不得通过 exclude 剔除（剔除请走变更单并留痕违约）。
        """
        _require(bool(note.strip()), "合并方案必须填写取舍理由（note）")
        with self.store.transaction() as tx:
            target = _get_plan(tx.state, target_id)
            source = _get_plan(tx.state, source_id)
            if target.status == "已冻结":
                raise PlanFrozenError(f"方案 {target_id} 已冻结，不能直接合并")
            if target.status == "已废弃":
                raise ValidationError(f"方案 {target_id} 已废弃")
            committed = {c.project_id for c in tx.state.commitments.values()
                         if not c.editable}
            excluded = set(exclude)
            dropped_locked = excluded & committed
            if dropped_locked:
                raise CommitmentBreachError(
                    f"合并时不得剔除已被冻结承诺锁定的工程：{'、'.join(sorted(dropped_locked))}")
            merged = (set(target.project_ids) | set(source.project_ids)) - excluded
            tx.add(ev.PLAN_MERGED, {
                "plan_id": target_id, "from_plan": source_id,
                "quarter": int(quarter),
                "project_ids": list(_ordered(tx.state, merged)),
                "note": note,
            })

    def rollback_plan(self, plan_id: str, target_index: int,
                      quarter: int, note: str) -> None:
        """回退工作副本到某一历史版本（回退本身也留一个新版本，可再前进）。"""
        _require(bool(note.strip()), "回退方案必须填写取舍理由（note）")
        with self.store.transaction() as tx:
            plan = _get_plan(tx.state, plan_id)
            if plan.status == "已冻结":
                raise PlanFrozenError(f"方案 {plan_id} 已冻结，不能回退；请提交变更单")
            if plan.status == "已废弃":
                raise ValidationError(f"方案 {plan_id} 已废弃")
            _require(0 <= target_index < len(plan.revisions),
                     f"回退目标版本 {target_index} 不存在（共 {len(plan.revisions)} 版）")
            tx.add(ev.PLAN_ROLLED_BACK, {
                "plan_id": plan_id, "target_index": int(target_index),
                "quarter": int(quarter), "note": note,
            })

    def discard_plan(self, plan_id: str, quarter: int) -> None:
        """废弃未冻结方案。"""
        with self.store.transaction() as tx:
            plan = _get_plan(tx.state, plan_id)
            if plan.status == "已冻结":
                raise PlanFrozenError("已冻结的正式方案不能废弃，只能通过变更单修订")
            if plan.status != "已废弃":
                tx.add(ev.PLAN_DISCARDED, {"plan_id": plan_id, "quarter": int(quarter)})

    def freeze_plan(self, plan_id: str, quarter: int, reason: str,
                    acknowledge_duplicates: bool = False) -> PlanEvaluation:
        """冻结为正式方案。

        前置条件：
        - 不存在预算超支与跨期依赖违例；
        - 已签署共建承诺涉及的工程必须全部在案（不破坏承诺）；
        - 仍有"空间+时间双重重复"需显式确认（acknowledge_duplicates）。
        """
        _require(bool(reason.strip()), "冻结正式方案必须说明理由（reason）")
        with self.store.transaction() as tx:
            plan = _get_plan(tx.state, plan_id)
            if plan.status == "已冻结":
                raise ValidationError(f"方案 {plan_id} 已处于冻结状态")
            if plan.status == "已废弃":
                raise ValidationError(f"方案 {plan_id} 已废弃，不能冻结")
            evaluation = evaluate(tx.state, plan)
            violations = evaluation.violations()
            if violations:
                raise ValidationError("方案不具备冻结条件：" + "；".join(violations))
            if evaluation.commitment_removed:
                raise CommitmentBreachError(
                    "以下工程已签共建承诺但未纳入方案，冻结将破坏承诺："
                    + "、".join(evaluation.commitment_removed))
            hard_dups = [d for d in evaluation.duplicates
                         if d.severity == "空间+时间双重重复"]
            if hard_dups and not acknowledge_duplicates:
                raise ValidationError(
                    "方案仍存在 " + str(len(hard_dups))
                    + " 处空间+时间双重重复；如已协调共址/分期，请显式确认后再冻结")
            tx.add(ev.PLAN_FROZEN, {
                "plan_id": plan_id, "quarter": int(quarter),
                "reason": reason,
                "duplicates_acknowledged": bool(acknowledge_duplicates and hard_dups),
            })
            return evaluation

    # ============ 冻结后的变更单 ============

    def propose_change_order(self, plan_id: str, kind: str,
                             project_ids: Sequence[str], quarter: int,
                             reason: str, proposer: str = "") -> str:
        _require(kind in ("add", "remove"), "变更单类型必须是 add 或 remove")
        _require(project_ids, "变更单至少包含一项工程")
        _require(bool(reason.strip()), "变更单必须说明变更理由（reason）")
        with self.store.transaction() as tx:
            plan = _get_plan(tx.state, plan_id)
            if plan.status != "已冻结":
                raise ValidationError("只有已冻结的正式方案才能提交变更单")
            _guard_projects_exist(tx.state, project_ids)
            current = set(plan.project_ids)
            if kind == "add" and not (set(project_ids) - current):
                raise ValidationError("增补变更单中的工程均已在方案中")
            if kind == "remove" and not (set(project_ids) & current):
                raise ValidationError("移除变更单中的工程均不在方案中")
            co_id = new_change_order_id(tx.state)
            tx.add(ev.CHANGE_ORDER_PROPOSED, {
                "co_id": co_id, "plan_id": plan_id, "kind": kind,
                "project_ids": list(_ordered(tx.state, set(project_ids))),
                "reason": reason, "proposer": proposer, "quarter": int(quarter),
            })
        return co_id

    def approve_change_order(self, co_id: str, quarter: int,
                             acknowledge_breach: bool = False) -> dict[str, Any]:
        """批准并执行变更单。执行前重新评估，超预算/违依赖则拒绝。

        移除已签承诺工程构成承诺违约：必须显式 acknowledge_breach，
        执行结果在方案修订与变更单上永久留痕。
        """
        with self.store.transaction() as tx:
            co = tx.state.change_orders.get(co_id)
            if co is None:
                raise NotFoundError(f"变更单不存在：{co_id}")
            if co.status == "已驳回":
                raise RejectedChangeOrderError(f"变更单 {co_id} 已被驳回，不能执行")
            if co.status == "已批准":
                raise ValidationError(f"变更单 {co_id} 已执行")
            plan = _get_plan(tx.state, co.plan_id)
            if plan.status != "已冻结":
                raise ValidationError("目标方案不是冻结状态")

            candidate = set(plan.project_ids)
            if co.kind == "add":
                candidate.update(co.project_ids)
            else:
                candidate.difference_update(co.project_ids)
            trial = evaluate(tx.state, _ordered(tx.state, candidate),
                             plan_id=plan.id)
            violations = trial.violations()
            if violations:
                raise ValidationError("变更执行后方案不可行，已拒绝：" + "；".join(violations))

            breach = False
            if co.kind == "remove":
                committed = {c.project_id for c in tx.state.commitments.values()}
                breach = bool(set(co.project_ids) & committed)
                if breach and not acknowledge_breach:
                    raise CommitmentBreachError(
                        "移除工程 " + "、".join(sorted(set(co.project_ids) & committed))
                        + " 将破坏已签共建承诺；如已履行解约/赔付，请显式确认违约留痕")
            tx.add(ev.CHANGE_ORDER_APPROVED, {
                "co_id": co_id, "quarter": int(quarter), "breach": breach,
            })
            return {"co_id": co_id, "breach": breach,
                    "project_ids": list(_ordered(tx.state, candidate))}

    def reject_change_order(self, co_id: str, quarter: int) -> None:
        with self.store.transaction() as tx:
            co = tx.state.change_orders.get(co_id)
            if co is None:
                raise NotFoundError(f"变更单不存在：{co_id}")
            if co.status != "待审批":
                raise ValidationError(f"变更单 {co_id} 当前状态为 {co.status}，不能驳回")
            tx.add(ev.CHANGE_ORDER_REJECTED, {"co_id": co_id, "quarter": int(quarter)})
