"""Synthetic preorders: no client, network, real account or live orders."""
import asyncio

import pytest

import bullet_trade.server.strategy.api as api_module
from bullet_trade.server.strategy import ExecutionRequest, LimitExecution, execution_request_to_wire
from bullet_trade.server.strategy.schema import connect_database
from tests.server.strategy import test_strategy_api as fixtures
from tests.server.strategy.test_strategy_api import (
    api, execution_clock, drain_execution, _seed_sellable_position, _capabilities, FakeData, SECURITY,
)
from tests.server.strategy.test_scheduled_targets import timed_clock


NEW = '510300.XSHG'


def request():
    return dict(strategy_id='good_etf', idempotency_key='preopen-sell-rotation',
                weights={NEW: .5}, marks={SECURITY: 10, NEW: 10},
                execution=execution_request_to_wire(ExecutionRequest(
                    style=LimitExecution(2000),
                    sell_style=LimitExecution(0, {SECURITY: 9_000_000}, preopen=True),
                )))


def session(service, clock, monkeypatch):
    monkeypatch.setattr(service, '_can_dispatch_now', lambda: (clock[0].hour, clock[0].minute) >= (9, 30))
    monkeypatch.setattr(service, '_is_limit_preopen_session',
                        lambda value: (9, 26) <= (value.hour, value.minute) < (9, 30))


@pytest.mark.asyncio
@pytest.mark.parametrize('fill_before_open', [False, True])
async def test_sell_preorder_partial_full_callbacks_unlock_one_buy_without_jq(
    api, timed_clock, monkeypatch, fill_before_open,
):
    service, broker, account, _ = api
    monkeypatch.setattr(fixtures, 'datetime', api_module.datetime)
    calls = await _seed_sellable_position(api, monkeypatch, 500)
    session(service, timed_clock, monkeypatch)
    scheduled = []
    monkeypatch.setattr(service, '_schedule_open_resume', scheduled.append)
    accepted = await service.submit_targets(account, 'default', request())
    await drain_execution(service)
    assert [p['side'] for p in calls] == ['SELL']
    assert calls[0]['style'] == {'type': 'limit', 'price': '9'}
    assert 'execute_at' not in accepted['intent'] or accepted['intent']['execute_at'] is None
    assert scheduled == ['good_etf']
    await service.submit_targets(account, 'default', request())
    await drain_execution(service)
    assert len(calls) == 1  # An active preorder is never blindly resent.
    sell = broker.orders[-1]
    original_targets = dict(service.planner.get_intent(accepted['intent']['intent_id']).targets)
    for index, quantity in enumerate((100, 400)):
        timed_clock[0] = timed_clock[0].replace(minute=29 if fill_before_open else 30, second=index + 1)
        sell.update(status='partly_filled' if quantity == 100 else 'filled',
                    filled=100 if quantity == 100 else 500)
        broker.cash += quantity * 10 - quantity / 100
        broker.positions = ([{'security': SECURITY, 'amount': 400, 'closeable_amount': 0}]
                            if quantity == 100 else [])
        broker.trades.append(dict(
            trade_id='sell-fill-' + str(quantity), trade_id_source='broker', order_id=sell['order_id'],
            security=SECURITY, side='SELL', amount=quantity, price=10,
            commission_fee=quantity / 100, commission_known=True, tax=0, tax_known=True,
            time=timed_clock[0].isoformat(),
        ))
        await service._handle_broker_event('default', 'trade')
        assert [p['side'] for p in calls] == (
            ['SELL', 'BUY'] if quantity == 400 and not fill_before_open else ['SELL'])
    if fill_before_open:
        db = connect_database(service.database_path)
        try:
            assert db.execute('SELECT reserved_cash_units FROM strategy_accounts').fetchone()[0] == 0
        finally:
            db.close()
        timed_clock[0] = timed_clock[0].replace(minute=30, second=0)
        await service._resume_intent(accepted['intent']['intent_id'])  # Server wake, not a JQ message.
    for _ in range(3):
        await service._handle_broker_event('default', 'order')
    assert [p['side'] for p in calls] == ['SELL', 'BUY']
    assert calls[1]['style'] == {'type': 'limit', 'price': '10.02'}
    assert dict(service.planner.get_intent(accepted['intent']['intent_id']).targets) == original_targets
    await service.close()


@pytest.mark.asyncio
async def test_no_sells_waits_without_buy_reservation_and_restores_after_restart(api, timed_clock, monkeypatch):
    service, broker, account, _ = api
    session(service, timed_clock, monkeypatch)
    monkeypatch.setattr(service, '_schedule_open_resume', lambda _: None)
    await service.ensure_account(account, 'default', {'strategy_id': 'good_etf'})
    accepted = await service.submit_targets(account, 'default', request())
    await drain_execution(service)
    assert broker.order_calls == 0 and accepted['snapshot']['reserved_cash'] == 0
    assert accepted['planned_orders'] == []
    await service.close()
    timed_clock[0] = timed_clock[0].replace(minute=28)
    restored = api_module.SQLiteStrategyAPI(service.config, broker, _capabilities(), FakeData())
    session(restored, timed_clock, monkeypatch)
    monkeypatch.setattr(restored, '_schedule_open_resume', lambda _: None)
    assert await restored.startup_check(account, 'default')
    await drain_execution(restored)
    assert broker.order_calls == 0
    timed_clock[0] = timed_clock[0].replace(minute=30)
    await restored._resume_intent(accepted['intent']['intent_id'])
    assert broker.order_calls == 1
    await restored._resume_intent(accepted['intent']['intent_id'])
    assert broker.order_calls == 1
    await restored.close()


@pytest.mark.asyncio
async def test_preopen_sell_only_policy_arms_server_open_wakeup(api, timed_clock, monkeypatch):
    service, broker, account, _ = api
    session(service, timed_clock, monkeypatch)
    await service.ensure_account(account, 'default', {'strategy_id': 'good_etf'})
    entered, release = asyncio.Event(), asyncio.Event()
    waits = []

    async def sleep(delay):
        waits.append(delay)
        entered.set()
        await release.wait()

    monkeypatch.setattr(api_module.asyncio, 'sleep', sleep)
    await service.submit_targets(account, 'default', request())
    await asyncio.wait_for(entered.wait(), 1)
    assert waits == [240] and broker.order_calls == 0
    assert not any(lock.locked() for lock in service._resume_locks.values())
    timed_clock[0] = timed_clock[0].replace(minute=30)
    release.set()
    await drain_execution(service)
    assert broker.order_calls == 1
    await service.close()


@pytest.mark.asyncio
async def test_yesterday_preopen_sell_rotation_cannot_buy_on_restart(api, timed_clock, monkeypatch):
    from datetime import timedelta
    service, broker, account, _ = api
    session(service, timed_clock, monkeypatch)
    monkeypatch.setattr(service, '_schedule_open_resume', lambda _: None)
    await service.ensure_account(account, 'default', {'strategy_id': 'good_etf'})
    accepted = await service.submit_targets(account, 'default', request())
    await drain_execution(service)
    await service.close()
    timed_clock[0] = (timed_clock[0] + timedelta(days=1)).replace(minute=30)
    restored = api_module.SQLiteStrategyAPI(service.config, broker, _capabilities(), FakeData())
    session(restored, timed_clock, monkeypatch)
    assert await restored.startup_check(account, 'default')
    await drain_execution(restored)
    assert broker.order_calls == 0
    assert restored.get_intent({'strategy_id': 'good_etf',
                                'intent_id': accepted['intent']['intent_id']})['state'] == 'CANCELED'
    await restored.close()
