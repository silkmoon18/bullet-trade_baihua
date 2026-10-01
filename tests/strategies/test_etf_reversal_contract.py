"""日线反转的日期边界、过滤、精确权重和延迟实验契约。"""

import ast
import datetime
import importlib.util
import sys
import types
from pathlib import Path

import pandas as pd
import pytest

from helpers import bullet_trade_jq_remote_helper as helper

ROOT = Path(__file__).resolve().parents[2]
PATH = ROOT / 'strategies/joinquant/etf_reversal.py'


@pytest.fixture
def strategy(monkeypatch):
    jq = types.ModuleType('jqdata')
    jq.__all__ = ['g', 'log']
    jq.g = types.SimpleNamespace()
    jq.log = types.SimpleNamespace(info=lambda msg: None, error=lambda msg: None)
    monkeypatch.setitem(sys.modules, 'jqdata', jq)
    monkeypatch.setitem(sys.modules, 'bullet_trade_jq_remote_helper', helper)
    spec = importlib.util.spec_from_file_location('etf_reversal_test', PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.g.reversal_signal_date = None
    module.g.reversal_candidates = None
    module.g.reversal_rebalance_today = False
    return module


def context():
    return types.SimpleNamespace(
        current_dt=datetime.datetime(2024, 5, 20, 9, 35),
        previous_date=datetime.date(2024, 5, 17),
        portfolio=types.SimpleNamespace(total_value=10000.0, positions={}),
    )


def bars(recent_return=-0.04, money=3e7, paused=0, count=60):
    # 上升中的回调：前55日100，5日前110，昨日110*(1+r)。
    close = [100.0] * 54 + [110.0] * 5 + [110.0 * (1 + recent_return)]
    return pd.DataFrame(
        {'close': close[-count:], 'money': money, 'paused': paused},
        index=pd.bdate_range(end='2024-05-17', periods=count),
    )


def quotes(codes):
    return {
        code: types.SimpleNamespace(
            last_price=10.0, paused=False, high_limit=11.0, low_limit=9.0
        ) for code in codes
    }


def install_data(monkeypatch, strategy, history, *, day_count=6):
    strategy.ETF_POOL = tuple(history)
    listed = pd.DataFrame(
        {'start_date': [datetime.date(2010, 1, 1)] * len(history)},
        index=list(history),
    )
    calls = []
    monkeypatch.setattr(strategy, 'get_trade_days', lambda **kw: (
        pd.bdate_range(end='2024-05-20', periods=day_count)
    ), raising=False)

    def get_all(types_, **kwargs):
        assert types_ == ['etf']
        assert kwargs == {'date': context().previous_date}
        return listed

    def get_price(code, **kwargs):
        calls.append((code, kwargs))
        return history[code]

    monkeypatch.setattr(strategy, 'get_all_securities', get_all, raising=False)
    monkeypatch.setattr(strategy, 'get_price', get_price, raising=False)
    snapshot = quotes(history)
    monkeypatch.setattr(strategy, 'get_current_data', lambda: snapshot, raising=False)
    return listed, calls, snapshot


def test_multisymbol_filters_sort_top_three_and_exact_slot_weights(monkeypatch, strategy):
    history = {
        'E': bars(-0.05), 'B': bars(-0.07), 'A': bars(-0.07),
        'C': bars(-0.06), 'D': bars(-0.04),
        'ILL': bars(-0.08, money=1e7), 'RISE': bars(0.03),
        'WEAK': bars(-0.20), 'PAST_PAUSED': bars(-0.08, paused=1),
        'SHORT': bars(-0.08, count=30), 'NEW': bars(-0.08),
        'UNLISTED': bars(-0.08), 'TODAY_PAUSED': bars(-0.08),
        'UP': bars(-0.08), 'DOWN': bars(-0.08),
    }
    listed, calls, snapshot = install_data(monkeypatch, strategy, history)
    listed.drop(index='UNLISTED', inplace=True)
    listed.loc['NEW', 'start_date'] = datetime.date(2024, 5, 1)
    snapshot['TODAY_PAUSED'].paused = True
    snapshot['UP'].last_price = 11.0
    snapshot['DOWN'].last_price = 9.0
    strategy.before_market_open(context())
    weights, marks = strategy.market_open(context())
    assert list(weights) == ['A', 'B', 'C']
    assert weights == {'A': 0.9 / 3, 'B': 0.9 / 3, 'C': 0.9 / 3}
    assert marks == {'A': 10.0, 'B': 10.0, 'C': 10.0}
    assert all(kw == {
        'end_date': datetime.date(2024, 5, 17), 'count': 60,
        'frequency': 'daily', 'fields': ['close', 'money', 'paused'],
        'skip_paused': False, 'fq': 'pre',
    } for _, kw in calls)
    assert {'NEW', 'UNLISTED'}.isdisjoint(code for code, _ in calls)


def test_current_price_is_not_a_ranking_factor_and_fewer_slots_stay_in_cash(monkeypatch, strategy):
    _, _, snapshot = install_data(monkeypatch, strategy, {'A': bars(-0.04), 'B': bars(-0.05)})
    snapshot['A'].last_price = 9.01
    snapshot['B'].last_price = 10.99
    strategy.before_market_open(context())
    weights, marks = strategy.market_open(context())
    assert list(weights) == ['B', 'A']
    assert weights == {'B': 0.9 / 3, 'A': 0.9 / 3}
    assert sum(weights.values()) == pytest.approx(0.6)
    assert marks == {'B': 10.99, 'A': 9.01}


def test_valid_empty_signal_means_liquidation(monkeypatch, strategy):
    install_data(monkeypatch, strategy, {'A': bars(0.04)})
    strategy.before_market_open(context())
    assert strategy.market_open(context()) == ({}, {})


@pytest.mark.parametrize('failure', ['empty', 'nan', 'future', 'stale', 'exception'])
def test_data_failure_clears_cache_and_skips_instead_of_liquidating(monkeypatch, strategy, failure):
    history = {'A': bars()}
    install_data(monkeypatch, strategy, history)
    strategy.before_market_open(context())
    assert strategy.market_open(context()) is not None
    if failure == 'empty':
        history['A'] = pd.DataFrame()
    elif failure == 'nan':
        history['A'].iloc[-1, 0] = float('nan')
    elif failure == 'future':
        history['A'].index = pd.bdate_range(end='2024-05-20', periods=60)
    elif failure == 'stale':
        history['A'].index = pd.bdate_range(end='2024-05-16', periods=60)
    else:
        monkeypatch.setattr(strategy, 'get_price', lambda *a, **kw: (_ for _ in ()).throw(RuntimeError('offline')))
    strategy.before_market_open(context())
    assert strategy.g.reversal_candidates is None
    assert strategy.market_open(context()) is None


@pytest.mark.parametrize('day_count,should_rebalance', [(1, True), (2, False), (5, False), (6, True), (11, True)])
def test_fixed_calendar_phase_skips_between_rotations(monkeypatch, strategy, day_count, should_rebalance):
    _, calls, _ = install_data(monkeypatch, strategy, {'A': bars()}, day_count=day_count)
    strategy.before_market_open(context())
    assert strategy.g.reversal_rebalance_today is should_rebalance
    assert bool(calls) is should_rebalance
    assert (strategy.market_open(context()) is not None) is should_rebalance


def test_calendar_is_anchored_to_constant_not_backtest_start(monkeypatch, strategy):
    calls = []
    def trade_days(**kw):
        calls.append(kw)
        return pd.bdate_range(end='2024-05-20', periods=6)
    monkeypatch.setattr(strategy, 'get_trade_days', trade_days, raising=False)
    ctx = context()
    ctx.start_date = datetime.date(2024, 1, 1)
    assert strategy._is_rebalance_day(ctx)
    ctx.start_date = datetime.date(2024, 5, 1)
    assert strategy._is_rebalance_day(ctx)
    assert calls == [{'start_date': '2010-01-04', 'end_date': ctx.current_dt.date()}] * 2


def test_restart_without_signal_cache_recomputes_yesterday_data(monkeypatch, strategy):
    _, calls, _ = install_data(monkeypatch, strategy, {'A': bars()})
    assert strategy.market_open(context()) == ({'A': 0.9 / 3}, {'A': 10.0})
    assert len(calls) == 1


@pytest.mark.parametrize('delay', [0, 1, 5, 15])
def test_real_helper_keeps_frozen_decision_for_delayed_orders(monkeypatch, strategy, delay):
    _, _, snapshot = install_data(monkeypatch, strategy, {'A': bars(-0.04), 'B': bars(-0.05)})
    ctx = context()
    strategy.before_market_open(ctx)
    calls = []
    namespace = vars(strategy)
    runtime = helper.JoinQuantRuntime({
        'mode': 'BACKTEST', 'jq_account_enabled': True,
        'qmt_account_enabled': False, 'strategy_id': strategy.STRATEGY_ID,
    }, namespace)
    monkeypatch.setattr(runtime, 'cancel_orders', lambda: 0)
    monkeypatch.setattr(runtime, 'order_target_value', lambda code, value: calls.append((code, value)))
    monkeypatch.setattr(runtime, 'send_target_buy_plan', lambda *a, **kw: None)
    runtime.prepare_rebalance(ctx, strategy.market_open)
    saved = dict(strategy.g.bt_open_decision)
    ctx.current_dt += datetime.timedelta(minutes=delay)
    ctx.portfolio.total_value = 15000.0
    snapshot['A'].last_price, snapshot['B'].last_price = 10.8, 9.2
    monkeypatch.setattr(strategy, 'get_current_data', lambda: pytest.fail('下单时不应重新选股'))
    result = runtime.execute_prepared_rebalance(ctx)
    assert result['errors'] == []
    assert saved['weights'] == {'B': 0.9 / 3, 'A': 0.9 / 3}
    assert saved['marks'] == {'B': 10.0, 'A': 10.0}
    assert calls == [('B', 10000.0 * (0.9 / 3)), ('A', 10000.0 * (0.9 / 3))]
    assert runtime.execute_prepared_rebalance(ctx) is None
    assert len(calls) == 2


def test_strategy_boundary_and_thin_initialize():
    tree = ast.parse(PATH.read_text(encoding='utf-8'), feature_version=(3, 8))
    forbidden = {'order', 'order_value', 'order_target', 'order_target_value', 'run_daily',
                 'set_option', 'set_benchmark', 'set_order_cost', 'set_slippage'}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id not in forbidden
        if isinstance(node, ast.Attribute):
            assert node.attr not in {'portfolio', 'positions', 'total_value',
                                     'jq_account_enabled', 'qmt_account_enabled'}
    for function in (node for node in tree.body if isinstance(node, ast.FunctionDef)
                     and node.name in {'initialize', 'process_initialize'}):
        assert function.body[0].value.func.id == '_install_runtime'


def test_initialize_delegates_schedule_and_backtest_needs_no_remote_profile(monkeypatch, strategy):
    installed = []
    scheduled = []
    runtime = types.SimpleNamespace(
        configure_platform=lambda: None,
        schedule_daily=lambda *args, **kw: scheduled.append((args, kw)),
    )
    def install(namespace, **kwargs):
        installed.append(kwargs)
        return runtime
    monkeypatch.setattr(helper, 'install_joinquant_runtime', install)
    strategy.initialize(context())
    assert installed[0]['validate_remote_during_backtest'] is False
    assert installed[0]['expected_api_version'] == 23
    assert installed[0]['strategy_id'] == 'etf_reversal_daily'
    args, kwargs = scheduled[0]
    assert args[3] == ()
    assert kwargs == {'open_decision_time': '09:35', 'open_order_time': '09:35'}


@pytest.mark.parametrize('parameter,value', [('HOLD_DAYS', 0), ('REVERSAL_DAYS', 1.5),
                                            ('ENTRY_RETURN', 0), ('DEPLOY_RATIO', 1.1)])
def test_invalid_parameters_fail_before_strategy_runs(strategy, parameter, value):
    setattr(strategy, parameter, value)
    with pytest.raises(ValueError):
        strategy._validate_parameters()
