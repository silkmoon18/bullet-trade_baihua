# Fork：聚宽 → BulletTrade → 大 QMT 本机桥接

适用于 miniQMT/直连 xtquant 不可用、但完整 QMT 可以运行 Python 交易策略的情况。
本文描述可选后端及配置方法；具体服务器是否已部署、交易是否启用，以该环境的当前配置和运行状态为准，不以本文的历史验收记录推断。
上游 HTTP 网关保留不变，本文的 `bridge` 是另一个传输选项，不要把两个 QMT 脚本同时运行。

## 职责和不变项

```text
聚宽 GoodETF 决策 → 原 JQ helper → BT 58620 → StrategyLedger / 执行器
                                                      ↕ localhost:9001
                                             大 QMT 通用单文件 → 券商
```

- 聚宽仍负责数据、选股、权重和风控决策，原 JQ 账户仍使用聚宽原生撮合。
- 服务器仍负责 QMT 策略归属持仓、资金分配、先卖后买、行情条件、追单、幂等、对账和飞书通知。
- 大 QMT 只调用原生查询、`passorder`、`cancel`，返回委托/成交/错误/账户/持仓/tick 回报；不选股，不建账本，不自行追单。
- GoodETF 的参考价和固定限价生成不变，不改为按卖一报价；服务器决定价格后原样传入 QMT。默认市价沿用原 xtquant 的五档即成剩撤（沪42/深47）；是否再发剩余量仍由原服务器执行规则决定。
- QMT 不需要下载 ETF PCF，也不调用 `get_etf_info`。行情、交易接口及订阅权限仍需实际券商客户端验证；本桥接不能绕过券商交易权限。

## 需要更新的文件

1. 服务器：安装此 fork 的 `codex/big-qmt-bridge` 分支，包括新增适配器与通信层。单独换 QMT 文件不够。
2. 大 QMT：复制 [helpers/big_qmt_bridge.py](../helpers/big_qmt_bridge.py) **整个文件**到新 Python 策略，例如 `BT_BRIDGE`。它是单文件，全部 ASCII，GBK/UTF-8 均可读，不需要安装 BulletTrade、xtquant 或额外 Python 库到 QMT 内部。
3. 聚宽：本次**不用更换** `good_etf.py`、`bullet_trade_jq_remote_helper.py` 或私有配置。继续使用 fork 的双账户门面，不要按上游其他教程替换为不同 API 的 helper。

`strategies/qmt/good_etf/good_etf.py` 是之前的“全策略在 QMT 内运行”备选，不是本桥接。保留，但不要与桥接执行同一策略。

## 顶部配置

大 QMT 单文件只需修改顶部配置，不改下面的执行代码：

| 字段 | 填法 |
|---|---|
| `ACCOUNT_ID` | 留空使用交易界面选定账户；识别不到时明确填同一个账户号 |
| `ACCOUNT_TYPE` | 当前只支持 `"STOCK"`，不支持信用/期货/多物理账户 |
| `BRIDGE_TOKEN` | 与服务器 `BIG_QMT_GATEWAY_PASSWORD` 一致；这是本机令牌，不是证券密码 |
| `BRIDGE_PORT` | 默认 `9001`，与服务器 `BIG_QMT_BRIDGE_PORT` 一致 |
| `ENABLE_TRADING` | 初次 `False`；仅在获准的模拟账户主动验证时改 `True` |
| `ENABLE_CANCEL` | 初次 `False`；主动验证时需同时设 `True` 才能撤测试委托 |
| `TIMER_MS` | 默认 `100`；非阻塞网络收发的定时周期，不是下单/追单间隔，也不是 tick 频率保证；QMT实际调度仍可能延后 |
| `MAX_SUBSCRIPTIONS` | 默认 `100`，仅订阅服务器需要的持仓和目标；按客户端配额调整 |

`20261016_bridge_v5`启动时分别检查账户、令牌和账户类型，错误只列出缺失字段，不输出凭据。兼容客户端提供的`accountID`和`account_id`，但不猜账户、不绕过配置检查。`waiting for BT`之前就报配置错误时，说明模型虽然被启动过，桥接实际上没有成功初始化。

`20261019_bridge_v6`将收发周期从500ms改为100ms，并为本机TCP启用`TCP_NODELAY`。命令结果和回调入发送缓冲后立即尝试一次非阻塞发送；短写或暂不可写的剩余字节继续缓冲，不循环等待、不重调`passorder`。这减少人为收发等待，不保证100ms内完成券商确认。断线、未知提交、写命令去重及原价格/数量规则保持不变；必须替换QMT模型并重启模型才能加载新收发逻辑。

每条命令的已有耗时回报另含`qmt_pump_gap_ms`，表示本轮与上一轮实际进入收发泵的间隔，首次为null；不增加接口查询或逐timer打印。它用于区分“配置100ms但宿主实际晚调度”和“原生接口本身慢”，不是命令单向网络耗时，也不是tick间隔。

