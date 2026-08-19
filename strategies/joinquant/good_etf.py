# 克隆自聚宽文章：https://www.joinquant.com/post/1399
# 标题：【量化课堂】多因子策略入门
# 作者：JoinQuant量化课堂

# 克隆自聚宽文章：https://www.joinquant.com/post/68704
# 标题：三马105+七星1.5+11年收益306倍回撤12.47
# 作者：rbq2025

# 克隆自聚宽文章基础上的自定义修改版
# 核心修改：消除风控函数中的未来函数风险，保证回测/实盘一致性
# 统一仓库来源：bt_quant@e6462dd（导入时已移除连接凭据）
# QMT_REMOTE通过StrategyLedger提交组合目标并展示真实账户视图；回测保持聚宽原生。

# 导入必要的库
import datetime  # 显式导入，保证复制到聚宽后可直接运行
from typing import Any, Dict, List, TYPE_CHECKING

from jqdata import *
import bullet_trade_jq_remote_helper as bt

if TYPE_CHECKING:
    # 仅供本地IDE使用；聚宽运行时不会导入该本地类型模块。
    from joinquant_typing import Context  # noqa: F401

# ===== 部署契约 =====
VALIDATE_REMOTE_DURING_BACKTEST = True
STRATEGY_ID = 'good_etf'
_EXPECTED_RUNTIME_API_VERSION = 9
_EXPECTED_RUNTIME_PROFILE_MODULE = 'jq_runtime_config'
ExecutionMode = bt.RuntimeMode

# ===== 策略参数 =====
MAX_HOLD_NUM = 3           # 最大持仓只数：选折价最深的前 N 只
MIN_MONEY = 5e6            # 流动性下限：前一日成交额 > 500 万
MAX_MONEY = 2e7            # 流动性上限：前一日成交额 < 2000 万
STOP_LOSS_RATIO = 0.95     # 止损线：现价跌破成本价 95%
TAKE_PROFIT_RATIO = 1.10   # 止盈线：现价涨超成本价 110%
DEPLOY_RATIO = 0.95        # 组合部署比例：预留5%现金覆盖整手、费用和价格波动
# 港股类 ETF 过滤关键词（名称包含任一关键词即剔除）
HK_KEYWORDS = ['港股', '恒生', 'H股', '国企', '香港', '恒生科技', '港股通', '恒生互联网']

# ===== QMT_REMOTE执行参数 =====
REMOTE_PRICE_BAND_PCT = 0.002  # 真实调仓价格边界：聚宽参考价上下0.2%；JQ模式不使用该参数
REMOTE_MARKET_RESERVATION_BAND_PCT = 0.015  # 市价执行估值/资金预留边界，不限制QMT真实市价成交
SKIP_SUSPENDED_LIMITUP = True  # 选股时剔除停牌/涨停标的（False 恢复原行为）
INITIAL_CAPITAL = 10000         # 聚宽策略分配给真实专用账户的固定初始资金
RISK_CHECK_TIMES = ('10:30', '13:30', '14:50')  # 每日止盈止损检查时间

_runtime: Any = None


def _install_runtime(context: 'Context') -> Dict[str, object]:
    """安装统一运行门面；模式、校验和远程预检均由helper负责。"""
    global _runtime
    _runtime = bt.install_joinquant_runtime(
        globals(),
        context=context,
        strategy_id=STRATEGY_ID,
        initial_capital=INITIAL_CAPITAL,
        expected_api_version=_EXPECTED_RUNTIME_API_VERSION,
        profile_module=_EXPECTED_RUNTIME_PROFILE_MODULE,
        validate_remote_during_backtest=VALIDATE_REMOTE_DURING_BACKTEST,
    )
    return dict(_runtime.state)


def _notify(message: str) -> None:
    # 聚宽侧只记录策略事件；飞书交易卡片由服务器统一发送。
    log.info('[策略通知] {}'.format(message))


