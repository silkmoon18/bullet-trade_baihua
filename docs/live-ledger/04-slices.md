# 实施 slices

## 1. 强制执行规则

每个实现slice严格采用：

```text
确认依赖和当前工作树
→ 状态改为IN_PROGRESS
→ 实现最小闭环并形成implementation commit
→ 运行精确记录的定向测试和必要回归
→ 独立审查implementation commit/diff
→ 修复findings并重新测试
→ 形成final candidate SHA
→ 独立复审最终SHA或精确修复diff
→ 记录证据并标记DONE
→ 才能进入下一实现slice
```

状态：`PENDING / IN_PROGRESS / REVIEW / REWORK / DONE / BLOCKED`。

每次review必须记录：reviewer、被审查commit或diff范围、精确测试命令和结果、findings、修复提交、最终复审结论、残余风险。审查过的代码发生变化后，旧结论自动失效。

真实交易日门禁与自动化实现分离。S18-S20可以因等待交易日、私有远端、凭据轮换或用户审批保持BLOCKED；不得用文档或mock结果替代真实证据。

## 2. 首版范围

首版仅支持：

- 专用物理账户。
- 单一策略。
- 禁止人工交易和其他策略交易。
- 无融资融券的现金多头账户。

共享账户、多策略同标的归属和人工交易池只在schema中预留扩展点，不属于本轮验收。任何无法归属的订单、成交、现金或持仓都必须HARD阻断。

## 3. Slice总览

| Slice | 名称 | 状态 | 依赖/结果 |
|---|---|---|---|
| S00 | Repository Baseline and Documentation | DONE | 检查点、最新基线、只读upstream、脱敏迁移、事实文档 |
| S01 | JoinQuant Source and Profile Contract | IN_PROGRESS | 同源策略、模式/profile、helper API兼容、fail-fast |
| S02 | JoinQuant Typings and IDE | PENDING | 严格类型桩、IDE导入、目标Python/API矩阵 |
| S03 | JoinQuant Validation and Export | PENDING | AST校验、敏感扫描、clean-room导入、原样导出 |
| S04 | Strategy Domain and Schema | PENDING | 整数尺度、状态、不变量、schema和迁移 |
| S05 | Transactional Repository | PENDING | 事务、CAS、事件序列、并发和重放基础 |
| S06 | Broker Capability Contract | PENDING | QMT标识、订单/成交唯一性、费用、lookback和unknown能力 |
| S07 | Persistent Idempotency and Outbox | PENDING | 请求幂等、operation、outbox、lease、unknown恢复 |
| S08 | Capital Allocation Ledger | PENDING | 未分配池、初始1万元、冻结/释放、显式资金流 |
| S09 | Fill Booking and Position Lots | PENDING | 买卖成交、费用、lot、T+1、成本和重复fill no-op |
| S10 | Valuation and Atomic Snapshot | PENDING | mark来源/时间戳、NAV、快照版本和陈旧价规则 |
| S11 | Broker Ingest and Reconciliation | PENDING | 跨日重扫、游标、quarantine、HARD阻断和readiness |
| S12 | Target Portfolio Planner | PENDING | NAV目标、整手、working exposure、费用缓冲和delta |
| S13 | Execution Orchestrator and Baseline Risk | PENDING | 卖后买、部分成交、unknown、恢复、kill switch |
| S14 | Strategy API and Authorization | PENDING | strategy.*、scope、feature握手、审计和统一错误 |
| S15 | JoinQuant Live Runtime and good_etf | PENDING | PortfolioView、事件恢复、record、组合提交和策略重构 |
| S16 | Performance and Observability | PENDING | TWR/回撤/费用、结构化日志、指标和告警 |
| S17 | Automated E2E and Deployment Artifacts | PENDING | chaos、恢复、Windows服务、备份和runbook |
| S18 | JoinQuant/Shadow Release Gate | BLOCKED | 真实平台smoke和至少5交易日只读证据 |
| S19 | QMT Simulation Release Gate | BLOCKED | 至少5交易日模拟、凭据/TLS/对账证据 |
| S20 | Small Live Approval Gate | BLOCKED | 用户审批、专用账户、小额实盘和扩资门禁 |

## S00：Repository Baseline and Documentation

### 交付

