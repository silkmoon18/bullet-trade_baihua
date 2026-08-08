# 聚宽私有运行配置

本目录定义策略与远程执行 helper 之间的版本化配置契约。示例文件可以提交，真实配置必须保留在本机并通过聚宽研究文件单独上传。

## 准备配置

1. 将 `jq_runtime_config.example.py` 复制为 `jq_runtime_config.py`。
2. 在私有文件中填写服务器地址、长随机 token，以及可选的账户定位和 TLS 证书名。
3. 保持 `PROFILE_SCHEMA_VERSION = 1`，并确保 profile 中的 `strategy_id` 与策略声明一致。
4. 将私有文件以 `jq_runtime_config.py` 的名字上传到聚宽研究根目录。
5. 同时上传仓库中的 `helpers/bullet_trade_jq_remote_helper.py`；不要把连接信息写回策略源码。

`jq_runtime_config.py` 和 `*.local.py` 已被 Git 忽略。示例中的 `host` 与 `token` 故意留空，因此误用示例会在初始化阶段明确失败，不会静默连接。

## Profile schema v1

每个 profile 的必填字段是：

- `strategy_id`：稳定的策略标识，只能包含字母、数字、点、下划线和连字符；
- `host`：BulletTrade 服务地址；
- `token`：服务端认证 token。

可选字段为 `port`、`account_key`、`sub_account_id`、`tls_cert`、`retries`、`retry_interval`、`rpc_timeout`、`place_order_timeout_margin`、`default_wait_timeout` 和 `debug`。未知字段会被拒绝，错误信息不会回显 token。

## 模式边界

| MODE | 允许的聚宽运行类型 | helper/profile | 交易行为 |
|---|---|---|---|
| `BACKTEST` | `simple_backtest`、`full_backtest` | 不需要，也不导入私有 profile或连接网络 | 使用聚宽原生回测订单 |
| `SHADOW` | `sim_trade` | 必须存在并通过schema校验，但S01不建远程连接 | 只生成计划和日志；所有下单、撤单入口均被阻断 |
| `LIVE` | `sim_trade` | 必须存在并通过schema校验 | S01仅保留旧兼容层验证路径；`good_etf.py`会因尚无StrategyLedger而拒绝启动 |

因此，S01 交付的是安全的源码/profile边界，并不授权真实资金。真正的成交入账、资金/持仓快照、恢复与对账完成后，还必须依次通过影子、QMT模拟和用户批准的小额实盘门禁。
