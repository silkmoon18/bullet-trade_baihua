# 架构与工程决策

## 决策记录

| ID | 决策 | 状态 |
|---|---|---|
| D001 | 统一到BulletTrade私有fork，但不合并含凭据的bt_quant Git历史 | Accepted；私有origin待配置 |
| D002 | 以 `v0.9.2/be0451b` 为改造基线 | Accepted |
| D003 | QMT是物理成交事实源，StrategyLedger是策略归属和绩效权威 | Accepted |
| D004 | 聚宽负责数据、信号、调度和展示，不承担权威实盘账本 | Accepted |
| D005 | 生产默认 `mirror_jq_orders=False` | Accepted |
| D006 | 真实指标由服务器账本计算，通过聚宽 `record()`展示 | Accepted |
| D007 | 初始1万元从未分配资金池原子划拨，重启不重置 | Accepted |
| D008 | 主调仓使用组合级TargetPortfolioIntent，不逐只同步追单 | Accepted |
| D009 | 幂等、订单、成交和事件必须持久化，内存TTL缓存不能作为生产保障 | Accepted |
| D010 | 未知订单、过期快照和HARD对账差异一律fail closed | Accepted |
| D011 | 单节点先SQLite WAL/FULL，多实例再PostgreSQL | Accepted |
| D012 | 第一阶段专用物理账户、单策略、禁止人工交易 | Accepted |
| D013 | 策略源码保持聚宽原生格式，本地通过jqdata兼容层和类型桩开发 | Accepted |
| D014 | helper/config一次上传后，策略文件可原样复制；单文件bundle仅作为可选产物 | Accepted |
| D015 | 生产策略token无权绕过账本调用raw `broker.place_order` | Accepted |
| D016 | 首版只支持专用物理账户、单策略、禁止人工交易；共享账户延期 | Accepted |
| D017 | 每个slice修复审查问题后必须对最终SHA再次独立复审 | Accepted |
| D018 | 自动化交付与真实交易日soak分开；外部门禁允许BLOCKED等待证据 | Accepted |
| D019 | S01采用profile schema v1和严格模式矩阵；StrategyLedger完成前LIVE必须失败关闭 | Accepted |

## D001：不合并敏感历史

原 `bt_quant` 已在 `e6462dd`形成完整检查点。其历史包含硬编码凭据，合并后即使删除当前文件也无法从Git历史移除。因此统一仓库仅导入脱敏后的有效源码，并在文档和文件头记录来源提交。

后果：旧提交历史需要在旧仓库查阅，但统一仓库不会传播历史密钥。

官方远端已改名为只读 `upstream` 并禁用push。用户提供私有fork URL之前不配置可写 `origin`，所有提交保持local-only。

## D003：双层事实与归属

券商只能说明物理账户发生了什么，不能天然说明某笔现金和持仓属于哪个策略。StrategyLedger通过订单标签、持久映射和成交事件完成策略归属，并作为策略NAV的权威来源。

后果：聚宽和任何策略都不得直接以整个券商账户执行 `order_target_value`。

## D005/D006：聚宽镜像与真实绩效分离

聚宽公开API不能注入一笔外部成交的实际数量、价格、费用和时间。`inout_cash`属于资金流，不可用于修补手续费。因此原生指标只能近似，真实绩效必须来自StrategyLedger。

后果：可选镜像只能用于操作体验，所有实盘决策和绩效读取 `real_*` 数据。

## D007：资本分配

首次创建策略账户时锁物理账户和未分配现金池，在同一事务内划拨。多个策略不能分别“检查后再扣减”，否则会并发超分配。

后果：修改聚宽初始资金不会静默重置已存在的实盘策略；必须显式执行审计化资本调整。

## D008：组合意图

逐只同步目标单无法原子表达组合、卖后买依赖和working order exposure，也容易在部分成交后重复提交。组合意图允许服务端统一规划和恢复。

后果：兼容层仍可保留单订单API，但正式调仓路径使用批量目标接口。

## D010：失败关闭

金融交易中“暂时不知道是否提交”比明确失败更危险。未知状态不得自动当作失败重试。

后果：系统可能主动暂停交易，但不会为追求可用性牺牲资金安全。

## D013/D014：本地开发与聚宽复制

同一策略文件保持 `from jqdata import *` 和独立helper导入。本地使用仓库根 `jqdata.py`、`.pyi`类型桩和受控PYTHONPATH；聚宽使用平台jqdata和已上传helper。

后果：可以消除导入红线和大部分API误用，但本地兼容引擎与聚宽私有撮合行为仍需契约测试，不能宣称绝对一致。

## D016：首版范围

首版不实现共享物理账户、多策略同标的归属或人工交易池。专用账户出现任何无法归属的人工订单、成交、现金或持仓都视为HARD差异并阻断。

后果：领域schema可预留未来扩展字段，但当前验收不承诺共享账户能力，避免在账本正确性尚未稳定时扩大资金归属复杂度。

## D017/D018：评审和外部门禁

实现提交接受第一次独立审查；修复findings后生成新的最终SHA，再由独立审查者复审该SHA或精确diff。只有最终复审通过才可DONE。

shadow、QMT模拟和小额实盘依赖真实交易日及用户外部授权，与自动化E2E实现拆开。没有足够证据时状态保持BLOCKED，不以文档完成代替真实验收。

## D019：过渡运行边界

聚宽策略默认`MODE='BACKTEST'`。BACKTEST不得读取私有profile或触达远端；SHADOW仅在`sim_trade`中运行，严格校验版本化profile但不建远程连接，并阻断runtime管理的下单/撤单入口；S01的LIVE只是旧兼容层验证路径，返回`production_ready=False`，`good_etf.py`必须据此拒绝启动。

后果：S01可以安全验证同源策略和配置契约，但不能被解释为已经具备实盘能力。只有S15替换为StrategyLedger runtime且S18至S20门禁通过后，才允许真实资金。
