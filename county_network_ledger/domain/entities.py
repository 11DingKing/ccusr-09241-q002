"""领域实体：网格、现有能力、候选工程、预算批次、共建承诺。

实体均为不可变值对象（frozen dataclass），状态变更通过事件表达，
保证回放与比较结果的确定性。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

# 覆盖等级编码：数值越大能力越强
COVERAGE_LEVELS = {"无": 0, "2G/3G": 1, "4G": 2, "5G": 3, "光纤": 4}
# 站点类工程可贡献的覆盖等级
SITE_LEVEL = {"2G/3G站点": 1, "4G站点": 2, "5G站点": 3}
BASIC_THRESHOLD = 2  # 基本覆盖：4G 及以上
FIBER_LEVEL = 4
MAINTENANCE_QUARTERS = 40  # 运维成本评估窗口：10 年 = 40 个季度


@dataclass(frozen=True)
class Grid:
    """规划网格。坐标使用矩形包围盒 [x0,y0,x1,y1]（单位：公里）。"""

    id: str
    name: str
    population: int
    x0: float
    y0: float
    x1: float
    y1: float
    industry: Optional[str] = None  # 重点产业标识，如 "茶叶加工"
    blank: bool = False  # 是否为基础设施空白网格（导入时申报，也可按覆盖核实）

    @property
    def area(self) -> float:
        return max(0.0, self.x1 - self.x0) * max(0.0, self.y1 - self.y0)


@dataclass(frozen=True)
class ExistingCapability:
    """现有网络能力：某网格内已有的站点/光纤（运营商自建存量）。"""

    id: str
    grid_id: str
    kind: str  # "2G/3G站点" | "4G站点" | "5G站点" | "光纤"
    operator: str


@dataclass(frozen=True)
class Project:
    """候选工程。

    kind:
      - 站点类："2G/3G站点" / "4G站点" / "5G站点"
      - "光纤"：入村光纤段
      - "光纤延伸"：依赖前置光纤的延伸段（跨期依赖由此表达）
    covers：该工程直接服务的网格（光纤沿程覆盖两侧网格时可多个）。
    deps：必须先建成的前置工程 id（跨期依赖）。
    shared_with：承诺共建分担方，如 ["广电"]。
    start_q / end_q：施工窗口（含端点，季度序号，2026Q1=0 起）。
    """

    id: str
    name: str
    kind: str
    covers: tuple[str, ...]
    budget_id: str
    cost: float
    annual_maintenance: float
    start_q: int
    end_q: int
    revenue: float = 0.0  # 预期年收益（元/年），偏远村落通常较低
    deps: tuple[str, ...] = field(default_factory=tuple)
    shared_with: tuple[str, ...] = field(default_factory=tuple)
    proposer: str = ""  # 申报方：运营商/园区/乡镇

    @property
    def level(self) -> int:
        if self.kind == "光纤" or self.kind == "光纤延伸":
            return FIBER_LEVEL
        return SITE_LEVEL.get(self.kind, 0)

    def active_in(self, quarter: int) -> bool:
        """该工程在指定季度是否处于施工窗口内。"""
        return self.start_q <= quarter <= self.end_q


@dataclass(frozen=True)
class BudgetBatch:
    """预算批次：专项/季度切块资金。

    方案选入的工程凡 ``budget_id`` 指向本批次者，其成本合计不得超过
    ``amount``（承诺分担金额按比例折减后计入，见 application 评估）。
    """

    id: str
    name: str
    amount: float
    quarter: int  # 资金到位季度
    # 乐观并发版本：每次修改 +1。import 落账时为 0。
    version: int = 0


@dataclass(frozen=True)
class Commitment:
    """共建承诺：申报方与分担方对某工程签署的正式约定。

    share：分担方承担的工程成本比例（0~1），剩余由县级配套。
    signed_q：签署季度。一旦签署，正式方案不得随意移除该工程，
    移除只能通过变更单，并判定是否违约。
    """

    id: str
    project_id: str
    party: str  # 主投方，如 "电信"
    shared_by: str  # 分担方，如 "广电"
    share: float
    signed_q: int
    editable: bool = True  # False：已纳入冻结方案锁定，不可直接改/删
