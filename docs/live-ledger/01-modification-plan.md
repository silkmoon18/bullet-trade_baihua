# 完整修改计划

## 1. 最终目标

构建一个成熟、可控、高健壮性的聚宽实盘量化系统：策略在聚宽侧使用同一份源码进行回测、影子运行和实盘信号计算；BulletTrade持久化策略级资金与持仓账本，QMT负责真实成交；真实指标返回聚宽展示。

## 2. 最终数据权威

```text
QMT/券商真实委托与成交
          ↓
BulletTrade StrategyLedger
  ├─ 策略现金/冻结
  ├─ 策略持仓/可卖/成本
  ├─ 订单与成交归属
  ├─ NAV/TWR/回撤/费用
  └─ 对账与审计
          ↓
聚宽 PortfolioView + record()
```

聚宽原生模拟账户只作为可选镜像。生产默认 `mirror_jq_orders=False`。

## 3. 仓库与开发体验

1. 以 `v0.9.2` 为上游基线，在私有fork分支开发。
2. 统一维护服务器、helper、策略、类型桩、导出工具和测试。
3. 策略源码保持聚宽格式，不导入服务器内部包。
4. 增加 `jqdata.pyi`、helper类型桩和策略typing模块。
5. 统一本地 `jq-dev` 解释器和依赖版本。
6. 增加一键校验/导出：语法、静态检查、禁用导入、敏感信息扫描、helper版本握手。
7. 正常路径为“helper和运行配置一次上传，策略源码直接复制”；另提供生成单文件bundle的可选工具。

## 4. StrategyLedger

新增独立 `bullet_trade/server/strategy/` 子系统，避免把现有账户路由器堆成账本：

- `domain.py`：金额、数量、账户、持仓、意图、订单、成交和状态。
- `repository.py`、`migrations/`：事务、CAS/锁、迁移和事件序列。
- `ledger.py`：资金分配、冻结、成交入账、费用、成本和现金流。
- `planner.py`：目标组合转目标整手数量，扣除现有仓位和working orders。
- `orchestrator.py`：卖后买、部分成交、撤单、超时和恢复。
- `broker_sync.py`：订单/成交全量重扫、游标和去重。
- `reconciliation.py`：物理账户、策略归属和未分配池对账。
- `valuation.py`、`performance.py`：NAV、单位净值、TWR、回撤、费用和换手。
- `risk.py`：策略级、物理账户级和执行级风控。
- `api.py`、`workers.py`：策略API、outbox、同步和后台任务。

## 5. 初始资金与资金池

首次创建策略账户时：

1. 读取聚宽配置的初始资金，例如10000元。
2. 锁定物理账户及 `UNALLOCATED_CASH_POOL`。
3. 校验未分配现金足够覆盖10000元和安全垫。
4. 在同一事务内从未分配池划拨至策略账户。
5. 写入append-only allocation事件。

重启只加载原账本，绝不重新分配或重置。以后调整资本必须使用显式、审计化的allocate/withdraw事件。

## 6. 目标组合执行

策略一次提交完整目标：

```text
strategy_id
rebalance_id
idempotency_key
expected_ledger_version
targets(weight/value/qty)
cash_buffer
price_policy
deadline
```

目标金额公式：

```text
strategy_nav = cash_balance + Σ(position_qty × mark_price)
investable = strategy_nav × deploy_ratio
target_value_i = investable × normalized_weight_i
```

服务端统一处理：

- 策略自有仓位。
- working order exposure。
- 整手、最小订单和费用缓冲。
- 先减仓/卖出，按实际回款重新规划买入。
- 漂移容差、追价上限、截止时间和最大换手。

## 7. 成交入账和状态机

成交以券商 `trade_id` 或稳定fingerprint唯一去重，在单个数据库事务中：

