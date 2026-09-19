# 模拟账户持仓在柜台消失时的人工账务调整

仅在 QMT 当前持仓持续缺失、没有可核对的卖出回报，且用户确认按外部持仓变更处理时使用。它不是成交恢复机制，也不能推断卖出时间、价格或费用。

`scripts/reconcile_external_positions.py` 是一次性管理工具，不参与聚宽决策或服务器正常下单。默认只预演；`--apply` 会先备份 SQLite，再在一个事务中：

- 要求策略已被对账阻断、无活动委托/意图、无冻结资金；最新 QMT 对账不超过 30 分钟，且阻断项必须仅为所列标的在 QMT 的持仓 0；
- 按原买入批次的剩余**账面成本**移除策略归属持仓，并登记非现金的 `ADJUSTMENT` 资金流；不修改策略现金或公共资金池；
- 保留原委托和成交，只新增 `EXTERNAL_POSITION_ADJUSTMENT` 分录与事件，不生成卖单、不记虚构的已实现盈亏；
- 校验账本重放、盈亏恒等式和外键。多笔资金流水使精确绩效保持不可用，不应把调整后的 NAV 当作精确收益。

操作时先停 BulletTrade 服务（不必停止 QMT），确认 QMT 仍显示标的持仓为 0，再用相同参数预演和执行。命令示例中的标的、数量、策略 ID 和引用号必须换成当次核实的事实：

```powershell
python scripts/reconcile_external_positions.py --database .data/strategy-ledger.db --strategy-id good_etf_remote --position 159613.XSHE=2800 --position 517180.XSHG=1600 --position 588900.XSHG=1900 --reference manual-qmt-position-adjustment-20260919
python scripts/reconcile_external_positions.py --database .data/strategy-ledger.db --strategy-id good_etf_remote --position 159613.XSHE=2800 --position 517180.XSHG=1600 --position 588900.XSHG=1900 --reference manual-qmt-position-adjustment-20260919 --reason "QMT current positions absent; broker sell evidence unavailable; user-approved external position adjustment" --backup-dir .data/backups --apply
```

执行后重启服务，确认新一轮对账 `READY`、策略持仓为 0、原成交仍在、无活动委托。策略可用现金保持调整前数值；如需要增加虚拟策略资金，应另走已存在的资金分配流程，不能在本工具中凭空充值。
