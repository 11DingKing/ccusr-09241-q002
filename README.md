# 县域通信共建规划账本

沉淀县域网络建设资料，支持覆盖与投资方案协同。

专班每季度汇总运营商、园区和乡镇提交的站点及光纤建设建议。本后端把**网格人口、
现有能力、候选工程、预算批次、共建承诺**纳入同一版本化账本，提供：

- **重复建设识别**：空间维度（同一网格被多方申报、与现有能力重叠）与时间维度
  （跨季度重复申报、现有能力投运后仍被申报）；
- **影响评估**：不同方案对基本覆盖（人口/网格）、重点产业（按产业标签）、长期
  运维成本（默认 10 年 TCO）的影响，以及预算超支与跨期依赖冲突；
- **方案协同**：比较、合并（预算内确定性取舍）、回退，全程不得破坏已签承诺；
- **并发与留痕**：预算批次与方案修订均采用乐观并发（期望版本不符即拒绝静默
  覆盖）；方案冻结后只能通过留痕变更单修订；
- **确定性报告**：导出包含取舍理由与未覆盖清单（含空白网格标记）的报告，同一
  账本状态渲染逐字节一致。

## 工程约定

项目采用 Python 包目录组织服务端代码。领域模型、应用服务、持久化适配和接口层
保持边界清晰；时间、标识生成及外部观测均通过可替换端口接入，便于稳定复现业务
过程。运行数据不得写入源码目录，临时文件和本地配置由 `.gitignore` 排除。

```
county_network_ledger/
├── domain/         # 模型、重复识别、影响评估、方案合并（纯函数，无外部依赖）
├── application/    # 应用服务门面、本地查询接口、确定性报告
├── persistence/    # JSON 文件存储（原子写入，运行数据独立于源码）
├── ports/          # 时钟与标识生成端口（可替换，支持确定性复现）
└── interfaces/     # 命令行接口
```

## 台账格式

台账为单个 JSON 文件（示例见 `examples/ledger_2026.json`）：

| 字段 | 说明 |
| --- | --- |
| `ledger_id` / `name` | 台账标识与名称 |
| `grids` | 网格：`id`、`name`、`population`、`industries`（重点产业标签） |
| `capabilities` | 现有能力：`id`、`kind`、`covers`、`since`（投运季度）、`om_annual` |
| `projects` | 候选工程：`id`、`proposer`、`kind`、`covers`、`capex`、`om_annual`、`declared_quarter`、`planned_quarter`、`depends_on`（跨期依赖） |
| `budgets` | 预算批次：`id`、`name`、`quarter`、`total`（万元，每季度唯一） |
| `commitments` | 共建承诺：`id`、`project`、`partner`、`signed_quarter` |

季度统一 `YYYYQn` 格式；金额单位为万元。导入时校验引用完整性、季度格式、
依赖环等，问题一次性全部报出。

## 命令行用法

数据目录默认为 `.ledger_data`（可用 `--data-dir` 或 `CNL_DATA_DIR` 覆盖）；
设置 `CNL_FIXED_TIME` 可固定留痕时间，便于逐字节复现。

```bash
# 导入台账
python3 -m county_network_ledger import examples/ledger_2026.json --data-dir .ledger_data

# 本地查询接口
python3 -m county_network_ledger query duplicates  --data-dir .ledger_data   # 重复建设
python3 -m county_network_ledger query evaluate    --plan PLAN-0001 --data-dir .ledger_data
python3 -m county_network_ledger query uncovered   --plan PLAN-0001 --data-dir .ledger_data
python3 -m county_network_ledger query compare     --plans PLAN-0001,PLAN-0002 --data-dir .ledger_data
python3 -m county_network_ledger query change-orders --plan PLAN-0001 --data-dir .ledger_data

# 方案管理
python3 -m county_network_ledger plan create --name 二三季度方案 --projects P01,P03,P06,P04,P07 --data-dir .ledger_data
python3 -m county_network_ledger plan update --plan PLAN-0001 --expected-revision 1 --projects P01,P03,P06 --data-dir .ledger_data
python3 -m county_network_ledger plan merge  --plans PLAN-0001,PLAN-0002 --name 合并案 --data-dir .ledger_data
python3 -m county_network_ledger plan rollback --plan PLAN-0001 --to-revision 1 --data-dir .ledger_data
python3 -m county_network_ledger plan freeze --plan PLAN-0001 --data-dir .ledger_data
python3 -m county_network_ledger plan change-order --plan PLAN-0001 --base-revision 1 \
    --remove P07 --reason "三季度预算缩减，暂缓云雾村站点" --author 县信息化专班 --data-dir .ledger_data

# 预算调整（携带期望版本号，防止多人同时调整时静默覆盖）
python3 -m county_network_ledger budget update --batch B2026Q3 --expected-version 1 --total 100 --data-dir .ledger_data

# 导出确定性报告（含取舍理由与未覆盖清单）
python3 -m county_network_ledger report --plan PLAN-0001 --out report.json --data-dir .ledger_data
```

约束要点：

- 任何方案的已选工程必须包含全部已签承诺工程（创建、修订、合并、回退、变更单
  均校验）；
- 存在约束冲突（依赖缺失/时序颠倒、预算超支、缺少预算批次）的方案不能冻结；
- 冻结方案拒绝直接修订与回退，只能通过变更单，变更单必须填写理由与经办人；
- 合并取并集后按“单位投资覆盖人口”从低到高确定性取舍，承诺工程永不舍弃。

## 测试

在项目根目录执行：

```bash
python3 -m unittest discover -s tests -v
```

覆盖空白网格、跨期依赖、预算缩减、并发冲突、承诺保护、报告确定性等场景。

## 编译检查

在项目根目录执行：

```bash
python3 -m compileall -q county_network_ledger tests
```
