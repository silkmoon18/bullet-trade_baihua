# StrategyLedger领域模型与SQLite Schema

## 当前边界

S04交付领域数据结构和可迁移SQLite schema；S05在其上增加事务repository和最小初始资金池划拨。当前仍未接入BulletTrade API，也不会自动读取真实账户、校准1万元、下单或入账成交；这些能力由S06至S15继续实现。

首版假设：个人专用QMT账户、单策略、可信服务器进程、无融资融券。保留真正影响资金正确性的约束，不处理同进程恶意Python代码或共享账户归属。

## 数值和时间

| 类型 | 整数尺度 | 示例 |
|---|---:|---:|
| 现金、手续费、税 | `10_000` | `1元 = 10000 units` |
| 价格 | `1_000_000` | `3.5元 = 3500000 units` |
| 单位净值/收益率内部值 | `1_000_000` | 留给S10/S16使用 |
| 股票/ETF数量 | 整数股 | 不接受小数 |

字符串、整数或`Decimal`输入使用四舍五入（`ROUND_HALF_UP`）转成整数；业务入口拒绝`float`。数据库金额、价格、数量列同时用`typeof(...)='integer'`和范围`CHECK`约束。

时间戳必须带时区，领域对象统一转换为`Asia/Shanghai`固定`UTC+08:00`。订单保留`trading_day`；lot同时保存买入交易日和实际可卖交易日，后者由后续交易日历计算，不能简单假设自然日加一。

## 表结构

迁移v1建立账本核心：

- `physical_accounts`：QMT物理账户标识和总状态。
- `cash_pools`：物理账户尚未分配给策略的现金。
- `strategy_accounts`：策略初始资本、现金、冻结、版本和事件序号。
- `ledger_entries`：现金/冻结变化的append-only记录。
- `positions`：策略证券汇总持仓、可卖数量和平均成本。
- `position_lots`：成交批次、剩余数量、买入与可卖交易日。

迁移v2建立执行与恢复骨架：

- `portfolio_intents`：一次完整目标组合及幂等键。
- `strategy_orders`：内部订单、client tag、券商订单号、数量和状态。
- `fills`：券商成交号或稳定fingerprint去重所需字段。
- `strategy_events`：按策略单调递增的事件流。
- `outbox`：后续S07用于事务内创建外部提交任务。
- `reconciliation_runs`：对账时间、状态和差异摘要。
- `capital_flows`：显式资金流扩展钩子。`corporate_actions`是旧schema兼容表，首版业务不读写。

迁移v3为`ledger_entries`和`strategy_events`增加禁止UPDATE/DELETE的触发器，业务修复只能追加新事件。

迁移v4新增`strategy_operations`，并为`outbox`增加一对一`operation_id`：同一策略、endpoint和幂等键只能创建一个operation；请求hash、client tag、状态和最终响应持久保存。

公司行动业务不在首版范围，但不为裁剪空表增加破坏性迁移。当前`LATEST_SCHEMA_VERSION=4`。

`outbox`的`lease_owner`/`lease_until`列已弃用：outbox改为单进程认领后不再有lease概念，两列恒为NULL；保留列只是为了避免对既有数据库做破坏性表重建，新代码不得再读写它们。

S04只保证schema和静态约束；事件追加、CAS、资金划拨、成交入账和对账业务不在本slice中伪实现。

## 迁移接口

```python
from bullet_trade.server.strategy import migrate_database

migrate_database(r"E:\bullet-trade-data\strategy-ledger.db")
```

连接默认启用：

- `foreign_keys=ON`
- `journal_mode=WAL`（文件数据库）
- `synchronous=FULL`
- `busy_timeout=5000ms`

迁移版本必须从1连续递增，每个版本独立使用`BEGIN IMMEDIATE`事务。`schema_migrations`保存版本、名称和SQL内容SHA-256，且必须与`PRAGMA user_version`一致。重复执行是no-op；数据库版本高于程序、历史名称/指纹不匹配、双版本源分叉或请求向下迁移都会拒绝。已经应用的迁移文本不可修改，schema变化必须追加新版本。

## 升级和回滚

当前只提供向前迁移，不提供破坏性down migration。生产升级流程：

1. 停止服务和聚宽策略，确保没有写入者。
2. 对WAL执行checkpoint并备份数据库主文件；自动备份工具在S17实现前由运维流程负责。
3. 使用目标版本代码运行迁移并检查`schema_migrations`和`PRAGMA user_version`。
4. 启动服务后先执行恢复、成交重扫和对账；未READY不得启用下单。
5. 单个迁移SQL失败会事务回滚；若迁移成功但新版本业务验证失败，停止服务并恢复升级前备份，不执行down migration。

数据库文件、WAL、备份和运行日志必须位于仓库外的运行目录，不能提交Git。
