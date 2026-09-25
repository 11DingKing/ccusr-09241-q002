"""JSON 文件存储：账本状态的原子读写，运行数据与源码目录分离。"""

from __future__ import annotations

import json
import os
from pathlib import Path

from ..domain.errors import StoreError
from ..domain.models import LedgerState


class JsonStore:
    """把账本状态持久化为数据目录下的单个 JSON 文件。"""

    def __init__(self, data_dir: str | os.PathLike) -> None:
        self.data_dir = Path(data_dir)
        self.path = self.data_dir / "ledger.json"

    def exists(self) -> bool:
        return self.path.exists()

    def load(self) -> LedgerState:
        if not self.path.exists():
            raise StoreError(f"台账不存在：{self.path}，请先执行 import")
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise StoreError(f"台账文件损坏：{self.path}（{exc}）") from exc
        try:
            return LedgerState.from_dict(raw)
        except (KeyError, TypeError, ValueError) as exc:
            raise StoreError(f"台账文件结构不完整：{self.path}（{exc}）") from exc

    def save(self, state: LedgerState) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        tmp_path = self.path.with_suffix(".json.tmp")
        payload = json.dumps(state.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        try:
            tmp_path.write_text(payload, encoding="utf-8")
            os.replace(tmp_path, self.path)
        except OSError as exc:
            raise StoreError(f"台账写入失败：{self.path}（{exc}）") from exc
