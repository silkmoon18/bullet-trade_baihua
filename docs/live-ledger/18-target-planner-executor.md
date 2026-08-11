# 目标规划与执行

`SQLiteTargetExecutionService`把聚宽提交的一次目标权重转换为一个持久组合意图。它只读取StrategyLedger真实快照，不读取聚宽模拟账户。

## 调用顺序

1. L01同步QMT并得到READY。
2. S10用最新行情生成`PortfolioSnapshot`。
3. `submit_target_weights(account_id, jq_key, weights, snapshot, marks)`创建或重放组合意图。
4. 如果需要卖出，只创建卖单；等待QMT真实成交、L01入账及新的快照后调用`advance_intent(...)`。
5. 不再需要卖出时，按最新真实可用资金创建买单。
6. server单进程循环调用`dispatch_next(submitter)`；`submitter`内部调用现有MiniQMT/BigQMT adapter的`place_order`。

## 配置

```python
PlannerConfig(
    trading_enabled=False,   # 生产默认关闭
    allow_buys=True,         # False为只卖不买
    lot_size=100,
    cash_buffer_units=money_to_units("100"),
    buy_fee_buffer_units=money_to_units("5"),
    order_wait_timeout_seconds=16,
)
```

同一账户只执行一个活跃意图。working order、T+1不可卖、SUBMIT_UNKNOWN、非ACTIVE账户、非READY对账或过期/版本漂移快照都会阻止新订单。异常提交一律按结果未知处理，不自动重发；下一步必须由L01查询券商订单/成交后恢复或人工处理。
