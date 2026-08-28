# -*- coding: utf-8 -*-
# flake8: noqa: F403,F405
"""BulletTrade 聚宽 JQ（模拟交易 + 计划通知）模式最小示例。

上传步骤：
1. 将 helpers/bullet_trade_jq_remote_helper.py 上传到聚宽研究根目录。
2. 参照 jq_runtime/jq_runtime_config.example.py 编写私有 jq_runtime_config.py
   并上传到聚宽研究根目录；其中 host/token 等字段在本地用占位符
   （如 <服务器地址>、<访问令牌>）管理，真实凭据只存在于聚宽私有文件中。
3. 将本文件内容复制到聚宽策略，以“模拟交易”方式运行。

JQ语义：策略照常调用聚宽原生交易接口，由聚宽模拟账户完成撮合、
持仓和指标计算；helper只把目标计划发给BulletTrade生成通知，绝不提交QMT目标。
"""

from jqdata import *  # 聚宽内置环境

import bullet_trade_jq_remote_helper as bt

# ===== 部署契约 =====
STRATEGY_ID = 'demo_jq'       # 对应 jq_runtime_config.STRATEGIES 中的键
QMT_INITIAL_CAPITAL = 10000   # 仅QMT开关启用时使用


def initialize(context):
    # 运行时安装必须是第一条可执行语句。
    runtime = bt.install_joinquant_runtime(
        globals(),
        context=context,
        strategy_id=STRATEGY_ID,
        qmt_initial_capital=QMT_INITIAL_CAPITAL,
    )
    state = runtime.state
    log.info('运行时已安装 | mode={} reason={}'.format(state['mode'], state['reason']))

    set_benchmark('000300.XSHG')
    set_option('use_real_price', True)
    # 本例只记录组合；业务策略可先notify_target_buy_plan，再调用聚宽order系列接口。
    run_daily(record_portfolio_snapshot, '14:50', reference_security='000300.XSHG')


def process_initialize(context):
    """聚宽重启/代码刷新时调用；同签名重复安装幂等返回原状态。"""
    runtime = bt.install_joinquant_runtime(
        globals(),
        context=context,
        strategy_id=STRATEGY_ID,
        qmt_initial_capital=QMT_INITIAL_CAPITAL,
    )
    state = runtime.state
    log.info('运行时已恢复 | mode={} reason={}'.format(state['mode'], state['reason']))


def record_portfolio_snapshot(context):
    """记录聚宽模拟组合；JQ模式不改变原生下单函数。"""
    portfolio = context.portfolio
    log.info('组合快照 | 可用={:.2f} 总资产={:.2f} 持仓市值={:.2f}'.format(
        portfolio.available_cash, portfolio.total_value, portfolio.positions_value))
    for code in sorted(portfolio.positions.keys()):
        position = portfolio.positions[code]
        log.info('持仓 | {} 数量={} 成本={:.4f} 现价={:.4f}'.format(
            code, position.total_amount, position.avg_cost, position.price))
