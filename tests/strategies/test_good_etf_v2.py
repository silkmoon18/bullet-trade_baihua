"""V2 retains auction decisions while QMT owns its independent rotation."""

import ast
import builtins
import importlib
import pickle
from pathlib import Path
from types import SimpleNamespace as NS

import pandas as pd
import pytest

from helpers import bullet_trade_jq_remote_helper as helper_module
from tests.strategies.test_good_etf_contract import _load_strategy
from tests.strategies.test_good_etf_0926_limitup import (
    FrozenRuntime, native_runtime, fill_pending_sell,
)
from tests.test_jq_rebalance import JQ

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / 'strategies/joinquant/etf_discount_auction_jq.py'


def load_strategy(monkeypatch):
    strategy = _load_strategy(monkeypatch, strategy_path=SOURCE)
    strategy._runtime = FrozenRuntime(helper_module.RuntimeMode.BACKTEST)
    return strategy


def test_v2_changes_identity_and_budget_not_selection_formula(monkeypatch):
    assert SOURCE.name == 'etf_discount_auction_jq.py'
    assert not (SOURCE.parent / 'good_etf_jq_bridge.py').exists()
    assert not (SOURCE.parent / 'good_etf_v2.py').exists()
    old = ast.parse((SOURCE.parent / 'good_etf_0926_limitup.py').read_text(encoding='utf8'))
    new = ast.parse(SOURCE.read_text(encoding='utf8'), feature_version=(3, 8))
    functions = lambda tree: {node.name: node for node in tree.body
                              if isinstance(node, ast.FunctionDef)}
    moved = functions(new)['_prepare_candidates']
    moved.name = 'before_market_open'
    assert ast.dump(functions(old)['before_market_open']) == ast.dump(moved)
    for name in ('_load_tracked_index_names',
                 'select_opening_etfs', 'handle_risk_management'):
        assert ast.dump(functions(old)[name]) == ast.dump(functions(new)[name])
    strategy = load_strategy(monkeypatch)
    assert strategy.STRATEGY_ID == 'good_etf_v2'
    assert strategy.STRATEGY_NAME == 'ETF竞价折价轮动_jq'
    assert strategy.MAX_HOLD_NUM == 2
    assert strategy.QMT_INITIAL_CAPITAL == 5000
    assert strategy.BUY_PRICE_INCREASE == .002
    assert strategy.OPEN_DECISION_TIME == '09:26'
    assert strategy.OPEN_ORDER_TIME == '09:30'
    assert strategy.VALIDATE_REMOTE_DURING_BACKTEST is False
    assert not {node.func.id for node in ast.walk(new) if isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)} & {
                    'run_daily', 'set_slippage', 'set_order_cost', 'order_target',
                    'order_target_value', 'get_portfolio', 'submit_targets',
                }
    assert 'qmt_account_enabled' not in SOURCE.read_text(encoding='utf8')


def test_strategy_only_declares_execution_policy(monkeypatch):
    strategy = load_strategy(monkeypatch)
    strategy.execute_opening_plan(NS())
    assert strategy._runtime.staged_advances[0][1] == {
        'jq_wait_for_sells': True, 'backtest_buy_price_increase': .002,
        'qmt_server_rotation': True,
    }
    prepared = []
    strategy._runtime.prepare_rebalance = lambda context, decide, **kwargs: prepared.append(kwargs)
    strategy.market_open(NS())
    assert prepared == [{'qmt_execute_time': '09:30', 'qmt_preopen_sells': True}]


@pytest.mark.parametrize('run_type', ['simple_backtest', 'full_backtest'])
def test_v2_backtest_never_reads_private_config_or_allocates_real_capital(monkeypatch, run_type):
    helper = importlib.reload(helper_module)
    strategy = load_strategy(monkeypatch)
    original_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        if name == 'jq_runtime_config':
            pytest.fail('Backtest must not require private configuration')
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, '__import__', guarded_import)
    monkeypatch.setattr(helper, '_strategy_request', lambda *a, **k: pytest.fail('No remote request'))
    strategy._install_runtime(NS(run_params=NS(type=run_type)))
    assert strategy._runtime.state['strategy_id'] == 'good_etf_v2'
    assert strategy._runtime.jq_account_enabled
    assert not strategy._runtime.qmt_account_enabled