def _rebalance_execution() -> Any:
    return bt.ExecutionRequest(
        style=bt.ConditionalLimitExecution(
            int(REMOTE_PRICE_BAND_PCT * 1_000_000),
            bt.ConditionalLimitPriceMode.BOUNDARY,
        ),
        # 调仓先以QMT真实市价卖出，成交回报释放资金后，再执行条件限价买入。
        sell_style=bt.MarketExecution(
            int(REMOTE_MARKET_RESERVATION_BAND_PCT * 1_000_000)
        ),
        follow_up=bt.FollowUpPolicy.UNTIL_FILLED_TODAY,
        repricing=bt.RepricingPolicy.KEEP_ORIGINAL,
    )


def _stop_loss_execution() -> Any:
    return bt.ExecutionRequest(
        style=bt.MarketExecution(
            int(REMOTE_MARKET_RESERVATION_BAND_PCT * 1_000_000)
        ),
        follow_up=bt.FollowUpPolicy.UNTIL_FILLED_TODAY,
        repricing=bt.RepricingPolicy.KEEP_ORIGINAL,
    )


def _take_profit_execution() -> Any:
    # 止盈不承担为新仓筹资的职责，继续等待0.2%价格边界后限价卖出。
    return bt.ExecutionRequest(
        style=bt.ConditionalLimitExecution(
            int(REMOTE_PRICE_BAND_PCT * 1_000_000),
            bt.ConditionalLimitPriceMode.BOUNDARY,
        ),
        follow_up=bt.FollowUpPolicy.UNTIL_FILLED_TODAY,
        repricing=bt.RepricingPolicy.KEEP_ORIGINAL,
    )


def initialize(context: 'Context') -> None:
    # 安全门必须是第一条可执行语句；门前不得访问任何聚宽平台对象。
    _install_runtime(context)

    # 设置日志级别
    log.set_level('system', 'error')
    # 避免使用未来数据（聚宽平台级未来数据防护）
    set_option("avoid_future_data", True)
    # 设定沪深300作为基准
    set_benchmark('000300.XSHG')
    # 开启动态复权模式（真实价格）
    set_option('use_real_price', True)
    # 设置聚宽BACKTEST/JQ的模拟交易成本；QMT_REMOTE只使用服务器确认的真实费用证据。
    set_order_cost(
        OrderCost(close_tax=0.000, open_commission=0.00025, close_commission=0.00025, min_commission=5),
        type='fund'
    )
    # 设置滑点（固定滑点0.1%）
    set_slippage(FixedSlippage(0.002))

    # 全局状态初始化，防止盘前预处理尚未运行时访问报 AttributeError
    g.fund_list = None
    if _runtime.mode is ExecutionMode.QMT_REMOTE:
        _runtime.ensure_ready(INITIAL_CAPITAL, context)

    log.info(f'策略初始化完成 | 模式={_runtime.mode.value} 最大持仓={MAX_HOLD_NUM} 流动性=({MIN_MONEY / 1e4:.0f}万,{MAX_MONEY / 1e4:.0f}万) '
             f'止损线={STOP_LOSS_RATIO:.0%} 止盈线={TAKE_PROFIT_RATIO:.0%}')

    # 每日运行函数调度
    # 9:20 预处理选股数据（前一日数据，无未来函数）
    run_daily(before_market_open, '09:20', reference_security='000300.XSHG')
    # 9:30 执行开盘选股+下单
    run_daily(market_open, '09:30', reference_security='000300.XSHG')
    # 按顶部配置注册盘中和尾盘止盈止损检查。
    for risk_time in RISK_CHECK_TIMES:
        run_daily(handle_risk_management, time=risk_time, reference_security='000300.XSHG')
    # 14:55 尾盘快照；QMT_REMOTE由统一门面读取StrategyLedger真实组合。
    run_daily(after_market_check, time='14:55', reference_security='000300.XSHG')
    log.info('任务调度完成 | 09:20 盘前预处理 | 09:30 开盘下单 | '
             '风控: {} | 14:55 尾盘快照'.format('/'.join(RISK_CHECK_TIMES)))


