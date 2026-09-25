"""方案管理：创建、修订、冻结、变更单、合并、回退、比较与承诺保护。"""

import unittest

from county_network_ledger.domain.errors import (
    CommitmentViolationError,
    FrozenPlanError,
    NotFoundError,
    StaleVersionError,
    ValidationError,
)

from support import FIXED_TIME, make_app


class PlanLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = make_app()

    def test_create_plan_assigns_deterministic_id(self) -> None:
        result = self.app.create_plan("二季度方案", ["P01", "P03", "P06"])
        self.assertEqual(result["plan"], "PLAN-0001")
        self.assertEqual(result["revision"], 1)
        self.assertEqual(result["selected"], ["P01", "P03", "P06"])

    def test_create_plan_must_keep_commitments(self) -> None:
        with self.assertRaises(CommitmentViolationError):
            self.app.create_plan("失信方案", ["P01", "P06"])  # 缺少已签承诺的 P03

    def test_update_plan_requires_current_revision(self) -> None:
        plan = self.app.create_plan("方案", ["P03", "P06"])
        with self.assertRaises(StaleVersionError):
            self.app.update_plan(plan["plan"], expected_revision=2, projects=["P03"])
        updated = self.app.update_plan(plan["plan"], expected_revision=1, projects=["P03", "P06", "P01"])
        self.assertEqual(updated["revision"], 2)
        with self.assertRaises(StaleVersionError):
            self.app.update_plan(plan["plan"], expected_revision=1, projects=["P03"])

    def test_update_plan_must_keep_commitments(self) -> None:
        plan = self.app.create_plan("方案", ["P03", "P06"])
        with self.assertRaises(CommitmentViolationError):
            self.app.update_plan(plan["plan"], expected_revision=1, projects=["P06"])

    def test_freeze_blocks_plans_with_violations(self) -> None:
        plan = self.app.create_plan("缺依赖", ["P03", "P04"])  # P04 依赖 P06
        with self.assertRaises(ValidationError) as ctx:
            self.app.freeze_plan(plan["plan"])
        self.assertIn("P06", str(ctx.exception))

    def test_frozen_plan_rejects_direct_edit_and_rollback(self) -> None:
        plan = self.app.create_plan("方案", ["P01", "P03", "P06"])
        self.app.freeze_plan(plan["plan"])
        with self.assertRaises(FrozenPlanError):
            self.app.update_plan(plan["plan"], expected_revision=1, projects=["P03", "P06"])
        with self.assertRaises(FrozenPlanError):
            self.app.rollback_plan(plan["plan"], to_revision=1)

    def test_rollback_restores_previous_selection(self) -> None:
        plan = self.app.create_plan("方案", ["P03", "P06"])
        self.app.update_plan(plan["plan"], expected_revision=1, projects=["P01", "P03", "P06"])
        result = self.app.rollback_plan(plan["plan"], to_revision=1)
        self.assertEqual(result["revision"], 3)
        self.assertEqual(result["selected"], ["P03", "P06"])
        detail = self.app.queries.plan(plan["plan"])
        self.assertEqual(detail["revisions"][2]["origin"], "rollback")

    def test_rollback_to_unknown_revision_fails(self) -> None:
        plan = self.app.create_plan("方案", ["P03", "P06"])
        with self.assertRaises(NotFoundError):
            self.app.rollback_plan(plan["plan"], to_revision=9)


class ChangeOrderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = make_app()
        plan = self.app.create_plan("正式方案", ["P01", "P03", "P06", "P04", "P07"])
        self.plan_id = plan["plan"]
        self.app.freeze_plan(self.plan_id)

    def test_change_order_is_the_only_way_to_modify_frozen_plan(self) -> None:
        result = self.app.apply_change_order(
            self.plan_id, base_revision=1, remove=["P07"],
            reason="三季度预算缩减，暂缓云雾村站点", author="县信息化专班",
        )
        self.assertEqual(result["change_order"], "CO-0001")
        self.assertEqual(result["revision"], 2)
        self.assertEqual(result["selected"], ["P01", "P03", "P04", "P06"])
        orders = self.app.queries.change_orders(self.plan_id)
        self.assertEqual(len(orders), 1)
        self.assertEqual(orders[0]["reason"], "三季度预算缩减，暂缓云雾村站点")
        self.assertEqual(orders[0]["author"], "县信息化专班")
        self.assertEqual(orders[0]["at"], FIXED_TIME)
        self.assertEqual(orders[0]["remove"], ["P07"])

    def test_change_order_requires_frozen_plan(self) -> None:
        draft = self.app.create_plan("草稿", ["P03", "P06"])
        with self.assertRaises(ValidationError):
            self.app.apply_change_order(draft["plan"], base_revision=1, add=["P01"],
                                        reason="测试", author="测试")

    def test_change_order_checks_base_revision(self) -> None:
        with self.assertRaises(StaleVersionError):
            self.app.apply_change_order(self.plan_id, base_revision=7, remove=["P07"],
                                        reason="测试", author="测试")

    def test_change_order_must_keep_commitments(self) -> None:
        with self.assertRaises(CommitmentViolationError):
            self.app.apply_change_order(self.plan_id, base_revision=1, remove=["P03"],
                                        reason="试图移除承诺工程", author="测试")

    def test_change_order_requires_reason_and_author(self) -> None:
        with self.assertRaises(ValidationError):
            self.app.apply_change_order(self.plan_id, base_revision=1, remove=["P07"], reason="", author="专班")
        with self.assertRaises(ValidationError):
            self.app.apply_change_order(self.plan_id, base_revision=1, remove=["P07"], reason="理由", author=" ")


class MergeAndCompareTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = make_app()

    def test_merge_unions_and_fits_budget_deterministically(self) -> None:
        plan_a = self.app.create_plan("甲案", ["P01", "P03", "P06"])
        plan_b = self.app.create_plan("乙案", ["P03", "P02", "P08", "P05"])
        result = self.app.merge_plans([plan_a["plan"], plan_b["plan"]], "合并案")
        # 二季度并集 265 万超 200 万：按单位投资覆盖人口依次舍弃 P06、P02
        self.assertEqual(result["selected"], ["P01", "P03", "P05", "P08"])
        self.assertEqual([drop["project"] for drop in result["drops"]], ["P06", "P02"])
        self.assertTrue(all("预算不足" in drop["reason"] for drop in result["drops"]))

    def test_merge_cascades_unmet_dependencies(self) -> None:
        plan_a = self.app.create_plan("甲案", ["P03", "P04"])  # P04 依赖 P06
        plan_b = self.app.create_plan("乙案", ["P03", "P08"])
        result = self.app.merge_plans([plan_a["plan"], plan_b["plan"]], "合并案")
        self.assertEqual(result["selected"], ["P03", "P08"])
        self.assertEqual(result["drops"][0]["project"], "P04")
        self.assertIn("P06", result["drops"][0]["reason"])

    def test_merge_never_drops_committed_projects(self) -> None:
        self.app.update_budget("B2026Q2", expected_version=1, total=80)  # 承诺工程 P03 一项即 90 万
        plan_a = self.app.create_plan("甲案", ["P03"])
        plan_b = self.app.create_plan("乙案", ["P03", "P01"])
        result = self.app.merge_plans([plan_a["plan"], plan_b["plan"]], "合并案")
        self.assertEqual(result["selected"], ["P03"])  # P01 被舍弃，承诺工程保留
        evaluation = self.app.queries.evaluate_plan(result["plan"])["evaluation"]
        self.assertTrue(evaluation["violations"]["budget"])  # 超支如实暴露

    def test_merge_requires_two_plans(self) -> None:
        plan = self.app.create_plan("甲案", ["P03"])
        with self.assertRaises(ValidationError):
            self.app.merge_plans([plan["plan"]], "合并案")

    def test_compare_reports_deltas(self) -> None:
        plan_a = self.app.create_plan("甲案", ["P01", "P03", "P06"])
        plan_b = self.app.create_plan("乙案", ["P03", "P06", "P08"])
        result = self.app.queries.compare(plan_a["plan"], plan_b["plan"])
        self.assertEqual(result["only_a"], ["P01"])
        self.assertEqual(result["only_b"], ["P08"])
        self.assertEqual(result["delta"]["population_covered"], -1700)
        self.assertEqual(result["delta"]["capex"], -20.0)


if __name__ == "__main__":
    unittest.main()
