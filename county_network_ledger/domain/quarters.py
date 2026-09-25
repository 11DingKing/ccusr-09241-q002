"""季度工具：统一 "YYYYQn" 格式的解析、校验与排序键。"""

from __future__ import annotations

import re

_QUARTER_RE = re.compile(r"^(\d{4})Q([1-4])$")


def is_valid_quarter(text: object) -> bool:
    return isinstance(text, str) and _QUARTER_RE.match(text) is not None


def quarter_key(text: str) -> int:
    """返回可比较的季度键，非法格式抛出 ValueError。"""
    match = _QUARTER_RE.match(text)
    if match is None:
        raise ValueError(f"非法季度格式：{text!r}，应为 YYYYQn（如 2026Q2）")
    year, quarter = int(match.group(1)), int(match.group(2))
    return year * 10 + quarter
