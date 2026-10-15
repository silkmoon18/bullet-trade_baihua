# 克隆自聚宽文章：https://www.joinquant.com/post/1399
# 标题：【量化课堂】多因子策略入门
# 作者：JoinQuant量化课堂

# 克隆自聚宽文章：https://www.joinquant.com/post/68704
# 标题：三马105+七星1.5+11年收益306倍回撤12.47
# 作者：rbq2025

# 克隆自聚宽文章基础上的自定义修改版
# 核心修改：消除风控函数中的未来函数风险，保证回测/实盘一致性
# 统一仓库来源：bt_quant@e6462dd（导入时已移除连接凭据）
# GoodETF v2：09:26按开盘价锁定选股并预发QMT目标；服务器09:30自主执行。
# JQ由helper逐笔确认必要卖单，买单仅提交一次；QMT由服务器推进组合目标。
# 新账本good_etf_v2，QMT初始资金5000；不继承、卖出或改写旧策略的归属持仓。

# 导入必要的库
import math
from typing import Any, Dict, List, Optional, Tuple, TYPE_CHECKING

from jqdata import *
import bullet_trade_jq_remote_helper as bt

try:
    # 聚宽基金数据库；本地兼容层没有该对象时自动使用轻量降级逻辑。
    from jqdata import finance, query
except ImportError:  # pragma: no cover - 仅本地IDE/单测环境
    finance = None
    query = None

if TYPE_CHECKING:
    # 仅供本地IDE使用；聚宽运行时不会导入该本地类型模块。
    from joinquant_typing import Context  # noqa: F401

# ===== 部署契约 =====
STRATEGY_ID = 'good_etf_v2'  # 必须与私有config及服务器白名单同名
VALIDATE_REMOTE_DURING_BACKTEST = False  # 回测只使用JQ，不创建QMT账本
_EXPECTED_RUNTIME_API_VERSION = 26
_EXPECTED_RUNTIME_PROFILE_MODULE = 'jq_runtime_config'

# ===== 策略参数 =====
MAX_HOLD_NUM = 3           # 最大持仓只数：选折价最深的前 N 只
MIN_MONEY = 5e6            # 流动性下限：前一日成交额 > 500 万
MAX_MONEY = 2e7            # 流动性上限：前一日成交额 < 2000 万
STOP_LOSS_RATIO = 0.95     # 止损线：现价跌破成本价 95%
TAKE_PROFIT_RATIO = 1.10   # 止盈线：现价涨超成本价 110%
DEPLOY_RATIO = 0.95        # 组合部署比例：预留5%现金覆盖整手、费用和价格波动
BUY_PRICE_INCREASE = 0.002 # 仅回测：原模型买入成交价额外上涨比例，0.001=0.1%；0保持原行为
QMT_INITIAL_CAPITAL = 5000 # 新QMT策略虚拟账户初始资金，不修改聚宽账户资金
SKIP_SUSPENDED_LIMITUP = True  # 选股时剔除停牌/涨停标的（False 恢复原行为）
OPEN_DECISION_TIME = '09:26'  # 冻结决策并通知QMT服务器；尚不发单，使用分钟回测
OPEN_ORDER_TIME = '09:30'     # QMT服务器独立启动；JQ回调执行自身先卖后买
RISK_CHECK_TIMES = ('10:30', '13:30', '14:50')  # 每日止盈止损检查时间

_runtime: Any = None


def _install_runtime(context: 'Context') -> None:
    """安装统一门面；两个账户按私有配置分别执行同一组选股决策。"""
    global _runtime
    _runtime = bt.install_joinquant_runtime(
        globals(),
        context=context,
        strategy_id=STRATEGY_ID,
        qmt_initial_capital=QMT_INITIAL_CAPITAL,
        profile_module=_EXPECTED_RUNTIME_PROFILE_MODULE,
        expected_api_version=_EXPECTED_RUNTIME_API_VERSION,
        validate_remote_during_backtest=VALIDATE_REMOTE_DURING_BACKTEST,
    )