def process_initialize(context: 'Context') -> None:
    """
    聚宽重启/代码刷新时调用，幂等恢复运行模式。
    """
    _install_runtime(context)
    if _runtime.mode is ExecutionMode.QMT_REMOTE:
        _runtime.ensure_ready(INITIAL_CAPITAL, context)
    log.info(f"process_initialize 重建配置 {datetime.datetime.now()}")


def before_market_open(context: 'Context') -> None:
    """开盘前预处理：获取ETF数据（基于前一交易日数据，无未来函数）"""
    start_time = datetime.datetime.now()
    log.info('===== 盘前预处理开始 =====')
    try:
        # 获取所有ETF基金（基于前一交易日数据）
        all_etf = get_all_securities(['etf'], context.previous_date)
        log.info(f'全市场ETF数量: {len(all_etf)}')

        # 过滤港股类ETF（名称为空时保留，避免 NaN 导致整体预处理失败）
        # 注意：必须用列表推导式 any([...])，不能用生成器 any(...)。
        # 聚宽环境中 any 可能被 numpy 的 np.any 覆盖，np.any(生成器) 恒为 True，
        # 会导致全部标的被误判为港股ETF而过滤掉（np.any(列表) 则行为正常）。
        fund_list: List[str] = []
        hk_samples: List[str] = []
        hk_count = 0
        for code, name in zip(all_etf.index, all_etf['display_name']):
            safe_code: str = code
            if isinstance(name, str) and any([kw in name for kw in HK_KEYWORDS]):
                hk_count += 1
                if len(hk_samples) < 5:
                    hk_samples.append(f'{safe_code}({name})')
                continue
            fund_list.append(safe_code)
        log.info(f'过滤港股ETF {hk_count} 只 | 剩余 {len(fund_list)} 只')
        if hk_samples:
            log.info(f'被过滤ETF样例: {hk_samples}')
        if fund_list:
            log.info(f'保留ETF样例: {fund_list[:5]}')

        if not fund_list:
            log.warn('过滤后无ETF标的')
            g.fund_list = None
            return

        # 获取前一日日线数据（高低价、成交额）
        high_df = history(count=1, unit='1d', field="high", security_list=fund_list).T
        low_df = history(count=1, unit='1d', field="low", security_list=fund_list).T
        volume_df = history(count=1, unit='1d', field="money", security_list=fund_list).T

        # 合并数据
        df = high_df.merge(low_df, left_index=True, right_index=True)
        df = df.merge(volume_df, left_index=True, right_index=True)
        df.columns = ['high_price', 'low_price', 'money']

        # 流动性筛选：成交额 500万 ~ 2000万
        before_count = len(df)
        df = df[(df['money'] < MAX_MONEY) & (df['money'] > MIN_MONEY)]
        log.info(f'流动性筛选({MIN_MONEY / 1e4:.0f}万~{MAX_MONEY / 1e4:.0f}万): {before_count} -> {len(df)} 只')

        if df.empty:
            log.warn('流动性筛选后无ETF标的')
            g.fund_list = None
            return

        # 获取前一日单位净值（核心折价率计算依据）
        nav_df = get_extras(
            'unit_net_value',
            df.index.tolist(),
            end_date=context.previous_date,
            df=True,
            count=1
        ).T
        nav_df.columns = ['unit_net_value']

        # 合并净值数据并存储到全局变量
        g.fund_list = df.merge(nav_df, left_index=True, right_index=True)
        elapsed = (datetime.datetime.now() - start_time).total_seconds()
        log.info(f'盘前预处理完成 | 候选ETF {len(g.fund_list)} 只 | 耗时 {elapsed:.1f}s')

    except Exception as e:
        log.error(f"开盘前预处理异常：{e}")
        g.fund_list = None  # 异常时置空，避免后续报错


