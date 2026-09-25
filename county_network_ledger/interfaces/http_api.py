"""本地查询接口（HTTP/JSON，仅标准库）。

只暴露只读查询；所有写操作走命令行与事件账本。响应 JSON 一律
``ensure_ascii=False, sort_keys=True``，同状态同输入结果确定。

路由：
  GET /health
  GET /summary
  GET /grids
  GET /projects
  GET /budgets
  GET /commitments
  GET /plans
  GET /plans/{id}/evaluation
  GET /plans/{id}/revisions
  GET /plans/{id}/suggest
  GET /compare?ids=FA-0001,FA-0002
  GET /duplicates
  GET /report?quarter=4&ids=FA-0001,FA-0002
"""
from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from ..application.planning import PlanningService
from ..application.report import build_report
from ..domain.analysis import evaluate
from ..domain.errors import DomainError
from ..persistence.event_store import EventStore


def _json_bytes(obj) -> bytes:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True,
                      indent=2).encode("utf-8") + b"\n"


def _grid_dict(g) -> dict:
    return {"id": g.id, "name": g.name, "population": g.population,
            "bbox": [g.x0, g.y0, g.x1, g.y1],
            "industry": g.industry, "blank": g.blank}


def _project_dict(p) -> dict:
    return {
        "id": p.id, "name": p.name, "kind": p.kind,
        "covers": list(p.covers), "budget_id": p.budget_id,
        "cost": p.cost, "annual_maintenance": p.annual_maintenance,
        "start_q": p.start_q, "end_q": p.end_q, "revenue": p.revenue,
        "deps": list(p.deps), "shared_with": list(p.shared_with),
        "proposer": p.proposer,
    }


def _evaluation_dict(e) -> dict:
    return {
        "plan_id": e.plan_id,
        "project_ids": list(e.project_ids),
        "covered_grids": list(e.covered_grids),
        "fiber_grids": list(e.fiber_grids),
        "uncovered_grids": [g.id for g in e.uncovered],
        "blank_uncovered_grids": [g.id for g in e.blank_uncovered],
        "uncovered_industry_grids": [g.id for g in e.uncovered_industry],
        "basic_coverage_rate": e.basic_coverage_rate,
        "basic_population": e.basic_population,
        "total_population": e.total_population,
        "industry_population": e.industry_population,
        "gross_cost": e.gross_cost,
        "shared_funding": e.shared_funding,
        "county_cost": e.county_cost,
        "annual_maintenance": e.annual_maintenance,
        "maintenance_10y": e.maintenance_horizon_cost,
        "expected_annual_revenue": e.expected_annual_revenue,
        "budgets": {
            bid: {"name": u.name, "amount": round(u.amount, 2),
                  "county_cost": round(u.county_cost, 2),
                  "shared_funding": round(u.shared_funding, 2),
                  "remaining": round(u.remaining, 2),
                  "overrun": round(u.overrun, 2),
                  "project_ids": u.project_ids}
            for bid, u in sorted(e.budgets.items())
        },
        "duplicates": [
            {"project_a": d.project_a, "project_b": d.project_b,
             "grid_id": d.grid_id, "severity": d.severity}
            for d in e.duplicates
        ],
        "dependency_issues": [
            {"project_id": d.project_id, "missing_dep": d.missing_dep, "reason": d.reason}
            for d in e.dependency_issues
        ],
        "commitment_locked": list(e.commitment_locked),
        "commitment_removed": list(e.commitment_removed),
        "feasible": e.feasible,
        "violations": e.violations(),
    }


