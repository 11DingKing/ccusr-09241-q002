"""时钟端口：运行环境时间可替换，保证审计留痕与报告可稳定复现。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Protocol


class Clock(Protocol):
    """返回 ISO-8601 文本时间的时钟协议。"""

    def now_iso(self) -> str: ...


class SystemClock:
    """真实时钟，生产运行使用。"""

    def now_iso(self) -> str:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")


class FixedClock:
    """固定时钟，测试与确定性复现使用。"""

    def __init__(self, value: str = "2026-04-01T09:00:00+00:00") -> None:
        self.value = value

    def now_iso(self) -> str:
        return self.value
