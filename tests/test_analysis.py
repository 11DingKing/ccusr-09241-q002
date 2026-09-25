"""重复建设识别、覆盖/产业/运维评估、跨期依赖、空白网格与预算缩减。"""
from __future__ import annotations

import unittest

from county_network_ledger.domain.analysis import (
    check_dependencies,
    evaluate,
    find_duplicates,
)

from _helpers import seeded_store


class DuplicateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp, self.store = seeded_store()
        self.addCleanup(self.tmp.cleanup)
        self.state = self.store.state()

    def test_spatial_temporal_duplicate_across_proposers(self) -> None:
        # P-01 电信与 P-02 移动在同一网格 G-03、窗口重叠、同等级
        dups = find_duplicates(self.state, ("P-01", "P-02"))
        self.assertEqual(len(dups), 1)
        d = dups[0]
        self.assertEqual((d.project_a, d.project_b, d.grid_id),
                         ("P-01", "P-02", "G-03"))
        self.assertTrue(d.spatial and d.temporal and d.same_kind)
        self.assertEqual(d.severity, "空间+时间双重重复")

    def test_spatial_only_when_windows_disjoint(self) -> None:
        # P-05 施工 q2-3，与 P-01(q0-1) 不同网格无交集；构造跨期但同网格场景
        dups = find_duplicates(self.state, ("P-01", "P-05"))
        self.assertEqual(dups, [])

    def test_existing_capability_duplicate_flagged(self) -> None:
        # P-11 申报在已有 4G 存量的 G-01 再建 4G：与存量重复
        dups = find_duplicates(self.state, ("P-11",))
        self.assertEqual(len(dups), 1)
        self.assertEqual(dups[0].project_b, "存量:G-01")
        self.assertEqual(dups[0].severity, "空间重复")
        # 全量扫描：唯二的双重重复仍是 P-01/P-02
        all_dups = find_duplicates(self.state, tuple(self.state.projects))
        hard = [d for d in all_dups if d.severity == "空间+时间双重重复"]
        self.assertEqual(len(hard), 1)

    def test_no_duplicate_between_site_and_fiber(self) -> None:
        # 站点与光纤互为配套，不判重复
        self.assertEqual(find_duplicates(self.state, ("P-01", "P-06")), [])


class DependencyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp, self.store = seeded_store()
        self.addCleanup(self.tmp.cleanup)
        self.state = self.store.state()

    def test_missing_dependency_detected(self) -> None:
        issues = check_dependencies(self.state, ("P-07",))  # 依赖 P-06 但未选入
        self.assertEqual(len(issues), 1)
        self.assertEqual((issues[0].project_id, issues[0].missing_dep),
                         ("P-07", "P-06"))
        self.assertIn("未选入", issues[0].reason)

    def test_cross_period_timing_violation(self) -> None:
        # P-10 q0 开工，前置 P-06 q1 才完工
        issues = check_dependencies(self.state, ("P-06", "P-10"))
        self.assertEqual(len(issues), 1)
        self.assertIn("晚于", issues[0].reason)

    def test_valid_dependency_schedule(self) -> None:
        # P-06 q1 完工，P-07 q4 开工：合规
        issues = check_dependencies(self.state, ("P-06", "P-07"))
        self.assertEqual(issues, [])


class EvaluationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp, self.store = seeded_store()
        self.addCleanup(self.tmp.cleanup)
        self.state = self.store.state()

    def test_blank_grids_uncovered_by_default(self) -> None:
        # 空方案：所有空白网格都应出现在未覆盖清单
        evl = evaluate(self.state, ())
        self.assertEqual({g.id for g in evl.blank_uncovered}, {"G-03", "G-04"})
        # 已有 2G/3G 的 G-05 不满足 4G 基本覆盖，也算未覆盖但不是空白网格
        self.assertIn("G-05", {g.id for g in evl.uncovered})
        self.assertNotIn("G-05", {g.id for g in evl.blank_uncovered})
        # G-01 已有 4G，视为已覆盖
        self.assertNotIn("G-01", {g.id for g in evl.uncovered})

    def test_coverage_industry_and_maintenance(self) -> None:
        evl = evaluate(self.state, ("P-01", "P-03", "P-04", "P-05", "P-06", "P-07"))
        covered = {g.id for g in evl.uncovered}
        # 五个有人网格全部达到基本覆盖（P-07 为 G-04 提供光纤级覆盖）
        self.assertEqual(covered, set())
        self.assertIn("G-04", evl.fiber_grids)
        # 重点产业网格 G-02 人口计入产业覆盖
        self.assertEqual(evl.industry_population, 3200)
        # 运维成本为各工程年运维之和，10 年窗口 = 年值*10
        self.assertAlmostEqual(
            evl.maintenance_horizon_cost,
            evl.annual_maintenance * 10, places=2)
        self.assertTrue(evl.feasible)

    def test_shared_funding_reduces_county_budget_occupation(self) -> None:
        # P-06 成本 150 万，广电承诺分担 40%：县级占用 90 万
        evl = evaluate(self.state, ("P-06",))
        self.assertAlmostEqual(evl.shared_funding, 600_000, places=2)
        self.assertAlmostEqual(evl.county_cost, 900_000, places=2)
        b1 = evl.budgets["B1"]
        self.assertAlmostEqual(b1.county_cost, 900_000, places=2)
        self.assertAlmostEqual(b1.remaining, 5_000_000 - 900_000, places=2)

    def test_budget_overrun_makes_infeasible(self) -> None:
        # B2 产业专项 200 万：P-03 成本 120 万，分担 30% 后 84 万，不超
        evl_ok = evaluate(self.state, ("P-03",))
        self.assertTrue(evl_ok.feasible)
        # 直接选入超额组合：B1 下所有工程毛成本远超 500 万
        evl = evaluate(self.state, ("P-01", "P-02", "P-04", "P-05", "P-06", "P-10"))
        self.assertFalse(evl.feasible)
        self.assertTrue(any("超支" in v for v in evl.violations()))

    def test_budget_reduction_scenario(self) -> None:
        """预算缩减场景：批次额度从 500 万缩到 200 万后，原可行方案变超支。"""
        from county_network_ledger.domain import events as ev
        from county_network_ledger.domain.state import apply, LedgerState

        state = self.state
        # 模拟预算缩减事件（应用层路径在 test_planning 中验证乐观锁）
        apply(state, {"seq": state.seq + 1, "name": ev.BUDGET_UPDATED, "payload": {
            "id": "B1", "name": "普服补助", "amount": 2_000_000, "quarter": 2}})
        evl = evaluate(state, ("P-01", "P-04", "P-06"))
        # 毛成本 310 万，P-06 分担 60 万，县级 250 万 > 200 万
        self.assertFalse(evl.feasible)
        usage = evl.budgets["B1"]
        self.assertAlmostEqual(usage.overrun, 500_000, places=2)
        # 缩减后只保留 P-01+P-06：县级 90+90=180 万，可行；
        # 但产业园 G-02（P-03 出局）与高山村 G-04（P-05 出局）退回未覆盖
        lean = evaluate(state, ("P-01", "P-06"))
        self.assertTrue(lean.feasible)
        lean_uncovered = {g.id for g in lean.uncovered}
        self.assertIn("G-02", lean_uncovered)
        self.assertIn("G-04", lean_uncovered)
        # G-05 虽无站点，但 P-06 光纤主干沿程覆盖，仍达标
        self.assertNotIn("G-05", lean_uncovered)

    def test_low_revenue_remote_village_quantified(self) -> None:
        # 高山村 P-05：年收益仅 1.2 万，仍应能在未覆盖清单中被识别为可补建
        evl = evaluate(self.state, ("P-01", "P-04", "P-06"))
        remote = {g.id: g for g in evl.uncovered}
        self.assertIn("G-04", remote)
        self.assertEqual(remote["G-04"].population, 620)


if __name__ == "__main__":
    unittest.main()
