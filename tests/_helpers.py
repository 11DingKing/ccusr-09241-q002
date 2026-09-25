"""测试公共工具：临时账本与示例台账。"""
from __future__ import annotations

import json
import os
import tempfile

from county_network_ledger.application.importer import import_ledger
from county_network_ledger.persistence.event_store import EventStore


SAMPLE_LEDGER = {
    "grids": [
        {"id": "G-01", "name": "城关", "population": 12000,
         "x0": 0, "y0": 0, "x1": 2, "y1": 2},
        {"id": "G-02", "name": "产业园", "population": 3200,
         "x0": 2, "y0": 0, "x1": 4, "y1": 2, "industry": "茶叶加工"},
        {"id": "G-03", "name": "河西村", "population": 1800,
         "x0": 0, "y0": 2, "x1": 2, "y1": 4, "blank": True},
        {"id": "G-04", "name": "高山村", "population": 620,
         "x0": 4, "y0": 4, "x1": 6, "y1": 6, "blank": True},
        {"id": "G-05", "name": "南山村", "population": 960,
         "x0": 2, "y0": 2, "x1": 4, "y1": 4},
    ],
    "capabilities": [
        {"id": "E-01", "grid_id": "G-01", "kind": "4G", "operator": "电信"},
        {"id": "E-02", "grid_id": "G-01", "kind": "光纤", "operator": "电信"},
        {"id": "E-03", "grid_id": "G-05", "kind": "2G/3G", "operator": "移动"},
    ],
    "budgets": [
        {"id": "B1", "name": "普服补助", "amount": 5_000_000, "quarter": 0},
        {"id": "B2", "name": "产业专项", "amount": 2_000_000, "quarter": 0},
        {"id": "B3", "name": "续建资金", "amount": 1_000_000, "quarter": 4},
    ],
    "projects": [
        {"id": "P-01", "name": "河西4G（电信）", "kind": "4G站点", "covers": ["G-03"],
         "budget_id": "B1", "cost": 900_000, "annual_maintenance": 36_000,
         "start_q": 0, "end_q": 1, "revenue": 88_000, "proposer": "电信"},
        {"id": "P-02", "name": "河西4G（移动重复）", "kind": "4G站点", "covers": ["G-03"],
         "budget_id": "B1", "cost": 920_000, "annual_maintenance": 37_000,
         "start_q": 0, "end_q": 2, "revenue": 82_000, "proposer": "移动"},
        {"id": "P-03", "name": "产业园5G", "kind": "5G站点", "covers": ["G-02"],
         "budget_id": "B2", "cost": 1_200_000, "annual_maintenance": 60_000,
         "start_q": 1, "end_q": 2, "revenue": 260_000, "proposer": "园区"},
        {"id": "P-04", "name": "南山4G", "kind": "4G站点", "covers": ["G-05"],
         "budget_id": "B1", "cost": 700_000, "annual_maintenance": 28_000,
         "start_q": 0, "end_q": 1, "revenue": 61_000, "proposer": "移动"},
        {"id": "P-05", "name": "高山4G（低收益）", "kind": "4G站点", "covers": ["G-04"],
         "budget_id": "B1", "cost": 950_000, "annual_maintenance": 34_000,
         "start_q": 2, "end_q": 3, "revenue": 12_000, "proposer": "电信"},
        {"id": "P-06", "name": "光纤主干", "kind": "光纤", "covers": ["G-03", "G-05"],
         "budget_id": "B1", "cost": 1_500_000, "annual_maintenance": 45_000,
         "start_q": 0, "end_q": 1, "revenue": 130_000,
         "shared_with": ["广电"], "proposer": "电信"},
        {"id": "P-07", "name": "高山光纤延伸", "kind": "光纤延伸", "covers": ["G-04"],
         "budget_id": "B3", "cost": 800_000, "annual_maintenance": 22_000,
         "start_q": 4, "end_q": 5, "revenue": 18_000, "deps": ["P-06"],
         "proposer": "电信"},
        {"id": "P-10", "name": "抢跑延伸段", "kind": "光纤延伸", "covers": ["G-04"],
         "budget_id": "B1", "cost": 830_000, "annual_maintenance": 23_000,
         "start_q": 0, "end_q": 0, "revenue": 18_000, "deps": ["P-06"],
         "proposer": "乡镇"},
        {"id": "P-11", "name": "城关4G（与存量重复申报）", "kind": "4G站点",
         "covers": ["G-01"], "budget_id": "B1", "cost": 500_000,
         "annual_maintenance": 20_000, "start_q": 0, "end_q": 1,
         "revenue": 150_000, "proposer": "联通"},
    ],
    "commitments": [
        {"id": "C-01", "project_id": "P-06", "party": "电信", "shared_by": "广电",
         "share": 0.4, "signed_q": 0},
        {"id": "C-02", "project_id": "P-03", "party": "移动", "shared_by": "园区",
         "share": 0.3, "signed_q": 1},
    ],
}


def make_store() -> tuple[tempfile.TemporaryDirectory, EventStore]:
    tmp = tempfile.TemporaryDirectory()
    store = EventStore(os.path.join(tmp.name, "ledger.jsonl"))
    return tmp, store


def seeded_store(ledger=None) -> tuple[tempfile.TemporaryDirectory, EventStore]:
    tmp, store = make_store()
    import_ledger(store, ledger if ledger is not None else SAMPLE_LEDGER)
    return tmp, store
