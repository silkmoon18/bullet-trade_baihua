# 当前 session

## Session元数据

- 日期：2026-08-09
- 时区：Asia/Shanghai
- 工作分支：`feat/joinquant-live-ledger`
- 上游基线：`v0.9.2 / be0451b`
- 原策略检查点：`bt_quant@e6462dd`
- 统一仓库S00基线：`7085155`

## 用户目标

1. 将 `bt_quant` 有效内容统一到BulletTrade项目管理。
2. 本地解释器能解析聚宽策略、提供代码提示并尽早发现错误。
3. 策略源码与聚宽侧保持一致，可直接复制运行。
4. 实现1万元策略虚拟账户、真实成交入账、真实持仓/资金/NAV回传聚宽。
5. 构建可恢复、可审计、强幂等、可对账的成熟实盘系统。
6. 全部工作先文档化并按slice实施；每个slice必须测试和代码审查通过后才能继续。

## 本session已完成

- 检查并原样提交 `bt_quant` 当前修改：`e6462dd`。
- 拉取BulletTrade上游最新版本，确认 `origin/main == v0.9.2 == be0451b`。
- 创建 `feat/joinquant-live-ledger` 分支。
- 从旧仓库导入脱敏的 `good_etf.py`，默认关闭真实信号。
- 决定不合并含敏感凭据的旧Git历史。
- 建立本目录内的工程事实文档和可执行基线校验脚本。
- 重新fetch并核验 `upstream/main == v0.9.2 == be0451b`。
- 官方远端改名为只读 `upstream`，push URL设为`DISABLED`；私有origin待用户提供。
- S00独立审查推动修复了API差异、忽略规则、迁移manifest、slice依赖和测试证据。返工历史曾短暂包含旧敏感审查特征，已在未推送前压平成单个脱敏提交`7085155`。

## 当前状态

