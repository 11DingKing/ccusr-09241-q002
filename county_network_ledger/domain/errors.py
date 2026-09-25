"""领域错误类型：接口层据此向用户返回稳定的错误信息。"""

from __future__ import annotations


class LedgerError(Exception):
    """账本领域全部错误的基类。"""


class NotFoundError(LedgerError):
    """引用的实体不存在。"""


class ValidationError(LedgerError):
    """数据或操作不满足领域约束，problems 为确定性排序的问题清单。"""

    def __init__(self, problems: list[str] | str) -> None:
        if isinstance(problems, str):
            problems = [problems]
        self.problems = list(problems)
        super().__init__("；".join(self.problems))


class StaleVersionError(LedgerError):
    """乐观并发冲突：期望版本与当前版本不一致，已拒绝静默覆盖。"""


class FrozenPlanError(LedgerError):
    """方案已冻结，直接修订被拒绝，应使用留痕变更单。"""


class CommitmentViolationError(LedgerError):
    """操作将破坏已签订的共建承诺。"""


class StoreError(LedgerError):
    """持久化层错误（台账缺失、损坏或冲突）。"""