- `bt_quant@e6462dd`可恢复检查点。
- fetch/tags证据证明最新基线为`v0.9.2/be0451b`。
- 独立开发分支和脱敏策略基线。
- 迁移manifest及来源/目标blob hash。
- 官方remote为只读`upstream`且push禁用；私有origin缺失时明确local-only。
- 现状、计划、决策、session和本文件。
- `.idea/`、缓存、日志、runtime、数据库sidecar、本地profile和导出产物不会入库。
- 明确迁移策略与上游helper当前不兼容，不宣称已经可运行。

### 验证

- `git fetch --prune upstream`及commit/tag核验。
- `git diff --check v0.9.2..HEAD`。
- Markdown相对链接检查。
- 敏感信息特征扫描。
- `python -m py_compile strategies/joinquant/good_etf.py`。
- 原仓库检查点和迁移blob可解析。

### 首次审查与修复记录

```text
Slice: S00
Implementation commit: pre-squash local-only S00 series；最终以压平提交为准
Reviewer: /root/review_s00
Reviewed commit/diff: v0.9.2..S00 candidate，以及迁移策略与bt_quant@e6462dd只读对比
Tests (initial):
  - git diff --check v0.9.2..HEAD -> 初次FAIL，发现Markdown EOF空行；修复后PASS
  - python scripts/validate_live_ledger_baseline.py --bt-quant E:\dev\pycharm\bt_quant -> 初次发现可达历史含旧敏感值，触发压平
Findings: 旧helper API不兼容未明确、EOF格式、runtime忽略不足、公共remote可误推、状态文档过期
Fix commit: 7085155（sanitized squash commit）
Retest:
  - git diff --check v0.9.2..7085155 -> PASS
  - git show --check --oneline --stat 7085155 -> PASS
  - python scripts/validate_live_ledger_baseline.py --bt-quant E:\dev\pycharm\bt_quant -> S00_BASELINE_CHECK_OK
Final code candidate SHA: 7085155
Final reviewer: /root/review_s00_final
Final review result: APPROVE；可达历史、validator、diff/show check、helper差异和元数据一致性通过
Residual risks/external blockers: 用户私有origin URL未提供，当前仓库local-only；外部token/Webhook尚待用户轮换
Decision: DONE
```

## S01：JoinQuant Source and Profile Contract

### 交付

- 策略只保留`PROFILE`、`MODE`、稳定`STRATEGY_ID`和业务参数。
- BACKTEST/SHADOW/LIVE模式定义及非法组合fail-fast。
- 无密钥profile schema、example和本地私有profile忽略规则。
- 迁移策略不再调用旧helper扩展参数/函数；统一使用最小版本化runtime facade，S01不启用旧`install_jq_compat`实盘接管。
- 缺helper、helper版本不匹配、缺profile、空token的明确错误。
- 策略文件不导入`bullet_trade.*`服务器内部包。

### 验证

- 本地导入策略不会因旧helper API立即报错。
- BACKTEST不连接远端；SHADOW/LIVE在profile导入前即禁止下单/撤单并清除旧客户端；LIVE只校验配置，不连接服务器、不接管portfolio，仅安装本地fail-closed函数。
- 策略源码无host、token、Webhook和账户明文。

### 首次审查与修复记录

```text
Slice: S01
Implementation commit: 655b3c9
Reviewers: /root/review_s01_runtime_security；/root/review_s01_strategy_contract
Reviewed commit/diff: f6a73b0..655b3c9
Initial result: REWORK
Findings:
  - BLOCKER: SHADOW可被晚到configure、namespace重绑和缓存broker绕过，幂等安装不修复postcondition
  - MAJOR: SHADOW继承旧broker后只做代理，读取账户仍会触达socket
  - MAJOR: S01 LIVE先安装真实兼容层、替换portfolio/订单函数，再由策略报错
  - MAJOR: 入选但超配的持仓减仓错误复用买入上浮限价，且日志误报为买入
  - MINOR: SHADOW docstring与实际无连接语义矛盾；profile等待参数缺少合理上限
Fix commit: 17f8eb2
Fixes:
  - 进程模式、namespace、公开helper、RemoteBrokerClient和ShortLivedClient多层fail-closed
  - SHADOW清除旧client并在幂等安装时重建保护；远程context要求干净进程重启
  - LIVE改为无远程连接和portfolio接管的profile校验，仅保留本地fail-closed保护；good_etf在helper调用前直接拒绝LIVE
  - 目标市值按当前持仓判断增持/减持；仅增持使用买入上浮限价
  - retries/timeout增加上下限并同步文档和边界测试
Retest:
  - $env:PYTHONDONTWRITEBYTECODE='1'; $env:DEFAULT_DATA_PROVIDER='tushare'; $env:DATA_CACHE_DIR=''; python -X utf8 -m pytest tests/test_jq_remote_helper.py tests/test_jq_strategy_runtime.py tests/strategies/test_good_etf_contract.py -q -o addopts='' -p no:cacheprovider -> 104 passed
  - python -X utf8 -m flake8 tests/test_jq_strategy_runtime.py tests/strategies/test_good_etf_contract.py jq_runtime/jq_runtime_config.example.py --max-line-length=120 -> PASS
  - python -X utf8 scripts/validate_live_ledger_baseline.py --bt-quant E:\dev\pycharm\bt_quant -> S00_BASELINE_CHECK_OK
  - git diff --check -> PASS
Final code candidate SHA: 335a707
Final review result: REWORK；详见下一轮记录
Decision: REWORK
```

