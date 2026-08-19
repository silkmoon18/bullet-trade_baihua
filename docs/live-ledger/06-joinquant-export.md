# 聚宽校验与导出

## 产物边界

S03只生成“可上传候选”，不生成真实私有配置，也不授权真实资金。固定白名单包含：

| 导出文件 | 来源 | 用途 |
|---|---|---|
| `good_etf.py` | `strategies/joinquant/good_etf.py` | 复制到聚宽策略编辑器 |
| `bullet_trade_jq_remote_helper.py` | `helpers/bullet_trade_jq_remote_helper.py` | 上传到聚宽研究根目录 |
| `jq_runtime_config.example.py` | `jq_runtime/jq_runtime_config.example.py` | 私有配置模板；填写后另存为`jq_runtime_config.py` |
| `manifest.json` | 导出器确定性生成 | 文件角色、源/目标名、字节数和SHA256 |

前三个Python文件按原始字节复制。导出器对每个源文件只读取一次形成不可变内存快照，语法/凭据校验、
SHA256、文件写出和manifest全部消费同一快照；源文件在导出期间变化也不会生成“文件是一版、
manifest是另一版”的混合产物，写出后还会重新读取比对SHA256。manifest确定性记录schema版本、
artifact kind、文件角色、源/目标名、上传名、字节数和SHA256，不写绝对路径、
生成时间、host、token、Webhook或账户值；相同源码重复导出的manifest完全一致。`production_ready=false`
表示它只是自动校验候选，真实聚宽、QMT模拟和小额实盘仍分别受S18至S20门禁约束。
L00已删除manifest的`contracts`段和跨文件契约一致性扫描；`strategy_id`以受控策略源码审查为准，mode和profile以已校验私有配置为准，不再由manifest重复声明。

单文件bundle不是标准路径，本slice不生成。标准部署仍是helper和私有profile上传一次，策略源码直接复制；
任何更新都必须遵守冷升级流程，不能在旧聚宽进程中reload。

## 使用方法

只检查仓库白名单源文件：

```powershell
python -X utf8 scripts/export_joinquant.py --validate-only
```

导出到默认忽略目录`dist/joinquant`：

```powershell
python -X utf8 scripts/export_joinquant.py
```

指定新目录：

```powershell
python -X utf8 scripts/export_joinquant.py --output C:\safe\joinquant-upload
```

目标必须不存在，且路径组件不得是symlink（包括断链）、Windows junction或其他reparse point；已有空目录也会
被拒绝，因此失败不会删除或改变调用前的目标。每次最终导出都选择一个全新目录，且只执行一次。真实私有
profile可在仓库外执行只读校验：

```powershell
python -X utf8 scripts/export_joinquant.py --validate-only `
  --private-profile C:\private\jq_runtime_config.py
```

该命令只按Python 3.8解析字面量数据，校验固定字段、类型、范围、profile名与`strategy_id`，并拒绝
`your-host`、`your-token`、`replace-me`等已知占位符；它不执行文件、
不复制文件、不计算或输出其hash，也不输出host、token或账户值。也可在正式导出命令中同时传入
`--private-profile`；私有文件仍不会进入导出目录或manifest。

退出码与输出：成功为0（`--validate-only`输出`JOINQUANT_EXPORT_VALIDATION_OK`，正式导出输出
`JOINQUANT_EXPORT_OK`，私有profile通过时另输出`JOINQUANT_PRIVATE_PROFILE_OK`）；校验失败为2
（stderr输出`JOINQUANT_EXPORT_ERROR`）；IO错误为3（stderr输出`JOINQUANT_EXPORT_IO_ERROR`）。

标准部署顺序是：

1. 在仓库内先确定并审查策略顶部的`VALIDATE_REMOTE_DURING_BACKTEST`和`STRATEGY_ID`。回测始终自动使用BACKTEST；模拟交易从私有
   `STRATEGIES[strategy_id]`读取profile和JQ/QMT_REMOTE，缺少键时使用`DEFAULT_PROFILE`并默认JQ。不得在导出后或聚宽编辑器内再次修改这些值。
2. 在仓库外复制`jq_runtime_config.example.py`为`jq_runtime_config.py`，填写真实host/token及可选账户/TLS字段，
   使用`--private-profile`通过只读校验。
3. 以同一受控源码执行最终导出，核对`manifest.json`中的文件SHA256和三个Python文件；上传后禁止手工编辑。
4. 不要把私有文件复制回仓库或导出目录，也不要把它加入manifest。
5. 停止旧聚宽策略并确认旧进程退出，再上传helper和已校验私有profile，最后原样复制导出策略并冷启动。

## 自动门禁

- 只处理固定白名单的三个源文件；源必须是仓库内的常规文件，symlink或越界路径直接拒绝。
- 所有Python源按Python 3.8语法解析（`ast.parse(feature_version=(3, 8))`）。
- 明显凭据扫描：文本正则拒绝飞书/Slack/Discord Webhook URL和Bearer特征；AST字面量检查拒绝常见
  `*_TOKEN`/`*_HOST`等敏感名称的非空、非占位符字面量，`configure(...)`的位置参数和调用关键字参数同样纳入检查。
- profile模板只接受`PROFILE_SCHEMA_VERSION`/`DEFAULT_PROFILE`/`STRATEGIES`/`PROFILES`的顶层字面量赋值；schema必须为v2，字段、类型
  和范围固定，模板host/token必须为空。
- 策略顶层`STRATEGY_ID`必须是非空字面量且符合命名单元；`--private-profile`按同一schema
  只读校验私有配置并解析该策略使用的profile和mode；不执行、不复制、不hash、不输出秘密。
- 导出目录必须不存在且不得经过symlink/junction/reparse point；先在同级临时目录写出并复核SHA256，
  再原子替换为目标，失败清理后保持调用前状态。
- clean-room smoke仅使用导出物验证Python 3.8语法、导入以及缺helper、缺profile、版本不匹配的失败关闭路径。
- 导出目标只有固定白名单文件和manifest，因此不会夹带日志、缓存、数据库、runtime或私有profile。

L00已删除AST角色门禁、别名/namespace对抗扫描、动态导入分析、`TYPE_CHECKING`绑定检查和跨文件契约重绑扫描；
保留的文本正则与字面量扫描只是防误提交门禁，不是Python沙箱、完备别名/数据流证明或完备秘密检测器，
不能证明聚宽私有API和撮合行为。私有profile只有在显式传入`--private-profile`时才属于本次已校验输入；
平台真实版本、导入和调度行为必须在S18上传探针后确认。
