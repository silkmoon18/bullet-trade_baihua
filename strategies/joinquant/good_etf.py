# 克隆自聚宽文章：https://www.joinquant.com/post/1399
# 标题：【量化课堂】多因子策略入门
# 作者：JoinQuant量化课堂

# 克隆自聚宽文章：https://www.joinquant.com/post/68704
# 标题：三马105+七星1.5+11年收益306倍回撤12.47
# 作者：rbq2025

# 克隆自聚宽文章基础上的自定义修改版
# 核心修改：消除风控函数中的未来函数风险，保证回测/实盘一致性
# 统一仓库来源：bt_quant@e6462dd（导入时已移除连接凭据）
# 状态：迁移基线，尚未完成 StrategyLedger 实盘改造，禁止直接用于真实资金。

# 导入必要的库
import datetime  # 显式导入，保证复制到聚宽后可直接运行

from jqdata import *
import bullet_trade_jq_remote_helper as bt

# ===== 配置区域 =====
DEBUG = False
SEND_SIGNALS = False
FEISHU_WEBHOOK_URL = ''

BT_REMOTE_HOST = "127.0.0.1"
BT_REMOTE_PORT = 58620
BT_REMOTE_TOKEN = ""
ACCOUNT_KEY = None  # 可选
SUB_ACCOUNT = None  # 可选
STRATEGY_NAME = 'good_etf'  # 策略标识：随订单上报，用于按策略隔离撤单/对账
BT_TLS_CERT = None  # 可选：TLS 证书文件路径（上传到聚宽研究环境后填文件名，如 'server.crt'）

# ===== 策略参数 =====
MAX_HOLD_NUM = 3           # 最大持仓只数：选折价最深的前 N 只
MIN_MONEY = 5e6            # 流动性下限：前一日成交额 > 500 万
MAX_MONEY = 2e7            # 流动性上限：前一日成交额 < 2000 万
STOP_LOSS_RATIO = 0.95     # 止损线：现价跌破成本价 95%
TAKE_PROFIT_RATIO = 1.10   # 止盈线：现价涨超成本价 110%
# 港股类 ETF 过滤关键词（名称包含任一关键词即剔除）
HK_KEYWORDS = ['港股', '恒生', 'H股', '国企', '香港', '恒生科技', '港股通', '恒生互联网']

# ===== 执行参数 =====
BUY_PRICE_FLOAT_PCT = 0.002   # 限价买入浮动比例：限价 = 最新价 × (1 + 0.2%)，上浮提高成交率
CHASE_MAX_ROUNDS = 3          # 未成交部分最大追单轮数（每轮撤单后重挂）
CHASE_ROUND_TIMEOUT = 8       # 每轮等待成交秒数
SKIP_SUSPENDED_LIMITUP = True  # 选股时剔除停牌/涨停标的（False 恢复原行为）


def _ensure_configured():
    if not BT_REMOTE_TOKEN:
        raise RuntimeError(
            "请先在 BT_REMOTE_HOST/BT_REMOTE_PORT/BT_REMOTE_TOKEN/ACCOUNT_KEY/SUB_ACCOUNT 填写远程服务器配置")
    bt.configure(
        host=BT_REMOTE_HOST,
        port=BT_REMOTE_PORT,
        token=BT_REMOTE_TOKEN,
        account_key=ACCOUNT_KEY,
        sub_account_id=SUB_ACCOUNT,
        jq_order=order,
        jq_order_value=order_value,
        jq_order_target=order_target,
        jq_order_target_value=order_target_value,
        debug=DEBUG,
        send_signals=SEND_SIGNALS,
        feishu_webhook_url=FEISHU_WEBHOOK_URL,
        strategy_name=STRATEGY_NAME,
        tls_cert=BT_TLS_CERT
    )
    # 让券商端可用数据补价
    bt.get_broker_client().bind_data_client(bt.get_data_client())
    log.info(f'远程配置完成 | host={BT_REMOTE_HOST} port={BT_REMOTE_PORT} | '
             f'SEND_SIGNALS={SEND_SIGNALS} DEBUG={DEBUG}')


