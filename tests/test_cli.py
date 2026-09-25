"""命令行端到端验收：导入 JSON 台账 → 本地查询 → 方案/预算操作 → 导出确定性报告。"""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from county_network_ledger.interfaces.cli import main as cli_main

EXAMPLE_LEDGER = Path(__file__).resolve().parent.parent / "examples" / "ledger_2026.json"


def run_cli(*argv: str) -> tuple[int, str, str]:
    stdout, stderr = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        code = cli_main(list(argv))
    return code, stdout.getvalue(), stderr.getvalue()


class CliAcceptanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.data_dir = tempfile.mkdtemp(prefix="cnl-cli-")

    def cli(self, *argv: str) -> tuple[int, str, str]:
        return run_cli(*argv, "--data-dir", self.data_dir)

    def test_full_acceptance_flow(self) -> None:
        # 1. 导入台账
        code, out, _ = self.cli("import", str(EXAMPLE_LEDGER))
        self.assertEqual(code, 0)
        summary = json.loads(out)
        self.assertEqual(summary["ledger_id"], "hx-2026")
        self.assertEqual(summary["projects"], 8)

        # 2. 查询重复建设（空间 + 时间）
        code, out, _ = self.cli("query", "duplicates")
        self.assertEqual(code, 0)
        duplicates = json.loads(out)
        self.assertTrue(duplicates["spatial"])
        self.assertTrue(duplicates["temporal"])

        # 3. 建方案并冻结
        code, out, _ = self.cli("plan", "create", "--name", "二三季度方案",
                                "--projects", "P01,P03,P06,P04,P07")
        self.assertEqual(code, 0)
        plan_id = json.loads(out)["plan"]
        code, _, _ = self.cli("plan", "freeze", "--plan", plan_id)
        self.assertEqual(code, 0)

        # 4. 预算缩减：并发保护 + 超支暴露
        code, out, _ = self.cli("budget", "update", "--batch", "B2026Q3",
                                "--expected-version", "1", "--total", "100")
        self.assertEqual(code, 0)
        code, _, err = self.cli("budget", "update", "--batch", "B2026Q3",
                                "--expected-version", "1", "--total", "90")
        self.assertEqual(code, 2)
        self.assertIn("已拒绝静默覆盖", err)
        code, out, _ = self.cli("query", "evaluate", "--plan", plan_id)
        evaluation = json.loads(out)["evaluation"]
        self.assertTrue(evaluation["violations"]["budget"])

        # 5. 冻结方案只能通过留痕变更单修订
        code, _, err = self.cli("plan", "update", "--plan", plan_id,
                                "--expected-revision", "1", "--projects", "P01,P03,P06,P04")
        self.assertEqual(code, 2)
        self.assertIn("冻结", err)
        code, out, _ = self.cli("plan", "change-order", "--plan", plan_id, "--base-revision", "1",
                                "--remove", "P07", "--reason", "三季度预算缩减至 100 万元，暂缓云雾村站点",
                                "--author", "县信息化专班")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["change_order"], "CO-0001")

        # 6. 查询未覆盖清单（含空白网格）
        code, out, _ = self.cli("query", "uncovered", "--plan", plan_id)
        uncovered = {item["grid"]: item for item in json.loads(out)["uncovered"]}
        self.assertIn("G09", uncovered)
        self.assertTrue(uncovered["G10"]["blank"])

        # 7. 导出确定性报告：两次导出逐字节一致，且包含取舍理由与未覆盖清单
        report_a = Path(self.data_dir) / "report_a.json"
        report_b = Path(self.data_dir) / "report_b.json"
        code, _, _ = self.cli("report", "--plan", plan_id, "--out", str(report_a))
        self.assertEqual(code, 0)
        code, _, _ = self.cli("report", "--plan", plan_id, "--out", str(report_b))
        self.assertEqual(code, 0)
        self.assertEqual(report_a.read_bytes(), report_b.read_bytes())
        report = json.loads(report_a.read_text(encoding="utf-8"))
        excluded = {item["project"]: item["reasons"] for item in report["evaluation"]["excluded"]}
        self.assertTrue(any("预算不足" in reason for reason in excluded["P07"]))
        self.assertEqual(len(report["change_orders"]), 1)

    def test_import_then_query_events(self) -> None:
        code, _, _ = self.cli("import", str(EXAMPLE_LEDGER))
        self.assertEqual(code, 0)
        code, out, _ = self.cli("query", "events")
        self.assertEqual(code, 0)
        events = json.loads(out)
        self.assertEqual(events[0]["op"], "import")

    def test_query_before_import_reports_error(self) -> None:
        code, _, err = self.cli("query", "info")
        self.assertEqual(code, 2)
        self.assertIn("import", err)

    def test_reimport_without_replace_fails(self) -> None:
        self.cli("import", str(EXAMPLE_LEDGER))
        code, _, err = self.cli("import", str(EXAMPLE_LEDGER))
        self.assertEqual(code, 2)
        self.assertIn("replace", err)


if __name__ == "__main__":
    unittest.main()
