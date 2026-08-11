# -*- coding: utf-8 -*-
# flake8: noqa: F403,F405
"""BulletTrade 聚宽 SHADOW（只读影子）模式最小示例。

上传步骤：
1. 将 helpers/bullet_trade_jq_remote_helper.py 上传到聚宽研究根目录。
2. 参照 jq_runtime/jq_runtime_config.example.py 编写私有 jq_runtime_config.py
   并上传到聚宽研究根目录；其中 host/token 等字段在本地用占位符
   （如 <服务器地址>、<访问令牌>）管理，真实凭据只存在于聚宽私有文件中。
3. 将本文件内容复制到聚宽策略，以“模拟交易”方式运行。

SHADOW 语义：helper 校验 profile 后，把 order/order_value/order_percent/
order_target/order_target_value/order_target_percent/cancel_order 替换为
失败关闭的 guard；策略侧只记录日志，不会产生任何真实委托。
"""

from jqdata import *  # 聚宽内置环境

import bullet_trade_jq_remote_helper as bt

# ===== 部署契约 =====
PROFILE = 'demo-prod'         # 对应 jq_runtime_config.PROFILES 中的键
MODE = 'SHADOW'               # 只读影子模式：校验链路但禁止下单
STRATEGY_ID = 'demo_shadow'   # 必须等于 profile 中的 strategy_id


def initialize(context):
    # 安全门必须是第一条可执行语句：安装运行模式并阻断交易函数。
    state = bt.install_strategy_runtime(
        globals(),
        context=context,
        profile=PROFILE,
        mode=MODE,
        strategy_id=STRATEGY_ID,
    )
    log.info('运行时已安装 | mode={} reason={}'.format(state['mode'], state['reason']))

    set_benchmark('000300.XSHG')
    set_option('use_real_price', True)
    # 每交易日 14:50 记录一次组合快照（纯日志，不下单）。
    run_daily(record_portfolio_snapshot, '14:50', reference_security='000300.XSHG')


def process_initialize(context):
    """聚宽重启/代码刷新时调用；同签名重复安装幂等返回原状态。"""
    state = bt.install_strategy_runtime(
        globals(),
        context=context,
        profile=PROFILE,
        mode=MODE,
        strategy_id=STRATEGY_ID,
    )
    log.info('运行时已恢复 | mode={} reason={}'.format(state['mode'], state['reason']))


def record_portfolio_snapshot(context):
    """只记录日志的定时任务；SHADOW 下任何下单调用都会被 guard 阻断。"""
    portfolio = context.portfolio
    log.info('组合快照 | 可用={:.2f} 总资产={:.2f} 持仓市值={:.2f}'.format(
        portfolio.available_cash, portfolio.total_value, portfolio.positions_value))
    for code in sorted(portfolio.positions.keys()):
        position = portfolio.positions[code]
        log.info('持仓 | {} 数量={} 成本={:.4f} 现价={:.4f}'.format(
            code, position.total_amount, position.avg_cost, position.price))
