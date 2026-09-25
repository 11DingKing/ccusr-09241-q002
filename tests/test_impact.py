"""影响评估：基本覆盖、重点产业、长期运维成本、预算与跨期依赖、取舍理由。"""

import tempfile
import unittest

from county_network_ledger.application.services import Application
from county_network_ledger.ports.clock import FixedClock

from support import make_app, sample_ledger

MAIN_PLAN = ["P01", "P03", "P06", "P04", "P07"]


class ImpactEvaluationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = make_app()
        result = self.app.create_plan("主方案", MAIN_PLAN)
        self.plan_id = result["plan"]
        self.evaluation = self.app.queries.evaluate_plan(self.plan_id)["evaluation"]

    def test_basic_coverage_metrics(self) -> None:
        coverage = self.evaluation["coverage"]
        self.assertEqual(coverage["grids_total"], 12)
        self.assertEqual(coverage["grids_covered"], 9)
        self.assertEqual(coverage["grids_covered_existing"], 3)
        self.assertEqual(coverage["population_total"], 27080)
        self.assertEqual(coverage["population_covered"], 25580)
        self.assertEqual(coverage["population_ratio"], 0.9446)

    def test_industry_coverage_metrics(self) -> None:
        industries = {item["name"]: item for item in self.evaluation["industries"]}
        self.assertEqual(industries["农业"]["population_total"], 2600)
        self.assertEqual(industries["农业"]["population_covered"], 2120)
        self.assertEqual(industries["农业"]["ratio"], 0.8154)
        self.assertEqual(industries["旅游"]["ratio"], 1.0)
        self.assertEqual(industries["政务"]["population_covered"], 8200)

    def test_long_term_cost_metrics(self) -> None:
        cost = self.evaluation["cost"]
        self.assertEqual(cost["capex"], 300.0)
        self.assertEqual(cost["om_annual_selected"], 8.1)
        self.assertEqual(cost["om_annual_existing"], 4.5)
        self.assertEqual(cost["om_horizon_years"], 10)
        self.assertEqual(cost["tco_selected"], 381.0)
        self.assertEqual(cost["tco_total"], 426.0)

    def test_budget_charging_by_quarter(self) -> None:
        budgets = {item["batch"]: item for item in self.evaluation["budgets"]}
        self.assertEqual(budgets["B2026Q2"]["charged"], 180.0)
        self.assertEqual(budgets["B2026Q2"]["remaining"], 20.0)
        self.assertFalse(budgets["B2026Q2"]["over"])
        self.assertEqual(budgets["B2026Q3"]["charged"], 120.0)
        self.assertEqual(budgets["B2026Q3"]["remaining"], 0.0)
        self.assertFalse(self.evaluation["has_violations"])

    def test_uncovered_list_marks_blank_grids(self) -> None:
        uncovered = self.evaluation["uncovered"]
        self.assertEqual([item["grid"] for item in uncovered], ["G12", "G08", "G10"])
        by_grid = {item["grid"]: item for item in uncovered}
        self.assertTrue(by_grid["G10"]["blank"])   # 无任何候选工程，真正空白
        self.assertFalse(by_grid["G08"]["blank"])  # P05 申报过但未入选
        self.assertFalse(by_grid["G12"]["blank"])

    def test_excluded_projects_carry_reasons(self) -> None:
        excluded = {item["project"]: item["reasons"] for item in self.evaluation["excluded"]}
        self.assertEqual(set(excluded), {"P02", "P05", "P08"})
        self.assertTrue(any("重复覆盖" in reason and "G04" in reason for reason in excluded["P02"]))
        self.assertTrue(any("预算不足" in reason for reason in excluded["P02"]))
        self.assertTrue(any("重复覆盖" in reason and "G07" in reason for reason in excluded["P05"]))
        self.assertTrue(any("预算不足" in reason for reason in excluded["P08"]))


class CrossPeriodDependencyTests(unittest.TestCase):
    def test_missing_dependency_is_flagged(self) -> None:
        app = make_app()
        plan = app.create_plan("缺依赖方案", ["P03", "P04"])  # P04 依赖 P06，未纳入
        evaluation = app.queries.evaluate_plan(plan["plan"])["evaluation"]
        violations = evaluation["violations"]["dependencies"]
        self.assertEqual(len(violations), 1)
        self.assertEqual(violations[0]["kind"], "missing")
        self.assertEqual(violations[0]["dependency"], "P06")
        self.assertTrue(evaluation["has_violations"])

    def test_later_quarter_dependency_is_flagged(self) -> None:
        raw = sample_ledger()
        for project in raw["projects"]:
            if project["id"] == "P04":
                project["planned_quarter"] = "2026Q2"
            if project["id"] == "P06":
                project["planned_quarter"] = "2026Q3"
        app = Application(tempfile.mkdtemp(prefix="cnl-order-"), clock=FixedClock())
        app.import_ledger(raw)
        plan = app.create_plan("时序冲突方案", ["P03", "P04", "P06"])
        evaluation = app.queries.evaluate_plan(plan["plan"])["evaluation"]
        violations = evaluation["violations"]["dependencies"]
        self.assertEqual(len(violations), 1)
        self.assertEqual(violations[0]["kind"], "order")
        self.assertIn("2026Q3", violations[0]["detail"])

    def test_missing_budget_batch_is_flagged(self) -> None:
        raw = sample_ledger()
        raw["projects"].append(
            {"id": "P20", "proposer": "运营商A", "kind": "site", "covers": ["G10"],
             "capex": 10, "om_annual": 0.5, "declared_quarter": "2026Q2", "planned_quarter": "2026Q4"}
        )
        app = Application(tempfile.mkdtemp(prefix="cnl-batch-"), clock=FixedClock())
        app.import_ledger(raw)
        plan = app.create_plan("跨期方案", ["P03", "P20"])
        evaluation = app.queries.evaluate_plan(plan["plan"])["evaluation"]
        missing = evaluation["violations"]["missing_batches"]
        self.assertEqual(missing, [{"project": "P20", "quarter": "2026Q4"}])
        self.assertTrue(evaluation["has_violations"])

    def test_over_budget_is_flagged(self) -> None:
        app = make_app()
        plan = app.create_plan("超支方案", ["P01", "P03", "P06", "P08"])  # 二季度 220 > 200
        evaluation = app.queries.evaluate_plan(plan["plan"])["evaluation"]
        budget_violations = evaluation["violations"]["budget"]
        self.assertEqual(len(budget_violations), 1)
        self.assertEqual(budget_violations[0]["batch"], "B2026Q2")
        self.assertEqual(budget_violations[0]["charged"], 220.0)


if __name__ == "__main__":
    unittest.main()
