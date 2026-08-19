# 聚宽策略工作区

本目录保存可复制到聚宽策略编辑器的策略源码。当前仅包含从 `bt_quant@e6462dd` 脱敏导入并迁移到版本化运行契约的 `good_etf.py`。

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

`good_etf.py`已经不再保存host、token、profile、Webhook或账户配置，也不再包含TCP、QMT回调、意图恢复、模式解析、通知异常处理或下单包装代码。策略文件只保留唯一`STRATEGY_ID`、回测开关、策略/执行参数、一次门面安装，以及ETF选股、权重、调仓、止盈止损和尾盘记录。`JoinQuantRuntime`门面对三种模式提供统一组合、下单、撤单、通知和查询入口。组合目标金额按`组合总资产 × DEPLOY_RATIO × 归一化权重`计算，避免把某一时刻的可用现金误当成整个组合的目标基数。

当前三种模式的边界是：

- `BACKTEST`：始终使用聚宽原生回测接口；helper必须与策略一起上传，关闭远程预检时不读取私有profile；
- `JQ`：只允许聚宽模拟交易，正常调用聚宽原生下单、撤单和模拟撮合，由聚宽维护资金、持仓和指标；同时通过helper/profile发送目标买入计划卡片，但绝不提交QMT目标；
- `QMT_REMOTE`：使用StrategyLedger真实组合与目标接口；聚宽原生交易函数保持阻断，只有真实账户对账READY后才把`production_ready`标记为true。服务端交易总开关默认仍为关闭。

当前仓库代码已经具备回测、JQ模拟和QMT_REMOTE链路；服务端交易开关默认关闭。真实资金启用前仍必须在目标QMT环境完成模拟与小额人工验收，并由用户明确放行。

helper按D021不防御同进程恶意Python代码；旧版远程交易API（`configure`/`install_jq_compat`/`RemoteBrokerClient`/order系列）与全部同进程对抗机制已在L00删除。同一进程内同签名重装幂等返回；签名漂移或检测到上一代helper遗留记录即失败关闭，必须使用干净进程重启，禁止reload或热补丁。

## 直接复制语义

标准工作流是：

1. 参考[`jq_runtime`说明](../../jq_runtime/README.md)，在仓库外的私有文件中维护连接配置。
2. 本目录策略源码保持 `from jqdata import *` 和顶层helper导入；本地和聚宽使用同一个策略文件，
   不维护本地专用分支。
3. 在仓库源码中审查顶部的`VALIDATE_REMOTE_DURING_BACKTEST`和`STRATEGY_ID`；回测自动使用`BACKTEST`。模拟交易的profile和模式在私有`jq_runtime_config.py`的`STRATEGIES[strategy_id]`中选择，缺少策略键时使用`DEFAULT_PROFILE`并安全地默认为`JQ`。
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
