# 飞书交易卡片通知

服务端支持参考 `bilibili_notifier` 的飞书交互卡片。蓝色表示委托已提交，绿色表示成交，灰色表示撤单，红色表示拒单或错误。卡片固定展示标的、方向、状态、金额、数量、单价和时间；有值时同时展示订单号、成交号和费用说明。

## 最小接入

Webhook 和签名密钥只放在服务器环境变量，不复制到聚宽策略：

```python
import os

from bullet_trade.server.feishu_notifier import FeishuTradeNotifier
from bullet_trade.server.strategy import SQLiteFillBookingService


notifier = FeishuTradeNotifier(
    webhook_url=os.environ["FEISHU_WEBHOOK_URL"],
    secret=os.environ.get("FEISHU_SIGNING_SECRET", ""),
)
booking = SQLiteFillBookingService(
    database_path="data/strategy-ledger.db",
    notification_handler=notifier.send,
)
```

创建 `BrokerOrder` 时填写 `limit_price_units`，委托卡片会据此计算并展示委托单价和预估金额；成交卡片始终使用券商回报里的实际数量、实际单价及含费用金额。市价委托没有可用价格时，这两个字段显示 `-`，成交后由实际回报补全。

通知只在数据库事务提交成功后发送。飞书超时或发送失败只返回 `False`，不会回滚已经确认的订单或成交，也不会改变交易状态。

## 当前边界

- 已接入订单登记、成交、撤单和拒单事件。
- 同一订单或成交的幂等重放不会重复通知。
- 本切片不增加重试队列或通知后台任务；个人部署先依赖服务日志定位偶发失败。
- 后续 QMT 同步切片只需把真实订单和成交送入现有登记/入账服务，不再另写一套通知代码。
