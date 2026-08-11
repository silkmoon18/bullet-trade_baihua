# 当前 session

## Session元数据

- 日期：2026-08-11
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
- S00基线`7085155` DONE：基线校验脚本和Git格式检查均PASS，最终独立复审APPROVE。
- S01最终候选`a94aa12060c5e8cef479224952e302eeac99f37d`：预提交与精确SHA两阶段的契约、并发/对抗、部署/文档三路审查均APPROVE，S01状态DONE；逐轮REWORK历史见[S01逐轮审查历史](archive/03-session-s01-review-history-pre-l00.md)（候选结论均已失效，仅作历史记录）。
- S02至S10均DONE；各slice收口见[实施 slices](04-slices.md)，S01至S03逐轮审查明细见[归档](archive/04-slices-s01-s03-review-detail-pre-l00.md)。
- 尚未轮换外部token/Webhook；这是需要用户在对应平台执行的外部动作。

## 最近完成slice

`L00 Existing Code Pruning`（DONE，`cd5ed99`）

## 当前slice

`L01 QMT Sync and Reconciliation`（REVIEW）

- 新增同步/异步QMT快照采集和`SQLiteReconciliationService`。
- 已知fill复用S09原子入账；未知活动、现金/持仓差异和缺失working order持久化BLOCKED。
- READY不解除人工kill switch；未完成QMT能力探针时明确BLOCKED。
- 9项L01定向测试、86项StrategyLedger联合回归通过；pyright、flake8和Python 3.8检查通过。

## S03收口

- 第六次10文件冻结的部署/文档与独立功能审查均为APPROVE；第三个审查代理因平台误判未产出结论，主审使用相同冻结哈希、文件集合、格式和测试证据补足合同核对。
- 实现提交：`224a68195eeff11a542885344957132a294c5399`。
- 两路独立精确SHA终审均APPROVE：提交只含冻结10文件，127项S03测试和447项联合矩阵通过，双份全新导出字节一致，工作树clean。
- S03决定：DONE。该决定只放行“可上传候选”的生成与验证，不放行真实资金；S04至S20仍未完成。

## 下一步

完成L01提交前审查；通过后进入L02目标规划与执行。

## S04收口

- 新增`bullet_trade.server.strategy`领域模型：现金池、策略账户、持仓/lot、组合意图、订单、成交、账本项、事件和对账结果。
- 金额、价格和NAV使用固定整数尺度；入口拒绝float并使用`ROUND_HALF_UP`，成交/事件时间转为Asia/Shanghai，lot显式保存可卖交易日。
- SQLite迁移v1建立账户、现金池、账本和持仓；v2建立意图、订单、成交、事件、outbox、对账和扩展钩子。文件库启用WAL/FULL/外键。
- 12项S04定向测试通过；加入既有scheduler回归后24 passed。新模块完整flake8、Python 3.8 AST、targeted mypy、targeted pyright和S00 baseline均PASS。
- 首次收集因默认`jqdatasdk`未安装失败；按仓库约定使用离线easy_tdx stub后通过。这暴露既有顶层数据源初始化耦合，但不在S04扩大重构范围。
- 修复两个包入口的Python 3.8运行注解：`list[str]`改为兼容写法，避免StrategyLedger导入在目标版本先失败。
- 尚未接入服务器、校准真实账户或处理真实成交；这些能力继续由S06及后续slice完成。
- 首轮只读审查为REWORK：组合意图/事件/对账对象保留调用方可变Mapping，迁移历史不绑定SQL且未核对`user_version`。候选现改为构造时递归冻结JSON数据，迁移记录SHA-256并校验双版本源；新增3项回归后重新审查。
- 修复后的工作树复审为两路APPROVE。实现提交`6bfb4469f3b8d32a0121d164bd2af96ac3e94326`再次通过两路精确SHA复审：提交只含批准的14文件，工作树clean，12项定向/24项联合矩阵通过。
- S04决定：DONE。

