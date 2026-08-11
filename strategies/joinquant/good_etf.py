# 克隆自聚宽文章：https://www.joinquant.com/post/1399
# 标题：【量化课堂】多因子策略入门
# 作者：JoinQuant量化课堂

# 克隆自聚宽文章：https://www.joinquant.com/post/68704
# 标题：三马105+七星1.5+11年收益306倍回撤12.47
# 作者：rbq2025

# 克隆自聚宽文章基础上的自定义修改版
# 核心修改：消除风控函数中的未来函数风险，保证回测/实盘一致性
# 统一仓库来源：bt_quant@e6462dd（导入时已移除连接凭据）
# LIVE通过StrategyLedger提交组合目标并展示真实账户视图；BACKTEST逻辑保持聚宽原生。

# 导入必要的库
import datetime  # 显式导入，保证复制到聚宽后可直接运行
from types import ModuleType
from typing import Any, Dict, List, Optional, TYPE_CHECKING, cast

from jqdata import *

if TYPE_CHECKING:
    # 仅供本地IDE使用；聚宽运行时不会导入该本地类型模块。
    from joinquant_typing import Context  # noqa: F401

bt: Optional[ModuleType]
try:
    import bullet_trade_jq_remote_helper as bt
except ModuleNotFoundError as exc:
    if exc.name != 'bullet_trade_jq_remote_helper':
        # helper本体之外的导入缺失不能按“未上传helper”兜底
        raise
    bt = None

# ===== 部署契约 =====
# 安全默认值为BACKTEST。SHADOW/LIVE需要上传独立的helper和私有jq_runtime_config.py。
PROFILE = 'good_etf-prod'
MODE = 'BACKTEST'
STRATEGY_ID = 'good_etf'
_EXPECTED_RUNTIME_API_VERSION = 2
_EXPECTED_PROFILE_SCHEMA_VERSION = 1
_EXPECTED_RUNTIME_HELPER_MARKER = 'bullet-trade-joinquant-runtime-helper-v2'
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

# ===== 执行参数 =====
BUY_PRICE_FLOAT_PCT = 0.002   # 限价买入浮动比例：限价 = 最新价 × (1 + 0.2%)，上浮提高成交率
SKIP_SUSPENDED_LIMITUP = True  # 选股时剔除停牌/涨停标的（False 恢复原行为）
INITIAL_CAPITAL = 10000         # 聚宽策略分配给真实专用账户的固定初始资金


def _run_type(context: 'Context') -> str:
    run_params = getattr(context, 'run_params', None)
    return str(getattr(run_params, 'type', '') or '').strip().lower()


# 运行时模式在安装时写入该模块级变量；交易入口只读取它，不读取平台侧展示副本。
_active_mode: Optional[str] = None


