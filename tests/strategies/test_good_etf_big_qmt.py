"""Independent BigQMT single file. Synthetic broker only; no network or terminal."""
import ast
import datetime as dt
import importlib.util
from pathlib import Path
import sys
import tokenize
import types

import pandas as pd
import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / 'strategies' / 'big_qmt' / 'good_etf' / 'good_etf.py'
LEGACY = REPO / 'strategies' / 'qmt' / 'good_etf' / 'tests' / 'test_native.py'
spec = importlib.util.spec_from_file_location('native_contract_fixture', LEGACY)
legacy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(legacy)
FakeQmt, NOW = legacy.FakeQmt, legacy.NOW


@pytest.fixture
def mod(monkeypatch):
    module = types.ModuleType('independent_bigqmt_good_etf')
    monkeypatch.setitem(sys.modules, module.__name__, module)
    exec(compile(SCRIPT.read_bytes(), str(SCRIPT), 'exec'), module.__dict__)
    monkeypatch.setattr(module, 'china_now', lambda: NOW)
    return module


@pytest.fixture
def env(mod, tmp_path):
    fake = FakeQmt()
    config = mod.Settings()
    config.STATE_DIR = str(tmp_path / 'broker')
    config.INITIAL_CAPITAL = 10000.
    config.ENABLE_TRADING = True
    config.RETRY_SECONDS = 0
    data = mod.QmtData(fake, fake.namespace(), config, lambda _: None)
    events = []
    executor = mod.Executor(data, config, 'mock-account', lambda *args: events.append(args))
    yield fake, executor, data, config, events
    executor.close()


CASES = (
    'test_selection_contract_multisecurity', 'test_prepare_filter_order_boundaries_and_nav',
    'test_risk_strict_thresholds_and_empty_selection', 'test_nav_requires_dates_not_iopv',
    'test_quote_formats_suspension_none_is_trading', 'test_no_preopen_quote_used_as_opening_price',
    'test_previous_daily_date_and_missing_inner_join', 'test_nav_file_date_and_valid_override',
    'test_fixed_opening_limit_not_ask_and_no_cage', 'test_partial_fills_deduplicate_and_fee_unknown',
    'test_status_55_is_live_53_is_terminal_wait_for_deal', 'test_sell_then_buy_and_t1',
    'test_manual_position_not_owned_and_no_nonexistent_sell', 'test_unknown_submit_not_retried_after_restart',
    'test_midnight_cancel_does_not_fake_completion', 'test_disabled_has_no_order_or_cancel',
    'test_rejection_does_not_retry_and_capital_not_reset', 'test_buy_cash_reservations_across_symbols',
    'test_risk_cancels_buy_and_does_not_reprice_existing_exit', 'test_direct_parity_with_existing_joinquant_strategy',
    'test_invalid_fill_id_and_direction_not_booked', 'test_same_account_strategy_cannot_run_twice',
    'test_runtime_schedule_open_once_restart', 'test_top_configuration_used_by_native_entry',
    'test_stop_releases_lock_and_restart_preserves_ledger', 'test_repeated_init_does_not_unlock_running_instance',
)


@pytest.mark.parametrize('case', CASES)
def test_existing_contracts_against_new_script(case, mod, env, tmp_path, monkeypatch):
    import inspect
    fn = getattr(legacy, case)
    args = dict(mod=mod, env=env, tmp_path=tmp_path, monkeypatch=monkeypatch)
    if case in ('test_top_configuration_used_by_native_entry',
                'test_stop_releases_lock_and_restart_preserves_ledger',
                'test_repeated_init_does_not_unlock_running_instance'):
        monkeypatch.setattr(mod, 'ENABLE_TRADING', True)  # These contracts exercise the trading ledger, not selection-only.
    if 'env' not in inspect.signature(fn).parameters:
        env[1].close()  # entry/restart contracts allocate their own ledger in tmp_path.
    fn(**{name: args[name] for name in inspect.signature(fn).parameters})


def submit(ex, **kwargs):
    legacy.target(ex, **kwargs)
    ex.advance(NOW)
    return legacy.latest(ex)


