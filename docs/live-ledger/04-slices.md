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
| S01 | JoinQuant Source and Profile Contract | DONE | 同源策略、模式/profile、helper API兼容、fail-fast |
| S02 | JoinQuant Typings and IDE | DONE | 严格类型桩、IDE导入、目标Python/API矩阵 |
| S03 | JoinQuant Validation and Export | DONE | AST校验、敏感扫描、clean-room导入、原样导出 |
| S04 | Strategy Domain and Schema | DONE | 整数尺度、状态、不变量、schema和迁移 |
| S05 | Transactional Repository | DONE | 事务、CAS、事件序列、并发和重放基础 |
| S06 | Broker Capability Contract | DONE | QMT标识、订单/成交唯一性、费用、lookback和unknown能力 |
| S07 | Persistent Idempotency and Outbox | DONE | 请求幂等、operation、outbox、lease、unknown恢复 |
| S08 | Capital Allocation Ledger | DONE | 未分配池、初始1万元、按订单冻结/释放、显式资金流 |
| S09 | Fill Booking and Position Lots | DONE | 买卖成交、费用、lot、T+1、成本和重复fill no-op |
| S10 | Valuation and Atomic Snapshot | DONE | mark来源/时间戳、NAV、快照版本和陈旧价规则 |
| S11 | Broker Ingest and Reconciliation | IN_PROGRESS | 跨日重扫、归属、quarantine、HARD阻断和readiness |
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
Final candidate SHA: c336d241d75f0e19c7ab02649b956e2652e5d4a6
Final reviewers: /root/review_s01_v4_exact_contract；/root/review_s01_v4_exact_security；/root/review_s01_v4_exact_adversarial
Final review result: REWORK（1 APPROVE，2 REWORK；任一REWORK即不得放行）
Final findings:
  - MAJOR: 无helper兜底只按sys.modules键名识别helper；若真实helper模块对象以innocent_runtime_cache等任意键缓存，context getter仍可借其旧client触达socket
  - MAJOR: 成功runtime的namespace record、进程signature和canonical state被同时清空而active mode仍为稳定值时，下一次安装会被误判为全新进程并发布改变后的契约
Residual risks/external blockers: helper缺失兜底会拒绝sys.modules中的helper别名和remote portfolio标记，但Python无法发现已从模块表删除后藏在任意局部变量/容器中的旧client；该路径只接受全新可信聚宽context，部署规约禁止卸载helper后同进程降级。profile仍是可信代码而非沙箱；真实聚宽smoke在S18
Decision: REWORK
```

### v4精确候选复审与v5补救记录

```text
Slice: S01
Reviewed commit: c336d241d75f0e19c7ab02649b956e2652e5d4a6
Review result: REWORK
Fix commit: PENDING
Fixes in current worktree:
  - helper导出稳定STRATEGY_RUNTIME_HELPER_MARKER；无helper兜底同时扫描sys.modules键和真实模块对象的__name__、marker、文件名及版本化安装入口，任意缓存键名都不能隐藏已加载helper
  - owner登记时在读取context前以普通内建值快照校验active mode、进程signature、canonical state和namespace runtime record是否构成同一已提交权威状态；只有四者都处于真实初始状态才允许fresh install
  - 成功状态的权威组成被全部/部分擦除、结构/值篡改或注入poison值时，统一转FAILED、安装namespace guard并要求干净进程重启，且不执行poison魔术方法
Retest:
  - $env:DEFAULT_DATA_PROVIDER='qmt'; $env:PYTHONDONTWRITEBYTECODE='1'; python -X utf8 -m pytest tests/test_jq_remote_helper.py tests/test_jq_strategy_runtime.py tests/strategies/test_good_etf_contract.py -q -o addopts='' -p no:cacheprovider -> 169 passed
  - fatal flake8 for helper/strategy/runtime tests -> PASS
  - Python 3.8 AST -> PY38_AST_OK
  - baseline validator -> S00_BASELINE_CHECK_OK
  - git diff --check -> PASS（仅工作树CRLF转换提示）
Pre-commit reviewers: /root/review_s01_v5_worktree_contract；/root/review_s01_v5_worktree_security（执行流中断，无有效结论）；/root/review_s01_exact_contract_v2（本轮作为对抗审查）
Pre-commit result: REWORK（两路REWORK；一路执行流中断不计为批准）
Pre-commit findings:
  - BLOCKER: active/signature/canonical/namespace record四项全部擦除后仍被当作fresh，因既有单调generation未参与首次安装判定
  - BLOCKER: 真实helper模块对象被改为ModuleType子类并只放在任意sys.modules键时会被精确type检查跳过，context getter仍可触达旧client/socket
  - MAJOR: 等值浅拷贝替换canonical/record、同步替换进程与record signature，或篡改公开版本常量及可变schema集合，仍可能通过封套或执行poison比较
  - MINOR: 仅具有任意整数API版本和同名可调用入口的无关模块被误判为BulletTrade helper
Final candidate SHA: PENDING
Final reviewers: PENDING（三方精确SHA复审）
Final review result: PENDING
Residual risks/external blockers: sys.modules对象扫描不能发现已经从模块表删除后仅藏在任意局部变量/容器中的旧client；该边界必须由干净聚宽进程和部署规约保证。profile仍是可信代码而非沙箱；真实聚宽smoke在S18
Decision: REWORK
```

### v5工作树审查与v6身份封套补救记录

```text
Slice: S01
Reviewed diff: c336d241d75f0e19c7ab02649b956e2652e5d4a6..worktree-v5
Review result: REWORK
Fix commit: PENDING
Fixes in current worktree:
  - fresh install除active/signature/canonical/capsule/namespace record均为空外，还要求单调contract generation精确为0；成功或失败尝试均安全递增，四项权威全擦不能回到fresh
  - 每次成功提交创建进程内commit capsule，以identity绑定signature、canonical state、namespace record、record state及提交generation；等值浅拷贝、global/record协同替换或正整数generation改写都在context前失败
  - authority快照使用函数内固定schema字面量；公开API/profile/state版本和helper marker先做精确内建类型与固定值校验，bool/poison/超大版本不触发魔术比较
  - generation安全递增不对篡改对象执行算术魔术方法；FAILED同时清除signature、canonical与commit capsule，但保留非零单调generation
  - sys.modules扫描接受ModuleType及其子类，并只依赖项目专属模块名、稳定marker或文件名；移除“任意API整数+同名callable”宽泛启发式，避免无关模块假阳性
Retest:
  - $env:DEFAULT_DATA_PROVIDER='qmt'; $env:PYTHONDONTWRITEBYTECODE='1'; python -X utf8 -m pytest tests/test_jq_remote_helper.py tests/test_jq_strategy_runtime.py tests/strategies/test_good_etf_contract.py -q -o addopts='' -p no:cacheprovider -> 180 passed
  - fatal flake8 for helper/strategy/runtime tests -> PASS
  - Python 3.8 AST -> PY38_AST_OK
  - baseline validator -> S00_BASELINE_CHECK_OK
  - git diff --check -> PASS（仅工作树CRLF转换提示）