def _load_tracked_index_names(codes: List[str], as_of: Any) -> Dict[str, str]:
    """批量读取ETF跟踪指数名称；失败时返回空映射，不阻塞开盘。"""
    if finance is None or query is None or not codes:
        return {}
    try:
        table = finance.FUND_INVEST_TARGET
        statement = query(
            table.code,
            table.traced_index_name,
            table.pub_date,
            table.start_date,
            table.end_date,
        ).filter(
            table.code.in_(codes),
            table.pub_date <= as_of,
            table.start_date <= as_of,
            (table.end_date == None) | (table.end_date > as_of),  # noqa: E711
        )
        rows = finance.run_query(statement)
        names = {}  # type: Dict[str, str]
        for _, row in rows.iterrows():
            code = str(row['code'])
            index_name = row.get('traced_index_name')
            if not isinstance(index_name, str) or not index_name:
                continue
            previous = names.get(code, '')
            names[code] = '{} {}'.format(previous, index_name).strip()
        return names
    except Exception as exc:
        _runtime.log_strategy_failure('跟踪指数数据', exc)
        return {}


def initialize(context: 'Context') -> None:
    # 安全门必须是第一条可执行语句；门前不得访问任何聚宽平台对象。
    _install_runtime(context)

    _runtime.configure_platform()

    # 全局状态初始化，防止盘前预处理尚未运行时访问报 AttributeError
    g.fund_list = None

    _declare_schedule()


def _declare_schedule() -> None:
    """只声明当前决策日程；替换旧调度由helper处理。"""
    _runtime.schedule_daily(
        before_market_open,
        execute_opening_plan,
        handle_risk_management,
        RISK_CHECK_TIMES,
        after_market_check,
        opening_decision=market_open,
        opening_decision_time=OPEN_DECISION_TIME,
        market_open_time=OPEN_ORDER_TIME,
        sell_then_buy=True,
        replace_existing=True,
    )


def process_initialize(context: 'Context') -> None:
    """
    聚宽重启/代码刷新时调用，幂等恢复运行模式。
    """
    _install_runtime(context)
    _declare_schedule()
    _runtime.log_process_initialize()


def before_market_open(context: 'Context') -> None:
    _runtime.run_decision(context, _prepare_candidates)


def _prepare_candidates(context: 'Context') -> None:
    """开盘前预处理：获取ETF数据（基于前一交易日数据，无未来函数）"""
    try:
        # 获取所有ETF基金（基于前一交易日数据）
        all_etf = get_all_securities(['etf'], context.previous_date)

        # 过滤港股类ETF：场内简称 + 跟踪指数 + 显式代码兜底。
        # 注意：必须用列表推导式 any([...])，不能用生成器 any(...)。
        # 聚宽环境中 any 可能被 numpy 的 np.any 覆盖，np.any(生成器) 恒为 True，
        # 会导致全部标的被误判为港股ETF而过滤掉（np.any(列表) 则行为正常）。
        fund_list: List[str] = []
        tracked_index_names = _load_tracked_index_names(
            [str(code) for code in all_etf.index], context.previous_date
        )
        for code, name in zip(all_etf.index, all_etf['display_name']):
            safe_code: str = str(code)
            if bt.is_hong_kong_etf(
                safe_code, name, tracked_index_names.get(safe_code, '')
            ):
                continue
            fund_list.append(safe_code)

        if not fund_list:
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
        df = df[(df['money'] < MAX_MONEY) & (df['money'] > MIN_MONEY)]

        if df.empty:
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
        _runtime.prepare_rebalance_candidates(context, g.fund_list.index.tolist())

    except Exception as e:
        _runtime.log_strategy_failure('盘前数据', e)
        g.fund_list = None  # 异常时置空，避免后续报错


