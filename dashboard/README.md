# 白话量化策略监控台

只读查看 BulletTrade `StrategyLedger` 的策略资金、持仓、目标、委托、成交和
server 日志。网页端没有任何下单、撤单或交易开关接口。

运行时需要两个仅服务端可见的环境变量：

- `BULLET_TRADE_DASHBOARD_URL`：Windows 服务器的看板 HTTP 地址；
- `BULLET_TRADE_DASHBOARD_TOKEN`：与服务器 `.env` 中
  `QMT_DASHBOARD_TOKEN` 相同的独立 token。
- `BULLET_TRADE_DEFAULT_STRATEGY_ID`：首次打开时默认展示的策略 ID。

本地未配置这两个变量时会显示演示数据，方便开发页面。正式发布由 Sites 托管，访问者
必须先通过 ChatGPT 登录，浏览器不会接触服务器 token。