def _install_runtime(context: 'Context') -> Dict[str, object]:
    """安装运行模式；此异常不得被策略业务层吞掉。"""
    if type(MODE) is not str:
        raise RuntimeError('good_etf MODE必须是普通字符串')
    mode = str.upper(str.strip(MODE))
    if mode not in ('BACKTEST', 'SHADOW', 'LIVE'):
        raise RuntimeError('good_etf MODE必须是BACKTEST、SHADOW或LIVE')
    if type(PROFILE) is not str or not PROFILE:
        raise RuntimeError('good_etf PROFILE必须是非空普通字符串')
    if type(STRATEGY_ID) is not str or not STRATEGY_ID:
        raise RuntimeError('good_etf STRATEGY_ID必须是非空普通字符串')
    if (
        type(_EXPECTED_PROFILE_SCHEMA_VERSION) is not int
        or _EXPECTED_PROFILE_SCHEMA_VERSION != 1
    ):
        raise RuntimeError('good_etf profile schema版本无效')

    global _active_mode
    # helper未上传时只允许聚宽原生回测兜底；此路径不导入profile、不访问网络。
    if bt is None:
        if mode != 'BACKTEST':
            raise RuntimeError(
                '当前模式需要bullet_trade_jq_remote_helper.py，请先上传到聚宽研究根目录')
        run_type = _run_type(context)
        if run_type not in ('simple_backtest', 'full_backtest'):
            raise RuntimeError(
                'good_etf运行模式不匹配: MODE=BACKTEST 仅允许聚宽回测，当前run_type={}'.format(
                    run_type or '<empty>'))
        state: Dict[str, object] = {
            'api_version': _EXPECTED_RUNTIME_API_VERSION,
            'profile_schema_version': _EXPECTED_PROFILE_SCHEMA_VERSION,
            'profile': PROFILE,
            'mode': mode,
            'run_type': run_type,
            'strategy_id': STRATEGY_ID,
            'enabled': False,
            'orders_enabled': True,
            'production_ready': False,
            'reason': 'backtest',
        }
        _active_mode = mode
        g.bt_runtime = state
        return state

    if getattr(bt, 'STRATEGY_RUNTIME_HELPER_MARKER', None) != _EXPECTED_RUNTIME_HELPER_MARKER:
        raise RuntimeError('聚宽helper运行时marker不匹配，必须重新上传helper')
    if getattr(bt, 'STRATEGY_RUNTIME_API_VERSION', None) != _EXPECTED_RUNTIME_API_VERSION:
        raise RuntimeError('聚宽helper运行时API版本不匹配，必须重新上传helper')
    install_entry = getattr(bt, 'install_strategy_runtime', None)
    if not callable(install_entry):
        raise RuntimeError('聚宽helper运行时入口无效，必须重新上传helper')
    remote_state = install_entry(
        globals(),
        context=context,
        profile=PROFILE,
        mode=mode,
        strategy_id=STRATEGY_ID,
        expected_api_version=_EXPECTED_RUNTIME_API_VERSION,
    )
    if not isinstance(remote_state, dict):
        raise RuntimeError('聚宽helper返回了无效的运行时状态，拒绝继续运行')
    checked_state = cast(Dict[str, object], remote_state)
    if (
        checked_state.get('mode') != mode
        or checked_state.get('strategy_id') != STRATEGY_ID
        or checked_state.get('api_version') != _EXPECTED_RUNTIME_API_VERSION
    ):
        raise RuntimeError('聚宽helper返回了无效的运行时状态，拒绝继续运行')
    _active_mode = mode
    g.bt_runtime = checked_state
    return checked_state


def _runtime_mode() -> str:
    if _active_mode is None:
        raise RuntimeError('good_etf运行时模式尚未安装，拒绝执行交易动作')
    return _active_mode


def _notify(message: str) -> None:
    # S01不在聚宽策略中保存Webhook；后续由服务器状态事件统一通知。
    log.info('[策略通知] {}'.format(message))


def _record_real_portfolio(portfolio: Any) -> None:
    record(  # type: ignore[name-defined]  # 聚宽运行时由jqdata注入
        real_cash=portfolio.available_cash,
        real_total=portfolio.total_value,
        real_positions=portfolio.positions_value,
        real_nav=portfolio.nav,
        real_return=portfolio.returns,
        real_fees=portfolio.fees,
    )


def _portfolio(context: 'Context') -> Any:
    if _runtime_mode() != 'LIVE':
        return context.portfolio
    if bt is None:
        raise RuntimeError('LIVE缺少聚宽helper')
    portfolio = bt.get_portfolio(as_of=context.current_dt)
    if not portfolio.performance_ready:
        raise RuntimeError('真实组合发生过运行中增减资，简单NAV指标不可用')
    g.bt_portfolio = portfolio
    _record_real_portfolio(portfolio)
    return portfolio