def market_open(context: 'Context') -> None:
    """开盘执行：选股并按当前执行模式处理目标权重。"""
    log.info('===== 开盘选股下单开始 =====')
    try:
        if not _runtime.advance_targets(context):
            return
        # 若盘前预处理未执行（聚宽在 09:20~09:30 间重启会错过），现场补跑一次
        if g.fund_list is None:
            log.warn('盘前预处理数据缺失，现场补跑 before_market_open')
            before_market_open(context)
        # 若预处理失败，直接返回
        if g.fund_list is None or g.fund_list.empty:
            log.warn("无符合条件的ETF标的，跳过本轮执行")
            return

        df = g.fund_list.copy()
        current_data = get_current_data()

        # 获取实时最新价（开盘时的真实价格，无未来函数）
        dataframe_codes: List[str] = df.index.tolist()
        df['last_price'] = [current_data[code].last_price for code in dataframe_codes]

        # 剔除停牌/涨停标的（避免选中后委托无法成交，浪费名额）
        if SKIP_SUSPENDED_LIMITUP:
            keep: List[str] = []
            keep_codes: List[str] = df.index.tolist()
            for code in keep_codes:
                cd = current_data[code]
                if cd.paused or df.loc[code, 'last_price'] >= cd.high_limit:
                    continue
                keep.append(code)
            skipped = len(df) - len(keep)
            if skipped:
                log.info(f'剔除停牌/涨停ETF {skipped} 只')
            df = df.loc[keep]

        # 计算折价率（<0为折价，核心选股逻辑）
        df['premium'] = (df['last_price'] / df['unit_net_value'] - 1) * 100

        # 筛选折价ETF，按折价率升序排列（折价最深的排最前）
        before_count = len(df)
        df = df[df['premium'] < 0].sort_values(['premium'], ascending=True)
        log.info(f'折价筛选: {before_count} -> {len(df)} 只折价ETF')

        # 选择折价最深的前 N 支
        selected_funds = df.head(MAX_HOLD_NUM)
        order_fund_codes = selected_funds.index.tolist()
        log.info(f'选中折价ETF {len(order_fund_codes)} 只: {order_fund_codes}')
        for code in order_fund_codes:
            row = selected_funds.loc[code]
            log.info(f'候选明细 | {code} 折价率={row["premium"]:.2f}% '
                     f'最新价={row["last_price"]:.3f} 净值={row["unit_net_value"]:.4f}')
        if order_fund_codes:
            _notify(f'选中折价ETF {len(order_fund_codes)} 只: {order_fund_codes}')

        # 撤销昨日遗留未成交挂单，避免干扰今日目标委托。
        cancelled = _runtime.cancel_orders()
        if cancelled:
            log.info(f'已撤销 {cancelled} 笔遗留挂单')

        # 第一步：对不在选定列表中的持仓提交清仓目标。
        # 注意：迭代持仓列表副本，避免下单过程中持仓变化影响遍历
        portfolio = _runtime.portfolio(context)
        hold_codes = list(portfolio.positions.keys())
        log.info(f'当前持仓 {len(hold_codes)} 只: {hold_codes}')

        raw_weights = selected_funds['premium'].abs().tolist()
        total_weight = sum(raw_weights) if sum(raw_weights) else 1.0
        # 固定计划生成时的组合快照。聚宽会在后续卖单成交后原地更新
        # context.portfolio；若再次读取 total_value，日志会把卖出后的资产
        # 与卖出前计算的目标部署金额混在一起。
        planning_total_value = float(portfolio.total_value)
        investable_value = planning_total_value * DEPLOY_RATIO
        target_values = {
            code: float(investable_value * weight / total_weight)
            for code, weight in zip(order_fund_codes, raw_weights)
        }
        buy_plan_items: List[Dict[str, object]] = []
        for code in order_fund_codes:
            position = portfolio.positions[code] if code in portfolio.positions else None
            current_value = getattr(position, 'value', None) if position is not None else 0.0
            if current_value is None:
                current_value = (
                    getattr(position, 'total_amount', 0)
                    * selected_funds.loc[code, 'last_price']
                )
            item = _runtime.target_buy_plan_item(
                code,
                target_values[code],
                float(current_value),
                float(selected_funds.loc[code, 'last_price']),
            )
            if item is not None:
                buy_plan_items.append(item)
        if _runtime.mode is ExecutionMode.QMT_REMOTE:
            target_weights = {
                code: float(weight / total_weight * DEPLOY_RATIO)
                for code, weight in zip(order_fund_codes, raw_weights)
            }
            if not target_weights:
                target_weights = {code: 0.0 for code in hold_codes}
            if not target_weights:
                log.info('真实组合无持仓且无新目标，本轮无需提交')
                return
            marks = {
                code: float(selected_funds.loc[code, 'last_price'])
                for code in order_fund_codes
            }
            for code, position in portfolio.positions.items():
                marks.setdefault(code, float(position.price))
            key = 'open-{}'.format(context.current_dt.strftime('%Y%m%d'))
            result = _runtime.submit_targets(
                context,
                target_weights,
                marks,
                key,
                _rebalance_execution(),
            )
            log.info('真实组合目标已提交 | intent_id={} state={} weights={}'.format(
                result['intent']['intent_id'], result['intent']['state'], target_weights))
            # 通知是旁路能力，必须排在真实目标提交之后，不能延迟QMT执行。
            _runtime.send_target_buy_plan(
                buy_plan_items, occurred_at=context.current_dt)
            return

        _runtime.send_target_buy_plan(
            buy_plan_items, occurred_at=context.current_dt)

        for hold_code in hold_codes:
            if hold_code not in order_fund_codes:
                pos = portfolio.positions[hold_code]
                log.info(f'调仓卖出 | {hold_code} 数量={pos.total_amount} 成本={pos.avg_cost:.4f}')
                order_result = _runtime.order_target(hold_code, 0)
                log.info('清仓目标已提交 | {} order_id={}'.format(
                    hold_code, getattr(order_result, 'order_id', None)))

        # 第二步：按折价率绝对值权重分配“组合目标市值”。
        # 目标金额必须基于组合总资产，而不是可用现金；否则已有持仓会被重复缩小，
        # 且卖单尚未成交时可用现金也不能代表本轮可部署资金。
        if not selected_funds.empty:
            # 计算权重（折价率绝对值占比）
            log.info(f'计划时组合总资产={planning_total_value:.2f} '
                     f'目标部署={investable_value:.2f} '
                     f'计划现金缓冲={planning_total_value - investable_value:.2f}')

            # 按权重处理目标市值；成交归属聚宽撮合或StrategyLedger。
            for code, weight in zip(order_fund_codes, raw_weights):
                normalized_weight = weight / total_weight
                target_value = target_values[code]
                position = portfolio.positions[code] if code in portfolio.positions else None
                current_value = getattr(position, 'value', None) if position is not None else 0.0
                if current_value is None:
                    current_value = getattr(position, 'total_amount', 0) * selected_funds.loc[code, 'last_price']
                target_delta = target_value - current_value
                if abs(target_delta) < 0.01:
                    action = '不变'
                elif target_delta > 0:
                    action = '增持'
                else:
                    action = '减持'
                log.info(f'调仓{action} | {code} 权重={normalized_weight:.1%} '
                         f'当前市值={current_value:.2f} 目标市值={target_value:.2f}')
                # JQ模式完全交给聚宽原生目标市值撮合，不叠加实盘价格保护。
                order_result = _runtime.order_target_value(code, target_value)
                log.info('目标市值已提交 | {} order_id={}'.format(
                    code, getattr(order_result, 'order_id', None)))
            log.info('===== 开盘选股下单完成 =====')
        else:
            message = '无折价ETF可选，已提交全部卖出目标，今日不再买入'
            log.warn(message)
            _notify(message)

    except Exception as e:
        log.error(f"开盘执行异常：{e}")


