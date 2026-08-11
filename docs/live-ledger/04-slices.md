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

> D023已取代原S11至S20的机构级展开。S11至S20条目保留用于历史映射，不再逐项实施；当前权威剩余计划见[个人量化精简计划](15-lean-personal-plan.md)。

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
| S11-S20 | 原机构级剩余计划 | PENDING | 已由D023合并为L00至L04；仅保留历史需求映射 |
| L00 | Existing Code Pruning | DONE | `cd5ed99`：净删除约1.6万行，334项定向矩阵通过 |
| L01 | QMT Sync and Reconciliation | REVIEW | 单账户订单/成交/资金/持仓同步与READY/BLOCKED |
| L02 | Target Planner and Executor | PENDING | 目标权重、先卖后买、整手/现金缓冲、kill switch |
| L03 | Strategy API and JoinQuant View | PENDING | ensure/snapshot/targets/intent/events/reconciliation与PortfolioView |
| L04 | Local Deployment and Small Live | PENDING | 服务启动、备份、飞书通知、SHADOW/模拟/小额人工验收 |

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

- 新增能力：脱敏同源策略只保留`PROFILE`、`MODE`和稳定`STRATEGY_ID`；BACKTEST/SHADOW/LIVE模式与非法组合fail-fast；无密钥profile schema v1、example和本地私有profile忽略规则；统一版本化runtime入口`install_strategy_runtime`，缺helper、API版本不匹配、缺profile均有明确错误；策略不导入`bullet_trade.*`服务器内部包。
- 最终测试：完整目标矩阵295 passed（runtime+deadlock 204、remote helper 35、strategy contract 56），并发/死锁定向矩阵20 passed；Python 3.8 AST、阻断级flake8、S00 baseline与`git diff --check`均PASS。
- 审查结论：第七轮预提交冻结与精确SHA终审的契约、并发/对抗、部署/文档三路均APPROVE，无BLOCKER/MAJOR/MINOR，起止HEAD一致且工作树clean。
- 实现提交：`a94aa12060c5e8cef479224952e302eeac99f37d`。
- 决定：DONE。S01只证明源码/profile边界可测试：BACKTEST可运行、SHADOW只生成计划、LIVE明确阻断；不授权真实资金。
- 逐轮冻结与审查明细见[S01-S03审查明细归档](archive/04-slices-s01-s03-review-detail-pre-l00.md)；归档内容均已失效，仅作历史记录。

## S02：JoinQuant Typings and IDE

- 新增能力：`jqdata.pyi`、helper `.pyi`与typing-only Context/Portfolio/Position/Snapshot模型；pyi与runtime API同步漂移测试；独立严格mypy/pyright配置；聚宽目标Python、pandas/numpy与API兼容矩阵；fresh venv/PyCharm自动化setup（pip>=21.3引导、`.pth`只写purelib）。
- 最终测试：S01+S02目标矩阵320 passed；strict mypy两个源文件PASS、strict pyright 0 errors/0 warnings；Python 3.8 AST、阻断级flake8、S00 baseline与commit diff --check均PASS；第三个全新空venv完成editable install与严格检查。
- 审查结论：首轮REWORK修复后预提交三路APPROVE，精确SHA终审三路APPROVE，起止HEAD一致且工作树clean。
- 实现提交：`3b54a4a7178fb36ab9f85de22a648bb08bd0448b`。
- 决定：DONE。聚宽托管Python/pandas/numpy与私有API行为仍由S18平台探针确认。
- 逐轮审查明细见[S01-S03审查明细归档](archive/04-slices-s01-s03-review-detail-pre-l00.md)。

## S03：JoinQuant Validation and Export

