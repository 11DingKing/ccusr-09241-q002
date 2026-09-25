"""本地查询接口：面向账本状态的只读查询，供命令行与测试直接调用。"""

from __future__ import annotations

from ..domain import duplicates as duplicates_domain
from ..domain import impact
from ..domain.errors import NotFoundError
from ..domain.models import LedgerState, Plan


class QueryApi:
    """只读查询门面：所有方法返回可 JSON 序列化的普通字典。"""

    def __init__(self, state: LedgerState) -> None:
        self._state = state

    def _plan(self, plan_id: str) -> Plan:
        plan = self._state.plans.get(plan_id)
        if plan is None:
            raise NotFoundError(f"方案不存在：{plan_id}")
        return plan

    def info(self) -> dict:
        state = self._state
        return {
            "ledger_id": state.ledger_id,
            "name": state.name,
            "version": state.version,
            "counts": {
                "grids": len(state.grids),
                "capabilities": len(state.capabilities),
                "projects": len(state.projects),
                "budgets": len(state.budgets),
                "commitments": len(state.commitments),
                "plans": len(state.plans),
                "change_orders": len(state.change_orders),
            },
        }

    def grids(self) -> list[dict]:
        return [self._state.grids[key].to_dict() for key in sorted(self._state.grids)]

    def projects(self) -> list[dict]:
        return [self._state.projects[key].to_dict() for key in sorted(self._state.projects)]

    def budgets(self) -> list[dict]:
        return [self._state.budgets[key].to_dict() for key in sorted(self._state.budgets)]

    def commitments(self) -> list[dict]:
        return [self._state.commitments[key].to_dict() for key in sorted(self._state.commitments)]

    def plans(self) -> list[dict]:
        return [self._plan_summary(plan) for plan in (self._state.plans[key] for key in sorted(self._state.plans))]

    def plan(self, plan_id: str) -> dict:
        plan = self._plan(plan_id)
        result = self._plan_summary(plan)
        result["revisions"] = [revision.to_dict() for revision in plan.revisions]
        return result

    @staticmethod
    def _plan_summary(plan: Plan) -> dict:
        return {
            "id": plan.id,
            "name": plan.name,
            "status": plan.status,
            "revision": plan.revision,
            "selected": list(plan.selected),
        }

    def duplicates(self) -> dict:
        return duplicates_domain.detect_duplicates(self._state)

    def evaluate_plan(self, plan_id: str) -> dict:
        plan = self._plan(plan_id)
        return {
            "plan": self._plan_summary(plan),
            "evaluation": impact.evaluate_selection(self._state, plan.selected),
        }

    def uncovered(self, plan_id: str) -> dict:
        plan = self._plan(plan_id)
        evaluation = impact.evaluate_selection(self._state, plan.selected)
        return {"plan": plan_id, "uncovered": evaluation["uncovered"]}

    def compare(self, plan_a: str, plan_b: str) -> dict:
        first, second = self._plan(plan_a), self._plan(plan_b)
        eval_a = impact.evaluate_selection(self._state, first.selected)
        eval_b = impact.evaluate_selection(self._state, second.selected)
        delta = {
            "population_covered": eval_b["coverage"]["population_covered"]
            - eval_a["coverage"]["population_covered"],
            "grids_covered": eval_b["coverage"]["grids_covered"] - eval_a["coverage"]["grids_covered"],
            "capex": impact.money(eval_b["cost"]["capex"] - eval_a["cost"]["capex"]),
            "om_annual_selected": impact.money(
                eval_b["cost"]["om_annual_selected"] - eval_a["cost"]["om_annual_selected"]
            ),
            "tco_total": impact.money(eval_b["cost"]["tco_total"] - eval_a["cost"]["tco_total"]),
        }
        return {
            "a": {"plan": self._plan_summary(first), "evaluation": eval_a},
            "b": {"plan": self._plan_summary(second), "evaluation": eval_b},
            "delta": delta,
            "only_a": sorted(set(first.selected) - set(second.selected)),
            "only_b": sorted(set(second.selected) - set(first.selected)),
        }

    def change_orders(self, plan_id: str) -> list[dict]:
        self._plan(plan_id)
        orders = [
            order
            for order in (self._state.change_orders[key] for key in sorted(self._state.change_orders))
            if order.plan == plan_id
        ]
        return [order.to_dict() for order in orders]

    def events(self, limit: int | None = None) -> list[dict]:
        events = [dict(event) for event in self._state.events]
        if limit is not None and limit >= 0:
            return events[-limit:] if limit else []
        return events
