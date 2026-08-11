# 原子估值与组合快照

## 用途

S10把StrategyLedger的真实现金、真实持仓和带时间的行情mark合成为单一只读快照。它是后续聚宽`PortfolioView`、`record()`指标和目标持仓规划的共同数据源，避免不同接口分别读取现金和持仓造成成交前后状态混合。

## 基本调用

```python
from datetime import timedelta
from bullet_trade.server.strategy import MarketMark, SQLiteValuationService

valuation = SQLiteValuationService(r"E:\bullet-trade-data\strategy-ledger.db")
snapshot = valuation.create_snapshot(
    account_id="good-etf",
    marks={
        "510050.XSHG": MarketMark(
            security="510050.XSHG",
            price_units=price_to_units("2.500"),
            as_of=current_time,
            source="qmt",
        )
    },
    as_of=current_time,
    max_mark_age=timedelta(seconds=30),
)
```

返回内容包括：

- `cash_units`、`reserved_cash_units`、`available_cash_units`；
- 每只证券的数量、可卖数量、平均成本、mark、市值、剩余成本和未实现盈亏；
- `positions_value_units`、`total_assets_units`和`net_capital_units`；
- `realized_pnl_units`、`unrealized_pnl_units`、`total_pnl_units`和`fees_units`；
- `nav_units`、`ledger_version`和确定性的`snapshot_version`。

## 行情可用条件

有持仓的证券必须全部提供mark。以下情况抛出`ValuationReadinessError`，不能把快照用于展示或新调仓：

- 缺少持仓证券mark；
- `as_of - mark.as_of`超过`max_mark_age`；
- mark时间晚于本次快照时间；
- mark键与mark自身证券不一致。

无持仓的纯现金组合不需要mark。

## 盈亏和成本

总资产始终为现金加持仓市值；总盈亏为总资产减净投入资金，并必须与已实现加未实现盈亏一致。

lot成本不再依赖舍入后的每股成本来做结转，而是使用原始买入fill的成交总额与费用。部分卖出时用“卖出前剩余成本减卖出后剩余成本”作为本次成本，保证佣金无法按股数整除时，整笔lot最终仍能精确结转。

快照的`sellable_qty`也从同一读事务中的lot按快照交易日重新汇总，不沿用买入当天可能仍为0的物化字段；因此跨到T+1后无需先写数据库，聚宽视图和规划器即可得到正确可卖数量。传入的marks在入口复制一次，校验、估值和版本生成始终使用同一批行情证据。

## NAV边界

首版运行场景是启动时固定分配1万元且运行中不增减资（D023），此时`performance_ready=True`，`nav_units`可作为基础组合净值。

严格的份额申购赎回净值从未实现，L00已明确不再建设该支持；`performance_ready`标记保留：如果管理员修复入口产生初始入金之外的`ALLOCATE/WITHDRAW`资金流动，资产和盈亏快照仍然正确，但`performance_ready=False`。聚宽绩效视图（L03）必须检查该字段，不能把简单资产/净投入比率伪装成严格份额净值。