def setup_rotation(monkeypatch, jq_enabled=True):
    helper = importlib.reload(helper_module)
    jq = JQ(cash=1000, holdings={'OLD': 400, 'TRIM': 500})
    runtime, sells = native_runtime(jq, helper)
    runtime.mode = helper.RuntimeMode.JQ_QMT_PARALLEL if jq_enabled else helper.RuntimeMode.QMT_REMOTE
    runtime.jq_account_enabled = jq_enabled
    runtime.qmt_account_enabled = True
    runtime.state.update(mode=runtime.mode.value, jq_account_enabled=jq_enabled,
                         qmt_account_enabled=True, strategy_id='good_etf_v2')
    runtime.state['server_features'] = {'scheduled_targets_v1': True, 'preopen_sell_rotation_v1': True}
    runtime._qmt_callback_allowed = lambda *args: True
    runtime.advance_targets = lambda context: True
    qmt = NS(total_value=5000, positions={
        'QMT_OLD': NS(total_amount=100, price=8.0, value=800),
        'QMT_TRIM': NS(total_amount=200, price=10.0, value=2000),
    })
    monkeypatch.setattr(helper, 'get_portfolio', lambda **kwargs: qmt)
    runtime._namespace['get_current_data'] = lambda: {
        code: NS(low_limit=7.2 if code == 'QMT_OLD' else 9.0)
        for code in ('OLD', 'TRIM', 'QMT_OLD', 'QMT_TRIM', 'BUY')
    }
    runtime._namespace['set_slippage'] = lambda *args: pytest.fail('No live buy stress')
    submitted = []

    def submit(context, weights, marks, key, execution, names):
        submitted.append((dict(weights), dict(marks), key, execution, len(jq.calls)))
        return {'intent': {'intent_id': 'test-intent'}, 'planned_orders': []}

    runtime.submit_targets = submit
    return helper, jq, runtime, qmt, sells, submitted


def start(jq, runtime, weights=None):
    weights = {'BUY': .5, 'QMT_TRIM': .2} if weights is None else weights
    jq.context.current_dt = jq.context.current_dt.replace(minute=26)
    runtime.prepare_rebalance(jq.context, lambda context: (weights, dict(jq.prices, QMT_TRIM=10)))
    jq.context.current_dt = jq.context.current_dt.replace(minute=30)
    runtime.execute_prepared_rebalance(
        jq.context, jq_wait_for_sells=True, qmt_server_rotation=True,
        backtest_buy_price_increase=.002,
    )


def test_qmt_uses_own_holdings_one_intent_and_server_sell_style_before_jq_calls(monkeypatch):
    helper, jq, runtime, qmt, sells, submitted = setup_rotation(monkeypatch)
    jq.pending.add('OLD')
    start(jq, runtime)
    assert len(submitted) == 1
    weights, marks, key, execution, calls_before_qmt = submitted[0]
    assert weights == {'BUY': .5, 'QMT_TRIM': .2}
    assert marks['QMT_OLD'] == 8.0  # Valuation isn't marked down to the lower limit.
    assert key == 'open-20260908'
    assert calls_before_qmt == 0
    assert execution.style == helper.LimitExecution(2000)
    assert execution.sell_style.limit_prices == {'QMT_OLD': 7200000, 'QMT_TRIM': 9000000}
    assert 'OLD' not in execution.sell_style.limit_prices  # JQ holdings aren't QMT holdings.
    assert execution.follow_up == helper.FollowUpPolicy.UNTIL_FILLED_TODAY
    assert execution.repricing == helper.RepricingPolicy.KEEP_ORIGINAL
    assert jq.calls == [('OLD', 0), ('TRIM', 0)]
    assert 'BUY' not in jq.positions  # JQ still awaits its own confirmed sell.
    assert jq.g.bt_sell_then_buy['qmt'] == 'BUY_SENT'
    fill_pending_sell(jq, 'OLD')
    jq.context.current_dt += pd.Timedelta(minutes=1)
    runtime.advance_sell_then_buy(jq.context)
    assert 'BUY' in jq.positions
    assert len(submitted) == 1  # QMT isn't re-driven by the JQ minute worker.


