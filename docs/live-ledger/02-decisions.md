# 架构与工程决策

## 决策记录

| ID | 决策 | 状态 |
|---|---|---|
| D001 | 统一到BulletTrade私有fork，但不合并含凭据的bt_quant Git历史 | Accepted；私有origin待配置 |
| D002 | 以 `v0.9.2/be0451b` 为改造基线 | Accepted |
| D003 | QMT是物理成交事实源，StrategyLedger是策略归属和绩效权威 | Accepted |
| D004 | 聚宽负责数据、信号、调度和展示，不承担权威实盘账本 | Accepted |
| D005 | 生产默认 `mirror_jq_orders=False` | Accepted |
| D006 | 真实指标由服务器账本计算，通过聚宽 `record()`展示 | Accepted |
| D007 | 初始1万元从未分配资金池原子划拨，重启不重置 | Accepted |
| D008 | 主调仓使用组合级TargetPortfolioIntent，不逐只同步追单 | Accepted |
| D009 | 幂等、订单、成交和事件必须持久化，内存TTL缓存不能作为生产保障 | Accepted |
| D010 | 未知订单、过期快照和HARD对账差异一律fail closed | Accepted |
| D011 | 单节点先SQLite WAL/FULL，多实例再PostgreSQL | Accepted |
| D012 | 第一阶段专用物理账户、单策略、禁止人工交易 | Accepted |
| D013 | 策略源码保持聚宽原生格式，本地通过jqdata兼容层和类型桩开发 | Accepted |
| D014 | helper/config一次上传后，策略文件可原样复制；单文件bundle仅作为可选产物 | Accepted |
| D015 | 生产策略token无权绕过账本调用raw `broker.place_order` | Accepted |
| D016 | 首版只支持专用物理账户、单策略、禁止人工交易；共享账户延期 | Accepted |
| D017 | 每个slice修复审查问题后必须对最终SHA再次独立复审 | Accepted |
| D018 | 自动化交付与真实交易日soak分开；外部门禁允许BLOCKED等待证据 | Accepted |
| D019 | S01采用profile schema v1和严格模式矩阵；StrategyLedger完成前LIVE必须失败关闭 | Accepted |
| D020 | helper reload gate仅作误用检测与fail-closed；生产升级一律停止策略后冷启动新进程 | Accepted |
| D021 | 个人单策略首版采用可信代码/单进程边界，优先账本、成交、对账和聚宽回传；停止扩张同进程对抗性防御 | Accepted |

## D001：不合并敏感历史

原 `bt_quant` 已在 `e6462dd`形成完整检查点。其历史包含硬编码凭据，合并后即使删除当前文件也无法从Git历史移除。因此统一仓库仅导入脱敏后的有效源码，并在文档和文件头记录来源提交。

后果：旧提交历史需要在旧仓库查阅，但统一仓库不会传播历史密钥。

官方远端已改名为只读 `upstream` 并禁用push。用户提供私有fork URL之前不配置可写 `origin`，所有提交保持local-only。

## D003：双层事实与归属

券商只能说明物理账户发生了什么，不能天然说明某笔现金和持仓属于哪个策略。StrategyLedger通过订单标签、持久映射和成交事件完成策略归属，并作为策略NAV的权威来源。

后果：聚宽和任何策略都不得直接以整个券商账户执行 `order_target_value`。

## D005/D006：聚宽镜像与真实绩效分离

聚宽公开API不能注入一笔外部成交的实际数量、价格、费用和时间。`inout_cash`属于资金流，不可用于修补手续费。因此原生指标只能近似，真实绩效必须来自StrategyLedger。

后果：可选镜像只能用于操作体验，所有实盘决策和绩效读取 `real_*` 数据。

## D007：资本分配

首次创建策略账户时锁物理账户和未分配现金池，在同一事务内划拨。多个策略不能分别“检查后再扣减”，否则会并发超分配。

后果：修改聚宽初始资金不会静默重置已存在的实盘策略；必须显式执行审计化资本调整。

## D008：组合意图

逐只同步目标单无法原子表达组合、卖后买依赖和working order exposure，也容易在部分成交后重复提交。组合意图允许服务端统一规划和恢复。

后果：兼容层仍可保留单订单API，但正式调仓路径使用批量目标接口。

## D010：失败关闭

金融交易中“暂时不知道是否提交”比明确失败更危险。未知状态不得自动当作失败重试。

后果：系统可能主动暂停交易，但不会为追求可用性牺牲资金安全。

## D013/D014：本地开发与聚宽复制

同一策略文件保持 `from jqdata import *` 和独立helper导入。本地使用仓库根 `jqdata.py`、`.pyi`类型桩和受控PYTHONPATH；聚宽使用平台jqdata和已上传helper。