Pre-commit reviewers: /root/review_s01_v5_worktree_contract；/root/review_s01_v5_worktree_security（因工作树将修改而中断，无有效结论）；/root/review_s01_v4_exact_security（本轮第三路回归）
Pre-commit result: REWORK（两路发现有效问题；一路中断不计为批准）
Pre-commit findings:
  - MAJOR: commit capsule全局本身缺少独立identity锚，等值浅拷贝胶囊仍可通过内部引用校验并读取context
  - MAJOR: capsule只保存canonical/record state字典identity，没有封存提交时的不可变值；两份字典保持identity并协同原地修改blocked_mutations后，会在读取context后被静默修复并成功
Final candidate SHA: PENDING
Final reviewers: PENDING（三方精确SHA复审）
Final review result: PENDING
Residual risks/external blockers: Python同进程中拥有任意代码执行权的可信维护者仍可同时重写全部模块内部状态；本门禁防御部署残留、热重载、并发、状态擦除和常见篡改，不是Python沙箱。sys.modules扫描不能发现已移出模块表后仅藏在局部变量/容器中的旧client；真实聚宽smoke在S18
Decision: REWORK
```

### v6工作树审查与v7闭包锚/提交快照补救记录

```text
Slice: S01
Reviewed diff: c336d241d75f0e19c7ab02649b956e2652e5d4a6..worktree-v6
Review result: REWORK
Fix commit: PENDING
Fixes in current worktree:
  - helper加载时创建独立闭包anchor；成功提交同时把同一capsule identity写入模块全局和闭包，单独等值浅拷贝或替换全局capsule在context前失败
  - capsule新增提交时的安全不可变state snapshot；pre-context要求canonical和record state当前快照同时等于提交快照，保持字典identity的协同原地值篡改也失败关闭
  - FAILED及helper generation漂移清空模块capsule，但把闭包anchor置为进程期失败latch；即使随后手工把active和generation复位，也不能伪装fresh，只有真正新进程的anchor为空
Retest:
  - $env:DEFAULT_DATA_PROVIDER='qmt'; $env:PYTHONDONTWRITEBYTECODE='1'; python -X utf8 -m pytest tests/test_jq_remote_helper.py tests/test_jq_strategy_runtime.py tests/strategies/test_good_etf_contract.py -q -o addopts='' -p no:cacheprovider -> 183 passed
  - fatal flake8 for helper/strategy/runtime tests -> PASS
  - Python 3.8 AST -> PY38_AST_OK
  - baseline validator -> S00_BASELINE_CHECK_OK
  - git diff --check -> PASS（仅工作树CRLF转换提示）
Pre-commit reviewers: /root/review_s01_v5_worktree_contract；/root/review_s01_v5_worktree_security；/root/review_s01_v4_exact_security（本轮第三路合同复核）
Pre-commit result: REWORK（2 APPROVE，1 REWORK；任一REWORK即不得放行）
Pre-commit findings:
  - MAJOR: `_track_runtime_request`捕获和`_assert_runtime_request_lease_current`比较非精确整数generation时会执行poison `__ne__`；伪造相等可让缓存client进入socket边界
  - MAJOR: importlib.reload初始化直接对旧module/contract/inflight generation执行`int(...)`；poison异常可在ACTIVE_MODE转FAILED和client清理前中断，留下混合代际开放状态
Final candidate SHA: PENDING
Final reviewers: PENDING（三方精确SHA复审）
Final review result: PENDING
Residual risks/external blockers: 闭包锚提高了常见模块全局篡改门槛，但本门禁仍不是Python沙箱；拥有任意可信代码执行权的维护者可通过反射或替换函数重写内部状态。sys.modules外局部/容器旧client不可发现；真实聚宽smoke在S18
Decision: REWORK
```

### v7工作树审查与v8 generation/RPC/reload补救记录

```text
Slice: S01
Reviewed diff: c336d241d75f0e19c7ab02649b956e2652e5d4a6..worktree-v7
Review result: REWORK
Fix commit: PENDING
Fixes in current worktree:
  - RPC lease捕获前要求contract generation与inflight count都是精确非负int，且允许远程访问的未安装状态generation必须为0；无效状态立即进入FAILED、清client且不调用poison比较/转换
  - 每次重试建socket前同时要求lease/current generation为精确非负int，再做内建整数比较；finally对被并发污染的inflight count安全归零，不执行算术魔术方法
  - module generation所有调用前/异常后校验统一要求精确正整数与instance token identity，安装路径不再直接比较poison对象
  - reload先用精确类型快照读取旧module/contract/inflight计数；无效旧值采用安全FAILED代际，不调用`int()`/`__index__`，重置transition并清空所有client
  - reload检测基于旧module generation键是否存在；即使旧generation本身poison，也建立新instance token、非零generation和闭包FAILED latch
Retest:
  - $env:DEFAULT_DATA_PROVIDER='qmt'; $env:PYTHONDONTWRITEBYTECODE='1'; python -X utf8 -m pytest tests/test_jq_remote_helper.py tests/test_jq_strategy_runtime.py tests/strategies/test_good_etf_contract.py -q -o addopts='' -p no:cacheprovider -> 185 passed
Pre-commit reviewers: /root/review_s01_v5_worktree_contract；/root/review_s01_v5_worktree_security；/root/review_s01_v4_exact_security
Pre-commit result: REWORK（三路均REWORK；任一REWORK即不得放行）
Pre-commit findings:
  - MAJOR: reload先发布module generation、后发布FAILED/清client；旧client未绑定定义时instance token/module generation，并发或中断reload窗口仍可进入socket
  - MAJOR: 成功commit capsule未锚定原instance token和module generation；改写generation，或协同替换全局token与runtime record token后，同参数安装仍会读取context并成功
  - MAJOR: RPC在精确类型检查前比较transition owner，poison `__ne__`可伪装当前线程并进入socket
  - MAJOR: RPC直接`with`可替换的runtime lock；代理锁会执行上下文协议，同类型不同identity的锁也会破坏线性化
  - MAJOR: inflight只在入口验证；登记后、建socket前污染为poison值仍可进入socket，且finally会静默归零而不锁存FAILED