## S05收口

- 新增`SQLiteStrategyRepository`：显式初始化、账户读取、CAS事件提交、append-only读取和账户重放。
- 每次写入使用独立连接和`BEGIN IMMEDIATE`；账户物化、ledger entry和strategy event在同一事务中提交。
- schema新增v3 append-only触发器，禁止UPDATE/DELETE账本项和策略事件；旧v1/v2只向前升级，不修改已应用迁移。
- `create_strategy_account`在单一事务内校验并扣减物理账户未分配现金、创建策略账户并追加初始`ALLOCATE`资金流水；资金不足时全部回滚。
- `replay_account`使用一个SQLite读事务快照读取账户、账本和事件，避免与并发写入交错产生假不一致。
- 10项repository回归覆盖初始划拨、资金不足/预留资金不可挪用、开户中途失败全回滚、提交/重放、并发快照、同版本双写、事件注入失败全回滚、负现金无半写和append-only约束；加入S04与scheduler后联合34 passed。新模块flake8、Python 3.8 AST、targeted mypy/pyright、S00 baseline和`git diff --check`均PASS。
- 当前仍不读取真实券商余额、不做可重复启动资金校准、不冻结订单资金或处理成交；这些由S08/S09完成。
- 首轮审查发现重放跨连接可能读到混合快照、初始资金无资金池来源，以及事务起点异常未统一收口；修复后重放改为单一读事务，开户原子扣减资金池并写资金流水。
- 第二轮审查发现开户必须按`unallocated-reserved`计算可用额，且缺少“资金池已扣减后建档失败”的回滚证据；修复后新增CAS可用额约束和故障注入回归。
- 最终工作树审查APPROVE；实现提交`9eb36f0`，限定矩阵34 passed。
- S05决定：DONE。

## S06实施计划

- 为MiniQMT和BigQMT建立同一能力结构，明确区分代码路径存在、需要真实QMT探针和明确不支持三种状态。
- 只保留StrategyLedger主闭环需要的能力：client tag回显、稳定order/trade ID、trade到order关联、side、费用、状态、working order以及orders/trades跨日lookback。
- 新增成交观察规范化：缺side时只按broker order ID映射订单；合成trade ID、费用字段缺失或无法映射side时不能通过`strategy_ledger_v1`。
- 适配器提供静态能力声明；S19再用真实QMT环境把`PROBE_REQUIRED`提升为已验证，本slice不伪造生产就绪。

## S06收口

- 新增`BrokerCapabilityProfile`与`require_strategy_ledger_v1()`，MiniQMT/BigQMT均显式返回静态profile；未经探针的能力保持`PROBE_REQUIRED`并拒绝实盘合同。
- 新增`BrokerTradeEvidence`规范化和批量去重：只接受原生broker trade ID，缺side仅按broker order ID映射，费用字段缺失时拒绝，同ID完全重复归并、冲突记录报错。
- MiniQMT成交结果保留`trade_id_source`、`commission_known`和`tax_known`；BigQMT规范化补齐同样的证据字段，显式区分真实0费用与缺字段默认0。
- 首轮审查发现MiniQMT无法解析的费用仍被标成已知0、负费用可进入证据，以及trade-order关联并不能证明order含side；候选已分别改为解析成功才标已知、拒绝负费用，并新增独立`order_side_for_trade`能力。
- 14项S06合同测试通过；加入S04/S05、MiniQMT订单/成交/等待、BigQMT adapter和scheduler后联合88 passed。新合同完整flake8、targeted mypy/pyright、变更文件Python 3.8 AST、旧文件阻断级flake8、S00 baseline和`git diff --check`均PASS。
- 修复后的工作树复审APPROVE；实现提交`8679bc9`。成交时间戳按slice边界在S09入账前加入，当前不能因此放行实盘。
- S06决定：DONE。

## S07当前计划