后果：可以消除导入红线和大部分API误用，但本地兼容引擎与聚宽私有撮合行为仍需契约测试，不能宣称绝对一致。

## D016：首版范围

首版不实现共享物理账户、多策略同标的归属或人工交易池。专用账户出现任何无法归属的人工订单、成交、现金或持仓都视为HARD差异并阻断。

后果：领域schema可预留未来扩展字段，但当前验收不承诺共享账户能力，避免在账本正确性尚未稳定时扩大资金归属复杂度。

## D017/D018：评审和外部门禁

实现提交接受第一次独立审查；修复findings后生成新的最终SHA，再由独立审查者复审该SHA或精确diff。只有最终复审通过才可DONE。

shadow、QMT模拟和小额实盘依赖真实交易日及用户外部授权，与自动化E2E实现拆开。没有足够证据时状态保持BLOCKED，不以文档完成代替真实验收。

## D019：过渡运行边界

聚宽策略默认`MODE='BACKTEST'`。三个合法模式都必须在原子登记安装owner时先建立进程级`TRANSITIONING`门禁，再读取context；transition owner/namespace/mode只接受精确内建`int`/`dict`/`str`或`None`，任何未知、被篡改或owner缺失的孤儿状态必须在context前固定失败，不得执行其比较、hash或字符串化。成功状态只有在active mode、进程signature、canonical state、进程commit capsule、独立闭包anchor和namespace runtime record按identity共同匹配，且当前安全state快照、原helper instance token和精确module generation都等于capsule封存值时才可重入；另一闭包anchor按identity绑定runtime、owner、socket三把锁及socket condition、helper token/generation与request-token registry，模块全局锁的代理或同类型替换不得被进入。fresh install还要求权威状态为空且单调contract generation精确为0。全部/部分擦除、等值浅拷贝、协同替换、helper token/generation改写或保持字典identity的原地值篡改都必须在context前失败关闭。公开版本和marker先做精确内建类型与固定值校验，poison对象不得进入比较。SHADOW/LIVE还会先把普通模块`globals()`中的交易入口置为`TRANSITIONING`，然后校验可信Python profile。BACKTEST不得读取私有profile、连接远端或替换聚宽原生下单函数；helper已上传时必须经版本化入口检查旧client/remote portfolio污染。只有精确`ModuleNotFoundError`及其traceback证明目标helper本体尚未执行时才允许纯聚宽回测本地兜底；helper内部导入失败、进程中仍加载任何helper模块对象或兜底context仍带旧远程portfolio必须在读取context前中止。模块对象识别不得只信sys.modules键名，必须接受ModuleType子类并使用项目专属marker、模块自身名称或文件名；不能仅凭通用API版本与可调用入口误判无关模块。两个BACKTEST并发时失败方保留原生函数；涉及任一远程模式时失败namespace继续失败关闭。SHADOW严格校验profile但不建远程连接；S01的LIVE只校验profile，返回`orders_enabled=False`和`production_ready=False`，不安装旧兼容层或替换portfolio，但会有意安装本地交易阻断函数。profile导入及导入后属性读取的意外异常必须固定脱敏并断开异常链，profile schema只接受精确内建容器和值类型，未知字段名不得回显，异常大的schema、数值和API版本必须得到稳定契约错误。任何合法runtime安装失败、旧兼容层污染、旧远程portfolio、在途RPC、检测到helper重载或状态完整性故障都保持`FAILED`并要求干净进程重启；污染进程不得切回BACKTEST。`good_etf.py`还必须在调用helper前拒绝LIVE启动。

FAILED在独立闭包anchor中保留进程期latch；只复位模块全局不能恢复fresh状态，必须启动真正的新进程。

RPC wrapper在定义时捕获helper instance token/module generation，并在请求入口为每个调用登记独立object token；入口、每次重试和紧邻socket边界前都必须验证精确contract/module generation、精确inflight计数、三把闭包锚定锁及helper token identity、当前request token仍在闭包锚定registry且`inflight == len(registry)`。无效类型或identity固定失败且不得执行对象魔术方法或污染锁协议。同代际finally只移除自己的token；reload/FAILED后的旧finally不得递减新代际计数或重复推进失败generation。每次成功加载还会生成闭包reload bootstrap；支持的跨线程reload误用检测会关闭单向socket gate、等待已登记attempt并发布FAILED。commit capsule按identity绑定提交namespace，reload完成时删除旧record并安装FAILED guard。该行为不构成热更新或任意`BaseException`后可在原进程恢复的保证，完整生产边界见D020。

