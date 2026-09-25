"""确定性规划报告导出。

同一份账本状态必然产生逐字节一致的报告：键排序、固定小数位、
列表全部按稳定键排序。报告包含：方案取舍理由（修订留痕）、
横向比较、预算占用、重复建设、跨期依赖、未覆盖/空白网格清单、
以及面向未覆盖网格的补建建议与变更单台账。
"""
from __future__ import annotations

import json
from typing import Any, Sequence

from ..domain.analysis import PlanEvaluation, compare, evaluate
from ..domain.state import LedgerState, PlanState
from ..persistence.event_store import EventStore, canonical_json


def _grid_row(grid) -> dict[str, Any]:
    return {
        "grid_id": grid.id, "name": grid.name,
        "population": grid.population, "blank": grid.blank,
        "industry": grid.industry,
        "bbox": [grid.x0, grid.y0, grid.x1, grid.y1],
    }


def _evaluation_block(state: LedgerState, evl: PlanEvaluation) -> dict[str, Any]:
    return {
        "plan_id": evl.plan_id,
        "project_ids": list(evl.project_ids),
        "coverage": {
            "basic_coverage_rate": evl.basic_coverage_rate,
            "basic_population": evl.basic_population,
            "total_population": evl.total_population,
            "covered_grid_count": len(evl.covered_grids),
            "covered_grids": list(evl.covered_grids),
            "fiber_grid_count": len(evl.fiber_grids),
            "fiber_grids": list(evl.fiber_grids),
        },
        "industry": {
            "served_population": evl.industry_population,
            "project_ids": list(evl.industry_projects),
            "uncovered_industry_grids": [_grid_row(g) for g in evl.uncovered_industry],
        },
        "cost": {
            "gross_cost": evl.gross_cost,
            "shared_funding": evl.shared_funding,
            "county_cost": evl.county_cost,
            "annual_maintenance": evl.annual_maintenance,
            "maintenance_10y": evl.maintenance_horizon_cost,
            "expected_annual_revenue": evl.expected_annual_revenue,
        },
        "budgets": {
            bid: {
                "name": u.name, "amount": round(u.amount, 2),
                "gross_cost": round(u.gross_cost, 2),
                "shared_funding": round(u.shared_funding, 2),
                "county_cost": round(u.county_cost, 2),
                "remaining": round(u.remaining, 2),
                "overrun": round(u.overrun, 2),
                "project_ids": u.project_ids,
            }
            for bid, u in sorted(evl.budgets.items())
        },
        "duplicates": [
            {"project_a": d.project_a, "project_b": d.project_b,
             "grid_id": d.grid_id, "severity": d.severity,
             "spatial": d.spatial, "temporal": d.temporal, "same_kind": d.same_kind}
            for d in evl.duplicates
        ],
        "dependency_issues": [
            {"project_id": d.project_id, "missing_dep": d.missing_dep, "reason": d.reason}
            for d in evl.dependency_issues
        ],
        "commitments": {
            "locked_project_ids": list(evl.commitment_locked),
            "missing_committed_project_ids": list(evl.commitment_removed),
        },
        "feasible": evl.feasible,
        "violations": evl.violations(),
    }


def _uncovered_block(state: LedgerState, evl: PlanEvaluation) -> dict[str, Any]:
    """未覆盖清单，并为每个网格附上可覆盖它的候选工程及成本/收益。"""
    items = []
    for grid in evl.uncovered:
        options = []
        for pid, pr in state.projects.items():
            if grid.id in pr.covers and pid not in evl.project_ids:
                options.append({
                    "project_id": pid, "name": pr.name, "kind": pr.kind,
                    "county_cost": round(pr.cost * (1 - state.commitment_share(pid)), 2),
                    "annual_revenue": round(pr.revenue, 2),
                    "proposer": pr.proposer,
                })
        options.sort(key=lambda r: (r["county_cost"], r["project_id"]))
        row = _grid_row(grid)
        row["candidate_projects"] = options
        items.append(row)
    return {
        "count": len(items),
        "blank_count": len(evl.blank_uncovered),
        "grids": items,
    }


