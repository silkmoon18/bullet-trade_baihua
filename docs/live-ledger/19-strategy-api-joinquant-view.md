# 策略API与聚宽真实组合视图

## 结论

L03复用BulletTrade现有TCP协议和token，提供六个动作：`strategy.ensure_account`、`strategy.get_snapshot`、`strategy.submit_targets`、`strategy.get_intent`、`strategy.get_reconciliation`和`strategy.notify_target_buy_plan`。最后一个动作只把JQ/QMT_REMOTE目标买入计划送入飞书队列，不访问券商、不提交订单也不写账本。没有新增HTTP服务、角色系统或第二套鉴权。账本事件保留在服务器SQLite中用于审计，不再暴露当前策略未使用的远程事件接口。

聚宽`BACKTEST`和`JQ`使用原生`context.portfolio`与原生下单接口；其中`JQ`额外发送目标计划通知。`QMT_REMOTE`不改写聚宽模拟账户，而是读取服务器`PortfolioView`、一次提交完整目标权重，并用`record()`记录真实现金、总资产、持仓市值、NAV、收益和费用。

必须准确理解这个边界：聚宽不提供把外部真实成交写回原生模拟账户的公开接口，因此平台内置模拟持仓和内置收益曲线不能变成券商实盘事实。当前实现提供聚宽自定义指标`real_cash`、`real_total`、`real_positions`、`real_nav`、`real_return`和`real_fees`；实盘权威数据仍在StrategyLedger。

## 服务配置

仅配置数据库路径时才启用策略API：

```dotenv
QMT_STRATEGY_LEDGER_DB=E:\dev\Github\bullet-trade_baihua\.data\strategy-ledger.db
QMT_STRATEGY_TRADING_ENABLED=false
QMT_STRATEGY_ENABLED_IDS=good_etf_remote
QMT_STRATEGY_ALLOW_BUYS=true
QMT_STRATEGY_MAX_AGE_SECONDS=300
QMT_STRATEGY_CASH_BUFFER=100
QMT_STRATEGY_MINIMUM_ORDER=0
QMT_STRATEGY_BUY_FEE_BUFFER=5
```

交易开关默认`false`。L04完成目标QMT能力探针、备份和人工验收前不得改为`true`。当前按`client_tag/order_remark`和broker order ID识别本策略委托与成交，因此允许QMT物理账户同时存在人工交易或其他策略资产；`sub_account_id`仍明确不支持。

## 每次调用的真实流程

`ensure_account`先读取QMT可用资金、持仓、订单和成交；首次建立物理现金池并检查真实资金是否足够分配固定1万元，然后立即对账。资金不足时不创建策略账户。

`get_snapshot`和`submit_targets`调用前都会：

1. 重新轮询QMT订单、成交、现金和持仓；
2. 将已知真实fill幂等入账；
3. 忽略不能归属于本策略的外部订单、成交和额外持仓；只有券商现金/持仓不足以覆盖策略归属资产，或本策略订单证据不完整时才持久化`BLOCKED`；
4. 使用聚宽传入的mark，并用QMT行情补齐重启后缺失的持仓/目标mark；
5. 从同一SQLite读事务生成真实组合快照。

`submit_targets`按聚宽调仓key幂等创建一个组合intent，派发当前可执行的卖单或买单，然后再次轮询QMT并返回最新真实快照。部分成交、零成交、拒单和费用只按券商回报更新；期望金额不会直接改写现金或持仓。

## 聚宽运行时

当前helper契约为`v10`。旧helper会因marker/API版本不匹配而失败，避免模式名称或执行语义不一致。

`good_etf_remote`的QMT_REMOTE路径：

1. 初始化调用`ensure_account(INITIAL_CAPITAL)`并要求对账`READY`；
2. 从`PortfolioView`读取真实现金、持仓和总资产；
3. 选股规则不变，把折价权重乘`DEPLOY_RATIO`后一次提交；
4. 后续回调推进同一intent，卖单完成并真实入账后才规划买单；
5. 聚宽重启时恢复服务器中的活跃intent，同一日期调仓key复用已保存权重；
6. 10:30、13:30、14:50风控一次提交完整组合目标，避免一个回调创建多个intent。

生产默认`mirror_jq_orders=False`：聚宽原生订单和模拟持仓不参与QMT_REMOTE决策。

## L03验证

- 1万元开户、资金不足拒绝、真实现金/NAV快照。
- 相同调仓key只派发一次、订单冻结后真实可用现金正确。
- intent/events/reconciliation查询和活跃intent恢复。
- StrategyLedger、helper、good_etf和导出联合回归280项通过。
- flake8、Python语法和`git diff --check`通过。

L03只证明软件闭环存在，不证明用户当前QMT柜台满足成交证据合同。真实放行仍属于L04。