def _ensure_live_ready(context: 'Context') -> None:
    if bt is None:
        raise RuntimeError('LIVE缺少聚宽helper')
    ensured = bt.ensure_account(INITIAL_CAPITAL)
    reconciliation = ensured.get('reconciliation', {})
    if reconciliation.get('state') != 'READY':
        raise RuntimeError('真实账户对账未就绪: {}'.format(
            reconciliation.get('details', {}).get('blockers', [])))
    _portfolio(context)
    _restore_live_intent()
    g.bt_runtime['production_ready'] = True


def _submit_live_targets(
    context: 'Context',
    weights: Dict[str, float],
    marks: Dict[str, float],
    key: str,
) -> Dict[str, Any]:
    if bt is None:
        raise RuntimeError('LIVE缺少聚宽helper')
    existing = bt.get_intent(idempotency_key=key)
    if existing:
        weights = cast(Dict[str, float], existing.get('weights', weights))
    result = bt.submit_targets(weights, key, marks=marks, as_of=context.current_dt)
    g.bt_target_key = key
    g.bt_target_weights = dict(weights)
    g.bt_target_marks = dict(marks)
    g.bt_intent_id = result['intent']['intent_id']
    portfolio = bt.PortfolioView(result['snapshot'])
    g.bt_portfolio = portfolio
    _record_real_portfolio(portfolio)
    return cast(Dict[str, Any], result)


def _restore_live_intent() -> None:
    if _runtime_mode() != 'LIVE' or bt is None:
        return
    intent = bt.get_intent()
    if not intent:
        g.bt_intent_id = None
        return
    g.bt_intent_id = intent['intent_id']
    g.bt_target_key = intent['idempotency_key']
    g.bt_target_weights = dict(intent.get('weights', {}))
    g.bt_target_marks = {}
    log.info('恢复未完成真实组合意图 | intent_id={} state={}'.format(
        g.bt_intent_id, intent['state']))


def _advance_live_intent(context: 'Context') -> bool:
    """推进未完成的卖后买意图；未完成时本轮不创建新意图。"""
    if _runtime_mode() != 'LIVE' or not getattr(g, 'bt_intent_id', None):
        return True
    if bt is None:
        raise RuntimeError('LIVE缺少聚宽helper')
    intent = bt.get_intent(g.bt_intent_id)
    if intent.get('state') in ('COMPLETED', 'CANCELED', 'FAILED'):
        g.bt_intent_id = None
        return True
    result = _submit_live_targets(
        context,
        g.bt_target_weights,
        g.bt_target_marks,
        g.bt_target_key,
    )
    state = result['intent']['state']
    if state == 'COMPLETED':
        g.bt_intent_id = None
        return True
    log.info('真实组合意图仍在执行，本轮跳过新目标 | intent_id={} state={}'.format(
        g.bt_intent_id, state))
    return False


def _cancel_open_orders_for_runtime() -> int:
    if _runtime_mode() in ('SHADOW', 'LIVE'):
        log.info('{}计划 | 订单生命周期由StrategyLedger管理，跳过聚宽撤单'.format(
            _runtime_mode()))
        return 0
    open_orders = get_open_orders() or {}
    cancelled = 0
    for order_obj in list(open_orders.values()):
        cancel_order(order_obj)
        cancelled += 1
    return cancelled


def _submit_target_amount(
    security: str,
    target_amount: int,
) -> Optional[object]:
    if _runtime_mode() == 'SHADOW':
        log.info('SHADOW目标数量 | {} -> {}'.format(security, target_amount))
        return None
    return order_target(security, target_amount)


