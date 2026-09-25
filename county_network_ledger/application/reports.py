"""确定性报告：汇总方案评估、重复建设、取舍理由与未覆盖清单。

报告不引入任何报告生成时刻的外部状态（如当前时间），同一账本状态
必然渲染出逐字节相同的报告文本。
"""

from __future__ import annotations

import json

from ..domain import duplicates as duplicates_domain
from ..domain import impact
from ..domain.errors import NotFoundError
from ..domain.models import LedgerState

REPORT_KIND = "county-network-plan-report"
SCHEMA_VERSION = 1


def build_plan_report(state: LedgerState, plan_id: str) -> dict:
    plan = state.plans.get(plan_id)
    if plan is None:
        raise NotFoundError(f"方案不存在：{plan_id}")
    evaluation = impact.evaluate_selection(state, plan.selected)
    orders = [
        state.change_orders[key].to_dict()
        for key in sorted(state.change_orders)
        if state.change_orders[key].plan == plan_id
    ]
    return {
        "report": REPORT_KIND,
        "schema_version": SCHEMA_VERSION,
        "ledger": {"id": state.ledger_id, "name": state.name, "version": state.version},
        "plan": {
            "id": plan.id,
            "name": plan.name,
            "status": plan.status,
            "revision": plan.revision,
            "selected": list(plan.selected),
        },
        "evaluation": evaluation,
        "duplicates": duplicates_domain.detect_duplicates(state),
        "revision_log": [revision.to_dict() for revision in plan.revisions],
        "change_orders": orders,
    }


def render_report(report: dict) -> str:
    """渲染为确定性 JSON 文本（键排序、固定缩进、末尾换行）。"""
    return json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
