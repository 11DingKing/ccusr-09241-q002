"""方案生命周期：比较/合并/回退、乐观并发、冻结与承诺、变更单。"""
from __future__ import annotations

import unittest

from county_network_ledger.application.planning import PlanningService
from county_network_ledger.domain.errors import (
    CommitmentBreachError,
    ConcurrentModificationError,
    PlanFrozenError,
    RejectedChangeOrderError,
    ValidationError,
)

from _helpers import seeded_store

Q = 2  # 统一业务季度


def _service():
    tmp, store = seeded_store()
    return tmp, PlanningService(store)


class BudgetConcurrencyTests(unittest.TestCase):
    def test_stale_version_rejected_not_silently_overwritten(self) -> None:
        tmp, svc = _service()
        self.addCleanup(tmp.cleanup)
        # 两人同时读到版本 0：甲先缩减到 200 万成功
        r1 = svc.update_budget("B1", 2_000_000, Q, expected_version=0)
        self.assertEqual(r1["version"], 1)
        # 乙仍持版本 0 追加预算：必须被拒绝
        with self.assertRaises(ConcurrentModificationError) as ctx:
            svc.update_budget("B1", 6_000_000, Q, expected_version=0)
        self.assertEqual(ctx.exception.current, 1)
        # 乙刷新到版本 1 后重试成功，甲的缩减未被静默覆盖丢失
        r2 = svc.update_budget("B1", 6_000_000, Q, expected_version=1)
        self.assertEqual(r2["version"], 2)
        self.assertEqual(svc.state().budgets["B1"].amount, 6_000_000)

    def test_first_then_reduced_budget_blocks_overweight_plan(self) -> None:
        tmp, svc = _service()
        self.addCleanup(tmp.cleanup)
        svc.update_budget("B1", 2_000_000, Q, expected_version=0)
        evl = svc.evaluate_ids(("P-01", "P-04", "P-06"))
        self.assertFalse(evl.feasible)


class PlanLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp, self.svc = _service()
        self.addCleanup(self.tmp.cleanup)

    def _plan(self, projects, name="测试方案", note="季度统筹取舍"):
        return self.svc.create_plan(name, Q, projects, note=note)

    def test_note_is_mandatory(self) -> None:
        with self.assertRaises(ValidationError):
            self.svc.create_plan("x", Q, ("P-01",), note="  ")

    def test_compare_alternative_plans(self) -> None:
        lean = self._plan(("P-01", "P-04"), "节约方案",
                          note="优先解决空白与退服村落")
        full = self._plan(("P-01", "P-03", "P-04", "P-05", "P-06", "P-07"),
                          "全面方案", note="产业与普惠并重，接受更高运维")
        rows = {r["plan_id"]: r for r in self.svc.compare_plans([lean, full])}
        self.assertGreater(rows[full]["basic_population"], rows[lean]["basic_population"])
        self.assertGreater(rows[full]["annual_maintenance"], rows[lean]["annual_maintenance"])
        self.assertTrue(rows[full]["feasible"] and rows[lean]["feasible"])

    def test_merge_union_and_rollback(self) -> None:
        a = self._plan(("P-01",), "甲方案", note="先保河西")
        b = self._plan(("P-04", "P-05"), "乙方案", note="南山与高山")
        self.svc.merge_plan(a, b, Q, note="合并两班建议，去重后统一实施")
        merged = self.svc.state().plans[a]
        self.assertEqual(set(merged.project_ids), {"P-01", "P-04", "P-05"})
        # 来源方案不被改动
        self.assertEqual(self.svc.state().plans[b].project_ids, ("P-04", "P-05"))
        # 回退到第 0 版（仅 P-01）
        self.svc.rollback_plan(a, 0, Q, note="高山村本季收益过低，暂缓")
        self.assertEqual(self.svc.state().plans[a].project_ids, ("P-01",))
        # 回退本身留痕：版本序列变长，取舍理由可查
        labels = [r.note for r in self.svc.state().plans[a].revisions]
        self.assertIn("高山村本季收益过低，暂缓", labels)

    def test_discard_unfrozen_allowed(self) -> None:
        pid = self._plan(("P-01",))
        self.svc.discard_plan(pid, Q)
        self.assertEqual(self.svc.state().plans[pid].status, "已废弃")
        with self.assertRaises(ValidationError):
            self.svc.set_projects(pid, ("P-04",), Q, note="再改")

    def test_suggest_targets_uncovered_blank_grids(self) -> None:
        pid = self._plan(("P-01", "P-06"), "基线", note="先保河西与光纤主干")
        suggestions = self.svc.suggest_for_uncovered(pid)
        by_id = {s["project_id"]: s for s in suggestions}
        # 高山村（空白、低收益）应能被 P-05/P-07 补建建议命中
        self.assertIn("P-05", by_id)
        self.assertIn("G-04", by_id["P-05"]["newly_covered_grids"])
        # 已在方案中的工程不再建议
        self.assertNotIn("P-01", by_id)
        # 建议按每万元新增覆盖人口降序
        eff = [s["people_per_10k_yuan"] for s in suggestions]
        self.assertEqual(eff, sorted(eff, reverse=True))

    def test_evaluate_by_ids(self) -> None:
        evl = self.svc.evaluate_ids(("P-01", "P-06"))
        self.assertIn("G-03", evl.covered_grids)
        with self.assertRaises(ValidationError):
            self.svc.evaluate_ids(("P-XX",))


class FreezeAndCommitmentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp, self.svc = _service()
        self.addCleanup(self.tmp.cleanup)

    def _freezable(self):
        # P-06 有已签承诺必须纳入；P-03 有承诺也必须纳入
        pid = self.svc.create_plan("正式方案", Q,
                                   ("P-01", "P-03", "P-04", "P-06"),
                                   note="覆盖河西/南山，产业园5G按承诺共建")
        return pid

    def test_freeze_blocks_direct_edit(self) -> None:
        pid = self._freezable()
        self.svc.freeze_plan(pid, Q, reason="季度专班审定")
        with self.assertRaises(PlanFrozenError):
            self.svc.set_projects(pid, ("P-01",), Q, note="想直接改")
        with self.assertRaises(PlanFrozenError):
            self.svc.rollback_plan(pid, 0, Q, note="想直接回退")

    def test_freeze_refused_when_commitment_missing(self) -> None:
        pid = self.svc.create_plan("缺承诺方案", Q, ("P-01", "P-04"),
                                   note="未包含已签承诺工程")
        with self.assertRaises(CommitmentBreachError):
            self.svc.freeze_plan(pid, Q, reason="试图冻结")
        self.assertEqual(self.svc.state().plans[pid].status, "工作副本")

    def test_freeze_refused_with_hard_duplicate_until_acknowledged(self) -> None:
        # P-01/P-02 双重重复，且承诺工程 P-03、P-06 均纳入
        pid = self.svc.create_plan("重复方案", Q,
                                   ("P-01", "P-02", "P-03", "P-06"),
                                   note="含两家对河西的重复申报")
        with self.assertRaisesRegex(ValidationError, "双重重复"):
            self.svc.freeze_plan(pid, Q, reason="送审")
        self.svc.freeze_plan(pid, Q, reason="已协调移动撤项、电信共址",
                             acknowledge_duplicates=True)
        self.assertEqual(self.svc.state().plans[pid].status, "已冻结")

    def test_freeze_refused_on_budget_overrun(self) -> None:
        self.svc.update_budget("B1", 1_500_000, Q, expected_version=0)
        pid = self.svc.create_plan("超支方案", Q, ("P-01", "P-03", "P-06"),
                                   note="预算缩减后仍超额")
        with self.assertRaisesRegex(ValidationError, "超支"):
            self.svc.freeze_plan(pid, Q, reason="送审")

    def test_change_order_lifecycle_after_freeze(self) -> None:
        pid = self._freezable()
        self.svc.freeze_plan(pid, Q, reason="季度专班审定")
        # 增补 P-05 覆盖偏远高山村
        co = self.svc.propose_change_order(pid, "add", ("P-05",), Q + 1,
                                           reason="高山村信访集中，动用续建资金",
                                           proposer="专班")
        self.assertEqual(co, "BG-0001")
        out = self.svc.approve_change_order(co, Q + 1)
        self.assertFalse(out["breach"])
        self.assertIn("P-05", self.svc.state().plans[pid].project_ids)
        plan = self.svc.state().plans[pid]
        self.assertTrue(any("BG-0001" in r.label for r in plan.revisions))

    def test_change_order_remove_committed_requires_breach_ack(self) -> None:
        pid = self._freezable()
        self.svc.freeze_plan(pid, Q, reason="审定")
        co = self.svc.propose_change_order(pid, "remove", ("P-06",), Q + 1,
                                           reason="广电毁约，光纤暂缓")
        with self.assertRaises(CommitmentBreachError):
            self.svc.approve_change_order(co, Q + 1)
        # 显式确认违约留痕后方可执行
        out = self.svc.approve_change_order(co, Q + 1, acknowledge_breach=True)
        self.assertTrue(out["breach"])
        state = self.svc.state()
        self.assertNotIn("P-06", state.plans[pid].project_ids)
        self.assertTrue(state.change_orders[co].breach)

    def test_change_order_rejected_cannot_execute(self) -> None:
        pid = self._freezable()
        self.svc.freeze_plan(pid, Q, reason="审定")
        co = self.svc.propose_change_order(pid, "add", ("P-05",), Q + 1,
                                           reason="待议增补")
        self.svc.reject_change_order(co, Q + 1)
        with self.assertRaises(RejectedChangeOrderError):
            self.svc.approve_change_order(co, Q + 1)

    def test_change_order_infeasible_after_budget_cut_rejected(self) -> None:
        pid = self._freezable()
        self.svc.freeze_plan(pid, Q, reason="审定")
        # 预算被大幅缩减
        self.svc.update_budget("B1", 1_000_000, Q + 1, expected_version=0)
        co = self.svc.propose_change_order(pid, "add", ("P-05",), Q + 1,
                                           reason="想增补高山村")
        with self.assertRaisesRegex(ValidationError, "不可行"):
            self.svc.approve_change_order(co, Q + 1)
        # 被拒后方案内容不变，变更单仍为待审批
        self.assertNotIn("P-05", self.svc.state().plans[pid].project_ids)
        self.assertEqual(self.svc.state().change_orders[co].status, "待审批")


if __name__ == "__main__":
    unittest.main()
