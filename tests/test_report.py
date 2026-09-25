"""确定性报告：同状态逐字节一致，且包含取舍理由与未覆盖清单。"""
from __future__ import annotations

import json
import unittest

from county_network_ledger.application.planning import PlanningService
from county_network_ledger.application.report import (
    build_report,
    render_json,
    render_markdown,
)

from _helpers import seeded_store

Q = 2


class ReportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp, self.store = seeded_store()
        self.addCleanup(self.tmp.cleanup)
        svc = PlanningService(self.store)
        svc.create_plan("全面方案", Q,
                        ("P-01", "P-03", "P-04", "P-05", "P-06", "P-07"),
                        note="普惠与产业并重，承担较高10年运维")
        svc.create_plan("节约方案", Q, ("P-01", "P-04", "P-06"),
                        note="先保空白村与已签承诺，高山村列入下一批")

    def test_report_is_byte_deterministic(self) -> None:
        r1 = render_json(build_report(self.store, Q))
        # 全新进程式重读：再建一个同路径 store
        from county_network_ledger.persistence.event_store import EventStore
        store2 = EventStore(self.store.path)
        r2 = render_json(build_report(store2, Q))
        self.assertEqual(r1, r2)
        parsed = json.loads(r1)
        self.assertEqual(parsed["meta"]["ledger_events"], self.store.version)
        self.assertEqual(len(parsed["meta"]["ledger_tip_hash"]), 64)

    def test_report_carves_out_required_sections(self) -> None:
        report = json.loads(render_json(build_report(self.store, Q)))
        by_id = {p["plan_id"]: p for p in report["plans"]}
        lean = [p for p in report["plans"] if p["name"] == "节约方案"][0]
        # 取舍理由
        notes = [r["note"] for r in lean["rationale"]["revisions"]]
        self.assertTrue(any("先保空白村" in n for n in notes))
        # 未覆盖清单：节约方案不含 P-05/P-07，高山村 G-04 未覆盖且为空白网格
        unc = lean["uncovered"]
        ids = {g["grid_id"] for g in unc["grids"]}
        self.assertIn("G-04", ids)
        gaoshan = next(g for g in unc["grids"] if g["grid_id"] == "G-04")
        self.assertTrue(gaoshan["blank"])
        # 并附可补建候选工程及成本
        self.assertTrue(any(o["project_id"] in ("P-05", "P-07")
                            for o in gaoshan["candidate_projects"]))
        # 比较表两行
        self.assertEqual(len(report["comparison"]), 2)

    def test_markdown_report_contains_reason_and_uncovered(self) -> None:
        md = render_markdown(build_report(self.store, Q))
        self.assertIn("取舍理由", md)
        self.assertIn("未覆盖清单", md)
        self.assertIn("高山村", md)
        self.assertIn("方案比较", md)

    def test_report_filters_plans_and_is_still_deterministic(self) -> None:
        full = [p for p in self.store.state().plans.values()
                if p.name == "全面方案"][0].id
        r1 = render_json(build_report(self.store, Q, [full]))
        from county_network_ledger.persistence.event_store import EventStore
        r2 = render_json(build_report(EventStore(self.store.path), Q, [full]))
        self.assertEqual(r1, r2)
        self.assertEqual(len(json.loads(r1)["plans"]), 1)


if __name__ == "__main__":
    unittest.main()
