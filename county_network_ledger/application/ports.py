"""可替换端口：时钟、标识、外部观测。

业务时间以"季度"为单位并由命令显式传入（保证回放与报告确定性）；
时钟仅用于事件信封的记录时间，测试时可注入固定时钟。
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from typing import Any, Protocol


class Clock(Protocol):
    def now(self) -> datetime:
        """返回当前时区感知时间。"""


class UtcClock:
    """默认 UTC 时钟。"""

    def now(self) -> datetime:
        return datetime.now(timezone.utc)


class FixedClock:
    """固定时钟，供确定性测试与离线复算使用。"""

    def __init__(self, value: datetime | str) -> None:
        if isinstance(value, str):
            value = datetime.fromisoformat(value)
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        self._value = value

    def now(self) -> datetime:
        return self._value


class Observer(Protocol):
    def event(self, name: str, fields: dict[str, Any]) -> None:
        """记录一条结构化观测。"""


class StderrObserver:
    """默认观测：结构化行输出到标准错误，不影响标准输出的确定性。"""

    def event(self, name: str, fields: dict[str, Any]) -> None:
        parts = " ".join(f"{k}={fields[k]}" for k in sorted(fields))
        print(f"[obs] {name} {parts}", file=sys.stderr)


class NullObserver:
    def event(self, name: str, fields: dict[str, Any]) -> None:
        return None
