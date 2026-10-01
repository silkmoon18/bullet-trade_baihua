# 聚宽策略工作区

本目录保存可复制到聚宽策略编辑器的策略源码：原`good_etf.py`在09:30选股并下单；`good_etf_open_0925.py`保留历史文件名，现为09:26开盘价选股/跌停价清仓、09:30起确认清仓后买入。两者都要求API23单文件helper，选股、排序和权重公式一致，但价格数据边界和执行时序不同。

另有[`etf_reversal.py`](etf_reversal.py)，使用昨日信号、趋势过滤、每5交易日轮换，依赖同一单文件helper API23。其参考来源、规则、费用及延迟实验步骤见[`etf_reversal.md`](etf_reversal.md)，尚未取得聚宽行情回测业绩。

导出新策略使用`python scripts/export_joinquant.py --strategy etf_reversal --output dist/joinquant-etf-reversal`；不指定`--strategy`时仍导出GoodETF。

## 迁移映射

| bt_quant内容 | 统一仓库位置/处理 |
|---|---|
| `jq_platform/good_etf.py` | `strategies/joinquant/good_etf.py`，已移除token、IP和Webhook |
| `jq_platform/bullet_trade_jq_remote_helper.py` | 不复制；统一使用上游 `helpers/bullet_trade_jq_remote_helper.py` |
| `jq_platform/jq_remote_strategy_example.py` | 不复制；上游已有 `helpers/jq_remote_strategy_example.py` |
| `jq_platform/README.md` | 由 `docs/live-ledger/` 和本文件替代 |
| `feishu_notifier.py`、`log.py` | 不复制；交易通知卡片由服务器统一发送，策略事件写聚宽日志 |
| `runtime/`、`logs/`、`__pycache__/` | 运行产物，不导入Git |
| `backtest_results/` | 历史产物保留在旧仓库检查点，不作为源码导入 |
| `main.py` | IDE模板，无有效业务逻辑，不导入 |

## 当前使用限制

`good_etf.py`已经不再保存host、token、profile、Webhook或账户开关，也不再包含TCP、QMT回调、意图恢复、模式解析、通知异常处理、账户下单分支、下单类型实现、平台参数设置或调度注册。策略文件只保留最薄的聚宽入口和运行时安装声明，以及ETF选股、目标权重、止盈止损阈值等决策代码。`JoinQuantRuntime`按配置分别驱动JQ和QMT账户；每个账户的目标金额均按其自己的`组合总资产 × DEPLOY_RATIO × 归一化权重`计算。详细边界以仓库根目录[`AGENTS.md`](../../AGENTS.md)为准。

当前账户组合的边界是：

- `BACKTEST`：始终使用聚宽原生回测接口；helper必须与策略一起上传，关闭远程预检时不读取私有profile；
- 仅JQ：正常调用聚宽原生下单、撤单和模拟撮合，不叠加QMT的0.2%价格边界，由聚宽维护资金、持仓和指标；维持现有JQ目标买入计划通知；
- 仅QMT：使用StrategyLedger真实组合与目标接口并阻断聚宽原生交易函数；
- JQ+QMT：同一组选股权重分别按两个账户自己的资金和持仓执行，止盈止损也分别使用各自成本；QMT开启时只发送QMT计划、订单和成交通知，不再重复发送JQ计划卡片。

QMT调仓先以沪深市价IOC卖出，收到终态回报后再按0.2%条件限价买入；买入阶段各标的独立等待和成交，不会因另一标的部分成交而全部阻塞。未完成的当日目标由常驻服务器在次日00:00主动撤单并闭环。止损使用市价IOC，止盈继续使用0.2%条件限价。服务端交易总开关仍独立控制是否实际下QMT订单。

helper在09:20触发QMT盘前账户对账，提前恢复旧目标、预订阅候选及已有持仓行情。实际选股/发单时间以策略顶部参数为准：原GoodETF为09:30；开盘价变体为09:26清仓、09:30起买入。服务器提交时取得新券商快照；首笔发单在2秒内且没有新的账户事件或对账时复用它，否则重查，后续每笔仍重新核对。BigQMT查询同批交给bridge；只有QMT买入使用参考价上浮0.2%的固定限价。