def test_v2_prepares_qmt_at_0926_and_0930_never_requires_remote_connection(monkeypatch):
    helper, jq, runtime, _, _, submitted = setup_rotation(monkeypatch)
    original = runtime.submit_targets
    times = []

    def submit(*args, **kwargs):
        times.append((args[0].current_dt, kwargs))
        return original(*args)

    runtime.submit_targets = submit
    jq.context.current_dt = jq.context.current_dt.replace(minute=26)
    runtime.prepare_rebalance(jq.context, lambda context: ({'BUY': .5}, jq.prices),
                              qmt_execute_time='09:30')
    assert len(submitted) == 1 and not jq.calls
    assert times == [(jq.context.current_dt, {'execute_at': '2026-09-08T09:30:00'})]
    assert submitted[0][3].style == helper.LimitExecution(2000)
    assert not submitted[0][3].style.preopen
    jq.g = pickle.loads(pickle.dumps(jq.g))
    runtime._namespace['g'] = jq.g
    monkeypatch.setattr(helper, 'get_portfolio', lambda **kwargs: pytest.fail('No QMT read at 09:30'))
    runtime.submit_targets = lambda *args, **kwargs: pytest.fail('No QMT submit at 09:30')
    runtime.advance_targets = lambda *args: pytest.fail('No QMT advance at 09:30')
    jq.context.current_dt = jq.context.current_dt.replace(minute=30)
    runtime.execute_prepared_rebalance(jq.context, jq_wait_for_sells=True, qmt_server_rotation=True)
    assert 'BUY' in jq.positions and len(submitted) == 1
    assert jq.g.bt_sell_then_buy['qmt'] == 'DONE'


def test_schedule_receipt_timeout_keeps_jq_plan_without_0930_qmt_fallback(monkeypatch):
    _, jq, runtime, _, _, _ = setup_rotation(monkeypatch)
    calls = []

    def timeout(*args, **kwargs):
        calls.append(kwargs)
        raise TimeoutError('receipt unknown')

    runtime.submit_targets = timeout
    jq.context.current_dt = jq.context.current_dt.replace(minute=26)
    runtime.prepare_rebalance(jq.context, lambda context: ({'BUY': .5}, jq.prices),
                              qmt_execute_time='09:30')
    assert jq.g.bt_open_decision is not None and len(calls) == 1
    assert not jq.g.bt_open_decision.get('qmt_scheduled')
    jq.context.current_dt = jq.context.current_dt.replace(minute=30)
    runtime.execute_prepared_rebalance(jq.context, jq_wait_for_sells=True, qmt_server_rotation=True)
    runtime.advance_sell_then_buy(jq.context)
    assert 'BUY' in jq.positions and len(calls) == 1


def test_missing_server_feature_never_falls_back_to_an_immediate_target(monkeypatch):
    _, jq, runtime, _, _, submitted = setup_rotation(monkeypatch)
    runtime.state['server_features'] = {}
    jq.context.current_dt = jq.context.current_dt.replace(minute=26)
    runtime.prepare_rebalance(jq.context, lambda context: ({'BUY': .5}, jq.prices), qmt_execute_time='09:30')
    assert not submitted and not jq.calls
    assert jq.g.bt_open_decision is not None and not jq.g.bt_open_decision.get('qmt_scheduled')
    jq.context.current_dt = jq.context.current_dt.replace(minute=30)
    runtime.execute_prepared_rebalance(jq.context, jq_wait_for_sells=True, qmt_server_rotation=True)
    assert not submitted and 'BUY' in jq.positions


