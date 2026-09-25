"""预算批次：乐观并发防静默覆盖，以及预算缩减后的处置闭环。"""

import unittest

from county_network_ledger.domain.errors import NotFoundError, StaleVersionError, ValidationError

from support import make_app


class BudgetConcurrencyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = make_app()

    def test_concurrent_updates_do_not_silently_overwrite(self) -> None:
        # 两人同时读到版本 1：先提交者成功，后提交者必须失败而不是静默覆盖
        first = self.app.update_budget("B2026Q3", expected_version=1, total=150)
        self.assertEqual(first["version"], 2)
        with self.assertRaises(StaleVersionError) as ctx:
            self.app.update_budget("B2026Q3", expected_version=1, total=90)
        self.assertIn("已拒绝静默覆盖", str(ctx.exception))
        # 基于最新版本重新提交则成功
        retry = self.app.update_budget("B2026Q3", expected_version=2, total=90)
        self.assertEqual(retry["total"], 90.0)
        self.assertEqual(retry["version"], 3)

    def test_unknown_batch_is_rejected(self) -> None:
        with self.assertRaises(NotFoundError):
            self.app.update_budget("B9999", expected_version=1, total=10)

    def test_negative_total_is_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            self.app.update_budget("B2026Q3", expected_version=1, total=-5)

    def test_noop_update_is_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            self.app.update_budget("B2026Q3", expected_version=1)


class BudgetReductionScenarioTests(unittest.TestCase):
    """预算缩减场景：冻结方案 → 缩减预算 → 暴露超支 → 变更单取舍 → 恢复可行。"""

    def test_reduction_flow(self) -> None:
        app = make_app()
        plan = app.create_plan("正式方案", ["P01", "P03", "P06", "P04", "P07"])
        app.freeze_plan(plan["plan"])

        app.update_budget("B2026Q3", expected_version=1, total=100)  # 三季度 120 → 100
        evaluation = app.queries.evaluate_plan(plan["plan"])["evaluation"]
        violations = evaluation["violations"]["budget"]
        self.assertEqual(len(violations), 1)
        self.assertEqual(violations[0]["batch"], "B2026Q3")
        self.assertEqual(violations[0]["charged"], 120.0)

        # 冻结方案只能通过变更单取舍：暂缓收益最低的云雾村站点
        result = app.apply_change_order(
            plan["plan"], base_revision=1, remove=["P07"],
            reason="三季度预算缩减至 100 万元，暂缓云雾村站点", author="县信息化专班",
        )
        self.assertEqual(result["selected"], ["P01", "P03", "P04", "P06"])
        evaluation = app.queries.evaluate_plan(plan["plan"])["evaluation"]
        self.assertFalse(evaluation["has_violations"])

        # 取舍理由与未覆盖清单可供报告使用
        excluded = {item["project"]: item["reasons"] for item in evaluation["excluded"]}
        self.assertTrue(any("预算不足" in reason for reason in excluded["P07"]))
        uncovered = {item["grid"] for item in evaluation["uncovered"]}
        self.assertIn("G09", uncovered)  # 云雾村因预算缩减回到未覆盖清单


if __name__ == "__main__":
    unittest.main()
