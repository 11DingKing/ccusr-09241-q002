"""台账导入校验：引用完整性、季度格式、依赖环与并发版本基线。"""

import tempfile
import unittest

from county_network_ledger.application.services import Application
from county_network_ledger.domain.errors import StoreError, ValidationError
from county_network_ledger.ports.clock import FixedClock

from support import sample_ledger


class ImportValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp(prefix="cnl-import-")
        self.app = Application(self.tmpdir, clock=FixedClock())

    def test_valid_ledger_imports_and_counts(self) -> None:
        summary = self.app.import_ledger(sample_ledger())
        self.assertEqual(summary["ledger_id"], "hx-2026")
        self.assertEqual(summary["grids"], 12)
        self.assertEqual(summary["projects"], 8)
        self.assertEqual(summary["budgets"], 2)
        self.assertEqual(summary["commitments"], 1)
        self.assertEqual(self.app.queries.info()["version"], 1)

    def test_unknown_grid_reference_is_rejected(self) -> None:
        raw = sample_ledger()
        raw["projects"][0]["covers"] = ["G03", "G99"]
        with self.assertRaises(ValidationError) as ctx:
            self.app.import_ledger(raw)
        self.assertIn("G99", str(ctx.exception))

    def test_unknown_dependency_is_rejected(self) -> None:
        raw = sample_ledger()
        raw["projects"][3]["depends_on"] = ["P99"]
        with self.assertRaises(ValidationError) as ctx:
            self.app.import_ledger(raw)
        self.assertIn("P99", str(ctx.exception))

    def test_dependency_cycle_is_rejected(self) -> None:
        raw = sample_ledger()
        raw["projects"][0]["depends_on"] = ["P04"]
        raw["projects"][3]["depends_on"] = ["P01"]
        with self.assertRaises(ValidationError) as ctx:
            self.app.import_ledger(raw)
        self.assertIn("依赖存在环", str(ctx.exception))

    def test_bad_quarter_format_is_rejected(self) -> None:
        raw = sample_ledger()
        raw["budgets"][0]["quarter"] = "2026-Q2"
        with self.assertRaises(ValidationError) as ctx:
            self.app.import_ledger(raw)
        self.assertIn("quarter", str(ctx.exception))

    def test_duplicate_ids_are_rejected(self) -> None:
        raw = sample_ledger()
        raw["grids"].append(dict(raw["grids"][0]))
        with self.assertRaises(ValidationError) as ctx:
            self.app.import_ledger(raw)
        self.assertIn("重复", str(ctx.exception))

    def test_commitment_to_unknown_project_is_rejected(self) -> None:
        raw = sample_ledger()
        raw["commitments"][0]["project"] = "P99"
        with self.assertRaises(ValidationError):
            self.app.import_ledger(raw)

    def test_reimport_requires_replace_flag(self) -> None:
        self.app.import_ledger(sample_ledger())
        with self.assertRaises(StoreError):
            self.app.import_ledger(sample_ledger())
        summary = self.app.import_ledger(sample_ledger(), replace=True)
        self.assertEqual(summary["version"], 1)

    def test_query_before_import_fails(self) -> None:
        with self.assertRaises(StoreError):
            self.app.queries.info()


if __name__ == "__main__":
    unittest.main()