def test_schedule_notification_failure_does_not_misreport_accepted_target(monkeypatch):
    _, jq, runtime, _, _, submitted = setup_rotation(monkeypatch)
    original = runtime.submit_targets
    runtime.submit_targets = lambda *args, **kwargs: original(*args)

    def failed_notification(*args, **kwargs):
        raise OSError('notification unavailable')

    runtime.send_target_buy_plan = failed_notification
    jq.context.current_dt = jq.context.current_dt.replace(minute=26)
    runtime.prepare_rebalance(jq.context, lambda context: ({'BUY': .5}, jq.prices),
                              qmt_execute_time='09:30')
    assert len(submitted) == 1 and jq.g.bt_open_decision['qmt_scheduled']


@pytest.mark.parametrize('jq_enabled', [False, True])
def test_v2_preopen_sells_start_at_0926_and_jq_0930_cannot_drive_qmt(monkeypatch, jq_enabled):
    helper, jq, runtime, _, _, submitted = setup_rotation(monkeypatch, jq_enabled)
    times, original = [], runtime.submit_targets

    def submit(*args, **kwargs):
        times.append((args[0].current_dt, kwargs))
        return original(*args)

    runtime.submit_targets = submit
    jq.context.current_dt = jq.context.current_dt.replace(minute=26)
    runtime.prepare_rebalance(jq.context, lambda context: ({'BUY': .5}, jq.prices),
                              qmt_execute_time='09:30', qmt_preopen_sells=True)
    assert len(submitted) == 1 and not jq.calls
    assert times == [(jq.context.current_dt, {})]  # Immediate target, not a future execute_at.
    execution = submitted[0][3]
    assert execution.style == helper.LimitExecution(2000) and not execution.style.preopen
    assert execution.sell_style == helper.LimitExecution(0, {'QMT_OLD': 7200000, 'QMT_TRIM': 9000000}, preopen=True)
    assert execution.follow_up is helper.FollowUpPolicy.UNTIL_FILLED_TODAY
    assert execution.repricing is helper.RepricingPolicy.KEEP_ORIGINAL
    jq.g = pickle.loads(pickle.dumps(jq.g))
    runtime._namespace['g'] = jq.g
    runtime.submit_targets = lambda *a, **k: pytest.fail('No second QMT target')
    monkeypatch.setattr(helper, 'get_portfolio', lambda **kwargs: pytest.fail('No QMT query from JQ opening callback'))
    runtime.advance_targets = lambda *a: pytest.fail('No JQ-driven QMT order progression')
    jq.context.current_dt = jq.context.current_dt.replace(minute=30)
    runtime.execute_prepared_rebalance(jq.context, jq_wait_for_sells=True, qmt_server_rotation=True)
    assert len(submitted) == 1
    assert ('BUY' in jq.positions) is jq_enabled


def test_old_server_cannot_silently_accept_preopen_rotation(monkeypatch):
    _, jq, runtime, _, _, submitted = setup_rotation(monkeypatch)
    runtime.state['server_features'] = {'scheduled_targets_v1': True}
    jq.context.current_dt = jq.context.current_dt.replace(minute=26)
    runtime.prepare_rebalance(jq.context, lambda context: ({'BUY': .5}, jq.prices),
                              qmt_execute_time='09:30', qmt_preopen_sells=True)
    assert not submitted and not jq.calls
    assert jq.g.bt_open_decision is not None and not jq.g.bt_open_decision['qmt_scheduled']


def test_missing_runtime_feature_rejected_before_private_config_or_account(monkeypatch):
    helper = importlib.reload(helper_module)
    monkeypatch.setattr(helper, '_load_runtime_configuration', lambda *a: pytest.fail('No config read'))
    with pytest.raises(RuntimeError, match='helper缺少策略要求的功能'):
        helper.install_joinquant_runtime({}, context=NS(run_params=NS(type='sim_trade')),
                                         qmt_initial_capital=5000, required_features=('unknown',))