已有服务器可生成私有完整文件，避免复制公开模板后漏填。公开`helpers/big_qmt_bridge.py`的凭据始终留空；下面的导出只读取已有配置、写入被Git忽略的`.data`，不连接券商、不启动模型、不改变运行中的交易开关：

```powershell
python scripts/export_big_qmt_bridge_private.py --env-file .data/.env --output .data/client-updates/BT_BRIDGE_REAL.py
```

默认生成文件的交易/撤单开关均为False。只有已明确授权的账户和用途，才分别加`--enable-trading`、`--enable-cancel`保留相应功能；这些参数不授予新的交易权限。服务器有多个账户时必须显式选择`--account-key`，不会猜测。输出文件已存在时拒绝覆盖；私有文件不得提交Git、发送到公开聊天或替换原服务器配置。

服务器配置示例在 [env.bigqmt-bridge.example](../env.bigqmt-bridge.example)。已有服务器应在**备份后的原 `.data/.env`**中增改：

```dotenv
QMT_SERVER_TYPE=big_qmt
BIG_QMT_TRANSPORT=bridge
BIG_QMT_BRIDGE_PORT=9001
BIG_QMT_GATEWAY_PASSWORD=填写本机令牌
QMT_STRATEGY_TRADING_ENABLED=false
QMT_STRATEGY_SIMULATION_VALIDATION_ENABLED=false
```

首次在已确认的模拟账户中直接使用真实策略信号验收时，可临时把
`QMT_STRATEGY_SIMULATION_VALIDATION_ENABLED`设为`true`。它只跳过“必须先有能力证明”
这一循环依赖，策略白名单、账本、对账、资金限制和幂等仍然生效。验证完成并写入实际
能力证据后必须改回`false`；真实资金账户不得使用。

保留原 `QMT_SERVER_TOKEN`、端口/公网访问限制、`QMT_ACCOUNT_ID`、账户 key、原 `QMT_STRATEGY_LEDGER_DB` 路径、通知和策略白名单。不要换一个空数据库或新策略 ID 来规避对账。

`QMT_STRATEGY_CAPABILITIES_FILE` 必须改为新后端实测确认的能力文件；**不得把 xtquant 的证明复制后改名为 BIG_QMT**。未验证时账本执行不放行是预期行为，不修改加载门禁来跳过验证。

## 启动和迁移顺序

1. 先关闭服务器策略交易，确认没有旧目标继续自动执行。停止旧 MiniQMT 执行服务/独立 GoodETF；检查并人工处理遗留委托，再备份原 `.data`（服务停止时备份数据库，保留 WAL/SHM 一并复制）。不要改旧上游仓库。
2. 在 fork 的服务器目录安装本版本，使用服务器原解释器，例如 `python -m pip install -e .`。不要仅 `pip install -U bullet-trade` 覆盖成官方包。
3. 按上面的差异修改私有配置。若原计划任务命令写死 `--server-type qmt`，同步改为 `big_qmt`，命令行参数优先于 `.env`。
4. 从 fork 根目录启动服务器：

   ```powershell
   python -m bullet_trade --env-file .data/.env server --server-type big_qmt
   ```

   `9001` 永远只监听本机 `127.0.0.1`，不用新增公网安全组。聚宽继续连接原 `58620`。

5. 登录 QMT 模拟资金账户，在**模型交易/策略交易**界面运行 `BT_BRIDGE`。不要使用历史回测或只在编辑器点击运行。QMT 的“实盘运行”表示调用所选账户的交易接口，不等于已确认它是模拟资金；须核实登录的资金账户确为模拟账户。
   同时在模型交易设置中勾选**终端启动后自动运行**。QMT 在交易日切换、行情断线重连或客户端重启时可能重新运行挂载模型；未勾选自动运行时，客户端重启后服务器只会等待连接，不能代替 QMT 启动脚本。
6. 初次脚本两个写开关都保持 `False`。预期出现 `[bt_bridge] ... waiting for BT`，随后 `connected | trading=False`；服务器健康中 `backend_type=big_qmt`、`transport=bridge`、`ready=true`。`ready` 说明当前连接和账户查询可用，不证明成交能力。
7. 完成下节验证后才讨论启用原策略白名单。阅读本说明本身不会部署服务、发送测试单或更改交易开关。

## 验收清单

先只读，再明确批准模拟账户测试；QMT 原生写开关与服务器策略总开关不是同一层：允许探针下单时，服务器 `QMT_STRATEGY_TRADING_ENABLED` 仍保持 `false`，防止探针与 GoodETF 并发。

