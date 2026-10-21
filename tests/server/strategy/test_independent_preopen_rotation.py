"""Synthetic independent preorders; never connects to a broker or network."""
from dataclasses import replace
from datetime import timedelta

import pytest

import bullet_trade.server.strategy.api as api_module
from bullet_trade.server.strategy import (
    ExecutionRequest, LimitExecution, OrderSequence, execution_request_to_wire, money_to_units,
)
from tests.server.strategy import test_strategy_api as fixtures
from tests.server.strategy.test_strategy_api import (
    api, execution_clock, drain_execution, _seed_sellable_position, _capabilities, FakeData, SECURITY,
)
from tests.server.strategy.test_scheduled_targets import timed_clock
from tests.server.strategy.test_preopen_sell_rotation import session, NEW


def request():
    return dict(strategy_id='good_etf', idempotency_key='independent-preopen',
                weights={NEW: .5}, marks={SECURITY: 10, NEW: 10},
                execution=execution_request_to_wire(ExecutionRequest(
                    style=LimitExecution(10000, preopen=True),
                    sell_style=LimitExecution(0, {SECURITY: 9_000_000}, preopen=True),
                    order_sequence=OrderSequence.INDEPENDENT)))


@pytest.mark.asyncio
@pytest.mark.parametrize('sellable', [0, 500])
async def test_buys_do_not_wait_for_working_or_unsellable_old_holdings(api, timed_clock, monkeypatch, sellable):
    service, broker, account, _ = api
    monkeypatch.setattr(fixtures, 'datetime', api_module.datetime)
    calls = await _seed_sellable_position(api, monkeypatch, sellable)
    session(service, timed_clock, monkeypatch)
    monkeypatch.setattr(service, '_schedule_open_resume', lambda _: None)
    accepted = await service.submit_targets(account, 'default', request())
    await drain_execution(service)
    assert [p['side'] for p in calls] == (['SELL', 'BUY'] if sellable else ['BUY'])
    assert calls[-1]['style'] == {'type': 'limit', 'price': '10.1'}
    assert calls[-1]['amount'] == 400
    assert broker.trades[-1]['side'] != 'SELL'
    for _ in range(3):
        await service.submit_targets(account, 'default', request())
        await drain_execution(service)
        await service._handle_broker_event('default', 'order')
    assert len(calls) == (2 if sellable else 1)
    assert service.planner.get_intent(accepted['intent']['intent_id']).execution_request.order_sequence is OrderSequence.INDEPENDENT
    await service.close()


@pytest.mark.asyncio
async def test_independent_preorder_restores_without_duplicate_submit_or_jq(api, timed_clock, monkeypatch):
    service, broker, account, _ = api
    session(service, timed_clock, monkeypatch)
    monkeypatch.setattr(service, '_schedule_open_resume', lambda _: None)
    await service.ensure_account(account, 'default', {'strategy_id': 'good_etf'})
    accepted = await service.submit_targets(account, 'default', request())
    await drain_execution(service)
    assert broker.order_calls == 1
    await service.close()
    restored = api_module.SQLiteStrategyAPI(service.config, broker, _capabilities(), FakeData())
    session(restored, timed_clock, monkeypatch)
    monkeypatch.setattr(restored, '_schedule_open_resume', lambda _: None)
    assert await restored.startup_check(account, 'default')
    await drain_execution(restored)
    assert broker.order_calls == 1
    assert restored.planner.get_intent(accepted['intent']['intent_id']).execution_request.order_sequence is OrderSequence.INDEPENDENT
    await restored.close()


def test_planner_independent_cash_budget_includes_limit_and_fee_not_future_sells(tmp_path):
    from tests.server.strategy.test_planner_executor import _setup, ACCOUNT, A
    _, repository, _, _, planner, snapshot, marks, now = _setup(tmp_path)
    constrained = replace(snapshot, available_cash_units=money_to_units('1002'))
    result = planner.submit_target_weights(
        ACCOUNT, 'budget', {A: .5}, constrained, marks, now,
        execution_request=ExecutionRequest(order_sequence=OrderSequence.INDEPENDENT))
    assert result.orders == ()
    assert repository.get_strategy_account(ACCOUNT).reserved_cash_units == 0


@pytest.mark.asyncio
async def test_independent_target_cannot_buy_next_day(api, timed_clock, monkeypatch):
    service, broker, account, _ = api
    await service.ensure_account(account, 'default', {'strategy_id': 'good_etf'})
    session(service, timed_clock, monkeypatch)
    monkeypatch.setattr(service, '_schedule_open_resume', lambda _: None)
    monkeypatch.setattr(service, '_queue_target_execution', lambda key, task: task.close())
    accepted = await service.submit_targets(account, 'default', request())
    assert broker.order_calls == 0
    await service.close()
    timed_clock[0] = (timed_clock[0] + timedelta(days=1)).replace(minute=30)
    restored = api_module.SQLiteStrategyAPI(service.config, broker, _capabilities(), FakeData())
    session(restored, timed_clock, monkeypatch)
    assert await restored.startup_check(account, 'default')
    await drain_execution(restored)
    assert broker.order_calls == 0
    assert restored.get_intent({'strategy_id': 'good_etf', 'intent_id': accepted['intent']['intent_id']})['state'] == 'CANCELED'
    await restored.close()