Final candidate SHA: PENDING
Final reviewers: PENDING（三方精确SHA复审）
Final review result: PENDING
Residual risks/external blockers: 可信Python代码仍不是沙箱；sys.modules外局部/容器旧client不可发现。真实聚宽/QMT证据分别在S18-S20，S01仍禁止真实资金
Decision: REWORK
```

### v8工作树审查与v9模块原语/多维RPC租约补救记录

```text
Slice: S01
Reviewed diff: c336d241d75f0e19c7ab02649b956e2652e5d4a6..worktree-v8
Review result: REWORK
Fix commit: PENDING
Fixes in current worktree:
  - reload在改变任何generation前先发布FAILED、重置transition并清空全部client；配置/runtime/RPC wrapper在装饰时捕获本模块instance token和module generation，旧调用不能在reload后借用新代际
  - 独立闭包anchor同时绑定runtime RLock、owner Lock、instance token、module generation和请求lease registry；所有锁在进入上下文协议前按identity校验，同类型替换或代理对象均固定失败且不执行魔术方法
  - transition owner/namespace/mode统一先快照，只接受None或精确内建int/dict/str；任何比较、membership或格式化都发生在类型通过后，失败路径幂等清除transition和全部权威/client状态
  - commit capsule新增原instance token identity和精确module generation；成功状态协同替换token/record或改写module generation在读取context前失败
  - 每个RPC使用定义时helper代际、请求时contract generation、精确inflight count和闭包锚定的独立request token registry组成多维lease；入口、每次重试及紧邻socket前均校验own token仍登记且`inflight == len(registry)`
  - finally只在同module/contract代际移除自己的lease；reload或既有FAILED已清registry时旧finally不得递减新代际计数或重复推进失败generation
Retest:
  - $env:DEFAULT_DATA_PROVIDER='qmt'; $env:PYTHONDONTWRITEBYTECODE='1'; python -X utf8 -m pytest tests/test_jq_remote_helper.py tests/test_jq_strategy_runtime.py tests/strategies/test_good_etf_contract.py -q -o addopts='' -p no:cacheprovider -> 198 passed
  - fatal flake8 for helper/strategy/runtime tests -> PASS
  - Python 3.8 AST -> PY38_AST_OK
  - baseline validator -> S00_BASELINE_CHECK_OK
Pre-commit reviewers: /root/audit_v10_registry；/root/audit_v10_gate；/root/review_s01_exact_contract_v2
Pre-commit result: REWORK
Findings:
  - fake-equal registry元素可利用set值相等绕过本请求token identity；finally未完整验证active/transition/reload状态
  - 部分reload后的旧finally可重复推进FAILED generation；最终lease检查与socket建立之间仍有TOCTOU
  - reload gate若保存在公开可替换结构中可被回调重新打开，异步中断还可能遗留attempt token或锁
Final candidate SHA: PENDING
Final reviewers: PENDING（三方精确SHA复审）
Final review result: PENDING
Residual risks/external blockers: 可信Python代码仍不是沙箱；反射替换闭包函数本身或sys.modules外局部/容器旧client不在本门禁可证明范围。真实聚宽/QMT证据分别在S18-S20，S01仍禁止真实资金
Decision: REWORK
```

### v9工作树审查与v10闭包gate/install lease补救记录

```text
Slice: S01
Reviewed diff: c336d241d75f0e19c7ab02649b956e2652e5d4a6..worktree-v9
Review result: REWORK
Fix commit: PENDING
Fixes in current worktree:
  - request registry只用set基类迭代和object identity检查；不可信元素先由闭包永久隔离，再清registry，避免析构器在FAILED锁存期间执行
  - socket gate把单向reload latch和attempt identity集合封入闭包；公开active/reload镜像不能重新打开，创建socket以runtime→gate顺序原子登记attempt
  - attempt外层finally在登记前建立，异步中断反复完成identity释放、condition通知和已建socket关闭；reload等待全部已登记attempt收尾
  - install reservation绑定helper token/module generation、thread、namespace/mode、contract generation、active mode和gate authority identity；每个可执行context/profile边界后及提交前复核
  - reload与旧request finally幂等复用FAILED anchor，旧代清理不再重复推进contract generation
Retest:
  - target suite -> 213 passed
Pre-commit reviewers: /root/audit_v10_registry；/root/audit_v10_gate；/root/review_s01_exact_contract_v2
Pre-commit result: REWORK
Findings:
  - BLOCKER: 新模块先执行imports、后识别reload；首个import处KeyboardInterrupt仍会保留旧client远程能力
  - BLOCKER: decorator最终generation检查与真实返回之间仍可并发完成reload，旧安装返回成功且namespace record残留
  - MAJOR: BACKTEST对profile/profile_module调用str()后才合法切换generation，可执行__str__回调并掩盖reservation篡改
Decision: REWORK
```

### v10精确复审与v11 pre-import/返回线性化补救记录（历史，已被后续REWORK取代）

```text
Slice: S01
Reviewed diff: c336d241d75f0e19c7ab02649b956e2652e5d4a6..worktree-v10
Review result: REWORK
Fix commit: PENDING
Fixes in current worktree:
  - v11曾以文件末bootstrap尝试在新代import前关闭旧gate并等待attempt；该历史实现已被后续REWORK和D020生产边界取代，不构成进程内reload或任意异步中断的安全保证
  - commit capsule按identity额外绑定提交namespace；reload会立即删除旧runtime record并安装FAILED guard，不依赖后续安装调用触发清理
  - 安装最终generation检查后复核权威gate；runtime锁覆盖owner收尾，最终以runtime→owner→gate原子复核helper代际、gate、公开reload镜像和transition identity后才允许成功返回
  - 最终边界失败显式按namespace撤销已提交record；并发reload不能留下成功结果或可变交易入口
  - BACKTEST只接受精确内建str的profile/profile_module，并在首次合法postcondition generation切换前复核原install lease，不执行自定义__str__
  - 新增bootstrap首行/首个import中断、generation检查后reload、reload/install双向线性化、最终化异步中断、确定性无效gate无自旋、提交namespace即时清理及字符串callback回归
Retest:
  - $env:DEFAULT_DATA_PROVIDER='qmt'; $env:PYTHONDONTWRITEBYTECODE='1'; python -X utf8 -m pytest tests/test_jq_remote_helper.py tests/test_jq_strategy_runtime.py tests/strategies/test_good_etf_contract.py -q -o addopts='' -p no:cacheprovider -> 222 passed
  - fatal flake8 for helper/strategy/runtime tests -> PASS
  - Python 3.8 AST -> PY38_AST_OK
  - baseline validator -> S00_BASELINE_CHECK_OK
  - git diff --check -> PASS（仅工作树CRLF转换提示）