def _plan_block(state: LedgerState, plan: PlanState) -> dict[str, Any]:
    evl = evaluate(state, plan)
    return {
        "plan_id": plan.id,
        "name": plan.name,
        "status": plan.status,
        "created_q": plan.created_q,
        "frozen_q": plan.frozen_q,
        "basis": plan.basis,
        "rationale": {
            # 取舍理由来自规划人员每次修订强制填写的留痕说明
            "revisions": [
                {"index": r.index, "label": r.label, "quarter": r.quarter, "note": r.note,
                 "project_ids": list(r.project_ids)}
                for r in plan.revisions
            ],
        },
        "evaluation": _evaluation_block(state, evl),
        "uncovered": _uncovered_block(state, evl),
    }


def build_report(store: EventStore, quarter: int,
                 plan_ids: Sequence[str] | None = None) -> dict[str, Any]:
    """构造确定性报告字典。tip_hash 锚定报告所基于的账本版本。"""
    records = store.load_records()
    state = store.state()
    tip_hash = records[-1]["hash"] if records else "0" * 64

    if plan_ids is None:
        plans = [state.plans[pid] for pid in sorted(state.plans)]
    else:
        plans = [state.plans[pid] for pid in plan_ids if pid in state.plans]

    comparison = compare([evaluate(state, p) for p in plans]) if plans else []

    # 变更单台账
    change_orders = []
    for co_id in sorted(state.change_orders):
        co = state.change_orders[co_id]
        change_orders.append({
            "co_id": co.id, "plan_id": co.plan_id, "kind": co.kind,
            "project_ids": list(co.project_ids), "reason": co.reason,
            "proposer": co.proposer, "status": co.status,
            "quarter": co.quarter, "decided_q": co.decided_q,
            "commitment_breach_recorded": co.breach,
        })

    # 预算批次当前状态（含乐观版本号，供多人编辑时比对）
    budgets = []
    for bid in sorted(state.budgets):
        b = state.budgets[bid]
        budgets.append({"budget_id": b.id, "name": b.name, "amount": round(b.amount, 2),
                        "quarter": b.quarter, "version": b.version})

    return {
        "meta": {
            "title": "县域通信共建规划报告",
            "quarter": int(quarter),
            "ledger_events": len(records),
            "ledger_tip_hash": tip_hash,
        },
        "budgets": budgets,
        "plans": [_plan_block(state, p) for p in plans],
        "comparison": comparison,
        "change_orders": change_orders,
    }


def render_json(report: dict[str, Any]) -> str:
    """规范 JSON：键排序、不转义中文、行尾单个换行。"""
    return json.dumps(report, ensure_ascii=False, sort_keys=True,
                      indent=2, separators=(",", ": ")) + "\n"