def initialize(context):
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

    # 首次启动即完成远程配置（process_initialize 在重启时会再次调用，configure 是幂等的）
    _ensure_configured()

    log.info(f'策略初始化完成 | 最大持仓={MAX_HOLD_NUM} 流动性=({MIN_MONEY / 1e4:.0f}万,{MAX_MONEY / 1e4:.0f}万) '
             f'止损线={STOP_LOSS_RATIO:.0%} 止盈线={TAKE_PROFIT_RATIO:.0%}')

    # 每日运行函数调度
    # 9:20 预处理选股数据（前一日数据，无未来函数）
    run_daily(before_market_open, '09:20', reference_security='000300.XSHG')
    # 9:30 执行开盘选股+下单
    run_daily(market_open, '09:30', reference_security='000300.XSHG')
    # 9:30 早盘风控检查（开盘下单后随即检查一次）
    run_daily(handle_risk_management, time='09:30', reference_security='000300.XSHG')
    # 10:30 / 13:30 盘中风控检查（提高止盈止损响应速度）
    run_daily(handle_risk_management, time='10:30', reference_security='000300.XSHG')
    run_daily(handle_risk_management, time='13:30', reference_security='000300.XSHG')
    # 14:50 尾盘风控检查
    run_daily(handle_risk_management, time='14:50', reference_security='000300.XSHG')
    # 14:55 尾盘对账（模拟持仓 vs 券商持仓，仅报告）
    run_daily(after_market_check, time='14:55', reference_security='000300.XSHG')
    log.info('任务调度完成 | 09:20 盘前预处理 | 09:30 开盘下单 | 风控: 09:30/10:30/13:30/14:50 | 14:55 尾盘对账')


def process_initialize(context):
    """
    聚宽重启/代码刷新时调用，此处完成所有初始化与任务注册。
    """
    log.info(f"process_initialize 重建配置 {datetime.datetime.now()}")
    _ensure_configured()


def before_market_open(context):
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
        fund_list = []
        hk_samples = []
        hk_count = 0
        for code, name in zip(all_etf.index, all_etf['display_name']):
            if isinstance(name, str) and any([kw in name for kw in HK_KEYWORDS]):
                hk_count += 1
                if len(hk_samples) < 5:
                    hk_samples.append(f'{code}({name})')
                continue
            fund_list.append(code)
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


def market_open(context):
    """开盘执行：选股+按折价率权重下单"""
    log.info('===== 开盘选股下单开始 =====')
    try:
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
        df['last_price'] = [current_data[code].last_price for code in df.index.tolist()]

        # 剔除停牌/涨停标的（避免选中后委托无法成交，浪费名额）
        if SKIP_SUSPENDED_LIMITUP:
            keep = []
            for code in df.index:
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
            bt.notify(f'选中折价ETF {len(order_fund_codes)} 只: {order_fund_codes}')

        # 撤销昨日遗留未成交挂单，避免干扰今日追单
        cancelled = bt.cancel_all_open_orders()
        if cancelled:
            log.info(f'已撤销 {cancelled} 笔遗留挂单')

        # 第一步：卖出不在选定列表中的持仓（调仓，市价追单直到清仓）
        # 注意：迭代持仓列表副本，避免下单过程中持仓变化影响遍历
        hold_codes = list(context.portfolio.positions.keys())
        log.info(f'当前持仓 {len(hold_codes)} 只: {hold_codes}')
        for hold_code in hold_codes:
            if hold_code not in order_fund_codes:
                pos = context.portfolio.positions[hold_code]
                log.info(f'调仓卖出 | {hold_code} 数量={pos.total_amount} 成本={pos.avg_cost:.4f}')
                result = bt.order_target_sync(hold_code, 0, max_rounds=CHASE_MAX_ROUNDS,
                                              round_timeout=CHASE_ROUND_TIMEOUT)
                if result:
                    msg = (f'卖出结果 | {hold_code} 成交={result["filled_amount"]} '
                           f'剩余={result["remaining"]} 轮数={result["rounds"]} 完成={result["done"]}')
                    log.info(msg)
                    bt.notify(msg)

        # 第二步：按折价率绝对值权重分配资金下单
        if not selected_funds.empty:
            # 计算权重（折价率绝对值占比）
            weights = selected_funds['premium'].abs().tolist()
            total_weight = sum(weights) if sum(weights) != 0 else 1e-9  # 防除零

            # 可用资金（全部用于买入；卖出已同步完成，此处口径准确）
            available_cash = context.portfolio.available_cash
            log.info(f'可用资金: {available_cash:.2f}')

            # 按权重下单（限价追单：每轮按最新价×(1+浮动比例) 重新定价）
            for code, weight in zip(order_fund_codes, weights):
                target_value = available_cash * (weight / total_weight)
                log.info(f'调仓买入 | {code} 权重={weight / total_weight:.1%} 目标市值={target_value:.2f}')
                result = bt.order_target_value_sync(
                    code, target_value,
                    price_float_pct=BUY_PRICE_FLOAT_PCT,
                    max_rounds=CHASE_MAX_ROUNDS,
                    round_timeout=CHASE_ROUND_TIMEOUT)
                if result:
                    msg = (f'买入结果 | {code} 目标={result["target_amount"]} '
                           f'成交={result["filled_amount"]} 剩余={result["remaining"]} '
                           f'轮数={result["rounds"]} 完成={result["done"]}')
                    log.info(msg)
                    bt.notify(msg)
            log.info('===== 开盘选股下单完成 =====')
        else:
            log.warn('无折价ETF可选，已执行全部卖出，今日不再买入')
            bt.notify('无折价ETF可选，已执行全部卖出，今日不再买入')

    except Exception as e:
        log.error(f"开盘下单异常：{e}")


