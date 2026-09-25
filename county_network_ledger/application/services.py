"""应用服务：台账导入、方案管理、预算调整的门面，组合持久化、时钟与领域逻辑。"""

from __future__ import annotations

import os

from ..domain import impact
from ..domain import merge as merge_domain
from ..domain.errors import (
    CommitmentViolationError,
    FrozenPlanError,
    NotFoundError,
    StaleVersionError,
    StoreError,
    ValidationError,
)
from ..domain.models import (
    ORIGIN_CHANGE_ORDER,
    ORIGIN_CREATE,
    ORIGIN_MANUAL,
    ORIGIN_MERGE,
    ORIGIN_ROLLBACK,
    PLAN_DRAFT,
    PLAN_FROZEN,
    ChangeOrder,
    LedgerState,
    Plan,
    PlanRevision,
    build_state_from_import,
)
from ..persistence.json_store import JsonStore
from ..ports.clock import FixedClock, SystemClock
from .queries import QueryApi
from .reports import build_plan_report


def _default_clock():
    """支持用环境变量固定时间，便于验收时逐字节复现留痕与报告。"""
    fixed = os.environ.get("CNL_FIXED_TIME")
    return FixedClock(fixed) if fixed else SystemClock()


class Application:
    """规划后端应用门面：每个实例绑定一个数据目录与一个可替换时钟。"""

    def __init__(self, data_dir: str, clock=None) -> None:
        self.store = JsonStore(data_dir)
        self.clock = clock if clock is not None else _default_clock()
        self.state: LedgerState | None = self.store.load() if self.store.exists() else None

    # ------------------------------------------------------------------
    # 台账
    # ------------------------------------------------------------------
    def import_ledger(self, raw: dict, replace: bool = False) -> dict:
        if self.store.exists() and not replace:
            raise StoreError("台账已存在，如需覆盖请显式指定 replace")
        state = build_state_from_import(raw)
        state.version = 1
        state.record("import", f"导入台账 {state.ledger_id}", self.clock.now_iso())
        self.store.save(state)
        self.state = state
        return {
            "ledger_id": state.ledger_id,
            "version": state.version,
            "grids": len(state.grids),
            "capabilities": len(state.capabilities),
            "projects": len(state.projects),
            "budgets": len(state.budgets),
            "commitments": len(state.commitments),
        }

    @property
    def queries(self) -> QueryApi:
        return QueryApi(self._require_state())

    def report(self, plan_id: str) -> dict:
        return build_plan_report(self._require_state(), plan_id)

    # ------------------------------------------------------------------
    # 方案
    # ------------------------------------------------------------------
    def create_plan(self, name: str, projects: list[str] | tuple[str, ...] = ()) -> dict:
        state = self._require_state()
        if not isinstance(name, str) or not name.strip():
            raise ValidationError("方案名称不能为空")
        selected = self._checked_selection(state, projects)
        plan_id = state.next_id("plan")
        revision = PlanRevision(
            rev=1,
            selected=selected,
            note="创建方案",
            origin=ORIGIN_CREATE,
            at=self.clock.now_iso(),
        )
        state.plans[plan_id] = Plan(id=plan_id, name=name.strip(), status=PLAN_DRAFT, revisions=[revision])
        self._save("plan.create", f"创建方案 {plan_id}（{name.strip()}）")
        return {"plan": plan_id, "revision": 1, "selected": list(selected)}

    def update_plan(self, plan_id: str, expected_revision: int, projects: list[str] | tuple[str, ...]) -> dict:
        state = self._require_state()
        plan = self._get_plan(state, plan_id)
        if plan.status == PLAN_FROZEN:
            raise FrozenPlanError(f"方案 {plan_id} 已冻结，只能通过留痕变更单修订")
        if plan.revision != expected_revision:
            raise StaleVersionError(
                f"方案 {plan_id} 当前修订为 {plan.revision}，与期望修订 {expected_revision} 不一致，已拒绝静默覆盖"
            )
        selected = self._checked_selection(state, projects)
        plan.revisions.append(
            PlanRevision(
                rev=plan.revision + 1,
                selected=selected,
                note="调整已选工程",
                origin=ORIGIN_MANUAL,
                at=self.clock.now_iso(),
            )
        )
        self._save("plan.update", f"修订方案 {plan_id} 至第 {plan.revision} 版")
        return {"plan": plan_id, "revision": plan.revision, "selected": list(selected)}

    def freeze_plan(self, plan_id: str) -> dict:
        state = self._require_state()
        plan = self._get_plan(state, plan_id)
        if plan.status == PLAN_FROZEN:
            raise ValidationError(f"方案 {plan_id} 已处于冻结状态")
        evaluation = impact.evaluate_selection(state, plan.selected)
        if evaluation["has_violations"]:
            problems = [item["detail"] for item in evaluation["violations"]["dependencies"]]
            problems += [item["detail"] for item in evaluation["violations"]["budget"]]
            problems += [
                f"工程 {item['project']} 计划季度 {item['quarter']} 缺少预算批次"
                for item in evaluation["violations"]["missing_batches"]
            ]
            raise ValidationError([f"方案存在约束冲突，不能冻结：{text}" for text in problems])
        plan.status = PLAN_FROZEN
        self._save("plan.freeze", f"冻结方案 {plan_id}（第 {plan.revision} 版）")
        return {"plan": plan_id, "status": plan.status, "revision": plan.revision}

    def rollback_plan(self, plan_id: str, to_revision: int) -> dict:
        state = self._require_state()
        plan = self._get_plan(state, plan_id)
        if plan.status == PLAN_FROZEN:
            raise FrozenPlanError(f"方案 {plan_id} 已冻结，回退须通过留痕变更单进行")
        target = next((rev for rev in plan.revisions if rev.rev == to_revision), None)
        if target is None:
            raise NotFoundError(f"方案 {plan_id} 不存在修订 {to_revision}")
        self._assert_commitments_kept(state, target.selected)
        plan.revisions.append(
            PlanRevision(
                rev=plan.revision + 1,
                selected=tuple(target.selected),
                note=f"回退至修订 {to_revision}",
                origin=ORIGIN_ROLLBACK,
                at=self.clock.now_iso(),
            )
        )
        self._save("plan.rollback", f"方案 {plan_id} 回退至修订 {to_revision}（生成第 {plan.revision} 版）")
        return {"plan": plan_id, "revision": plan.revision, "selected": list(plan.selected)}

    def merge_plans(self, plan_ids: list[str] | tuple[str, ...], name: str) -> dict:
        state = self._require_state()
        if len(plan_ids) < 2:
            raise ValidationError("合并至少需要两个方案")
        if not isinstance(name, str) or not name.strip():
            raise ValidationError("合并方案名称不能为空")
        parents = [self._get_plan(state, pid) for pid in plan_ids]
        merged, drops = merge_domain.merge_selections(state, [parent.selected for parent in parents])
        self._assert_commitments_kept(state, merged)
        plan_id = state.next_id("plan")
        revision = PlanRevision(
            rev=1,
            selected=tuple(merged),
            note=f"合并方案 {'、'.join(plan_ids)}",
            origin=ORIGIN_MERGE,
            at=self.clock.now_iso(),
            drops=tuple(drops),
        )
        state.plans[plan_id] = Plan(id=plan_id, name=name.strip(), status=PLAN_DRAFT, revisions=[revision])
        self._save("plan.merge", f"合并 {'、'.join(plan_ids)} 生成方案 {plan_id}（{name.strip()}）")
        return {"plan": plan_id, "revision": 1, "selected": merged, "drops": drops}

    def apply_change_order(
        self,
        plan_id: str,
        base_revision: int,
        add: list[str] | tuple[str, ...] = (),
        remove: list[str] | tuple[str, ...] = (),
        reason: str = "",
        author: str = "",
    ) -> dict:
        state = self._require_state()
        plan = self._get_plan(state, plan_id)
        if plan.status != PLAN_FROZEN:
            raise ValidationError(f"方案 {plan_id} 尚未冻结，可直接修订，无需变更单")
        if plan.revision != base_revision:
            raise StaleVersionError(
                f"方案 {plan_id} 当前修订为 {plan.revision}，变更单基于修订 {base_revision}，已拒绝套用"
            )
        if not reason.strip():
            raise ValidationError("变更单必须填写理由（留痕要求）")
        if not author.strip():
            raise ValidationError("变更单必须填写经办人（留痕要求）")
        self._assert_projects_exist(state, add)
        self._assert_projects_exist(state, remove)
        current = set(plan.selected)
        not_selected = [pid for pid in remove if pid not in current]
        if not_selected:
            raise ValidationError(f"变更单移除的工程不在方案中：{'、'.join(sorted(not_selected))}")
        new_selected = tuple(sorted((current - set(remove)) | set(add)))
        self._assert_commitments_kept(state, new_selected)
        order_id = state.next_id("co")
        order = ChangeOrder(
            id=order_id,
            plan=plan_id,
            base_rev=base_revision,
            add=tuple(sorted(add)),
            remove=tuple(sorted(remove)),
            reason=reason.strip(),
            author=author.strip(),
            at=self.clock.now_iso(),
        )
        state.change_orders[order_id] = order
        plan.revisions.append(
            PlanRevision(
                rev=plan.revision + 1,
                selected=new_selected,
                note=f"变更单 {order_id}：{order.reason}",
                origin=ORIGIN_CHANGE_ORDER,
                at=self.clock.now_iso(),
                change_order=order_id,
            )
        )
        self._save("plan.change_order", f"变更单 {order_id} 修订方案 {plan_id} 至第 {plan.revision} 版")
        return {"change_order": order_id, "plan": plan_id, "revision": plan.revision, "selected": list(new_selected)}

    # ------------------------------------------------------------------
    # 预算
    # ------------------------------------------------------------------
    def update_budget(
        self,
        batch_id: str,
        expected_version: int,
        total: float | None = None,
        name: str | None = None,
    ) -> dict:
        state = self._require_state()
        batch = state.budgets.get(batch_id)
        if batch is None:
            raise NotFoundError(f"预算批次不存在：{batch_id}")
        if batch.version != expected_version:
            raise StaleVersionError(
                f"预算批次 {batch_id} 当前版本为 {batch.version}，与期望版本 {expected_version} 不一致，已拒绝静默覆盖"
            )
        if total is None and name is None:
            raise ValidationError("预算调整至少需要提供 total 或 name 之一")
        if total is not None:
            if not isinstance(total, (int, float)) or isinstance(total, bool) or total < 0:
                raise ValidationError("预算总额必须是非负数值")
            batch.total = float(total)
        if name is not None:
            if not name.strip():
                raise ValidationError("预算批次名称不能为空")
            batch.name = name.strip()
        batch.version += 1
        self._save("budget.update", f"调整预算批次 {batch_id} 至版本 {batch.version}（总额 {batch.total} 万元）")
        return {"batch": batch_id, "version": batch.version, "total": batch.total, "name": batch.name}

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------
    def _require_state(self) -> LedgerState:
        if self.state is None:
            raise StoreError("尚未导入台账，请先执行 import")
        return self.state

    def _save(self, op: str, detail: str) -> None:
        state = self._require_state()
        state.version += 1
        state.record(op, detail, self.clock.now_iso())
        self.store.save(state)

    @staticmethod
    def _get_plan(state: LedgerState, plan_id: str) -> Plan:
        plan = state.plans.get(plan_id)
        if plan is None:
            raise NotFoundError(f"方案不存在：{plan_id}")
        return plan

    @staticmethod
    def _assert_projects_exist(state: LedgerState, projects) -> None:
        unknown = sorted({pid for pid in projects if pid not in state.projects})
        if unknown:
            raise NotFoundError(f"候选工程不存在：{'、'.join(unknown)}")

    @staticmethod
    def _assert_commitments_kept(state: LedgerState, selected) -> None:
        missing = sorted(state.committed_projects() - set(selected))
        if missing:
            raise CommitmentViolationError(f"方案必须保留已签承诺工程：{'、'.join(missing)}")

    def _checked_selection(self, state: LedgerState, projects) -> tuple[str, ...]:
        self._assert_projects_exist(state, projects)
        selected = tuple(sorted(dict.fromkeys(projects)))
        self._assert_commitments_kept(state, selected)
        return selected