def render_markdown(report: dict[str, Any]) -> str:
    """渲染为人工可读 Markdown（同样确定）。"""
    lines: list[str] = []
    meta = report["meta"]
    lines.append(f"# {meta['title']}")
    lines.append("")
    lines.append(f"- 报告季度：{meta['quarter']}")
    lines.append(f"- 账本事件数：{meta['ledger_events']}")
    lines.append(f"- 账本锚点哈希：`{meta['ledger_tip_hash']}`")
    lines.append("")

    lines.append("## 预算批次")
    lines.append("")
    lines.append("| 批次 | 名称 | 额度(元) | 到位季度 | 版本 |")
    lines.append("| --- | --- | --- | --- | --- |")
    for b in report["budgets"]:
        lines.append(f"| {b['budget_id']} | {b['name']} | {b['amount']:.2f} "
                     f"| {b['quarter']} | {b['version']} |")
    lines.append("")

    lines.append("## 方案比较")
    lines.append("")
    if report["comparison"]:
        lines.append("| 方案 | 工程数 | 基本覆盖率 | 覆盖人口 | 光纤网格 | "
                     "产业人口 | 未覆盖 | 空白未覆盖 | 县级配套(元) | 共建分担(元) | "
                     "年运维(元) | 10年运维(元) | 重复(处) | 依赖问题 | 可行 |")
        lines.append("|" + " --- |" * 15)
        for r in report["comparison"]:
            lines.append(
                f"| {r['plan_id']} | {r['project_count']} | {r['basic_coverage_rate']:.4f} "
                f"| {r['basic_population']} | {r['fiber_grid_count']} "
                f"| {r['industry_population']} | {r['uncovered_count']} "
                f"| {r['blank_uncovered_count']} | {r['county_cost']:.2f} "
                f"| {r['shared_funding']:.2f} | {r['annual_maintenance']:.2f} "
                f"| {r['maintenance_10y']:.2f} | {r['duplicate_count']} "
                f"| {r['dependency_issue_count']} | {'是' if r['feasible'] else '否'} |")
    else:
        lines.append("（暂无方案）")
    lines.append("")

    for plan in report["plans"]:
        evl = plan["evaluation"]
        lines.append(f"## 方案 {plan['plan_id']}：{plan['name']}（{plan['status']}）")
        lines.append("")
        lines.append("### 取舍理由（修订留痕）")
        lines.append("")
        for r in plan["rationale"]["revisions"]:
            note = r["note"] or "（无）"
            lines.append(f"- 第 {r['index'] + 1} 版 · 季度 {r['quarter']} · "
                         f"{r['label']}：{note}")
        lines.append("")
        cov = evl["coverage"]
        cost = evl["cost"]
        lines.append("### 影响评估")
        lines.append("")
        lines.append(f"- 基本覆盖率：{cov['basic_coverage_rate']:.4f}"
                     f"（{cov['basic_population']}/{cov['total_population']} 人）")
        lines.append(f"- 光纤覆盖网格：{cov['fiber_grid_count']} 个")
        lines.append(f"- 重点产业覆盖人口：{evl['industry']['served_population']} 人")
        lines.append(f"- 工程毛成本：{cost['gross_cost']:.2f} 元；"
                     f"共建分担：{cost['shared_funding']:.2f} 元；"
                     f"县级配套：{cost['county_cost']:.2f} 元")
        lines.append(f"- 年运维：{cost['annual_maintenance']:.2f} 元；"
                     f"10 年运维：{cost['maintenance_10y']:.2f} 元；"
                     f"预期年收益：{cost['expected_annual_revenue']:.2f} 元")
        if evl["violations"]:
            lines.append("- 不可行项：" + "；".join(evl["violations"]))
        lines.append("")
        if evl["duplicates"]:
            lines.append("### 重复建设")
            lines.append("")
            for d in evl["duplicates"]:
                lines.append(f"- {d['severity']}：{d['project_a']} ↔ {d['project_b']}"
                             f"（网格 {d['grid_id']}）")
            lines.append("")
        unc = plan["uncovered"]
        lines.append(f"### 未覆盖清单（{unc['count']} 个，其中空白网格 {unc['blank_count']} 个）")
        lines.append("")
        if unc["grids"]:
            lines.append("| 网格 | 名称 | 人口 | 空白 | 重点产业 | 可选补建工程（县级成本/年收益） |")
            lines.append("| --- | --- | --- | --- | --- | --- |")
            for g in unc["grids"]:
                opts = "；".join(
                    f"{o['project_id']} {o['name']}({o['county_cost']:.0f}元/"
                    f"{o['annual_revenue']:.0f}元, {o['proposer']})"
                    for o in g["candidate_projects"]
                ) or "无候选工程"
                lines.append(f"| {g['grid_id']} | {g['name']} | {g['population']} "
                             f"| {'是' if g['blank'] else '否'} | {g['industry'] or '—'} | {opts} |")
        else:
            lines.append("全部网格达到基本覆盖。")
        lines.append("")

    if report["change_orders"]:
        lines.append("## 变更单台账（冻结后留痕修订）")
        lines.append("")
        lines.append("| 单号 | 方案 | 类型 | 工程 | 理由 | 状态 | 违约留痕 |")
        lines.append("| --- | --- | --- | --- | --- | --- | --- |")
        for co in report["change_orders"]:
            lines.append(
                f"| {co['co_id']} | {co['plan_id']} | {co['kind']} "
                f"| {'、'.join(co['project_ids'])} | {co['reason']} | {co['status']} "
                f"| {'是' if co['commitment_breach_recorded'] else '否'} |")
        lines.append("")

    return "\n".join(lines) + "\n"


def write_report(report: dict[str, Any], path: str) -> str:
    """按扩展名写出 .json 或 .md，返回实际使用的格式。"""
    if path.endswith(".md"):
        content = render_markdown(report)
    else:
        content = render_json(report)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)
    return "markdown" if path.endswith(".md") else "json"
