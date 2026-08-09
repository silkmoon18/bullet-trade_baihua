# 持久幂等与Outbox

## 当前边界

S07提供单机SQLite的请求幂等和券商提交outbox，不直接调用QMT，也不创建目标组合、冻结资金或写成交。这些业务变化在后续slice通过专用服务与同一事务边界组合，当前模块先固定“一个请求只有一个operation和一个外部提交任务”的恢复语义。

首版面向个人单策略/单服务进程。SQLite lease只用于区分“尚未跨过外部调用边界，可重新领取”和“已经准备调用券商，结果必须视为未知”，不建设分布式消息平台。

## 创建与重放

```python
from bullet_trade.server.strategy import SQLiteOperationRepository

operations = SQLiteOperationRepository(r"E:\bullet-trade-data\strategy-ledger.db")
created = operations.create_operation(
    strategy_account_id="good-etf",
    endpoint="portfolio.submit",
    idempotency_key="2026-08-10-rebalance-1",
    payload={"targets": {"510300.XSHG": 1000}},
)
```

数据库唯一键是`(strategy_id, endpoint, idempotency_key)`：

- key相同且canonical payload hash相同，返回原operation，`replayed=True`，不新增outbox。
- key相同而payload不同，抛出`IdempotencyConflictError`。
- 首次请求在一个事务内写入`strategy_operations`和一条outbox；任一步失败全部回滚。
- `client_tag`在外部调用前生成并持久化，同时写入outbox payload。

operation的`response_json`是最终响应的持久副本。服务重启或聚宽重复请求时读取该记录，而不是依赖原服务器进程内存缓存。

## 外部调用边界

worker顺序固定为：

1. `claim_next(worker_id)`领取`PENDING`任务；领取后尚未发生外部效果，lease过期可由新worker重新领取。
2. 紧邻QMT调用前执行`begin_submission(outbox_id, worker_id)`，operation进入`SUBMITTING`。
3. 调用QMT一次。
4. 得到确定响应后调用`finish_submission(..., unknown=False)`，持久化响应并进入`COMPLETED`。
5. 请求已发出但响应丢失/超时时调用`finish_submission(..., unknown=True)`，进入`SUBMIT_UNKNOWN`且outbox不再投递。

进程启动时先调用`quarantine_inflight()`。任何遗留`SUBMITTING`都转为`SUBMIT_UNKNOWN`，因为系统无法证明崩溃发生在券商收到请求之前。后续只能使用S06已验证的client tag/order查询路径恢复，不能再次发送原请求。

## 尚未接入

- 现有通用`RemoteBrokerApplication`仍使用短期内存幂等缓存；正式StrategyLedger API在S14切换到本repository。
- intent、资金冻结和订单行将在S08/S12/S13的专用事务入口中与operation/outbox组合，不能先提交业务再单独补outbox。
- `SUBMIT_UNKNOWN`自动查询与人工处置流程在S11/S13实现。
