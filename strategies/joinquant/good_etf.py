# 克隆自聚宽文章：https://www.joinquant.com/post/1399
# 标题：【量化课堂】多因子策略入门
# 作者：JoinQuant量化课堂

# 克隆自聚宽文章：https://www.joinquant.com/post/68704
# 标题：三马105+七星1.5+11年收益306倍回撤12.47
# 作者：rbq2025

# 克隆自聚宽文章基础上的自定义修改版
# 核心修改：消除风控函数中的未来函数风险，保证回测/实盘一致性
# 统一仓库来源：bt_quant@e6462dd（导入时已移除连接凭据）
# 统一策略脚本：同一组选股决策可独立驱动聚宽和QMT账户。

# 导入必要的库
import datetime  # 显式导入，保证复制到聚宽后可直接运行
from typing import Any, Dict, List, TYPE_CHECKING

from jqdata import *
import bullet_trade_jq_remote_helper as bt

if TYPE_CHECKING:
    # 仅供本地IDE使用；聚宽运行时不会导入该本地类型模块。
    from joinquant_typing import Context  # noqa: F401

# ===== 部署契约 =====
STRATEGY_ID = 'good_etf_remote'

VALIDATE_REMOTE_DURING_BACKTEST = True
_EXPECTED_RUNTIME_API_VERSION = 14
_EXPECTED_RUNTIME_PROFILE_MODULE = 'jq_runtime_config'

# ===== 策略参数 =====
MAX_HOLD_NUM = 3           # 最大持仓只数：选折价最深的前 N 只
MIN_MONEY = 5e6            # 流动性下限：前一日成交额 > 500 万
MAX_MONEY = 2e7            # 流动性上限：前一日成交额 < 2000 万
STOP_LOSS_RATIO = 0.95     # 止损线：现价跌破成本价 95%
TAKE_PROFIT_RATIO = 1.10   # 止盈线：现价涨超成本价 110%
DEPLOY_RATIO = 0.95        # 组合部署比例：预留5%现金覆盖整手、费用和价格波动
# 港股类 ETF 过滤关键词（名称包含任一关键词即剔除）
HK_KEYWORDS = ['港股', '恒生', 'H股', '国企', '香港', '恒生科技', '港股通', '恒生互联网']

# ===== QMT执行参数 =====
REMOTE_PRICE_BAND_PCT = 0.002  # 真实调仓价格边界：聚宽参考价上下0.2%；JQ模式不使用该参数
REMOTE_MARKET_RESERVATION_BAND_PCT = 0.015  # 市价执行估值/资金预留边界，不限制QMT真实市价成交
SKIP_SUSPENDED_LIMITUP = True  # 选股时剔除停牌/涨停标的（False 恢复原行为）
QMT_INITIAL_CAPITAL = 10000     # 分配给QMT策略虚拟账户的固定初始资金；不影响聚宽账户资金
RISK_CHECK_TIMES = ('10:30', '13:30', '14:50')  # 每日止盈止损检查时间

_runtime: Any = None


def _install_runtime(context: 'Context') -> Dict[str, object]:
    """安装统一运行门面；模式、校验和远程预检均由helper负责。"""
    global _runtime
    _runtime = bt.install_joinquant_runtime(
        globals(),
        context=context,
        strategy_id=STRATEGY_ID,
        qmt_initial_capital=QMT_INITIAL_CAPITAL,
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
    # 设置聚宽账户的模拟交易成本；QMT账户只使用服务器确认的真实费用证据。
    set_order_cost(
        OrderCost(close_tax=0.000, open_commission=0.00025, close_commission=0.00025, min_commission=5),
        type='fund'
    )
    # 设置滑点（固定滑点0.1%）
    set_slippage(FixedSlippage(0.002))

    # 全局状态初始化，防止盘前预处理尚未运行时访问报 AttributeError
    g.fund_list = None
    log.info(f'策略初始化完成 | 最大持仓={MAX_HOLD_NUM} 流动性=({MIN_MONEY / 1e4:.0f}万,{MAX_MONEY / 1e4:.0f}万) '
             f'止损线={STOP_LOSS_RATIO:.0%} 止盈线={TAKE_PROFIT_RATIO:.0%}')

    # 每日运行函数调度
    # 9:20 预处理选股数据（前一日数据，无未来函数）
    run_daily(before_market_open, '09:20', reference_security='000300.XSHG')
    # 9:30 执行开盘选股+下单
    run_daily(market_open, '09:30', reference_security='000300.XSHG')
    # 按顶部配置注册盘中和尾盘止盈止损检查。
    for risk_time in RISK_CHECK_TIMES:
        run_daily(handle_risk_management, time=risk_time, reference_security='000300.XSHG')
    # 14:55 尾盘快照；统一门面分别读取所有已启用账户。
    run_daily(after_market_check, time='14:55', reference_security='000300.XSHG')
    log.info('任务调度完成 | 09:20 盘前预处理 | 09:30 开盘下单 | '
             '风控: {} | 14:55 尾盘快照'.format('/'.join(RISK_CHECK_TIMES)))


def process_initialize(context: 'Context') -> None:
    """
    聚宽重启/代码刷新时调用，幂等恢复运行模式。
    """
    _install_runtime(context)
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

        # 策略只生成一份目标权重和参考价；helper使用两个账户各自的
        # 总资产、持仓和可用资金独立执行，不在策略层混用账户状态。
        raw_weights = selected_funds['premium'].abs().tolist()
        total_weight = sum(raw_weights) if sum(raw_weights) else 1.0
        target_weights = {
            code: float(weight / total_weight * DEPLOY_RATIO)
            for code, weight in zip(order_fund_codes, raw_weights)
        }
        marks = {
            code: float(selected_funds.loc[code, 'last_price'])
            for code in order_fund_codes
        }
        key = 'open-{}'.format(context.current_dt.strftime('%Y%m%d'))
        _runtime.execute_rebalance(
            context,
            target_weights,
            marks,
            key,
            _rebalance_execution(),
        )
        if selected_funds.empty:
            message = '无折价ETF可选，已提交全部卖出目标，今日不再买入'
            log.warn(message)
            _notify(message)
        else:
            log.info('===== 开盘选股下单完成 =====')

    except Exception as e:
        log.error(f"开盘执行异常：{e}")


def handle_risk_management(context: 'Context') -> None:
    """同一风控规则分别检查JQ和QMT账户自己的持仓与成本。"""
    try:
        key = 'risk-{}'.format(context.current_dt.strftime('%Y%m%d-%H%M'))
        _runtime.execute_risk_management(
            context,
            STOP_LOSS_RATIO,
            TAKE_PROFIT_RATIO,
            key,
            _stop_loss_execution(),
            _take_profit_execution(),
        )

    except Exception as e:
        log.error(f"风控执行异常：{e}")


def after_market_check(context: 'Context') -> None:
    """由运行时统一记录所有已启用账户的组合快照。"""
    _runtime.log_account_snapshots(context)