### 最终候选复审与第二次修复记录

```text
Slice: S01
Reviewed commit: 335a7077e5e53eebe7eeefe4167fdce9370b3045
Reviewers: /root/review_s01_final_security；/root/review_s01_final_strategy
Review result: REWORK
Findings:
  - BLOCKER: SHADOW/LIVE在导入Python profile之后才设置进程门禁；导入副作用可调用configure并触达socket
  - BLOCKER: profile缺失或校验失败时active仍为空、旧client仍可复用，后续configure继续可用
  - MAJOR: 继承远程兼容context的SHADOW失败路径先恢复原生下单函数再抛错，namespace可继续mutation
Fix commit: 5bca3702e40dd8751d6df53092fa747115179865
Fixes:
  - SHADOW/LIVE在任何run_type、契约或profile校验前先设置进程门禁、清client并保护namespace
  - profile导入、校验及远程context拒绝的任意异常统一进入FAILED，重新安装保护并删除过期runtime state
  - LIVE也显式安装本地fail-closed函数；SHADOW/LIVE幂等安装均修复被重绑的namespace
  - 远程运行安装一旦失败，同一进程不得重试或切回BACKTEST，必须使用干净进程重启
  - 明确Python profile是可信可执行代码而非沙箱，门禁只覆盖BulletTrade及策略namespace入口
Retest:
  - $env:PYTHONDONTWRITEBYTECODE='1'; $env:DEFAULT_DATA_PROVIDER='tushare'; $env:DATA_CACHE_DIR=''; python -X utf8 -m pytest tests/test_jq_remote_helper.py tests/test_jq_strategy_runtime.py tests/strategies/test_good_etf_contract.py -q -o addopts='' -p no:cacheprovider -> 111 passed
  - python -X utf8 -m flake8 tests/test_jq_strategy_runtime.py tests/strategies/test_good_etf_contract.py jq_runtime/jq_runtime_config.example.py --max-line-length=120 -> PASS
  - python -X utf8 scripts/validate_live_ledger_baseline.py --bt-quant E:\dev\pycharm\bt_quant -> S00_BASELINE_CHECK_OK
  - git diff --check -> PASS
Exact review candidate: 354ecf3230b28d6569a71fe548c7234847b591bf
Final review result: REWORK；契约与安全审查APPROVE，对抗审查发现新的BLOCKER/MAJOR，详见下一轮记录
Decision: REWORK
```

### 精确候选第三次对抗审查与v3修复记录

