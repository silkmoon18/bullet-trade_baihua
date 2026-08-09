# StrategyLedger事务Repository

## 当前边界

S05提供单机SQLite repository和最小资金划拨原语，不提供下单或成交业务规则。`create_strategy_account`会在一个事务内检查物理账户未分配资金、扣减资金池、创建策略账户并记录`ALLOCATE`资金流水。S08仍需补齐真实券商余额校准、可重复启动和追加/收回资金等应用层流程。

首版每个写操作打开独立连接并执行`BEGIN IMMEDIATE`，由SQLite串行化写入者。不会引入ORM、分布式锁或多租户Unit of Work。

## 初始化

```python
from bullet_trade.server.strategy import SQLiteStrategyRepository

repository = SQLiteStrategyRepository(r"E:\bullet-trade-data\strategy-ledger.db")
repository.initialize()
```

运行目录必须位于仓库外。`initialize()`执行向前迁移；schema v3为`ledger_entries`和`strategy_events`安装禁止UPDATE/DELETE的append-only触发器，v4增加S07持久operation与outbox关联。

## 原子事件提交

`append_account_event(...)`在一个事务中完成：

1. 读取策略账户并校验`expected_ledger_version`。
2. 校验事件后的现金不为负、冻结不超过现金。
3. CAS更新`cash/reserved/ledger_version/event_seq`。
4. 以同一`event_seq`追加`ledger_entries`和`strategy_events`。
5. 全部成功后提交；任一步失败则整体回滚。

`ledger_version`和`event_seq`每次成功提交都加一。同一旧版本的两个并发写入最多一个成功，另一个得到`VersionConflictError`，调用方必须重新读取并重新规划，不能盲目重试原结果。

Repository只接受事件后的冻结金额和本次现金变化，不负责判断这些值是否来自合法订单或成交。S08/S09会在服务层计算业务结果，再调用本原子入口。

## 读取和重放

- `get_strategy_account()`读取物化账户。
- `list_ledger_entries()`和`list_events()`按`event_seq`顺序读取append-only记录。
- `replay_account()`在同一个SQLite读事务快照中，从初始资本和全部账本项重建现金/冻结/版本，并要求账本项、事件、物化账户完全一致。

重放不一致抛出`LedgerInvariantError`，不能自动覆盖数据库。真实运行时应转为交易阻断并进入后续S11对账流程。

## 错误语义

| 异常 | 含义 | 调用方动作 |
|---|---|---|
| `AccountNotFoundError` | 策略账户不存在 | 停止并检查初始化/strategy_id |
| `VersionConflictError` | 账户已被其他事务更新 | 重新读取和重新规划 |
| `LedgerInvariantError` | 金额、冻结、序列或重放不一致 | 阻断业务，不提交外部订单 |
| `RepositoryError` | SQLite约束或持久化失败 | 事务已回滚；记录并停止本轮 |

S05不实现自动重试和外部订单恢复，避免把数据库重试误扩展为券商下单重试。持久幂等与outbox在S07实现。
