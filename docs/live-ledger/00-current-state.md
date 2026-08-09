# 当前架构、依赖与状态

更新时间：2026-08-09（Asia/Shanghai）

## 1. 仓库与版本状态

### BulletTrade 统一仓库

- 路径：`E:\dev\Github\bullet-trade`
- 上游：`https://github.com/BulletTrade/bullet-trade.git`
- 基线标签：`v0.9.2`
- 基线提交：`be0451b`
- 开发分支：`feat/joinquant-live-ledger`
- S00策略和文档已压平为 `v0.9.2` 之上的单个脱敏基线提交 `7085155`，返工期间的提交不再位于分支可达历史。
- S01实现候选提交为`a94aa12060c5e8cef479224952e302eeac99f37d`；预提交与精确SHA的契约、并发/对抗、部署/文档三路审查均APPROVE。
- 官方远端已改名为只读 `upstream`；fetch URL保留官方GitHub，push URL为`DISABLED`。
- 用户私有 `origin` 尚未配置；在提供私有fork URL前，本分支仅允许本地提交。
- `.idea/`、根目录`runtime/`、聚宽导出目录和本地运行profile已经加入忽略规则。

### 原 bt_quant 仓库

- 路径：`E:\dev\pycharm\bt_quant`
- 分支：`master`
- 当前状态检查点：`e6462dd`
- 远端：`https://gitee.com/SilkMoon18/bt_quant.git`
- 原仓库暂时保留为只读安全副本，尚未删除。

没有把两个 Git 历史直接合并。原因是 `bt_quant` 历史包含硬编码的远程访问凭据和飞书Webhook，直接合并会把敏感信息永久带入未来可推送的统一仓库。统一仓库只导入了脱敏后的策略源码，并在文件头记录来源提交。

## 2. 当前目录职责

```text
bullet-trade/
├─ bullet_trade/
│  ├─ core/                 回测、调度、订单模型、风险控制
│  ├─ data/                 聚宽兼容数据API及数据源适配
│  ├─ broker/               本地/远程QMT券商适配
│  ├─ server/               远程交易服务、协议、账户路由
│  ├─ compat/               jqdata兼容导出
│  ├─ reporting/            回测报告
│  └─ utils/                通用工具
├─ helpers/
│  ├─ bullet_trade_jq_remote_helper.py  单文件helper及版本化运行入口
│  └─ jq_remote_strategy_example.py
├─ strategies/
│  └─ joinquant/
│     └─ good_etf.py        脱敏、同源的聚宽策略
├─ jq_runtime/              私有profile schema、示例和上传说明
├─ jqdata.py                本地 `from jqdata import *` 兼容入口
├─ tests/                   单元、集成、E2E和策略测试
├─ docs/                    项目文档
└─ pyproject.toml           打包、依赖和开发工具配置
```

## 3. 当前运行架构

### 聚宽侧

当前策略依赖：

- 聚宽平台提供的 `jqdata`、`context`、`g`、`log`和定时任务。
- `helpers/bullet_trade_jq_remote_helper.py` 作为上传到聚宽研究目录的单文件helper。
- 聚宽负责选股、取数、触发下单和日志展示。

`v0.9.2` 已提供 `install_jq_compat(...)`：回测保持聚宽行为；模拟盘可接管常用下单函数和策略可见的 `context.portfolio`。该接管是Python代理，不会修改聚宽内部撮合账本。

