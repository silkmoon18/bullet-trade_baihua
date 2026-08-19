# 本机部署、备份与小额验收

## 当前放行结论

代码已具备本机运行入口、滚动日志、每日SQLite在线备份、恢复工具、飞书交易卡片和启动对账。真实QMT能力验证及JQ/QMT模拟/小额实盘仍必须在用户机器人工执行；mock测试不能替代这些证据。

在这些步骤完成前保持：

```dotenv
QMT_STRATEGY_TRADING_ENABLED=false
```

## 1. 准备专用账户和目录

StrategyLedger只归属带有本策略订单标记的委托、成交和持仓，允许QMT物理账户存在人工交易或其他策略资产。若外部操作导致物理现金或持仓不足以覆盖策略账本，仍会阻断。建议把运行数据放在仓库外：

```text
E:\bullet-trade-data\
├─ .env
├─ strategy-ledger.db
├─ strategy-capabilities.json
├─ logs\
└─ backups\
```

复制`env.example`为上述`.env`并填写QMT、server、StrategyLedger和可选飞书配置。私有文件不要提交Git。

## 2. 运行真实QMT探针

先启动QMT和BulletTrade，交易开关仍为false。执行：

```powershell
.\.venv\Scripts\python.exe -m bullet_trade.server.runtime_probe `
  --env-file E:\bullet-trade-data\.env `
  --output-dir E:\bullet-trade-data\runtime-probe `
  --trade-smoke
```

`trade-smoke`会产生真实小额委托，执行前必须确认标的、数量和模拟/小额账户。结合`probe_report.json`及QMT原始查询，跨进程、跨交易日人工核对：remark回显、稳定order/trade ID、trade-order关联、方向、明确费用、订单状态、当前与working查询、前一交易日lookback。

佣金以QMT/券商明确返回的数据为最终依据。迅投标准股票`XtTrade`提供`traded_id`、成交量、成交价和成交金额，但官方结构没有佣金字段；`used_commission`属于期货持仓统计字段，不能预设为股票逐笔成交佣金。部分柜台或扩展版本可能在成交或`query_data(..., data_type='deal')`中补充费用，服务器会兼容读取；买入费用缓冲只用于下单前现金预留，不是最终佣金。字段缺失或目标QMT版本没有明确费用证据时保持Remote交易阻断，不回退到聚宽`set_order_cost`估算。

复制[能力证明模板](strategy-capabilities.example.json)到仓库外，只把实际证明为真的字段改为`true`，填写真实报告路径和lookback天数。任一必需项为false时服务器会拒绝加载，不能靠代码猜值或补零。

## 3. 首次只读启动

```powershell
Set-Location E:\dev\Github\bullet-trade
.\.venv\Scripts\python.exe -m bullet_trade --env-file E:\bullet-trade-data\.env server
```

服务器启动后会对已存在策略账户立即同步QMT；若QMT仍在启动，则保持`strategy_ledger_ready=false`并每5秒重试，成功后停止重试。第一次由聚宽调用`ensure_account`建立1万元策略账户。交易关闭时能力证明可以暂缓，账本仍可返回`READY`；将`QMT_STRATEGY_TRADING_ENABLED`改为`true`后，能力证明缺失会立即重新阻断。

日志已使用`RotatingFileHandler`，单文件5MB、保留3个历史文件。配置`QMT_SERVER_LOG_FILE`即可，无需另一套日志轮转程序。

## 4. Windows计划任务

在已经创建`.venv`后执行：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\windows\register_strategy_tasks.ps1 `
  -ProjectDir E:\dev\Github\bullet-trade `
  -EnvFile E:\bullet-trade-data\.env `
  -LedgerDatabase E:\bullet-trade-data\strategy-ledger.db `
  -BackupDir E:\bullet-trade-data\backups
```

注册两个当前用户、有限权限任务：登录时启动server（失败最多重启3次），每日18:00在线备份SQLite。QMT通常依赖登录桌面会话，因此不注册无人登录的SYSTEM服务。

## 5. 备份与恢复

手工备份：

```powershell
python scripts\strategy_ledger_backup.py backup `
  --database E:\bullet-trade-data\strategy-ledger.db `
  --output-dir E:\bullet-trade-data\backups
```

恢复前停止BulletTrade server，把当前数据库及`-wal/-shm`文件整体移到单独故障目录，然后恢复到不存在的目标；工具拒绝覆盖已有数据库：

```powershell
python scripts\strategy_ledger_backup.py restore `
  --backup E:\bullet-trade-data\backups\strategy-ledger-YYYYMMDD-HHMMSS.db `
  --database E:\bullet-trade-data\strategy-ledger.db
```

恢复后先保持交易关闭，启动server并确认启动对账READY，再恢复聚宽策略。

## 6. 飞书卡片

配置Webhook后自动发送：`JQ/QMT_REMOTE`目标买入计划，以及QMT_REMOTE的委托提交、部分成交、全部成交、撤单、拒单和对账阻断。计划卡片汇总标的、本轮新增买入数量和总金额；交易卡片包含标的、方向、状态、金额、数量、单价、时间，以及可用时的订单号/成交号。通知发送失败只记为通知失败，不回滚真实账本。

## 7. 人工验收顺序

1. 聚宽`JQ`至少观察若干完整交易日：选股、权重、时间、聚宽模拟订单/持仓/指标和目标计划卡片符合预期。
2. QMT模拟账户完成买入部分成交、撤单、拒单、卖出零成交、T+1、进程重启和同一调仓key重放。
3. 核对0重复订单、0未解释账实差异，备份恢复演练成功，飞书卡片字段正确。
4. 用户再次明确批准准确小额金额后，才把`QMT_STRATEGY_TRADING_ENABLED=true`用于专用小额账户。
5. 至少完成一个小额真实买卖闭环和日终对账后，再单独决定是否提高到1万元。

GoodETF的远程执行验收顺序应为：条件未满足时QMT无挂单；卖一/买一进入固定边界后按边界价挂单；活动限价单保持原订单；订单终态仍有剩余量时仅在当日按策略补单；止损目标使用市价类型并追踪当日剩余量。行情推进来自QMT tick回调，订单、成交和错误推进来自QMT trader回调；启动、重连和收盘仍执行主动对账。

本仓库不会因代码测试通过自动打开真实交易，也不会替用户批准资金。
