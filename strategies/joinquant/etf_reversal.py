"""ETF日线超跌反转实验：昨日信号、趋势过滤、固定交易日周期轮换。

研究说明和参考来源见 etf_reversal.md。参数未经收益优化，尚未在聚宽回测。
与 good_etf 独立；运行依赖研究根目录单文件 helper API23。
"""

import math
from typing import Any, Dict, Optional, Tuple, TYPE_CHECKING

import pandas as pd
from jqdata import *
import bullet_trade_jq_remote_helper as bt

if TYPE_CHECKING:
    from joinquant_typing import Context

STRATEGY_ID = 'etf_reversal_daily'
_EXPECTED_RUNTIME_API_VERSION = 23
_EXPECTED_RUNTIME_PROFILE_MODULE = 'jq_runtime_config'
VALIDATE_REMOTE_DURING_BACKTEST = False
QMT_INITIAL_CAPITAL = 10000

# 固定研究池，不按回测收益挑ETF。另用当时已上市名单与历史长度过滤。
ETF_POOL = (
    '510050.XSHG',  # 上证50
    '510300.XSHG',  # 沪深300
    '510500.XSHG',  # 中证500
    '512100.XSHG',  # 中证1000
    '159915.XSHE',  # 创业板
    '159901.XSHE',  # 深证100
    '159928.XSHE',  # 消费
    '512010.XSHG',  # 医药
    '512800.XSHG',  # 银行
    '512880.XSHG',  # 证券
    '512660.XSHG',  # 军工
    '512480.XSHG',  # 半导体
)
REVERSAL_DAYS = 5            # 可比较3/5日跌幅
ENTRY_RETURN = -0.03         # 区间跌幅至少3%
TREND_DAYS = 60              # 昨日收盘价必须高于这条均线
LIQUIDITY_DAYS = 20
MIN_AVG_MONEY = 2e7          # 过去20交易日日均成交额至少2000万元
MIN_LISTING_DAYS = 120       # 自上市起至少120个自然日
MAX_HOLD_NUM = 3
DEPLOY_RATIO = 0.90          # 每个名额30%；不足3只时保留剩余现金
HOLD_DAYS = 5                # 每5个交易日重选；连续入选可以继续持有
REBALANCE_ANCHOR = '2010-01-04'  # 固定周期起点，避免回测起始日改变轮换相位
OPEN_DECISION_TIME = '09:35'
OPEN_ORDER_TIME = '09:35'    # 固定选股时间，改为09:36/09:40/09:50测试0/1/5/15分钟

_runtime: Any = None


def _install_runtime(context: 'Context') -> None:
    global _runtime
    _runtime = bt.install_joinquant_runtime(
        globals(), context=context, strategy_id=STRATEGY_ID,
        qmt_initial_capital=QMT_INITIAL_CAPITAL,
        expected_api_version=_EXPECTED_RUNTIME_API_VERSION,
        profile_module=_EXPECTED_RUNTIME_PROFILE_MODULE,
        validate_remote_during_backtest=VALIDATE_REMOTE_DURING_BACKTEST,
    )


def initialize(context: 'Context') -> None:
    _install_runtime(context)
    _validate_parameters()
    _runtime.configure_platform()
    g.reversal_signal_date = None
    g.reversal_candidates = None
    g.reversal_rebalance_today = False
    _runtime.schedule_daily(
        before_market_open, market_open, handle_risk_management, (),
        after_market_check,
        open_decision_time=OPEN_DECISION_TIME, open_order_time=OPEN_ORDER_TIME,
    )


def process_initialize(context: 'Context') -> None:
    _install_runtime(context)
    _validate_parameters()
    _runtime.log_process_initialize()


def _validate_parameters() -> None:
    for value in (REVERSAL_DAYS, TREND_DAYS, LIQUIDITY_DAYS, MAX_HOLD_NUM, HOLD_DAYS):
        if type(value) is not int or value < 1:
            raise ValueError('窗口、持仓名额和轮换周期必须为正整数')
    if not (-1 < ENTRY_RETURN < 0 and 0 < DEPLOY_RATIO <= 1):
        raise ValueError('ENTRY_RETURN必须在(-1,0)，DEPLOY_RATIO必须在(0,1]')
    if MIN_LISTING_DAYS < 0 or not math.isfinite(MIN_AVG_MONEY) or MIN_AVG_MONEY < 0:
        raise ValueError('上市天数和成交额下限必须非负且有效')


def _is_rebalance_day(context: 'Context') -> bool:
    today = context.current_dt.date()
    days = get_trade_days(start_date=REBALANCE_ANCHOR, end_date=today)
    if len(days) == 0 or pd.Timestamp(days[-1]).date() != today:
        return False
    return (len(days) - 1) % HOLD_DAYS == 0


