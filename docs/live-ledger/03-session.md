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
- S01 v4首轮工作树预审继续为REWORK，又发现异常进程状态失败开放、无helper兜底接受旧远程portfolio、helper内部ImportError被误判为缺失、未知profile字段名回显、超大整数逃逸稳定错误、并发双BACKTEST给失败namespace遗留guard等问题。第二轮修复后的预审仍发现孤儿`TRANSITIONING`可恢复成功、超大API版本错误不稳定，以及无helper兜底在已加载其他helper别名时仍有context getter远程窗口。第三轮修复将孤儿态直接转FAILED，要求精确`ModuleNotFoundError`的traceback证明helper本体尚未执行，并在context前拒绝任何已加载helper别名和旧remote portfolio；预审仅余策略期望API和helper实际API两个超大内部版本的稳定错误MINOR。第四轮工作树已对API比较两侧统一使用有界安全显示。162项相关测试、阻断级flake8、Python 3.8 AST、基线验证和Git格式检查已通过，第四轮契约、安全、对抗预审均APPROVE；当前可提交候选，但精确SHA三方复审通过前仍不能关闭S01。
- 尚未开始真实StrategyLedger实现。
- 尚未轮换外部token/Webhook；这是需要用户在对应平台执行的外部动作。

## 当前slice

`S01 JoinQuant Source and Profile Contract`

当前目标：

- 策略只保留`PROFILE`、`MODE`、`STRATEGY_ID`部署契约。
- 三个合法模式都在原子登记owner时先建立进程远程门禁；BACKTEST不读取profile、不连接网络、不替换聚宽原生函数，有helper时检查历史远程污染、无helper时允许纯回测兜底；SHADOW/LIVE还会先禁止namespace mutation并清除旧客户端，且不连接服务器或接管portfolio；LIVE的namespace变化仅是本地fail-closed保护。
- runtime只接受普通字符串mode和真实模块`globals()`字典；并发、递归、热重载、污染状态或在途RPC不能发布虚假成功契约。
- 移除迁移策略对旧定制helper API的调用。
- 缺helper、旧helper版本、缺profile和无效配置fail-fast且不泄露token。

## 下一步

完成S01 v4预提交独立审查，修复全部finding后提交代码和行为文档；随后对新的精确SHA重新执行安全、契约和对抗三方复审。三方全部APPROVE后才能关闭S01并进入S02类型桩与IDE支持。

## 恢复检查表

新session继续前依次执行：

1. 阅读本文件与 `04-slices.md`。
2. 检查 `git branch --show-current`。
3. 检查 `git status --short`，不得覆盖用户未说明的修改。
4. 查看当前slice最近一次实现和review记录。
5. 仅在当前slice出口条件满足后更新下一个slice为IN_PROGRESS。
