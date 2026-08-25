# 真实现金校准与策略资金

## 对应最初的1万元流程

S08把聚宽配置的策略初始资金与真实QMT账户现金分开处理：

1. 从QMT账户读取`available_cash`并转换为StrategyLedger货币整数单位。
2. `calibrate_broker_available_cash()`以QMT真实可用现金重算未分配资金池。
3. `ensure_strategy_account(..., initial_capital_units=100_000_000)`申请1万元策略资金。
4. 真实账户可用资金不足时抛出`LedgerInvariantError`，策略账户、资金池和资金流水都不会创建。
5. 足够时在同一事务扣减物理资金池、创建策略账户并写初始`ALLOCATE`流水。

```python
from bullet_trade.server.strategy import SQLiteCapitalService, money_to_units

capital = SQLiteCapitalService(r"E:\dev\Github\bullet-trade_baihua\.data\strategy-ledger.db")
capital.calibrate_broker_available_cash("qmt-main", money_to_units("12500.00"))
result = capital.ensure_strategy_account(
    account_id="good-etf",
    strategy_id="good_etf_remote",
    physical_account_id="qmt-main",
    initial_capital_units=money_to_units("10000.00"),
)
```

`result.created`只在首次分配时为`True`。重启后使用相同配置会返回原账户，不会再次扣1万元；如果聚宽配置改为其他初始资金，会报`CapitalConfigurationError`，必须走显式资金调整。

## 重启和账实校验

策略账户存在后，再次校准不会覆盖任何策略现金、持仓或流水。共享物理账户中的人工交易、其他策略和外部资金变化统一由未分配资金池承担：

```text
未分配可用现金 = QMT真实可用现金 - 所有StrategyLedger策略可用现金
```

只要结果不小于新策略申请的初始资金，就可以创建新的虚拟策略账户。QMT真实可用现金小于已有策略可用现金总和时，才抛出`BrokerCashMismatchError`并停止后续交易；这表示物理账户已经无法覆盖StrategyLedger承诺的策略现金。新增策略申请超过剩余未分配现金时抛出`LedgerInvariantError`，且不会创建账户或资金流水。

## 下单前冻结与释放

买单规划得到最大占用金额后调用`reserve_cash()`；实际成交、撤单或拒单返回后，后续服务按真实结果扣款并调用`release_cash()`释放剩余冻结。两者使用`expected_ledger_version`做CAS并追加账本事件，并在同一事务按`order_id`核对该订单自己的剩余冻结额。同一订单可以追加冻结，但重复或超额释放会被拒绝，不会占用其他订单的冻结资金。

例如策略现金1万元，冻结3000元后可用现金为7000元。若实际只成交2000元，S09会按真实成交价和费用扣除实际金额，再释放未使用冻结；不会按期望3000元直接扣账。

## 显式追加和收回资金（仅管理员修复入口）

按D023首版固定初始资金1万元，运行期间不增减资。`adjust_capital()`仅是管理员修复入口（例如人工纠错后的账务修复），不接入日常运行流程，聚宽侧和执行路径都不会调用它。

`adjust_capital()`只接受`ALLOCATE`和`WITHDRAW`，同时更新物理资金池、策略现金、账本事件和`capital_flows`。`external_ref`必须稳定：相同ref和相同参数只返回当前账户而不重复生效，不同参数复用同一ref会报错。收回金额不能占用策略已冻结现金。一旦发生初始入金之外的capital flow，估值快照会把`performance_ready`置为`False`（见[原子估值与组合快照](14-valuation-snapshot.md)）。

## 当前边界

- S08尚未从QMT adapter自动拉取账户资金；启动校准接入和账实对账属L01，策略API属L03。
- 按成交更新现金/持仓已在S09完成：部分成交、手续费、卖出回款和T+1 lot见[真实成交入账与持仓批次](13-fill-booking.md)。
- 聚宽展示的真实资金、持仓和指标视图在L03接入。在此之前`LIVE`仍保持阻断。
