# 2026-09-16：成交价未知时按保护价边界保守估算

## 背景与用户决策

2026-09-16 是 [26-zero-price-fallback.md](26-zero-price-fallback.md) 上线后第一个交易日，实盘暴露该文档第 22 条“零价买入不扣本金；零价卖出不增加本金现金”的后果。

当日 159063.XSHE、159295.XSHE 两笔市价卖出（`m_nOrderPriceType=88`）回报的成交价与成交金额均为 0，落成 `price_units=0, price_source=ZERO_FALLBACK, price_known=false`。零价入账使卖出回款完全不进策略现金，当日买入预算凭空少一笔钱：588900.XSHG 目标 2600 股、只成交 1900 股，目标达成率停在 6300/7000。

用户决策：

1. **保留市价卖出**（不改为限价单）；
2. 在记账时对**显式零价成交**（`price_source=ZERO_FALLBACK`）按保护价边界保守估算并补足账本金额——买卖两侧同样处理；
3. 只收窄到显式 0 价：委托价兜底（`ORDER_PRICE_FALLBACK`，价格非 0）保持原有行为，不被抬高到保护价边界；
4. 不建立“真实成交价返回后再校正”的通道，以估算为准。

零价本身的方向差异：卖出是现金流入，记 0 使流入少进（账本现金偏低）；买入是现金流出，记 0 使流出少出（账本现金偏高、且持仓成本只剩费用）。两者都要修正，才是对称的。

## 口径

只对 `price_source is ZERO_FALLBACK`（显式 0 价）的成交生效；委托价兜底与有价成交一律不变：

- 卖出取**保护价下沿**：`reference_prices_units[security] × (1 − 卖出 protect_price_band_ppm)`，作为回款下界（市价卖出保护带 15000 即 1.5%）。
- 买入取**保护价上沿**：`reference_prices_units[security] × (1 + 买入 protect_price_band_ppm)`，作为成本上界（限价买入保护带 2000 即 0.2%）。
- 现金：卖出 `cash_delta = 估算毛回款 − 已知费用`；买入 `cash_delta = −(估算毛成本 + 已知费用)`。
- 买入的估算毛额受该委托**已预留现金**约束（上限为 `预留 − 已知费用`），避免突破既有不变式 `buy fill exceeds order reserved cash`；被压缩时在依据里标 `capped_to_order_reservation=true`。
- 买入的估算额同时写入持仓成本，使 `positions.avg_cost_price_units` 不再退化成“只有费用”。
- **不写回成交价**：`fills.price_units` 仍为 0、`price_known` 仍为 0、`price_source` 仍为 `ZERO_FALLBACK`，价格 CHECK 约束未改，无新增迁移。
- 幂等：按差额补足（显式零价成交的已记毛额恒为 0），重复回报由既有 duplicate 检测拦截。
- 依据完整落在账本分录：`estimated_proceeds_units` / `estimated_cost_units` 与 `proceeds_estimate` / `cost_estimate`（含 `basis`、参考价、保护带、边界价、估算毛额、是否被预留压缩）。
- 缺少参考价或不带执行方案时不估算，退化为原行为（按 0 记账），不抛异常；有价成交一律不受影响。
- 飞书通知区分该情形：卖出“按卖出保护价下沿保守估算回款入账”、买入“按买入保护价上沿保守估算成本扣款入账”，均注明“非真实成交价，收益不准确”。

## 与 26 号文档的关系

26 号文档第 22 条对**价格未知的成交**不再适用；其第 25 条“已按 0 入账的成交不主动重算、正确恢复需要独立校正”继续遵守，本轮当日补记即按此办理。

## 实现与验收

改动集中在 `bullet_trade/server/strategy/fill_booking.py`：

1. 新增 `_conservative_sell_proceeds`（卖出下沿）与 `_conservative_buy_cost`（买入上沿，含预留压缩）。
2. 买入分支：估算额并入现金与持仓成本；`_fee_notification_detail` 支持买卖两种措辞。
3. `_refresh_position`：成交价为 0 的批次改用批次上已记录的估算成本参与均价重算，否则由 0 价重算会把成本错误地压成只剩费用（该分支只影响 0 价批次，其余批次推导不变）。

新增 10 项契约测试：卖出侧 5 项（精确补足且成交价仍不可信、缺参考价退化、委托价兜底不估算、可信价不受影响、估算后现金使后续买单可用），买入侧 5 项（按上沿扣款并修正成本、被预留压缩、缺参考价退化为只扣费用、委托价兜底不估算、有价买入不受影响）。

`tests/server` 1284 passed（改动前 1279）；`tests/unit` 与干净基线一致，无新增失败。`pyflakes` 无输出、`git diff --check` 通过；`mypy` 在该文件仍只有改动前就存在的 3 处 `_security_name` 告警，本次新增 0 处。

## 部署与当日校正

- 2026-09-16 20:00 收盘后部署**卖出侧**（当日已收盘，避免盘中重启）：备份到 `.data/backups/zero-price-sell-estimate-20260916-200022`（`fill_booking.py.before` / `.after` / `deploy.ps1` / `server-task.xml` / `server.env`），`py_compile` 通过后才停 `BulletTradeBaihua-Server`、杀监听进程、替换文件、起服；安装后 SHA256 与本地一致。重启未产生任何委托，当日订单仍为 5 笔。
- 提交 `aa7887c` 推送 `origin/codex/big-qmt-bridge`；服务器快进到同一提交，工作区干净，`strategy_ledger_ready=true`、big_qmt ready。
- 当日已有成交不主动重算（遵 26 号第 25 条），改为一次性独立校正：按同一口径补记 159063.XSHE 8,084,880 units（≈808.49 元）与 159295.XSHE 8,037,600 units（≈803.76 元），合计 16,122,480 units（≈1612.25 元）；`entry_type=SELL_PROCEEDS_ESTIMATE_CORRECTION`，逐笔以 `fill_id` 作 `reference_id`，脚本幂等。策略现金 266.51 → 1878.76 元，`ledger_version` 54 → 56，`replay_account` 与账本一致。校正脚本存于上述备份目录 `backfill.py`。
- 同一维护窗口轮换了 `QMT_SERVER_TOKEN`（聚宽侧已同步更新 `bt.configure`）；IP 白名单先启用后按用户要求撤回，最终仅靠 token。
- 同日 22:18 追加部署**买入侧与收窄后的触发条件**：提交 `355c586`（`aa7887c..355c586`）。服务器先 `fetch` 并核对目标提交与 `fill_booking.py` 对象哈希（`2f0d5c3e`）一致后才停服，停服后 `reset --hard` 到该提交、`py_compile` 通过再起服；备份在 `.data/backups/unpriced-fill-estimates-20260916-221826`。部署后工作区干净（文件与提交逐字节一致）、模块已含买卖两个估算函数、`strategy_ledger_ready=true`、当日订单仍为 5 笔、现金 1878.76 元与 `ledger_version=56` 未变、两条校正分录（16,122,480 units）完整。
- 当日 588900.XSHG 缺 700 股不会自愈：该 intent 于次日首次提交调仓时按既有规则自动 CANCEL，次日按新计划重新推导目标。

## 遗留

- `repricing=KEEP_ORIGINAL` 下当日未成交的差额不会自愈，需人工决定是否补单。
- 估算值只影响账本金额与成本，不影响券商侧；若同一成交日后返回有效价格，仍按既有成交冲突规则提示并需独立校正，不会静默改写。
