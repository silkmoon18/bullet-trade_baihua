# 聚宽实盘策略账本改造文档

本目录是 `feat/joinquant-live-ledger` 分支的工程事实来源。实现顺序固定为：先更新文档，再实施一个 slice，随后测试和代码审查；评审通过后才进入下一个 slice。

## 文档索引

1. [当前架构、依赖与状态](00-current-state.md)
2. [完整修改计划](01-modification-plan.md)
3. [架构与工程决策](02-decisions.md)
4. [当前 session](03-session.md)
5. [实施 slices](04-slices.md)
6. [bt_quant迁移清单](05-migration-manifest.md)
7. [聚宽本地开发与兼容矩阵](05-joinquant-development.md)
8. [聚宽校验与导出](06-joinquant-export.md)

## 权威边界

- QMT/券商：物理账户、委托和成交事实来源。
- BulletTrade `StrategyLedger`：策略级现金、冻结、持仓归属、净值与绩效的权威账本。
- 聚宽：数据、信号、调度与真实指标展示；原生模拟账本不是实盘权威来源。