```text
Slice: S01
Reviewed commit: 354ecf3230b28d6569a71fe548c7234847b591bf
Reviewers: /root/review_s01_exact_contract_v2；/root/review_s01_exact_security_v2；/root/review_s01_exact_adversarial_v2
Review result: REWORK（2 APPROVE，1 REWORK；任一REWORK即不得放行）
Findings:
  - BLOCKER: legacy compat originals、直接别名和跨reload旧portfolio可绕过SHADOW门禁
  - MAJOR: namespace缓存被当作权威、helper reload和并发不同契约可发布不一致状态
  - MAJOR: ShortLivedClient入口检查与建socket之间存在切换TOCTOU
  - MAJOR: 污染进程切回BACKTEST仍可能保留旧wrapper/context/client却返回orders_enabled=True
  - MAJOR: profile导入的SystemExit/KeyboardInterrupt可携带敏感文本逃逸
  - MAJOR: 篡改namespace公开state可伪造LIVE/orders_enabled/production_ready
Fix commit: f2538270d845da65d0835ae6a2a34b5c406ce390
Fixes in current candidate:
  - runtime lock、原子transition owner、在途RPC lease和contract generation共同线性化安装/请求；在途请求使安装FAILED且禁止后续重试
  - helper instance token、module generation、进程signature/canonical state和严格namespace envelope拒绝reload恢复及公开state篡改
  - 任何legacy compat痕迹、旧remote portfolio、已发布client或污染BACKTEST均隔离并要求干净进程
  - legacy originals被清空；标准交易名、直接/import alias、partial、wrapped和直接closure引用被本地guard
  - profile import BaseException统一脱敏；configure/runtime调用跨reload不能重新发布client或虚假成功状态
  - runtime只接受普通字符串mode和真实模块globals字典；SHADOW/LIVE在owner登记时即安装TRANSITIONING门禁
  - poison callable/dict/str/partial子类不能阻断基础FAILED guard；并发/递归不同namespace也会先保护再拒绝
Retest:
  - $env:DEFAULT_DATA_PROVIDER='qmt'; $env:PYTHONDONTWRITEBYTECODE='1'; python -X utf8 -m pytest tests/test_jq_remote_helper.py tests/test_jq_strategy_runtime.py tests/strategies/test_good_etf_contract.py -q -o addopts='' -p no:cacheprovider -> 137 passed
  - python -X utf8 -m flake8 helpers/bullet_trade_jq_remote_helper.py tests/test_jq_strategy_runtime.py --select=E9,F63,F7,F82 -> PASS
  - Python 3.8 ast.parse(feature_version=(3, 8)) for helper/runtime tests -> PY38_AST_OK
  - python -X utf8 scripts/validate_live_ledger_baseline.py --bt-quant E:\dev\pycharm\bt_quant -> S00_BASELINE_CHECK_OK
  - git diff --check -> PASS（仅工作树CRLF转换提示）
Pre-commit reviewer: /root/audit_s01_v3_contract -> APPROVE；helper/test冻结哈希与上述137项验证一致，无BLOCKER/MAJOR/MINOR
Final candidate SHA: 34944b32692744db0c5d8482508a0fed8d8df5c7
Final reviewers: /root/review_s01_v3_exact_contract；/root/review_s01_v3_exact_security；/root/review_s01_exact_adversarial_v2
Final review result: REWORK（1 APPROVE，2 REWORK；任一REWORK即不得放行）
Final findings:
  - MAJOR: good_etf的BACKTEST本地分支绕过helper，旧client/remote portfolio污染仍可返回orders_enabled=True
  - MAJOR: helper在BACKTEST读取context属性前未建立进程门禁，属性副作用可通过缓存client发出远程mutation并返回虚假成功
  - MAJOR: profile import脱敏错误仍通过__context__保留含凭据的原异常对象
  - MINOR: import成功后的PROFILE_SCHEMA_VERSION/PROFILES属性异常不在脱敏边界内
Residual risks/external blockers: 任意其他模块、容器、callable对象或局部变量中预存的原生函数引用无法由Python撤销；profile仍是可信代码而非沙箱；真实聚宽smoke在S18
Decision: REWORK
```

### v4边界修复与第四次精确候选记录

