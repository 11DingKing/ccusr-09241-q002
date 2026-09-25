"""台账导入与校验、幂等、哈希链完整性。"""
from __future__ import annotations

import copy
import json
import unittest

from county_network_ledger.application.importer import import_ledger, validate_ledger
from county_network_ledger.domain.errors import (
    LedgerIntegrityError,
    ValidationError,
)
from county_network_ledger.persistence.event_store import EventStore

from _helpers import SAMPLE_LEDGER, make_store, seeded_store


class ImportValidationTests(unittest.TestCase):
    def test_valid_ledger_imports_all_sections(self) -> None:
        tmp, store = make_store()
        self.addCleanup(tmp.cleanup)
        counts = import_ledger(store, SAMPLE_LEDGER)
        self.assertEqual(counts, {"grids": 5, "capabilities": 3, "projects": 9,
                                  "budgets": 3, "commitments": 2})

    def test_import_is_idempotent(self) -> None:
        tmp, store = seeded_store()
        self.addCleanup(tmp.cleanup)
        again = import_ledger(store, SAMPLE_LEDGER)
        self.assertEqual(sum(again.values()), 0)
        state = store.state()
        self.assertEqual(len(state.grids), 5)

    def test_bad_reference_project_to_grid_rejected(self) -> None:
        data = copy.deepcopy(SAMPLE_LEDGER)
        data["projects"][0]["covers"] = ["G-NOPE"]
        with self.assertRaises(ValidationError):
            validate_ledger(data)

    def test_dependency_must_exist(self) -> None:
        data = copy.deepcopy(SAMPLE_LEDGER)
        data["projects"][0]["deps"] = ["P-99"]
        with self.assertRaises(ValidationError):
            validate_ledger(data)

    def test_commitment_share_over_one_rejected(self) -> None:
        data = copy.deepcopy(SAMPLE_LEDGER)
        data["commitments"][0]["share"] = 0.9
        data["commitments"][1].update(project_id="P-06", share=0.5)
        with self.assertRaisesRegex(ValidationError, "分担比例"):
            validate_ledger(data)

    def test_bad_bbox_rejected(self) -> None:
        data = copy.deepcopy(SAMPLE_LEDGER)
        data["grids"][0]["x1"] = -1
        with self.assertRaises(ValidationError):
            validate_ledger(data)

    def test_invalid_json_file_atomic_no_partial_events(self) -> None:
        tmp, store = make_store()
        self.addCleanup(tmp.cleanup)
        data = copy.deepcopy(SAMPLE_LEDGER)
        data["budgets"] = [{"id": "BX", "name": "x", "amount": -1, "quarter": 0}]
        with self.assertRaises(ValidationError):
            import_ledger(store, data)
        # 全有或全无：没有任何事件落盘
        self.assertEqual(store.version, 0)

    def test_empty_sections_allowed(self) -> None:
        validate_ledger({})
        tmp, store = make_store()
        self.addCleanup(tmp.cleanup)
        self.assertEqual(sum(import_ledger(store, {}).values()), 0)


class HashChainTests(unittest.TestCase):
    def test_tamper_detected(self) -> None:
        tmp, store = seeded_store()
        self.addCleanup(tmp.cleanup)
        path = store.path
        with open(path, "r", encoding="utf-8") as fh:
            lines = fh.readlines()
        rec = json.loads(lines[2])
        rec["payload"]["amount"] = 999_999_999
        lines[2] = json.dumps(rec, ensure_ascii=False) + "\n"
        with open(path, "w", encoding="utf-8") as fh:
            fh.writelines(lines)
        with self.assertRaises(LedgerIntegrityError):
            store.state()

    def test_gap_in_seq_detected(self) -> None:
        tmp, store = seeded_store()
        self.addCleanup(tmp.cleanup)
        path = store.path
        with open(path, "r", encoding="utf-8") as fh:
            lines = fh.readlines()
        del lines[3]
        with open(path, "w", encoding="utf-8") as fh:
            fh.writelines(lines)
        with self.assertRaises(LedgerIntegrityError):
            EventStore(path).state()


if __name__ == "__main__":
    unittest.main()
