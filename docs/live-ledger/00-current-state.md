# 当前架构、依赖与状态

更新时间：2026-08-08（Asia/Shanghai）

## 1. 仓库与版本状态

### BulletTrade 统一仓库

- 路径：`E:\dev\Github\bullet-trade`
- 上游：`https://github.com/BulletTrade/bullet-trade.git`
- 基线标签：`v0.9.2`
- 基线提交：`be0451b`
- 开发分支：`feat/joinquant-live-ledger`
- S00策略和文档将压平为 `v0.9.2` 之上的单个脱敏基线提交，避免传播返工期间出现过的敏感审查特征。
- 官方远端已改名为只读 `upstream`；fetch URL保留官方GitHub，push URL为`DISABLED`。
- 用户私有 `origin` 尚未配置；在提供私有fork URL前，本分支仅允许本地提交。
- `.idea/`、根目录`runtime/`、聚宽导出目录和本地运行profile已经加入忽略规则。

### 原 bt_quant 仓库

- 路径：`E:\dev\pycharm\bt_quant`
- 分支：`master`
- 当前状态检查点：`e6462dd`
- 远端：`https://gitee.com/SilkMoon18/bt_quant.git`
- 原仓库暂时保留为只读安全副本，尚未删除。

没有把两个 Git 历史直接合并。原因是 `bt_quant` 历史包含硬编码的远程访问凭据和飞书Webhook，直接合并会把敏感信息永久带入未来可推送的统一仓库。统一仓库只导入了脱敏后的策略源码，并在文件头记录来源提交。

## 2. 当前目录职责

```text
bullet-trade/
├─ bullet_trade/
│  ├─ core/                 回测、调度、订单模型、风险控制
│  ├─ data/                 聚宽兼容数据API及数据源适配
│  ├─ broker/               本地/远程QMT券商适配
│  ├─ server/               远程交易服务、协议、账户路由
│  ├─ compat/               jqdata兼容导出
│  ├─ reporting/            回测报告
│  └─ utils/                通用工具
├─ helpers/
│  ├─ bullet_trade_jq_remote_helper.py
│  └─ jq_remote_strategy_example.py
├─ strategies/
│  └─ joinquant/
│     └─ good_etf.py        从bt_quant脱敏导入的迁移基线
├─ jqdata.py                本地 `from jqdata import *` 兼容入口
├─ tests/                   单元、集成、E2E和策略测试
├─ docs/                    项目文档
└─ pyproject.toml           打包、依赖和开发工具配置
```

## 3. 当前运行架构

### 聚宽侧

当前策略依赖：

- 聚宽平台提供的 `jqdata`、`context`、`g`、`log`和定时任务。
- `helpers/bullet_trade_jq_remote_helper.py` 作为上传到聚宽研究目录的单文件helper。
- 聚宽负责选股、取数、触发下单和日志展示。

`v0.9.2` 已提供 `install_jq_compat(...)`：回测保持聚宽行为；模拟盘可接管常用下单函数和策略可见的 `context.portfolio`。该接管是Python代理，不会修改聚宽内部撮合账本。

### BulletTrade服务器侧

当前能力包括：

- TCP协议、token、TLS和连接处理。
- MiniQMT/BigQMT适配。
- 账户、持仓、订单、成交查询。
- 下单、撤单、订单状态归一。
- `submit_unknown`等基础不确定状态表达。
- `sub_account_id`路由和单笔限额。

当前不具备：

- 持久化的策略级现金和持仓账本。
- 1万元策略资金的原子分配与冻结。
- 策略持仓归属和共享账户隔离。
- 持久幂等、成交去重、事务outbox。
- 组合目标执行器和卖后买状态机。
- 策略级真实NAV/TWR/回撤。
- 可阻断交易的账实对账系统。

## 4. 依赖现状

### Python与基础依赖

- Python：`>=3.8`
- pandas：`>=1.3,<3.0`
- numpy：`>=1.21`
- matplotlib、plotly、pyecharts：图表和报告
- jqdatasdk：聚宽数据源
- python-dotenv：服务器配置
- filelock：本地运行锁
- jupyterlab/ipykernel：研究环境

### 可选依赖

- `qmt`：`xtquant`
- `qmtserver`：Windows `pywin32`
- `tushare`、`rqdata`、`tdx`：替代数据源
- `report`：报告截图相关依赖

### 开发依赖

- pytest、pytest-cov、pytest-mock、pytest-asyncio
- black、flake8、mypy、isort

### 聚宽平台特有依赖

聚宽运行时不能假设安装完整 `bullet_trade` 包。可直接复制的策略必须只依赖：

