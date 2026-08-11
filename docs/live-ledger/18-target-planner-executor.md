# 目标规划与执行

`SQLiteTargetExecutionService`把聚宽提交的一次目标权重转换为一个持久组合意图。它只读取StrategyLedger真实快照，不读取聚宽模拟账户。

## 调用顺序

1. L01同步QMT并得到READY。
2. S10用最新行情生成`PortfolioSnapshot`。
3. `submit_target_weights(account_id, jq_key, weights, snapshot, marks)`创建或重放组合意图。
4. 如果需要卖出，只创建卖单；等待QMT真实成交、L01入账及新的快照后调用`advance_intent(...)`。
5. 不再需要卖出时，按最新真实可用资金创建买单。
6. server单进程循环调用`dispatch_next(submitter)`；`submitter`内部调用现有MiniQMT/BigQMT adapter的`place_order`。
7. 目标权重始终基于组合总资产换算；现金缓冲只在买入可负担性检查时扣除，避免重复缩小目标和误卖未触发风控的持仓。
8. 买入限价默认比行情上浮0.2%，卖出限价默认下浮0.2%；工作订单超过10分钟时，下一次intent推进会请求撤单，待券商确认后按最新行情重算。

## 配置

```python
PlannerConfig(
    trading_enabled=False,   # 生产默认关闭
    allow_buys=True,         # False为只卖不买
    lot_size=100,
    cash_buffer_units=money_to_units("100"),
    buy_fee_buffer_units=money_to_units("5"),
    limit_price_offset_ppm=2_000,
    working_order_timeout=timedelta(minutes=10),
    order_wait_timeout_seconds=16,
)
```

同一账户只执行一个活跃意图。working order、T+1不可卖、SUBMIT_UNKNOWN、非ACTIVE账户、非READY对账或过期/版本漂移快照都会阻止新订单。异常提交一律按结果未知处理，不自动重发；下一步由L01按券商订单号或完整`client_tag`备注恢复，无法唯一认领时才需要人工处理。StrategyLedger订单超过`ORDER_MAX_VOLUME`会明确拒绝，避免旧QMT自动拆单只返回首个订单号造成账务遗漏。