def handle_risk_management(context):
    """风控函数：9:30/14:50 双触发，止盈10%/止损5%（实时价格，无未来函数）"""
    hold_codes = list(context.portfolio.positions.keys())
    if not hold_codes:
        log.info('风控检查 | 当前无持仓')
        return
    log.info(f'风控检查开始 | 持仓 {len(hold_codes)} 只 | {context.current_dt}')
    try:
        # 遍历所有持仓ETF（迭代列表副本）
        for hold_code in hold_codes:
            # 关键修复：用聚宽官方无未来函数的实时价格
            position = context.portfolio.positions[hold_code]
            current_price = position.price  # 实时最新价（回测/实盘一致）
            cost_basis = position.avg_cost  # 持仓成本价
            pnl = (current_price / cost_basis - 1) if cost_basis else 0.0
            log.info(f'持仓检查 | {hold_code} 成本={cost_basis:.4f} 现价={current_price:.4f} 盈亏={pnl:.2%}')

            # 止损逻辑：跌破成本价95%（5%止损）
            if current_price < cost_basis * STOP_LOSS_RATIO:
                msg = (f"止损触发 | 标的：{hold_code} | 成本价：{cost_basis:.4f} | "
                       f"当前价：{current_price:.4f} | 时间：{context.current_dt}")
                log.info(msg)
                bt.notify(msg)
                result = bt.order_target_sync(hold_code, 0, max_rounds=CHASE_MAX_ROUNDS,
                                              round_timeout=CHASE_ROUND_TIMEOUT)
                if result:
                    log.info(f'止损卖出结果 | {hold_code} 成交={result["filled_amount"]} '
                             f'剩余={result["remaining"]} 完成={result["done"]}')

            # 止盈逻辑：涨超成本价110%（10%止盈）
            elif current_price > cost_basis * TAKE_PROFIT_RATIO:
                msg = (f"止盈触发 | 标的：{hold_code} | 成本价：{cost_basis:.4f} | "
                       f"当前价：{current_price:.4f} | 时间：{context.current_dt}")
                log.info(msg)
                bt.notify(msg)
                result = bt.order_target_sync(hold_code, 0, max_rounds=CHASE_MAX_ROUNDS,
                                              round_timeout=CHASE_ROUND_TIMEOUT)
                if result:
                    log.info(f'止盈卖出结果 | {hold_code} 成交={result["filled_amount"]} '
                             f'剩余={result["remaining"]} 完成={result["done"]}')

    except Exception as e:
        log.error(f"风控执行异常：{e}")


def after_market_check(context):
    """尾盘对账：对比聚宽模拟持仓与券商实际持仓（仅报告，不自动纠偏）"""
    log.info('===== 尾盘对账开始 =====')
    try:
        remote_positions = bt.get_positions()
        remote_map = {p.security: p for p in remote_positions}
        sim_map = dict(context.portfolio.positions)

        diff_msgs = []
        all_codes = list(set(sim_map.keys()) | set(remote_map.keys()))
        for code in all_codes:
            sim_amt = sim_map[code].total_amount if code in sim_map else 0
            rem_amt = remote_map[code].amount if code in remote_map else 0
            log.info(f'对账 | {code} 模拟={sim_amt} 实盘={rem_amt}')
            if sim_amt != rem_amt:
                diff_msgs.append(f'{code} 模拟={sim_amt} 实盘={rem_amt}')

        acct = bt.get_account()
        log.info(f'对账资金 | 模拟可用={context.portfolio.available_cash:.2f} '
                 f'实盘可用={acct.available_cash:.2f} 实盘总资产={acct.total_value:.2f}')

        if diff_msgs:
            msg = '尾盘对账发现持仓差异:\n' + '\n'.join(diff_msgs)
            log.warn(msg)
            bt.notify(msg)
        else:
            log.info('尾盘对账一致，无持仓差异')
    except Exception as e:
        # 回测环境无券商连接时会抛异常，跳过即可
        log.warn(f'尾盘对账跳过（可能为回测环境）: {e}')