- 聚焦一次策略请求只产生一次持久operation和一次待发送outbox记录；不引入分布式消息队列或多租户lease框架。
- 同一`(strategy_id, endpoint, idempotency_key)`与相同payload重放既有结果，不同payload明确冲突。
- 外部下单前持久化client tag和operation；发送结果未知时保持`SUBMIT_UNKNOWN`，只能按S06查询合同恢复，不能自动重发。

## S07收口

- schema v4新增`strategy_operations`，outbox增加唯一`operation_id`关联；operation保存canonical payload hash、持久client tag、状态和响应。
- `SQLiteOperationRepository.create_operation()`原子创建operation/outbox；同key同payload重放原记录，不同payload冲突，100并发只有一个首次创建。
- `claim_next()`只重领尚未越过外部调用边界的过期claim；`begin_submission()`先把operation置为`SUBMITTING`，随后才允许调用QMT。
- 确定响应进入`COMPLETED`并持久化；响应未知或启动时发现遗留`SUBMITTING`进入`SUBMIT_UNKNOWN`，outbox停止投递。
- 首轮审查发现可变payload被读取两次，可能使operation hash与outbox effect不一致；候选改为首次canonical JSON后所有记录共享同一请求快照。
- 10项S07定向测试覆盖相同请求重放、payload冲突、单一请求快照、100并发、原子回滚、双worker单claim、确定响应、未知响应、重启隔离和调用前lease重领；加入S04至S06及scheduler后联合58 passed。新模块完整flake8、targeted mypy/pyright、Python 3.8 AST、旧文件阻断级flake8、S00 baseline和`git diff --check`均PASS。
- 修复后的工作树复审APPROVE；实现提交`661f153`。
- S07决定：DONE。

## S08当前计划

- 以券商可用现金校准物理账户现金池，检查聚宽配置的初始资金是否足够；不足明确报错，不创建策略账户。
- 已存在策略账户重复启动只返回原分配，不因聚宽初始资金变化静默重置；调整资金必须使用显式allocate/withdraw并写资金流水。
- 为下单资金提供reserve/release原子入口，使后续实际成交只按返回结果扣账；不在本slice实现订单规划或成交持仓。

## S08收口

- 新增`SQLiteCapitalService`：券商可用现金初始校准、策略账户幂等ensure、现金reserve/release和显式allocate/withdraw。
- 初始1万元只从已校准物理现金池原子划拨；真实可用资金不足不建账户。50并发ensure只分配一次，重复启动返回原账户，配置变化明确冲突。
- 策略账户存在后，券商可用现金必须等于物理未分配可用现金加策略可用现金；差异只报错，不覆盖本地账本。
- reserve/release在同一事务按`order_id`核对各订单剩余冻结额，避免重复释放某订单时误释放其他订单资金；显式资金调整由`external_ref`幂等，任何中途失败回滚资金池、账户、账本和资金流水。
- 首轮审查发现释放资金只校验账户汇总冻结额，重复释放订单A可能误释放订单B；修复后在同一事务按`order_id`重放该订单冻结余额，同订单可追加冻结，重复或超额释放明确拒绝。
- 10项S08定向测试、加入S04至S07与scheduler后联合68项通过；新模块完整flake8、targeted mypy/pyright、Python 3.8 AST和`git diff --check`均PASS。
- 修复后的工作树复审APPROVE；实现提交`4b2f164`。
- S08决定：DONE。

## S09当前计划

- 只处理真实成交入账：按broker trade ID去重，买卖成交原子更新现金、订单冻结、position/lot、费用和已实现盈亏。
- 先覆盖部分成交、撤单/拒单释放余款、卖出不存在持仓返回0、A股T+1可卖约束；不提前建设估值、聚宽指标回传或通用风控平台。
- 为QMT成交证据补齐可靠成交时间，并以账本重放和重复fill no-op作为本slice出口。

## S09收口

