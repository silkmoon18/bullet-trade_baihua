# 当前状态与使用指南

> **结论：L01至L05的仓库代码已实现，但当前分支仍不能直接用于真实资金。** 真实QMT能力证明、聚宽JQ/QMT模拟和小额人工验收只能在用户目标环境完成；服务端交易开关默认关闭。

## 1. 现在已经具备什么

- `bt_quant`的有效策略内容已迁入统一BulletTrade仓库，旧仓库保留为迁移来源。
- 本地可通过`jqdata.pyi`、helper类型声明和严格mypy/pyright配置获得代码提示及静态检查。
- `good_etf.py`是仓库内唯一受控策略源码；导出器原样复制策略、helper和示例profile，并生成确定性manifest。
- 私有profile只读校验不会执行、复制、hash或输出其中的host/token等值。
- BACKTEST使用聚宽历史撮合；JQ使用聚宽模拟账户和原生订单，同时发送目标计划通知；QMT_REMOTE使用服务器真实PortfolioView和一次组合目标。
- 已具备真实券商可用现金校准、初始1万元单次分配、按订单冻结/释放和显式增减资的事务服务。
- 已具备按真实买卖成交更新现金、订单冻结、持仓lot、费用和已实现盈亏的事务服务，重复fill不会重复入账。
- 已具备基于新鲜行情的现金、持仓市值、总资产、NAV、费用和盈亏原子快照，可作为后续聚宽组合视图数据源。
- MiniQMT包装账户快照已统一解包；提交响应未知的订单可按完整`client_tag`自动认领。
- 目标权重按总资产计算，现金缓冲只在实际买入检查时执行；QMT_REMOTE每次调用明确携带买卖两侧的执行类型、追单和改价策略。GoodETF调仓卖出使用交易所市价IOC，买入使用0.2%条件边界限价。
- 直连xtquant时，QMT tick先在本地账本判断价格条件，命中后才查询真实账户并下单；活动意图结束后自动退订不再需要的标的。订单、成交、错误和断连由QMT原生回调推进，不增加固定1秒轮询。BigQMT HTTP gateway尚未桥接这些原生回调，不能把该适配器视为同等完成。

## 2. 还缺什么

下列外部验证与部署能力尚未完成，因此不能宣称满足实盘要求：

1. 在用户实际MiniQMT/BigQMT上完成订单备注、稳定order/trade ID、费用、状态和跨日查询能力探针。
2. 聚宽JQ、QMT模拟和小额实盘的人工运行证据；真实资金必须再次明确批准。

完整依赖顺序见[实施 slices](04-slices.md)。只有仓库修复完成且真实交易日人工验收逐级通过，才能启用真实资金。

## 3. 当前推荐用法

### 本地开发

按[聚宽本地开发与兼容矩阵](05-joinquant-development.md)建立专用环境。编辑：

```text
strategies/joinquant/good_etf.py
```

不要维护第二份聚宽策略源码，也不要把本地profile、导出目录或凭据提交到Git。

### 聚宽回测

同一份策略自动识别回测，不再切换字符串MODE：

```python
VALIDATE_REMOTE_DURING_BACKTEST = True
STRATEGY_ID = 'good_etf'
```

v9 helper在所有模式均须上传。远程预检为`True`时还必须上传私有配置；它只读取真实快照，历史回测仍使用聚宽原生订单。离线回测可临时设为`False`，此时helper不读取配置、不连接服务器。

先运行校验：

```powershell
python -X utf8 scripts/export_joinquant.py --validate-only
```

再导出到一个尚不存在的目录：

```powershell
python -X utf8 scripts/export_joinquant.py --output E:\temp\good_etf_joinquant
```

按[聚宽校验与导出](06-joinquant-export.md)上传产物。上传后不得手工修改；否则聚宽文件已不再对应manifest和已审查源码。

### JQ

在私有`jq_runtime_config.py`的`STRATEGIES["good_etf"]`中设置`{"profile": "qmt-main", "mode": "JQ"}`后，聚宽模拟交易会正常调用聚宽原生下单、撤单和模拟撮合，不叠加QMT_REMOTE的0.2%价格边界，由聚宽维护资金、持仓和指标。产生新增买入目标时还会通过BulletTrade发送目标计划卡片；通知接口不写StrategyLedger、不受交易开关影响，也绝不提交QMT目标。缺少策略键时使用`DEFAULT_PROFILE`并默认`JQ`。

### QMT_REMOTE

把私有配置中的`STRATEGIES["good_etf"]["mode"]`改为`"QMT_REMOTE"`并冷启动后，只有聚宽模拟交易会进入远程执行；回测仍固定BACKTEST。人工验收前保持`QMT_STRATEGY_TRADING_ENABLED=false`。必须上传v9 helper和私有配置；启动时真实资金不足、能力未证明、账实差异或对账不新鲜都会失败关闭。完整操作见[本机部署runbook](20-local-deployment-runbook.md)。

`good_etf.py`中的`set_order_cost`只用于BACKTEST/JQ模拟撮合。QMT_REMOTE在下单前仅按服务器的保守费用缓冲预留现金，成交后只接受QMT/券商明确返回的实际费用，并通过StrategyLedger的`fees`和聚宽`real_fees`指标展示；不得用聚宽模拟佣金覆盖真实费用。迅投官方标准股票`XtTrade`结构没有佣金字段，部分柜台或扩展版本可能补充`commission_fee`、`commission`或`used_commission`，因此必须通过目标账户的`query_data(..., data_type='deal')`或小额成交证明费用字段可用。

### 每次调用的执行参数