Pre-commit reviewers: /root/design_reload_linearization；/root/audit_v11_bootstrap；第三路未形成可放行结论
Pre-commit result: INVALIDATED；后续工作树已修改，v11审查和222项测试只保留为历史证据
Final candidate SHA: PENDING
Final reviewers: PENDING（三方精确SHA复审）
Final review result: PENDING
Residual risks/external blockers: 该记录中的绝对reload/BaseException保证已被后续审查否定并收窄；见下一轮记录
Decision: REWORK（被后续工作树取代）
```

### v11后续reload/effect边界REWORK与当前候选记录

```text
Slice: S01
Reviewed diff: c336d241d75f0e19c7ab02649b956e2652e5d4a6..a94aa12060c5e8cef479224952e302eeac99f37d
Review result: DONE；逐轮结论与最终精确SHA见本记录下方
Implementation commit: a94aa12060c5e8cef479224952e302eeac99f37d
Committed candidate changes:
  - 三把闭包锚定锁分别为runtime RLock、owner Lock和socket RLock；支持路径采用runtime -> owner -> socket锁序
  - socket authority用attempt token -> thread id登记在途连接；lease检查与attempt登记在runtime -> socket临界区原子完成，connector随后通过独立最终permit进入且不持续持gate锁，reload关闭gate后等待已登记attempt结束
  - TLS包装、握手和request/mutation发送effect在socket RLock内线性化；mutation在调用effect前发布handoff，发送结果不确定时禁止自动重试
  - 在post-connector runtime复核前先结束socket attempt，避免reload持runtime等待attempt与请求持attempt等待runtime的锁循环
  - 同线程已持socket锁或拥有attempt时递归reload不等待自身，发布FAILED并抛出不属于Exception的RuntimeReloadAbort
  - reload gate明确降级为误用检测与fail-closed防线，不是热更新API；即使importlib.reload返回成功，该进程也永久FAILED
  - 生产禁止raw importlib.reload、热补丁、same-thread recursive reload及sys.settrace/sys.setprofile/signal catch-and-resume；任何reload异常、RuntimeReloadAbort或异步中断必须终止进程
  - helper升级固定为停策略、确认旧进程退出、换文件、全新进程冷启动；首次从raw-Lock/pre-bootstrap旧helper升级亦如此
  - good_etf在调用helper runtime前精确校验稳定marker；missing/wrong/bool/poison marker固定失败且不触达install入口
  - good_etf在任何模式归一化/API比较前先校验MODE为精确str、expected/actual API为精确int；boolean/poison值不执行魔术方法且不触达helper
  - good_etf从模块`__dict__`以`dict.get`捕获runtime入口并要求精确Python函数；缺失入口、模块`__getattr__`和任意callable对象不会在门禁前执行，返回state只接受精确dict
First pre-review freeze:
  - runtime + reload deadlock regression -> 204 passed
  - remote helper + strategy contract -> 63 passed
  - total -> 267 passed
Pre-commit round 1 reviewers: /root/precommit_s01_contract_frozen -> REWORK；并发/部署两路因工作树解冻标记INVALIDATED
Pre-commit round 1 result: REWORK
Pre-commit round 1 finding:
  - MAJOR: good_etf只校验STRATEGY_RUNTIME_API_VERSION，未校验STRATEGY_RUNTIME_HELPER_MARKER；API碰巧为1的错误同名helper可在门禁前获得globals/context并返回伪状态
Retest after marker remediation:
  - 完整目标测试 -> 271 passed（runtime+deadlock 204；remote helper+strategy contract 67）
  - fatal flake8（helper/strategy/相关tests）-> PASS
  - Python 3.8 AST（6个变更Python文件）-> PY38_AST_OK
  - git diff --check -> PASS（仅工作树CRLF转换提示）
Pre-commit round 2 reviewers: /root/precommit_s01_contract_frozen -> APPROVE；/root/update_s01_docs_boundary -> APPROVE；/root/audit_rlock_reload_v13 -> REWORK
Pre-commit round 2 result: REWORK（2 APPROVE，1 REWORK；任一REWORK即不得放行）
Pre-commit round 2 findings:
  - MAJOR: `str(MODE or '')`会在helper门禁前执行非普通MODE的`__bool__/__str__`
  - MAJOR: 在确认`_EXPECTED_RUNTIME_API_VERSION`为精确int前执行`actual != expected`，可调用poison expected的`__ne__`
Retest after pre-gate poison remediation:
  - 完整目标测试 -> 275 passed（runtime+deadlock 204；remote helper+strategy contract 71）
  - fatal flake8（helper/strategy/相关tests）-> PASS
  - Python 3.8 AST（6个变更Python文件）-> PY38_AST_OK
  - git diff --check -> PASS（仅工作树CRLF转换提示）
Pre-commit round 3 reviewers: /root/precommit_s01_contract_frozen -> REWORK；/root/audit_rlock_reload_v13 -> REWORK；/root/update_s01_docs_boundary -> APPROVE
Pre-commit round 3 result: REWORK（1 APPROVE，2 REWORK；任一REWORK即不得放行）
Pre-commit round 3 finding:
  - MAJOR: 普通属性访问/调用`bt.install_strategy_runtime`可在入口缺失时触发模块`__getattr__`，或对任意callable入口执行`__call__`，均早于真正runtime gate
Retest after exact runtime-entry remediation:
  - 完整目标测试 -> 278 passed（runtime+deadlock 204；remote helper+strategy contract 74）
  - fatal flake8（helper/strategy/相关tests）-> PASS
  - Python 3.8 AST（6个变更Python文件）-> PY38_AST_OK
  - git diff --check -> PASS（仅工作树CRLF转换提示）
Pre-commit round 4 reviewers: /root/update_s01_docs_boundary -> APPROVE；契约路 -> REWORK；并发路 -> REWORK
Pre-commit round 4 result: REWORK（1 APPROVE，2 REWORK；任一REWORK即不得放行）
Pre-commit round 4 findings:
  - MAJOR: helper返回值只校验精确dict、未验证完整state；伪state可把SHADOW降级为BACKTEST，随后触发聚宽原生order
  - MAJOR: initialize/process_initialize在helper gate前调用jqdata/platform对象，平台回调可先产生副作用
Fixes after round 4:
  - 对runtime state完整校验schema、strategy identity、mode、run_type、enabled/orders_enabled/production_ready、reason、profile_module及blocked_mutations；_runtime_mode拒绝提交后篡改
  - initialize与process_initialize的首条可执行语句均安装runtime，任何jqdata/platform调用只能发生在gate之后
  - 新增15个策略回归，覆盖伪state、字段篡改、SHADOW降级和生命周期首语句顺序
Retest after state/lifecycle remediation:
  - 完整目标测试 -> 293 passed（runtime+deadlock 204；remote helper 35；strategy contract 54）
  - fatal flake8（helper/strategy/相关tests）-> PASS
  - Python 3.8 AST（6个变更Python文件）-> PY38_AST_OK
  - baseline validator -> S00_BASELINE_CHECK_OK
  - git diff --check -> PASS（仅工作树CRLF转换提示）
Pre-round-5 adversarial advisory: REWORK（非正式冻结前咨询，不构成正式第5轮结论）
Pre-round-5 finding:
  - MAJOR: `_runtime_mode`先读取`g.bt_runtime`会执行平台属性协议；poison getter可先把SHADOW改为BACKTEST、返回伪BACKTEST state并触发原生order
Additional fixes before round 5:
  - 完整state校验成功后把请求模式写入一次性闭包权威；`g.bt_runtime`降为聚宽侧展示副本，交易入口完全不读取g属性协议
  - `_runtime_mode`只比较精确当前MODE与闭包权威；MODE漂移、未安装或权威损坏均在原生交易函数前固定失败
  - poison g getter、g/MODE协同降级及cancel/order_target/order_target_value三条SHADOW wrapper均有回归覆盖