def _submit_target_value(
    security: str,
    target_value: float,
    last_price: Optional[float] = None,
    current_value: float = 0.0,
) -> Optional[object]:
    if _runtime_mode() == 'SHADOW':
        log.info('SHADOW目标市值 | {} -> {:.2f}'.format(security, target_value))
        return None
    style = None
    is_increase = target_value > current_value
    if is_increase and last_price is not None and last_price > 0 and BUY_PRICE_FLOAT_PCT > 0:
        style = LimitOrderStyle(last_price * (1 + BUY_PRICE_FLOAT_PCT))
    return order_target_value(security, target_value, style=style)


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
    # 设置交易成本（针对基金/ETF）
    set_order_cost(
        OrderCost(close_tax=0.000, open_commission=0.00025, close_commission=0.00025, min_commission=5),
        type='fund'
    )
    # 设置滑点（固定滑点0.1%）
    set_slippage(FixedSlippage(0.002))

    # 全局状态初始化，防止盘前预处理尚未运行时访问报 AttributeError
    g.fund_list = None
    g.bt_intent_id = None
    if _runtime_mode() == 'LIVE':
        _ensure_live_ready(context)

    log.info(f'策略初始化完成 | 最大持仓={MAX_HOLD_NUM} 流动性=({MIN_MONEY / 1e4:.0f}万,{MAX_MONEY / 1e4:.0f}万) '
             f'止损线={STOP_LOSS_RATIO:.0%} 止盈线={TAKE_PROFIT_RATIO:.0%}')

    # 每日运行函数调度
    # 9:20 预处理选股数据（前一日数据，无未来函数）
    run_daily(before_market_open, '09:20', reference_security='000300.XSHG')
    # 9:30 执行开盘选股+下单
    run_daily(market_open, '09:30', reference_security='000300.XSHG')
    # 10:30 / 13:30 盘中风控检查（提高止盈止损响应速度）
    run_daily(handle_risk_management, time='10:30', reference_security='000300.XSHG')
    run_daily(handle_risk_management, time='13:30', reference_security='000300.XSHG')
    # 14:50 尾盘风控检查
    run_daily(handle_risk_management, time='14:50', reference_security='000300.XSHG')
    # 14:55 尾盘快照（S01仅记录聚宽组合；真实对账由后续StrategyLedger切片实现）
    run_daily(after_market_check, time='14:55', reference_security='000300.XSHG')
    log.info('任务调度完成 | 09:20 盘前预处理 | 09:30 开盘下单 | 风控: 10:30/13:30/14:50 | 14:55 尾盘快照')


def process_initialize(context: 'Context') -> None:
    """
    聚宽重启/代码刷新时调用，幂等恢复运行模式。
    """
    _install_runtime(context)
    if _runtime_mode() == 'LIVE':
        _ensure_live_ready(context)
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
    """开盘执行：选股+按折价率权重下单"""
    log.info('===== 开盘选股下单开始 =====')
    try:
        if not _advance_live_intent(context):
            return
        # 若盘前预处理未执行（聚宽在 09:20~09:30 间重启会错过），现场补跑一次
        if g.fund_list is None:
            log.warn('盘前预处理数据缺失，现场补跑 before_market_open')
            before_market_open(context)
        # 若预处理失败，直接返回
        if g.fund_list is None or g.fund_list.empty:
            log.warn("无符合条件的ETF标的，跳过下单")
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
        cancelled = _cancel_open_orders_for_runtime()
        if cancelled:
            log.info(f'已撤销 {cancelled} 笔遗留挂单')

        # 第一步：对不在选定列表中的持仓提交清仓目标。
        # 注意：迭代持仓列表副本，避免下单过程中持仓变化影响遍历
        portfolio = _portfolio(context)
        hold_codes = list(portfolio.positions.keys())
        log.info(f'当前持仓 {len(hold_codes)} 只: {hold_codes}')

        if _runtime_mode() == 'LIVE':
            raw_weights = selected_funds['premium'].abs().tolist()
            total_weight = sum(raw_weights) if sum(raw_weights) else 1.0
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
            result = _submit_live_targets(context, target_weights, marks, key)
            log.info('真实组合目标已提交 | intent_id={} state={} weights={}'.format(
                result['intent']['intent_id'], result['intent']['state'], target_weights))
            return

        for hold_code in hold_codes:
            if hold_code not in order_fund_codes:
                pos = portfolio.positions[hold_code]
                log.info(f'调仓卖出 | {hold_code} 数量={pos.total_amount} 成本={pos.avg_cost:.4f}')
                order_result = _submit_target_amount(hold_code, 0)
                log.info('清仓目标已提交 | {} order_id={}'.format(
                    hold_code, getattr(order_result, 'order_id', None)))

        # 第二步：按折价率绝对值权重分配“组合目标市值”。
        # 目标金额必须基于组合总资产，而不是可用现金；否则已有持仓会被重复缩小，
        # 且卖单尚未成交时可用现金也不能代表本轮可部署资金。
        if not selected_funds.empty:
            # 计算权重（折价率绝对值占比）
            weights = selected_funds['premium'].abs().tolist()
            total_weight = sum(weights) if sum(weights) != 0 else 1e-9  # 防除零

            total_value = portfolio.total_value
            investable_value = total_value * DEPLOY_RATIO
            log.info(f'组合总资产={total_value:.2f} 目标部署={investable_value:.2f} '
                     f'现金缓冲={total_value - investable_value:.2f}')

            # 按权重提交目标市值；实际成交与资金入账由后续StrategyLedger切片接管。
            for code, weight in zip(order_fund_codes, weights):
                normalized_weight = weight / total_weight
                target_value = investable_value * normalized_weight
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
                order_result = _submit_target_value(
                    code, target_value, selected_funds.loc[code, 'last_price'], current_value)
                log.info('目标市值已提交 | {} order_id={}'.format(
                    code, getattr(order_result, 'order_id', None)))
            log.info('===== 开盘选股下单完成 =====')
        else:
            log.warn('无折价ETF可选，已执行全部卖出，今日不再买入')
            _notify('无折价ETF可选，已提交全部卖出目标，今日不再买入')

    except Exception as e:
        log.error(f"开盘下单异常：{e}")