S01候选在同一helper上增加了`install_strategy_runtime(...)`、稳定helper marker和profile schema v1：三个合法模式都在原子登记安装owner时先建立进程级`TRANSITIONING`门禁，再读取context；owner/namespace/mode只接受精确内建类型，未知、被篡改或owner缺失的孤儿状态会在context前固定失败，且poison对象不会进入比较、membership或错误格式化。已经成功安装时，active mode、进程signature、canonical state、commit capsule、独立闭包anchor与namespace runtime record必须在context读取前构成同一identity封套；capsule还封存提交时的原helper instance token、module generation和不可变安全state snapshot。runtime、owner、socket三把锁及socket condition、helper token/generation、请求lease registry由另一闭包anchor绑定，模块全局锁即使替换为同类型对象也不能被使用。fresh install要求权威状态为空且单调generation精确为0；全部/部分擦除、等值浅拷贝、协同替换、module token/generation改写或保持字典identity的原地值篡改均失败关闭。公开版本/marker先做精确类型与固定值校验，poison不会进入比较。SHADOW/LIVE还会同时保护namespace并在读取可信profile前清除旧远程客户端。BACKTEST不读取profile或连接网络，也不替换聚宽原生下单函数；helper已上传时，`good_etf.py`必须经版本化入口检查旧client/remote portfolio等污染，只有精确`ModuleNotFoundError`及其traceback证明目标helper本体尚未执行时才允许纯聚宽回测本地兜底。helper内部导入失败、进程中仍加载任何helper模块对象或兜底context仍带旧远程portfolio都会在读取context前中止；模块对象识别接受ModuleType子类，并使用项目专属模块名、稳定marker或文件名，不能通过任意sys.modules缓存键隐藏，也不会仅因无关模块具有相似API入口而误判。并发BACKTEST的失败方保留原生下单函数；涉及任一远程模式的并发失败namespace仍安装本地门禁。进程模式、namespace、公开helper、缓存broker和短连接client共同阻断交易或远程访问；并发/递归安装、在途RPC、旧兼容状态、旧远程portfolio、检测到helper重载或公开状态篡改都失败关闭。profile导入和导入后属性读取的意外异常使用固定消息并断开异常链，profile容器和值只接受精确内建类型；未知字段名不回显，异常大的schema、数值或API版本也只产生稳定契约错误。S01的LIVE只校验profile，不安装旧兼容层、不替换portfolio且保持连接和交易关闭；其namespace替换仅是本地fail-closed保护。合法runtime安装失败后必须使用干净进程重启，污染进程也不能切回BACKTEST。`good_etf.py`在调用helper前就会拒绝LIVE启动。

FAILED会把独立闭包anchor保留为进程期失败latch；即使随后复位active mode、generation及其他模块全局，也不能伪装成fresh install。只有真正新进程的anchor为空。

远程RPC wrapper在定义时捕获helper instance token/module generation，请求时再登记独立object token；入口、每次重试和紧邻`socket.create_connection`前都要求精确generation/count、当前token仍在闭包锚定registry且`inflight == len(registry)`。类型/identity污染会在socket前固定失败且不执行对象比较或锁上下文协议；同代际finally只移除自己的token，reload后的旧finally不能改写新代际计数。helper reload先于任何generation变化发布FAILED并清client，随后只读取旧状态中的精确内建计数；poison旧值或初始化中断都不能留下可远程访问的混合代际。

该边界要求直接传普通模块`globals()`字典和普通字符串mode。它会保护标准交易名、直接别名及可安全识别的函数名、partial、wrapped和直接闭包引用，但无法撤销已经藏在其他模块、容器、任意callable对象或局部变量中的原生函数引用；因此策略初始化必须单线程、先安装runtime再启动任何回调，profile仍属于可信代码而非沙箱。namespace runtime record不是权威恢复源，进程内signature/canonical state、commit capsule、单调generation与当前helper实例必须同时匹配。

### S01当前并发模型与生产边界（取代上述v11重载表述）

当前候选使用三把闭包锚定锁：runtime `RLock`保护权威状态，owner `Lock`保护安装所有权，socket `RLock`保护gate和远程effect；支持路径遵循`runtime -> owner -> socket`锁序。socket authority以`attempt token -> thread id`登记在途连接。lease检查与attempt登记在`runtime -> socket`临界区原子完成；随后connector通过独立的最终latch/token permit进入，且不持续持有socket锁，所以跨线程reload可以关闭gate，但必须等待该attempt结束。TLS包装、握手及request/mutation发送等远程effect在socket `RLock`内线性化；mutation在调用effect前发布handoff，发送已开始或结果不确定时不得自动重试。post-connector的runtime复核发生前先结束attempt，以避免reload持runtime等待attempt、请求持attempt等待runtime的锁循环。同线程持有socket锁或拥有attempt时发起递归reload不会等待自身，而是进入进程终止型失败。

reload gate只是误用检测和fail-closed防线，不是热更新API。生产禁止直接调用`importlib.reload()`、热补丁、same-thread recursive reload，以及用`sys.settrace`、`sys.setprofile`或signal handler在任意字节码/C返回点触发reload后捕获异常并恢复旧栈。即使普通`importlib.reload()`自身返回成功，旧代bootstrap也已经把该进程永久置为`FAILED`；这不代表热升级成功，后续不得重新安装runtime、发起请求或切回BACKTEST。

