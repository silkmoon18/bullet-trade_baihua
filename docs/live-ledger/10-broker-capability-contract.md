# QMT券商能力合同

## 目的

不同MiniQMT、BigQMT网关和券商柜台返回的字段并不完全一致。StrategyLedger不能因为适配器代码里存在某个字段名，就假定真实环境一定会返回稳定订单号、成交号、备注和费用。S06用`BrokerCapabilityProfile`统一描述这些能力，并由`require_strategy_ledger_v1()`决定是否允许进入后续实盘主链。

能力状态只有三种：

- `SUPPORTED`：已由目标QMT环境探针验证。
- `PROBE_REQUIRED`：代码有查询或传递路径，但目标环境尚未验证。
- `UNSUPPORTED`：当前适配器没有直接提供该能力。

## 当前矩阵

| 能力 | MiniQMT | BigQMT | 说明 |
|---|---|---|---|
| client tag/remark回显 | PROBE_REQUIRED | PROBE_REQUIRED | 下单路径会传递，必须验证订单查询在重启后仍可读回 |
| 稳定broker order ID | PROBE_REQUIRED | PROBE_REQUIRED | 必须能从提交结果或订单查询得到 |
| 原生broker trade ID | PROBE_REQUIRED | PROBE_REQUIRED | 合成ID只用于诊断，不满足成交幂等 |
| trade关联order ID | PROBE_REQUIRED | PROBE_REQUIRED | 缺失时无法安全确定归属和方向 |
| trade直接提供side | UNSUPPORTED | UNSUPPORTED | 当前统一返回不承诺；允许按同一order ID映射 |
| order提供可映射side | PROBE_REQUIRED | PROBE_REQUIRED | trade缺side时必须额外验证该能力，只有order ID关联仍不够 |
| commission/tax字段 | PROBE_REQUIRED | PROBE_REQUIRED | 明确返回0才是零费用；字段缺失不能按0入账 |
| 订单状态 | PROBE_REQUIRED | PROBE_REQUIRED | 需要覆盖部分成交、已成、已撤和拒单 |
| 当前orders/trades查询 | PROBE_REQUIRED | PROBE_REQUIRED | 路径存在，真实返回范围待探针 |
| working orders查询 | PROBE_REQUIRED | PROBE_REQUIRED | 必须在重启后仍能恢复在途单 |
| orders/trades lookback | 未知 | 未知 | `strategy_ledger_v1`最低要求覆盖前一交易日 |

直连 `XtQuantTrader.query_stock_orders/query_stock_trades` 的官方语义是当日委托、
当日成交。本项目启用 StrategyLedger 数据库时，会把 QMT 回调和每次当日查询的
规范化结果写入同一个 SQLite 文件；对账读取“当日实时结果 + 已观察到的本地历史”。
因此，本地持久历史可以替代券商跨日 lookback 要求，但不能补回服务器连续停机并
跨过交易日边界期间从未观察到的回报。活动委托仍只以当日实时查询为准，历史中的
未完成状态不会被误当作仍在挂单。

手续费字段与历史留存是两项独立能力。字段缺失时保持 `*_known=false`，不能因为
本地保存成功就把未知费用改成 0 或宣称费用能力已验证。

因此当前两个静态profile都会被`require_strategy_ledger_v1()`拒绝，这是正确状态；S19在目标QMT模拟环境执行探针并保存证据后，才能把对应项提升为`SUPPORTED`。

## 成交证据规则

`normalize_trade_evidence()`和`normalize_trade_batch()`执行后续成交入账所需的最小规则：

1. 必须有来源标记为`broker`的原生成交号和broker order ID。
2. 必须有证券、正成交数量和正成交价格。
3. side优先取成交记录；缺失时只按broker order ID读取已验证含side的对应订单，不能按证券、数量或价格猜测。
4. commission和tax字段必须真实存在且可解析为非负数，显式`0`有效，字段缺失或无效不能默认成0。
5. 同一原生成交号的完全相同重复记录归并为一条；经济字段冲突则报错。

MiniQMT现有兼容代码在缺少原生成交号时仍生成合成ID用于日志和旧接口，同时新增`trade_id_source='synthetic'`。StrategyLedger合同会拒绝这种记录。MiniQMT与BigQMT规范化结果也新增`commission_known`和`tax_known`，避免把“字段缺失后默认0”误当成真实零费用。

## 后续真实探针

S19至少需要用目标账户完成一次可撤委托和一次小额成交，并跨进程、跨交易日验证：

- 订单备注、broker order ID和最终状态仍可查询；
- 成交号稳定，重复查询不变化，trade能关联原订单；
- 费用字段明确出现，零费用品种也返回明确的0；
- working order及前一交易日orders/trades可查询。

探针失败只表示当前QMT/网关配置不满足合同，不应通过代码猜测或自动补零绕过。
