"""命令行接口：导入 JSON 台账、调用本地查询接口、管理方案与预算、导出确定性报告。"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from ..application.reports import render_report
from ..application.services import Application
from ..domain.errors import LedgerError, ValidationError

DEFAULT_DATA_DIR = os.environ.get("CNL_DATA_DIR", ".ledger_data")


def _print(obj) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True))


def _csv(text: str | None) -> list[str]:
    return [item.strip() for item in (text or "").split(",") if item.strip()]


def _require(value, message: str):
    if value is None or value == "":
        raise ValidationError(message)
    return value


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--data-dir",
        default=DEFAULT_DATA_DIR,
        help="账本数据目录（默认 .ledger_data，可用环境变量 CNL_DATA_DIR 覆盖）",
    )
    parser = argparse.ArgumentParser(
        prog="county_network_ledger",
        description="县域通信共建规划账本：版本化台账、重复识别、方案比选与确定性报告",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_import = sub.add_parser("import", parents=[common], help="导入 JSON 台账")
    p_import.add_argument("file", help="台账 JSON 文件路径")
    p_import.add_argument("--replace", action="store_true", help="覆盖已存在的台账")

    p_query = sub.add_parser("query", parents=[common], help="本地查询接口")
    p_query.add_argument(
        "what",
        choices=[
            "info",
            "grids",
            "projects",
            "budgets",
            "commitments",
            "plans",
            "plan",
            "duplicates",
            "events",
            "evaluate",
            "uncovered",
            "compare",
            "change-orders",
        ],
    )
    p_query.add_argument("--plan", help="方案标识（evaluate/uncovered/plan/change-orders 需要）")
    p_query.add_argument("--plans", help="逗号分隔的两个方案标识（compare 需要）")
    p_query.add_argument("--limit", type=int, help="events 最多返回的条数")

    p_plan = sub.add_parser("plan", parents=[common], help="方案管理")
    p_plan.add_argument("action", choices=["create", "update", "freeze", "rollback", "merge", "change-order"])
    p_plan.add_argument("--plan", help="方案标识")
    p_plan.add_argument("--plans", help="逗号分隔的方案标识（merge 需要）")
    p_plan.add_argument("--name", help="方案名称（create/merge 需要）")
    p_plan.add_argument("--projects", help="逗号分隔的工程标识")
    p_plan.add_argument("--expected-revision", type=int, help="期望的方案修订号（update 需要）")
    p_plan.add_argument("--to-revision", type=int, help="回退目标修订号（rollback 需要）")
    p_plan.add_argument("--base-revision", type=int, help="变更单基准修订号（change-order 需要）")
    p_plan.add_argument("--add", help="逗号分隔的新增工程（change-order）")
    p_plan.add_argument("--remove", help="逗号分隔的移除工程（change-order）")
    p_plan.add_argument("--reason", help="变更理由（change-order 必填）")
    p_plan.add_argument("--author", help="经办人（change-order 必填）")

    p_budget = sub.add_parser("budget", parents=[common], help="预算批次管理")
    p_budget.add_argument("action", choices=["update"])
    p_budget.add_argument("--batch", required=True, help="预算批次标识")
    p_budget.add_argument("--expected-version", type=int, required=True, help="期望的批次版本号（防静默覆盖）")
    p_budget.add_argument("--total", type=float, help="新的预算总额（万元）")
    p_budget.add_argument("--name", help="新的批次名称")

    p_report = sub.add_parser("report", parents=[common], help="导出确定性方案报告")
    p_report.add_argument("--plan", required=True, help="方案标识")
    p_report.add_argument("--out", help="报告输出文件（缺省打印到标准输出）")
    return parser


def _cmd_import(app: Application, args) -> dict:
    path = Path(args.file)
    if not path.exists():
        raise ValidationError(f"台账文件不存在：{path}")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValidationError(f"台账文件不是合法 JSON：{exc}") from exc
    return app.import_ledger(raw, replace=args.replace)


def _cmd_query(app: Application, args):
    queries = app.queries
    what = args.what
    if what == "info":
        return queries.info()
    if what == "grids":
        return queries.grids()
    if what == "projects":
        return queries.projects()
    if what == "budgets":
        return queries.budgets()
    if what == "commitments":
        return queries.commitments()
    if what == "plans":
        return queries.plans()
    if what == "plan":
        return queries.plan(_require(args.plan, "query plan 需要 --plan"))
    if what == "duplicates":
        return queries.duplicates()
    if what == "events":
        return queries.events(limit=args.limit)
    if what == "evaluate":
        return queries.evaluate_plan(_require(args.plan, "query evaluate 需要 --plan"))
    if what == "uncovered":
        return queries.uncovered(_require(args.plan, "query uncovered 需要 --plan"))
    if what == "compare":
        pair = _csv(args.plans)
        if len(pair) != 2:
            raise ValidationError("query compare 需要 --plans 指定两个方案，如 PLAN-0001,PLAN-0002")
        return queries.compare(pair[0], pair[1])
    if what == "change-orders":
        return queries.change_orders(_require(args.plan, "query change-orders 需要 --plan"))
    raise ValidationError(f"未知查询：{what}")


def _cmd_plan(app: Application, args) -> dict:
    action = args.action
    if action == "create":
        return app.create_plan(_require(args.name, "plan create 需要 --name"), _csv(args.projects))
    if action == "update":
        return app.update_plan(
            _require(args.plan, "plan update 需要 --plan"),
            _require(args.expected_revision, "plan update 需要 --expected-revision"),
            _csv(args.projects),
        )
    if action == "freeze":
        return app.freeze_plan(_require(args.plan, "plan freeze 需要 --plan"))
    if action == "rollback":
        return app.rollback_plan(
            _require(args.plan, "plan rollback 需要 --plan"),
            _require(args.to_revision, "plan rollback 需要 --to-revision"),
        )
    if action == "merge":
        pair = _csv(args.plans)
        return app.merge_plans(pair, _require(args.name, "plan merge 需要 --name"))
    if action == "change-order":
        return app.apply_change_order(
            _require(args.plan, "plan change-order 需要 --plan"),
            _require(args.base_revision, "plan change-order 需要 --base-revision"),
            add=_csv(args.add),
            remove=_csv(args.remove),
            reason=args.reason or "",
            author=args.author or "",
        )
    raise ValidationError(f"未知方案操作：{action}")


def _cmd_budget(app: Application, args) -> dict:
    return app.update_budget(args.batch, args.expected_version, total=args.total, name=args.name)


def _cmd_report(app: Application, args) -> int:
    report = app.report(args.plan)
    text = render_report(report)
    if args.out:
        out_path = Path(args.out)
        if out_path.parent and str(out_path.parent) != ".":
            out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(text, encoding="utf-8")
        _print({"report_file": str(out_path), "plan": args.plan, "bytes": len(text.encode("utf-8"))})
    else:
        sys.stdout.write(text)
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    app = Application(args.data_dir)
    try:
        if args.command == "import":
            _print(_cmd_import(app, args))
        elif args.command == "query":
            _print(_cmd_query(app, args))
        elif args.command == "plan":
            _print(_cmd_plan(app, args))
        elif args.command == "budget":
            _print(_cmd_budget(app, args))
        elif args.command == "report":
            return _cmd_report(app, args)
    except LedgerError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