- 原 `bt_quant` 工作树干净，旧仓库保留。
- `.idea/`、runtime、导出产物和本地profile已有明确忽略规则。
- S00最终候选为`7085155`；基线校验脚本和Git格式检查均PASS，最终独立复审已APPROVE。
- S01初始实现`655b3c9`经多轮修复形成精确候选`354ecf3`；契约和安全审查批准，但对抗审查仍发现旧compat originals/别名、helper reload、并发契约、RPC切换、污染BACKTEST、BaseException凭据脱敏及namespace状态伪造问题，因此该SHA明确REWORK，不能发布。
- S01 v3精确候选`34944b3`经三方复审仍为REWORK：策略BACKTEST分支可绕过helper污染检查；helper在BACKTEST读取context前尚未建立进程门禁；`raise ... from None`仍通过`__context__`保留profile导入异常；profile导入成功后的属性异常也未脱敏。
- S01 v4首轮工作树预审继续为REWORK，又发现异常进程状态失败开放、无helper兜底接受旧远程portfolio、helper内部ImportError被误判为缺失、未知profile字段名回显、超大整数逃逸稳定错误、并发双BACKTEST给失败namespace遗留guard等问题。第二轮修复后的预审仍发现孤儿`TRANSITIONING`可恢复成功、超大API版本错误不稳定，以及无helper兜底在已加载其他helper别名时仍有context getter远程窗口。第三轮修复将孤儿态直接转FAILED，要求精确`ModuleNotFoundError`的traceback证明helper本体尚未执行，并在context前拒绝任何已加载helper别名和旧remote portfolio；预审仅余策略期望API和helper实际API两个超大内部版本的稳定错误MINOR。第四轮对API比较两侧统一使用有界安全显示，162项相关测试、阻断级flake8、Python 3.8 AST、基线验证和Git格式检查均通过，契约、安全、对抗预审全部APPROVE。实现提交`aa04303`、记录提交`c336d24`的精确复审为REWORK：任意sys.modules键可隐藏真实helper模块对象；成功runtime的进程和namespace权威被同时清空时会被误判为fresh。v5工作树虽增加marker、模块对象扫描和普通值封套，预审仍复现四项权威全擦恢复、ModuleType子类漏检、等值协同替换/常量poison及无关模块假阳性，因此继续REWORK。v6使用非零generation和commit capsule绑定身份后，预审又发现capsule全局本身可等值替换、两份状态字典可保持identity协同原地改值。v7新增闭包identity锚、进程期FAILED latch和提交时不可变state snapshot，上述情况都在context前失败；183项相关测试及静态/语法/基线/格式验证通过，等待重新三路预提交复审。
- v7三路预审为2 APPROVE、1 REWORK：RPC lease对poison generation执行自定义比较后可能进入socket，reload初始化对poison旧计数调用`int()`会在FAILED/client清理前中断。v8改为精确整数快照后，三路复审仍全部REWORK：并发/中断reload旧client窗口、commit envelope缺少原helper token/generation、transition owner自定义比较、可替换锁identity和请求登记后inflight污染均可越过预期边界。v9在任何reload generation变化前先发布FAILED，使用闭包锚定模块代际、两把锁和request-token registry，commit capsule绑定原token/generation，所有transition字段先做精确类型校验，并在紧邻每次socket前验证多维lease；198项测试通过，但复审继续发现fake-equal registry、finally漂移、部分reload重复generation和lease到socket的TOCTOU，因此仍REWORK。
- v10将单向reload latch与socket attempt identity集合移入闭包，加入request隔离、异步清理和完整install reservation lease；213项测试通过。精确复审仍发现三个阻断：新代imports早于reload失败关闭、最终generation检查与实际返回存在并发窗口、BACKTEST隐式`str(profile/profile_module)`可执行回调并掩盖reservation篡改。
- v11在任何新代import前调用旧代闭包bootstrap，先关闭gate再等待attempt；commit capsule绑定namespace并在reload时即时清理record/安装guard，安装最终以`runtime -> owner -> socket`复核返回。其222项测试和已发生的审查仅保留为历史证据；后续reload/socket线性化REWORK已经修改工作树，因此v11测试结论和审查结论均自动失效，不能用于放行。
- 当前工作树采用三锁模型（runtime `RLock`、owner `Lock`、socket `RLock`）和`attempt token -> thread id`登记；lease检查与attempt登记在`runtime -> socket`临界区原子完成，connector随后通过独立最终permit进入且不持续持gate锁，reload关闭gate后等待已登记attempt；TLS包装、握手及request/mutation发送effect在socket锁内线性化，mutation在调用effect前发布handoff。同线程递归reload不再等待自身，而以`RuntimeReloadAbort`进入进程终止路径。reload gate仅是误用检测/fail-closed防线，生产禁止raw reload、热补丁及trace/profile/signal catch-and-resume，升级必须停止策略后冷启动全新进程。
- 首轮冻结预审的契约路发现MAJOR：`good_etf.py`只校验helper API版本、未校验稳定marker，API恰为1的错误同名模块可在runtime门禁前取得`globals/context`并返回伪状态；并发和部署两路结论随冻结失效。修复后策略在调用runtime前按精确内建类型和值校验helper marker，并新增missing/wrong/bool/poison四项fail-fast回归，错误helper的安装入口不会被调用。
- 第二轮冻结预审为2 APPROVE、1 REWORK：策略在helper门禁前用`str(MODE or '')`执行非普通MODE的`__bool__/__str__`，且在确认期望API为精确`int`前执行`actual != expected`，可触发poison expected的`__ne__`。修复后MODE先做精确`str`检查并仅用内建`str.strip/upper`归一化；API比较先短路验证expected/actual均为精确`int`。boolean/poison MODE与expected API均新增不执行魔术方法、不调用helper的回归。
- 第三轮冻结预审为1 APPROVE、2 REWORK：策略用普通属性访问调用`bt.install_strategy_runtime`，合法marker/API但缺入口的标准模块可执行PEP 562 `__getattr__`，任意callable入口也可在真正helper门禁前执行`__call__`。修复后只用已取得的模块`__dict__`和`dict.get`捕获入口，要求精确Python函数类型并调用局部引用；missing入口、poison模块`__getattr__`和poison callable均固定失败且不执行回调。runtime state也收紧为精确内建`dict`。
- 第四轮冻结预审为部署/文档APPROVE、契约REWORK、并发REWORK：helper虽返回精确`dict`，策略未验证完整state，伪state可把SHADOW降级成BACKTEST并触发聚宽原生order；`initialize`/`process_initialize`也在helper gate前调用了jqdata/platform对象。修复后策略逐字段验证state schema、identity、mode、run_type、flags、reason、profile_module和blocked_mutations，`_runtime_mode`拒绝任何篡改；两个生命周期入口的首条可执行语句均为runtime安装。
- 第5轮冻结前对抗探针又发现MAJOR：`_runtime_mode`先读取`g.bt_runtime`会执行平台属性协议，poison getter可先把SHADOW改成BACKTEST并触发原生order。修复后已验证模式封存在一次性闭包权威中，`g.bt_runtime`仅作展示且三个交易入口完全不读取它；当前MODE与闭包权威漂移固定失败。
- 第5轮正式冻结审查为1 APPROVE、2 REWORK；代码、并发和部署边界均未发现问题，两路REWORK源于同两项MINOR文档漂移：当前候选摘要仍停在首轮marker阶段，现状文档日期仍为2026-08-08。两处现已更正，前五轮历史均保留，第6轮冻结待执行。
- 第6轮正式冻结三路均因同一MINOR文档漂移REWORK：`00-current-state.md`正文仍称处于第5轮，与第6轮PENDING记录矛盾；代码与并发路径没有新finding。该句现改为不随轮次失效的S01 IN_PROGRESS/两阶段复审未完成表述，第7轮冻结待执行。
- 第7轮预提交冻结的契约、并发/对抗、部署/文档三路均APPROVE；候选提交`a94aa12060c5e8cef479224952e302eeac99f37d`随后再次通过三路精确SHA终审，均无BLOCKER/MAJOR/MINOR，起止工作树clean。
- 当前阶段共新增17个策略回归。最新完整目标测试为295 passed（runtime+deadlock 204、remote helper 35、strategy contract 56），并发/死锁定向矩阵20 passed；Python 3.8 AST（6个变更Python文件）、阻断级flake8、S00 baseline validator和Git格式检查均PASS，`git diff --check`仅有CRLF提示。S01状态为DONE。
- S02实现提交`3b54a4a7178fb36ab9f85de22a648bb08bd0448b`已通过三路预提交及精确SHA终审，起止HEAD一致且工作树clean。目标矩阵320 passed；真实策略与契约probe的strict mypy/pyright、Python 3.8 AST、阻断级flake8、S00 baseline和commit格式均PASS。
- 最新setup在第三个全新空venv完成`pip>=21.3`引导、editable安装和严格检查；`.pth`只写入目标`purelib`，普通Python可解析`jqdata`和helper。S02状态为DONE。
- 尚未开始真实StrategyLedger实现。
- 尚未轮换外部token/Webhook；这是需要用户在对应平台执行的外部动作。