- 新增能力：固定白名单三文件按单次不可变源码快照原样导出；确定性manifest记录角色、相对路径、字节数、SHA256与部署mode契约；目标目录reparse防护与原子导出；明显凭据扫描；私有profile只读校验（不执行、不复制、不hash、不输出秘密）；clean-room导入与缺helper/profile/版本不匹配的失败关闭smoke。
- 最终测试：S03定向127 passed、与S01/S02联合矩阵447 passed（3个既有warning）；strict mypy/pyright、完整/阻断级flake8、Python 3.8 AST、S00 baseline、`git diff --check`、validate-only与全新目录真实导出均PASS。
- 审查结论：第六次10文件冻结的部署/文档与独立功能审查APPROVE，两路独立精确SHA终审APPROVE；提交只含冻结10文件，双份全新导出字节一致，工作树clean。
- 实现提交：`224a68195eeff11a542885344957132a294c5399`。
- 决定：DONE。只放行“可上传候选”的生成与验证，不放行真实资金；L00已按D021/D023将导出器精简，当前边界见[聚宽校验与导出](06-joinquant-export.md)。
- 逐轮冻结明细见[S01-S03审查明细归档](archive/04-slices-s01-s03-review-detail-pre-l00.md)。

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

## L00：Existing Code Pruning

### 当前状态

- DONE：实现提交`cd5ed99`。主审撤销破坏性删表迁移、补齐旧接口文档失效提示后，334项定向矩阵及静态检查通过。

### 实施计划

1. helper精简：`helpers/bullet_trade_jq_remote_helper.py`重写为只含`STRATEGY_RUNTIME_API_VERSION`/`STRATEGY_RUNTIME_HELPER_MARKER`/`PROFILE_SCHEMA_VERSION`/`install_strategy_runtime`的单文件；删除旧版远程交易API（configure/install_jq_compat/RemoteBrokerClient/order系列）和全部同进程对抗机制（reload代际、闭包authority、对象投毒检测、lease/socket gate）。保留：普通类型/版本/模式校验、SHADOW交易函数门禁、profile schema v1校验、幂等重装（不同签名拒绝）、LIVE阻断状态。`.pyi`同步瘦身；删除`tests/test_jq_remote_helper.py`、`tests/helpers/test_jq_remote_helper_warning.py`、`tests/test_jq_runtime_reload_deadlock_regression.py`；`tests/test_jq_strategy_runtime.py`重写为精简套件。
2. 策略适配：`strategies/joinquant/good_etf.py`删除远程portfolio检测、helper别名扫描、运行时模式authority闭包和深度state校验；保留普通marker/版本/mode校验与无helper BACKTEST兜底。`tests/strategies/test_good_etf_contract.py`同步收缩。
3. 导出器精简：`scripts/export_joinquant.py`删除AST角色门禁、别名/namespace对抗扫描、动态导入分析、TYPE_CHECKING绑定检查和跨文件契约重绑扫描；保留固定白名单、Python 3.8语法检查、明显凭据扫描（字面量+文本模式）、profile形状校验、确定性manifest、reparse防护、原子导出和clean-room smoke。`tests/test_joinquant_export.py`同步收缩。
4. 单进程idempotency：`bullet_trade/server/strategy/idempotency.py`删除worker_id/lease归属与过期重领，claim退化为单进程原子认领，`quarantine_inflight`增加启动时CLAIMED→PENDING重置；outbox lease列保留为弃用占位（不破坏既有迁移哈希纪律）。不新增删除表的破坏性迁移，暂不用的`corporate_actions`表只保留兼容。多租户授权与复杂角色系统不建设（原S14范围），上游v0.9.2 server的sub-account功能保持原样。
5. adjust_capital定位固化：估值与资金代码保持现状（严格份额NAV从未实现，现有`performance_ready`标记保留），文档明确`adjust_capital`仅为管理员修复入口，不接入日常流程。
6. 文档归档：`03-session.md`与`04-slices.md`的多轮审查明细归档至`docs/live-ledger/archive/`，正文只保留架构、使用、恢复、部署和当前测试证据；`00-current-state.md`第3节改写为精简runtime描述；`06-joinquant-export.md`、`11-persistent-idempotency-outbox.md`同步更新。

### 保留底线（不得裁剪）

