# 当前 session

## Session元数据

- 日期：2026-08-08
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
- S00最终候选为`7085155`；基线校验脚本和Git格式检查均PASS，正在进行最终独立复审。
- 尚未开始真实StrategyLedger实现。
- 尚未轮换外部token/Webhook；这是需要用户在对应平台执行的外部动作。

## 当前slice

`S00 Repository Baseline and Documentation`

出口条件：

- 文档覆盖现状、计划、决策、session和全部slices。
- 迁移来源和安全边界可追踪。
- `.idea/`、缓存、日志和runtime状态不会进入新提交。
- 文档链接有效，Git diff经过审查。

## 下一步

完成S00最终SHA复审后进入S01：策略源码、导入和profile契约。类型桩与导出工具分别在后续独立slice完成。

## 恢复检查表

新session继续前依次执行：

1. 阅读本文件与 `04-slices.md`。
2. 检查 `git branch --show-current`。
3. 检查 `git status --short`，不得覆盖用户未说明的修改。
4. 查看当前slice最近一次实现和review记录。
5. 仅在当前slice出口条件满足后更新下一个slice为IN_PROGRESS。
