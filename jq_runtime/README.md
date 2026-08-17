# 聚宽私有运行配置

本目录定义策略与远程执行 helper 之间的版本化配置契约。示例文件可以提交，真实配置必须保留在本机并通过聚宽研究文件单独上传。

## 准备配置

1. 在仓库外的受限目录中复制 `jq_runtime_config.example.py` 并命名为 `jq_runtime_config.py`；不要在本目录
   创建或维护真实配置。
2. 在私有文件中填写服务器地址、长随机 token，以及可选的账户定位和 TLS 证书名。
3. 保持 `PROFILE_SCHEMA_VERSION = 1`，并确保 profile 中的 `strategy_id` 与策略声明一致。
4. 从仓库根运行 `python -X utf8 scripts/export_joinquant.py --validate-only --private-profile <仓库外文件>`；
   该校验不执行、不复制且不输出其中的秘密。
5. 将已校验私有文件以 `jq_runtime_config.py` 的名字上传到聚宽研究根目录。
6. 同时上传仓库中的 `helpers/bullet_trade_jq_remote_helper.py`；不要把连接信息写回策略源码。

`jq_runtime_config.py` 和 `*.local.py` 仍被 Git 忽略，作为旧流程的最后防线；忽略规则不等于推荐把秘密放进仓库目录。示例中的 `host` 与 `token` 故意留空，因此误用示例会在初始化阶段明确失败，不会静默连接。

`jq_runtime_config.py`是会被Python导入执行的可信配置代码，不是沙箱。helper能在安装时把策略namespace中的聚宽交易函数替换为抛错guard，但无法阻止配置文件自行`import socket`或执行其他任意Python代码。因此该文件只能由策略维护者生成和上传，不得接受外部内容，也不应包含网络调用或业务逻辑。

## Profile schema v1

每个 profile 的必填字段是：

- `strategy_id`：稳定的策略标识，只能包含字母、数字、点、下划线和连字符；
- `host`：BulletTrade 服务地址；
- `token`：服务端认证 token。

可选字段仅为 `port`、`account_key`、`tls_cert` 和 `rpc_timeout`。`PROFILES`和单个profile必须是普通`dict`，字段名和字符串值必须是普通`str`。未知字段会被拒绝且不会回显字段名；配置导入或属性读取的意外异常使用固定消息且不保留异常链，避免日志/异常采集器通过错误文本或`__context__`取回 token。

为避免配置笔误造成无限等待，schema v1限制`rpc_timeout`为5至300秒。helper当前不执行自动重试，避免一次聚宽调用在网络不确定时重复提交。

## 模式边界

策略不再要求用户填写字符串`MODE`。它根据聚宽`run_type`自动决定：

| 聚宽环境 | 有效执行模式 | helper/profile | 交易行为 |
|---|---|---|---|
| `simple_backtest`、`full_backtest` | `ExecutionMode.BACKTEST` | 默认不需要helper/profile；远程预检开启时需要 | 聚宽历史撮合；远程快照只写日志，不参与历史决策，也不提交远程目标 |
| `sim_trade` + `ExecutionMode.JQ_PAPER` | `JQ_PAPER` | 不需要helper/profile | 正常调用聚宽原生下单、撤单和模拟撮合；不连接QMT |
| `sim_trade` + `ExecutionMode.SIGNAL_ONLY` | `SIGNAL_ONLY` | 需要helper/profile | 计算并记录目标、发送计划卡片；聚宽交易函数由guard阻断 |
| `sim_trade` + `ExecutionMode.QMT_REMOTE` | `QMT_REMOTE` | 需要helper/profile | 读取StrategyLedger组合并提交远程目标；是否下QMT订单取决于服务器交易开关 |

用户只配置：

```python
SIM_EXECUTION_MODE = ExecutionMode.SIGNAL_ONLY
VALIDATE_REMOTE_DURING_BACKTEST = True
```

`VALIDATE_REMOTE_DURING_BACKTEST`在模拟交易中完全无作用。设为`True`时，回测初始化会幂等执行`ensure_account`和`get_portfolio`，验证公网连接、认证、资金覆盖、账实对账及真实快照，但不会调用`submit_targets`，因此不能替代QMT下单/成交能力探针。设为`False`时允许不上传helper/profile做纯离线回测。

只有导入目标`bullet_trade_jq_remote_helper`本身得到`ModuleNotFoundError`（`exc.name`匹配）时，策略才把helper视为缺失；仅`BACKTEST`关闭远程预检或`JQ_PAPER`时允许无helper运行。helper文件已上传但其内部导入失败会直接中止。helper导出稳定marker和API版本，策略用普通`getattr`校验二者。

策略内部使用`ExecutionMode`枚举；只在独立helper和网络协议边界传递`BACKTEST/JQ_PAPER/SIGNAL_ONLY/QMT_REMOTE`字符串。`SIGNAL_ONLY`和`QMT_REMOTE`会把namespace中的七个聚宽原生交易函数替换为抛错guard；`JQ_PAPER`保持这些函数原样。`QMT_REMOTE`订单只允许经StrategyLedger目标接口提交。helper不会替换`context.portfolio`，真实组合通过`PortfolioView`返回。

门禁只覆盖策略namespace中上述七个标准交易函数名。helper按D021不防御同进程恶意Python代码、monkey patch或热重载；策略与profile必须是维护者可信代码，并在任何回调或工作线程启动前完成runtime安装。同一进程内同签名重装幂等返回；签名漂移、上一代helper遗留namespace记录（`__bt_strategy_runtime_state__` token不符）或记录缺失均失败关闭。任一安装或校验失败后，必须用干净进程重启，不在同一进程重试或切回BACKTEST。

## 重载禁令与冷升级

helper不支持热重载：生产禁止`importlib.reload()`和热补丁，任何升级都必须冷启动。本helper运行在用户自有的可信策略进程中，不防御同进程任意代码执行（D021）。

安装或运行校验抛出异常后，不得在同一进程重试或改为BACKTEST，应丢弃该进程并以全新进程重新启动。

冷升级步骤：

1. 停止聚宽策略，并确认承载旧helper的进程已经退出。
2. 替换helper、私有`jq_runtime_config.py`和需要更新的策略文件；不要在旧进程中reload。
3. 由聚宽启动全新进程，重新校验helper marker/API、profile schema、`strategy_id`和执行模式。
4. 任一校验或初始化失败时停止该进程；修正文件后再次从全新进程启动，不在原进程重试。

从任何旧版helper升级都只能执行上述冷升级，绝不能在旧进程内reload。

因此，当前helper交付的是源码/profile边界与失败关闭门禁，并不授权真实资金。成交入账、资金/持仓快照已完成；QMT同步对账（L01）、目标规划执行（L02）和聚宽真实视图（L03）完成后，还必须依次通过影子、QMT模拟和用户批准的小额实盘验收（L04）。