`RuntimeReloadAbort`、任何reload异常和在runtime/网络effect期间发生的任意异步中断都按进程终止事件处理：不得捕获后继续旧调用栈，不得在同一进程重试，不得用BACKTEST“恢复”。支持的保证限定为正常控制流和受测的跨线程并发；上文关于reload或`BaseException`失败关闭的描述也只在这个边界内成立。纯Python无法对恶意catch-and-resume、任意opcode/C返回点的中断，以及connector返回资源到共享holder之间的极短handoff窗口给出不可绕过的原子保证。尤其是trace/debugger读取活动authority frame的`frame.f_locals`会物化旧closure cell值；递归reload关闭latch后，CPython的trace locals同步可能把旧`False`写回同一cell并回滚gate，所以具有活动帧改写权的调试/跟踪代码不在可防御契约内。进程退出是释放这类残余OS资源和清除旧闭包状态的最终边界。未来若LIVE要求抵抗这类同进程任意代码执行，应把网络IO移到带epoch的独立worker/子进程，或使用原生原子gate。

helper升级必须冷升级：停止聚宽策略并确认旧进程退出，替换helper/config/策略文件，再由平台启动全新进程并重新校验marker、profile和模式。首次从缺少当前primitive anchor的旧helper升级，尤其raw `Lock`/pre-bootstrap版本，也只能走此流程，绝不能在旧进程内reload。

### BulletTrade服务器侧

当前能力包括：

- TCP协议、token、TLS和连接处理。
- MiniQMT/BigQMT适配。
- 账户、持仓、订单、成交查询。
- 下单、撤单、订单状态归一。
- `submit_unknown`等基础不确定状态表达。
- `sub_account_id`路由和单笔限额。

当前不具备：

- 持久化的策略级现金和持仓账本。
- 1万元策略资金的原子分配与冻结。
- 策略持仓归属和共享账户隔离。
- 持久幂等、成交去重、事务outbox。
- 组合目标执行器和卖后买状态机。
- 策略级真实NAV/TWR/回撤。
- 可阻断交易的账实对账系统。

## 4. 依赖现状

### Python与基础依赖

- Python：`>=3.8`
- pandas：`>=1.3,<3.0`
- numpy：`>=1.21`
- matplotlib、plotly、pyecharts：图表和报告
- jqdatasdk：聚宽数据源
- python-dotenv：服务器配置
- filelock：本地运行锁
- jupyterlab/ipykernel：研究环境

### 可选依赖

- `qmt`：`xtquant`
- `qmtserver`：Windows `pywin32`
- `tushare`、`rqdata`、`tdx`：替代数据源
- `report`：报告截图相关依赖

### 开发依赖

- pytest、pytest-cov、pytest-mock、pytest-asyncio
- black、flake8、mypy、isort

### 聚宽平台特有依赖

聚宽运行时不能假设安装完整 `bullet_trade` 包。可直接复制的策略必须只依赖：

- `from jqdata import *`
- Python标准库和聚宽内置库
- 已预先上传到聚宽研究根目录的独立helper/config文件

## 5. 本地与聚宽代码一致性现状

仓库根目录已有 `jqdata.py`，本地策略可以保持聚宽原生写法：

```python
from jqdata import *
```

本地运行时它会转发到 `bullet_trade.compat.jqdata`。这已经解决运行导入问题，但IDE体验仍不完整：

- 动态 `from ... import *` 的类型推断有限。
- `g`、`log`、`context.portfolio`等聚宽动态对象缺少精确类型桩。
- `helpers/` 默认不一定是IDE Source Root，顶层helper导入可能提示未解析。
- 本地Python/pandas版本与聚宽运行环境可能不同。
- 本地兼容实现只能保证已覆盖API的契约接近，不能自动保证聚宽私有引擎行为完全相同。

因此后续需要增加 `.pyi` 类型桩、统一开发环境、API契约测试和聚宽导出校验。

## 6. 当前 good_etf 策略状态

应保留的部分：

- `avoid_future_data=True`、`use_real_price=True`。
- 使用前一交易日构造ETF池。
- 流动性和港股类ETF过滤。
- 折价排序、前N只和折价绝对值权重。
- 停牌/涨停过滤、多时点风控和日终对账思想。