## 最近完成slice

`S03 JoinQuant Validation and Export`（DONE）

## 当前slice

`S04 Strategy Domain and Schema`（IN_PROGRESS）

- 已实现固定白名单导出器、确定性manifest、私有profile只读校验、clean-room smoke和127项S03回归。
- 多轮冻结前审查已依次推动修复：单次不可变源码快照、严格契约唯一绑定和精确类型、`TYPE_CHECKING`来源/重绑定、相对与动态服务器包导入、危险builtin别名、敏感字段组合构造、私有profile占位值、目标路径reparse/断链、动态namespace与契约字段改写。
- 最近一轮对抗审查复现helper可经`getattr(..., '__dict__')`、直接下标及`object.__getattribute__`保存当前模块namespace后用动态键改写API版本；导出器现统一拒绝保存或修改原始对象namespace，并把策略/helper内两处合法只读模块查询改为不保存namespace对象的单键读取。
- 首次10文件正式冻结又发现未绑定`dict.__setitem__/update/pop`把`globals()`或原始`__dict__`作为首参数时可绕过接收者检查；该轮正式REWORK并失效。修复后mutator统一解析真实修改目标，`dict.*`/`builtins.dict.*`以首参数为目标，5个computed-key回归均拒绝。
- 第二次正式冻结发现BLOCKER：`object.__setattr__`和模块`.__setattr__`可用computed field把静态BACKTEST/API=1改为运行时LIVE/API=2；另有派生dict类型未绑定mutator以及helper静态`__import__`绕过角色白名单两个MAJOR。该轮正式REWORK并失效。修复后所有namespace mutator对接收者和参数中的动态/raw namespace失败关闭，四种属性mutator调用形态统一要求静态字段并保护契约字段，静态`__import__`复用角色白名单，只有helper的`profile_module`变量保留受控动态导入。
- 第三次正式冻结发现两个MAJOR：通过`getattr`/`object.__getattribute__`直接取得`__setattr__/__delattr__`后调用仍可绕过字段门禁；`__import__(name=...)`和`**kwargs`绕过仅看位置参数的导入检查。该轮正式REWORK并失效。修复后统一静态解析直接/属性/getter形式的被调函数名与owner，属性修改字段索引按bound/unbound语义确定；动态导入统一规范化首位置参数或唯一`name=`，歧义、缺失和`**kwargs`失败关闭。
- 第四次正式冻结发现两个MAJOR：helper可经`__builtins__['__import__']`绕过动态导入白名单；函数体或不可达分支中的`TYPE_CHECKING`导入会被误认成有效模块绑定并让导出策略运行时报NameError。该轮正式REWORK并失效。修复后所有角色禁止直接访问`__builtins__`，并额外拒绝动态namespace直接下标及其敏感内建键读取；`TYPE_CHECKING`只接受无条件模块顶层的单次显式导入，拒绝嵌套、条件和typing通配符导入。
- 第五次正式冻结发现MAJOR：`dict.__getitem__(globals(), '__builtins__')`和bound/getter等价形式绕过只覆盖`[]/.get`的敏感namespace读取门禁。该轮正式REWORK并失效。修复后复用静态callable解析，统一识别bound/unbound/getter形式的`__getitem__`，只要目标或参数含动态namespace且参数含敏感内建键即失败关闭。
- 所有旧冻结摘要在后续工作树变更后均已失效，不构成放行证据；只接受下一次10文件SHA256冻结的三路一致APPROVE。
- 第五次冻结候选的444项结果已随REWORK失效；本次修复后S03定向127 passed、联合矩阵447 passed（3个既有warning），strict mypy/pyright、完整/阻断级flake8、Python 3.8 AST、S00 baseline、`git diff --check`、validate-only及全新目录真实导出均PASS。