Final retest before round 5:
  - 完整目标测试 -> 295 passed（runtime+deadlock 204；remote helper 35；strategy contract 56）
  - fatal flake8（helper/strategy/相关tests）-> PASS
  - Python 3.8 AST（6个变更Python文件）-> PY38_AST_OK
  - baseline validator -> S00_BASELINE_CHECK_OK
  - git diff --check -> PASS（仅工作树CRLF转换提示）
Pre-commit round 5 reviewers: /root/precommit_s01_contract_frozen -> APPROVE；/root/update_s01_docs_boundary -> REWORK；/root/audit_rlock_reload_v13 -> REWORK
Pre-commit round 5 result: REWORK（1 APPROVE，2 REWORK；代码/并发无finding，任一文档finding仍不得放行）
Pre-commit round 5 findings:
  - MINOR: 本记录顶部Review result仍停在首轮marker修复阶段，与同段已记录round 2-5矛盾
  - MINOR: `00-current-state.md`更新时间仍为2026-08-08，与2026-08-09的当前session/冻结状态不一致
Fixes after round 5:
  - 当前候选摘要改为概括前五轮REWORK并明确等待round 6；保留逐轮历史证据
  - 当前状态文档更新时间同步为2026-08-09（Asia/Shanghai）
Pre-commit round 6 reviewers: /root/precommit_s01_contract_frozen -> REWORK；/root/update_s01_docs_boundary -> REWORK；/root/audit_rlock_reload_v13 -> REWORK
Pre-commit round 6 result: REWORK（三路均命中同一MINOR；代码/并发无新finding）
Pre-commit round 6 finding:
  - MINOR: `00-current-state.md`正文仍称处于第5轮预提交审查，与round 5已REWORK、round 6 PENDING的session/slice记录矛盾
Fix after round 6:
  - 现状正文改为不随审查轮次失效的“S01仍为IN_PROGRESS，预提交审查与精确SHA复审尚未全部完成”
Pre-commit round 7 reviewers: /root/precommit_s01_contract_frozen -> APPROVE；/root/audit_rlock_reload_v13 -> APPROVE；/root/update_s01_docs_boundary -> APPROVE
Pre-commit round 7 result: APPROVE（三路均无BLOCKER/MAJOR/MINOR；冻结tracked指纹0c97824c8fdc5861ec8f2bd5427759b353e82e38，deadlock test SHA256=ACE880F2434643ABC73593D8E42DAC28131B9780E26B3436BD41C179C4FD2874）
Final candidate SHA: a94aa12060c5e8cef479224952e302eeac99f37d
Final reviewers: /root/precommit_s01_contract_frozen -> APPROVE；/root/audit_rlock_reload_v13 -> APPROVE；/root/update_s01_docs_boundary -> APPROVE
Final review result: APPROVE（三路均无BLOCKER/MAJOR/MINOR；起止HEAD精确匹配且工作树clean；完整目标测试295 passed，并发/死锁定向矩阵20 passed）
Final verification commands:
  - root提交前实跑：`$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'; $env:DEFAULT_DATA_PROVIDER='easy_tdx'; $env:EASY_TDX_USE_STUB='1'; python -X utf8 -m pytest -p no:cacheprovider tests/test_jq_strategy_runtime.py tests/test_jq_runtime_reload_deadlock_regression.py tests/test_jq_remote_helper.py tests/strategies/test_good_etf_contract.py -q` -> 295 passed, 3 warnings
  - 契约路精确SHA终审实跑：`$env:DEFAULT_DATA_PROVIDER='qmt'; $env:PYTHONDONTWRITEBYTECODE='1'; python -X utf8 -m pytest tests/test_jq_remote_helper.py tests/test_jq_strategy_runtime.py tests/strategies/test_good_etf_contract.py tests/test_jq_runtime_reload_deadlock_regression.py -q -o addopts='' -p no:cacheprovider` -> 295 passed, 3 warnings
  - 部署/文档路精确SHA终审实跑：`$env:PYTHONUTF8='1'; $env:DEFAULT_DATA_PROVIDER='tushare'; $env:DATA_CACHE_DIR=''; $env:PYTHONDONTWRITEBYTECODE='1'; pytest tests/test_jq_strategy_runtime.py tests/test_jq_runtime_reload_deadlock_regression.py tests/test_jq_remote_helper.py tests/strategies/test_good_etf_contract.py -q -o addopts='' -p no:cacheprovider` -> 295 passed, 3 warnings
  - 并发/对抗路精确SHA终审实跑：`$env:DEFAULT_DATA_PROVIDER='tushare'; $env:DATA_CACHE_DIR=''; $env:PYTHONDONTWRITEBYTECODE='1'; python -X utf8 -m pytest tests/test_jq_strategy_runtime.py tests/test_jq_runtime_reload_deadlock_regression.py -q -o addopts='' -p no:cacheprovider -k "reload_from_own_socket_attempt or recursive_reload_while_holding_socket_gate_lock or mutation_send_base_exception or mutation_response_base_exception or recursive_reload_after_socket_attempt_registration or recursive_reload_during_final_socket_validation or recursive_reload_after_phase_lease_check or reload_waits_for_linearized_mutation_effect or reload_waiting_for_completed_connector or reload_ownership_probe_interrupt"` -> 20 passed, 184 deselected
  - `python -X utf8 -m flake8 helpers/bullet_trade_jq_remote_helper.py strategies/joinquant/good_etf.py tests/test_jq_strategy_runtime.py tests/test_jq_remote_helper.py tests/test_jq_runtime_reload_deadlock_regression.py tests/strategies/test_good_etf_contract.py --select=E9,F63,F7,F82` -> PASS
  - `python -X utf8 scripts/validate_live_ledger_baseline.py --bt-quant E:\dev\pycharm\bt_quant` -> S00_BASELINE_CHECK_OK
  - `git diff --check c336d241d75f0e19c7ab02649b956e2652e5d4a6 a94aa12060c5e8cef479224952e302eeac99f37d` -> PASS