安装reservation绑定helper token/generation、线程、namespace/mode、contract generation、active mode和gate authority identity；每个可执行context/profile回调之后及任何发布之前都要复核。BACKTEST的profile参数只接受精确`str`，不能通过`__str__`执行回调并掩盖reservation漂移。成功返回的最终线性化点在`runtime→owner→gate`锁内同时验证helper代际、gate仍开放、公开reload镜像和transition identity；在正常控制流和受测跨线程并发中，收尾异常会撤销namespace提交并进入FAILED。任意异步中断的终止边界及纯Python残余见D020。

后果：runtime只接受普通字符串mode和真实模块`globals()`字典。namespace record不能恢复进程权威状态；并发/递归安装不会排队覆盖已返回契约。门禁覆盖标准交易名、直接别名及可安全识别的函数名/partial/wrapped/直接闭包，但无法撤销藏在其他模块、容器、任意callable对象或局部变量中的原生引用。策略必须先完成单线程初始化再启动回调；profile属于维护者可信代码，运行门禁不承诺沙箱化任意Python副作用。S01只可安全验证同源策略和配置契约，不能被解释为已经具备实盘能力。只有S15替换为StrategyLedger runtime且S18至S20门禁通过后，才允许真实资金。

策略不得仅凭helper返回值是`dict`就信任运行模式；必须验证完整state schema、strategy identity、mode/run_type、布尔flags、reason及模式专属字段。验证成功后的执行模式写入一次性闭包权威；`g.bt_runtime`只是聚宽侧展示副本，任何交易决策不得读取其属性协议，当前MODE与闭包权威漂移必须固定失败。`initialize`与`process_initialize`的首条可执行语句必须安装runtime，任何jqdata/platform调用均不得先于helper gate。

## D020：reload只失败关闭，升级必须冷启动

D019中关于reload、最终线性化和`BaseException`的保证，仅适用于正常控制流和受测的跨线程并发；不得解释为任意异步中断后可以在原进程恢复。当前三锁模型由runtime `RLock`、owner `Lock`和socket `RLock`组成，支持路径遵循`runtime -> owner -> socket`。socket authority以`attempt token -> thread id`登记连接；lease检查与attempt登记在`runtime -> socket`临界区原子完成，connector随后通过独立最终permit进入且不持续持gate锁，reload关闭gate后等待已登记attempt收尾。TLS包装、握手与request/mutation发送effect在socket锁内线性化；mutation调用effect前发布handoff，发送结果不确定时不自动重试。同线程持有socket锁或拥有attempt时触发reload不得等待自身，而是终止进程。

reload gate不是热更新API。生产禁止raw `importlib.reload()`、热补丁、same-thread recursive reload，以及借助`sys.settrace`、`sys.setprofile`或signal handler在任意字节码/C返回点reload、捕获异常并恢复旧栈。即使`importlib.reload()`返回成功，旧代bootstrap也已把进程永久锁存为`FAILED`；后续安装或请求均不受支持。

升级顺序固定为：停止策略并确认旧进程退出；替换helper、私有config和策略文件；启动全新进程；重新完成版本/marker/profile/mode校验。首次从缺少当前primitive anchor的旧helper迁移，尤其raw `Lock`/pre-bootstrap版本，也必须冷启动。`RuntimeReloadAbort`、reload异常或runtime/网络effect期间的任意异步中断一律终止进程，不得catch后继续、同进程重试或切回BACKTEST。

纯Python无法把任意opcode/C返回点的恶意catch-and-resume、许可读取后的旧栈恢复，以及connector返回资源到共享holder之间的handoff全部变成不可分割操作。trace/debugger读取活动authority frame的`frame.f_locals`还会物化旧closure cell值，CPython随后可能把旧`False`同步回已经被递归reload关闭的latch；这类活动帧改写权明确不在可防御契约内。进程退出是这些残余的资源与状态清理边界。若未来LIVE要对抗同进程任意代码执行，需采用独立IO worker/子进程epoch或原生原子gate。

## D021：聚焦个人实盘主闭环

首版运行在用户自有、可信的策略和服务器进程中，不以抵抗同进程恶意Python代码、任意monkey patch、对象协议投毒或热重载攻击为目标。后续审查优先验证资金不变量、数据库事务、幂等、成交去重、未知提交恢复、对账阻断和真实指标一致性。

已有S01门禁继续作为兼容边界，但不再追加对抗性分支。核心闭环稳定后，可以在保留BACKTEST/SHADOW/LIVE基本合同、凭据隔离和失败关闭测试的前提下删除冗余门禁与历史对抗测试。裁剪不得改变以下底线：LIVE未就绪时不下单；响应未知时不盲目重发；重复成交不重复入账；账实不一致时暂停新单。