当前边界：

- 只导出固定白名单中的策略、独立helper和示例profile；非bundle文件字节必须与一次读取形成的不可变源快照完全相同。
- AST门禁按角色限制导入和危险能力；所有名称绑定、受测`globals/locals/vars`与静态保留键改写、`vars(obj)`/原始`__dict__`可写namespace、helper契约改写、`TYPE_CHECKING`来源/重绑定、相对/静态可判定动态服务器包导入、危险别名、敏感命名/位置参数和组合Webhook均有拒绝回归。该扫描不是完备别名/数据流证明；helper允许其远程客户端所需网络模块，但策略/profile不得自行访问网络、文件或进程。
- 敏感扫描只接受空值或明确占位符，不把真实host、token、Webhook、账户值写入产物或manifest；它是防误提交门禁，不是完备秘密检测器。
- 私有profile可通过显式`--private-profile`只读校验assignment-only/schema/字段类型/范围及profile/strategy契约；不执行、不复制、不hash或输出秘密。
- 导出目录必须不存在且路径不得经过含断链在内的symlink/junction/reparse point；manifest确定性记录角色、相对路径、字节数、SHA256和部署mode契约，不宣称产物已获真实资金授权。
- 部署声明必须先在受控源码中确定并审查；导出后及聚宽侧禁止再次手工修改，否则文件hash和manifest不再代表实际部署物。
- clean-room仅用导出物完成Python 3.8语法、导入及缺helper/profile/版本不匹配smoke；聚宽真实行为仍留到S18。

## S03收口

- 第六次10文件冻结的部署/文档与独立功能审查均为APPROVE；第三个审查代理因平台误判未产出结论，主审使用相同冻结哈希、文件集合、格式和测试证据补足合同核对。
- 实现提交：`224a68195eeff11a542885344957132a294c5399`。
- 两路独立精确SHA终审均APPROVE：提交只含冻结10文件，127项S03测试和447项联合矩阵通过，双份全新导出字节一致，工作树clean。
- S03决定：DONE。该决定只放行“可上传候选”的生成与验证，不放行真实资金；S04至S20仍未完成。

## 下一步

实现S04最小领域模型与两阶段SQLite迁移，验证整数尺度、约束、重复迁移、旧版本升级和失败回滚。根据D021，不为可信个人策略增加新的同进程对抗门禁。

## S04当前候选

- 新增`bullet_trade.server.strategy`领域模型：现金池、策略账户、持仓/lot、组合意图、订单、成交、账本项、事件和对账结果。
- 金额、价格和NAV使用固定整数尺度；入口拒绝float并使用`ROUND_HALF_UP`，成交/事件时间转为Asia/Shanghai，lot显式保存可卖交易日。
- SQLite迁移v1建立账户、现金池、账本和持仓；v2建立意图、订单、成交、事件、outbox、对账和扩展钩子。文件库启用WAL/FULL/外键。
- 12项S04定向测试通过；加入既有scheduler回归后24 passed。新模块完整flake8、Python 3.8 AST、targeted mypy、targeted pyright和S00 baseline均PASS。
- 首次收集因默认`jqdatasdk`未安装失败；按仓库约定使用离线easy_tdx stub后通过。这暴露既有顶层数据源初始化耦合，但不在S04扩大重构范围。
- 修复两个包入口的Python 3.8运行注解：`list[str]`改为兼容写法，避免StrategyLedger导入在目标版本先失败。
- 当前等待实现候选代码审查；尚未接入服务器、分配1万元或处理真实成交。
- 首轮只读审查为REWORK：组合意图/事件/对账对象保留调用方可变Mapping，迁移历史不绑定SQL且未核对`user_version`。候选现改为构造时递归冻结JSON数据，迁移记录SHA-256并校验双版本源；新增3项回归后重新审查。

## 恢复检查表

新session继续前依次执行：

1. 阅读本文件与 `04-slices.md`。
2. 检查 `git branch --show-current`。
3. 检查 `git status --short`，不得覆盖用户未说明的修改。
4. 查看当前slice最近一次实现和review记录。
5. 仅在当前slice出口条件满足后更新下一个slice为IN_PROGRESS。
