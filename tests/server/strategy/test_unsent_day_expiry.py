"""DAY validity fences local outbox recovery, without touching broker facts."""
from dataclasses import replace
from datetime import timedelta

import pytest

from bullet_trade.server.strategy import OrderState
from bullet_trade.server.strategy.schema import connect_database
from tests.server.strategy.test_planner_executor import _setup, ACCOUNT, A


@pytest.mark.asyncio
async def test_dispatch_day_guard_does_not_claim_or_send_a_previous_day_order(tmp_path):
    database, _, _, _, planner, snapshot, marks, now = _setup(tmp_path)
    result = planner.submit_target_weights(ACCOUNT, 'unsent', {A: .5}, snapshot, marks, now)
    async def forbidden(payload):
        pytest.fail('An expired order must not reach the broker')
    assert await planner.dispatch_next(forbidden, ACCOUNT, trading_day=(now + timedelta(days=1)).date()) is None
    db = connect_database(database)
    try:
        assert db.execute('SELECT state FROM outbox').fetchone()[0] == 'PENDING'
        assert db.execute('SELECT state FROM strategy_orders').fetchone()[0] == 'PENDING_SUBMIT'
    finally:
        db.close()
    assert planner.get_intent(result.intent.intent_id).state.value == 'EXECUTING'


@pytest.mark.parametrize('after_fence_crash', [False, True])
def test_expiry_releases_cash_once_even_if_recovery_crashed_after_fencing(tmp_path, after_fence_crash):
    database, repository, _, _, planner, snapshot, marks, now = _setup(tmp_path)
    result = planner.submit_target_weights(ACCOUNT, 'unsent', {A: .5}, snapshot, marks, now)
    before = repository.get_strategy_account(ACCOUNT)
    assert before.reserved_cash_units > 0
    tomorrow = now + timedelta(days=1)
    if after_fence_crash:
        assert planner._operations.expire_unsent_intent_orders(result.intent.intent_id, tomorrow.date())
        assert repository.get_strategy_account(ACCOUNT).reserved_cash_units == before.reserved_cash_units
    advance = planner.advance_intent(result.intent.intent_id, snapshot, marks, tomorrow)
    assert advance.intent.state.value == 'CANCELED' and not advance.orders
    after = repository.get_strategy_account(ACCOUNT)
    assert after.cash_units == before.cash_units and after.reserved_cash_units == 0
    planner.advance_intent(result.intent.intent_id, snapshot, marks, tomorrow)
    assert repository.get_strategy_account(ACCOUNT) == after
    db = connect_database(database)
    try:
        assert db.execute('SELECT state FROM strategy_orders').fetchone()[0] == 'CANCELED'
        assert db.execute('SELECT state FROM outbox').fetchone()[0] == 'FAILED'
    finally:
        db.close()


def test_expiry_never_discards_an_unknown_submission_or_its_reservation(tmp_path):
    _, repository, _, _, planner, snapshot, marks, now = _setup(tmp_path)
    result = planner.submit_target_weights(ACCOUNT, 'unknown', {A: .5}, snapshot, marks, now)
    planner._booking.mark_order_submit_unknown(ACCOUNT, result.orders[0].order_id, 'test unknown')
    reserved = repository.get_strategy_account(ACCOUNT).reserved_cash_units
    assert not planner.expire_unsent_orders(result.intent.intent_id, (now + timedelta(days=1)).date())
    assert repository.get_strategy_account(ACCOUNT).reserved_cash_units == reserved
    assert planner._working_orders(ACCOUNT)[0]['state'] == OrderState.SUBMIT_UNKNOWN.value
