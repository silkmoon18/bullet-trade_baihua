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

可选字段为 `port`、`account_key`、`sub_account_id`、`tls_cert`、`retries`、`retry_interval`、`rpc_timeout`、`place_order_timeout_margin`、`default_wait_timeout` 和 `debug`。`PROFILES`和单个profile必须是普通`dict`，字段名和字符串值必须是普通`str`，数值与布尔字段也只接受对应的精确内建类型。未知字段会被拒绝且不会回显字段名；配置导入或属性读取的意外异常使用固定消息且不保留异常链，避免日志/异常采集器通过错误文本或`__context__`取回 token。超范围或异常大的精确整数也只产生稳定的契约错误。

为避免配置笔误造成无限等待，schema v1限制`retries`为0至10、`retry_interval`为0.1至30秒、`rpc_timeout`为5至300秒、下单等待及其安全余量为0至300秒。

## 模式边界

| MODE | 允许的聚宽运行类型 | helper/profile | 交易行为 |
|---|---|---|---|
| `BACKTEST` | `simple_backtest`、`full_backtest` | helper可缺省；存在时校验marker/API版本与回测run_type，仍不导入私有profile或连接网络 | 使用聚宽原生回测订单，不替换下单函数 |
| `SHADOW` | `sim_trade` | 必须通过profile schema v1校验；不建远程连接 | 只生成计划和日志；所有下单、撤单入口均被替换为抛错guard |
| `LIVE` | `sim_trade` | 必须通过profile schema v1校验；不建远程连接 | helper保持`enabled=False`阻断态并安装同样的guard；`good_etf.py`在调用helper前即因尚无StrategyLedger而拒绝启动 |

只有导入目标`bullet_trade_jq_remote_helper`本身得到`ModuleNotFoundError`（`exc.name`匹配）时，策略才把helper视为缺失并仅在BACKTEST本地兜底；helper文件已上传但其内部导入失败会直接中止，不能静默降级为BACKTEST。helper导出稳定`STRATEGY_RUNTIME_HELPER_MARKER`和`STRATEGY_RUNTIME_API_VERSION`，策略用普通`getattr`校验二者。

`install_strategy_runtime`的第一个参数必须直接传模块的普通`globals()`字典，`mode`必须是普通字符串。SHADOW和helper级LIVE会把namespace中的七个聚宽交易函数替换为抛错guard，这是有意的本地fail-closed保护；旧`install_jq_compat`远程接管API已在L00删除，helper不会替换`context.portfolio`或连接服务器。

门禁只覆盖策略namespace中上述七个标准交易函数名。helper按D021不防御同进程恶意Python代码、monkey patch或热重载；策略与profile必须是维护者可信代码，并在任何回调或工作线程启动前完成runtime安装。同一进程内同签名重装幂等返回；签名漂移、上一代helper遗留namespace记录（`__bt_strategy_runtime_state__` token不符）或记录缺失均失败关闭。任一安装或校验失败后，必须用干净进程重启，不在同一进程重试或切回BACKTEST。

## 重载禁令与冷升级

helper不支持热重载：生产禁止`importlib.reload()`和热补丁，任何升级都必须冷启动。本helper运行在用户自有的可信策略进程中，不防御同进程任意代码执行（D021）。

安装或运行校验抛出异常后，不得在同一进程重试或改为BACKTEST，应丢弃该进程并以全新进程重新启动。

冷升级步骤：

1. 停止聚宽策略，并确认承载旧helper的进程已经退出。
2. 替换helper、私有`jq_runtime_config.py`和需要更新的策略文件；不要在旧进程中reload。
3. 由聚宽启动全新进程，重新校验helper marker/API、profile schema、`strategy_id`和MODE。
4. 任一校验或初始化失败时停止该进程；修正文件后再次从全新进程启动，不在原进程重试。

从任何旧版helper升级都只能执行上述冷升级，绝不能在旧进程内reload。

因此，当前helper交付的是源码/profile边界与失败关闭门禁，并不授权真实资金。成交入账、资金/持仓快照已完成；QMT同步对账（L01）、目标规划执行（L02）和聚宽真实视图（L03）完成后，还必须依次通过影子、QMT模拟和用户批准的小额实盘验收（L04）。
