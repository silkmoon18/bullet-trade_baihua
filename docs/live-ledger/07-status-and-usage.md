# 当前状态与使用指南

> **结论：当前分支尚不能用于真实资金。** S00至S07完成，S08正在实现；S09至S17尚未实现；S18至S20的聚宽、QMT模拟和小额实盘门禁尚未通过。`LIVE`会失败关闭，这是设计行为。

## 1. 现在已经具备什么

- `bt_quant`的有效策略内容已迁入统一BulletTrade仓库，旧仓库保留为迁移来源。
- 本地可通过`jqdata.pyi`、helper类型声明和严格mypy/pyright配置获得代码提示及静态检查。
- `good_etf.py`是仓库内唯一受控策略源码；导出器原样复制策略、helper和示例profile，并生成确定性manifest。
- 私有profile只读校验不会执行、复制、hash或输出其中的host/token等值。
- BACKTEST使用聚宽原生组合与下单函数；SHADOW只验证配置并记录计划；LIVE在StrategyLedger完成前强制阻断。

## 2. 还缺什么

下列能力尚未实现，因此不能宣称满足实盘要求：

1. 真实券商余额校准、策略级冻结资金、持仓批次和交易日状态的持久账本（S05已具备最小初始资金池划拨）。
2. 委托幂等、响应丢失恢复、部分成交/撤单/拒单入账和卖后买状态机。
3. QMT订单、成交、持仓和资金的持续摄取、归属映射与自动对账。
4. 基于真实成交的策略NAV、收益、费用、指标快照及回传聚宽的只读视图。
5. 权限、kill switch、监控告警、备份恢复、E2E、聚宽实测、QMT模拟和小额实盘放行证据。

完整依赖顺序见[实施 slices](04-slices.md)。只有S04至S17完成且S18至S20逐级通过，才能启用真实资金。

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

当前禁止使用。把`MODE`改为`LIVE`不会得到一个可交易系统，只会触发`LIVE_BLOCKED`/运行时错误。不要绕过门禁或直接调用旧helper下单接口。

## 4. good_etf与原策略的关系

选股和风控主逻辑保持：ETF池、港股关键词过滤、前一日成交额区间、单位净值、停牌/涨停过滤、折价排序、前三名、按折价绝对值分配，以及5%止损/10%止盈。

执行与配置并非原样保留，修改是有意的：

- 删除策略内硬编码host、token、Webhook和账户定位，改为`PROFILE/MODE/STRATEGY_ID`合同。
- 删除旧同步追单扩展；未来由S07/S13的持久幂等和异步执行状态机替代。
- SHADOW改为只记录，不下单；LIVE在S15和放行门禁前失败关闭。
- 组合目标由`available_cash * weight`改为`total_value * DEPLOY_RATIO * normalized_weight`，避免把目标仓位误当成本轮新增买入额并在每轮缩小已有持仓。
- 当前尾盘检查只记录聚宽模拟组合，不代表真实券商对账。

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