@pytest.mark.parametrize('time,flag,kind', [('09:35', True, ValueError), (None, True, ValueError),
                                          ('09:30', 1, TypeError)])
def test_preopen_rotation_cannot_accidentally_change_buy_start(monkeypatch, time, flag, kind):
    _, jq, runtime, _, _, submitted = setup_rotation(monkeypatch)
    with pytest.raises(kind):
        runtime.prepare_rebalance(jq.context, lambda context: ({'BUY': .5}, jq.prices),
                                  qmt_execute_time=time, qmt_preopen_sells=flag)
    assert not submitted and not jq.calls


@pytest.mark.parametrize('enabled', [False, True])
def test_scheduled_prepare_does_not_submit_qmt_for_jq_or_historical_replay(monkeypatch, enabled):
    _, jq, runtime, _, _, submitted = setup_rotation(monkeypatch)
    runtime.qmt_account_enabled = enabled
    runtime._qmt_callback_allowed = lambda *args: False
    jq.context.current_dt = jq.context.current_dt.replace(minute=26)
    runtime.prepare_rebalance(jq.context, lambda context: ({'BUY': .5}, jq.prices),
                              qmt_execute_time='09:30')
    assert not submitted and not jq.calls
    runtime._qmt_callback_allowed = lambda *args: True
    jq.context.current_dt = jq.context.current_dt.replace(minute=30)
    runtime.execute_prepared_rebalance(jq.context, jq_wait_for_sells=True, qmt_server_rotation=True)
    assert 'BUY' in jq.positions and not submitted


def test_jq_sell_failure_does_not_cancel_or_block_independent_qmt_intent(monkeypatch):
    _, jq, runtime, _, _, submitted = setup_rotation(monkeypatch)
    runtime._namespace['order_target'] = lambda *args, **kwargs: None
    start(jq, runtime)
    assert len(submitted) == 1
    assert jq.g.bt_sell_then_buy['jq'] == 'FAILED'
    assert jq.g.bt_sell_then_buy['qmt'] == 'BUY_SENT'


def test_qmt_submission_failure_does_not_stop_jq_and_recovery_uses_saved_exact_request(monkeypatch):
    helper, jq, runtime, qmt, _, submitted = setup_rotation(monkeypatch)
    successful = runtime.submit_targets
    attempted = []

    def timeout(*args):
        attempted.append(args)
        raise TimeoutError('receipt unknown')

    runtime.submit_targets = timeout
    start(jq, runtime)
    assert 'BUY' in jq.positions
    assert jq.g.bt_sell_then_buy['qmt'] == 'SERVER_SUBMITTING'
    jq.g = pickle.loads(pickle.dumps(jq.g))
    runtime._namespace['g'] = jq.g
    qmt.positions.clear()  # The recovery request doesn't change after an unknown receipt.
    runtime.submit_targets = successful
    runtime.advance_sell_then_buy(jq.context)
    weights, marks, key, execution, _ = submitted[0]
    assert (weights, marks, key, helper._execution_to_wire(execution)) == (
        attempted[0][1], attempted[0][2], attempted[0][3],
        helper._execution_to_wire(attempted[0][4]),
    )
    assert len(jq.calls) == 3  # No repeated JQ buys/sells after recovery.


def test_qmt_only_does_not_read_or_trade_jq_account(monkeypatch):
    _, jq, runtime, _, sells, submitted = setup_rotation(monkeypatch, jq_enabled=False)
    jq.context.portfolio = None
    start(jq, runtime)
    assert len(submitted) == 1 and not sells and not jq.calls


def test_all_cash_selection_only_sells_qmt_owned_positions(monkeypatch):
    _, jq, runtime, _, _, submitted = setup_rotation(monkeypatch)
    start(jq, runtime, {})
    assert submitted[0][0] == {'QMT_OLD': 0, 'QMT_TRIM': 0}
    assert 'OLD' not in submitted[0][0]


