"""命令行接口：导入台账、方案操作、变更单、报告、查询服务。

所有写命令必须显式传入业务季度 ``--quarter`` 与取舍理由 ``--note``/``--reason``。
结构化结果以规范 JSON 输出到标准输出，便于脚本验收；人类阅读用报告导出。

典型流程见 README；数据文件默认放在源码树外的 var/ 目录。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Sequence

from ..application.importer import import_file
from ..application.planning import PlanningService
from ..application.report import build_report, write_report
from ..domain.errors import DomainError
from ..persistence.event_store import EventStore
from .http_api import serve

DEFAULT_LEDGER = os.environ.get(
    "CNL_LEDGER_PATH",
    os.path.join(os.getcwd(), "var", "ledger.jsonl"),
)


def _out(obj: Any) -> None:
    print(json.dumps(obj, ensure_ascii=False, sort_keys=True, indent=2))


def _ids(value: str | None) -> list[str]:
    if not value:
        return []
    return [x.strip() for x in value.split(",") if x.strip()]


def _store(path: str) -> EventStore:
    return EventStore(path)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cnl", description="县域通信共建规划账本命令行")
    parser.add_argument("--ledger", default=DEFAULT_LEDGER,
                        help=f"事件账本路径（默认 {DEFAULT_LEDGER} 或环境变量 CNL_LEDGER_PATH）")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("import", help="导入一组 JSON 台账（全有或全无，可重复执行）")
    p.add_argument("file")

    sub.add_parser("verify", help="校验事件账本哈希链")

    p = sub.add_parser("summary", help="输出账本汇总")

    sub.add_parser("duplicates", help="跨申报方空间/时间重复建设扫描")

    p = sub.add_parser("plans", help="列出全部方案")

    p = sub.add_parser("evaluate", help="评估一个方案或一组工程")
    p.add_argument("plan_or_ids", help="方案 id（如 FA-0001）或逗号分隔的工程 id")
    p.add_argument("--by-ids", action="store_true", help="按工程 id 列表即时评估")

    p = sub.add_parser("compare", help="横向比较多个方案")
    p.add_argument("plan_ids", help="逗号分隔的方案 id")

    p = sub.add_parser("suggest", help="为方案的未覆盖网格推荐补建工程")
    p.add_argument("plan_id")

    p = sub.add_parser("budget-adjust", help="调整预算批次（乐观并发，需带版本号）")
    p.add_argument("--budget", required=True)
    p.add_argument("--amount", required=True, type=float)
    p.add_argument("--quarter", required=True, type=int)
    p.add_argument("--expected-version", required=True, type=int,
                   help="调用方读到的批次版本号，冲突时命令失败而非静默覆盖")

    p = sub.add_parser("plan-create", help="创建工作副本方案")
    p.add_argument("--name", required=True)
    p.add_argument("--quarter", required=True, type=int)
    p.add_argument("--projects", default="", help="逗号分隔的候选工程 id")
    p.add_argument("--note", required=True, help="编制说明/取舍理由（强制留痕）")
    p.add_argument("--basis", default=None, help="派生来源方案 id")

    p = sub.add_parser("plan-set", help="全量设置方案选入工程（自动保留历史版本）")
    p.add_argument("plan_id")
    p.add_argument("--projects", required=True)
    p.add_argument("--quarter", required=True, type=int)
    p.add_argument("--note", required=True)

    p = sub.add_parser("plan-merge", help="合并另一方案到工作副本")
    p.add_argument("target_plan_id")
    p.add_argument("--source", required=True)
    p.add_argument("--quarter", required=True, type=int)
    p.add_argument("--note", required=True)
    p.add_argument("--exclude", default="", help="合并且明确排除的工程 id")

    p = sub.add_parser("plan-rollback", help="回退方案到历史版本")
    p.add_argument("plan_id")
    p.add_argument("--index", required=True, type=int, help="目标历史版本序号（从 0 开始）")
    p.add_argument("--quarter", required=True, type=int)
    p.add_argument("--note", required=True)

    p = sub.add_parser("plan-discard", help="废弃未冻结方案")
    p.add_argument("plan_id")
    p.add_argument("--quarter", required=True, type=int)

    p = sub.add_parser("plan-freeze", help="冻结为正式方案（此后只能走变更单）")
    p.add_argument("plan_id")
    p.add_argument("--quarter", required=True, type=int)
    p.add_argument("--reason", required=True)
    p.add_argument("--acknowledge-duplicates", action="store_true",
                   help="已知晓双重重复（如已协调共址分期），显式确认冻结")

    p = sub.add_parser("change-propose", help="对冻结方案提交变更单")
    p.add_argument("plan_id")
    p.add_argument("--kind", required=True, choices=["add", "remove"])
    p.add_argument("--projects", required=True)
    p.add_argument("--quarter", required=True, type=int)
    p.add_argument("--reason", required=True)
    p.add_argument("--proposer", default="")

    p = sub.add_parser("change-approve", help="批准并执行变更单（重新校验可行性与承诺）")
    p.add_argument("co_id")
    p.add_argument("--quarter", required=True, type=int)
    p.add_argument("--acknowledge-breach", action="store_true",
                   help="移除承诺工程时显式确认，并永久记录承诺违约")

    p = sub.add_parser("change-reject", help="驳回变更单")
    p.add_argument("co_id")
    p.add_argument("--quarter", required=True, type=int)

    p = sub.add_parser("report", help="导出确定性报告（.json 或 .md）")
    p.add_argument("--out", required=True)
    p.add_argument("--quarter", required=True, type=int)
    p.add_argument("--plans", default="", help="逗号分隔方案 id；缺省为全部方案")

    p = sub.add_parser("serve", help="启动本地只读查询接口")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", default=8080, type=int)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    store = _store(args.ledger)
    svc = PlanningService(store)

    try:
        if args.command == "import":
            _out({"imported": import_file(store, args.file), "events": store.version})

        elif args.command == "verify":
            records = store.load_records()
            _out({"ok": True, "events": len(records),
                  "tip_hash": records[-1]["hash"] if records else "0" * 64})

        elif args.command == "summary":
            state = store.state()
            records = store.load_records()
            _out({
                "events": len(records),
                "tip_hash": records[-1]["hash"] if records else "0" * 64,
                "grids": len(state.grids), "capabilities": len(state.capabilities),
                "projects": len(state.projects), "budgets": len(state.budgets),
                "commitments": len(state.commitments),
                "plans": {pid: p.status for pid, p in sorted(state.plans.items())},
                "change_orders": len(state.change_orders),
            })

        elif args.command == "duplicates":
            _out(svc.duplicates_overall())

        elif args.command == "plans":
            state = store.state()
            _out([{
                "plan_id": p.id, "name": p.name, "status": p.status,
                "created_q": p.created_q, "frozen_q": p.frozen_q,
                "revision_count": p.revision_count,
                "project_ids": list(p.project_ids),
            } for p in sorted(state.plans.values(), key=lambda x: x.id)])

        elif args.command == "evaluate":
            if args.by_ids:
                evl = svc.evaluate_ids(_ids(args.plan_or_ids))
            else:
                evl = svc.evaluate_plan(args.plan_or_ids)
            from ..interfaces.http_api import _evaluation_dict
            _out(_evaluation_dict(evl))

        elif args.command == "compare":
            _out(svc.compare_plans(_ids(args.plan_ids)))

        elif args.command == "suggest":
            _out(svc.suggest_for_uncovered(args.plan_id))

        elif args.command == "budget-adjust":
            _out(svc.update_budget(args.budget, args.amount, args.quarter,
                                   args.expected_version))

        elif args.command == "plan-create":
            pid = svc.create_plan(args.name, args.quarter, _ids(args.projects),
                                  basis=args.basis, note=args.note)
            _out({"plan_id": pid})

        elif args.command == "plan-set":
            svc.set_projects(args.plan_id, _ids(args.projects), args.quarter, args.note)
            _out({"ok": True, "plan_id": args.plan_id})

        elif args.command == "plan-merge":
            svc.merge_plan(args.target_plan_id, args.source, args.quarter,
                           args.note, _ids(args.exclude))
            _out({"ok": True, "target": args.target_plan_id})

        elif args.command == "plan-rollback":
            svc.rollback_plan(args.plan_id, args.index, args.quarter, args.note)
            _out({"ok": True, "plan_id": args.plan_id})

        elif args.command == "plan-discard":
            svc.discard_plan(args.plan_id, args.quarter)
            _out({"ok": True})

        elif args.command == "plan-freeze":
            evl = svc.freeze_plan(args.plan_id, args.quarter, args.reason,
                                  args.acknowledge_duplicates)
            _out({"ok": True, "plan_id": args.plan_id,
                  "basic_coverage_rate": evl.basic_coverage_rate,
                  "uncovered_count": len(evl.uncovered)})

        elif args.command == "change-propose":
            co_id = svc.propose_change_order(
                args.plan_id, args.kind, _ids(args.projects),
                args.quarter, args.reason, args.proposer)
            _out({"co_id": co_id})

        elif args.command == "change-approve":
            _out({"ok": True, **svc.approve_change_order(
                args.co_id, args.quarter, args.acknowledge_breach)})

        elif args.command == "change-reject":
            svc.reject_change_order(args.co_id, args.quarter)
            _out({"ok": True})

        elif args.command == "report":
            plan_ids = _ids(args.plans) or None
            report = build_report(store, args.quarter, plan_ids)
            fmt = write_report(report, args.out)
            _out({"ok": True, "format": fmt, "path": os.path.abspath(args.out),
                  "plans": len(report["plans"]),
                  "tip_hash": report["meta"]["ledger_tip_hash"]})

        elif args.command == "serve":
            httpd = serve(store, args.host, args.port)
            print(f"查询接口已启动：http://{args.host}:{args.port}（Ctrl+C 停止）",
                  file=sys.stderr)
            try:
                httpd.serve_forever()
            except KeyboardInterrupt:
                pass
            finally:
                httpd.server_close()
            return 0

    except DomainError as exc:
        print(json.dumps({"error": type(exc).__name__, "message": str(exc)},
                         ensure_ascii=False), file=sys.stderr)
        return 2

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
