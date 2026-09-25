"""JSON 台账导入：校验并整体落账。

导入是全有或全无的：所有记录先通过校验，再在一次事务内追加事件，
保证不会出现半套台账。重复导入同一文件（按记录 id 幂等）安全。
"""
from __future__ import annotations

import json
import os
from typing import Any

from ..domain import events as ev
from ..domain.entities import COVERAGE_LEVELS
from ..domain.errors import ValidationError
from ..persistence.event_store import EventStore

PROJECT_KINDS = {"2G/3G站点", "4G站点", "5G站点", "光纤", "光纤延伸"}


def _require(cond: bool, msg: str) -> None:
    if not cond:
        raise ValidationError(msg)


def validate_ledger(data: Any) -> None:
    """对导入 JSON 做完整结构与引用校验。空白/缺失段落按空处理。"""
    _require(isinstance(data, dict), "台账顶层必须是 JSON 对象")

    grids = data.get("grids", [])
    caps = data.get("capabilities", [])
    projects = data.get("projects", [])
    budgets = data.get("budgets", [])
    commitments = data.get("commitments", [])
    for name, section in (("grids", grids), ("capabilities", caps),
                          ("projects", projects), ("budgets", budgets),
                          ("commitments", commitments)):
        _require(isinstance(section, list), f"台账段落 {name} 必须是数组")

    grid_ids: set[str] = set()
    for g in grids:
        _require(isinstance(g, dict), "grids 中存在非对象记录")
        gid = g.get("id")
        _require(isinstance(gid, str) and gid, "网格 id 必须为非空字符串")
        _require(gid not in grid_ids, f"网格 id 重复：{gid}")
        grid_ids.add(gid)
        _require(isinstance(g.get("name"), str) and g["name"], f"网格 {gid} 缺少 name")
        _require(isinstance(g.get("population"), int) and g["population"] >= 0,
                 f"网格 {gid} 的 population 必须为非负整数")
        coords = [g.get(k) for k in ("x0", "y0", "x1", "y1")]
        _require(all(isinstance(c, (int, float)) for c in coords),
                 f"网格 {gid} 的坐标必须为数值")
        x0, y0, x1, y1 = coords
        _require(x1 >= x0 and y1 >= y0, f"网格 {gid} 的包围盒坐标无效（x1>=x0, y1>=y0）")
        if "industry" in g and g["industry"] is not None:
            _require(isinstance(g["industry"], str), f"网格 {gid} 的 industry 必须为字符串")
        if "blank" in g:
            _require(isinstance(g["blank"], bool), f"网格 {gid} 的 blank 必须为布尔值")

    cap_ids: set[str] = set()
    for c in caps:
        cid = c.get("id")
        _require(isinstance(cid, str) and cid, "现有能力 id 必须为非空字符串")
        _require(cid not in cap_ids, f"现有能力 id 重复：{cid}")
        cap_ids.add(cid)
        _require(c.get("grid_id") in grid_ids, f"能力 {cid} 引用了不存在的网格 {c.get('grid_id')}")
        _require(c.get("kind") in COVERAGE_LEVELS, f"能力 {cid} 的 kind 非法：{c.get('kind')}")
        _require(isinstance(c.get("operator"), str) and c["operator"], f"能力 {cid} 缺少 operator")

    budget_ids: set[str] = set()
    for b in budgets:
        bid = b.get("id")
        _require(isinstance(bid, str) and bid, "预算批次 id 必须为非空字符串")
        _require(bid not in budget_ids, f"预算批次 id 重复：{bid}")
        budget_ids.add(bid)
        _require(isinstance(b.get("name"), str) and b["name"], f"预算批次 {bid} 缺少 name")
        _require(isinstance(b.get("amount"), (int, float)) and b["amount"] >= 0,
                 f"预算批次 {bid} 的 amount 必须为非负数")
        _require(isinstance(b.get("quarter"), int), f"预算批次 {bid} 的 quarter 必须为整数")

    project_ids: set[str] = set()
    for p in projects:
        pid = p.get("id")
        _require(isinstance(pid, str) and pid, "候选工程 id 必须为非空字符串")
        _require(pid not in project_ids, f"候选工程 id 重复：{pid}")
        project_ids.add(pid)
    # 依赖引用需要在全部 id 收集后再校验
    for p in projects:
        pid = p["id"]
        _require(isinstance(p.get("name"), str) and p["name"], f"工程 {pid} 缺少 name")
        _require(p.get("kind") in PROJECT_KINDS, f"工程 {pid} 的 kind 非法：{p.get('kind')}")
        covers = p.get("covers", [])
        _require(isinstance(covers, list) and covers, f"工程 {pid} 的 covers 必须为非空数组")
        for gid in covers:
            _require(gid in grid_ids, f"工程 {pid} 覆盖了不存在的网格 {gid}")
        _require(p.get("budget_id") in budget_ids,
                 f"工程 {pid} 引用了不存在的预算批次 {p.get('budget_id')}")
        _require(isinstance(p.get("cost"), (int, float)) and p["cost"] >= 0,
                 f"工程 {pid} 的 cost 必须为非负数")
        am = p.get("annual_maintenance", 0.0)
        _require(isinstance(am, (int, float)) and am >= 0,
                 f"工程 {pid} 的 annual_maintenance 必须为非负数")
        sq, eq = p.get("start_q"), p.get("end_q")
        _require(isinstance(sq, int) and isinstance(eq, int) and eq >= sq,
                 f"工程 {pid} 的施工季度无效（要求整数且 end_q >= start_q）")
        revenue = p.get("revenue", 0.0)
        _require(isinstance(revenue, (int, float)) and revenue >= 0,
                 f"工程 {pid} 的 revenue 必须为非负数")
        for dep in p.get("deps", []):
            _require(dep in project_ids and dep != pid,
                     f"工程 {pid} 的依赖 {dep} 不存在或自引用")
        for sw in p.get("shared_with", []):
            _require(isinstance(sw, str) and sw, f"工程 {pid} 的 shared_with 含非法值")

    commit_ids: set[str] = set()
    for c in commitments:
        cid = c.get("id")
        _require(isinstance(cid, str) and cid, "承诺 id 必须为非空字符串")
        _require(cid not in commit_ids, f"承诺 id 重复：{cid}")
        commit_ids.add(cid)
        _require(c.get("project_id") in project_ids,
                 f"承诺 {cid} 引用了不存在的工程 {c.get('project_id')}")
        _require(isinstance(c.get("party"), str) and c["party"], f"承诺 {cid} 缺少 party")
        _require(isinstance(c.get("shared_by"), str) and c["shared_by"],
                 f"承诺 {cid} 缺少 shared_by")
        share = c.get("share")
        _require(isinstance(share, (int, float)) and 0.0 <= share <= 1.0,
                 f"承诺 {cid} 的 share 必须在 0~1 之间")
        _require(isinstance(c.get("signed_q"), int), f"承诺 {cid} 的 signed_q 必须为整数")

    # 同一工程的承诺分担比例合计不得超过 1
    share_sum: dict[str, float] = {}
    for c in commitments:
        share_sum[c["project_id"]] = share_sum.get(c["project_id"], 0.0) + c["share"]
    for pid, total in share_sum.items():
        _require(total <= 1.0 + 1e-9, f"工程 {pid} 的承诺分担比例合计超过 1（{total}）")