```text
Slice: S01
Reviewed commit: 34944b32692744db0c5d8482508a0fed8d8df5c7
Review result: REWORK
Fix commit: aa043034760e42617be795b318cb70d2b22af70a
Fixes in current candidate:
  - 三个合法模式在原子owner登记时先建立进程级TRANSITIONING门禁，再读取任何context属性
  - good_etf在helper存在时连BACKTEST也统一调用版本化入口；仅helper缺失的纯聚宽BACKTEST走本地兜底
  - BACKTEST经helper检查旧client、remote portfolio及legacy compat污染，但不替换聚宽原生下单函数、不导入profile、不连接网络
  - profile导入和导入后属性读取统一安全快照；脱敏错误离开except后抛出，__context__/__cause__不保留秘密异常
  - PROFILES、单个profile及字段值只接受精确内建类型，拒绝通过魔术方法执行的poison子类
  - 通用configure/runtime跨reload脱敏错误同样在except外抛出并断开异常链
Retest:
  - $env:DEFAULT_DATA_PROVIDER='qmt'; $env:PYTHONDONTWRITEBYTECODE='1'; python -X utf8 -m pytest tests/test_jq_remote_helper.py tests/test_jq_strategy_runtime.py tests/strategies/test_good_etf_contract.py -q -o addopts='' -p no:cacheprovider -> 147 passed
  - python -X utf8 -m flake8 helpers/bullet_trade_jq_remote_helper.py strategies/joinquant/good_etf.py tests/test_jq_strategy_runtime.py tests/strategies/test_good_etf_contract.py --select=E9,F63,F7,F82 -> PASS
  - Python 3.8 ast.parse(feature_version=(3, 8)) for changed Python files -> PY38_AST_OK
  - python -X utf8 scripts/validate_live_ledger_baseline.py --bt-quant E:\dev\pycharm\bt_quant -> S00_BASELINE_CHECK_OK
  - git diff --check -> PASS（仅工作树CRLF转换提示）
First pre-commit reviewers: /root/audit_s01_v4_security；/root/audit_s01_v4_contract；/root/audit_s01_v4_adversarial
First pre-commit result: REWORK（三方均REWORK）
First pre-commit findings:
  - MAJOR: 被篡改为未知值的进程active state未被固定blocked set覆盖，BACKTEST context属性仍可让缓存client触达socket
  - MAJOR: helper缺失兜底不检查旧remote portfolio，仍可返回orders_enabled=True
  - MAJOR: helper内部ImportError/依赖缺失被顶层宽泛ImportError误判为helper未安装并静默降级
  - MAJOR: 未知profile字段名原样进入错误文本，字段名自身含凭据时泄露
  - MAJOR: 并发两个不同namespace的BACKTEST会给失败方永久安装TRANSITIONING guard，与最终BACKTEST状态冲突
  - MINOR: 超大精确整数可在schema错误格式化或math.isfinite中逃逸为ValueError/OverflowError
Second fixes in current worktree:
  - 任意未知/非普通字符串active state在owner登记时直接转FAILED，固定错误不读取对象文本；remote/mutation gate改为非None一律阻断
  - 只有目标helper自身的精确ModuleNotFoundError才允许BACKTEST兜底；内部导入错误原样中止，兜底也拒绝稳定marker/旧类特征remote portfolio
  - 未知字段使用固定错误且不回显键；schema版本仅回显有界整数，数值先做类型、有限性和范围检查再转换
  - 新增transition mode；只有两个纯BACKTEST竞争时失败方保留原生函数，涉及任一远程模式仍保护失败namespace
Second retest:
  - $env:DEFAULT_DATA_PROVIDER='qmt'; $env:PYTHONDONTWRITEBYTECODE='1'; python -X utf8 -m pytest tests/test_jq_remote_helper.py tests/test_jq_strategy_runtime.py tests/strategies/test_good_etf_contract.py -q -o addopts='' -p no:cacheprovider -> 155 passed
  - fatal flake8 for helper/strategy/runtime tests -> PASS
  - Python 3.8 AST -> PY38_AST_OK
  - baseline validator -> S00_BASELINE_CHECK_OK
  - git diff --check -> PASS（仅工作树CRLF转换提示）
Second pre-commit reviewers: /root/audit_s01_v4_security；/root/audit_s01_v4_contract；/root/audit_s01_v4_adversarial
Second pre-commit result: REWORK（1 APPROVE，2 REWORK）
Second pre-commit findings:
  - MAJOR: owner缺失的孤儿TRANSITIONING被下一次BACKTEST恢复为orders_enabled=True，而非FAILED
  - MAJOR: 无helper兜底读取context前不检查其他模块名下仍加载的helper，getter可用缓存client触达socket
  - MINOR: helper本体执行后主动抛同名ModuleNotFoundError仍可能被误判为目标模块缺失
  - MINOR: helper expected_api_version和策略读取的helper API version为超大整数时，错误格式化逃逸为ValueError
Third fixes in current worktree:
  - owner为空时只有BACKTEST/SHADOW/LIVE_BLOCKED/FAILED是稳定状态；孤儿TRANSITIONING和其他值在context前统一转FAILED
  - helper缺失兜底同时要求精确ModuleNotFoundError、目标name和仅调用点单帧traceback；helper本体开始执行后的同名异常继续抛出
  - 兜底在任何context getter前扫描sys.modules并拒绝顶层或包别名helper；随后仍检查稳定marker、继承marker和旧类/module特征
  - helper与策略API版本错误只回显有界精确整数，其他值固定显示<invalid>
Third retest:
  - $env:DEFAULT_DATA_PROVIDER='qmt'; $env:PYTHONDONTWRITEBYTECODE='1'; python -X utf8 -m pytest tests/test_jq_remote_helper.py tests/test_jq_strategy_runtime.py tests/strategies/test_good_etf_contract.py -q -o addopts='' -p no:cacheprovider -> 160 passed
  - fatal flake8 for helper/strategy/runtime tests -> PASS
  - Python 3.8 AST -> PY38_AST_OK
  - baseline validator -> S00_BASELINE_CHECK_OK
  - git diff --check -> PASS（仅工作树CRLF转换提示）
Third pre-commit reviewers: /root/audit_s01_v4_security；/root/audit_s01_v4_contract；/root/audit_s01_v4_adversarial
Third pre-commit result: REWORK（2 APPROVE，1 REWORK）
Third pre-commit findings:
  - MINOR: good_etf内部期望API版本被篡改为超大整数时，expected错误格式化逃逸为ValueError
  - MINOR: helper自身API版本被篡改为超大整数时，actual错误格式化逃逸为ValueError
Fourth fixes in current worktree:
  - 策略与helper的API版本比较对expected和actual两侧都只回显有界精确整数，其他值固定显示<invalid>
Fourth retest:
  - $env:DEFAULT_DATA_PROVIDER='qmt'; $env:PYTHONDONTWRITEBYTECODE='1'; python -X utf8 -m pytest tests/test_jq_remote_helper.py tests/test_jq_strategy_runtime.py tests/strategies/test_good_etf_contract.py -q -o addopts='' -p no:cacheprovider -> 162 passed
  - fatal flake8 for helper/strategy/runtime tests -> PASS
  - Python 3.8 AST -> PY38_AST_OK
  - baseline validator -> S00_BASELINE_CHECK_OK
  - git diff --check -> PASS（仅工作树CRLF转换提示）
Fourth pre-commit reviewers: /root/audit_s01_v4_security；/root/audit_s01_v4_contract；/root/audit_s01_v4_adversarial
Fourth pre-commit result: APPROVE（三方均无BLOCKER/MAJOR/MINOR；安全专项20 passed，对抗专项11 passed，契约版本专项4 passed）
Final candidate SHA: PENDING
Final reviewers: PENDING（三方精确SHA复审）
Final review result: PENDING
Residual risks/external blockers: helper缺失兜底会拒绝sys.modules中的helper别名和remote portfolio标记，但Python无法发现已从模块表删除后藏在任意局部变量/容器中的旧client；该路径只接受全新可信聚宽context，部署规约禁止卸载helper后同进程降级。profile仍是可信代码而非沙箱；真实聚宽smoke在S18
Decision: IN_PROGRESS
```