def test_isolated_defaults_and_single_file_py36(mod):
    assert mod.STRATEGY_ID == 'good_etf_big_qmt'
    assert mod.INITIAL_CAPITAL == 1000
    assert mod.ENABLE_TRADING is True
    assert mod.PAPER_TRADING is False and mod.PAPER_INITIAL_CAPITAL == 5000
    assert mod.STATE_DIR != r'C:\qmt_good_etf_data'
    assert mod.ACCOUNT_ID == mod.FEISHU_WEBHOOK == ''
    with tokenize.open(SCRIPT) as stream:
        source = stream.read()
    tree = ast.parse(source, feature_version=(3, 6))
    names = {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    names |= {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
    assert not any(name.startswith(('bullet_trade', 'jqdata', 'xtquant', 'requests', 'dataclasses')) for name in names)
    assert not any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                   and n.func.id in ('passorder', 'cancel') for n in tree.body)


def test_real_callback_direction_missing_and_native_price_amount(env):
    fake, ex, data, cfg, events = env
    order = submit(ex)
    report = legacy.row(order, m_strTradeID='fill-1', m_nVolume=100,
                        m_dTradePrice=1.002, m_dPrice=.4, m_dTradeAmount=100.2)
    report['m_nOffsetFlag'] = None
    data.quotes = lambda *a, **kw: pytest.fail('normal report must not query quote')
    ex.report('deal', report)
    ex.report('deal', report)
    assert order['booked'] == 100
    assert ex.state['cash'] == pytest.approx(9899.8)
    assert ex.state['positions']['510300.SH']['cost'] == pytest.approx(100.2)
    assert len(ex.state['fills']) == 1
    assert next(iter(ex.state['fills'].values()))['estimated'] is False


@pytest.mark.parametrize('fields', [
    {'m_dTradePrice': 0, 'm_dTradeAmount': 0},
    {'m_dTradePrice': .4, 'm_dTradeAmount': 100.2},
    {'m_dTradePrice': float('nan'), 'm_dTradeAmount': float('nan')},
])
def test_bad_price_only_then_quote_and_estimated_booking(env, fields):
    fake, ex, data, cfg, events = env
    order = submit(ex)
    calls = []
    data.quotes = lambda codes, now: calls.append(codes) or {'510300.SH': {'last_price': 1.01}}
    report = legacy.row(order, m_strTradeID='zero', m_nVolume=100, **fields)
    ex.report('deal', report)
    ex.report('deal', report)
    assert calls == [['510300.SH']]
    assert order['booked'] == 100
    assert ex.state['cash'] == 9899
    assert next(iter(ex.state['fills'].values()))['estimated'] is True
    assert '非精确收益' in events[-1][2]


def test_one_tick_amount_difference_does_not_query_price(env):
    fake, ex, data, cfg, events = env
    order = submit(ex)
    data.quotes = lambda *a: pytest.fail('one tick rounding is not a broken fill')
    ex.report('deal', legacy.row(order, m_strTradeID='rounded', m_nVolume=100,
                                 m_dTradePrice=1., m_dTradeAmount=100.1, m_dCommission=.1))
    assert ex.state['cash'] == pytest.approx(9899.8)


def test_estimated_fill_late_price_not_silently_relabelled(env):
    fake, ex, data, cfg, events = env
    order = submit(ex)
    data.quotes = lambda *a: {'510300.SH': {'last_price': 1.01}}
    report = legacy.row(order, m_strTradeID='late', m_nVolume=100, m_dTradePrice=0)
    ex.report('deal', report)
    report.update(m_dTradePrice=1.002, m_dTradeAmount=100.2)
    ex.report('deal', report)
    assert ex.state['cash'] == 9899
    assert next(iter(ex.state['fills'].values()))['estimated'] is True
    assert events[-1][0] == '估算成交待核对'


def test_replayed_fill_missing_price_keeps_original_known_price(env):
    fake, ex, data, cfg, events = env
    order = submit(ex)
    report = legacy.row(order, m_strTradeID='known', m_nVolume=100, m_dTradePrice=1.002)
    data.quotes = lambda *a: pytest.fail('already-booked fill needs no quote')
    ex.report('deal', report)
    report.update(m_dTradePrice=0, m_dTradeAmount=0)
    ex.report('deal', report)
    assert ex.state['cash'] == pytest.approx(9899.8)
    assert next(iter(ex.state['fills'].values()))['estimated'] is False


def test_trade_price_zero_valid_old_price_field_needs_no_quote(env):
    fake, ex, data, cfg, events = env
    order = submit(ex)
    data.quotes = lambda *a: pytest.fail('valid legacy price should be used')
    ex.report('deal', legacy.row(order, m_strTradeID='legacy', m_nVolume=100,
                                 m_dTradePrice=0, m_dPrice=1.002, m_dTradeAmount=100.2,
                                 m_dCommission=None, m_dComission=.03))
    assert ex.state['cash'] == pytest.approx(9899.77)


def test_unmatched_blank_direction_is_not_accepted(env):
    fake, ex, data, cfg, events = env
    order = submit(ex)
    report = legacy.row(order, m_strTradeID='foreign', m_nVolume=100, m_dPrice=1)
    report.update(m_strRemark='', m_strOrderSysID='', m_nOffsetFlag=None)
    ex.report('deal', report)
    assert ex.state['fills'] == {}


def test_receipt_side_conflict_rejected(env):
    fake, ex, data, cfg, events = env
    order = submit(ex)
    with pytest.raises(RuntimeError, match='方向'):
        ex.report('deal', legacy.row(order, m_strTradeID='bad', m_nVolume=100,
                                     m_dPrice=1, m_nOpType=24))
    assert ex.state['fills'] == {}


@pytest.mark.parametrize('exchange,expected,flag', [('SH', 42, 'SH_NATIVE_MARKET'), ('SZ', 47, 'SZ_NATIVE_MARKET')])
def test_market_sell_native_and_explicit_limit_fallback(env, exchange, expected, flag):
    fake, ex, data, cfg, events = env
    code = '510300.' + exchange
    goal = ex.target(0, 1.015, 'market', '20260911')
    assert ex.order_price(code, goal, 'SELL') == (1.0, expected)
    setattr(cfg, flag, False)
    assert ex.order_price(code, goal, 'SELL') == (1.0, 11)
    goal['style'] = 'profit'
    assert ex.order_price(code, goal, 'SELL') == (1.013, 11)


def test_partial_full_order_receipt_does_not_manufacture_fills(env, mod):
    fake, ex, data, cfg, events = env
    order = submit(ex)
    ex.report('order', legacy.row(order, m_nOrderStatus=56, m_nVolumeTraded=100))
    assert order['filled'] == 100 and order['booked'] == 0
    assert not mod.settled(order)
    ex.advance(NOW)
    assert len(fake.sent) == 1


def test_bad_one_quote_excluded_whole_group_not_aborted(env, mod):
    fake, ex, data, cfg, events = env
    fake.ticks['510301.SH'] = dict(lastPrice=0, timetag='20260911 09:30:00')
    quotes = data.quotes(['510300.SH', '510301.SH'], NOW, strict=False)
    decision = mod.select(pd.DataFrame({'unit_net_value': [1.1, 1.2]},
                                      index=['510300.SH', '510301.SH']), quotes)
    assert decision.weights == {'510300.SH': .95}
    fake.ticks['510300.SH'] = fake.ticks['510301.SH']
    with pytest.raises(RuntimeError, match='全体'):
        data.quotes(['510300.SH', '510301.SH'], NOW, strict=False)


def test_empty_selection_not_confused_with_missing_prices(env, mod):
    fake, ex, data, cfg, events = env
    quotes = data.quotes(['510300.SH'], NOW, strict=False)
    result = mod.select(pd.DataFrame({'unit_net_value': [.9]}, index=['510300.SH']), quotes)
    assert result == mod.Decision({}, {})


def test_local_log_without_feishu(mod, tmp_path):
    fake = FakeQmt()
    cfg = mod.Settings()
    cfg.ACCOUNT_ID, cfg.STATE_DIR = 'mock-account', str(tmp_path)
    runtime = mod.Runtime(fake, fake.namespace(), cfg, mod.prepare, mod.select, mod.risk_signal)
    try:
        runtime.start('09:20', '09:30', ('10:30',), '14:55')
        logs = list(tmp_path.glob('*.log'))
        assert len(logs) == 1
        assert '独立大QMT版' in logs[0].read_text(encoding='utf-8')
        assert not fake.sent
    finally:
        runtime.close()


def test_backtest_mode_never_creates_live_account_ledger(mod, tmp_path):
    fake = FakeQmt()
    fake.do_back_test, fake.period, fake.dividend_type, fake.capital = True, '1m', 'none', 5000.
    cfg = mod.Settings()
    cfg.STATE_DIR = str(tmp_path / 'live')
    runtime = mod.create_runtime(fake, fake.namespace(), cfg, mod.prepare, mod.select, mod.risk_signal)
    assert isinstance(runtime, mod.BacktestRuntime)
    assert runtime.data.sessions == {}
    runtime.close()
    assert not (tmp_path / 'live').exists()
    assert fake.sent == []


BACKTEST_CASES = (
    'test_backtest_isolated_native_entry_and_panel_capital',
    'test_backtest_selection_and_single_equity_budget', 'test_first_completed_minute_then_next_day',
    'test_historical_pool_and_nav_never_use_today',
    'test_historical_suspension_price_limit_and_explicit_no_limit',
    'test_platform_equity_and_snapshot_no_fee_or_fill_fabrication',
    'test_reduce_before_increase_and_no_minute_chasing',
    'test_empty_selection_clears_but_no_candidates_does_not',
    'test_warmup_and_mode_switch_cannot_trade',
    'test_backtest_timestamp_formats',
)


@pytest.fixture
def bt(mod, tmp_path, monkeypatch):
    import json
    # Reuse synthetic fixtures only; the deployed single file imports none of these.
    monkeypatch.setitem(sys.modules, 'test_native', legacy)
    test_spec = importlib.util.spec_from_file_location('native_backtest_fixture', LEGACY.with_name('test_backtest.py'))
    suite = importlib.util.module_from_spec(test_spec)
    test_spec.loader.exec_module(suite)
    cfg = mod.Settings()
    path = tmp_path / 'history.json'
    path.write_text(json.dumps(suite.history_document()), encoding='utf-8')
    cfg.BACKTEST_DATA_FILE = str(path)
    cfg.ACCOUNT_ID, cfg.STATE_DIR = 'must-not-use', str(tmp_path / 'live-ledger')
    fake = suite.BacktestQmt()
    fake.prices = {'510001.SH': 1.9, '510002.SH': 1.5, '510003.SH': 1., '520590.SH': .01}
    fake.money['510004.SH'] = 5e6
    runtime = mod.create_runtime(fake, fake.namespace(), cfg, mod.prepare, mod.select, mod.risk_signal)
    runtime.start('09:20', '09:30', ('10:30', '13:30', '14:50'), '14:55')
    yield (fake, runtime, cfg), suite
    runtime.close()
    assert not (tmp_path / 'live-ledger').exists()


@pytest.mark.parametrize('case', BACKTEST_CASES)
def test_backtest_contracts_against_new_script(case, bt, mod, tmp_path, monkeypatch, capsys):
    import inspect
    fixture, suite = bt
    fn = getattr(suite, case)
    if case == 'test_backtest_isolated_native_entry_and_panel_capital':
        # This inherited fixture asserts the older script's observation-only
        # default; enabled-trading isolation is tested separately below.
        monkeypatch.setattr(mod, 'ENABLE_TRADING', False)
    args = dict(bt=fixture, mod=mod, tmp_path=tmp_path, monkeypatch=monkeypatch, capsys=capsys)
    fn(**{name: args[name] for name in inspect.signature(fn).parameters})


def test_qmt_editor_source_gbk_and_all_config_at_top(mod):
    with tokenize.open(SCRIPT) as stream:
        source = stream.read()
    top, rest = source.split('# ==================== 配置结束', 1)
    config = [n.targets[0].id for n in ast.parse(top).body if isinstance(n, ast.Assign)]
    assert all(getattr(mod.Settings(), name) == getattr(mod, name) for name in config)
    assert 'SZ_NATIVE_MARKET' in config and 'class Settings' in rest
    raw = SCRIPT.read_bytes()
    assert raw.startswith(b'# encoding:gbk')
    assert raw.decode('gbk') == source
    compile(raw, str(SCRIPT), 'exec')


def test_lossy_clipboard_cannot_change_runtime_strings():
    # Some QMT copy/save paths replace Chinese source characters with '?'.
    # Only comments may depend on source encoding; literals must survive.
    with tokenize.open(SCRIPT) as stream:
        source = stream.read()
    copied = source.encode('ascii', errors='replace').decode('ascii')
    assert ast.dump(ast.parse(copied)) == ast.dump(ast.parse(source))
    compile(copied.encode('ascii'), str(SCRIPT), 'exec')


@pytest.fixture
def qmt_only(bt, mod):
    fixture, suite = bt
    fake, old_runtime, cfg = fixture
    old_runtime.close()
    cfg.BACKTEST_DATA_FILE = ''
    securities = suite.history_document()['sessions']['20260911']['securities']
    fake.pcf = {code: dict(nav=2., preTradingDay='20260910', tradingDay='20260911')
                for code in securities}
    fake.sector_calls, fake.pcf_calls = [], []

    def sector(name, asof):
        fake.sector_calls.append((name, asof))
        return list(securities)

    def pcf(code):
        fake.pcf_calls.append(code)
        return fake.pcf.get(code, {})

    fake.get_stock_list_in_sector = sector
    fake.get_etf_info = pcf
    fake.get_instrument_detail = lambda code: dict(InstrumentName=securities[code]['name'],
                                                OpenDate='20200101', ExpireDate='99999999')
    market = fake.get_market_data_ex
    fake.limit_stamp = int(dt.datetime(2026, 9, 11, tzinfo=dt.timezone(dt.timedelta(hours=8))).timestamp() * 1000)
    fake.limit_value, fake.limit_missing = 3., False

    def prices(fields, codes, **kwargs):
        if kwargs['period'] != 'stoppricedata':
            return market(fields, codes, **kwargs)
        fake.market_calls.append((fields, codes, kwargs))
        assert kwargs['end_time'] <= fake.now.strftime('%Y%m%d%H%M%S')
        assert not kwargs['subscribe'] and not kwargs['fill_data']
        if fake.limit_missing:
            return {}
        return {code: pd.DataFrame({'time': [fake.limit_stamp], '涨停价': [fake.limit_value],
                                   '跌停价': [.01]}) for code in codes}

    fake.get_market_data_ex = prices
    runtime = mod.create_runtime(fake, fake.namespace(), cfg, mod.prepare, mod.select, mod.risk_signal)
    logs = []
    runtime.data.log = logs.append
    runtime.start('09:20', '09:30', ('10:30',), '14:55')
    yield fake, runtime, cfg, logs
    runtime.close()


def test_qmt_only_backtest_no_external_file_exact_selection_and_weights(qmt_only):
    fake, runtime, cfg, logs = qmt_only
    runtime.handlebar()
    assert cfg.BACKTEST_DATA_FILE == ''
    assert [code for code, _ in fake.orders] == ['510003.SH', '510002.SH', '510001.SH']
    raw = [abs(price / 2. - 1) * 100 for price in (1., 1.5, 1.9)]
    assert [amount for _, amount in fake.orders] == [fake.balance * (x / sum(raw) * .95) for x in raw]
    requested = dt.datetime.fromtimestamp(fake.sector_calls[0][1] / 1000,
                                         dt.timezone(dt.timedelta(hours=8)))
    assert requested == dt.datetime(2026, 9, 10, 23, 59, 59, tzinfo=dt.timezone(dt.timedelta(hours=8)))
    assert '520590.SH' not in fake.pcf_calls  # 港股过滤顺序不变
    assert any('QMT净值接口' in line and '覆盖=3/3' in line for line in logs)
    assert any('QMT历史涨停价' in line and '覆盖=3/3' in line for line in logs)
    assert not fake.sent and not fake.canceled and not fake.history_queries
    assert len([call for call in fake.market_calls if call[2]['period'] == 'stoppricedata']) == 1


@pytest.mark.parametrize('info', [
    {}, dict(nav=0, preTradingDay='20260910', tradingDay='20260911'),
    dict(nav=2., preTradingDay='', tradingDay=''),
    dict(nav=2., preTradingDay='20261013', tradingDay='20261014'),
    dict(nav=2., preTradingDay='20260909', tradingDay='20260911'),
])
def test_qmt_backtest_never_substitutes_current_or_undated_pcf(qmt_only, info):
    fake, runtime, cfg, logs = qmt_only
    fake.pcf = {code: info for code in fake.pcf}
    with pytest.raises(RuntimeError, match='历史前日单位净值'):
        runtime.handlebar()
    assert runtime.closed and not fake.orders
    assert any('覆盖=0/3' in line and '期望前日=20260910' in line for line in logs)


def test_qmt_backtest_pcf_permission_error_explained(qmt_only):
    fake, runtime, cfg, logs = qmt_only

    def denied(code):
        raise PermissionError('PCF未授权')

    runtime.data.namespace['get_etf_info'] = denied
    with pytest.raises(RuntimeError, match='BACKTEST_DATA_FILE'):
        runtime.handlebar()
    assert any('PCF未授权' in line for line in logs)
    assert not fake.orders


def test_qmt_historical_sector_failure_does_not_use_latest_pool(qmt_only):
    fake, runtime, cfg, logs = qmt_only
    calls = []

    def old_signature(*args):
        calls.append(args)
        raise TypeError('客户端不支持历史成员参数')

    fake.get_stock_list_in_sector = old_signature
    with pytest.raises(RuntimeError, match='历史ETF池不可用'):
        runtime.handlebar()
    assert len(calls) == 1 and len(calls[0]) == 2
    assert not fake.orders


@pytest.mark.parametrize('case', ['missing', 'zero', 'previous', 'future'])
def test_qmt_backtest_missing_or_wrong_day_limit_not_guessed(qmt_only, case):
    fake, runtime, cfg, logs = qmt_only
    if case == 'missing':
        fake.limit_missing = True
    elif case == 'zero':
        fake.limit_value = 0
    elif case == 'previous':
        fake.limit_stamp -= 86400000
    else:
        fake.limit_stamp += 86400000
    with pytest.raises(RuntimeError, match='历史涨停价缺失'):
        runtime.handlebar()
    assert not fake.orders and runtime.closed


def test_qmt_pcf_first_does_not_read_unused_live_file(env):
    fake, _, data, cfg, _ = env
    fake.pcf['510300.SH'] = dict(nav=1.2, preTradingDay='20260910', tradingDay='20260911')
    cfg.NAV_FILE = 'must-not-read-missing-file.json'
    assert data.nav(['510300.SH'], '20260910', '20260911').iloc[0, 0] == 1.2


def test_qmt_pcf_partial_live_file_only_fills_missing(env, tmp_path):
    import json
    fake, _, data, cfg, _ = env
    fake.pcf['510300.SH'] = dict(nav=1.2, preTradingDay='20260910', tradingDay='20260911')
    path = tmp_path / 'nav.json'
    path.write_text(json.dumps(dict(date='20260910', nav={'510300.SH': 9., '510500.SH': 2.})), encoding='utf-8')
    cfg.NAV_FILE = str(path)
    result = data.nav(['510300.SH', '510500.SH'], '20260910', '20260911')
    assert result['unit_net_value'].to_dict() == {'510300.SH': 1.2, '510500.SH': 2.}


def test_qmt_first_backtest_file_only_fills_missing(qmt_only):
    fake, runtime, cfg, logs = qmt_only
    runtime.data.sessions = {'20260911': dict(previous_date='20260910', securities={
        code: dict(name='兜底名称', nav=9., high_limit=20.) for code in fake.pcf})}
    fake.pcf['510002.SH'] = {}  # 只有这一只需要历史文件补净值
    runtime.handlebar()
    assert runtime.frame.loc['510001.SH', 'unit_net_value'] == 2.
    assert runtime.frame.loc['510002.SH', 'unit_net_value'] == 9.
    assert runtime.data.limits['510001.SH'] == 3.
    assert any('仅补QMT缺失净值 1 只' in line for line in logs)
    assert len(fake.orders) == 3


def test_qmt_only_waits_for_first_bar_before_limit_query(qmt_only):
    fake, runtime, cfg, logs = qmt_only
    fake.now = fake.now.replace(minute=30)
    fake.limit_missing = True
    runtime.handlebar()
    assert not fake.orders and runtime.data.limits == {}
    assert not any(call[2]['period'] == 'stoppricedata' for call in fake.market_calls)
    fake.now = fake.now.replace(minute=31)
    fake.limit_missing = False
    runtime.handlebar()
    assert len(fake.orders) == 3 and not runtime.closed


@pytest.fixture
def selection(mod, tmp_path):
    fake = FakeQmt()
    cfg = mod.Settings()
    cfg.ENABLE_TRADING = False
    cfg.PAPER_TRADING = False  # Preserve the original observation-only contract.
    cfg.STATE_DIR = str(tmp_path / 'selection-log')
    cfg.ACCOUNT_ID = ''
    cfg.INITIAL_CAPITAL = 0  # Selection-only must not read or allocate cash.
    fake.codes = ['510001.SH', '510002.SH', '510003.SH', '510004.SH', '520590.SH']
    fake.prices = dict(zip(fake.codes, [1.9, 1.5, 1., .5, .01]))
    fake.pcf = {code: dict(nav=2., preTradingDay=0, tradingDay=0) for code in fake.codes}
    fake.sector_calls, fake.market_calls = [], []

    def forbidden(*args, **kwargs):
        pytest.fail('selection-only must not bind, query, trade or cancel any account')

    for name in ('set_account', 'get_trade_detail_data', 'get_history_trade_detail_data', 'passorder', 'cancel'):
        setattr(fake, name, forbidden)

    def sector(*args):
        fake.sector_calls.append(args)
        assert args == (cfg.ETF_SECTOR,)  # Current pool; no historical constituent request.
        return fake.codes

    def daily(fields, codes, **kwargs):
        fake.market_calls.append((fields, codes, kwargs))
        assert kwargs['period'] == '1d' and kwargs['end_time'] == '20260910235959'
        assert kwargs['dividend_type'] == 'none' and not kwargs['subscribe']
        return {code: pd.DataFrame({'high': [2.], 'low': [1.],
                                   'amount': [5e6 if code == '510004.SH' else 1e7]},
                                  index=['20260910']) for code in codes}

    fake.get_stock_list_in_sector, fake.get_market_data_ex = sector, daily
    fake.get_full_tick = lambda codes: {code: dict(lastPrice=fake.prices[code],
                                               timetag='20260911 09:30:01', openInt=13) for code in codes}
    runtime = mod.create_runtime(fake, fake.namespace(), cfg, mod.prepare, mod.select, mod.risk_signal)
    runtime.start('09:20', '09:30', ('10:30', '13:30', '14:50'), '14:55')
    yield fake, runtime, cfg
    runtime.close()


def test_selection_only_default_no_account_no_ledger_current_pool_and_exact_weights(selection, mod, capsys):
    fake, runtime, cfg = selection
    assert isinstance(runtime, mod.SelectionRuntime)
    assert not hasattr(runtime, 'account') and not hasattr(runtime, 'executor')
    runtime.tick(NOW.replace(minute=20))
    runtime.tick(NOW)
    result = runtime.last_decision
    assert list(result.weights) == ['510003.SH', '510002.SH', '510001.SH']
    raw = [abs(price / 2. - 1) * 100 for price in (1., 1.5, 1.9)]
    assert result.weights == {code: x / sum(raw) * .95 for code, x in zip(result.weights, raw)}
    assert '520590.SH' not in fake.market_calls[0][1]
    assert '510004.SH' not in runtime.frame.index  # Strict liquidity boundary unchanged.
    runtime.tick(NOW.replace(second=5))
    assert len(fake.sector_calls) == 1 and not fake.sent and not fake.canceled
    output = capsys.readouterr().out
    assert '净值日期=未核验' in output and '测试ETF' in output
    assert '单价=' in output and '单位净值=' in output and '折价率=' in output and '权重=' in output
    assert '前日成交额=' in output and '不用于交易' in output
    assert list(Path(cfg.STATE_DIR).glob('*.log'))
    assert not list(Path(cfg.STATE_DIR).rglob('*.json'))
    assert not list(Path(cfg.STATE_DIR).rglob('*.lock'))


def test_selection_only_callbacks_risk_snapshot_stop_are_account_free(selection, capsys):
    fake, runtime, cfg = selection
    runtime.handlebar()  # Initialization's historical bars must not select/trade.
    assert not fake.sector_calls
    runtime.tick(NOW)
    runtime.on_report('order', {})
    runtime.on_report('deal', {})
    runtime.on_error({}, 'foreign callback')
    runtime.tick(NOW.replace(hour=10, minute=30))
    runtime.tick(NOW.replace(hour=14, minute=55))
    runtime.close()
    runtime.tick(NOW.replace(hour=9, minute=30))
    assert '不计算持仓/收益' in capsys.readouterr().out
    assert len(fake.sector_calls) == 1 and not fake.sent and not fake.history_queries


def test_selection_only_does_not_select_late_or_outside_trading_day(selection):
    fake, runtime, cfg = selection
    runtime.tick(NOW.replace(minute=31))
    assert not fake.sector_calls and runtime.last_decision is None
    runtime.tick(NOW.replace(day=12))
    assert not fake.sector_calls and runtime.last_decision is None


def test_selection_only_rejects_known_stale_pcf_and_zero_nav(selection, mod):
    fake, runtime, cfg = selection
    fake.pcf['510001.SH'] = dict(nav=2., preTradingDay='20260909', tradingDay=0)
    fake.pcf['510002.SH'] = dict(nav=0, preTradingDay=0, tradingDay=0)
    runtime.tick(NOW)
    assert runtime.last_decision == mod.Decision({'510003.SH': .95}, {'510003.SH': 1.})
    assert runtime.data.unverified_nav == {'510003.SH'}
    assert not fake.sent


def test_selection_only_valid_dates_display_as_verified(selection, capsys):
    fake, runtime, cfg = selection
    fake.pcf = {code: dict(nav=2., preTradingDay='20260910', tradingDay='20260911') for code in fake.codes}
    runtime.tick(NOW)
    assert runtime.data.unverified_nav == set()
    assert '净值日期=20260910' in capsys.readouterr().out


def test_live_uses_valid_qmt_nav_with_missing_dates_without_external_file(mod, tmp_path, capsys):
    fake = FakeQmt()
    cfg = mod.Settings()
    cfg.ENABLE_TRADING, cfg.ACCOUNT_ID, cfg.STATE_DIR = True, 'mock-account', str(tmp_path)
    fake.pcf['510300.SH'] = dict(nav=1.2, preTradingDay=0, tradingDay=0)
    runtime = mod.create_runtime(fake, fake.namespace(), cfg, mod.prepare, mod.select, mod.risk_signal)
    try:
        assert isinstance(runtime, mod.Runtime) and runtime.data.allow_undated_nav
        assert runtime.settings.NAV_FILE == ''
        assert runtime.data.nav(['510300.SH'], '20260910', '20260911').iloc[0, 0] == 1.2
        assert runtime.data.unverified_nav == {'510300.SH'}
        assert runtime.data.nav_details['510300.SH']['preTradingDay'] == 0
        assert runtime.data.nav_details['510300.SH']['tradingDay'] == 0
        assert not runtime.data.nav_details['510300.SH']['date_verified']
        output = capsys.readouterr().out
        assert '无法证明PCF已更新' in output and '读取时间不等于净值日期' in output
        assert not fake.sent
    finally:
        runtime.close()


@pytest.mark.parametrize('missing', [None, '', 0, 0.0, '0', '0.0', '00000000'])
def test_live_missing_date_formats_and_partial_dates_are_unverified(env, mod, missing):
    fake, _, data, _, _ = env
    data.allow_undated_nav = True
    codes = ['510001.SH', '510002.SH', '510003.SH']
    fake.pcf = {
        codes[0]: dict(nav=1.2, preTradingDay=missing, tradingDay=missing),
        codes[1]: dict(nav=1.3, preTradingDay='20260910', tradingDay=missing),
        codes[2]: dict(nav=1.4, preTradingDay=missing, tradingDay='20260911'),
    }
    result = data.nav(codes, '20260910', '20260911')
    assert result['unit_net_value'].to_dict() == dict(zip(codes, [1.2, 1.3, 1.4]))
    assert data.unverified_nav == set(codes)


@pytest.mark.parametrize('prior,current', [('20260909', 0), (0, '20260910'),
                                          ('20260910', '20260914'), ('garbage', 0)])
def test_live_explicit_wrong_date_is_not_rescued_by_allowing_missing_dates(env, prior, current):
    fake, _, data, _, _ = env
    data.allow_undated_nav = True
    fake.pcf['510300.SH'] = dict(nav=1.2, preTradingDay=prior, tradingDay=current)
    with pytest.raises(RuntimeError, match='单位净值'):
        data.nav(['510300.SH'], '20260910', '20260911')
    assert not data.nav_details and not data.unverified_nav


@pytest.mark.parametrize('nav', [None, 0, -1, float('nan'), float('inf'), 'bad', True, False])
def test_live_missing_dates_do_not_make_invalid_nav_valid(env, nav):
    fake, _, data, _, _ = env
    data.allow_undated_nav = True
    fake.pcf['510300.SH'] = dict(nav=nav, preTradingDay=0, tradingDay=0, iopv=1.2)
    with pytest.raises(RuntimeError, match='单位净值'):
        data.nav(['510300.SH'], '20260910', '20260911')
    assert not data.nav_details


def test_live_strict_date_switch_remains_available(mod, tmp_path):
    fake, cfg = FakeQmt(), mod.Settings()
    cfg.ACCOUNT_ID, cfg.STATE_DIR, cfg.ALLOW_UNDATED_LIVE_NAV = 'mock-account', str(tmp_path), False
    fake.pcf['510300.SH'] = dict(nav=1.2, preTradingDay=0, tradingDay=0)
    runtime = mod.create_runtime(fake, fake.namespace(), cfg, mod.prepare, mod.select, mod.risk_signal)
    try:
        with pytest.raises(RuntimeError, match='单位净值'):
            runtime.data.nav(['510300.SH'], '20260910', '20260911')
    finally:
        runtime.close()


def test_backtest_never_accepts_undated_current_pcf_even_with_live_option(bt):
    (fake, runtime, cfg), _ = bt
    cfg.ALLOW_UNDATED_LIVE_NAV = True
    assert not runtime.data.allow_undated_nav
    fake.pcf['510001.SH'] = dict(nav=1.2, preTradingDay=0, tradingDay=0)
    assert runtime.data.pcf_nav(['510001.SH'], '20260910', '20260911') == {}


def test_live_matching_dates_are_verified_and_new_read_clears_old_warning(env):
    fake, _, data, _, _ = env
    data.allow_undated_nav = True
    fake.pcf['510300.SH'] = dict(nav=1.2, preTradingDay=0, tradingDay=0)
    data.nav(['510300.SH'], '20260910', '20260911')
    assert data.unverified_nav == {'510300.SH'}
    fake.pcf['510300.SH'].update(nav=1.3, preTradingDay='20260910', tradingDay='20260911')
    result = data.nav(['510300.SH'], '20260910', '20260911')
    assert result.iloc[0, 0] == 1.3 and data.unverified_nav == set()
    assert data.nav_details['510300.SH']['date_verified'] is True


def test_live_1000_opening_trades_from_qmt_undated_nav_without_file(mod, tmp_path, capsys):
    fake, cfg = FakeQmt(), mod.Settings()
    cfg.ACCOUNT_ID, cfg.STATE_DIR = 'mock-account', str(tmp_path)
    cfg.NAV_FILE = str(tmp_path / 'must-not-be-read.json')
    codes = ['510001.SH', '510002.SH', '510003.SH']
    fake.get_stock_list_in_sector = lambda *args: codes
    fake.get_market_data_ex = lambda fields, requested, **kwargs: {
        code: pd.DataFrame({'high': [2.], 'low': [1.], 'amount': [1e7]}, index=['20260910'])
        for code in requested}
    fake.pcf = {code: dict(nav=2., preTradingDay=0, tradingDay=0) for code in codes}
    runtime = mod.create_runtime(fake, fake.namespace(), cfg, mod.prepare, mod.select, mod.risk_signal)
    try:
        runtime.start('09:20', '09:30', ('10:30',), '14:55')
        runtime.tick(NOW.replace(minute=20))
        assert not fake.sent and runtime.data.unverified_nav == set(codes)
        runtime.tick(NOW)
        assert fake.sent
        assert sum(args[5] * args[6] + cfg.CASH_RESERVE for args in fake.sent) <= 1000
        assert all(args[0] == 23 for args in fake.sent)
        output = capsys.readouterr().out
        assert '入选净值' in output and '原始preTradingDay=0' in output
        assert '原始tradingDay=0' in output and '未核验，按QMT返回值使用' in output
        assert '实单耗时' in output
        assert not Path(cfg.NAV_FILE).exists()
    finally:
        runtime.close()


def test_readonly_native_entry_needs_no_account_or_capital(mod, selection, monkeypatch):
    fake, original, cfg = selection
    original.close()
    monkeypatch.setattr(mod, 'STATE_DIR', cfg.STATE_DIR)
    monkeypatch.setattr(mod, 'INITIAL_CAPITAL', 0)
    monkeypatch.setattr(mod, 'ENABLE_TRADING', False)
    for name, fn in fake.namespace().items():
        monkeypatch.setattr(mod, name, fn, raising=False)
    mod.init(fake)
    try:
        assert isinstance(mod._runtime, mod.SelectionRuntime)
        mod.handlebar(fake)
        mod.on_timer(fake)
        mod.order_callback(fake, {})
        mod.deal_callback(fake, {})
        mod.orderError_callback(fake, {}, 'unused')
        assert mod._runtime.last_decision is not None and not fake.sent
    finally:
        mod.stop(fake)


def test_paper_native_entry_never_binds_reads_or_submits_an_account(mod, selection, monkeypatch, capsys):
    fake, original, cfg = selection
    original.close()
    monkeypatch.setattr(mod, 'STATE_DIR', cfg.STATE_DIR)
    monkeypatch.setattr(mod, 'PAPER_TRADING', True)
    monkeypatch.setattr(mod, 'ENABLE_TRADING', False)
    for name, fn in fake.namespace().items():
        monkeypatch.setattr(mod, name, fn, raising=False)
    mod.init(fake)
    try:
        runtime = mod._runtime
        assert runtime.paper.cash == 5000
        mod.on_timer(fake)
        assert len(runtime.paper.orders) == 3
        assert all(order['side'] == 'BUY' and order['filled'] for order in runtime.paper.orders)
        assert runtime.paper.cash >= 0
        assert runtime.last_timing['paper_order_count'] == 3
        assert runtime.last_timing['first_paper_order_at'] is not None
        assert runtime.last_timing['first_paper_order_ms'] >= runtime.last_timing['callback_to_decision_ms']
        mod.on_timer(fake)
        assert len(runtime.paper.orders) == 3
        mod.order_callback(fake, {'m_nVolume': 999999})
        mod.deal_callback(fake, {'m_nVolume': 999999})
        assert len(runtime.paper.orders) == 3
        output = capsys.readouterr().out
        for text in ('纸面下单', '纸面耗时', '定时触发延后=', '行情=', '选股=', '目标计算=',
                     '回调至首笔=', '不含柜台/网络/撮合耗时', '非真实成交'):
            assert text in output
        assert not fake.sent and not fake.canceled and not fake.history_queries
        assert list(Path(cfg.STATE_DIR).glob('paper_*.log'))
        assert not list(Path(cfg.STATE_DIR).rglob('*.json'))
    finally:
        mod.stop(fake)


def paper_account(mod):
    cfg = mod.Settings()
    data = mod.QmtData(FakeQmt(), {}, cfg, lambda text: None, allow_undated_nav=True)
    return mod.PaperAccount(data, cfg)


def paper_timing(mod):
    return {'started': mod.time.perf_counter(), 'first_paper_order_ms': None}


def test_paper_rotation_sells_old_before_buys_and_preserves_selected_shares(mod):
    paper = paper_account(mod)
    paper.cash = 1000
    paper.positions = {'510001.SH': {'qty': 1000, 'cost': 1000},
                       '510002.SH': {'qty': 1000, 'cost': 1000}}
    quotes = {code: dict(last_price=1., paused=False, high_limit=20)
              for code in ('510001.SH', '510002.SH', '510003.SH')}
    decision = mod.Decision({'510002.SH': .3, '510003.SH': .6},
                            {'510002.SH': 1., '510003.SH': 1.})
    paper.rebalance(decision, quotes, NOW, paper_timing(mod))
    assert [(order['side'], order['code'], order['quantity']) for order in paper.orders] == [
        ('SELL', '510002.SH', 100), ('SELL', '510001.SH', 1000), ('BUY', '510003.SH', 1800)]
    assert paper.positions['510002.SH']['qty'] == 900
    assert paper.cash == pytest.approx(300)
    assert sum(pos['qty'] for pos in paper.positions.values()) == 2700
    # Rebalance again: zeroed old positions aren't missing-market errors.
    paper.rebalance(decision, {code: quotes[code] for code in decision.weights}, NOW, paper_timing(mod))
    assert len(paper.orders) == 3


def test_paper_unfilled_sell_prevents_buy_and_no_fake_clearing(mod):
    paper = paper_account(mod)
    paper.positions = {'510001.SH': {'qty': 1000, 'cost': 1000}}
    quotes = {'510001.SH': dict(last_price=1., paused=True),
              '510002.SH': dict(last_price=1., paused=False)}
    paper.rebalance(mod.Decision({'510002.SH': .95}, {'510002.SH': 1.}),
                    quotes, NOW, paper_timing(mod))
    assert len(paper.orders) == 1 and paper.orders[0]['side'] == 'SELL'
    assert not paper.orders[0]['filled'] and paper.positions['510001.SH']['qty'] == 1000


def test_paper_buy_limit_uses_fixed_reference_and_does_not_assume_fill_above_limit(mod):
    paper = paper_account(mod)
    paper.rebalance(mod.Decision({'510300.SH': .95}, {'510300.SH': 1.}),
                    {'510300.SH': dict(last_price=1.1, paused=False)}, NOW, paper_timing(mod))
    assert len(paper.orders) == 1
    assert paper.orders[0]['limit_price'] == 1.002
    assert paper.orders[0]['filled'] is False and paper.cash == 5000
    assert paper.positions['510300.SH']['qty'] == 0


def test_paper_cash_shortage_sizes_by_limit_price_without_negative_cash(mod):
    paper = paper_account(mod)
    paper.cash = 1000
    paper.rebalance(mod.Decision({'510300.SH': 1.}, {'510300.SH': 1.}),
                    {'510300.SH': dict(last_price=1., paused=False)}, NOW, paper_timing(mod))
    assert paper.orders[0]['quantity'] == 900
    assert paper.cash == 100


def test_paper_empty_decision_clears_virtual_positions_only(mod):
    paper = paper_account(mod)
    paper.positions = {'510300.SH': {'qty': 100, 'cost': 100}}
    paper.rebalance(mod.Decision({}, {}),
                    {'510300.SH': dict(last_price=1., paused=False)}, NOW, paper_timing(mod))
    assert paper.cash == 5100 and paper.positions['510300.SH']['qty'] == 0
    assert len(paper.orders) == 1 and paper.orders[0]['side'] == 'SELL'


def test_paper_no_target_order_timing_is_explicitly_empty(mod):
    paper = paper_account(mod)
    timing = paper_timing(mod)
    assert paper.rebalance(mod.Decision({}, {}), {}, NOW, timing) == []
    assert timing['first_paper_order_ms'] is None and timing['paper_order_count'] == 0


def test_actual_1000_budget_and_opening_timing_with_fake_broker_only(mod, tmp_path, capsys):
    fake, cfg = FakeQmt(), mod.Settings()
    cfg.ACCOUNT_ID, cfg.STATE_DIR = 'mock-account', str(tmp_path)
    frame = pd.DataFrame({'unit_net_value': [1.2], 'money': [1e7]}, index=['510300.SH'])
    runtime = mod.create_runtime(fake, fake.namespace(), cfg, lambda *args: frame, mod.select, mod.risk_signal)
    try:
        assert isinstance(runtime, mod.Runtime) and runtime.executor.state['capital'] == 1000
        runtime.start('09:20', '09:30', ('10:30',), '14:55')
        runtime.tick(NOW)
        assert len(fake.sent) == 1
        assert fake.sent[0][6] == 900
        assert fake.sent[0][5] == 1.002
        assert fake.sent[0][6] * fake.sent[0][5] + cfg.CASH_RESERVE <= 1000
        assert runtime.last_timing['first_submit_at'] == '2026-09-11T09:30:00.000'
        assert runtime.last_timing['first_submit_ms'] >= 0
        order = legacy.latest(runtime.executor)
        runtime.on_report('order', legacy.row(order, m_nOrderStatus=50, m_nVolumeTraded=0,
                                             m_strInsertTime='09:30:00'))
        runtime.on_report('deal', legacy.row(order, m_strTradeID='mock-fill', m_nVolume=100,
                                            m_dTradePrice=1.0, m_dTradeAmount=100,
                                            m_strTradeTime='09:30:01'))
        output = capsys.readouterr().out
        import json
        traces = [json.loads(line.split('执行链路耗时 | ', 1)[1]) for line in output.splitlines()
                  if '执行链路耗时 | ' in line]
        opening = next(trace for trace in traces if trace['phase'] == 'native.timer')
        assert opening['stages']['qmt.api.passorder']['calls'] == 1
        assert opening['stages']['qmt.api.get_full_tick']['calls'] >= 1
        assert opening['stages']['local.ledger_save_json_fsync_replace']['calls'] >= 1
        assert opening['stages']['local.select_rank_weights']['calls'] == 1
        for text in ('实单耗时', '定时触发延后=', '定时查询=', '行情=', '选股=', '目标准备',
                     '首笔接口调用时间=', '接口调用时间:', '接口返回时间:', '接口耗时:',
                     '执行回报耗时', '来源=回调', '券商原始时间=09:30:01'):
            assert text in output
        assert '纸面下单' not in output
    finally:
        runtime.close()


def test_idle_afterhours_keeps_queries_but_does_not_flood_timing(mod, tmp_path, capsys):
    fake, cfg = FakeQmt(), mod.Settings()
    cfg.ACCOUNT_ID, cfg.STATE_DIR = 'mock-account', str(tmp_path)
    runtime = mod.create_runtime(fake, fake.namespace(), cfg, mod.prepare, mod.select, mod.risk_signal)
    try:
        runtime.start('09:20', '09:30', ('10:30',), '14:55')
        runtime.day, runtime.previous, runtime.is_trading_day = '20260911', '20260910', True
        capsys.readouterr()
        runtime.tick(NOW.replace(hour=20))
        assert '执行链路耗时' not in capsys.readouterr().out
        assert not fake.sent
    finally:
        runtime.close()


def test_report_timing_distinguishes_query_from_callback_and_deduplicates(env, mod):
    fake, ex, data, _, _ = env
    logs = []
    data.log = logs.append
    order = submit(ex)
    report = legacy.row(order, m_nOrderStatus=50, m_nVolumeTraded=0, m_strInsertTime='09:30:00')
    ex.report('order', report)
    ex.report('order', report)
    ex.report('order', report, source='回调')
    assert len(logs) == 2 and '来源=查询' in logs[0] and '来源=回调' in logs[1]
    assert all('提交至本次观察=' in line for line in logs)
    assert len(fake.sent) == 1


def test_recovered_order_does_not_invent_monotonic_latency(env):
    _, ex, data, _, _ = env
    order = submit(ex)
    ex.timings.clear()  # A restart cannot retain process-local perf_counter samples.
    logs = []
    data.log = logs.append
    ex.report('order', legacy.row(order, m_nOrderStatus=50, m_nVolumeTraded=0))
    assert '未知（重启前委托，无单调时钟采样）' in logs[0]


def test_latency_reporting_failure_does_not_change_fill_booking(env, monkeypatch):
    _, ex, _, _, _ = env
    order = submit(ex)

    def failed(*args):
        raise OSError('optional timing log failed')

    monkeypatch.setattr(ex, 'record_report_timing', failed)
    ex.report('deal', legacy.row(order, m_strTradeID='mock-fill', m_nVolume=100,
                                 m_dTradePrice=1., m_dTradeAmount=100))
    assert order['booked'] == 100 and ex.state['positions']['510300.SH']['qty'] == 100


def test_enabled_trading_default_still_uses_only_panel_backtest_account(bt, mod, monkeypatch):
    (fake, old_runtime, cfg), _ = bt
    old_runtime.close()
    assert mod.ENABLE_TRADING is True
    for name in ('BACKTEST_DATA_FILE', 'ACCOUNT_ID', 'STATE_DIR'):
        monkeypatch.setattr(mod, name, getattr(cfg, name))
    for name, fn in fake.namespace().items():
        monkeypatch.setattr(mod, name, fn, raising=False)
    mod.init(fake)
    try:
        assert isinstance(mod._runtime, mod.BacktestRuntime)
        mod.handlebar(fake)
        assert len(fake.orders) == 3 and sum(amount for _, amount in fake.orders) == pytest.approx(20000 * .95)
        assert not fake.sent and not fake.canceled
        assert not Path(cfg.STATE_DIR).exists()
    finally:
        mod.stop(fake)
