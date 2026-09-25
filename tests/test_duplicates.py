"""重复建设识别：空间重复与时间重复。"""

import unittest

from support import make_app


class DuplicateDetectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = make_app()
        self.result = self.app.queries.duplicates()

    def test_spatial_project_pairs_detected(self) -> None:
        pairs = [item for item in self.result["spatial"] if item["kind"] == "project_pair"]
        pair_keys = {tuple(item["projects"]) for item in pairs}
        self.assertIn(("P01", "P02"), pair_keys)  # 同争 G04
        self.assertIn(("P04", "P05"), pair_keys)  # 同争 G07
        self.assertIn(("P03", "P08"), pair_keys)  # 同争 G11
        g04 = next(item for item in pairs if item["projects"] == ["P01", "P02"])
        self.assertEqual(g04["grids"], ["G04"])
        self.assertEqual(g04["proposers"], ["运营商A", "园区管委会"])

    def test_spatial_project_vs_capability_detected(self) -> None:
        hits = [item for item in self.result["spatial"] if item["kind"] == "project_vs_capability"]
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["project"], "P01")
        self.assertEqual(hits[0]["capability"], "CAP02")
        self.assertEqual(hits[0]["grids"], ["G03"])

    def test_temporal_redeclaration_across_quarters(self) -> None:
        redeclared = [item for item in self.result["temporal"] if item["kind"] == "redeclared_grid"]
        by_grid = {item["grid"]: item for item in redeclared}
        self.assertIn("G04", by_grid)  # 一季度运营商A、二季度园区重复申报
        self.assertIn("G11", by_grid)
        declarations = by_grid["G04"]["declarations"]
        self.assertEqual([d["quarter"] for d in declarations], ["2026Q1", "2026Q2"])
        self.assertEqual(declarations[0]["projects"], ["P01"])
        self.assertEqual(declarations[1]["projects"], ["P02"])

    def test_temporal_redeclaration_after_capability(self) -> None:
        hits = [item for item in self.result["temporal"] if item["kind"] == "redeclared_after_capability"]
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["grid"], "G03")
        self.assertEqual(hits[0]["capability"], "CAP02")
        self.assertEqual(hits[0]["projects"][0]["project"], "P01")

    def test_same_quarter_overlap_is_not_temporal(self) -> None:
        redeclared = {item["grid"] for item in self.result["temporal"] if item["kind"] == "redeclared_grid"}
        self.assertNotIn("G07", redeclared)  # P04/P05 同为 2026Q2 申报，仅属空间重复


if __name__ == "__main__":
    unittest.main()
