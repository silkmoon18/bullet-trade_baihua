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

可选字段为 `port`、`account_key`、`sub_account_id`、`tls_cert`、`retries`、`retry_interval`、`rpc_timeout`、`place_order_timeout_margin`、`default_wait_timeout` 和 `debug`。`PROFILES`和单个profile必须是普通`dict`，字段名和字符串值必须是普通`str`，数值与布尔字段也只接受对应的精确内建类型。未知字段会被拒绝且不会回显字段名；配置导入或属性读取的意外异常使用固定消息且不保留异常链，避免日志/异常采集器通过错误文本或`__context__`取回 token。超范围或异常大的精确整数也只产生稳定的契约错误。

为避免配置笔误造成无限等待，schema v1限制`retries`为0至10、`retry_interval`为0.1至30秒、`rpc_timeout`为5至300秒、下单等待及其安全余量为0至300秒。

## 模式边界

| MODE | 允许的聚宽运行类型 | helper/profile | 交易行为 |
|---|---|---|---|
| `BACKTEST` | `simple_backtest`、`full_backtest` | helper可缺省；存在时只做版本和历史污染检查，仍不导入私有profile或连接网络 | 使用聚宽原生回测订单，不替换下单函数 |
| `SHADOW` | `sim_trade` | 导入profile前先建立本地门禁；必须通过schema校验，S01不建远程连接 | 只生成计划和日志；所有下单、撤单入口均被阻断 |
| `LIVE` | `sim_trade` | 导入profile前先建立本地门禁；必须通过schema校验，S01不建远程连接 | helper保持交易关闭并安装本地阻断函数；`good_etf.py`在调用helper前即因尚无StrategyLedger而拒绝启动 |

只有导入目标`bullet_trade_jq_remote_helper`本身得到精确`ModuleNotFoundError`，且异常traceback表明helper本体尚未开始执行时，策略才把helper视为缺失；helper文件已上传但其内部依赖、初始化或同名缺失异常必须直接中止，不能静默降级为BACKTEST。helper导出稳定`STRATEGY_RUNTIME_HELPER_MARKER`。无helper兜底会在读取context前扫描`sys.modules`键，以及ModuleType和其子类对象的自身名称、项目marker或文件名，因此任意缓存键名和模块子类不能隐藏已加载helper；它不会仅凭通用API版本与可调用入口误判无关模块。兜底也会拒绝带稳定marker或旧helper类特征的远程portfolio，要求干净进程重启。

`install_strategy_runtime`的第一个参数必须直接传模块的普通`globals()`字典，`mode`必须是普通字符串；不接受可覆写字典行为的子类或会在`__str__`中执行代码的对象。SHADOW和helper级LIVE都不会安装旧`install_jq_compat`、替换`context.portfolio`或连接服务器；替换namespace中的交易函数是有意的本地fail-closed保护。

门禁会覆盖标准下单/撤单名称、同一函数对象的直接别名，以及能安全识别的函数原始名、`functools.partial`、`functools.wraps`和直接闭包引用。Python无法撤销已经保存在其他模块、容器、任意callable对象内部或本地变量中的原生函数引用；策略与profile必须是维护者可信代码，并在任何回调或工作线程启动前完成runtime安装。不得把该门禁解释为Python沙箱，也不得在profile中直接使用`socket`或另存原生下单函数。

任何旧`install_jq_compat`状态、旧远程portfolio、已发布client、检测到helper重载、运行状态篡改或安装失败都会要求干净进程重启；这也适用于BACKTEST，系统不会猜测旧兼容层是否已完整恢复。成功runtime的active mode、进程signature、canonical state、进程commit capsule、独立闭包anchor、其提交contract generation与namespace runtime record必须在读取context前按identity通过完整封套校验；capsule还绑定提交时的原helper instance token、精确module generation和不可变安全state snapshot。另一闭包anchor绑定三把锁、helper token/generation及request-token registry。fresh install要求权威状态为空且单调contract generation精确为0。全部/部分擦除、等值浅拷贝、协同替换、token/generation改写、保持字典identity的原地值篡改、结构篡改或poison值均失败关闭。namespace record只是可篡改副本，不能单独恢复进程权威状态。

FAILED会在独立闭包anchor中保留进程期失败latch；即使随后复位active mode、generation及其他模块全局，也不能伪装成fresh install。只有真正新进程的anchor为空。

远程RPC wrapper在定义时捕获helper instance token/module generation，请求入口再登记独立object token和contract generation。入口、每次重试及紧邻`socket.create_connection`前都只接受精确generation/count，按identity确认三把闭包锚定锁和helper token，且要求本请求token仍在精确set registry中、`inflight == len(registry)`；无效类型/identity会固定失败、清理client且不执行对象比较、转换或候选锁协议。同代际finally只移除自己的token；reload或已有FAILED清空registry后，旧finally不会改写新代际计数。