Residual risks/external blockers: 纯Python无法对任意opcode/C返回点的恶意catch-and-resume、最终许可读取后的旧栈恢复、connector返回资源到共享holder之间的极短handoff窗口提供不可绕过的原子保证。生产以禁止这些机制并在任何异常时终止进程作为边界；进程退出负责最终释放残余OS资源。活动authority frame的f_locals物化/同步还可能把旧closure cell值写回并回滚已关闭latch，因此trace/debugger/frame introspection具有活动帧改写权，不在可防御契约内。若未来LIVE需抵抗同进程任意代码执行，须引入带epoch的独立IO worker/子进程或原生原子gate。真实聚宽/QMT证据仍分别在S18-S20，S01禁止真实资金
Decision: DONE
```

## S02：JoinQuant Typings and IDE

### 交付

- `jqdata.pyi`、helper `.pyi`和typing-only Context/Portfolio/Position/Snapshot模型。
- pyi与runtime API同步测试，导出符号、参数名称/种类和必填/可选形状漂移即失败。
- 独立严格类型配置，不受项目全局`ignore_missing_imports`掩盖。
- 记录聚宽目标Python、pandas/numpy及使用API兼容矩阵；未知版本明确为待平台核验。
- fresh venv/PyCharm源码路径配置说明和自动化setup。

### 验证

- 全新venv中editable install后，策略范围严格mypy/pyright通过。
- 常用`context.portfolio`、Position和helper返回值具有补全。
- Python目标版本语法编译通过。

### S02审查记录

```text
Slice: S02
Implementation commit: 3b54a4a7178fb36ab9f85de22a648bb08bd0448b
Pre-commit reviewers: /root/precommit_s01_contract_frozen；/root/audit_rlock_reload_v13；/root/update_s01_docs_boundary
Initial result: REWORK
Initial findings:
  - helper runtime-state TypedDict字段/blocked_mutations类型错误，六个交易入口退化为**kwargs: Any，漂移测试覆盖不足
  - 严格检查只覆盖合成probe而未覆盖真实good_etf.py；setup未验证venv/prefix/purelib，文档夸大--full和轻量环境pytest能力
  - Windows site.getsitepackages()[0]实际指向venv根；类型收窄引入的runtime cast可被篡改后把SHADOW伪装为BACKTEST
  - Python 3.8没有ast.unparse，旧pip可能不支持PEP 660 editable安装
Fixes:
  - 对齐全部导出函数、类构造器/公共方法的参数名称、种类和必填性；TypedDict按真实builder分为必填和模式可选字段
  - 真实策略与契约probe同时进入strict mypy/pyright；策略安全路径清除全部runtime cast并增加poisoned-cast fail-close回归
  - setup校验sys.prefix/base_prefix和目标目录，先确保pip>=21.3，只向sysconfig purelib写.pth并拒绝越界
  - AST测试移除3.9专属API；文档明确轻量/full、版本范围、editable/wheel和S17/S18边界
Final pre-commit result: APPROVE（三路均无BLOCKER/MAJOR/MINOR）
Final exact-SHA reviewers: 同上三路
Final exact-SHA result: APPROVE；三路起止HEAD均为3b54a4a7178fb36ab9f85de22a648bb08bd0448b且工作树clean
Verification:
  - S01+S02目标矩阵 -> 320 passed, 3 warnings
  - strict mypy -> 2 source files PASS；strict pyright -> 0 errors/0 warnings
  - Python 3.8 AST、阻断级flake8、S00 baseline、commit diff --check -> PASS
  - 最新脚本在第三个全新空venv完成pip引导、editable install、严格检查和两模块find_spec；.pth仅位于Lib/site-packages
Residual risks:
  - 聚宽托管Python/pandas/numpy和私有API行为仍须S18平台探针确认；普通wheel顶层类型文件布局仍由S17门禁处理
  - 全仓测试仍受既有jqdatasdk/外部策略路径及若干非S02历史失败影响，本slice不宣称全仓套件已全绿