def before_market_open(context: 'Context') -> None:
    """只缓存决策所需数据；不读取账户资金、实际持仓或成交状态。"""
    g.reversal_signal_date = context.current_dt.date()
    g.reversal_rebalance_today = False
    g.reversal_candidates = None
    try:
        _validate_parameters()
        g.reversal_rebalance_today = _is_rebalance_day(context)
        if not g.reversal_rebalance_today:
            return
        as_of = context.previous_date
        listed = get_all_securities(['etf'], date=as_of)
        window = max(TREND_DAYS, REVERSAL_DAYS + 1, LIQUIDITY_DAYS)
        rows = []
        # 保留原池顺序读取；最终按跌幅、代码排序，代码是并列时的确定性规则。
        for code in ETF_POOL:
            if code not in listed.index:
                continue
            start_date = pd.Timestamp(listed.loc[code, 'start_date']).date()
            if (as_of - start_date).days < MIN_LISTING_DAYS:
                continue
            bars = get_price(
                code, end_date=as_of, count=window, frequency='daily',
                fields=['close', 'money', 'paused'],
                skip_paused=False, fq='pre',
            )
            if bars is None or bars.empty:
                raise ValueError('{}历史行情缺失'.format(code))
            dates = pd.to_datetime(bars.index)
            if dates.max().date() > as_of or dates[-1].date() != as_of:
                raise ValueError('{}历史行情日期不符合昨日截止边界'.format(code))
            if len(bars) < window:
                continue
            bars = bars.iloc[-window:]
            close = [float(value) for value in bars['close']]
            money = [float(value) for value in bars['money'].iloc[-LIQUIDITY_DAYS:]]
            paused = float(bars['paused'].iloc[-1])
            if not all([math.isfinite(value) and value > 0 for value in close]):
                raise ValueError('{}历史价格无效'.format(code))
            if not all([math.isfinite(value) and value >= 0 for value in money]):
                raise ValueError('{}历史成交额无效'.format(code))
            if not math.isfinite(paused):
                raise ValueError('{}历史停牌状态无效'.format(code))
            if paused != 0:
                continue
            avg_money = sum(money) / LIQUIDITY_DAYS
            if avg_money < MIN_AVG_MONEY:
                continue
            trend_ma = sum(close[-TREND_DAYS:]) / TREND_DAYS
            if close[-1] <= trend_ma:
                continue
            recent_return = close[-1] / close[-REVERSAL_DAYS - 1] - 1
            if recent_return > ENTRY_RETURN:
                continue
            rows.append({
                'code': code, 'recent_return': recent_return,
                'close': close[-1], 'trend_ma': trend_ma, 'avg_money': avg_money,
            })
        g.reversal_candidates = pd.DataFrame(
            rows, columns=['code', 'recent_return', 'close', 'trend_ma', 'avg_money']
        ).sort_values(['recent_return', 'code'], kind='mergesort')
        log.info('日线反转 | 数据截止={} | 候选={} | 周期={}交易日'.format(
            as_of, len(rows), HOLD_DAYS,
        ))
    except Exception as exc:
        # 数据失败不能解释成空仓信号，也不能沿用昨天的缓存。
        g.reversal_candidates = None
        log.error('日线反转数据准备失败，跳过本轮：{}'.format(exc))


def market_open(context: 'Context') -> Optional[Tuple[Dict[str, float], Dict[str, float]]]:
    """昨日因子决定排名；当日快照只用于可交易过滤和下单参考价。"""
    if g.reversal_signal_date != context.current_dt.date():
        before_market_open(context)
    if not g.reversal_rebalance_today or g.reversal_candidates is None:
        return None
    try:
        candidates = g.reversal_candidates
        if candidates.empty:
            log.info('本轮无日线超跌信号，提交空仓目标')
            return {}, {}
        current = get_current_data()
        weights = {}  # type: Dict[str, float]
        marks = {}  # type: Dict[str, float]
        for _, row in candidates.iterrows():
            code = str(row['code'])
            quote = current[code]
            price = float(quote.last_price)
            if quote.paused or not math.isfinite(price) or price <= 0:
                continue
            if price >= quote.high_limit or price <= quote.low_limit:
                continue
            weights[code] = DEPLOY_RATIO / MAX_HOLD_NUM
            marks[code] = price
            log.info('入选={} | {}日收益={:.2%} | 昨收={:.3f} MA{}={:.3f}'.format(
                code, REVERSAL_DAYS, row['recent_return'], row['close'],
                TREND_DAYS, row['trend_ma'],
            ))
            if len(weights) == MAX_HOLD_NUM:
                break
        return weights, marks
    except Exception as exc:
        log.error('日线反转选股失败，跳过本轮：{}'.format(exc))
        return None


def handle_risk_management(context: 'Context') -> None:
    """基线版本不注册盘中风控；退出由下一轮目标决定。"""
    pass


def after_market_check(context: 'Context') -> None:
    _runtime.log_account_snapshots(context)
