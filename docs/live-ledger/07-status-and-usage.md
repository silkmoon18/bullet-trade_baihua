# 当前状态与使用指南

> **结论：L01至L04的仓库代码已实现，但当前分支仍不能直接用于真实资金。** 真实QMT能力证明、聚宽SHADOW/QMT模拟和小额人工验收只能在用户目标环境完成；服务端交易开关默认关闭。

## 1. 现在已经具备什么

- `bt_quant`的有效策略内容已迁入统一BulletTrade仓库，旧仓库保留为迁移来源。
- 本地可通过`jqdata.pyi`、helper类型声明和严格mypy/pyright配置获得代码提示及静态检查。
- `good_etf.py`是仓库内唯一受控策略源码；导出器原样复制策略、helper和示例profile，并生成确定性manifest。
- 私有profile只读校验不会执行、复制、hash或输出其中的host/token等值。
- BACKTEST使用聚宽原生组合与下单函数；SHADOW只验证配置并记录计划；LIVE使用服务器真实PortfolioView和一次组合目标。
- 已具备真实券商可用现金校准、初始1万元单次分配、按订单冻结/释放和显式增减资的事务服务。
- 已具备按真实买卖成交更新现金、订单冻结、持仓lot、费用和已实现盈亏的事务服务，重复fill不会重复入账。
- 已具备基于新鲜行情的现金、持仓市值、总资产、NAV、费用和盈亏原子快照，可作为后续聚宽组合视图数据源。

## 2. 还缺什么

下列外部验证与部署能力尚未完成，因此不能宣称满足实盘要求：

1. 在用户实际MiniQMT/BigQMT上完成订单备注、稳定order/trade ID、费用、状态和跨日查询能力探针。
2. 聚宽SHADOW、QMT模拟和小额实盘的人工运行证据；真实资金必须再次明确批准。

完整依赖顺序见[实施 slices](04-slices.md)。只有L01至L04完成且真实交易日人工验收逐级通过，才能启用真实资金。

## 3. 当前推荐用法

### 本地开发

按[聚宽本地开发与兼容矩阵](05-joinquant-development.md)建立专用环境。编辑：

```text
strategies/joinquant/good_etf.py
```

不要维护第二份聚宽策略源码，也不要把本地profile、导出目录或凭据提交到Git。

### 聚宽回测

保持策略顶部：

```python
PROFILE = 'good_etf-prod'
MODE = 'BACKTEST'
STRATEGY_ID = 'good_etf'
```

先运行校验：

```powershell
python -X utf8 scripts/export_joinquant.py --validate-only
```

再导出到一个尚不存在的目录：

```powershell
python -X utf8 scripts/export_joinquant.py --output E:\temp\good_etf_joinquant
```

按[聚宽校验与导出](06-joinquant-export.md)上传产物。上传后不得手工修改；否则聚宽文件已不再对应manifest和已审查源码。

### SHADOW

SHADOW只用于验证profile合同和观察信号，不会提交真实订单。私有`jq_runtime_config.py`必须仅保存在本地/聚宽私有文件区，并显式传给导出器做只读校验；示例profile不能用于连接服务器。

### LIVE

代码路径已存在，但人工验收前保持`QMT_STRATEGY_TRADING_ENABLED=false`。必须上传v2 helper和私有profile；启动时真实资金不足、能力未证明、账实差异或对账不新鲜都会失败关闭。完整操作见[本机部署runbook](20-local-deployment-runbook.md)。

## 4. good_etf与原策略的关系

选股和风控主逻辑保持：ETF池、港股关键词过滤、前一日成交额区间、单位净值、停牌/涨停过滤、折价排序、前三名、按折价绝对值分配，以及5%止损/10%止盈。

执行与配置并非原样保留，修改是有意的：

- 删除策略内硬编码host、token、Webhook和账户定位，改为`PROFILE/MODE/STRATEGY_ID`合同。
- 删除旧同步追单扩展；持久幂等已在S07完成，异步执行状态机由L02实现。
- SHADOW改为只记录，不下单；LIVE通过StrategyLedger执行，人工验收前服务端交易开关保持关闭。
- 组合目标由`available_cash * weight`改为`total_value * DEPLOY_RATIO * normalized_weight`，避免把目标仓位误当成本轮新增买入额并在每轮缩小已有持仓。
- LIVE尾盘读取真实PortfolioView并通过自定义`record()`指标展示；聚宽内置模拟账户和内置收益曲线仍不代表真实券商账户。

因此可以说“策略思想和选股规则基本一致”，不能说“执行、资金和持仓语义与原版完全一样”。

## 5. 仓库与上游更新

推荐远端布局：

```text
origin   -> 个人GitHub fork（可推送）
upstream -> BulletTrade/bullet-trade（只拉取，push URL为DISABLED）
```

同步上游时先保持工作树clean，然后：

```powershell
git fetch upstream --tags
git switch feat/joinquant-live-ledger
git merge upstream/main
```

发生冲突时不得机械覆盖策略、helper、schema、迁移或放行文档；解决后必须重跑当前slice及其依赖测试并重新审查。若希望线性历史，可在未与他人共享分支时使用rebase，但不要对已共享分支强制推送。

## 6. 权威事实来源

- 当前阶段和恢复点：[当前 session](03-session.md)
- 完整架构和实盘缺口：[当前架构、依赖与状态](00-current-state.md)
- 不可变设计决策：[架构与工程决策](02-decisions.md)
- 逐slice验收条件：[实施 slices](04-slices.md)
- 本地编辑/类型检查：[聚宽本地开发与兼容矩阵](05-joinquant-development.md)
- 聚宽导出：[聚宽校验与导出](06-joinquant-export.md)

若本文与提交后的实现不一致，以代码、测试和最新`03-session.md`共同核对，不应仅凭旧聊天记录放行。
