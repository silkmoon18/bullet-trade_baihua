"""Durable targets start on the server without a second JQ request."""

import asyncio
from dataclasses import replace
from datetime import datetime, timedelta

import pytest

import bullet_trade.server.strategy.api as api_module
from bullet_trade.server.strategy import SQLiteStrategyAPI
from bullet_trade.server.strategy import ExecutionRequest, LimitExecution, execution_request_to_wire
from bullet_trade.server.strategy.domain import SHANGHAI_TZ
from bullet_trade.server.strategy.schema import connect_database
from tests.server.strategy.test_strategy_api import (
    api, _capabilities, FakeData, SECURITY, drain_execution,
)


@pytest.fixture
def timed_clock(monkeypatch):
    clock = [datetime(2026, 10, 15, 9, 26, tzinfo=SHANGHAI_TZ)]

    class ClockMeta(type):
        def __instancecheck__(cls, value):
            return isinstance(value, datetime)

    class Clock(datetime, metaclass=ClockMeta):
        @classmethod
        def now(cls, tz=None):
            return clock[0]

    monkeypatch.setattr(api_module, 'datetime', Clock)
    original = api_module.collect_async_broker_snapshot

    async def snapshot(broker, account, *args, **kwargs):
        return await original(broker, account, as_of=clock[0])

    monkeypatch.setattr(api_module, 'collect_async_broker_snapshot', snapshot)
    return clock


def request(clock):
    return {'strategy_id': 'good_etf', 'idempotency_key': 'open-20261015',
            'weights': {SECURITY: .5}, 'marks': {SECURITY: 10},
            'execute_at': clock[0].replace(hour=9, minute=30).isoformat()}


@pytest.mark.asyncio
@pytest.mark.parametrize('minute', [30, 35])
@pytest.mark.parametrize('preopen', [False, True])
async def test_scheduled_target_no_orders_or_reservations_until_server_timer(
    api, timed_clock, monkeypatch, minute, preopen,
):
    service, broker, account, _ = api
    await service.ensure_account(account, 'default', {'strategy_id': 'good_etf'})
    entered, release = asyncio.Event(), asyncio.Event()
    delays = []

    async def sleep(delay):
        delays.append(delay)
        entered.set()
        await release.wait()

    monkeypatch.setattr(api_module.asyncio, 'sleep', sleep)
    payload = request(timed_clock)
    payload['execute_at'] = timed_clock[0].replace(minute=minute).isoformat()
    payload['execution'] = execution_request_to_wire(ExecutionRequest(style=LimitExecution(preopen=preopen)))
    result = await service.submit_targets(account, 'default', payload)
    await asyncio.wait_for(entered.wait(), 1)
    assert result['planned_orders'] == [] and result['snapshot']['reserved_cash'] == 0
    assert result['intent']['execute_at'] == payload['execute_at']
    assert result['intent']['targets'] == {SECURITY: 500}
    assert delays == [(minute - 26) * 60] and broker.order_calls == 0
    assert not any(lock.locked() for lock in service._resume_locks.values())
    repeated = await service.submit_targets(account, 'default', payload)
    assert repeated['intent']['intent_id'] == result['intent']['intent_id']
    timed_clock[0] = timed_clock[0].replace(minute=minute-1, second=59)
    await service._resume_intent(result['intent']['intent_id'])  # Native callbacks can't start early.
    assert broker.order_calls == 0
    timed_clock[0] = timed_clock[0].replace(minute=minute, second=0)
    release.set()  # No further JQ request; only the server-owned timer wakes.
    await drain_execution(service)
    assert broker.order_calls == 1
    db = connect_database(service.database_path)
    try:
        assert db.execute('SELECT COUNT(*) FROM strategy_orders').fetchone()[0] == 1
    finally:
        db.close()
    await service._resume_intent(result['intent']['intent_id'])
    assert broker.order_calls == 1  # Reconnect/repeated wake doesn't duplicate a valid order.
    await service.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('restart_at_open', [False, True])
async def test_restart_restores_durable_schedule_without_jq(api, timed_clock, monkeypatch, restart_at_open):
    service, broker, account, _ = api
    await service.ensure_account(account, 'default', {'strategy_id': 'good_etf'})
    monkeypatch.setattr(service, '_schedule_open_resume', lambda strategy_id: None)
    accepted = await service.submit_targets(account, 'default', request(timed_clock))
    await service.close()
    assert broker.order_calls == 0
    timed_clock[0] = timed_clock[0].replace(minute=30 if restart_at_open else 28)
    restored = SQLiteStrategyAPI(service.config, broker, _capabilities(), FakeData())
    release = asyncio.Event()

    async def sleep(delay):
        assert delay == 120
        await release.wait()

    monkeypatch.setattr(api_module.asyncio, 'sleep', sleep)
    assert await restored.startup_check(account, 'default')
    if not restart_at_open:
        assert broker.order_calls == 0 and len(restored._opening_resume_tasks) == 1
        timed_clock[0] = timed_clock[0].replace(minute=30)
        release.set()
    await drain_execution(restored)
    assert broker.order_calls == 1
    assert restored.get_intent({'strategy_id': 'good_etf'})['intent_id'] == accepted['intent']['intent_id']
    await restored.close()