def test_old_active_qmt_target_waits_without_a_second_submission(monkeypatch):
    _, jq, runtime, _, _, submitted = setup_rotation(monkeypatch)
    runtime.advance_targets = lambda context: False
    start(jq, runtime)
    assert not submitted
    assert 'BUY' in jq.positions
    runtime.advance_targets = lambda context: True
    runtime.advance_sell_then_buy(jq.context)
    assert len(submitted) == 1


def test_historical_replay_does_not_create_or_later_resume_a_qmt_target(monkeypatch):
    _, jq, runtime, _, _, submitted = setup_rotation(monkeypatch)
    runtime._qmt_callback_allowed = lambda *args: False
    start(jq, runtime)
    assert not submitted and 'BUY' in jq.positions
    runtime._qmt_callback_allowed = lambda *args: True
    runtime.advance_sell_then_buy(jq.context)
    assert not submitted


def test_next_day_cannot_submit_yesterdays_pending_rotation(monkeypatch):
    _, jq, runtime, _, _, submitted = setup_rotation(monkeypatch)
    runtime.advance_targets = lambda context: False
    start(jq, runtime)
    jq.context.current_dt += pd.Timedelta(days=1)
    runtime.advance_targets = lambda context: True
    runtime.advance_sell_then_buy(jq.context)
    assert not submitted


def test_nonempty_v2_selection_preserves_exact_auction_ranking_and_weights(monkeypatch):
    strategy = load_strategy(monkeypatch)
    strategy.g.fund_list = pd.DataFrame({'unit_net_value': [2.0] * 4},
                                      index=['SECOND', 'THIRD', 'FIRST', 'EXPENSIVE'])
    strategy.get_current_data = lambda: {
        'FIRST': NS(day_open=1.0, high_limit=2.2, paused=False, last_price=3),
        'SECOND': NS(day_open=1.5, high_limit=2.2, paused=False, last_price=.1),
        'THIRD': NS(day_open=1.8, high_limit=2.2, paused=False, last_price=.01),
        'EXPENSIVE': NS(day_open=2.1, high_limit=2.2, paused=False, last_price=.01),
    }
    weights, marks = strategy.select_opening_etfs(NS())
    assert list(weights) == ['FIRST', 'SECOND']
    assert weights == pytest.approx({'FIRST': .95 * 2/3, 'SECOND': .95/3})
    assert marks == {'FIRST': 1, 'SECOND': 1.5}


def test_new_5000_account_does_not_take_old_strategy_positions(tmp_path):
    from tests.server.strategy.test_planner_executor import _setup, _add_position, ACCOUNT, PHYSICAL
    from bullet_trade.server.strategy import money_to_units
    from datetime import date

    database, repository, capital, _, _, _, _, _ = _setup(tmp_path)
    _add_position(database, capital, date(2026, 8, 10), date(2026, 8, 11))
    before = repository.get_strategy_account(ACCOUNT)
    created = capital.ensure_strategy_account(
        'good_etf_v2', 'good_etf_v2', PHYSICAL, money_to_units('5000'),
    )
    assert created.created
    assert created.account.initial_capital_units == money_to_units('5000')
    assert created.account.cash_units == money_to_units('5000')
    assert repository.get_strategy_account(ACCOUNT) == before
    from bullet_trade.server.strategy.schema import connect_database
    db = connect_database(database)
    try:
        assert db.execute('SELECT COUNT(*) FROM position_lots WHERE strategy_account_id = ?',
                          ('good_etf_v2',)).fetchone()[0] == 0
        assert db.execute('SELECT COUNT(*) FROM position_lots WHERE strategy_account_id = ?',
                          (ACCOUNT,)).fetchone()[0] > 0
    finally:
        db.close()


