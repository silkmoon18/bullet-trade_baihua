# 聚宽校验与导出

## 产物边界

S03只生成“可上传候选”，不生成真实私有配置，也不授权真实资金。固定白名单包含：

| 导出文件 | 来源 | 用途 |
|---|---|---|
| `good_etf.py` | `strategies/joinquant/good_etf.py` | 复制到聚宽策略编辑器 |
| `bullet_trade_jq_remote_helper.py` | `helpers/bullet_trade_jq_remote_helper.py` | 上传到聚宽研究根目录 |
| `jq_runtime_config.example.py` | `jq_runtime/jq_runtime_config.example.py` | 私有配置模板；填写后另存为`jq_runtime_config.py` |
| `manifest.json` | 导出器确定性生成 | 文件角色、源/目标名、字节数和SHA256 |

前三个Python文件按原始字节复制。导出器对每个源文件只读取一次形成不可变内存快照，语法/能力校验、
契约解析、SHA256、文件写出和manifest全部消费同一快照；源文件在导出期间变化也不会生成“文件是一版、
manifest是另一版”的混合产物。`contracts`段固定记录`mode`、`strategy_id`、profile名/模块/schema、helper
API版本和marker；契约字段必须有且只有一个顶层字面量赋值，任一引用漂移都会在导出前失败。
manifest不写绝对路径、
生成时间、host、token、Webhook或账户值；相同源码重复导出的manifest完全一致。`production_ready=false`
表示它只是自动校验候选，真实聚宽、QMT模拟和小额实盘仍分别受S18至S20门禁约束。

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
`--private-profile`；私有文件仍不会进入导出目录或manifest。标准部署顺序是：

1. 在仓库内先确定并审查策略顶部的`PROFILE`、`MODE`和`STRATEGY_ID`；默认`MODE='BACKTEST'`不代表
   SHADOW/LIVE部署声明。不得在导出后或聚宽编辑器内再次修改这些值。
2. 在仓库外复制`jq_runtime_config.example.py`为`jq_runtime_config.py`，填写真实host/token及可选账户/TLS字段，
   使用`--private-profile`通过只读校验。
3. 以同一受控源码执行最终导出，核对`manifest.json`中的`contracts`和三个Python文件；上传后禁止手工编辑。
4. 不要把私有文件复制回仓库或导出目录，也不要把它加入manifest。
5. 停止旧聚宽策略并确认旧进程退出，再上传helper和已校验私有profile，最后原样复制导出策略并冷启动。

## 自动门禁

- 所有Python源按Python 3.8语法解析。
- 三种角色的可静态求值`__import__`与普通import使用同一角色白名单，并禁止导入`bullet_trade.*`服务器包；
  不可静态求值的动态导入只允许helper通过已校验的`profile_module`变量加载私有profile；所有角色都禁止
  直接访问`__builtins__`，动态namespace也不得通过下标、bound/unbound/getter `__getitem__`或`get`读取
  `__builtins__/__import__`键。
- 策略仅允许聚宽API、独立helper和所需标准库；`TYPE_CHECKING`必须在模块顶层无条件、单次、显式从
  `typing`导入，不得通过嵌套/条件/通配符导入或再次绑定，`joinquant_typing`必须位于该静态分支。
- 契约字段和`TYPE_CHECKING`的普通赋值、循环/with/异常别名、导入/定义，以及已纳入回归的直接或保存后的
  `globals/locals/vars`与静态保留键容器改写会被拒绝；helper自身API/schema/marker契约同样适用。三种角色
  均禁止索引`sys.modules`、调用`vars(obj)`以及保存/修改对象的原始`__dict__`namespace，helper的
  `setattr/delattr`属性名必须静态可判定；策略直接访问`__builtins__`也不属于允许能力。
- 策略和profile禁止文件、进程和直接网络能力；helper因职责需要允许受控socket/TLS/标准库和pandas。
- profile模板和可选私有profile校验都只接受固定字段的字面量赋值；模板host/token必须为空，私有文件必须
  非空且满足与helper一致的字段类型和范围。
- 策略的mode/profile/strategy_id/helper API/marker/schema引用必须与helper和profile模板交叉一致，固定
  版本值使用精确内建类型。
- 敏感字段（含常见`*_TOKEN`/`*_HOST`等配置名）不接受非空字面量、拼接/f-string/join/format静态构造值
  或常见Webhook/Bearer特征；`configure(...)`的直接和属性调用位置参数也纳入防误提交检查。
- clean-room测试仅使用导出Python文件验证导入，以及缺helper、helper API版本不匹配的失败关闭路径。
- 导出目标只有固定白名单文件和manifest，因此不会夹带日志、缓存、数据库、runtime或私有profile。

AST与特征扫描是防误提交门禁，不是Python沙箱、完备别名/数据流证明或完备的秘密检测器，不能证明聚宽私有API和撮合行为。
私有profile只有在显式传入`--private-profile`时才属于本次已校验输入；平台真实版本、导入和
调度行为必须在S18上传探针后确认。