SQLite事务和CAS、资金冻结、请求幂等、未知提交隔离、真实fill去重、精确费用/lot成本、T+1、账实对账、原子估值、启动阻断、kill switch（地基）和数据库备份（地基）。

### 出口条件

- 核心BACKTEST/SHADOW、资金、成交、估值测试继续通过；L00定向矩阵全绿。
- 生产代码与测试数量明显下降；helper与导出器恢复到个人可维护规模。
- 独立审查APPROVE后收口。

### L00收口

- helper：6001→403行，公开API仅marker/API版本/profile schema/`install_strategy_runtime`；旧远程交易API与同进程对抗机制删除；`.pyi`与`typecheck/joinquant_contract.py`同步。`good_etf.py` 826→489行，业务逻辑零改动（diff确认hunk止于`_runtime_mode`）。
- 导出器：1500→615行；深层AST/别名/namespace对抗扫描与跨契约重绑扫描删除；manifest删除contracts字段；127→36用例。凭据扫描收窄为文本正则+字面量（拼接构造不再拦截），已在`06-joinquant-export.md`如实声明。
- idempotency：单进程原子认领；`quarantine_inflight`增加CLAIMED→PENDING崩溃恢复；SUBMIT_UNKNOWN不再投递保留。outbox lease列弃用保留，`corporate_actions`表只作兼容、不进入首版业务。已知取舍：CLAIMED搁浅仅随进程重启恢复，L02接线dispatcher时须记住此前提。
- 测试删除：`tests/test_jq_remote_helper.py`、`tests/helpers/test_jq_remote_helper_warning.py`、`tests/test_jq_runtime_reload_deadlock_regression.py`（均为已删除能力的测试）。
- 文档：S01/S03逐轮审查历史归档至`docs/live-ledger/archive/`（结论失效仅作记录）；00/03/04/06/08/11/12/14与两份README对齐现状；三篇上游接管文档与notebook 04添加失效标注。
- 测试证据：L00定向矩阵334 passed（runtime 121、策略契约25、helpers 31、typings 12、export 36、server 97、scheduler 12；PYTHONUTF8=1、DEFAULT_DATA_PROVIDER=easy_tdx）；全量724 passed、8个既有环境失败（pre-L00基线worktree复跑同样8失败，与L00无关）；strict mypy/pyright 0 issues；阻断级flake8、py3.8 AST、S00 baseline、`git diff --check`均PASS。
- 审查：helper/策略路APPROVE（MAJOR“profile校验零覆盖”已补64用例；MINOR幂等重装重装guard、签名含run_type已修复）；导出器/server/文档路APPROVE（无BLOCKER/MAJOR；两条MINOR为已声明的能力收窄）。
- 实现提交：`cd5ed99`。
- L00决定：DONE。

## L01：QMT Sync and Reconciliation

### 当前状态

- REVIEW：核心实现和9项定向测试完成，等待提交前联合回归与最终审查。

### 最小实现

- 同时支持同步QMT broker和BulletTrade异步server adapter采集账户、持仓、订单、成交快照。
- 只有通过`strategy_ledger_v1`能力合同的适配器才能进入同步；未完成真实探针时持久化BLOCKED。
- 已知broker trade按成交证据转换为现有`BrokerFill`并复用S09事务/去重/T+1入账。
- 未知订单、未知成交、丢失working order、现金差异或持仓差异均BLOCKED，不自动覆盖本地账本。
- 每轮结果写入`reconciliation_runs`并更新物理/策略账户状态；READY只解除`RECONCILIATION_BLOCKED`，不会清掉人工`TRADING_BLOCKED`或CLOSED。
- 首版为专用账户单策略；跨日遗漏最终通过现金/持仓差异失败关闭，真实lookback能力仍必须由QMT探针确认。

### 验证

- 空账户READY、真实买入fill及重复快照no-op、未知订单、未知成交、现金/持仓差异、撤单释放、kill switch保持、能力未证明BLOCKED、同步/异步采集。

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
