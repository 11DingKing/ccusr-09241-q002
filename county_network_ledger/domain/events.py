"""领域事件：账本中唯一的事实来源。

事件为不可变字典结构，经持久化层写入哈希链 JSONL。
所有状态都由事件回放得到；事件名即事实，不记录"命令失败"。
"""
from __future__ import annotations

from typing import Any

# 台账事实（导入产生）
GRID_IMPORTED = "GridImported"
CAPABILITY_IMPORTED = "CapabilityImported"
PROJECT_IMPORTED = "ProjectImported"
BUDGET_IMPORTED = "BudgetImported"
COMMITMENT_SIGNED = "CommitmentSigned"

# 预算维护（带乐观版本）
BUDGET_UPDATED = "BudgetUpdated"

# 方案生命周期
PLAN_CREATED = "PlanCreated"
PLAN_PROJECTS_SET = "PlanProjectsSet"  # 全量设置选入工程（工作副本）
PLAN_FROZEN = "PlanFrozen"
PLAN_MERGED = "PlanMerged"  # 将另一方案的工程并入工作副本
PLAN_ROLLED_BACK = "PlanRolledBack"  # 回退到历史版本

# 冻结后修订
CHANGE_ORDER_PROPOSED = "ChangeOrderProposed"
CHANGE_ORDER_APPROVED = "ChangeOrderApproved"
CHANGE_ORDER_REJECTED = "ChangeOrderRejected"

# 生命周期结束（未冻结方案允许废弃）
PLAN_DISCARDED = "PlanDiscarded"

LEDGER_RESET = "LedgerReset"  # 仅用于测试/重新初始化


def event(name: str, payload: dict[str, Any], seq: int) -> dict[str, Any]:
    """组装一条事件（哈希与时间戳由持久化层补全）。"""
    return {"seq": seq, "name": name, "payload": payload}
