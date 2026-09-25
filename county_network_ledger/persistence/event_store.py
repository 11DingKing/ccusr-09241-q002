"""哈希链 JSONL 事件存储（持久化适配层）。

每行一条事件信封：

    {"seq":1,"ts":"...","name":"...","payload":{...},"prev_hash":"...","hash":"..."}

``hash = sha256(seq|ts|name|规范JSON(payload)|prev_hash)``，
载入时逐条复算，断链即报 ``LedgerIntegrityError``。
追加写入通过 fcntl 对侧车锁文件加锁，多进程并发不会交错写坏；
应用层的乐观版本校验在同一临界区内完成，避免静默覆盖。
"""
from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
from typing import Any, Iterator, Sequence

from ..application.ports import Clock, UtcClock
from ..domain.errors import LedgerIntegrityError
from ..domain.state import replay, LedgerState

GENESIS = "0" * 64


def canonical_json(obj: Any) -> str:
    """事件载荷的规范序列化：键排序、紧凑分隔、不转义中文。"""
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(seq: int, ts: str, name: str, payload: dict[str, Any], prev_hash: str) -> str:
    h = hashlib.sha256()
    h.update(str(seq).encode("utf-8"))
    h.update(b"|")
    h.update(ts.encode("utf-8"))
    h.update(b"|")
    h.update(name.encode("utf-8"))
    h.update(b"|")
    h.update(canonical_json(payload).encode("utf-8"))
    h.update(b"|")
    h.update(prev_hash.encode("utf-8"))
    return h.hexdigest()


class EventStore:
    """文件型事件存储。``path`` 应位于源码树之外的数据目录。"""

    def __init__(self, path: str, clock: Clock | None = None) -> None:
        self.path = path
        self._clock = clock or UtcClock()
        self._lock_path = path + ".lock"
        parent = os.path.dirname(os.path.abspath(path))
        os.makedirs(parent, exist_ok=True)

    # ---- 读取与校验 ----

    def load_records(self) -> list[dict[str, Any]]:
        if not os.path.exists(self.path):
            return []
        records: list[dict[str, Any]] = []
        with open(self.path, "r", encoding="utf-8") as fh:
            for lineno, line in enumerate(fh, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise LedgerIntegrityError(f"第 {lineno} 行不是合法 JSON：{exc}") from exc
                records.append(rec)
        self._verify_chain(records)
        return records

    @staticmethod
    def _verify_chain(records: Sequence[dict[str, Any]]) -> None:
        prev = GENESIS
        expected_seq = 1
        for rec in records:
            seq = rec.get("seq")
            if seq != expected_seq:
                raise LedgerIntegrityError(
                    f"事件序号不连续：期望 {expected_seq}，实际 {seq!r}"
                )
            want = digest(seq, rec["ts"], rec["name"], rec["payload"], prev)
            if rec.get("hash") != want:
                raise LedgerIntegrityError(f"第 {seq} 条事件哈希校验失败，账本可能被篡改")
            if rec.get("prev_hash") != prev:
                raise LedgerIntegrityError(f"第 {seq} 条事件哈希链断裂")
            prev = want
            expected_seq += 1

    def load_events(self) -> list[dict[str, Any]]:
        return [{"seq": r["seq"], "name": r["name"], "payload": r["payload"]}
                for r in self.load_records()]

    def state(self) -> LedgerState:
        return replay(self.load_events())

    @property
    def version(self) -> int:
        """账本事件数（导入基线为 0）。"""
        return len(self.load_records())

    # ---- 事务化追加 ----

    @contextlib.contextmanager
    def transaction(self) -> Iterator["Transaction"]:
        """开账本锁，在临界区内读状态、做决策、追加事件。"""
        with open(self._lock_path, "w", encoding="utf-8") as lock_fh:
            fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX)
            try:
                records = self.load_records()  # 持锁后重读，避免脏决策
                tx = Transaction(self, records)
                yield tx
                if tx._staged:
                    self._append(records, tx._staged)
            finally:
                fcntl.flock(lock_fh.fileno(), fcntl.LOCK_UN)

    def _append(self, prior_records: Sequence[dict[str, Any]],
                staged: Sequence[tuple[str, dict[str, Any]]]) -> None:
        prev = prior_records[-1]["hash"] if prior_records else GENESIS
        seq = len(prior_records)
        tmp_path = self.path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as out:
            for name, payload in staged:
                seq += 1
                ts = self._clock.now().isoformat()
                h = digest(seq, ts, name, payload, prev)
                rec = {"seq": seq, "ts": ts, "name": name,
                       "payload": payload, "prev_hash": prev, "hash": h}
                out.write(json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n")
                prev = h
            out.flush()
            os.fsync(out.fileno())
        # 原子拼接：先拷已有内容，再替换（账本整体较小，简单可靠）
        if os.path.exists(self.path):
            with open(tmp_path, "r", encoding="utf-8") as inf, \
                    open(self.path, "a", encoding="utf-8") as app:
                app.write(inf.read())
            os.remove(tmp_path)
        else:
            os.replace(tmp_path, self.path)


class Transaction:
    """事务句柄：持锁期间基于开事务时的状态做决策，事件暂存后统一提交。"""

    def __init__(self, store: EventStore, records: Sequence[dict[str, Any]]) -> None:
        self._store = store
        self._staged: list[tuple[str, dict[str, Any]]] = list()
        self.state = replay([{"seq": r["seq"], "name": r["name"], "payload": r["payload"]}
                             for r in records])
        self.record_count = len(records)

    def add(self, name: str, payload: dict[str, Any]) -> None:
        self._staged.append((name, payload))

    def add_all(self, events: Sequence[tuple[str, dict[str, Any]]]) -> None:
        self._staged.extend(events)
