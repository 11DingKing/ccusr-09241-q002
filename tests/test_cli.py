"""命令行端到端验收：导入 JSON 台账 → 查询 → 方案操作 → 确定性报告导出。"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAMPLE = os.path.join(REPO_ROOT, "examples", "ledger.json")


class CliAcceptanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ledger = os.path.join(self.tmp.name, "ledger.jsonl")

    def _run(self, *args: str, check: bool = True) -> tuple[int, str, str]:
        cmd = [sys.executable, "-m", "county_network_ledger",
               "--ledger", self.ledger, *args]
        proc = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True)
        if check and proc.returncode != 0:
            self.fail(f"命令失败：{args}\nstdout={proc.stdout}\nstderr={proc.stderr}")
        return proc.returncode, proc.stdout, proc.stderr

    def _json(self, *args: str):
        _, out, _ = self._run(*args)
        return json.loads(out)

    def test_full_acceptance_flow(self) -> None:
        # 1) 导入
        imp = self._json("import", SAMPLE)
        self.assertGreater(imp["events"], 10)
        # 重复导入幂等
        self.assertEqual(sum(self._json("import", SAMPLE)["imported"].values()), 0)

        # 2) 哈希链校验
        self.assertTrue(self._json("verify")["ok"])

        # 3) 查重：电信/移动对河西村的重复申报
        dups = self._json("duplicates")
        hard = [d for d in dups if d["severity"] == "空间+时间双重重复"]
        self.assertEqual(len(hard), 1)
        pair = {hard[0]["proposer_a"], hard[0]["proposer_b"]}
        self.assertEqual(pair, {"电信", "移动"})

        # 4) 创建两个工作副本方案并比较
        lean = self._json("plan-create", "--name", "节约方案", "--quarter", "2",
                          "--projects", "P-01,P-04,P-06",
                          "--note", "优先空白村与已签承诺，偏远村延后")["plan_id"]
        full = self._json(
            "plan-create", "--name", "全面方案", "--quarter", "2",
            "--projects", "P-01,P-03,P-04,P-05,P-06,P-07",
            "--note", "兼顾产业园5G与高山村，接受更高运维成本")["plan_id"]
        rows = {r["plan_id"]: r for r in self._json("compare", f"{lean},{full}")}
        self.assertGreater(rows[full]["basic_population"], rows[lean]["basic_population"])

        # 5) 合并 + 回退
        self._run("plan-merge", lean, "--source", full, "--quarter", "2",
                  "--note", "试并全面方案评估资金压力")
        self._run("plan-rollback", lean, "--index", "0", "--quarter", "2",
                  "--note", "资金不足，回退到节约基线")
        eval_lean = self._json("evaluate", lean)
        self.assertEqual(set(eval_lean["project_ids"]), {"P-01", "P-04", "P-06"})

        # 6) 多人并发调预算：旧版本号必须失败
        self._json("budget-adjust", "--budget", "B-2026-TB",
                   "--amount", "2000000", "--quarter", "2", "--expected-version", "0")
        code, _, err = self._run(
            "budget-adjust", "--budget", "B-2026-TB",
            "--amount", "9000000", "--quarter", "2", "--expected-version", "0",
            check=False)
        self.assertEqual(code, 2)
        self.assertIn("ConcurrentModificationError", err)

        # 7) 预算缩减后全面方案冻结被拒（超支）；恢复预算后可冻结
        self._json("budget-adjust", "--budget", "B-2026-TB",
                   "--amount", "8000000", "--quarter", "2", "--expected-version", "1")
        self._run("plan-freeze", full, "--quarter", "3",
                  "--reason", "专班季度审定：普惠与产业并重")

        # 8) 冻结后直接改被拒，必须走变更单
        code, _, err = self._run(
            "plan-set", full, "--projects", "P-01", "--quarter", "3",
            "--note", "尝试直接改冻结方案", check=False)
        self.assertEqual(code, 2)
        self.assertIn("PlanFrozenError", err)

        co = self._json("change-propose", full, "--kind", "add",
                        "--projects", "P-08", "--quarter", "4",
                        "--reason", "增补柳坪村4G，回应人大建议",
                        "--proposer", "专班")["co_id"]
        self._json("change-approve", co, "--quarter", "4")
        self.assertIn("P-08", self._json("evaluate", full)["project_ids"])

        # 9) 导出 JSON 与 Markdown 报告，且 JSON 报告确定性（重复导出一致）
        json1 = os.path.join(self.tmp.name, "report1.json")
        json2 = os.path.join(self.tmp.name, "report2.json")
        md = os.path.join(self.tmp.name, "report.md")
        self._run("report", "--out", json1, "--quarter", "4")
        self._run("report", "--out", json2, "--quarter", "4")
        self._run("report", "--out", md, "--quarter", "4", "--plans", full)
        with open(json1, "rb") as a, open(json2, "rb") as b:
            self.assertEqual(a.read(), b.read())
        text = open(md, encoding="utf-8").read()
        self.assertIn("取舍理由", text)
        self.assertIn("未覆盖清单", text)
        self.assertIn("变更单台账", text)
        report = json.loads(open(json1, encoding="utf-8").read())
        # 报告锚定账本版本，且包含未覆盖网格与变更单留痕
        self.assertEqual(len(report["meta"]["ledger_tip_hash"]), 64)
        self.assertTrue(report["change_orders"])


if __name__ == "__main__":
    unittest.main()