- 新增`SQLiteFillBookingService`：登记策略订单、真实成交入账、撤单/拒单终态和订单余款释放。
- 买入只按实际成交金额与费用扣现金，部分成交保留余单冻结，全部成交释放价格缓冲；真实卖出按FIFO lot扣持仓并按净回款增加现金。
- 买入lot显式保存下一可卖交易日；同日卖出、超卖和无持仓成交拒绝。卖出不存在持仓但券商返回拒单/零成交时，只更新订单终态，策略现金保持不变。
- broker trade ID或fingerprint重复成交为no-op，ID内容冲突拒绝；现金、订单、fill、position、lot、ledger和event在同一事务提交，故障全量回滚。
- QMT成交合同新增可靠`traded_at`，MiniQMT沿用`time`，BigQMT补齐常见原生时间字段映射；缺失或非法时间拒绝进入账本。
- 首轮审查发现BigQMT分离日期/时间可能被误解为1970年，以及同日lot按本地到达顺序而非成交顺序FIFO；候选现合成完整券商时间并让lot按真实`traded_at`排序。
- S09成交入账定向8项、S04至S09加scheduler联合77项通过；变更模块flake8、targeted mypy/pyright、Python 3.8 AST和`git diff --check`均PASS。
- 修复后的工作树复审APPROVE；实现提交`08081c9`。
- S09决定：DONE。

## S10当前计划

- 基于同一SQLite读事务生成现金、冻结、持仓市值、总资产、费用与已实现盈亏快照，作为后续聚宽PortfolioView唯一数据源。
- 行情输入必须带`as_of`，只实现缺价/过期价的明确阻断和最小估值，不扩展通用行情平台或复杂风控。
- 快照版本绑定账户ledger version和持仓版本，确保聚宽一次读取不会混合成交前后的状态。

## S10收口

- 新增`SQLiteValuationService`与只读`PortfolioSnapshot`：现金、冻结、可用现金、持仓市值、总资产、净投入资金、费用、已实现/未实现/总盈亏和NAV一次返回。
- 账户、持仓、lot、fill、资金流水和PnL账本均在同一SQLite读事务读取；并发成交发生在账户读取之后时，当前快照仍完整保持成交前版本。
- `MarketMark`显式携带证券、价格、来源和`as_of`；持仓缺价、价格过期或来自快照未来时不生成估值。
- `snapshot_version`绑定ledger/event、持仓版本和mark证据；相同账本与mark重算结果一致。
- 修复S09每股成本舍入尾差：lot剩余成本从原始fill总价费精确分摊，部分卖出用卖前与卖后剩余成本之差结转，佣金不能整除股数时也不丢金额。
- 初始固定资金场景可直接使用NAV；发生后续显式增减资时`performance_ready=False`，防止把简单资产/净投入比率误当成严格份额净值。
- 首轮审查发现物化`positions.sellable_qty`不会随T+1日期自动刷新，以及可变marks可能在校验与使用之间换批；候选现从同一lot快照按估值日计算可卖数，并在入口只复制一次mark证据。
- S10定向11项、S04至S10加scheduler联合88项通过；flake8、targeted mypy/pyright、Python 3.8 AST和`git diff --check`均PASS。
- 修复后的工作树复审APPROVE；实现提交`4e190cc`。
- S10决定：DONE。

## D023后的下一步计划

- L00已在`cd5ed99`完成并保留实盘账务底线。
- L01至L04依次完成单账户同步对账、目标规划执行、聚宽真实视图、本机部署与小额验收。

## 恢复检查表

新session继续前依次执行：

1. 阅读本文件与 `04-slices.md`。
2. 检查 `git branch --show-current`。
3. 检查 `git status --short`，不得覆盖用户未说明的修改。
4. 查看当前slice最近一次实现和review记录。
5. 仅在当前slice出口条件满足后更新下一个slice为IN_PROGRESS。