- 只读：账户、持仓、当日委托/成交能查询；目标 ETF 的最新价、买卖一档、原始时间和价格边界有效；服务器最后行情时间正常更新。
- 原生回报：在交易时段用最小可交易数量验证限价买入、精确撤单、市价成交及清理；确认 client_tag、稳定委托号/成交号、成交与原委托关联、方向、状态。佣金/税费缺失继续标为未知。
- 恢复：重启 QMT 桥接，服务器变为未就绪再恢复；订阅和已有目标能重新对账。测试持仓与活动委托都必须清理；不要用修改账本来伪造清理成功。
- 旧账迁移：核对旧 xtquant `order_id` 与原生 QMT `m_strOrderSysID` 的对应关系。两者不能默认相同；若旧在途委托/历史成交的关联对不上，先只读核对并制定映射迁移，不删除旧记录、不改 ID 强行放行。本次未对你的历史库执行 ID 迁移。
- 策略：最后才仅开放获准的策略 ID。先观察一次完整交易日的计划、委托、成交、持仓和日终快照，再决定是否继续。切换完整 QMT 不代表真资金已获准。

现有 `python -m bullet_trade.server.runtime_probe --env-file .data/.env --output-dir .data/bridge-inspect` 默认只读，可复用；配置需要本机 `QMT_SERVER_HOST`。该旧探针也会测试历史 K 线，本桥接刻意不提供 `data.history`，这些项失败不能当作下单故障，亦不能把整份报告冒称全绿。主动 `--trade-smoke` 必须另外批准；此文不自动执行。

## 边界和恢复语义

- 一个物理 STOCK 账户可以承载多个服务器策略账本；不支持一个 QMT 桥接同时管理多个物理账户。
- 100ms 定时器负责非阻塞收发，每条写命令只调用一次原生接口，价格/tick/成交回报驱动原服务器状态机。初次下单短暂等待委托号（默认3秒），不是等待全部成交。行情由 QMT 推送，不保证每次交易所价格变化都收到。
- 服务器只对证券T+0/T+1这类静态规则按上海交易日和连接会话缓存，同一证券的并发首次读取合并为一次；跨日、断线或重连失效，错误和非0/1结果不缓存，显式配置覆盖每次优先读取。资金、持仓、委托和成交不使用该缓存，首笔快照的2秒与账户事件失效条件不变。
- 未来定时目标在执行时点前的普通唤醒只保留定时等待，不反复做全量账户查询。真实委托/成交等账户事件仍对账入账，执行时点到达后仍先读取新快照；不是拿09:26快照直接下单。
- fork 的 `bt:...` 唯一操作标识优先使用本次原生委托回报确认：稳定委托号、标识、标的、方向、数量、价格及时间证据须匹配，重复的同一委托回报合并。最多先等0.5秒回报，再查询原委托兜底；总确认窗口仍默认3秒，缺回报不重发。其他旧接口调用及HTTP后端保留原查询确认。这里的0.5秒不是行情时效，也不是固定睡眠，回报一到即继续。
- `20261012_bridge_v3` 只新增原生收命令、调用/返回 `passorder` 的Unix毫秒时间（`_bt_submit_timing`），不改价格、数量、市价类型或500ms定时器。新服务器兼容旧v2；使用v2时这几项时间为空，不能据此推断原生调用时间。v3的时间不是柜台或交易所确认时间。
- `passorder` 的 `0`、`None` 或其他返回值不当作订单号；查询/回调里稳定的真实委托号才可确认。断线或超时的写入按未知态处理，查询认领后继续，不盲目重发。
- 服务器复用原 SQLite 委托/成交历史，跨日返回的是**曾实际观察并保存**的数据，不代表 QMT 原生历史接口可查任意日期。断线跨越交易日、且未收到过的记录不能凭空恢复，需人工核对。
- `stop()` 仅断开本机连接和退订行情，不自动撤券商委托。交易回路停止后仍须核对在途委托。
- 暂不支持本地历史数据策略、PCF、期货/信用、跨机器桥接。旧 `BIG_QMT_TRANSPORT=http` 仍可用，但不具有本桥接的原生回调接入链路。

## 依据和验证记录

- 原生定时器、停止回调和历史回测限制：[迅投系统函数](https://dict.thinktrader.net/innerApi/system_function.html)。
- QMT 单线程环境，不在策略中创建 Python 线程或阻塞服务循环：[迅投使用须知](https://dict.thinktrader.net/innerApi/user_attention.html)。
- 53=部撤、54=已撤、55=部成，沪深市价类型：[迅投枚举](https://dict.thinktrader.net/innerApi/enum_constants.html)。
- 查询外的原生实时回调：[迅投回报函数](https://dict.thinktrader.net/innerApi/callback_function.html)。
- 计划、上游评估、提交和测试记录：[本次 session](live-ledger/43-big-qmt-bridge-plan.md)。