def _grid_payload(g: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": g["id"], "name": g["name"], "population": int(g["population"]),
        "x0": float(g["x0"]), "y0": float(g["y0"]),
        "x1": float(g["x1"]), "y1": float(g["y1"]),
        "industry": g.get("industry"), "blank": bool(g.get("blank", False)),
    }


def import_ledger(store: EventStore, data: Any) -> dict[str, int]:
    """校验并把一套台账落账。返回各段落事件计数。已存在的同 id 记录跳过。"""
    validate_ledger(data)
    staged: list[tuple[str, dict[str, Any]]] = []
    counts = {"grids": 0, "capabilities": 0, "projects": 0,
              "budgets": 0, "commitments": 0}

    with store.transaction() as tx:
        for g in data.get("grids", []):
            if g["id"] in tx.state.grids:
                continue
            staged.append((ev.GRID_IMPORTED, _grid_payload(g)))
            counts["grids"] += 1
        for c in data.get("capabilities", []):
            if any(x.id == c["id"] for x in tx.state.capabilities):
                continue
            staged.append((ev.CAPABILITY_IMPORTED, {
                "id": c["id"], "grid_id": c["grid_id"], "kind": c["kind"],
                "operator": c["operator"],
            }))
            counts["capabilities"] += 1
        for b in data.get("budgets", []):
            if b["id"] in tx.state.budgets:
                continue
            staged.append((ev.BUDGET_IMPORTED, {
                "id": b["id"], "name": b["name"],
                "amount": float(b["amount"]), "quarter": int(b["quarter"]),
            }))
            counts["budgets"] += 1
        for p in data.get("projects", []):
            if p["id"] in tx.state.projects:
                continue
            staged.append((ev.PROJECT_IMPORTED, {
                "id": p["id"], "name": p["name"], "kind": p["kind"],
                "covers": list(p["covers"]), "budget_id": p["budget_id"],
                "cost": float(p["cost"]),
                "annual_maintenance": float(p.get("annual_maintenance", 0.0)),
                "start_q": int(p["start_q"]), "end_q": int(p["end_q"]),
                "revenue": float(p.get("revenue", 0.0)),
                "deps": list(p.get("deps", [])),
                "shared_with": list(p.get("shared_with", [])),
                "proposer": p.get("proposer", ""),
            }))
            counts["projects"] += 1
        for c in data.get("commitments", []):
            if c["id"] in tx.state.commitments:
                continue
            staged.append((ev.COMMITMENT_SIGNED, {
                "id": c["id"], "project_id": c["project_id"],
                "party": c["party"], "shared_by": c["shared_by"],
                "share": float(c["share"]), "signed_q": int(c["signed_q"]),
            }))
            counts["commitments"] += 1
        tx.add_all(staged)
    return counts


def import_file(store: EventStore, path: str) -> dict[str, int]:
    if not os.path.exists(path):
        raise ValidationError(f"台账文件不存在：{path}")
    with open(path, "r", encoding="utf-8") as fh:
        try:
            data = json.load(fh)
        except json.JSONDecodeError as exc:
            raise ValidationError(f"台账文件不是合法 JSON：{exc}") from exc
    return import_ledger(store, data)
