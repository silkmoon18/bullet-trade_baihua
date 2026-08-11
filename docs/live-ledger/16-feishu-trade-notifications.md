# 飞书交易卡片通知

服务端支持参考 `bilibili_notifier` 的飞书交互卡片。蓝色表示委托已提交，绿色表示成交，灰色表示撤单，红色表示拒单或错误。卡片固定展示标的、方向、状态、金额、数量、单价和时间；有值时同时展示订单号、成交号和费用说明。

## 服务端接入

Webhook 和签名密钥只放在服务器环境变量，不复制到聚宽策略：

```dotenv
FEISHU_WEBHOOK_URL=https://open.feishu.cn/open-apis/bot/v2/hook/replace_me
FEISHU_SIGNING_SECRET=
```

启动`bullet-trade server`时会自动创建notifier并注入StrategyLedger，无需策略或部署脚本再写Python接线。Webhook不复制到聚宽。

创建 `BrokerOrder` 时填写 `limit_price_units`，委托卡片会据此计算并展示委托单价和预估金额；成交卡片始终使用券商回报里的实际数量、实际单价及含费用金额。市价委托没有可用价格时，这两个字段显示 `-`，成交后由实际回报补全。

通知只在数据库事务提交成功后发送。飞书超时或发送失败只返回 `False`，不会回滚已经确认的订单或成交，也不会改变交易状态。

## 覆盖旧 `bt_quant` 脚本

本文件同时提供旧版同名类 `FeishuNotifier`，兼容以下旧调用：

- `FeishuNotifier()`
- `queue_message(text)` 与 `flush()`
- `send_text(text)`
- `send_rich_text(title, text)`

因此可以把 `bullet_trade/server/feishu_notifier.py` 直接复制并覆盖旧仓库根目录的 `feishu_notifier.py`，旧 `log.py` 无需修改。覆盖前在服务器设置一次环境变量 `FEISHU_WEBHOOK_URL`；机器人开启签名校验时再设置 `FEISHU_SIGNING_SECRET`。新文件不再携带写死的 Webhook。

旧的纯文本调用会改成橙色日志卡片。需要标的、金额、数量和单价的订单/回报应传入 `TradeNotification`；BulletTrade 新账本已经自动这样调用。

## 当前边界

- 已接入订单登记、成交、撤单和拒单事件。
- 同一订单或成交的幂等重放不会重复通知。
- 本切片不增加重试队列或通知后台任务；个人部署先依赖服务日志定位偶发失败。
- QMT同步和Strategy API已接入：委托提交、部分/全部成交、撤单、拒单及对账BLOCKED都会发送卡片。