def market_open(context: 'Context') -> None:
    """09:26只冻结决策并委托helper；不读取账户或推进订单。"""
    _runtime.prepare_rebalance(context, select_opening_etfs, qmt_execute_time=OPEN_ORDER_TIME)


def execute_opening_plan(context: 'Context') -> None:
    """开盘执行冻结计划；成交确认由helper负责，不重选、不补买。"""
    _runtime.execute_prepared_rebalance(
        context, jq_wait_for_sells=True,
        backtest_buy_price_increase=BUY_PRICE_INCREASE,
        qmt_server_rotation=True,
    )


def select_opening_etfs(
    context: 'Context',
) -> Optional[Tuple[Dict[str, float], Dict[str, float]]]:
    """折价与权重用当日开盘价计算；卖出/买入接口由helper负责。"""
    try:
        # 若盘前预处理未执行（聚宽在 09:20~09:30 间重启会错过），现场补跑一次
        if g.fund_list is None:
            before_market_open(context)
        # 若预处理失败，直接返回
        if g.fund_list is None or g.fund_list.empty:
            return None

        df = g.fund_list.copy()
        current_data = get_current_data()

        # 不用昨收/最新价代替缺失的开盘价；涨停过滤沿用原决策边界。
        opening_prices: Dict[str, float] = {}
        for code in df.index.tolist():
            try:
                price = float(current_data[code].day_open)
                high_limit = float(current_data[code].high_limit)
            except (AttributeError, KeyError, TypeError, ValueError):
                continue
            if not (math.isfinite(price) and price > 0
                    and math.isfinite(high_limit) and high_limit > 0):
                continue
            opening_prices[code] = price
        if not opening_prices:
            return None
        df = df.loc[list(opening_prices)]
        df['open_price'] = [opening_prices[code] for code in df.index]

        # 剔除停牌/涨停标的（避免选中后委托无法成交，浪费名额）
        if SKIP_SUSPENDED_LIMITUP:
            keep: List[str] = []
            keep_codes: List[str] = df.index.tolist()
            for code in keep_codes:
                cd = current_data[code]
                if cd.paused or df.loc[code, 'open_price'] >= cd.high_limit:
                    continue
                keep.append(code)
            df = df.loc[keep]

        # 计算折价率（<0为折价，核心选股逻辑）
        df['premium'] = (df['open_price'] / df['unit_net_value'] - 1) * 100

        # 筛选折价ETF，按折价率升序排列（折价最深的排最前）
        df = df[df['premium'] < 0].sort_values(['premium'], ascending=True)

        # 选择折价最深的前 N 支
        selected_funds = df.head(MAX_HOLD_NUM)
        order_fund_codes = selected_funds.index.tolist()

        # 决策只包含目标权重和开盘参考价；不处理账户/订单。
        raw_weights = selected_funds['premium'].abs().tolist()
        total_weight = sum(raw_weights) if sum(raw_weights) else 1.0
        target_weights = {
            code: float(weight / total_weight * DEPLOY_RATIO)
            for code, weight in zip(order_fund_codes, raw_weights)
        }
        marks = {
            code: float(selected_funds.loc[code, 'open_price'])
            for code in order_fund_codes
        }
        return target_weights, marks

    except Exception as e:
        _runtime.log_strategy_failure('选股', e)
        return None


def handle_risk_management(context: 'Context') -> None:
    """沿用GoodETF的止盈止损决策，账户检查与执行交给helper。"""
    try:
        key = 'risk-{}'.format(context.current_dt.strftime('%Y%m%d-%H%M'))
        _runtime.execute_risk_management(
            context,
            STOP_LOSS_RATIO,
            TAKE_PROFIT_RATIO,
            key,
        )

    except Exception as e:
        _runtime.log_strategy_failure('风控', e)


def after_market_check(context: 'Context') -> None:
    """由helper分别记录已启用账户的组合快照。"""
    _runtime.log_account_snapshots(context)
