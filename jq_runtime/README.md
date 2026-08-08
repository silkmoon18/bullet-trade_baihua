# 聚宽私有运行配置

本目录定义策略与远程执行 helper 之间的版本化配置契约。示例文件可以提交，真实配置必须保留在本机并通过聚宽研究文件单独上传。

## 准备配置

1. 将 `jq_runtime_config.example.py` 复制为 `jq_runtime_config.py`。
2. 在私有文件中填写服务器地址、长随机 token，以及可选的账户定位和 TLS 证书名。
3. 保持 `PROFILE_SCHEMA_VERSION = 1`，并确保 profile 中的 `strategy_id` 与策略声明一致。
4. 将私有文件以 `jq_runtime_config.py` 的名字上传到聚宽研究根目录。
5. 同时上传仓库中的 `helpers/bullet_trade_jq_remote_helper.py`；不要把连接信息写回策略源码。

`jq_runtime_config.py` 和 `*.local.py` 已被 Git 忽略。示例中的 `host` 与 `token` 故意留空，因此误用示例会在初始化阶段明确失败，不会静默连接。

`jq_runtime_config.py`是会被Python导入执行的可信配置代码，不是沙箱。运行门禁能在导入前阻断BulletTrade的`configure`、缓存客户端、broker和策略namespace交易入口，但无法阻止配置文件自行`import socket`或执行其他任意Python代码。因此该文件只能由策略维护者生成和上传，不得接受外部内容，也不应包含网络调用或业务逻辑。

## Profile schema v1

每个 profile 的必填字段是：

- `strategy_id`：稳定的策略标识，只能包含字母、数字、点、下划线和连字符；
- `host`：BulletTrade 服务地址；
- `token`：服务端认证 token。

可选字段为 `port`、`account_key`、`sub_account_id`、`tls_cert`、`retries`、`retry_interval`、`rpc_timeout`、`place_order_timeout_margin`、`default_wait_timeout` 和 `debug`。未知字段会被拒绝，错误信息不会回显 token。

为避免配置笔误造成无限等待，schema v1限制`retries`为0至10、`retry_interval`为0.1至30秒、`rpc_timeout`为5至300秒、下单等待及其安全余量为0至300秒。

## 模式边界

| MODE | 允许的聚宽运行类型 | helper/profile | 交易行为 |
|---|---|---|---|
| `BACKTEST` | `simple_backtest`、`full_backtest` | 不需要，也不导入私有 profile或连接网络 | 使用聚宽原生回测订单 |
| `SHADOW` | `sim_trade` | 导入profile前先建立本地门禁；必须通过schema校验，S01不建远程连接 | 只生成计划和日志；所有下单、撤单入口均被阻断 |
| `LIVE` | `sim_trade` | 导入profile前先建立本地门禁；必须通过schema校验，S01不建远程连接 | helper保持交易关闭并安装本地阻断函数；`good_etf.py`在调用helper前即因尚无StrategyLedger而拒绝启动 |

`install_strategy_runtime`的第一个参数必须直接传模块的普通`globals()`字典，`mode`必须是普通字符串；不接受可覆写字典行为的子类或会在`__str__`中执行代码的对象。SHADOW和helper级LIVE都不会安装旧`install_jq_compat`、替换`context.portfolio`或连接服务器；替换namespace中的交易函数是有意的本地fail-closed保护。

门禁会覆盖标准下单/撤单名称、同一函数对象的直接别名，以及能安全识别的函数原始名、`functools.partial`、`functools.wraps`和直接闭包引用。Python无法撤销已经保存在其他模块、容器、任意callable对象内部或本地变量中的原生函数引用；策略与profile必须是维护者可信代码，并在任何回调或工作线程启动前完成runtime安装。不得把该门禁解释为Python沙箱，也不得在profile中直接使用`socket`或另存原生下单函数。

任何旧`install_jq_compat`状态、旧远程portfolio、已发布client、helper热重载、运行状态篡改或安装失败都会要求干净进程重启；这也适用于BACKTEST，系统不会猜测旧兼容层是否已完整恢复。namespace中的runtime record只是可篡改副本，进程内signature和canonical state才是权威，不能从namespace缓存恢复进程状态。

安装与旧配置入口使用单进程原子切换边界。SHADOW/LIVE登记owner时即先安装`TRANSITIONING`门禁；并发、递归或不同契约安装不会排队覆盖已返回结果。在途RPC会让新runtime安装失败并进入`FAILED`，因此不会成功发布SHADOW/LIVE；已经获得lease的当前网络attempt可能收尾，但generation变化后不得开始下一次重试，新请求也会被拒绝。

任一合法runtime安装、配置导入或上下文校验失败后，进程进入`FAILED`并清空运行状态；不得在同一进程重试或切回BACKTEST，应让聚宽使用干净进程重启策略。

因此，S01 交付的是安全的源码/profile边界，并不授权真实资金。真正的成交入账、资金/持仓快照、恢复与对账完成后，还必须依次通过影子、QMT模拟和用户批准的小额实盘门禁。