## S02：JoinQuant Typings and IDE

### 交付

- `jqdata.pyi`、helper `.pyi`和typing-only Context/Portfolio/Position/Snapshot模型。
- pyi与runtime API同步测试，导出符号和签名漂移即失败。
- 独立严格类型配置，不受项目全局`ignore_missing_imports`掩盖。
- 记录聚宽目标Python、pandas/numpy及使用API兼容矩阵；未知版本明确为待平台核验。
- fresh venv/PyCharm源码路径配置说明和自动化setup。

### 验证

- 全新venv中editable install后，策略范围严格mypy/pyright通过。
- 常用`context.portfolio`、Position和helper返回值具有补全。
- Python目标版本语法编译通过。

## S03：JoinQuant Validation and Export

### 交付

- AST校验：禁止服务器内部导入、危险文件/进程/网络用法和不支持语法。
- 敏感信息扫描和配置引用检查。
- 导出工具输出原样策略、helper、example profile和manifest。
- 非bundle模式源策略和导出策略hash一致。
- clean-room目录导入测试以及helper/profile缺失、版本不匹配fail-fast测试。
- 明确：自动化通过只表示“可上传候选”，真实聚宽运行证据在S18。

### 验证

- 全新临时目录只使用导出物完成语法、导入和mock runtime smoke。
- 导出包不含token、Webhook、日志、缓存、数据库或服务器内部模块。

## S04：Strategy Domain and Schema

### 交付

