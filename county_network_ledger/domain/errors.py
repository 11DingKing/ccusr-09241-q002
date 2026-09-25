"""领域错误。"""
from __future__ import annotations


class DomainError(Exception):
    """所有领域规则违反的基类。"""


class ValidationError(DomainError):
    """台账数据或命令参数不合法。"""


class NotFoundError(DomainError):
    """引用的实体不存在。"""


class ConcurrentModificationError(DomainError):
    """乐观锁冲突：调用方持有的版本已过期。

    ``current_version`` 为账本当前版本，调用方应以它为基准重试。
    """

    def __init__(self, resource: str, expected: int, current: int) -> None:
        self.resource = resource
        self.expected = expected
        self.current = current
        super().__init__(
            f"{resource} 已被其他规划人员修改：期望版本 {expected}，当前版本 {current}，"
            "请刷新后基于最新版本重试"
        )


class PlanFrozenError(DomainError):
    """正式方案已冻结，必须经由留痕变更单修订。"""


class CommitmentBreachError(DomainError):
    """操作会破坏已签署的共建承诺。"""


class LedgerIntegrityError(DomainError):
    """事件账本哈希链校验失败，账本可能被篡改或损坏。"""


class RejectedChangeOrderError(DomainError):
    """变更单已被驳回，不能再执行。"""
