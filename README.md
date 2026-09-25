# 县域通信共建规划账本

县域信息化专班的站点与光纤建设协同规划后端：把**网格人口、现有能力、候选工程、
预算批次、共建承诺**纳入同一版本化事件账本，识别空间与时间上的重复建设，
计算不同方案对**基本覆盖、重点产业、长期运维成本**的影响，支持规划人员在不破坏
已签承诺的前提下**比较、合并、回退**方案；多人同时调整同一预算时以乐观版本号
拒绝静默覆盖；正式方案冻结后只能通过**留痕变更单**修订。

## 快速开始（验收流程）

```bash
# 1. 导入一组 JSON 台账（全有或全无，可重复执行，幂等）
python3 -m county_network_ledger --ledger var/ledger.jsonl import examples/ledger.json

# 2. 跨申报方重复建设扫描（空间 × 时间）
python3 -m county_network_ledger --ledger var/ledger.jsonl duplicates

# 3. 启动本地只读查询接口
python3 -m county_network_ledger --ledger var/ledger.jsonl serve --port 8080
curl -s http://127.0.0.1:8080/summary
curl -s http://127.0.0.1:8080/duplicates

# 4. 编制/比较/合并/回退方案，冻结后走变更单，导出确定性报告
python3 -m county_network_ledger --ledger var/ledger.jsonl plan-create \
    --name 全面方案 --quarter 2 --projects P-01,P-03,P-04,P-05,P-06,P-07 \
    --note "兼顾产业园5G与偏远高山村，接受更高10年运维成本"
python3 -m county_network_ledger --ledger var/ledger.jsonl compare FA-0001
python3 -m county_network_ledger --ledger var/ledger.jsonl plan-freeze FA-0001 \
    --quarter 3 --reason "专班季度审定"
python3 -m county_network_ledger --ledger var/ledger.jsonl report \
    --out var/report.json --quarter 4
python3 -m county_network_ledger --ledger var/ledger.jsonl report \
    --out var/report.md --quarter 4
```

一条命令跑通全部验收场景（含空白网格、跨期依赖、预算缩减、并发冲突、
冻结后变更单、确定性报告）：

```bash
bash scripts/acceptance.sh
```

## 命令一览

| 命令 | 说明 |
| --- | --- |
| `import <file>` | 导入 JSON 台账（校验通过才落账，同 id 幂等） |
| `verify` | 校验事件账本哈希链 |
| `summary` / `plans` / `duplicates` | 账本汇总 / 方案列表 / 跨申报方查重 |
| `evaluate <plan|ids> [--by-ids]` | 评估方案或一组工程的覆盖/产业/成本/预算/依赖 |
| `compare <ids>` | 多方案横向比较 |
| `suggest <plan>` | 针对未覆盖网格的补建建议（按每万元新增覆盖人口排序） |
| `budget-adjust` | 调整预算批次，`--expected-version` 乐观并发，冲突即拒绝 |
| `plan-create / plan-set / plan-merge / plan-rollback / plan-discard` | 工作副本方案全生命周期，`--note` 取舍理由强制留痕 |
| `plan-freeze` | 冻结正式方案（校验预算/依赖/承诺/双重重复） |
| `change-propose / change-approve / change-reject` | 冻结后的留痕变更单 |
| `report --out <.json|.md>` | 导出确定性报告（含取舍理由与未覆盖清单） |
| `serve [--port]` | 本地只读 HTTP 查询接口 |

所有写命令必须显式传入业务季度 `--quarter`（整数，2026Q1=0 起）与理由参数；
账本路径由 `--ledger` 或环境变量 `CNL_LEDGER_PATH` 指定，默认 `./var/ledger.jsonl`。

## 领域规则要点

- **版本化账本**：全部状态由哈希链 JSONL 事件（`persistence/event_store.py`）
  回放得到；追加写入经 fcntl 文件锁串行化，断链/篡改载入即报错。
- **重复建设**：同类工程（站点同等级、光纤段）服务同一网格——施工窗口重叠为
  "空间+时间双重重复"，错期为"空间重复"；站点跨等级窗口重叠仅提示共址协调；
  站点与光纤互为配套不判重；候选工程与存量能力同等级覆盖亦判"空间重复"。
- **覆盖评估**：网格达到 4G 及以上为基本覆盖；光纤/光纤延伸提供光纤级覆盖；
  重点产业网格单独统计覆盖人口；空白网格（`blank`）在未覆盖清单中单列。
- **成本与预算**：共建承诺按分担比例折减县级配套占用；运维按 10 年（40 季度）
  窗口合计；方案可行性 = 无预算超支 且 无跨期依赖违例（前置工程须先完工）。
- **并发控制**：预算批次携带版本号，`budget-adjust` 在账本锁内复核版本，
  过期即抛 `ConcurrentModificationError`，绝不静默覆盖。
- **承诺保护**：已签共建承诺的工程必须纳入正式方案才能冻结；冻结后承诺锁定，
  移除只能经变更单并显式确认，违约在方案修订与变更单上永久留痕。
- **冻结与变更单**：冻结校验预算/依赖/承诺/双重重复；冻结后 `plan-set`、
  `plan-merge`、`plan-rollback` 一律拒绝，只能 `change-propose` →
  `change-approve`（批准时重新评估可行性）。
- **确定性报告**：同一账本状态导出逐字节一致的 JSON/Markdown，报告锚定
  账本事件数与 tip 哈希；未覆盖清单附每个网格的可补建候选工程及成本/收益。

## 工程约定

项目采用 Python 包目录组织服务端代码，仅依赖标准库（Python ≥ 3.10）：

```
county_network_ledger/
  domain/        实体、事件、回放状态、纯分析逻辑（无 IO）
  application/   端口（时钟/观测）、导入、规划服务、报告
  persistence/   哈希链 JSONL 事件存储（fcntl 锁）
  interfaces/    命令行与本地 HTTP 查询接口
examples/        示例台账（含重复申报、空白网格、跨期依赖、低收益偏远村）
scripts/         验收脚本
tests/           单元与端到端测试
```

领域模型、应用服务、持久化适配和接口层保持边界清晰；时间、标识生成及外部观测
均通过可替换端口接入，便于稳定复现业务过程。运行数据不得写入源码目录，
`var/` 等临时文件由 `.gitignore` 排除。

## 测试

在项目根目录执行：

```bash
python3 -m unittest discover -s tests -v
```

## 编译检查

在项目根目录执行：

```bash
python3 -m compileall -q county_network_ledger tests
```