@pytest.mark.parametrize('scheduled', [False, True])
@pytest.mark.parametrize('preopen', [False, True])
def test_server_lower_limit_sells_must_fill_before_fixed_premium_buys(tmp_path, scheduled, preopen):
    from tests.server.strategy.test_planner_executor import _setup, _add_position, ACCOUNT, PHYSICAL, A, B
    from bullet_trade.server.strategy import (
        money_to_units, price_to_units, BrokerAccountSnapshot, BrokerPositionSnapshot,
        SQLiteValuationService, MarketMark, ExecutionRequest, LimitExecution,
        SQLiteFillBookingService, OrderSide,
    )
    from bullet_trade.server.strategy.domain import BrokerFill
    from datetime import date, timedelta

    initial_time = pd.Timestamp('2026-08-11 09:26', tz='Asia/Shanghai').to_pydatetime()
    database, repository, capital, reconciliation, planner, _, _, now = _setup(tmp_path, initial_time)
    _add_position(database, capital, date(2026, 8, 10), date(2026, 8, 11))
    marks = {A: MarketMark(A, price_to_units('2'), now, 'test'),
             B: MarketMark(B, price_to_units('5'), now, 'test')}
    valuation = SQLiteValuationService(database)

    def refresh(quantity, broker_cash):
        reconciliation.synchronize(ACCOUNT, PHYSICAL, BrokerAccountSnapshot(
            money_to_units(broker_cash),
            (BrokerPositionSnapshot(A, quantity, quantity),) if quantity else (), (), (), now,
        ))
        return valuation.create_snapshot(ACCOUNT, marks, now, timedelta(minutes=1))

    snapshot = refresh(1000, '17995')
    result = planner.submit_target_weights(
        ACCOUNT, 'v2-server-rotation', {B: .95}, snapshot, marks, now,
        execution_request=ExecutionRequest(style=LimitExecution(2000),
                                          sell_style=LimitExecution(0, {A: price_to_units('1.8')}, preopen=preopen)),
        execute_at=now.replace(minute=30) if scheduled else None,
    )
    if scheduled:
        assert not result.orders and repository.get_strategy_account(ACCOUNT).reserved_cash_units == 0
        assert not planner.advance_intent(result.intent.intent_id, snapshot, marks, now).orders
        now = now.replace(minute=30)
        marks = {code: type(mark)(code, mark.price_units, now, 'test') for code, mark in marks.items()}
        result = planner.advance_intent(result.intent.intent_id, refresh(1000, '17995'), marks, now)
    assert len(result.orders) == 1
    sell = result.orders[0]
    assert sell.side is OrderSide.SELL and sell.limit_price_units == price_to_units('1.8')
    assert not planner.advance_intent(result.intent.intent_id, snapshot, marks, now).orders
    booking = SQLiteFillBookingService(database)
    booking.mark_order_submitted(ACCOUNT, sell.order_id, 'broker-sell')

    def fill(quantity, key):
        account = repository.get_strategy_account(ACCOUNT)
        booking.book_fill(ACCOUNT, BrokerFill(
            fill_id=key, broker_trade_id='broker-' + key, order_id=sell.order_id,
            fingerprint=key, security=A, side=OrderSide.SELL, quantity=quantity,
            price_units=price_to_units('2'), commission_units=0, tax_units=0,
            traded_at=now,
        ), account.ledger_version)

    fill(500, 'partial')
    partial = valuation.create_snapshot(ACCOUNT, marks, now, timedelta(minutes=1))
    assert not planner.advance_intent(result.intent.intent_id, partial, marks, now).orders
    fill(500, 'complete')
    flat = valuation.create_snapshot(ACCOUNT, marks, now, timedelta(minutes=1))
    if preopen and not scheduled:
        assert not planner.advance_intent(result.intent.intent_id, flat, marks, now).orders
        assert repository.get_strategy_account(ACCOUNT).reserved_cash_units == 0
        now = now.replace(minute=30)
        marks = {code: type(mark)(code, mark.price_units, now, 'test') for code, mark in marks.items()}
        flat = valuation.create_snapshot(ACCOUNT, marks, now, timedelta(minutes=10))
    buy = planner.advance_intent(result.intent.intent_id, flat, marks, now)
    assert len(buy.orders) == 1
    assert buy.orders[0].side is OrderSide.BUY and buy.orders[0].security == B
    assert buy.orders[0].limit_price_units == price_to_units('5.010')