def create_handler(store: EventStore):
    class QueryHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):  # 安静：查询服务不污染验收输出
            return

        def _send(self, code: int, obj) -> None:
            body = _json_bytes(obj)
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _error(self, code: int, message: str, error_type: str) -> None:
            self._send(code, {"error": error_type, "message": message})

        def do_GET(self) -> None:  # noqa: N802
            parts = urlsplit(self.path)
            path = parts.path.rstrip("/") or "/"
            q = parse_qs(parts.query)
            svc = PlanningService(store)
            try:
                self._route(svc, path, q)
            except DomainError as exc:
                code = 404 if "不存在" in str(exc) else 400
                self._error(code, str(exc), type(exc).__name__)
            except Exception as exc:  # noqa: BLE001
                self._error(500, str(exc), type(exc).__name__)

        def _route(self, svc: PlanningService, path: str, q) -> None:
            if path == "/health":
                self._send(200, {"status": "ok"})
                return

            state = svc.state()
            tip = store.load_records()
            tip_hash = tip[-1]["hash"] if tip else "0" * 64

            if path == "/summary":
                self._send(200, {
                    "ledger_events": len(tip),
                    "ledger_tip_hash": tip_hash,
                    "grids": len(state.grids),
                    "capabilities": len(state.capabilities),
                    "projects": len(state.projects),
                    "budgets": len(state.budgets),
                    "commitments": len(state.commitments),
                    "plans": {pid: p.status for pid, p in sorted(state.plans.items())},
                    "change_orders": len(state.change_orders),
                })
            elif path == "/grids":
                self._send(200, [_grid_dict(state.grids[g]) for g in sorted(state.grids)])
            elif path == "/projects":
                self._send(200, [_project_dict(state.projects[p])
                                 for p in sorted(state.projects)])
            elif path == "/budgets":
                self._send(200, [{
                    "budget_id": b.id, "name": b.name, "amount": b.amount,
                    "quarter": b.quarter, "version": b.version,
                } for b in sorted(state.budgets.values(), key=lambda x: x.id)])
            elif path == "/commitments":
                self._send(200, [{
                    "id": c.id, "project_id": c.project_id, "party": c.party,
                    "shared_by": c.shared_by, "share": c.share,
                    "signed_q": c.signed_q, "editable": c.editable,
                } for c in sorted(state.commitments.values(), key=lambda x: x.id)])
            elif path == "/plans":
                self._send(200, [{
                    "plan_id": p.id, "name": p.name, "status": p.status,
                    "created_q": p.created_q, "frozen_q": p.frozen_q,
                    "basis": p.basis, "revision_count": p.revision_count,
                    "project_ids": list(p.project_ids),
                } for p in sorted(state.plans.values(), key=lambda x: x.id)])
            elif path == "/duplicates":
                self._send(200, svc.duplicates_overall())
            elif path == "/compare":
                ids = q.get("ids", [""])[0].split(",")
                ids = [i.strip() for i in ids if i.strip()]
                self._send(200, svc.compare_plans(ids))
            elif path == "/report":
                quarter = int(q.get("quarter", ["0"])[0])
                ids_raw = q.get("ids", [""])[0].strip()
                ids = [i.strip() for i in ids_raw.split(",") if i.strip()] or None
                self._send(200, build_report(store, quarter, ids))
            elif path.startswith("/plans/"):
                rest = path[len("/plans/"):].split("/")
                if len(rest) == 2 and rest[1] == "evaluation":
                    self._send(200, _evaluation_dict(svc.evaluate_plan(rest[0])))
                elif len(rest) == 2 and rest[1] == "suggest":
                    self._send(200, svc.suggest_for_uncovered(rest[0]))
                elif len(rest) == 2 and rest[1] == "revisions":
                    plan = state.plans[rest[0]]
                    if plan is None:
                        self._error(404, f"方案不存在：{rest[0]}", "NotFoundError")
                        return
                    self._send(200, [{
                        "index": r.index, "label": r.label,
                        "quarter": r.quarter, "note": r.note,
                        "project_ids": list(r.project_ids),
                    } for r in plan.revisions])
                else:
                    self._error(404, f"未知路由：{path}", "NotFound")
            else:
                self._error(404, f"未知路由：{path}", "NotFound")

    return QueryHandler


def serve(store: EventStore, host: str = "127.0.0.1", port: int = 8080) -> ThreadingHTTPServer:
    httpd = ThreadingHTTPServer((host, port), create_handler(store))
    return httpd
