"""确定性报告：内容完备（取舍理由、未覆盖清单）且同一状态渲染逐字节一致。"""

import unittest

from county_network_ledger.application.reports import render_report
from county_network_ledger.application.services import Application
from county_network_ledger.ports.clock import FixedClock

from support import FIXED_TIME, make_app


class DeterministicReportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_app = make_app()
        plan = self.tmp_app.create_plan("正式方案", ["P01", "P03", "P06", "P04", "P07"])
        self.plan_id = plan["plan"]
        self.tmp_app.freeze_plan(self.plan_id)
        self.tmp_app.update_budget("B2026Q3", expected_version=1, total=100)
        self.tmp_app.apply_change_order(
            self.plan_id, base_revision=1, remove=["P07"],
            reason="三季度预算缩减至 100 万元，暂缓云雾村站点", author="县信息化专班",
        )
        self.data_dir = self.tmp_app.store.data_dir

    def test_report_contains_tradeoffs_and_uncovered_list(self) -> None:
        report = self.tmp_app.report(self.plan_id)
        self.assertEqual(report["report"], "county-network-plan-report")
        self.assertEqual(report["plan"]["revision"], 2)
        self.assertEqual(report["plan"]["status"], "frozen")
        excluded = {item["project"]: item["reasons"] for item in report["evaluation"]["excluded"]}
        self.assertIn("P07", excluded)
        self.assertTrue(any("预算不足" in reason for reason in excluded["P07"]))
        uncovered = {item["grid"]: item for item in report["evaluation"]["uncovered"]}
        self.assertIn("G09", uncovered)          # 预算缩减后暂缓的村落
        self.assertTrue(uncovered["G10"]["blank"])  # 空白网格
        self.assertEqual(len(report["change_orders"]), 1)
        self.assertEqual(report["change_orders"][0]["reason"], "三季度预算缩减至 100 万元，暂缓云雾村站点")
        self.assertEqual([rev["origin"] for rev in report["revision_log"]], ["create", "change_order"])
        self.assertTrue(report["duplicates"]["spatial"])

    def test_report_is_byte_identical_for_same_state(self) -> None:
        first = render_report(self.tmp_app.report(self.plan_id))
        second = render_report(self.tmp_app.report(self.plan_id))
        self.assertEqual(first, second)
        # 重新从磁盘加载后渲染仍然一致
        reloaded = Application(str(self.data_dir), clock=FixedClock(FIXED_TIME))
        third = render_report(reloaded.report(self.plan_id))
        self.assertEqual(first, third)

    def test_report_has_no_wall_clock_fields(self) -> None:
        report = self.tmp_app.report(self.plan_id)
        self.assertNotIn("generated_at", report)
        # 留痕时间来自账本状态（固定时钟），而非报告生成时刻
        self.assertEqual(report["change_orders"][0]["at"], FIXED_TIME)


if __name__ == "__main__":
    unittest.main()
