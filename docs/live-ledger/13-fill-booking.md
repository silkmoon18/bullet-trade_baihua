# 真实成交入账与持仓批次

## 核心原则

聚宽配置的目标金额只负责产生交易意图，StrategyLedger只以QMT返回的真实成交为准：

- 期望买入3000元、实际成交2000元加5元费用，只扣2005元；余单仍工作时保留剩余冻结，撤单后释放。
- 卖出只有真实fill才增加现金；券商返回拒单或没有该持仓而成交量为0时，现金不变。
- 同一broker trade ID或稳定fingerprint重复到达只返回原结果，不重复扣现金或改持仓。

## 服务入口

```python
from bullet_trade.server.strategy import SQLiteFillBookingService

booking = SQLiteFillBookingService(r"E:\bullet-trade-data\strategy-ledger.db")
booking.register_order(strategy_order)
result = booking.book_fill(
    "good-etf",
    broker_fill,
    expected_ledger_version=current_version,
    sellable_from_trade_date=next_trade_date,
)
```

买入必须由调用方传入交易日历计算出的`next_trade_date`，不能简单把自然日加一。卖出会按`acquired_trade_date`、创建时间和lot ID做FIFO，且只能消费`sellable_from_trade_date <= traded_at.date()`的lot。

撤单或拒单使用：

```python
booking.finalize_order(
    "good-etf",
    order_id,
    OrderState.CANCELED,
    expected_ledger_version=current_version,
)
```

买单剩余冻结与订单终态在同一事务释放；卖单零成交终态不改变账户现金。

## 原子更新内容

每个新fill在单一SQLite事务中同时更新：

1. 策略账户现金、订单冻结、ledger version和event sequence；
2. 订单累计成交量与状态；
3. `fills`去重记录；
4. `positions`和`position_lots`；
5. ledger entry与strategy event。

任一写入失败都会全部回滚。买入lot成本包含该fill的佣金和税费，lot时间使用券商真实`traded_at`，因此同日乱序到达的fill仍按成交时间FIFO；卖出净回款扣除佣金和税费，已实现盈亏保存在fill账本事件中。

## 当前边界

- S09负责“已确认真实成交如何入账”，不负责下单规划和发送；执行编排在S13接入。
- QMT持续拉取、broker order ID到内部order ID映射、跨日补扫和账实对账在S11接入。
- 下一交易日必须由实际交易日历提供；当前服务只校验其晚于成交日。
- NAV、收益率与聚宽只读PortfolioView在S10、S15和S16实现。在这些完成前，聚宽仍不能完整展示真实组合指标。