def handle_risk_management(context: 'Context') -> None:
    """止盈止损；QMT_REMOTE一次提交完整组合目标，避免同轮多个intent。"""
    try:
        remote_intent_idle = _runtime.advance_targets(context)
        portfolio = _runtime.portfolio(context)
        hold_codes = list(portfolio.positions.keys())
        if not hold_codes:
            log.info('风控检查 | 当前无持仓')
            return
        log.info(f'风控检查开始 | 持仓 {len(hold_codes)} 只 | {context.current_dt}')
        stop_loss_exits: List[str] = []
        take_profit_exits: List[str] = []
        # 遍历所有持仓ETF（迭代列表副本）
        for hold_code in hold_codes:
            # 关键修复：用聚宽官方无未来函数的实时价格
            position = portfolio.positions[hold_code]
            current_price = position.price  # 实时最新价（回测/实盘一致）
            cost_basis = position.avg_cost  # 持仓成本价
            pnl = (current_price / cost_basis - 1) if cost_basis else 0.0
            log.info(f'持仓检查 | {hold_code} 成本={cost_basis:.4f} 现价={current_price:.4f} 盈亏={pnl:.2%}')

            # 止损逻辑：跌破成本价95%（5%止损）
            if current_price < cost_basis * STOP_LOSS_RATIO:
                msg = (f"止损触发 | 标的：{hold_code} | 成本价：{cost_basis:.4f} | "
                       f"当前价：{current_price:.4f} | 时间：{context.current_dt}")
                log.info(msg)
                _notify(msg)
                if _runtime.mode is ExecutionMode.QMT_REMOTE:
                    stop_loss_exits.append(hold_code)
                else:
                    order_result = _runtime.order_target(hold_code, 0)
                    log.info('止损清仓目标已提交 | {} order_id={}'.format(
                        hold_code, getattr(order_result, 'order_id', None)))

            # 止盈逻辑：涨超成本价110%（10%止盈）
            elif current_price > cost_basis * TAKE_PROFIT_RATIO:
                msg = (f"止盈触发 | 标的：{hold_code} | 成本价：{cost_basis:.4f} | "
                       f"当前价：{current_price:.4f} | 时间：{context.current_dt}")
                log.info(msg)
                _notify(msg)
                if _runtime.mode is ExecutionMode.QMT_REMOTE:
                    take_profit_exits.append(hold_code)
                else:
                    order_result = _runtime.order_target(hold_code, 0)
                    log.info('止盈清仓目标已提交 | {} order_id={}'.format(
                        hold_code, getattr(order_result, 'order_id', None)))

        exits = stop_loss_exits + take_profit_exits
        if exits and _runtime.mode is ExecutionMode.QMT_REMOTE:
            if not remote_intent_idle and not _runtime.cancel_targets():
                log.warn('风控目标等待旧调仓订单撤销确认 | exits={}'.format(exits))
                return
            total = portfolio.total_value or 1.0
            target_weights = {
                code: (0.0 if code in exits else position.value / total)
                for code, position in portfolio.positions.items()
            }
            marks = {
                code: float(position.price)
                for code, position in portfolio.positions.items()
            }
            key = 'risk-{}'.format(context.current_dt.strftime('%Y%m%d-%H%M'))
            execution = (
                _stop_loss_execution()
                if stop_loss_exits
                else _take_profit_execution()
            )
            result = _runtime.submit_targets(
                context, target_weights, marks, key, execution)
            log.info('真实风控目标已提交 | intent_id={} exits={}'.format(
                result['intent']['intent_id'], exits))

    except Exception as e:
        log.error(f"风控执行异常：{e}")


def after_market_check(context: 'Context') -> None:
    """记录聚宽组合或QMT_REMOTE真实StrategyLedger组合。"""
    portfolio = _runtime.portfolio(context)
    positions = portfolio.positions
    log.info('===== 尾盘组合快照 =====')
    log.info('组合资金 | 可用={:.2f} 总资产={:.2f} 持仓市值={:.2f}'.format(
        portfolio.available_cash,
        portfolio.total_value,
        portfolio.positions_value))
    for code in sorted(positions.keys()):
        position = positions[code]
        log.info('组合持仓 | {} 数量={} 可卖={} 成本={:.4f} 现价={:.4f}'.format(
            code,
            position.total_amount,
            getattr(position, 'closeable_amount', 0),
            position.avg_cost,
            position.price))
    if _runtime.mode is ExecutionMode.QMT_REMOTE:
        log.info('真实指标 | NAV={:.6f} 收益={:.2%} 费用={:.2f}'.format(
            portfolio.nav, portfolio.returns, portfolio.fees))