模式只决定“在哪个账户执行”，订单语义由每次`submit_targets`调用的不可变`ExecutionRequest`决定：

| 执行类型 | 含义 |
|---|---|
| `LimitExecution` | 立即按参考价计算的限价提交 |
| `ConditionalLimitExecution` | 对手价进入固定允许边界后才提交限价；GoodETF调仓买入和止盈使用此类型 |
| `MarketExecution` | 使用QMT股票市价类型；GoodETF调仓卖出和止损使用此类型 |
| `MarketableLimitExecution` | 立即提交带保护边界的积极限价，不伪装成市价 |

`ExecutionRequest.style`是默认及买侧类型，`sell_style`可为同一个组合目标单独指定卖侧类型；网络执行契约为schema v2，服务器仍可读取历史schema v1意图。`FollowUpPolicy.NONE`表示订单终态后不补剩余量；`UNTIL_FILLED_TODAY`表示只在提交当日继续处理剩余目标。`KEEP_ORIGINAL`固定使用最初聚宽参考价；`RECOMPUTE`在后续补单时使用最新QMT行情重新计算。所有类型均通过同一个StrategyLedger接口，不是GoodETF专用服务器分支。

GoodETF当前逐类委托如下：

| 场景 | BACKTEST/JQ | QMT_REMOTE |
|---|---|---|
| 09:30清理非目标持仓/减仓 | 聚宽`order_target(code, 0)`或`order_target_value(...)`，默认撮合，策略不做订单级追单 | 沪市五档即时成交剩余撤销、深市五档即时成交剩余撤销；终态回报后继续补剩余目标，卖出完成后才规划买入 |
| 09:30增持目标ETF | 聚宽`order_target_value(code, target_value)`，不传限价，策略不做订单级追单 | 聚宽参考价上方0.2%条件限价，`UNTIL_FILLED_TODAY + KEEP_ORIGINAL` |
| 5%止损 | 现价严格低于成本95%时调用聚宽`order_target(code, 0)`；未成交且下次风控仍满足条件时再次调用 | 无最低价格阈值的QMT市价IOC，终态回报后在当日继续处理剩余清仓目标 |
| 10%止盈 | 现价严格高于成本110%时调用聚宽`order_target(code, 0)`；未成交且下次风控仍满足条件时再次调用 | 以触发时参考价下方0.2%为固定最低卖价；买一价达到边界才挂限价，`UNTIL_FILLED_TODAY + KEEP_ORIGINAL` |

这里的“追踪至当日完成”不是定时撤单改价：服务器不会为了追价反复撤销仍有效的同价委托。只有上一笔委托已进入成交、撤销或拒绝等终态后仍有剩余目标，才继续处理；市价IOC直接再次提交，条件限价则重新等待原始价格边界。跨交易日自动取消未完成目标。代码中的1.5%仅用于市价单的账本估值/资金预留字段，不是QMT市价成交边界。若同一轮同时触发止损和止盈，为优先风险退出，该批清仓目标统一使用止损的市价执行方式。

若风控触发时普通调仓仍活动，服务器先持久化取消请求：等待价格但尚未挂单的意图可立即结束；已有在途订单时先撤单并等待QMT确认，确认前不会提交第二个重叠目标，也不会在回调后恢复原调仓。

## 4. good_etf与原策略的关系

选股和风控主逻辑保持：ETF池、港股关键词过滤、前一日成交额区间、单位净值、停牌/涨停过滤、折价排序、前三名、按折价绝对值分配，以及5%止损/10%止盈。

执行与配置并非原样保留，修改是有意的：

- 删除策略内硬编码host、token、Webhook、账户定位和模式变量；模式由私有配置按`strategy_id`选择。
- 删除旧同步追单扩展；持久幂等已在S07完成，异步执行状态机由L02实现。
- JQ走聚宽模拟账户并发送目标计划通知，但不碰QMT；QMT_REMOTE通过StrategyLedger执行，人工验收前服务端交易开关保持关闭。
- 组合目标由`available_cash * weight`改为`total_value * DEPLOY_RATIO * normalized_weight`，避免把目标仓位误当成本轮新增买入额并在每轮缩小已有持仓。
- QMT_REMOTE尾盘读取真实PortfolioView并通过自定义`record()`指标展示；聚宽内置模拟账户和内置收益曲线仍不代表真实券商账户。

因此可以说“策略思想和选股规则基本一致”，不能说“执行、资金和持仓语义与原版完全一样”。

## 5. 仓库与上游更新

推荐远端布局：

```text
origin   -> 个人GitHub fork（可推送）
upstream -> BulletTrade/bullet-trade（只拉取，push URL为DISABLED）
```

同步上游时先保持工作树clean，然后：

```powershell
git fetch upstream --tags
git switch feat/joinquant-live-ledger
git merge upstream/main
```

发生冲突时不得机械覆盖策略、helper、schema、迁移或放行文档；解决后必须重跑当前slice及其依赖测试并重新审查。若希望线性历史，可在未与他人共享分支时使用rebase，但不要对已共享分支强制推送。

## 6. 权威事实来源

- 当前阶段和恢复点：[当前 session](03-session.md)
- 完整架构和实盘缺口：[当前架构、依赖与状态](00-current-state.md)
- 不可变设计决策：[架构与工程决策](02-decisions.md)
- 逐slice验收条件：[实施 slices](04-slices.md)
- 本地编辑/类型检查：[聚宽本地开发与兼容矩阵](05-joinquant-development.md)
- 聚宽导出：[聚宽校验与导出](06-joinquant-export.md)

若本文与提交后的实现不一致，以代码、测试和最新`03-session.md`共同核对，不应仅凭旧聊天记录放行。
