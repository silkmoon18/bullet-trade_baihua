# 聚宽本地开发与兼容矩阵

## 目标

`strategies/joinquant/good_etf.py`是唯一策略源码，可原样复制到聚宽。运行时仍使用
`from jqdata import *`和聚宽研究根目录中的`bullet_trade_jq_remote_helper.py`；本地IDE通过同名
`.pyi`补全，不把`bullet_trade.*`服务器包导入策略。

`joinquant_typing.pyi`只描述结构类型。策略仅在`TYPE_CHECKING`分支导入它，聚宽运行时不会尝试
加载该文件。`Context`、`Portfolio`、`Position`和行情`Snapshot`因此有字段补全，而上传文件仍保持
同一份源码。

## 一键设置

在仓库根目录执行：

```powershell
python scripts/setup_joinquant_dev.py
```

脚本创建`.venv`，先确保`pip>=21.3`以支持PEP 660 editable安装，再以`--no-deps` editable安装源码，
最后安装受约束版本范围内的轻量mypy/pyright检查器并运行门禁；
它使用较小的mypy源码包，避免大体积二进制wheel在受限网络中被截断后触发哈希失败，也不会为纯策略
编辑下载Jupyter、绘图和数据源全家桶。需要同时在该解释器运行BulletTrade本地引擎时使用：

```powershell
python scripts/setup_joinquant_dev.py --full
```

`--full`会额外安装BulletTrade基础运行依赖和仓库`dev`依赖（包括pytest），但不声称安装每一种可选
券商或数据源extra；需要某个可选后端时仍应显式安装对应extra。

PyCharm选择`.venv\Scripts\python.exe`作为Existing Interpreter即可。脚本会验证解释器的`sys.prefix`确实
匹配目标虚拟环境，并拒绝仓库根目录、非空非虚拟环境目录以及指向目标环境之外的`site-packages`。
验证通过后，脚本会在该虚拟环境写入只含仓库根目录
和`helpers`目录的`.pth`路径文件，因此PyCharm、mypy、pyright和普通Python解析器看到同一套源码，
无需复制helper或手工标记Sources Root。

本slice保证源码工作区和editable install的开发体验，不宣称普通wheel已携带顶层`jqdata.pyi`；正式
发布包布局在S17部署产物门禁处理，避免把类型文件错误安装到虚拟环境前缀而非`site-packages`。

轻量环境的日常检查：

```powershell
python -m mypy --config-file mypy.joinquant.ini
python -m pyright -p pyrightconfig.joinquant.json
```

使用`--full`安装的完整开发环境才运行仓库pytest门禁：

```powershell
$env:PYTHONUTF8='1'
$env:DEFAULT_DATA_PROVIDER='qmt'
python -m pytest tests/test_joinquant_typings.py -q -o addopts="" -p no:cacheprovider
```

项目级`[tool.mypy]`保留给旧代码；S02门禁只读取`mypy.joinquant.ini`，其中
`ignore_missing_imports=False`且`strict=True`，不能被全局宽松设置掩盖；真实策略
`strategies/joinquant/good_etf.py`和独立契约探针都在严格检查范围内。
pyright只关闭`reportMissingModuleSource`：这是因为`joinquant_typing`有意只提供`.pyi`、不提供可执行
模块；缺少类型桩仍按error处理。动态防篡改代码中pyright无法理解、但已由运行时精确
`type(...) is ...`门禁保证的少量表达式使用行级、
带错误码的抑制，不能使用文件级静默。

## 兼容矩阵

| 项目 | 本地开发契约 | 聚宽托管事实 | 门禁/处置 |
|---|---|---|---|
| Python | 语法下限3.8；mypy约束为`>=1.8,<1.12`以保留3.8目标支持 | 未核验，不猜测具体补丁版本 | S02用3.8 AST编译；S18运行平台探针 |
| pandas | BulletTrade声明`>=1.3,<3`；策略只依赖DataFrame常用索引/排序 | 未核验 | 避免依赖新版本专属API；S18记录`pd.__version__` |
| numpy | BulletTrade声明`>=1.21`；策略不依赖新版本专属API | 未核验 | 显式使用内建`any`，避免`np.any(generator)`差异；S18记录版本 |
| `jqdata` | 根模块`jqdata.py`与`jqdata.pyi`同名 | 聚宽内建模块 | 导出集合与参数名称/种类/必填性测试；S18平台smoke |
| helper | `helpers/bullet_trade_jq_remote_helper.py/.pyi`同名 | 用户上传到研究根目录 | 导出集合与参数名称/种类/必填性测试；marker/API精确校验 |
| 策略源码 | 仓库内唯一文件，IDE只消费类型信息 | 原样复制/后续由S03导出 | 禁止本地专用运行分支；hash门禁在S03 |

## 当前策略使用的API

| API/模型 | 本地类型 | 聚宽用途 | 当前边界 |
|---|---|---|---|
| `Context.portfolio/subportfolios/run_params/current_dt/previous_date` | 结构化 | 生命周期与组合状态 | 平台对象结构待S18确认 |
| `Portfolio.positions/available_cash/total_value/positions_value` | 结构化 | 调仓和展示 | S15前仍是聚宽回测组合 |
| `Position.total_amount/avg_cost/price/value` | 结构化 | 目标仓位与风控 | S15后由真实账本视图提供 |
| `get_all_securities/history/get_extras/get_current_data` | 有声明 | 选池、历史数据、快照 | 行为差异需平台smoke |
| `order_target/order_target_value/cancel_order/get_open_orders` | 有声明 | 交易入口 | BACKTEST/JQ原生可用；QMT_REMOTE由runtime阻断并改走StrategyLedger |
| `install_strategy_runtime` | TypedDict状态 | 模式/profile门禁 | 不等于已启用真实资金 |
| helper账户/订单/成交/持仓 | 结构化返回 | 后续账本和对账 | 业务接入在S04-S15 |

类型检查只能发现名字、字段和调用形状问题，不能证明聚宽私有撮合、数据时间边界或真实成交语义。
这些外部事实必须保留为S18-S20证据门禁。