1. 插入fill，重复则no-op。
2. 锁定策略账户、订单和持仓。
3. 更新现金、冻结、数量、lot、成本和已实现盈亏。
4. 更新累计成交和订单状态。
5. 释放终态剩余冻结。
6. 追加ledger entry和strategy event。
7. 递增 `ledger_version`并提交。

未知提交必须进入 `SUBMIT_UNKNOWN → RECONCILING`，未查清前禁止同策略继续调仓。

## 8. 持久幂等和outbox

- `(strategy_id, endpoint, idempotency_key)` 数据库唯一。
- 相同key和相同payload返回原operation；不同payload报冲突。
- 创建intent、冻结资产和写outbox同一事务。
- worker提交券商，响应丢失后按持久client tag查单，禁止盲重发。
- 一个物理账户同一时刻只有一个active executor/lease。

## 9. 对账

触发时点：启动、QMT重连、盘前、活动订单期间、`submit_unknown`、收盘和人工触发。

顺序：

1. 全量重扫当日orders/trades并去重入账。
2. 对齐working orders。
3. 对比物理持仓与策略owned lots加未分配持仓。
4. 对比物理现金与策略现金、冻结和未分配池。

数量、现金、未知订单/成交属于HARD差异，必须置为 `RECONCILIATION_BLOCKED`。禁止自动覆盖账本，修复只能追加带原因和证据的adjustment事件。

## 10. 聚宽运行层

统一 `TradeRuntime/PortfolioView`：

- `BACKTEST`：聚宽原生portfolio/order。
- `SHADOW`：读取真实快照和生成目标，但不下单。
- `LIVE`：读取策略账本并提交目标组合。

策略只关心：

```python
portfolio = runtime.portfolio(context)
runtime.rebalance(targets)
runtime.poll_events()
runtime.record_metrics()
```

真实资金、冻结、持仓、成本、open orders、版本、时间戳和对账状态由原子快照返回。策略不再根据整个物理账户自行计算target delta。

## 11. 策略逻辑改造

- 保留现有ETF筛选和折价评分作为初始alpha。
- 使用策略NAV而不是available cash计算最终目标。
- 增加部署比例、现金缓冲、整手组合分配器和最小交易额。
- 区分 `VALID_EMPTY` 与 `DATA_ERROR`。
- 增加最小折价阈值、排名滞回和止损冷却。
- 09:31后或验证行情时间戳后执行。
- 止盈止损读取真实策略成本，风险退出优先且不与调仓并发。
- 若没有同时间IOPV，将指标明确为昨日NAV偏离分数并限制适用品种。

## 12. 指标展示

服务器计算并返回：

- real_nav、unit_nav、daily/total return、TWR、drawdown。
- cash_balance、available_cash、reserved_cash、positions_value。
- realized/unrealized PnL、commission、tax、slippage、turnover。
- fill ratio、order latency、pending/unknown count、reconcile状态。

聚宽通过 `record()`展示。原生收益曲线明确标记为模拟镜像，不作为权威实盘绩效。

## 13. 安全与部署

- 私有fork；官方仓库设为 `upstream`。
- 轮换已经暴露的token和Webhook。
- 策略代码只保留profile、mode和strategy_id。
- TLS、VPN/反向隧道、allowlist、strategy-scoped token和日志脱敏。
- 服务启动顺序：DB迁移、取得账户lease、连接QMT、恢复任务、重扫成交、对账通过、READY。
- 单节点可先使用SQLite WAL/FULL；多实例或多策略成熟后迁移PostgreSQL。
- 第一阶段专用账户、单策略、禁止人工交易。

## 14. 总体验收

- 断网、超时、重复请求和服务重启不产生重复订单。
- 部分成交后下一轮只补真实差额。
- 策略现金、冻结、持仓和费用可由append-only事件重放得到相同结果。
- 任意HARD账实差异自动阻断新单。
- 真实NAV可由初始资本、现金流、逐笔成交、费用和持仓行情复算一致。
- 同一份策略源码通过本地静态检查、BulletTrade策略测试和聚宽语法/API约束校验。
