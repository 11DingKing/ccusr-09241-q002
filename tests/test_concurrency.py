"""多进程并发：两个进程同时以同一版本号缩减预算，恰有一个成功。"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest

from county_network_ledger.application.importer import import_ledger
from county_network_ledger.persistence.event_store import EventStore

from _helpers import SAMPLE_LEDGER

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class MultiProcessConcurrencyTests(unittest.TestCase):
    def test_concurrent_budget_edits_one_wins_no_silent_overwrite(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        ledger = os.path.join(tmp.name, "ledger.jsonl")
        import_ledger(EventStore(ledger), SAMPLE_LEDGER)

        env = dict(os.environ, PYTHONPATH=REPO_ROOT)
        procs = []
        for amount in ("1500000", "6500000"):
            cmd = [sys.executable, "-m", "county_network_ledger",
                   "--ledger", ledger, "budget-adjust",
                   "--budget", "B1", "--amount", amount,
                   "--quarter", "2", "--expected-version", "0"]
            procs.append((amount, subprocess.Popen(
                cmd, cwd=REPO_ROOT, env=env,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)))

        results = []
        for amount, p in procs:
            out, err = p.communicate(timeout=20)
            results.append((amount, p.returncode, out, err))

        ok = [r for r in results if r[1] == 0]
        rejected = [r for r in results if r[1] != 0]
        self.assertEqual(len(ok), 1, results)
        self.assertEqual(len(rejected), 1, results)
        self.assertIn("ConcurrentModificationError", rejected[0][3])
        # 最终额度等于胜出者的写入，且版本只前进一次
        state = EventStore(ledger).state()
        self.assertEqual(state.budgets["B1"].amount, float(ok[0][0]))
        self.assertEqual(state.budgets["B1"].version, 1)


if __name__ == "__main__":
    unittest.main()
