# 当前架构、依赖与状态

更新时间：2026-08-08（Asia/Shanghai）

## 1. 仓库与版本状态

### BulletTrade 统一仓库

- 路径：`E:\dev\Github\bullet-trade`
- 上游：`https://github.com/BulletTrade/bullet-trade.git`
- 基线标签：`v0.9.2`
- 基线提交：`be0451b`
- 开发分支：`feat/joinquant-live-ledger`
- S00策略和文档已压平为 `v0.9.2` 之上的单个脱敏基线提交 `7085155`，返工期间的提交不再位于分支可达历史。
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
│  ├─ bullet_trade_jq_remote_helper.py  单文件helper及版本化运行入口
│  └─ jq_remote_strategy_example.py
├─ strategies/
│  └─ joinquant/
│     └─ good_etf.py        脱敏、同源的聚宽策略
├─ jq_runtime/              私有profile schema、示例和上传说明
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

S01候选在同一helper上增加了`install_strategy_runtime(...)`和profile schema v1：BACKTEST不读取profile或连接网络；SHADOW严格校验profile但不建远程连接，并阻断策略namespace中的交易变更；LIVE只保留非生产兼容验证路径。`good_etf.py`在StrategyLedger完成前会拒绝LIVE启动。

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

S01候选已经处理：

- 删除host、token、Webhook和账户定位等策略内连接配置，只保留`PROFILE`、`MODE`、`STRATEGY_ID`。
- 移除旧定制helper的同步追单、账户查询、全账户撤单和通知调用。
- 过渡期组合目标改为`context.portfolio.total_value × DEPLOY_RATIO × normalized_weight`，不再用可用现金直接计算最终目标。
- 尾盘仅记录聚宽组合快照，不再把旧helper返回的整个物理账户误报为策略级对账。

仍然存在的问题：

1. 过渡目标仍使用聚宽组合总资产；实盘必须改用StrategyLedger的策略虚拟NAV、working exposure和费用缓冲。
2. 生产级异步执行、卖后买和部分成交恢复尚未实现；不能把S01兼容路径用于真实资金。
3. 策略级资金/持仓归属、持久幂等和券商硬对账尚未实现。
4. 09:30调仓和09:30风控可能对同一标的产生冲突。
5. 昨日单位净值与今日开盘价不是严格同时间折价。
6. 1万元、3只ETF受到100份整手和最低佣金显著影响。
7. 数据故障与“有效但无候选”尚未严格区分。

### 6.1 与 v0.9.2 helper 的已知API差异

迁移策略来自 `bt_quant@e6462dd` 的定制helper调用。下表保留迁移差异及S01候选的处理结果：

| 迁移策略调用 | v0.9.2状态 | 处理决策 |
|---|---|---|
| `configure(jq_order=..., jq_order_value=..., jq_order_target=..., jq_order_target_value=...)` | 4个参数均不受支持 | 已移除；统一调用版本化runtime入口 |
| `configure(send_signals=...)` | 不支持该参数 | 已移除；由BACKTEST/SHADOW/LIVE模式控制 |
| `configure(feishu_webhook_url=...)` | 不支持该参数 | 已移除；通知后续移到服务器事件 |
| `configure(strategy_name=...)` | 不支持该参数 | 已改为稳定`STRATEGY_ID`；S14由strategy-scoped API承载 |
| `bt.notify(...)` | 上游helper无此函数 | 已移除；S01只写聚宽日志 |
| `bt.cancel_all_open_orders()` | 上游helper无此扩展 | 已移除；回测仅撤聚宽可见挂单，生产后续按strategy intent撤单 |
| `bt.order_target_sync(...)` | 上游helper无此扩展 | 已移除；生产由组合执行状态机异步完成 |
| `bt.order_target_value_sync(...)` | 上游helper无此扩展 | 已移除；生产改为TargetPortfolioIntent |

S01候选只证明源码/profile边界可测试：BACKTEST可运行，SHADOW只生成计划；LIVE仍被明确阻断。S03导出smoke、S15 StrategyLedger runtime和S18至S20真实门禁均不可省略。

## 7. 安全现状

- 原策略曾硬编码远程token、服务器地址和飞书Webhook；这些值应视为已经暴露，必须在外部系统轮换。
- 新仓库策略已移除这些值；默认`MODE='BACKTEST'`，且S01的LIVE明确失败关闭。
- 官方公共仓库已配置为只读 `upstream` 并禁用push；在用户提供私有fork URL后再添加可写 `origin`。
- 生产配置不得提交到Git，日志不得打印token、Webhook或完整账户信息。

## 8. 当前基线结论

当前分支适合作为统一改造起点，但尚不具备真实资金上线条件。P0条件是完成开发体验、协议契约、策略账本、持久幂等、真实成交入账和硬对账闸门。