- 账户、现金池、ledger entry、position/lot、intent、order、fill、event、outbox和reconcile领域状态。
- 货币、价格和数量整数尺度及舍入规则；禁止float记账。
- 核心不变量：`reserved<=cash`、`available>=0`、lots合计等于position、版本/事件序列单调。
- 交易日、Asia/Shanghai时间、T+1结算和跨日未终态订单字段。
- capital flow、公司行动、分红拆分的schema钩子，不在本slice实现业务。
- 版本化SQLite schema和向前迁移；回滚采用备份恢复策略时必须文档化。

### 验证

- 空库建库、重复迁移、旧schema升级和失败恢复测试。
- 数据库约束拒绝负数和非法状态。

## S05：Transactional Repository

### 交付

- repository接口和SQLite WAL/FULL实现。
- 单writer策略、事务边界、CAS/ledger_version、单调event_seq。
- 行级语义锁或SQLite等价事务策略。
- append-only读取、快照重建和测试隔离。

### 验证

- 并发更新只有一个CAS成功。
- crash前后事务全有或全无。
- ledger replay得到相同物化状态。

## S06：Broker Capability Contract

### 交付

- MiniQMT/BigQMT adapter对client tag/remark、broker order ID、trade ID、side、费用和状态的能力矩阵。
- 当日/跨日orders、trades、working order查询lookback和ID稳定性定义。
- `SUBMIT_UNKNOWN`可用的查询路径和无法恢复时的quarantine语义。
- 缺side的trade通过order映射；无法映射不得猜测。

### 验证

- adapter合同测试覆盖重复/乱序fill、remark roundtrip、断连、跨日working order和费用字段。
- 能力不足的adapter显式拒绝`strategy_ledger_v1`。

## S07：Persistent Idempotency and Outbox

### 交付

- `(strategy_id, endpoint, idempotency_key)`唯一及payload hash冲突。
- operation持久状态和原响应重放。
- 业务变更与outbox同事务；worker claim、lease和单物理账户执行者。
- 提交前持久client tag；响应丢失进入`SUBMIT_UNKNOWN`并按S06能力恢复。

### 验证

- 100个同key并发只创建一个operation/outbox项。
- 相同key不同payload明确冲突。
- 各crash point重启不产生第二次外部提交。

## S08：Capital Allocation Ledger

### 交付

- `UNALLOCATED_CASH_POOL`及物理账户首次校准。
- 从未分配池原子分配初始1万元，重复ensure返回原账户。
- reserve/release、显式allocate/withdraw和审计原因。
- 重启不重新分配；修改聚宽初始资金不静默改账。

### 验证

- 并发创建同策略只分配一次。
- 两策略并发不能超分配，即使当前首版API只开放一个策略。
- `cash-reserved=available`始终成立。

## S09：Fill Booking and Position Lots

### 交付

- broker trade ID或稳定fingerprint唯一去重。
- 买卖fill单事务更新现金、冻结、lot、position、费用和realized PnL。
- 部分成交、终态释放、T+1可卖和跨交易日处理。
- append-only replay及公司行动扩展钩子。

### 验证

- 计划买3000、实成2000、费用5，分别验证余单working和canceled。
- 重复fill no-op、累计成交越界quarantine。
- 随机事件序列验证资产和lot不变量。

## S10：Valuation and Atomic Snapshot

### 交付

- mark price来源、`as_of`、freshness和陈旧价策略。
- NAV、positions value、cash/reserved/available和原子快照version。
- 行情缺失/过期时readiness和fail-closed规则。

### 验证

- 相同事件和mark重算NAV一致。
- 账户与持仓不发生非原子拼接快照。
- 陈旧价格不能用于新调仓。

## S11：Broker Ingest and Reconciliation

### 交付

- orders/trades按配置lookback全量重扫，不只依赖进程内游标。
- 游标与入账原子提交，重复/乱序事件安全。
- working orders、fills、现金和持仓的对账顺序。
- 无法映射事件quarantine及带审计的解除/adjustment流程。
- 专用账户发现人工/未知活动即HARD阻断。
- readiness暴露last sync、freshness、hard diff和unknown数量。

### 验证

- 漏fill、重复fill、跨日未终态、未知订单、人工买卖和现金差异。
- HARD差异不自动覆盖账本且阻断执行。

## S12：Target Portfolio Planner

### 交付