def handle_risk_management(context: 'Context') -> None:
    """止盈止损；LIVE一次提交完整组合目标，避免同轮多个intent。"""
    try:
        if not _advance_live_intent(context):
            return
        portfolio = _portfolio(context)
        hold_codes = list(portfolio.positions.keys())
        if not hold_codes:
            log.info('风控检查 | 当前无持仓')
            return
        log.info(f'风控检查开始 | 持仓 {len(hold_codes)} 只 | {context.current_dt}')
        exits: List[str] = []
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
                if _runtime_mode() == 'LIVE':
                    exits.append(hold_code)
                else:
                    order_result = _submit_target_amount(hold_code, 0)
                    log.info('止损清仓目标已提交 | {} order_id={}'.format(
                        hold_code, getattr(order_result, 'order_id', None)))

            # 止盈逻辑：涨超成本价110%（10%止盈）
            elif current_price > cost_basis * TAKE_PROFIT_RATIO:
                msg = (f"止盈触发 | 标的：{hold_code} | 成本价：{cost_basis:.4f} | "
                       f"当前价：{current_price:.4f} | 时间：{context.current_dt}")
                log.info(msg)
                _notify(msg)
                if _runtime_mode() == 'LIVE':
                    exits.append(hold_code)
                else:
                    order_result = _submit_target_amount(hold_code, 0)
                    log.info('止盈清仓目标已提交 | {} order_id={}'.format(
                        hold_code, getattr(order_result, 'order_id', None)))

        if exits and _runtime_mode() == 'LIVE':
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
            result = _submit_live_targets(context, target_weights, marks, key)
            log.info('真实风控目标已提交 | intent_id={} exits={}'.format(
                result['intent']['intent_id'], exits))

    except Exception as e:
        log.error(f"风控执行异常：{e}")


def after_market_check(context: 'Context') -> None:
    """记录BACKTEST原生组合或LIVE真实StrategyLedger组合。"""
    portfolio = _portfolio(context)
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
    if _runtime_mode() == 'LIVE':
        log.info('真实指标 | NAV={:.6f} 收益={:.2%} 费用={:.2f}'.format(
            portfolio.nav, portfolio.returns, portfolio.fees))
