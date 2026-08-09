# 聚宽策略工作区

本目录保存可复制到聚宽策略编辑器的策略源码。当前仅包含从 `bt_quant@e6462dd` 脱敏导入并迁移到版本化运行契约的 `good_etf.py`。

## 迁移映射

| bt_quant内容 | 统一仓库位置/处理 |
|---|---|
| `jq_platform/good_etf.py` | `strategies/joinquant/good_etf.py`，已移除token、IP和Webhook |
| `jq_platform/bullet_trade_jq_remote_helper.py` | 不复制；统一使用上游 `helpers/bullet_trade_jq_remote_helper.py` |
| `jq_platform/jq_remote_strategy_example.py` | 不复制；上游已有 `helpers/jq_remote_strategy_example.py` |
| `jq_platform/README.md` | 由 `docs/live-ledger/` 和本文件替代 |
| `feishu_notifier.py`、`log.py` | 不复制；通知和日志后续迁入服务器能力 |
| `runtime/`、`logs/`、`__pycache__/` | 运行产物，不导入Git |
| `backtest_results/` | 历史产物保留在旧仓库检查点，不作为源码导入 |
| `main.py` | IDE模板，无有效业务逻辑，不导入 |

## 当前使用限制

`good_etf.py`已经不再保存host、token、Webhook或账户配置，也不再调用旧定制helper的同步追单/通知接口。组合目标金额按`组合总资产 × DEPLOY_RATIO × 归一化权重`计算，避免把某一时刻的可用现金误当成整个组合的目标基数。

当前三种模式的边界是：

- `BACKTEST`：不需要profile且始终使用聚宽原生回测接口；helper已上传时先经版本化入口检查旧远程client/portfolio污染，只有traceback证明目标helper本体尚未执行且确实不存在时才使用纯聚宽本地兜底；helper内部导入失败、任意`sys.modules`键下仍缓存带项目名称/marker/文件特征的helper模块对象（含ModuleType子类），或无helper但context仍是旧远程portfolio都会中止；仅有相似API入口的无关模块不会被误判；
- `SHADOW`：需要版本匹配的helper和私有profile，只记录计划，所有交易变更均被阻断；
- `LIVE`：S01尚未切换到StrategyLedger，策略会明确拒绝启动。

因此当前源码可用于回测和后续影子验证，但仍不能用于真实资金。S15接入StrategyLedger runtime；S18至S20依次完成真实聚宽、QMT模拟和用户批准的小额实盘门禁。

helper的reload gate仅用于误用检测和fail-closed，不是热更新API。生产禁止raw `importlib.reload()`、热补丁、same-thread recursive reload，以及`sys.settrace`/`sys.setprofile`/signal catch-and-resume。即使reload调用返回成功，该进程也已经永久`FAILED`；`RuntimeReloadAbort`、reload异常或runtime/网络effect期间的任意异步中断都必须终止进程，不能继续旧栈、同进程重试或切回BACKTEST。

## 直接复制语义

标准工作流是：

1. 参考[`jq_runtime`说明](../../jq_runtime/README.md)，在本地私有文件中维护连接配置。
2. 首次部署时，将统一helper和私有`jq_runtime_config.py`上传到聚宽研究根目录。
3. 本目录策略源码保持 `from jqdata import *` 和可选的顶层helper导入。
4. 本地和聚宽使用同一个策略文件，不维护本地专用分支。
5. 复制策略源码到聚宽策略编辑器；只修改顶部的`PROFILE`、`MODE`和`STRATEGY_ID`部署声明。
6. S03完成后使用导出工具生成可校验的上传清单；单文件bundle是可选方案。

更新helper/config/策略时必须冷升级：先停止策略并确认旧进程退出，再替换文件，最后让聚宽启动全新进程并重新完成marker/profile/MODE校验。首次从旧raw `Lock`/pre-bootstrap helper升级也必须如此；禁止在旧进程内reload或热补丁。任何启动失败都应丢弃该进程，修正后再次以全新进程启动。

“代码一致”指同一份策略源码和已验证API契约，不代表本地兼容引擎与聚宽私有撮合实现绝对相同。
