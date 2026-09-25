"""本地 HTTP 查询接口测试。"""
from __future__ import annotations

import json
import threading
import unittest
import urllib.request
from urllib.error import HTTPError

from county_network_ledger.application.planning import PlanningService
from county_network_ledger.interfaces.http_api import serve

from _helpers import seeded_store

Q = 2


class HttpApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp, self.store = seeded_store()
        self.addCleanup(self.tmp.cleanup)
        svc = PlanningService(self.store)
        svc.create_plan("测试方案", Q, ("P-01", "P-06"), note="接口测试基线")
        self.httpd = serve(self.store, "127.0.0.1", 0)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=2)

    def _get(self, path: str):
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}{path}", timeout=5) as r:
            return json.loads(r.read().decode("utf-8"))

    def test_health_summary_and_routes(self) -> None:
        self.assertEqual(self._get("/health")["status"], "ok")
        summary = self._get("/summary")
        self.assertEqual(summary["grids"], 5)
        self.assertEqual(summary["projects"], 9)
        grids = self._get("/grids")
        self.assertEqual({g["id"] for g in grids},
                         {"G-01", "G-02", "G-03", "G-04", "G-05"})

    def test_plan_evaluation_and_compare(self) -> None:
        plans = self._get("/plans")
        pid = plans[0]["plan_id"]
        evl = self._get(f"/plans/{pid}/evaluation")
        self.assertIn("G-03", evl["covered_grids"])
        self.assertIn("G-04", evl["uncovered_grids"])
        rows = self._get(f"/compare?ids={pid}")
        self.assertEqual(rows[0]["plan_id"], pid)

    def test_duplicates_route(self) -> None:
        dups = self._get("/duplicates")
        self.assertTrue(any(d["severity"] == "空间+时间双重重复" for d in dups))

    def test_report_route(self) -> None:
        report = self._get("/report?quarter=3")
        self.assertEqual(report["meta"]["quarter"], 3)
        self.assertTrue(report["plans"])

    def test_unknown_plan_returns_404(self) -> None:
        with self.assertRaises(HTTPError) as ctx:
            self._get("/plans/FA-9999/evaluation")
        self.assertEqual(ctx.exception.code, 404)


if __name__ == "__main__":
    unittest.main()