开盘价变体经用户明确授权，使用helper的通用`prepare_sell_then_buy`/`advance_sell_then_buy`：清空全部旧归属持仓（含再次入选的标的），两个账户各自确认清仓后才买入。JQ买入仍是原生市价单、仅提交一次，没有分钟买入补单；QMT的订单状态和余量继续由服务器管理。若09:30未卖完，后续分钟继续确认，不提前买；重启恢复不重新选股，旧日计划不继续买入。详情见[执行指南](../../docs/live-ledger/07-status-and-usage.md#开盘价选股0926清仓--0930起买入变体)。

当前仓库代码具备回测、JQ模拟、QMT远程和JQ+QMT并行链路。真实资金启用前仍必须在目标QMT环境完成模拟与小额人工验收，并由用户明确放行。

## 下单链路提速（helper API23）

盘前helper保存候选代码及名称，QMT服务器预订阅候选与已有持仓的行情。选股/下单回调前一分钟预热RPC连接，默认09:29；独立09:26变体则在09:25、09:29预热。正常请求复用同一经过认证的连接，空闲超过90秒、身份变化或异常后丢弃连接；可能已发送的写请求不自动重发。预热只准备连接和行情订阅，执行时仍按原规则取得新券商快照并检查行情时效。

普通GoodETF的成功决策日志缓存在helper中，所有已启用账户的委托接口调用完成后才发送计划通知和输出日志。JQ仍按原生接口调用顺序执行。冻结权重、参考价、JQ资金基数及QMT价格边界沿用已有延迟实验语义。独立清仓买入变体保留其明确授权的阶段流程。

日志`调仓耗时`及`g.bt_last_rebalance_timing`包含决策耗时、执行开始至首个JQ接口调用/QMT目标提交与接受的耗时，以及通知、日志和连接次数。JQ接口调用与QMT接受目标均不代表已成交，实际服务器发单需对照原有执行日志。先保持两个时间均为09:30对比性能，再仅改下单时间做延迟实验；代码提速的收益影响尚未取得聚宽/QMT实测。

使用时同时更新策略与研究根目录helper（API23）；候选行情预订阅还需更新服务器的`strategy.prepare_session`实现。本次只生成本地导出物，尚未部署。设计及验证记录见[GoodETF下单链路提速](../../docs/live-ledger/45-goodetf-latency.md)。

## 选股与下单时间实验（helper API23）

策略顶部独立配置两个时间，默认均为`09:30`。同一时间由helper在一次回调内先选股、再下单，避免依赖同分钟回调的注册顺序。

```python
OPEN_DECISION_TIME = '09:30'  # 计算折价、过滤、排序和权重
OPEN_ORDER_TIME = '09:35'     # 使用冻结结果下单，实验5分钟延迟
```

测试0、1、5、15分钟延迟时，保持`OPEN_DECISION_TIME='09:30'`，仅将`OPEN_ORDER_TIME`分别设为`09:30`、`09:31`、`09:35`、`09:45`。两个时间都必须是盘中`HH:MM`，下单不能早于选股；配置错误在注册调度前报错。请使用分钟回测，并保持回测区间、资金、成本和其他参数相同。手续费、整手、资金不足及T+1等仍由JQ原生撮合决定。

选股回调只返回目标权重与参考价。helper保存当天决策；下单时不重新获取选股价格、不重新过滤、排序或计算权重。JQ目标金额按**选股时的JQ总资产 × 冻结权重**确定；实际成交使用下单时的JQ原生价格和撮合规则，不把参考价作为JQ限价。空目标在下单时清理持仓；数据缺失或选股失败则跳过，不把失败当作清仓。当天快照可随`g`恢复，跨日快照拒绝执行；下单回调只提交一次，不增加分钟补单或先卖完再买机制。

该配置也影响聚宽脚本驱动的QMT目标提交时间：QMT继续使用冻结参考价对应的原有限价，目标金额仍由服务器按QMT提交时的账户资产计算。QMT先卖后买和续单规则保持原样。这次没有修改完整QMT本地版策略。

使用时同步更新`good_etf.py`和研究根目录的单文件`bullet_trade_jq_remote_helper.py`（API23），按下文导出和冷启动流程操作；私有连接配置与账户开关无需修改。

helper按D021不防御同进程恶意Python代码；旧版远程交易API（`configure`/`install_jq_compat`/`RemoteBrokerClient`/order系列）与全部同进程对抗机制已在L00删除。同一进程内同签名重装幂等返回；签名漂移或检测到上一代helper遗留记录即失败关闭，必须使用干净进程重启，禁止reload或热补丁。

## 直接复制语义

标准工作流是：

1. 参考[`jq_runtime`说明](../../jq_runtime/README.md)，在仓库外的私有文件中维护连接配置。
2. 本目录策略源码保持 `from jqdata import *` 和顶层helper导入；本地和聚宽使用同一个策略文件，
   不维护本地专用分支。
3. 在仓库源码中审查顶部的`VALIDATE_REMOTE_DURING_BACKTEST`、`STRATEGY_ID`和`QMT_INITIAL_CAPITAL`；回测自动使用`BACKTEST`。模拟交易在私有`jq_runtime_config.py`中通过`jq_account_enabled`和`qmt_account_enabled`选择账户，缺少策略键时默认只启用JQ。
4. 使用[`scripts/export_joinquant.py`](../../scripts/export_joinquant.py)执行Python 3.8语法、明显凭据扫描、
   profile形状和私有profile只读门禁，并生成原样文件与确定性manifest；完整步骤见
   [`聚宽校验与导出`](../../docs/live-ledger/06-joinquant-export.md)。单文件bundle不是标准路径。
5. 核对manifest中各文件SHA256与受控源码的部署声明（`VALIDATE_REMOTE_DURING_BACKTEST`/`STRATEGY_ID`），停止旧进程后上传统一helper与已校验的私有
   `jq_runtime_config.py`，最后把导出的策略原样复制到聚宽编辑器。
6. 导出后和聚宽侧均禁止再次编辑部署声明或helper；任何变更都回到受控源码重新校验、导出并冷升级。

更新helper/config/策略时必须冷升级：先停止策略并确认旧进程退出，再替换文件，最后让聚宽启动全新进程并重新完成marker/profile/执行模式校验；禁止在旧进程内reload或热补丁。任何启动失败都应丢弃该进程，修正后再次以全新进程启动。

“代码一致”指同一份策略源码和已验证API契约，不代表本地兼容引擎与聚宽私有撮合实现绝对相同。

本地解释器、PyCharm和严格类型检查的设置见
[`聚宽本地开发与兼容矩阵`](../../docs/live-ledger/05-joinquant-development.md)。类型模型只在
`TYPE_CHECKING`分支加载，不会让上传后的策略依赖BulletTrade服务器包。