@pytest.mark.asyncio
async def test_yesterday_schedule_is_canceled_not_executed_on_restart(api, timed_clock, monkeypatch):
    service, broker, account, _ = api
    await service.ensure_account(account, 'default', {'strategy_id': 'good_etf'})
    monkeypatch.setattr(service, '_schedule_open_resume', lambda strategy_id: None)
    accepted = await service.submit_targets(account, 'default', request(timed_clock))
    await service.close()
    timed_clock[0] = (timed_clock[0] + timedelta(days=1)).replace(minute=30)
    restored = SQLiteStrategyAPI(service.config, broker, _capabilities(), FakeData())
    assert await restored.startup_check(account, 'default')
    await drain_execution(restored)
    assert broker.order_calls == 0
    assert restored.get_intent({'strategy_id': 'good_etf', 'intent_id': accepted['intent']['intent_id']})['state'] == 'CANCELED'
    await restored.close()


@pytest.mark.asyncio
async def test_canceled_schedule_and_disabled_trading_cannot_start(api, timed_clock, monkeypatch):
    service, broker, account, _ = api
    await service.ensure_account(account, 'default', {'strategy_id': 'good_etf'})
    monkeypatch.setattr(service, '_schedule_open_resume', lambda strategy_id: None)
    accepted = await service.submit_targets(account, 'default', request(timed_clock))
    canceled = await service.cancel_intent(account, 'default', {
        'strategy_id': 'good_etf', 'intent_id': accepted['intent']['intent_id'],
    })
    assert canceled['canceled']
    timed_clock[0] = timed_clock[0].replace(minute=30)
    await service._resume_intent(accepted['intent']['intent_id'])
    assert broker.order_calls == 0 and broker.cancel_calls == []
    await service.close()
    blocked = SQLiteStrategyAPI(replace(service.config, trading_enabled=False), broker, _capabilities(), FakeData())
    with pytest.raises(Exception, match='global trading switch is disabled'):
        await blocked.submit_targets(account, 'default', dict(request(timed_clock), idempotency_key='blocked'))
    assert not blocked._opening_resume_tasks and broker.order_calls == 0
    await blocked.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('execute_at', ['2026-10-14T09:30:00+08:00',
                                      '2026-10-16T09:30:00+08:00',
                                      '2026-10-15T12:00:00+08:00'])
async def test_invalid_scheduling_time_cannot_create_an_intent(api, timed_clock, execute_at):
    service, broker, account, _ = api
    with pytest.raises(ValueError, match='trading-session time today'):
        await service.submit_targets(account, 'default', dict(request(timed_clock), execute_at=execute_at))
    assert service.planner.active_intents() == () and broker.order_calls == 0
    await service.close()


@pytest.mark.asyncio
async def test_future_client_snapshot_cannot_preplan_a_scheduled_order(api, timed_clock):
    service, broker, account, _ = api
    await service.ensure_account(account, 'default', {'strategy_id': 'good_etf'})
    with pytest.raises(Exception, match='from the future'):
        await service.submit_targets(account, 'default', dict(
            request(timed_clock), as_of='2026-10-15T09:30:00+08:00',
        ))
    assert service.planner.active_intents() == () and broker.order_calls == 0
    await service.close()


@pytest.mark.asyncio
async def test_timed_target_idempotency_cannot_change_execute_time(api, timed_clock, monkeypatch):
    service, broker, account, _ = api
    await service.ensure_account(account, 'default', {'strategy_id': 'good_etf'})
    monkeypatch.setattr(service, '_schedule_open_resume', lambda strategy_id: None)
    original = request(timed_clock)
    accepted = await service.submit_targets(account, 'default', original)
    changed = dict(original, execute_at='2026-10-15T09:31:00+08:00')
    with pytest.raises(Exception, match='different execute_at'):
        await service.submit_targets(account, 'default', changed)
    reused = await service.submit_targets(account, 'default', dict(changed, reuse_existing=True))
    assert reused['intent']['intent_id'] == accepted['intent']['intent_id']
    assert reused['intent']['execute_at'] == original['execute_at']
    assert broker.order_calls == 0
    await service.close()


@pytest.mark.asyncio
async def test_preflight_crossing_the_open_starts_worker_not_a_past_timer(api, timed_clock, monkeypatch):
    service, broker, account, _ = api
    await service.ensure_account(account, 'default', {'strategy_id': 'good_etf'})

    async def slow_quotes(*args):
        timed_clock[0] = timed_clock[0].replace(minute=30)
        return {}

    monkeypatch.setattr(service, '_execution_quotes', slow_quotes)
    accepted = await service.submit_targets(account, 'default', request(timed_clock))
    assert not service._opening_resume_tasks
    assert accepted['intent']['execute_at'] == '2026-10-15T09:30:00+08:00'
    await drain_execution(service)
    assert broker.order_calls == 1
    await service.close()