Decision: DONE
```

## S03：JoinQuant Validation and Export

### 交付

- AST校验：禁止服务器内部导入、危险文件/进程/网络用法和不支持语法。
- 敏感信息扫描和配置引用检查。
- 导出工具输出原样策略、helper、example profile和manifest。
- 非bundle模式源策略和导出策略hash一致。
- 校验、契约、hash、写出和manifest来自同一次不可变源码快照；部署声明在源码中先确定，导出/上传后禁止编辑。
- 可选私有profile只读校验不执行、不复制且不输出秘密；最终私有文件未显式传入时不得宣称已校验。
- 目标目录必须不存在且不经过symlink/junction/reparse point，失败保持调用前状态。
- clean-room目录导入测试以及helper/profile缺失、版本不匹配fail-fast测试。
- 明确：自动化通过只表示“可上传候选”，真实聚宽运行证据在S18。

### 验证

- 全新临时目录只使用导出物完成语法、导入和mock runtime smoke。
- 导出包不含token、Webhook、日志、缓存、数据库或服务器内部模块。

### 最终实现与审查

- 固定白名单三文件按一次不可变源码快照原样导出，manifest确定性记录契约、字节数和SHA256；目标必须不存在且路径不得经过symlink/junction/reparse point。
- 私有profile只按Python 3.8 AST读取字面量，校验schema、字段、精确类型、范围和strategy/profile契约；不执行、不复制、不hash、不输出秘密。
- 127项S03回归覆盖确定性、失败原子性、契约漂移、危险能力、敏感信息、clean-room、私有profile和namespace改写；与S01/S02联合矩阵为447 passed、3个既有warning。
- 冻结前REWORK已修复不可变快照、契约重绑定、动态/相对导入、`TYPE_CHECKING`污染、危险builtin别名、组合秘密、路径reparse/断链以及通过`globals/locals/vars/sys.modules/__dict__`改写契约等问题。最新修复拒绝保存或修改`getattr`/`object.__getattribute__`取得的原始namespace；策略与helper的合法只读模块查询改为单键读取而不保存namespace对象。
- 首次10文件正式冻结为REWORK：未绑定`dict.__setitem__/update/pop`可把`globals()`或原始`__dict__`作为首参数并配合computed key改写契约。mutator现统一解析真实修改目标，`dict.*`与`builtins.dict.*`以首参数为目标，新增5个拒绝回归。
- 第二次10文件正式冻结为REWORK：`object.__setattr__`/模块`.__setattr__`computed field可改运行时契约；`type({})`、`globals().__class__`等派生dict未绑定mutator仍可绕过；helper静态`__import__`未复用角色白名单。修复后mutator对其接收者和全部参数中的动态/raw namespace失败关闭，四种属性mutator形态统一验证静态字段，静态动态导入复用角色白名单且仅helper受控`profile_module`变量可动态解析。
- 第三次10文件正式冻结为REWORK：静态getter取得`__setattr__/__delattr__`后可绕过字段验证，关键字或`**kwargs`形式的`__import__`可绕过导入目标检查。被调函数名/owner现统一解析直接、属性和getter形式；导入目标统一规范化首位置参数或唯一`name=`，歧义/缺失/`**kwargs`失败关闭。
- 第四次10文件正式冻结为REWORK：helper的`__builtins__`下标可绕过动态导入白名单，嵌套/条件`TYPE_CHECKING`导入可通过校验但让策略加载NameError。所有角色现禁止直接`__builtins__`访问，并拒绝动态namespace直接下标和敏感内建键读取；`TYPE_CHECKING`只接受无条件模块顶层单次显式导入，typing通配符也被拒绝。
- 第五次10文件正式冻结为REWORK：bound/unbound/getter形式`__getitem__`可从动态namespace读取`__builtins__`并绕过角色导入白名单。现复用静态callable解析统一检查三种形式的目标、参数和敏感键。
- 该REWORK之后S03定向127 passed、联合矩阵447 passed；strict mypy/pyright、完整/阻断级flake8、Python 3.8 AST、S00 baseline、`git diff --check`、validate-only和全新目录真实导出均PASS。
- AST/特征扫描明确只是防误提交门禁，不是Python沙箱、完备别名/数据流证明或完备秘密检测器；真实聚宽行为仍由S18验证。
- 第六次冻结的部署/文档与独立功能审查均APPROVE；第三个审查代理因平台误判未产出结论，主审使用相同冻结指纹和验证结果补足合同核对。
- 实现提交：`224a68195eeff11a542885344957132a294c5399`。两路独立精确SHA终审均APPROVE，确认只包含冻结10文件，127项S03测试与447项联合矩阵通过，确定性导出与manifest逐项匹配。
- 残余边界：该扫描器不是Python沙箱、完备别名/数据流证明或完备秘密检测器；聚宽真实平台、QMT模拟和小额实盘仍分别受S18至S20门禁约束。
- Decision: DONE

## S04：Strategy Domain and Schema

### 本轮边界

- 聚焦个人专用账户、单策略和可信进程；不实现共享账户、多租户权限或同进程恶意代码防御。
- 本slice只定义模型、状态、整数尺度、SQLite表和迁移；事务repository、资金划拨与成交入账分别留在S05、S08、S09。
- schema为后续主闭环保留必要字段，不提前实现复杂通用框架。

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

### 最终实现与审查

- `domain.py`提供整数尺度、Asia/Shanghai时间、账户/现金/持仓/lot/意图/订单/成交/事件/对账的最小不可变模型。
- `schema.py`提供两阶段、逐版本事务化SQLite迁移；拒绝向下迁移、非连续或名称不匹配的历史。
- schema通过整数类型、非负、冻结不超过现金、可卖不超过持仓、lot剩余不超过原始数量、成交数量/价格和状态枚举等约束尽早拒绝坏数据。
- 备份恢复与表用途记录在`08-strategy-ledger-schema.md`；自动备份、repository和业务写入仍属于后续slice。
- 当前验证：S04定向12 passed，加入scheduler回归24 passed；新模块flake8、Python 3.8 AST、targeted mypy/pyright、S00 baseline和`git diff --check`通过。
- 首轮代码审查REWORK已修复两项主流程一致性问题：意图/事件/对账输入在构造时生成递归不可变快照；迁移历史保存SQL SHA-256并与`PRAGMA user_version`交叉校验，禁止静默接受旧迁移漂移。
- 实现提交：`6bfb4469f3b8d32a0121d164bd2af96ac3e94326`。修复后工作树和精确提交两阶段均获两路APPROVE；最终定向12 passed、联合24 passed，工作树clean。
- Decision: DONE

## S05：Transactional Repository

### 本轮边界

- 单机SQLite、一个服务进程，可容忍误启动第二写入者但不实现分布式lease。
- 直接使用标准库`sqlite3`和显式SQL，不引入ORM或通用Unit of Work框架。
- repository保证CAS、事务、重放一致性和最小初始资金池划拨；真实券商余额校准、可重复启动、冻结规则和成交业务仍由S08/S09服务层负责。

### 交付

- repository接口和SQLite WAL/FULL实现。
- 单writer策略、事务边界、CAS/ledger_version、单调event_seq。
- 行级语义锁或SQLite等价事务策略。
- append-only读取、快照重建和测试隔离。

### 验证

- 并发更新只有一个CAS成功。
- crash前后事务全有或全无。
- ledger replay得到相同物化状态。

### 最终实现与审查

- 标准库SQLite显式SQL；每个写操作独立连接和`BEGIN IMMEDIATE`，不引入ORM或分布式框架。
- `create_strategy_account`原子扣减物理账户未分配资金、建策略账户并写初始`ALLOCATE`资金流水，资金不足时全量回滚。
- `append_account_event`以`ledger_version`做CAS，在同一事务更新物化账户并追加ledger/event；序列共同单调递增。
- v3触发器保证ledger/event只能追加；`replay_account`在单一读事务快照中从初始资本重建并与物化账户核对。
- repository定向10项、S04+S05+scheduler联合34项通过；新模块flake8、Python 3.8 AST、targeted mypy/pyright、S00 baseline和`git diff --check`通过。
- 审查修复了三类主链问题：重放改为单连接读事务快照；开户资金原子来自物理账户可用资金池并记录`ALLOCATE`；开户中途失败完整回滚资金池、账户和资金流水。
- 实现提交：`9eb36f0`。最终工作树复审APPROVE。
- Decision: DONE

## S06：Broker Capability Contract

### 当前状态

- IN_PROGRESS：先盘点现有QMT adapter真实返回字段与查询能力，再冻结最小能力合同；不在本slice提前实现执行编排器。
- 当前代码已确认MiniQMT/BigQMT都有订单、成交和working order查询路径，但tag持久回显、原生成交号、完整费用和跨日lookback受实际QMT/网关版本影响，必须保留`PROBE_REQUIRED`而不能静态宣称支持。

### 实施计划

- 新增轻量能力状态/合同与`strategy_ledger_v1`验收函数，不引入插件框架。
- MiniQMT/BigQMT adapter各自暴露静态profile；真实环境证据由后续probe覆盖，不写死未经验证的lookback天数。
- 规范化成交时保留trade ID来源和费用是否真实出现；side缺失只按order ID映射。
- 用纯fixture合同测试覆盖可用profile、能力不足阻断、合成ID、费用缺失和side映射。

### 交付

- MiniQMT/BigQMT adapter对client tag/remark、broker order ID、trade ID、side、费用和状态的能力矩阵。
- 当日/跨日orders、trades、working order查询lookback和ID稳定性定义。
- `SUBMIT_UNKNOWN`可用的查询路径和无法恢复时的quarantine语义。
- 缺side的trade通过order映射；无法映射不得猜测。

### 验证

- adapter合同测试覆盖重复/乱序fill、remark roundtrip、断连、跨日working order和费用字段。
- 能力不足的adapter显式拒绝`strategy_ledger_v1`。

### 最终实现与审查

- MiniQMT/BigQMT静态profile区分`SUPPORTED / PROBE_REQUIRED / UNSUPPORTED`；当前目标环境未验证，因此不会被静态代码放行。
- `strategy_ledger_v1`要求tag回显、稳定order/trade ID、trade-order关联、完整费用/状态、current/working查询及至少前一交易日lookback。
- 成交证据只接受原生成交号；缺side按同一order ID映射，缺费用拒绝，完全重复成交归并而冲突重复报错。
- 首轮审查修复无效费用误标已知0、负费用放行，以及把order ID关联误等同于order含side三项主链问题。
- S06合同定向14项、S04至S06/QMT adapter/scheduler联合88项通过；新模块完整flake8、targeted mypy/pyright、Python 3.8 AST、旧文件阻断级flake8、S00 baseline和`git diff --check`通过。
- 修复后工作树复审APPROVE；实现提交`8679bc9`。
- Decision: DONE

## S07：Persistent Idempotency and Outbox

### 当前状态

- IN_PROGRESS：先实现单机SQLite operation/outbox原子写入和请求重放；只保留未知提交恢复所需状态，不提前建设通用消息平台。

### 交付

- `(strategy_id, endpoint, idempotency_key)`唯一及payload hash冲突。
- operation持久状态和原响应重放。
- 业务变更与outbox同事务；worker claim、lease和单物理账户执行者。
- 提交前持久client tag；响应丢失进入`SUBMIT_UNKNOWN`并按S06能力恢复。

### 验证

- 100个同key并发只创建一个operation/outbox项。
- 相同key不同payload明确冲突。
- 各crash point重启不产生第二次外部提交。

### 最终实现与审查

- schema v4持久保存operation、请求hash、client tag、状态与响应，outbox通过唯一`operation_id`一对一关联。
- operation/outbox同事务创建；同key同payload重放，不同payload冲突，100并发仅一个首次创建。
- claim在`begin_submission`前过期可重领；`begin_submission`是外部effect边界，之后的未知响应或重启遗留均进入`SUBMIT_UNKNOWN`且不重投。
- 首轮审查修复operation hash与outbox二次读取可变payload后可能使用不同请求快照的问题。
- S07定向10项、S04至S07加scheduler联合58项通过；新模块完整flake8、targeted mypy/pyright、Python 3.8 AST、旧文件阻断级flake8、S00 baseline和`git diff --check`通过。
- 修复后工作树复审APPROVE；实现提交`661f153`。
- Decision: DONE

## S08：Capital Allocation Ledger

### 当前状态

- DONE：真实券商现金校准、可重复ensure、按订单reserve/release与显式资金调整已完成；订单规划和成交持仓留在后续slice。

### 交付

- `UNALLOCATED_CASH_POOL`及物理账户首次校准。
- 从未分配池原子分配初始1万元，重复ensure返回原账户。
- reserve/release、显式allocate/withdraw和审计原因。
- 重启不重新分配；修改聚宽初始资金不静默改账。

### 验证

- 并发创建同策略只分配一次。
- 两策略并发不能超分配，即使当前首版API只开放一个策略。
- `cash-reserved=available`始终成立。

### 实现与审查结果

- `SQLiteCapitalService`实现券商可用现金校准、幂等ensure、按订单隔离的reserve/release和显式allocate/withdraw。
- 初始资金不足全量拒绝；重复启动不重新分配，配置变化不静默重置；50并发仅一个首次分配。
- 已有账户的券商现金快照只做账实核对，不覆盖本地资金；显式资金调整用external ref幂等并与资金池/账本/流水同事务。
- 首轮审查发现订单释放可能占用另一订单冻结额；修复后按`order_id`在同一事务核对余额，并新增两订单重复释放回归。
- S08定向10项、S04至S08加scheduler联合68项通过；新模块完整flake8、targeted mypy/pyright、Python 3.8 AST和`git diff --check`通过。
- 修复后的工作树复审APPROVE；实现提交`4b2f164`。
- Decision: DONE

## S09：Fill Booking and Position Lots

### 当前状态

- DONE：真实成交已驱动现金、订单冻结、position/lot、费用和已实现盈亏入账；估值与聚宽回传留在后续slice。

### 交付

- broker trade ID或稳定fingerprint唯一去重。
- 买卖fill单事务更新现金、冻结、lot、position、费用和realized PnL。
- 部分成交、终态释放、T+1可卖和跨交易日处理。
- append-only replay及公司行动扩展钩子。

### 验证

- 计划买3000、实成2000、费用5，分别验证余单working和canceled。
- 重复fill no-op、累计成交越界quarantine。
- 随机事件序列验证资产和lot不变量。

### 实现与审查结果

- `SQLiteFillBookingService`实现订单登记、买卖fill原子入账与撤单/拒单终态；不含估值、对账摄取和执行规划。
- 部分买入按真实成交价费扣账并保留余单冻结，全部成交/撤单释放订单余款；卖出按FIFO可卖lot计算净回款和已实现盈亏。
- 重复broker trade ID/fingerprint no-op，冲突ID、累计超额、同日卖出和无持仓卖出fill拒绝；卖出零成交/拒单不改变现金。
- 成交时间进入QMT证据合同，BigQMT分离日期/HHMMSS先合成完整时间，缺失或非法时间不能入账；同日lot按真实成交时间FIFO。
- 首轮审查发现BigQMT分离日期/HHMMSS会导致错误交易日，以及同日lot按入账时间而非成交时间FIFO；修复后均有真实字段/逆序回归。
- S09成交入账定向8项、联合77项和静态/语法/格式检查通过。
- 修复后的工作树复审APPROVE；实现提交`08081c9`。
- Decision: DONE

## S10：Valuation and Atomic Snapshot

### 当前状态

- DONE：同一读事务的现金、持仓市值、总资产、PnL、NAV和版本快照已完成；聚宽回传留在S15/S16。

### 交付

- mark price来源、`as_of`、freshness和陈旧价策略。
- NAV、positions value、cash/reserved/available和原子快照version。
- 行情缺失/过期时readiness和fail-closed规则。

### 验证

- 相同事件和mark重算NAV一致。
- 账户与持仓不发生非原子拼接快照。
- 陈旧价格不能用于新调仓。

### 实现与审查结果

- `SQLiteValuationService`在同一SQLite读事务生成现金、持仓市值、总资产、净投入、费用、三类PnL和NAV快照。
- mark必须包含来源与时间；缺失、陈旧和未来mark明确阻断。快照版本绑定ledger、position和mark证据，重复计算确定一致。
- lot成本改为按原fill总价费精确保留，部分卖出按剩余成本差结转，避免每股成本舍入累计漂移。
- 固定初始资金可输出performance-ready NAV；存在后续增减资时快照仍可估值，但不宣称严格绩效NAV可用。
- 首轮审查发现T+1可卖数物化值陈旧和mark校验/使用可能跨批；现改为按快照日期汇总lot可卖数，并在入口捕获单一marks副本。
- 首轮审查发现T+1可卖数物化值陈旧和mark校验/使用可能跨批；修复后分别从同一lot快照重算并捕获单一mark副本。
- S10定向11项、联合88项和静态/语法/格式检查通过。
- 修复后的工作树复审APPROVE；实现提交`4e190cc`。
- Decision: DONE

## S11：Broker Ingest and Reconciliation

### 当前状态

- IN_PROGRESS：实现单账户QMT订单/成交/资金/持仓重复重扫、策略归属、差异结果和readiness；不扩展消息队列或多节点worker。

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