S01候选已经处理：

- 删除host、token、Webhook和账户定位等策略内连接配置，只保留`PROFILE`、`MODE`、`STRATEGY_ID`。
- 移除旧定制helper的同步追单、账户查询、全账户撤单和通知调用。
- 过渡期组合目标改为`context.portfolio.total_value × DEPLOY_RATIO × normalized_weight`，不再用可用现金直接计算最终目标。
- 尾盘仅记录聚宽组合快照，不再把旧helper返回的整个物理账户误报为策略级对账。

仍然存在的问题：

1. 过渡目标仍使用聚宽组合总资产；实盘必须改用StrategyLedger的策略虚拟NAV、working exposure和费用缓冲。
2. 生产级异步执行、卖后买和部分成交恢复尚未实现；不能把S01兼容路径用于真实资金。
3. 策略级资金/持仓归属、持久幂等和券商硬对账尚未实现。
4. 09:30调仓和09:30风控可能对同一标的产生冲突。
5. 昨日单位净值与今日开盘价不是严格同时间折价。
6. 1万元、3只ETF受到100份整手和最低佣金显著影响。
7. 数据故障与“有效但无候选”尚未严格区分。

### 6.1 与 v0.9.2 helper 的已知API差异

迁移策略来自 `bt_quant@e6462dd` 的定制helper调用。下表保留迁移差异及S01候选的处理结果：

| 迁移策略调用 | v0.9.2状态 | 处理决策 |
|---|---|---|
| `configure(jq_order=..., jq_order_value=..., jq_order_target=..., jq_order_target_value=...)` | 4个参数均不受支持 | 已移除；统一调用版本化runtime入口 |
| `configure(send_signals=...)` | 不支持该参数 | 已移除；由BACKTEST/SHADOW/LIVE模式控制 |
| `configure(feishu_webhook_url=...)` | 不支持该参数 | 已移除；通知后续移到服务器事件 |
| `configure(strategy_name=...)` | 不支持该参数 | 已改为稳定`STRATEGY_ID`；S14由strategy-scoped API承载 |
| `bt.notify(...)` | 上游helper无此函数 | 已移除；S01只写聚宽日志 |
| `bt.cancel_all_open_orders()` | 上游helper无此扩展 | 已移除；回测仅撤聚宽可见挂单，生产后续按strategy intent撤单 |
| `bt.order_target_sync(...)` | 上游helper无此扩展 | 已移除；生产由组合执行状态机异步完成 |
| `bt.order_target_value_sync(...)` | 上游helper无此扩展 | 已移除；生产改为TargetPortfolioIntent |

S01候选只证明源码/profile边界可测试：BACKTEST可运行，SHADOW只生成计划；LIVE仍被明确阻断。S03导出smoke、S15 StrategyLedger runtime和S18至S20真实门禁均不可省略。

第四轮冻结后，策略还收紧了helper返回契约与生命周期入口：runtime state必须是完整、精确且自洽的schema/identity/mode/run_type/flags/reason/profile_module/blocked_mutations组合；`initialize`和`process_initialize`的首条可执行语句都是runtime安装，jqdata/platform调用不得先于helper gate。随后冻结前对抗探针证明读取`g.bt_runtime`本身可执行平台属性协议并协同降级MODE，因此执行模式现封存在安装后的一次性闭包权威中，`g.bt_runtime`只保留为聚宽侧展示副本，交易入口完全不读取它；当前MODE与闭包权威漂移会固定失败。该阶段共增加17个策略回归；S01的预提交审查与精确SHA复审均已通过，状态为DONE。

## 7. 安全现状

- 原策略曾硬编码远程token、服务器地址和飞书Webhook；这些值应视为已经暴露，必须在外部系统轮换。
- 新仓库策略已移除这些值；默认`MODE='BACKTEST'`，且S01的LIVE明确失败关闭。
- 官方公共仓库已配置为只读 `upstream` 并禁用push；在用户提供私有fork URL后再添加可写 `origin`。
- 生产配置不得提交到Git，日志不得打印token、Webhook或完整账户信息。

## 8. 当前基线结论

当前分支适合作为统一改造起点，但尚不具备真实资金上线条件。P0条件是完成开发体验、协议契约、策略账本、持久幂等、真实成交入账和硬对账闸门。