- `from jqdata import *`
- Python标准库和聚宽内置库
- 已预先上传到聚宽研究根目录的独立helper/config文件

## 5. 本地与聚宽代码一致性现状

仓库根目录已有 `jqdata.py`，本地策略可以保持聚宽原生写法：

```python
from jqdata import *
```

本地运行时它会转发到 `bullet_trade.compat.jqdata`。这已经解决运行导入问题，但IDE体验仍不完整：

- 动态 `from ... import *` 的类型推断有限。
- `g`、`log`、`context.portfolio`等聚宽动态对象缺少精确类型桩。
- `helpers/` 默认不一定是IDE Source Root，顶层helper导入可能提示未解析。
- 本地Python/pandas版本与聚宽运行环境可能不同。
- 本地兼容实现只能保证已覆盖API的契约接近，不能自动保证聚宽私有引擎行为完全相同。

因此后续需要增加 `.pyi` 类型桩、统一开发环境、API契约测试和聚宽导出校验。

## 6. 当前 good_etf 策略状态

应保留的部分：

- `avoid_future_data=True`、`use_real_price=True`。
- 使用前一交易日构造ETF池。
- 流动性和港股类ETF过滤。
- 折价排序、前N只和折价绝对值权重。
- 停牌/涨停过滤、多时点风控和日终对账思想。

已知问题：

1. 当前增强版用 `available_cash * weight` 作为 `order_target_value` 的最终目标，是直接逻辑错误。最终目标应基于策略虚拟NAV，而不是剩余现金。
2. 当前同步追单循环可能长时间阻塞聚宽回调。
3. 目标差额按整个物理账户计算，无法隔离人工/其他策略持仓。
4. 订单超时查重仍依赖模糊匹配，不能提供持久幂等。
5. `cancel_all_open_orders()` 在缺少策略标签时可能回退撤全部，实盘不可接受。
6. 09:30调仓和09:30风控可能对同一标的产生冲突。
7. 昨日单位净值与今日开盘价不是严格同时间折价。
8. 1万元、3只ETF受到100份整手和最低佣金显著影响。
9. 数据故障与“有效但无候选”尚未严格区分。
10. 文件仍是迁移基线，已关闭真实信号并清空连接凭据，禁止直接用于真实资金。

### 6.1 与 v0.9.2 helper 的已知API差异

迁移策略来自 `bt_quant@e6462dd` 的定制helper调用，当前不能直接搭配上游 `v0.9.2` helper运行。S01必须先消除以下差异，未完成前策略初始化会失败：

| 迁移策略调用 | v0.9.2状态 | 处理决策 |
|---|---|---|
| `configure(jq_order=..., jq_order_value=..., jq_order_target=..., jq_order_target_value=...)` | 4个参数均不受支持 | 改用 `install_jq_compat(...)` 接管聚宽同名函数，后续主调仓改为runtime组合意图 |
| `configure(send_signals=...)` | 不支持该参数 | 由明确的BACKTEST/SHADOW/LIVE模式控制 |
| `configure(feishu_webhook_url=...)` | 不支持该参数 | 通知移到服务器，不在策略内持有Webhook |
| `configure(strategy_name=...)` | 不支持该参数 | S01使用稳定`STRATEGY_ID`作本地契约；S14由strategy-scoped API正式承载 |
| `bt.notify(...)` | 上游helper无此函数 | 改为服务器状态事件/通知接口 |
| `bt.cancel_all_open_orders()` | 上游helper无此扩展 | 后续仅允许按strategy intent取消，禁止账户级回退 |
| `bt.order_target_sync(...)` | 上游helper无此扩展 | 由组合执行状态机异步完成 |
| `bt.order_target_value_sync(...)` | 上游helper无此扩展 | 改为一次提交TargetPortfolioIntent |

S01的入口不是“策略已经可运行”，而是“保留一份脱敏、可追踪、明确不可运行的迁移基线”。S01完成后才允许把导出smoke作为可运行证据。

## 7. 安全现状

- 原策略曾硬编码远程token、服务器地址和飞书Webhook；这些值应视为已经暴露，必须在外部系统轮换。
- 新仓库导入版本已移除这些值，并默认 `SEND_SIGNALS=False`。
- 官方公共仓库已配置为只读 `upstream` 并禁用push；在用户提供私有fork URL后再添加可写 `origin`。
- 生产配置不得提交到Git，日志不得打印token、Webhook或完整账户信息。

## 8. 当前基线结论

当前分支适合作为统一改造起点，但尚不具备真实资金上线条件。P0条件是完成开发体验、协议契约、策略账本、持久幂等、真实成交入账和硬对账闸门。
