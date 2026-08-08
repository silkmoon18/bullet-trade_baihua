# bt_quant迁移清单

## 1. 可恢复来源

- 原仓库：`E:\dev\pycharm\bt_quant`
- 检查点：`e6462dd checkpoint: 保存聚宽执行层当前修改`
- 原远端：`https://gitee.com/SilkMoon18/bt_quant.git`
- 处理原则：原仓库保留为只读安全副本，不删除、不改写历史。

旧历史包含已经暴露的连接凭据，统一仓库不得通过subtree、merge或cherry-pick引入该历史。外部token与Webhook必须由用户在对应平台轮换。

## 2. 已迁移内容

| 来源 | 来源Git blob | 目标 | 目标Git blob | 处理 |
|---|---|---|---|---|
| `jq_platform/good_etf.py` | `39c165cd3eaead36345ed6d87c652c527595ae05` | `strategies/joinquant/good_etf.py` | `4d03b202b87a4b363bef76c46bc4f83373abb91b` | 清空host/token/Webhook、关闭真实信号、增加来源和禁实盘说明 |

目标策略与S00文档在安全审查后压平为单个脱敏基线提交；不保留返工提交链。

## 3. 未直接迁移内容

| 来源 | 来源Git blob/类别 | 决策 |
|---|---|---|
| `jq_platform/bullet_trade_jq_remote_helper.py` | `cbd7644e868bde4fb99bfc0ef72575d2aaf65939` | 不复制；逐项迁移必要能力到 `v0.9.2` helper，避免形成第二份权威实现 |
| `jq_platform/jq_remote_strategy_example.py` | 源码 | 上游已有示例，不复制重复文件 |
| `jq_platform/README.md` | 文档 | 由本目录和策略工作区README替代 |
| `feishu_notifier.py`、`log.py` | 源码 | 后续仅在服务器侧复用需要的能力 |
| `runtime/`、`logs/`、`__pycache__/` | 运行产物 | 不迁移、不提交 |
| `backtest_results/` | 历史产物 | 仅保留在旧仓库检查点 |
| `main.py` | IDE模板 | 无业务价值，不迁移 |

## 4. 上游基线证据

- 官方只读远端：`upstream=https://github.com/BulletTrade/bullet-trade.git`
- `upstream/main`：`be0451be09b1de3516d3959e70008031824103cb`
- 精确标签：`v0.9.2`
- 上游helper blob：`7fb6ba898a5315948cd91ac394a0f17a922934fe`

## 5. 后续变更规则

1. 任何从旧仓库迁移的新逻辑都要记录来源文件、来源提交和安全审查结果。
2. 不复制密钥、Webhook、运行状态、日志和缓存。
3. helper能力按测试驱动逐项迁移，不整体覆盖上游文件。
4. 迁移后运行语义变化必须写入决策记录和对应slice review。
