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
SCRIPT = REPO / 'strategies' / 'big_qmt' / 'good_etf' / 'etf_discount_open_qmt.py'
LEGACY = REPO / 'strategies' / 'qmt' / 'good_etf' / 'tests' / 'test_native.py'
spec = importlib.util.spec_from_file_location('native_contract_fixture', LEGACY)
legacy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(legacy)
FakeQmt, NOW = legacy.FakeQmt, legacy.NOW


class LegacyDataFrame(pd.DataFrame):
    @classmethod
    def from_dict(cls, data, orient='columns', dtype=None):
        # Deliberately matches the old QMT protocol: columns= must raise TypeError.
        return pd.DataFrame.from_dict(data, orient=orient, dtype=dtype)


class LegacyPandas:
    DataFrame = LegacyDataFrame
    __version__ = 'test-protocol-pandas-0.22.0'

    def __getattr__(self, name):
        return getattr(pd, name)


@pytest.fixture(params=['current_pandas', 'qmt_legacy_protocol'])
def mod(monkeypatch, request):
    module = types.ModuleType('independent_bigqmt_good_etf')
    monkeypatch.setitem(sys.modules, module.__name__, module)
    exec(compile(SCRIPT.read_bytes(), str(SCRIPT), 'exec'), module.__dict__)
    if request.param == 'qmt_legacy_protocol':
        module.pd = LegacyPandas()
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
    if case in ('test_selection_contract_multisecurity',
                'test_direct_parity_with_existing_joinquant_strategy'):
        # Compare the unchanged formula at the legacy strategy's three-name setting.
        # The default two-name selection is covered by the live/backtest tests below.
        monkeypatch.setattr(mod, 'MAX_HOLD_NUM', 3)
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
    assert SCRIPT.name == 'etf_discount_open_qmt.py'
    assert not (SCRIPT.parent / 'good_etf_qmt_native.py').exists()
    assert not (SCRIPT.parent / 'good_etf.py').exists()
    assert mod.STRATEGY_ID == 'good_etf_big_qmt'
    assert mod.STRATEGY_NAME == 'ETF开盘折价轮动_qmt'
    assert mod.MAX_HOLD_NUM == 2
    assert mod.INITIAL_CAPITAL == 1000
    assert mod.ENABLE_TRADING is True
    assert mod.PAPER_TRADING is False and mod.PAPER_INITIAL_CAPITAL == 5000
    assert mod.STATE_DIR == r'C:\qmt_good_etf_big_qmt_data'
    assert mod.ACCOUNT_ID == mod.FEISHU_WEBHOOK == ''
    with tokenize.open(SCRIPT) as stream:
        source = stream.read()
    tree = ast.parse(source, feature_version=(3, 6))
    names = {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    names |= {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
    assert not any(name.startswith(('bullet_trade', 'jqdata', 'xtquant', 'requests', 'dataclasses')) for name in names)
    assert not any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                   and n.func.id in ('passorder', 'cancel') for n in tree.body)


def test_legacy_protocol_reproduces_the_observed_client_error():
    with pytest.raises(TypeError, match='columns'):
        LegacyDataFrame.from_dict({'A': [1.]}, orient='index', columns=['value'])


@pytest.mark.parametrize('rows,columns', [
    ({'B': [3., 1., 9.], 'A': [2., 1., 8.]}, ['high_price', 'low_price', 'money']),
    ({'B': 3., 'A': 2.}, ['unit_net_value']),
    ({}, ['unit_net_value']),
])
def test_frame_builder_preserves_codes_values_and_explicit_column_order(mod, rows, columns):
    expected = (pd.DataFrame.from_dict(rows, orient='index', columns=columns)
                if rows else pd.DataFrame(columns=columns))
    pd.testing.assert_frame_equal(mod.frame_from_rows(rows, columns), expected)


def test_startup_self_check_only_uses_synthetic_memory_and_prints_version(mod, monkeypatch, capsys):
    monkeypatch.setattr(mod, 'open', lambda *a, **kw: pytest.fail('No file access'), raising=False)
    monkeypatch.setattr(mod, 'QmtData', lambda *a, **kw: pytest.fail('No QMT/account APIs'))
    monkeypatch.setattr(mod, 'Executor', lambda *a, **kw: pytest.fail('No ledger or order APIs'))
    mod.check_pandas_environment()
    text = capsys.readouterr().out
    assert '20261019_pandas_v1' in text and 'dataframe_check=PASS' in text
    assert 'python=' in text and 'pandas=' in text and 'synthetic only' in text


def test_startup_self_check_failure_precedes_context_or_ledger_access(mod, monkeypatch, tmp_path):
    def broken(*args):
        raise TypeError('synthetic client incompatibility')
    monkeypatch.setattr(mod, 'frame_from_rows', broken)
    monkeypatch.setattr(mod, 'Runtime', lambda *a: pytest.fail('No runtime after failed self-check'))
    config = mod.Settings()
    config.STATE_DIR = str(tmp_path / 'must-not-exist')
    with pytest.raises(RuntimeError, match='dataframe_check=FAIL'):
        mod.create_runtime(object(), {}, config, mod.prepare, mod.select, mod.risk_signal)
    assert not Path(config.STATE_DIR).exists()


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
        assert logs == []
        assert any('独立大QMT版' in line for _, line in runtime._log_buffer.rows)
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
    # Keep inherited three-name execution contracts at their original parameter.
    # Native two-name defaults are exercised by the QMT-first and entry tests.
    monkeypatch.setattr(mod, 'MAX_HOLD_NUM', 3)
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
    assert [code for code, _ in fake.orders] == ['510003.SH', '510002.SH']
    raw = [abs(price / 2. - 1) * 100 for price in (1., 1.5)]
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
    assert len(fake.orders) == 2


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
    assert len(fake.orders) == 2 and not runtime.closed


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
    assert list(result.weights) == ['510003.SH', '510002.SH']
    raw = [abs(price / 2. - 1) * 100 for price in (1., 1.5)]
    assert result.weights == {code: x / sum(raw) * .95 for code, x in zip(result.weights, raw)}
    assert '520590.SH' not in fake.market_calls[0][1]
    assert '510004.SH' not in runtime.frame.index  # Strict liquidity boundary unchanged.
    runtime.tick(NOW.replace(second=5))
    assert len(fake.sector_calls) == 1 and not fake.sent and not fake.canceled
    output = capsys.readouterr().out
    assert '净值日期=未核验' in output and '测试ETF' in output
    assert '单价=' in output and '单位净值=' in output and '折价率=' in output and '权重=' in output
    assert '前日成交额=' in output and '不用于交易' in output
    assert not list(Path(cfg.STATE_DIR).glob('*.log'))
    assert runtime._log_buffer.rows
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
        assert len(runtime.paper.orders) == 2
        assert all(order['side'] == 'BUY' and order['filled'] for order in runtime.paper.orders)
        assert runtime.paper.cash >= 0
        assert runtime.last_timing['paper_order_count'] == 2
        assert runtime.last_timing['first_paper_order_at'] is not None
        assert runtime.last_timing['first_paper_order_ms'] >= runtime.last_timing['callback_to_decision_ms']
        mod.on_timer(fake)
        assert len(runtime.paper.orders) == 2
        mod.order_callback(fake, {'m_nVolume': 999999})
        mod.deal_callback(fake, {'m_nVolume': 999999})
        assert len(runtime.paper.orders) == 2
        output = capsys.readouterr().out
        for text in ('纸面下单', '纸面耗时', '定时触发延后=', '行情=', '选股=', '目标计算=',
                     '回调至首笔=', '不含柜台/网络/撮合耗时', '非真实成交'):
            assert text in output
        assert not fake.sent and not fake.canceled and not fake.history_queries
        assert not list(Path(cfg.STATE_DIR).glob('*.log'))
        assert any('纸面下单' in line for _, line in runtime._log_buffer.rows)
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
        assert not list(tmp_path.glob('*.log'))
        stored = '\n'.join(line for _, line in runtime._log_buffer.rows)
        assert '下单请求' in stored and '成交回报' in stored
        assert all(prefix in stored for prefix in ('执行链路耗时 | ', '实单耗时 | ', '执行回报耗时 | '))
        ledger = json.loads(Path(runtime.executor.path).read_text(encoding='utf8'))
        assert ledger['positions']['510300.SH']['qty'] == 100
        assert len(ledger['fills']) == 1
    finally:
        runtime.close()


@pytest.mark.parametrize('runtime_name', ['Runtime', 'SelectionRuntime'])
@pytest.mark.parametrize('prefix', ['执行链路耗时', '实单耗时', '执行回报耗时', '纸面耗时'])
def test_native_timing_prints_without_open_mkdir_or_notifications(mod, tmp_path, monkeypatch, capsys, runtime_name, prefix):
    host = types.SimpleNamespace(settings=types.SimpleNamespace(STRATEGY_ID='test'),
                                 executor=types.SimpleNamespace(path=str(tmp_path / 'ledger.json')),
                                 paper=None)

    def forbidden(*args, **kwargs):
        pytest.fail('Timing output must not open files, make directories or send notifications')

    monkeypatch.setattr('builtins.open', forbidden)
    monkeypatch.setattr(mod.os, 'makedirs', forbidden)
    host.emit = forbidden
    getattr(mod, runtime_name).log(host, prefix + ' | test')
    assert prefix + ' | test' in capsys.readouterr().out


def test_native_timing_print_failure_is_best_effort(mod, monkeypatch):
    def failed(*args, **kwargs):
        raise OSError('QMT output unavailable')

    monkeypatch.setattr('builtins.print', failed)
    assert mod.print_timing_only('执行链路耗时 | test', 'test') is True
    assert mod.print_timing_only('成交回报 | test', 'test') is False


@pytest.mark.parametrize('runtime_name', ['Runtime', 'SelectionRuntime'])
def test_business_and_timing_logs_are_memory_only_during_trading(mod, tmp_path, monkeypatch, runtime_name):
    host = types.SimpleNamespace(settings=types.SimpleNamespace(STRATEGY_ID='test'),
                                 executor=types.SimpleNamespace(path=str(tmp_path/'ledger.json')),
                                 paper=None)
    def forbidden(*a, **kw):
        pytest.fail('Trading-period logs must not open/mkdir files')
    with monkeypatch.context() as patch:
        patch.setattr('builtins.open', forbidden)
        patch.setattr(mod.os, 'makedirs', forbidden)
        getattr(mod, runtime_name).log(host, '成交回报 | normal')
        getattr(mod, runtime_name).log(host, '执行链路耗时 | normal')
        mod.maybe_archive_native_logs(host)
    assert len(host._log_buffer.rows) == 2 and not list(tmp_path.iterdir())


@pytest.fixture
def native_archive_workers(mod, monkeypatch):
    workers = []

    class Worker:
        def __init__(self, target, args, daemon):
            assert daemon is True
            self.target, self.args = target, args

        def start(self):
            workers.append(self)

        def run(self):
            self.target(*self.args)

    monkeypatch.setattr(mod.threading, 'Thread', Worker)
    return workers


@pytest.mark.parametrize('hour', [0, 6, 9, 10, 12, 14, 16, 20])
def test_native_archive_each_scheduled_point_once_in_worker(mod, tmp_path, native_archive_workers, hour):
    cfg = mod.Settings()
    assert cfg.LOG_ARCHIVE_TIMES == ('00:00', '06:00', '09:00', '10:00', '12:00', '14:00', '16:00', '20:00')
    buffer = mod.NativeLogBuffer(4096)
    due = NOW.replace(hour=hour, minute=0, second=0)
    before = due - dt.timedelta(seconds=30)
    path = str(tmp_path/'ledger.json')
    buffer.append('NAV original report', before)
    assert not buffer.request_flush(path, before, cfg.LOG_ARCHIVE_TIMES)
    assert buffer.request_flush(path, due + dt.timedelta(seconds=7), cfg.LOG_ARCHIVE_TIMES)
    assert not buffer.request_flush(path, due + dt.timedelta(seconds=8), cfg.LOG_ARCHIVE_TIMES)
    assert not list(tmp_path.iterdir())  # The QMT callback starts a worker, never writes a log.
    assert len(native_archive_workers) == 1
    native_archive_workers.pop().run()
    saved = tmp_path/('ledger.json.' + before.strftime('%Y%m%d') + '.log')
    assert saved.read_text(encoding='utf8') == 'NAV original report\n'
    buffer.append('later report', due + dt.timedelta(seconds=20))
    assert not buffer.request_flush(path, due + dt.timedelta(seconds=30), cfg.LOG_ARCHIVE_TIMES)
    assert not buffer.request_flush(path, due + dt.timedelta(minutes=15), cfg.LOG_ARCHIVE_TIMES)
    assert len(buffer.rows) == 1 and not native_archive_workers


@pytest.mark.parametrize('hour', [0, 6, 9, 10, 12, 14, 16, 20])
def test_native_archive_start_in_scheduled_minute(mod, tmp_path, native_archive_workers, hour):
    now = NOW.replace(hour=hour, minute=0, second=40)
    buffer = mod.NativeLogBuffer(4096)
    buffer.append('startup report', now)
    assert buffer.request_flush(str(tmp_path/'ledger.json'), now, mod.LOG_ARCHIVE_TIMES)
    native_archive_workers.pop().run()
    assert not buffer.rows


def test_native_archive_midinterval_restart_waits_but_delayed_callback_catches_up(mod, tmp_path, native_archive_workers):
    buffer = mod.NativeLogBuffer(4096)
    path = str(tmp_path/'ledger.json')
    buffer.append('opening report', NOW)
    assert not buffer.request_flush(path, NOW, mod.LOG_ARCHIVE_TIMES)
    assert not buffer.request_flush(path, NOW.replace(minute=59), mod.LOG_ARCHIVE_TIMES)
    # An already running process delayed over several points saves one batch, not one per missed point.
    assert buffer.request_flush(path, NOW.replace(hour=12, minute=4), mod.LOG_ARCHIVE_TIMES)
    assert len(native_archive_workers) == 1
    native_archive_workers.pop().run()
    assert buffer.archive_slot == '20260911:12:00'
    assert not buffer.request_flush(path, NOW.replace(hour=12, minute=5), mod.LOG_ARCHIVE_TIMES)


def test_native_archive_empty_point_does_not_flush_opening_logs_later(mod, tmp_path, native_archive_workers):
    buffer = mod.NativeLogBuffer(4096)
    path = str(tmp_path/'ledger.json')
    assert not buffer.request_flush(path, NOW.replace(hour=8, minute=59), mod.LOG_ARCHIVE_TIMES)
    assert not buffer.request_flush(path, NOW.replace(minute=0), mod.LOG_ARCHIVE_TIMES)
    buffer.append('09:30 selection NAV', NOW)
    assert not buffer.request_flush(path, NOW, mod.LOG_ARCHIVE_TIMES)
    assert buffer.request_flush(path, NOW.replace(hour=10, minute=0), mod.LOG_ARCHIVE_TIMES)
    native_archive_workers.pop().run()
    assert (tmp_path/'ledger.json.20260911.log').read_text(encoding='utf8') == '09:30 selection NAV\n'


def test_native_archive_bounded_buffer_retry_and_new_logs_preserved(mod, tmp_path, monkeypatch, native_archive_workers):
    buffer = mod.NativeLogBuffer(1024)
    for index in range(30):
        buffer.append('x'*100 + str(index), NOW)
    assert buffer.chars <= 1024 and buffer.dropped > 0
    path, due = str(tmp_path/'ledger.json'), NOW.replace(hour=10, minute=0)
    mono = [100.]
    monkeypatch.setattr(mod.time, 'monotonic', lambda: mono[0])
    assert not buffer.request_flush(path, NOW, mod.LOG_ARCHIVE_TIMES)
    assert buffer.request_flush(path, due, mod.LOG_ARCHIVE_TIMES)
    with monkeypatch.context() as patch:
        patch.setattr(mod, 'open', lambda *a, **k: (_ for _ in ()).throw(OSError('disk unavailable')), raising=False)
        native_archive_workers.pop().run()
    assert buffer.rows and not buffer.writing and buffer.chars <= 1024
    mono[0] += 59
    assert not buffer.request_flush(path, due + dt.timedelta(seconds=59), mod.LOG_ARCHIVE_TIMES)
    mono[0] += 1
    assert buffer.request_flush(path, due + dt.timedelta(minutes=1), mod.LOG_ARCHIVE_TIMES)
    # Appended while the worker is writing: do not erase, and do not launch a duplicate worker.
    original_open = open

    def append_during_write(*args, **kwargs):
        buffer.append('concurrent report', due)
        return original_open(*args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(mod, 'open', append_during_write, raising=False)
        native_archive_workers.pop().run()
    saved = (tmp_path/'ledger.json.20260911.log').read_text(encoding='utf8')
    assert 'log_archive_gap' in saved and 'x'*100 in saved
    assert [line for _, line in buffer.rows] == ['concurrent report'] and not buffer.writing
    assert buffer.archive_slot == '20260911:10:00'
    assert not buffer.request_flush(path, due + dt.timedelta(minutes=2), mod.LOG_ARCHIVE_TIMES)


def test_native_archive_next_day_and_original_log_dates(mod, tmp_path, monkeypatch, native_archive_workers):
    buffer = mod.NativeLogBuffer(4096)
    path = str(tmp_path/'ledger.json')
    before = NOW.replace(hour=23, minute=59)
    midnight = (before + dt.timedelta(days=1)).replace(hour=0, minute=0)
    buffer.append('previous day NAV', before)
    assert not buffer.request_flush(path, before, mod.LOG_ARCHIVE_TIMES)
    buffer.append('new day report', midnight)
    assert buffer.request_flush(path, midnight, mod.LOG_ARCHIVE_TIMES)
    native_archive_workers.pop().run()
    assert (tmp_path/'ledger.json.20260911.log').read_text(encoding='utf8') == 'previous day NAV\n'
    assert (tmp_path/'ledger.json.20260912.log').read_text(encoding='utf8') == 'new day report\n'
    assert buffer.archive_slot == '20260912:00:00'
    mono = mod.time.monotonic() + 3600
    monkeypatch.setattr(mod.time, 'monotonic', lambda: mono)
    buffer.append('morning NAV', midnight.replace(hour=6))
    assert buffer.request_flush(path, midnight.replace(hour=6), mod.LOG_ARCHIVE_TIMES)
    native_archive_workers.pop().run()
    assert buffer.archive_slot == '20260912:06:00'


def test_native_archive_thread_start_failure_keeps_pending_logs(mod, tmp_path, monkeypatch, native_archive_workers):
    buffer = mod.NativeLogBuffer(4096)
    path, due = str(tmp_path/'ledger.json'), NOW.replace(hour=10, minute=0)
    mono = [100.]
    monkeypatch.setattr(mod.time, 'monotonic', lambda: mono[0])
    buffer.append('NAV original report', NOW)
    assert not buffer.request_flush(path, NOW, mod.LOG_ARCHIVE_TIMES)
    original_thread = mod.threading.Thread

    def failed_thread(*args, **kwargs):
        raise RuntimeError('cannot start worker')

    with monkeypatch.context() as patch:
        patch.setattr(mod.threading, 'Thread', failed_thread)
        assert not buffer.request_flush(path, due, mod.LOG_ARCHIVE_TIMES)
    assert buffer.rows and not buffer.writing and not native_archive_workers
    assert mod.threading.Thread is original_thread
    mono[0] += 60
    assert buffer.request_flush(path, due + dt.timedelta(minutes=1), mod.LOG_ARCHIVE_TIMES)
    native_archive_workers.pop().run()
    assert not buffer.rows and buffer.archive_slot == '20260911:10:00'


def test_native_archive_gap_only_failure_is_retryable(mod, tmp_path, monkeypatch, native_archive_workers):
    buffer = mod.NativeLogBuffer(1024)
    path, due = str(tmp_path/'ledger.json'), NOW.replace(hour=10, minute=0)
    mono = [100.]
    monkeypatch.setattr(mod.time, 'monotonic', lambda: mono[0])
    buffer.append('x'*2048, NOW)
    assert not buffer.rows and buffer.dropped == 1
    assert not buffer.request_flush(path, NOW, mod.LOG_ARCHIVE_TIMES)
    assert buffer.request_flush(path, due, mod.LOG_ARCHIVE_TIMES)
    with monkeypatch.context() as patch:
        patch.setattr(mod, 'open', lambda *a, **k: (_ for _ in ()).throw(OSError('disk unavailable')), raising=False)
        native_archive_workers.pop().run()
    assert not buffer.rows and buffer.dropped == 1 and buffer.archive_slot == '20260911:09:00'
    mono[0] += 60
    assert buffer.request_flush(path, due + dt.timedelta(minutes=1), mod.LOG_ARCHIVE_TIMES)
    native_archive_workers.pop().run()
    assert (tmp_path/'ledger.json.20260911.log').read_text(encoding='utf8') == '[log_archive_gap] dropped_records=1\n'
    assert buffer.dropped == 0 and buffer.archive_slot == '20260911:10:00'


def test_native_archive_inflight_write_does_not_consume_later_empty_point(mod, tmp_path, monkeypatch, native_archive_workers):
    buffer = mod.NativeLogBuffer(4096)
    path, due = str(tmp_path/'ledger.json'), NOW.replace(hour=10, minute=0)
    mono = [100.]
    monkeypatch.setattr(mod.time, 'monotonic', lambda: mono[0])
    buffer.append('original report', NOW)
    assert not buffer.request_flush(path, NOW, mod.LOG_ARCHIVE_TIMES)
    assert buffer.request_flush(path, due, mod.LOG_ARCHIVE_TIMES)
    original_open = open

    def later_callback_during_write(*args, **kwargs):
        # Worker already drained the batch. Do not advance a later slot while it is still writing.
        assert not buffer.rows
        assert not buffer.request_flush(path, due.replace(hour=12), mod.LOG_ARCHIVE_TIMES)
        assert buffer.archive_slot == '20260911:09:00'
        buffer.append('new report', due.replace(hour=12))
        return original_open(*args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(mod, 'open', later_callback_during_write, raising=False)
        native_archive_workers.pop().run()
    mono[0] += 7200
    assert buffer.archive_slot == '20260911:10:00'
    assert buffer.request_flush(path, due.replace(hour=12), mod.LOG_ARCHIVE_TIMES)
    native_archive_workers.pop().run()
    assert (tmp_path/'ledger.json.20260911.log').read_text(encoding='utf8') == 'original report\nnew report\n'
    assert not buffer.rows and buffer.archive_slot == '20260911:12:00'


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


@pytest.mark.parametrize('index_kind', ['text', 'timestamp', 'numpy_ms', 'text_ms', 'numpy_seconds'])
def test_daily_date_accepts_real_qmt_pandas_indexes_without_future_data(mod, index_kind):
    import numpy as np
    day = pd.Timestamp('2026-09-10', tz='Asia/Shanghai')
    index = {'text': '20260910', 'timestamp': day,
             'numpy_ms': np.int64(day.timestamp() * 1000),
             'text_ms': str(int(day.timestamp() * 1000)),
             'numpy_seconds': np.int64(day.timestamp())}[index_kind]
    frame = pd.DataFrame({'high': [2.], 'low': [1.], 'amount': [1e7]}, index=[index])
    future = frame.copy()
    future.index = ['20260911']
    rows, missing, samples = mod.QmtData.daily_rows({'GOOD': frame, 'FUTURE': future}, ['GOOD', 'FUTURE'], '20260910')
    assert rows == {'GOOD': [2., 1., 1e7]} and missing == ['FUTURE']
    assert samples[0][1] == 'wrong_day:20260911'


def warmup_data(mod, monkeypatch, count=3):
    now = NOW.replace(hour=9, minute=10)
    monkeypatch.setattr(mod, 'china_now', lambda: now)
    cfg = mod.Settings()
    cfg.DAILY_DOWNLOAD_BATCH, cfg.DAILY_DOWNLOAD_BUDGET_MS = 2, 100000
    codes = ['51000{}.SH'.format(i) for i in range(count)]
    cached, downloads, reads = {}, [], []
    frame = pd.DataFrame({'high': [2.], 'low': [1.], 'amount': [1e7]}, index=['20260910'])

    def read(fields, requested, **kwargs):
        assert kwargs == dict(period='1d', end_time='20260910235959', count=1,
                              dividend_type='none', fill_data=False, subscribe=False)
        reads.append(list(requested))
        return {code: cached[code] for code in requested if code in cached}

    def download(code, period, start, end):
        assert (period, start, end) == ('1d', '20260910', '20260910')
        downloads.append(code)
        cached[code] = frame

    context = types.SimpleNamespace(get_stock_list_in_sector=lambda _: codes, get_market_data_ex=read)
    data = mod.QmtData(context, {'download_history_data': download}, cfg, lambda _: None)
    return now, cfg, data, cached, downloads, reads, frame, codes


def test_daily_warmup_is_batched_missing_only_and_never_downloads_at_open(mod, monkeypatch):
    now, cfg, data, cached, downloads, reads, frame, codes = warmup_data(mod, monkeypatch, 4)
    cached[codes[0]] = frame
    assert data.warm_daily_cache('20260910', now) is True
    assert downloads == codes[1:3]
    assert data.warm_daily_cache('20260910', now) is False
    assert downloads == codes[1:]
    prior = (list(downloads), list(reads))
    assert data.warm_daily_cache('20260910', now) is False
    assert (downloads, reads) == prior
    assert data.warm_daily_cache('20260910', NOW) is False
    assert (downloads, reads) == prior


def test_missing_daily_at_open_has_no_automatic_download_and_reports_raw_date(mod, monkeypatch):
    now, _, data, _, downloads, _, _, _ = warmup_data(mod, monkeypatch)
    logs = []
    data.log = logs.append
    assert data.warm_daily_cache('20260910', NOW) is False
    with pytest.raises(mod.DailyDataUnavailable):
        data.daily(['510001.SH'], '20260910')
    assert not downloads and 'no_result' in logs[0]
    assert 'previous=20260910' in logs[0]
    with pytest.raises(mod.DailyDataUnavailable):
        data.daily(['510001.SH'], '20260910')
    assert len(logs) == 1


def test_slow_daily_download_crossing_cutoff_stops_next_native_call(mod, monkeypatch):
    now, cfg, data, _, downloads, _, _, _ = warmup_data(mod, monkeypatch)
    clocks = iter([now, now.replace(minute=25)])
    monkeypatch.setattr(mod, 'china_now', lambda: next(clocks))
    assert data.warm_daily_cache('20260910', now) is False
    assert len(downloads) == 1


def test_warmup_failure_never_uses_stale_daily_or_resends_download(mod, monkeypatch):
    now, cfg, data, _, downloads, _, _, codes = warmup_data(mod, monkeypatch, 1)
    def fail(*args):
        downloads.append(args[0])
        raise RuntimeError('terminal data download unavailable')
    data.namespace['download_history_data'] = fail
    assert data.warm_daily_cache('20260910', now) is True
    assert data.warm_daily_cache('20260910', now) is True
    assert downloads == codes
    with pytest.raises(mod.DailyDataUnavailable):
        data.daily(codes, '20260910')


def test_partial_daily_cache_does_not_wait_for_missing_symbols_past_prepare_time(mod, monkeypatch):
    now, cfg, data, cached, downloads, reads, frame, codes = warmup_data(mod, monkeypatch, 3)
    logs, mono = [], [1000.]
    data.log = logs.append
    cfg.DAILY_DOWNLOAD_BATCH = 3
    monkeypatch.setattr(mod.time, 'monotonic', lambda: mono[0])
    def download(code, *args):
        downloads.append(code)
        if code != codes[-1]:
            cached[code] = frame
    data.namespace['download_history_data'] = download
    assert data.warm_daily_cache('20260910', now) is True
    mono[0] += 600
    assert data.warm_daily_cache('20260910', now.replace(minute=20)) is False
    assert downloads == codes and data.daily(codes, '20260910').index.tolist() == codes[:-1]
    assert 'missing=1' in logs[-2] and 'original exclusion policy' in logs[-2]
    assert not any('verified;' in line for line in logs)
    previous = (list(downloads), list(reads))
    assert data.warm_daily_cache('20260910', now.replace(minute=21)) is False
    assert (downloads, reads) == previous


@pytest.mark.parametrize('trading', [False, True])
def test_preparation_type_error_is_not_retried_at_every_timer_or_open(mod, monkeypatch, tmp_path, trading):
    fake, cfg, calls = FakeQmt(), mod.Settings(), []
    cfg.ACCOUNT_ID, cfg.STATE_DIR, cfg.ENABLE_TRADING = 'test-account', str(tmp_path), trading
    def broken(*args):
        calls.append(True)
        raise TypeError("from_dict() got an unexpected keyword argument 'columns'")
    runtime = mod.create_runtime(fake, fake.namespace(), cfg, broken, mod.select, mod.risk_signal)
    risks = []
    try:
        runtime.start('09:20', '09:30', ('10:30',), '14:55')
        for minute in (25, 25, 26, 27, 29, 30, 30):
            runtime.tick(NOW.replace(minute=minute))
        assert len(calls) == 1 and runtime.preparation.fatal
        assert not fake.sent and not fake.canceled and runtime.frame is None
        if trading:
            runtime.executor.risk = lambda *args: risks.append(True)
            runtime.tick(NOW.replace(hour=10, minute=30))
            assert risks == [True]  # A preparation failure must not disable existing-position risk handling.
            assert not runtime.executor.state['targets']
    finally:
        runtime.close()


@pytest.mark.parametrize('trading', [False, True])
def test_temporary_preparation_error_retries_after_interval_and_open_submits_once(mod, monkeypatch, tmp_path, trading):
    fake, cfg, calls, mono = FakeQmt(), mod.Settings(), [], [1000.]
    cfg.ACCOUNT_ID, cfg.STATE_DIR, cfg.ENABLE_TRADING = 'test-account', str(tmp_path), trading
    monkeypatch.setattr(mod.time, 'monotonic', lambda: mono[0])
    def prepare(*args):
        calls.append(True)
        if len(calls) == 1:
            raise mod.DailyDataUnavailable('temporary cache miss')
        return pd.DataFrame({'unit_net_value': [2., 2.], 'money': [1e7, 1e7]},
                            index=['510001.SH', '510002.SH'])
    runtime = mod.create_runtime(fake, fake.namespace(), cfg, prepare, mod.select, mod.risk_signal)
    try:
        runtime.start('09:20', '09:30', ('10:30',), '14:55')
        runtime.tick(NOW.replace(minute=20))
        for second in range(1, 20):
            mono[0] = 1000. + second
            runtime.tick(NOW.replace(minute=20, second=second))
        assert len(calls) == 1 and not fake.sent
        mono[0] = 1000. + cfg.PREPARE_RETRY_SECONDS
        runtime.tick(NOW.replace(minute=21))
        assert len(calls) == 2 and not fake.sent
        runtime.tick(NOW)
        runtime.tick(NOW.replace(second=5))
        assert len(calls) == 2
        if trading:
            assert len(fake.sent) == 2 and all(call[4:6] == (11, 1.002) for call in fake.sent)
        else:
            assert not fake.sent and len(runtime.last_decision.weights) == 2
    finally:
        runtime.close()


def test_preparation_guard_resets_only_on_new_day_and_does_not_carry_late_plan(mod, monkeypatch):
    guard = mod.PreparationGuard(mod.Settings())
    assert guard.ready('20261019')
    guard.failed(TypeError('incompatible call'))
    assert not guard.ready('20261019')
    assert guard.ready('20261020') and not guard.fatal


@pytest.mark.parametrize('trading', [False, True])
def test_warmup_api_type_error_is_also_bounded_before_prepare_minute(mod, monkeypatch, tmp_path, trading):
    fake, cfg, calls = FakeQmt(), mod.Settings(), []
    cfg.ACCOUNT_ID, cfg.STATE_DIR, cfg.ENABLE_TRADING = 'test-account', str(tmp_path), trading
    runtime = mod.create_runtime(fake, fake.namespace(), cfg, mod.prepare, mod.select, mod.risk_signal)
    def broken(*args):
        calls.append(True)
        raise TypeError('unsupported native daily query signature')
    runtime.data.warm_daily_cache = broken
    try:
        runtime.start('09:20', '09:30', ('10:30',), '14:55')
        for second in range(10):
            runtime.tick(NOW.replace(minute=10, second=second))
        assert calls == [True] and runtime.preparation.fatal
        assert not fake.sent and not fake.canceled
    finally:
        runtime.close()


@pytest.mark.parametrize('trading', [False, True])
def test_recovered_data_does_not_create_a_missed_opening_plan(mod, monkeypatch, tmp_path, trading):
    fake, cfg, calls, mono = FakeQmt(), mod.Settings(), [], [1000.]
    cfg.ACCOUNT_ID, cfg.STATE_DIR, cfg.ENABLE_TRADING = 'test-account', str(tmp_path), trading
    monkeypatch.setattr(mod.time, 'monotonic', lambda: mono[0])
    def prepare(*args):
        calls.append(True)
        if len(calls) == 1:
            raise mod.DailyDataUnavailable('data not ready at open')
        return pd.DataFrame({'unit_net_value': [2.]}, index=['510001.SH'])
    runtime = mod.create_runtime(fake, fake.namespace(), cfg, prepare, mod.select, mod.risk_signal)
    try:
        runtime.start('09:20', '09:30', ('10:30',), '14:55')
        runtime.tick(NOW)
        mono[0] += 120
        runtime.tick(NOW.replace(minute=31))
        assert len(calls) == 1 and not fake.sent and runtime.frame is None
    finally:
        runtime.close()


def test_preparation_failure_never_prevents_confirmed_fill_booking(mod, tmp_path):
    fake, cfg = FakeQmt(), mod.Settings()
    cfg.ACCOUNT_ID, cfg.STATE_DIR = 'mock-account', str(tmp_path)
    runtime = mod.create_runtime(fake, fake.namespace(), cfg, mod.prepare, mod.select, mod.risk_signal)
    try:
        ex = runtime.executor
        order = submit(ex)
        assert runtime.preparation.ready('20260911')
        runtime.preparation.failed(TypeError('preparation only is blocked'))
        runtime.on_report('deal', legacy.row(order, m_strTradeID='confirmed-fill', m_nVolume=100,
                                            m_dTradePrice=1.002, m_dTradeAmount=100.2))
        assert order['booked'] == 100 and len(ex.state['fills']) == 1
        assert ex.state['positions']['510300.SH']['qty'] == 100
        assert runtime.preparation.fatal
    finally:
        runtime.close()


@pytest.mark.parametrize('trading', [False, True])
def test_preopen_cache_repair_finishes_before_decision_and_keeps_order_policy(mod, monkeypatch, tmp_path, trading):
    now, cfg, data, cached, downloads, reads, _, codes = warmup_data(mod, monkeypatch)
    fake = FakeQmt()
    fake.get_stock_list_in_sector = data.c.get_stock_list_in_sector
    fake.get_market_data_ex = data.c.get_market_data_ex
    fake.pcf = {code: dict(nav=2., preTradingDay='20260910', tradingDay='20260911') for code in codes}
    cfg.ENABLE_TRADING, cfg.PAPER_TRADING = trading, False
    cfg.ACCOUNT_ID, cfg.STATE_DIR = 'test-account', str(tmp_path)
    namespace = dict(fake.namespace(), **data.namespace)
    clock = [now.replace(minute=20)]
    monkeypatch.setattr(mod, 'china_now', lambda: clock[0])
    runtime = mod.create_runtime(fake, namespace, cfg, mod.prepare, mod.select, mod.risk_signal)
    try:
        runtime.start('09:20', '09:30', ('10:30',), '14:55')
        runtime.tick(clock[0])
        assert len(downloads) == 2 and not fake.sent
        assert runtime.frame is None
        clock[0] = now.replace(minute=21)
        runtime.tick(clock[0])
        assert downloads == codes and runtime.frame.index.tolist() == codes
        assert not fake.sent  # Cache preparation never creates a trading order.
        prior = (list(downloads), list(reads))
        clock[0] = NOW
        runtime.tick(clock[0])
        assert (downloads, reads) == prior  # No history downloads/reads on the hot path.
        if trading:
            assert len(fake.sent) == 2
            assert all(call[4] == 11 and call[5] == 1.002 for call in fake.sent)
            assert all(target['day'] == '20260911' for target in runtime.executor.state['targets'].values())
        else:
            assert list(runtime.last_decision.weights) == codes[:2] and not fake.sent
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
        assert len(fake.orders) == 2 and sum(amount for _, amount in fake.orders) == pytest.approx(20000 * .95)
        assert not fake.sent and not fake.canceled
        assert not Path(cfg.STATE_DIR).exists()
    finally:
        mod.stop(fake)


@pytest.mark.parametrize('form', [
    'datetime', 'aware_utc', 'seconds', 'milliseconds', 'seconds_text', 'milliseconds_text',
    'numpy_seconds', 'numpy_milliseconds', 'compact', 'compact_millis', 'space', 'iso',
])
def test_quote_clock_formats_are_explicitly_beijing_not_host_timezone(mod, form):
    import numpy as np
    zone = dt.timezone(dt.timedelta(hours=8))
    expected = NOW.replace(tzinfo=zone)
    seconds = int(expected.timestamp())
    raw = dict(datetime=NOW, aware_utc=expected.astimezone(dt.timezone.utc),
               seconds=seconds, milliseconds=seconds * 1000,
               seconds_text=str(seconds), milliseconds_text=str(seconds * 1000),
               numpy_seconds=np.int64(seconds), numpy_milliseconds=np.int64(seconds * 1000),
               compact='20260911093000', compact_millis='20260911093000000',
               space='20260911 09:30:00', iso='2026-09-11T09:30:00')[form]
    assert mod.quote_datetime(raw) == expected


@pytest.mark.parametrize('raw', [None, '', 0, 'garbage', float('nan'), float('inf'),
                                 '20269999093000', '093000', pd.NaT])
def test_unknown_quote_time_is_not_assumed_current(mod, raw):
    assert mod.quote_datetime(raw) is None


def test_quote_batch_reasons_and_stale_suspended_zero_price_are_distinct(env):
    fake, _, data, cfg, _ = env
    stamps = ['20260911 09:29:59', '20260910 09:30:00', 'bad',
              '20260911 09:31:00', '20260911 09:30:00', '20260910 15:00:00']
    codes = ['51000{}.SH'.format(i) for i in range(6)]
    fake.ticks = {code: dict(lastPrice=0. if i in (4, 5) else 1., timetag=stamp,
                             openInt=17 if i == 5 else 13)
                  for i, (code, stamp) in enumerate(zip(codes, stamps))}
    batch = data.quote_batch(codes, NOW, allow_suspended=True)
    assert [issue.reason for issue in batch.issues] == [
        'before_open', 'wrong_day', 'invalid_time', 'future_time', 'invalid_price']
    assert batch.rows == {codes[5]: dict(last_price=0., paused=True, high_limit=20.)}
    assert batch.paused == 1
    assert all(issue.time_type == 'str' and issue.raw_time for issue in batch.issues)
    # Risk/execution reads keep strict positive-current-price checks, even when suspended.
    assert len(data.quote_batch(codes, NOW).issues) == 6
    # Only confirmed suspension is exempt, not a missing security or zero price.
    fake.ticks[codes[5]]['openInt'] = 13
    assert len(data.quote_batch(codes, NOW).issues) == 6


def test_opening_gate_pending_is_aggregate_bounded_and_non_blocking(env, mod, monkeypatch):
    fake, _, data, _, _ = env
    logs, mono = [], [100.]
    data.log = logs.append
    monkeypatch.setattr(mod.time, 'monotonic', lambda: mono[0])
    monkeypatch.setattr(mod.time, 'sleep', lambda *a: pytest.fail('Never sleep on trading thread'))
    codes = ['51{:04d}.SH'.format(i) for i in range(405)]
    fake.ticks = {code: dict(lastPrice=1., timetag='20260911 09:29:59', openInt=13) for code in codes[7:]}
    assert data.opening_quotes(codes, NOW, '09:30') is None
    assert len(logs) == 1 and 'candidates=405 ready=7 paused=0 pending=398' in logs[0]
    assert logs[0].count("'security':") == 5 and "'before_open': 398" in logs[0]
    mono[0] += 1
    assert data.opening_quotes(codes, NOW.replace(second=1), '09:30') is None
    assert len(logs) == 1
    mono[0] += 1
    assert data.opening_quotes(codes, NOW.replace(second=2), '09:30') is None
    assert len(logs) == 2
    assert all('parsed_time' in line and 'raw_price' in line for line in logs)


def readiness_runtime(mod, tmp_path, trading, codes):
    fake, cfg, decisions = FakeQmt(), mod.Settings(), []
    cfg.ACCOUNT_ID, cfg.STATE_DIR = 'mock-account', str(tmp_path)
    cfg.ENABLE_TRADING, cfg.PAPER_TRADING, cfg.AUTO_DOWNLOAD_DAILY = trading, False, False
    frame = pd.DataFrame({'unit_net_value': [2.] * len(codes), 'money': [1e7] * len(codes)}, index=codes)

    def select(frame, quotes):
        decision = mod.select(frame, quotes)
        decisions.append(decision)
        return decision

    runtime = mod.create_runtime(fake, fake.namespace(), cfg, lambda *a: frame.copy(), select, mod.risk_signal)
    runtime.start('09:20', '09:30', ('10:30',), '14:55')
    if trading:
        # Isolate planning decisions from broker tests, which already exercise actual fake submits.
        runtime.executor.rebalance = lambda *a: None
        runtime.executor.advance = lambda *a: None
    return fake, runtime, cfg, decisions


@pytest.mark.parametrize('trading', [False, True])
def test_full_405_pool_preserved_until_398_quotes_recover_exact_top2_once(mod, tmp_path, trading):
    codes = ['51{:04d}.SH'.format(i) for i in range(405)]
    fake, runtime, _, decisions = readiness_runtime(mod, tmp_path, trading, codes)
    warmed, reads = [], []
    fake.subscribe_whole_quote = lambda requested: warmed.append(requested) or 23
    fake.unsubscribe_quote = lambda sub: warmed.append(sub)
    original = fake.get_full_tick
    fake.get_full_tick = lambda requested: reads.append(list(requested)) or original(requested)
    try:
        runtime.tick(NOW.replace(minute=20))
        assert warmed == [codes] and reads == []  # Subscription is prepared before the hot path.
        for code in codes[7:]:
            fake.ticks[code] = dict(lastPrice=.5 if code == codes[-1] else .7,
                                    timetag='20260911 09:29:59', openInt=13)
        runtime.tick(NOW)
        jobs = runtime.executor.state['jobs'] if trading else runtime.jobs
        assert not decisions and '20260911:09:30' not in jobs
        assert runtime.frame.index.tolist() == codes and not fake.sent and not fake.canceled
        assert len(reads) == 1 and reads[0] == codes
        for code in codes[7:]:
            fake.ticks[code]['timetag'] = '20260911 09:30:01'
        # Avoid ties so exact weights and top-two order are deterministic on old/new pandas.
        fake.ticks[codes[-2]]['lastPrice'] = .6
        runtime.tick(NOW.replace(second=1))
        assert len(decisions) == 1 and list(decisions[0].weights) == codes[-1:-3:-1]
        assert decisions[0].weights == {codes[-1]: 75. / 145. * .95, codes[-2]: 70. / 145. * .95}
        assert '20260911:09:30' in jobs and len(reads) == 2
        runtime.tick(NOW.replace(second=2))
        assert len(decisions) == 1 and len(reads) == 2
    finally:
        runtime.close()
        runtime.close()
    assert warmed == [codes, 23]


@pytest.mark.parametrize('trading', [False, True])
def test_opening_timeout_preserves_holdings_no_partial_or_late_plan_and_restart(mod, tmp_path, trading, capsys):
    codes = ['510001.SH', '510002.SH']
    fake, runtime, cfg, decisions = readiness_runtime(mod, tmp_path, trading, codes)
    fake.ticks[codes[1]] = dict(lastPrice=0., timetag='20260911 09:29:59', openInt=13)
    if trading:
        runtime.executor.state['positions']['510300.SH'] = dict(qty=100, sellable=100, cost=100.)
        before = dict(runtime.executor.state['positions'])
    try:
        runtime.tick(NOW)
        runtime.tick(NOW.replace(second=11))
        jobs = runtime.executor.state['jobs'] if trading else runtime.jobs
        assert '20260911:09:30' in jobs and not decisions
        if trading:
            assert runtime.executor.state['positions'] == before
            assert not runtime.executor.state['targets']
        fake.ticks.clear()
        runtime.tick(NOW.replace(second=12))
        runtime.tick(NOW.replace(minute=31))
        assert not decisions and not fake.sent and not fake.canceled
        assert '保留原持仓，本轮不调仓' in capsys.readouterr().out
    finally:
        runtime.close()
    if trading:
        restarted = mod.create_runtime(fake, fake.namespace(), cfg,
                                       lambda *a: pytest.fail('No new plan after restart'), mod.select, mod.risk_signal)
        try:
            restarted.start('09:20', '09:30', ('10:30',), '14:55')
            restarted.executor.advance = lambda *a: None
            restarted.tick(NOW.replace(second=13))
            assert restarted.executor.state['positions'] == before
            assert not fake.sent
        finally:
            restarted.close()


@pytest.mark.parametrize('trading', [False, True])
def test_all_ready_opening_has_no_extra_wait_or_quote_read(mod, tmp_path, trading, monkeypatch):
    fake, runtime, _, decisions = readiness_runtime(mod, tmp_path, trading, ['510001.SH'])
    reads = []
    original = fake.get_full_tick
    fake.get_full_tick = lambda codes: reads.append(codes) or original(codes)
    monkeypatch.setattr(mod.time, 'sleep', lambda *a: pytest.fail('No unconditional opening delay'))
    try:
        runtime.tick(NOW)
        assert len(decisions) == len(reads) == 1
    finally:
        runtime.close()


def test_quote_query_error_waits_then_recovery_does_not_liquidate(env):
    fake, _, data, _, _ = env
    original = fake.get_full_tick
    fake.get_full_tick = lambda codes: (_ for _ in ()).throw(RuntimeError('temporary QMT read failure'))
    assert data.opening_quotes(['510300.SH'], NOW, '09:30') is None
    assert 'query_failed' in data.opening_status
    fake.get_full_tick = original
    assert data.opening_quotes(['510300.SH'], NOW.replace(second=1), '09:30')['510300.SH']['last_price'] == 1.
    assert not fake.sent and not fake.canceled


def test_opening_slow_read_cannot_execute_beyond_absolute_deadline(env, mod, monkeypatch):
    _, _, data, _, _ = env
    clock = iter([100., 111.])
    monkeypatch.setattr(mod.time, 'perf_counter', lambda: next(clock))
    # Bypass tracing only so perf_counter measures the read interval precisely in this test.
    data.quote_batch = lambda *a, **kw: mod.QuoteBatch({'510300.SH': {'last_price': 1.}}, (), 0)
    with pytest.raises(mod.OpeningQuotesTimeout, match='deadline='):
        data.opening_quotes(['510300.SH'], NOW, '09:30')


@pytest.mark.parametrize('bad', [0, -1, 60, float('nan'), float('inf')])
def test_quote_wait_limit_is_bounded_within_original_opening_minute(env, bad):
    _, _, data, cfg, _ = env
    cfg.OPEN_QUOTE_WAIT_SECONDS = bad
    with pytest.raises(ValueError, match='between 0 and 60'):
        data.opening_quotes(['510300.SH'], NOW, '09:30')


def test_quote_subscription_lifecycle_only_owns_its_own_feed(env):
    fake, _, data, _, _ = env
    subscriptions, closed = [], []
    fake.subscribe_whole_quote = lambda codes: subscriptions.append(codes) or len(subscriptions)
    fake.unsubscribe_quote = closed.append
    data.warm_quotes(['510300.SH'], NOW.replace(minute=20))
    data.warm_quotes(['510300.SH'], NOW)
    assert subscriptions == [['510300.SH']] and not closed
    data.warm_quotes(['510300.SH'], NOW.replace(day=14))
    assert closed == [1] and len(subscriptions) == 2
    data.close_quote_watch()
    data.close_quote_watch()
    assert closed == [1, 2] and not fake.canceled


def test_failed_subscription_falls_back_without_repeated_subscription_calls(env):
    fake, _, data, _, _ = env
    calls = []
    fake.subscribe_whole_quote = lambda codes: calls.append(codes) or -1
    fake.unsubscribe_quote = lambda sub: pytest.fail('Do not unsubscribe someone else after failure')
    data.warm_quotes(['510300.SH'], NOW.replace(minute=20))
    data.warm_quotes(['510300.SH'], NOW)
    assert len(calls) == 1
    assert data.opening_quotes(['510300.SH'], NOW, '09:30')
    data.close_quote_watch()


def test_opening_timeout_does_not_block_confirmed_reports_existing_targets_or_later_risk(mod, tmp_path):
    fake, runtime, _, decisions = readiness_runtime(mod, tmp_path, True, ['510001.SH'])
    advances, risks = [], []
    ex = runtime.executor
    ex.risk = lambda *args: risks.append(args)
    fake.ticks['510001.SH'] = dict(lastPrice=0., timetag='20260911 09:30:00', openInt=13)
    # The pre-existing order is confirmed while opening quote collection is still waiting.
    ex.advance = mod.Executor.advance.__get__(ex)
    order = submit(ex)
    ex.advance = lambda now: advances.append(now)
    try:
        runtime.tick(NOW)
        runtime.on_report('deal', legacy.row(order, m_strTradeID='during-readiness-wait',
                                             m_nVolume=100, m_dTradePrice=1.002, m_dTradeAmount=100.2))
        assert order['booked'] == 100 and len(ex.state['fills']) == 1
        runtime.tick(NOW.replace(second=11))
        runtime.tick(NOW.replace(hour=10, minute=30))
        assert not decisions and len(advances) >= 3 and len(risks) == 1
    finally:
        runtime.close()


@pytest.mark.parametrize('trading', [False, True])
def test_opening_deadline_includes_slow_preparation_not_just_quote_read(mod, tmp_path, trading, monkeypatch):
    fake, runtime, _, decisions = readiness_runtime(mod, tmp_path, trading, ['510001.SH'])
    clock, original_prepare = [100.], runtime.prepare_fn
    monkeypatch.setattr(mod.time, 'perf_counter', lambda: clock[0])

    def slow_prepare(*args):
        frame = original_prepare(*args)
        clock[0] += 11
        return frame

    runtime.prepare_fn = slow_prepare
    fake.get_full_tick = lambda *a: pytest.fail('Deadline already expired before quote read')
    try:
        runtime.tick(NOW)
        jobs = runtime.executor.state['jobs'] if trading else runtime.jobs
        assert not decisions and '20260911:09:30' in jobs and not fake.sent
    finally:
        runtime.close()


@pytest.mark.parametrize('trading', [False, True])
def test_opening_readiness_resets_each_trading_day_and_never_uses_yesterday(mod, tmp_path, trading):
    fake, runtime, _, decisions = readiness_runtime(mod, tmp_path, trading, ['510001.SH'])
    fake.get_trading_dates = lambda *args: ['20260910', '20260911', '20260914']
    try:
        runtime.tick(NOW)
        assert len(decisions) == 1
        runtime.tick(NOW.replace(day=14, minute=20))
        runtime.tick(NOW.replace(day=14))
        assert len(decisions) == 1 and 'wrong_day' in runtime.data.opening_status
        fake.ticks['510001.SH'] = dict(lastPrice=1., timetag='20260914 09:30:01', openInt=13)
        runtime.tick(NOW.replace(day=14, second=1))
        assert len(decisions) == 2 and runtime.data.opening_attempts == 2
        assert list(decisions[1].weights) == ['510001.SH']
    finally:
        runtime.close()


def test_suspension_exemption_is_only_enabled_with_original_filter(env, mod, monkeypatch):
    fake, _, data, _, _ = env
    fake.ticks['510300.SH'] = dict(lastPrice=0., timetag='20260910 15:00:00', openInt=17)
    assert data.opening_quotes(['510300.SH'], NOW, '09:30')['510300.SH']['paused']
    monkeypatch.setattr(mod, 'SKIP_SUSPENDED_LIMITUP', False)
    assert data.opening_quotes(['510300.SH'], NOW, '09:30') is None
    with pytest.raises(RuntimeError, match='最新行情无效'):
        data.quotes(['510300.SH'], NOW)


@pytest.mark.parametrize('trading', [False, True])
def test_quote_diagnostics_do_not_write_files_or_send_notices(mod, tmp_path, trading, monkeypatch):
    _, runtime, _, _ = readiness_runtime(mod, tmp_path, trading, ['510001.SH'])
    try:
        monkeypatch.setattr(mod, 'open', lambda *a, **kw: pytest.fail('Quote diagnostic must only print'), raising=False)
        runtime.log('[opening_quotes] pending=398')
        runtime.log('[quote_status] samples=[]')
    finally:
        # close() has a business log, which deliberately retains its existing file persistence.
        monkeypatch.undo()
        runtime.close()


def test_compact_quote_time_keeps_subsecond_precision(mod):
    parsed = mod.quote_datetime('20260911093000.123')
    assert parsed.replace(tzinfo=None) == NOW.replace(microsecond=123000)


def test_readiness_recovery_reaches_real_executor_once_with_original_fixed_limits(mod, tmp_path):
    codes = ['510001.SH', '510002.SH']
    fake, runtime, _, decisions = readiness_runtime(mod, tmp_path, True, codes)
    ex = runtime.executor
    ex.rebalance = mod.Executor.rebalance.__get__(ex)
    ex.advance = mod.Executor.advance.__get__(ex)
    fake.ticks[codes[1]] = dict(lastPrice=1., timetag='20260911 09:29:59', openInt=13)
    try:
        runtime.tick(NOW.replace(minute=20))
        runtime.tick(NOW)
        assert not fake.sent and not ex.state['targets'] and not decisions
        fake.ticks[codes[1]]['timetag'] = '20260911 09:30:01'
        runtime.tick(NOW.replace(second=1))
        assert len(decisions) == 1 and len(fake.sent) == 2
        assert all(call[4] == 11 and call[5] == 1.002 for call in fake.sent)
        assert set(ex.state['targets']) == set(codes)
        runtime.tick(NOW.replace(second=2))
        assert len(fake.sent) == 2 and len(decisions) == 1 and not fake.canceled
    finally:
        runtime.close()