- 基于S10新鲜NAV的weight/value/qty目标归一。
- deploy ratio、cash buffer、整手、最小订单、最大权重、费用估计和drift tolerance。
- 只使用策略owned position并扣除working exposure。
- 当前首版不读取或分配其他策略/人工持仓。

### 验证

- 1万元、A已有3000、现金7000、A/B各50%的正确目标。
- 余单存在时不重复报单。
- 陈旧snapshot或HARD reconcile状态拒绝规划。

## S13：Execution Orchestrator and Baseline Risk

### 交付

- 组合意图状态机、先卖后买和按真实回款重规划。
- 部分成交、撤单、deadline、追价上限和重启恢复。
- 未解决unknown阻止下一调仓。
- 最小pre-trade risk、global/account/strategy kill switch和只卖不买模式。
- 默认执行禁用；仅S11 readiness通过且kill switch允许时提交。

### 验证

- 卖出不完整时买入不透支。
- 重复聚宽回调返回同一intent。
- 对账阻断、过期数据和kill switch均不能产生新买单。

## S14：Strategy API and Authorization

### 交付

- account ensure、snapshot、target submit、intent/order/fill/event/performance/reconcile查询。
- strategy-scoped token和admin动作隔离；策略token不能调用raw broker下单。
- feature handshake `strategy_ledger_v1`。
- 统一响应字段、错误码、retryable和审计日志。

### 验证

- 旧helper、缺feature和越权请求fail-fast，不降级raw broker。
- 策略token不能访问其他strategy_id或管理员动作。

## S15：JoinQuant Live Runtime and good_etf

### 交付

- BACKTEST/SHADOW/LIVE统一runtime。
- 策略级PortfolioView、snapshot freshness/version/open orders/reservations。
- events `after_seq`断点恢复和`record()`真实指标。
- `good_etf`拆为纯候选、纯target builder和一次组合提交。
- 使用策略NAV而非available cash；09:30风险/调仓冲突消除。
- 生产默认`mirror_jq_orders=False`。

### 验证

- 回测和LIVE复用同一选股/target纯函数。
- 部分成交后下一轮只补真实差额。
- 聚宽重启从event seq恢复，实盘决策不读取原生镜像账本。

## S16：Performance and Observability

### 交付

- unit NAV、TWR、daily/total return、drawdown、费用、换手和slippage。
- 真实capital flow不计作收益。
- 结构化日志、关联ID、指标、告警和通知节流。
- 扩展风险计数持久化和公司行动业务实现。

### 验证

- 绩效可由ledger/fills/marks重算一致。
- 重启不清零风险计数或高水位。

## S17：Automated E2E and Deployment Artifacts

### 交付

- 端到端、并发、crash/chaos、adapter和恢复测试。
- Windows service/supervisor、启动readiness、数据库备份恢复、日志轮转和runbook。
- 生产checklist：私有origin、token/Webhook轮换、TLS、allowlist、专用账户和禁止人工交易。

### 验证

- 覆盖冻结后提交前、券商接收后响应丢失、fill commit前后、DB/QMT断连和重复回调。
- 备份恢复演练后状态一致。
- 任一生产安全项未确认时LIVE readiness=false。

## S18：JoinQuant/Shadow Release Gate

- 在真实聚宽环境验证helper/config一次上传后策略原样复制运行。
- 缺helper、缺profile和版本不匹配表现与S01/S03一致。
- SHADOW只读至少5个交易日；保存平台日志、目标、账本快照和差异证据。
- 未达到真实交易日证据时保持BLOCKED，但不阻止继续完善非LIVE代码。

## S19：QMT Simulation Release Gate

- 用户确认旧token/Webhook已轮换，TLS/token scope和专用账户配置完成。
- QMT模拟至少5个交易日。
- 要求0重复订单、0未解释HARD差异，重启恢复和NAV重放通过。

## S20：Small Live Approval Gate

- 需要用户明确审批真实资金和准确额度。
- 专用账户先使用极小资金，完成预定义场景和日终对账。
- 通过独立运行审查后才允许提高到1万元；共享账户仍不在范围内。

## 4. Review记录模板

```text
Slice:
Implementation commit:
Reviewer:
Reviewed commit/diff:
Tests (exact commands and result):
Findings:
Fix commit:
Retest:
Final candidate SHA:
Final reviewer:
Final review result:
Residual risks/external blockers:
Decision: DONE / REWORK / BLOCKED
```
