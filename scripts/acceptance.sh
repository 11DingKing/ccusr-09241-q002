#!/usr/bin/env bash
# 验收脚本：导入 JSON 台账 → 本地查询接口 → 方案比较/合并/回退 →
# 并发预算冲突 → 预算缩减 → 冻结与变更单 → 导出确定性报告。
# 用法：scripts/acceptance.sh [工作目录]（默认使用 mktemp 临时目录）
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

WORK="${1:-$(mktemp -d)}"
mkdir -p "$WORK"
LEDGER="$WORK/ledger.jsonl"
CNL=(python3 -m county_network_ledger --ledger "$LEDGER")

step() { printf '\n\033[1m== %s ==\033[0m\n' "$*"; }

step "1. 导入 JSON 台账（运营商/园区/乡镇季度申报汇总）"
"${CNL[@]}" import examples/ledger.json

step "2. 校验账本哈希链"
"${CNL[@]}" verify

step "3. 跨申报方重复建设扫描（空间×时间）"
"${CNL[@]}" duplicates

step "4. 编制两个工作副本方案并横向比较"
LEAN=$("${CNL[@]}" plan-create --name 节约方案 --quarter 2 \
  --projects P-01,P-04,P-06 \
  --note "优先空白村与已签承诺工程；高山村收益仅1.2万/年，本季暂缓" \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["plan_id"])')
FULL=$("${CNL[@]}" plan-create --name 全面方案 --quarter 2 \
  --projects P-01,P-03,P-04,P-05,P-06,P-07 \
  --note "兼顾产业园5G与偏远高山村，接受更高10年运维成本" \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["plan_id"])')
echo "工作副本：节约=$LEAN 全面=$FULL"
"${CNL[@]}" compare "$LEAN,$FULL"

step "5. 试合并后回退（不破坏已签承诺，理由留痕）"
"${CNL[@]}" plan-merge "$LEAN" --source "$FULL" --quarter 2 \
  --note "试并全面方案，评估资金与运维压力"
"${CNL[@]}" plan-rollback "$LEAN" --index 0 --quarter 2 \
  --note "普服补助不足，回退到节约基线，高山村列入下一批"

step "6. 多人同时调整同一预算：旧版本号必须失败（不静默覆盖）"
"${CNL[@]}" budget-adjust --budget B-2026-TB --amount 2000000 --quarter 2 --expected-version 0
if "${CNL[@]}" budget-adjust --budget B-2026-TB --amount 9000000 --quarter 2 --expected-version 0; then
  echo "!! 旧版本号竟被接受，验收失败" >&2; exit 1
else
  echo ">> 旧版本号被正确拒绝（ConcurrentModificationError）"
fi

step "7. 预算缩减场景：200万额度下全面方案冻结被拒（超支）"
if "${CNL[@]}" plan-freeze "$FULL" --quarter 3 --reason "预算缩减后仍试图送审"; then
  echo "!! 超支方案竟被冻结，验收失败" >&2; exit 1
else
  echo ">> 超支冻结被正确拒绝"
fi

step "8. 恢复预算并冻结正式方案"
"${CNL[@]}" budget-adjust --budget B-2026-TB --amount 8000000 --quarter 3 --expected-version 1
"${CNL[@]}" plan-freeze "$FULL" --quarter 3 --reason "专班季度审定：普惠与产业并重，运维纳入年度预算"

step "9. 冻结后直接修改被拒，必须走留痕变更单"
if "${CNL[@]}" plan-set "$FULL" --projects P-01 --quarter 3 --note "尝试绕过变更单"; then
  echo "!! 冻结方案竟被直接修改，验收失败" >&2; exit 1
else
  echo ">> 直接修改被正确拒绝（PlanFrozenError）"
fi
CO=$("${CNL[@]}" change-propose "$FULL" --kind add --projects P-08,P-09 --quarter 4 \
  --reason "增补柳坪村4G与入村光纤，回应人大建议与乡镇申报" --proposer 专班 \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["co_id"])')
"${CNL[@]}" change-approve "$CO" --quarter 4
echo ">> 变更单 $CO 已批准执行"

step "10. 启动本地查询接口并抽查"
PORT=18080
"${CNL[@]}" serve --port $PORT &
SRV=$!
trap 'kill $SRV 2>/dev/null || true' EXIT
sleep 1
curl -fsS "http://127.0.0.1:$PORT/summary"
curl -fsS "http://127.0.0.1:$PORT/plans/$FULL/evaluation" | head -40
curl -fsS "http://127.0.0.1:$PORT/duplicates"
kill $SRV 2>/dev/null || true
trap - EXIT

step "11. 导出确定性报告（两次导出逐字节一致）"
"${CNL[@]}" report --out "$WORK/report-a.json" --quarter 4
"${CNL[@]}" report --out "$WORK/report-b.json" --quarter 4
"${CNL[@]}" report --out "$WORK/report.md" --quarter 4 --plans "$FULL"
cmp "$WORK/report-a.json" "$WORK/report-b.json" \
  && echo ">> 两次 JSON 报告逐字节一致（确定性成立）"
echo "报告文件：$WORK/report-a.json / $WORK/report.md"

step "12. 未覆盖清单与取舍理由抽查"
python3 - "$WORK/report-a.json" <<'PY'
import json, sys
report = json.load(open(sys.argv[1], encoding="utf-8"))
for plan in report["plans"]:
    unc = plan["uncovered"]
    names = "、".join(g["name"] for g in unc["grids"]) or "无"
    print(f"方案 {plan['plan_id']}（{plan['name']}）：未覆盖 {unc['count']} 个，"
          f"空白 {unc['blank_count']} 个 -> {names}")
    for rev in plan["rationale"]["revisions"]:
        if rev["note"]:
            print(f"  取舍理由[第{rev['index']+1}版]：{rev['note']}")
print("变更单台账：", [(c["co_id"], c["status"]) for c in report["change_orders"]])
PY

echo
echo "验收完成。账本：$LEDGER"
