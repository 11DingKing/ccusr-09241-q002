"""标识生成端口：基于账本内持久化计数器生成确定性标识。"""

from __future__ import annotations


class SequentialIds:
    """按类别递增的确定性标识生成器，计数器随账本一起持久化。"""

    def __init__(self, counters: dict[str, int]) -> None:
        self._counters = counters

    def next(self, kind: str) -> str:
        value = self._counters.get(kind, 0) + 1
        self._counters[kind] = value
        return f"{kind.upper()}-{value:04d}"