每次完整加载都会创建锚定本代runtime锁、owner锁、socket gate和提交namespace的闭包bootstrap。socket authority以`attempt token -> thread id`登记在途连接；lease检查与attempt登记在`runtime -> socket`临界区原子完成，connector随后通过独立最终latch/token permit进入且不持续持有gate锁，因此跨线程reload可以关闭gate，但会等待已登记attempt收尾。TLS包装、握手和request/mutation发送effect在socket `RLock`内线性化；mutation在调用effect前发布handoff，发送异常后按结果不确定处理，不自动重试。三锁职责分别是runtime `RLock`保护权威状态、owner `Lock`保护安装所有权、socket `RLock`保护gate/effect；支持路径遵循`runtime -> owner -> socket`，并在post-connector runtime复核前结束attempt以避免锁循环。

安装与旧配置入口使用单进程原子切换边界。BACKTEST/SHADOW/LIVE登记owner时都先建立进程级`TRANSITIONING`门禁，再读取`context`；SHADOW/LIVE还会立即保护namespace交易入口。transition三元组只接受精确内建类型；所有锁先按闭包identity校验再进入上下文协议。安装reservation绑定helper代际、线程、namespace/mode、contract generation、active mode和闭包gate identity，并在每个可执行context/profile边界后复核。最终返回以`runtime → owner → socket gate`锁序再次原子确认gate仍开放和transition identity；reload先关闭gate时安装必须失败并撤销刚提交的namespace状态，安装先完成时后续reload负责立即清理。BACKTEST的`profile`和`profile_module`也只接受普通`str`，不执行自定义`__str__`。并发、递归或不同契约安装不会排队覆盖已返回结果。在途RPC会让新runtime安装失败并进入`FAILED`，因此不会成功发布新契约；已经进入socket的当前attempt可能收尾，但FAILED后不得开始新的socket或重试，新请求也会被拒绝。

任一合法runtime安装、配置导入或上下文校验失败后，进程进入`FAILED`并清空运行状态；不得在同一进程重试或切回BACKTEST，应让聚宽使用干净进程重启策略。

## 重载禁令与冷升级

reload gate只用于误用检测和fail-closed，不是热更新API。生产禁止raw `importlib.reload()`、热补丁、same-thread recursive reload，以及通过`sys.settrace`、`sys.setprofile`或signal handler在任意字节码/C返回点发起reload、捕获异常后恢复旧栈。即使普通`importlib.reload()`自身返回成功，旧代bootstrap也已把进程永久锁存为`FAILED`；返回成功不代表热升级成功，后续不得重新安装runtime、发起请求或切回BACKTEST。

`RuntimeReloadAbort`、任一reload异常，以及runtime/网络effect期间的任意异步中断都必须终止进程。禁止捕获后继续旧调用栈、在同一进程重试或改为BACKTEST。helper提供的保证限于正常控制流和受测的跨线程并发，不承诺任意`BaseException`发生后仍可在原进程安全恢复。特别是trace/debugger读取活动authority frame的`frame.f_locals`会物化旧closure cell值；递归reload关闭latch后，CPython的trace locals同步可能把旧`False`写回同一cell并回滚gate。此类活动帧改写权不在可防御契约内。

冷升级步骤：

1. 停止聚宽策略，并确认承载旧helper的进程已经退出。
2. 替换helper、私有`jq_runtime_config.py`和需要更新的策略文件；不要在旧进程中reload。
3. 由聚宽启动全新进程，重新校验helper marker/API、profile schema、`strategy_id`和MODE。
4. 任一校验或初始化失败时停止该进程；修正文件后再次从全新进程启动，不在原进程重试。

首次从缺少当前primitive anchor的旧helper升级，尤其raw `Lock`/pre-bootstrap版本，也只能执行上述冷升级。纯Python无法对任意opcode/C返回点的恶意catch-and-resume、最终许可读取后的旧栈恢复，以及connector返回资源到共享holder之间的极短handoff窗口提供不可绕过的原子保证；进程退出是释放残余OS资源和清除旧闭包状态的最终边界。若未来LIVE必须防御同进程任意代码执行，需采用带epoch的独立IO worker/子进程，或原生原子gate。

因此，S01 交付的是安全的源码/profile边界，并不授权真实资金。真正的成交入账、资金/持仓快照、恢复与对账完成后，还必须依次通过影子、QMT模拟和用户批准的小额实盘门禁。
