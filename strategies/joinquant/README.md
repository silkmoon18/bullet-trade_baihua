# 聚宽策略工作区

本目录保存可复制到聚宽策略编辑器的策略源码。当前仅包含从 `bt_quant@e6462dd` 脱敏导入的迁移基线 `good_etf.py`。

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

`good_etf.py`仍是迁移基线：

- `SEND_SIGNALS=False`
- 连接token为空
- 尚未切换到StrategyLedger
- 已知目标金额和同步追单问题尚待后续slice修复
- 仍调用旧定制helper的4个 `jq_order*` 配置参数、`strategy_name`、`notify`、`cancel_all_open_orders`、`order_target_sync` 和 `order_target_value_sync`；这些接口与上游v0.9.2 helper不兼容

因此当前文件只能编译和审查，不能搭配上游helper直接运行。S01先完成helper/profile契约迁移；S15完成StrategyLedger runtime。在S20小额实盘门禁通过前不得启用真实信号。

## 直接复制语义

S01完成后的标准工作流将是：

1. 将统一helper和无密钥profile文件一次上传到聚宽研究根目录。
2. 本目录策略源码保持 `from jqdata import *` 和顶层helper导入。
3. 本地和聚宽使用同一个策略文件，不维护本地专用分支。
4. 复制策略源码到聚宽策略编辑器即可运行。
5. 新环境可使用导出工具生成上传清单；单文件bundle是可选方案。

“代码一致”指同一份策略源码和已验证API契约，不代表本地兼容引擎与聚宽私有撮合实现绝对相同。
