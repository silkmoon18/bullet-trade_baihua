"""Warmup capacity and report booking must not depend on idle quote valuation."""

from datetime import datetime, timedelta
import pytest

import bullet_trade.server.strategy.api as api_module
from bullet_trade.server.strategy.domain import SHANGHAI_TZ
from bullet_trade.server.strategy.schema import connect_database
from tests.server.strategy.test_strategy_api import (
    SECURITY, api, execution_clock, _seed_sellable_position, submit_and_drain,
)
from tests.server.strategy.test_scheduled_targets import timed_clock


@pytest.mark.asyncio
async def test_large_legacy_candidate_pool_preserves_small_holdings_subscription(api, monkeypatch):
    service, broker, account, _ = api
    calls = []

    class Data:
        async def replace_execution_quotes(self, owner, symbols):
            assert len(symbols) <= 100
            calls.append((owner, tuple(symbols)))

        async def get_current_tick(self, security):
            assert security == SECURITY
            return {"lastPrice": 10, "time": datetime.now(SHANGHAI_TZ).isoformat()}

    service.data_provider = Data()
    monkeypatch.setattr(service, "_held_securities", lambda _: (SECURITY,))
    result = await service.prepare_session(account, "default", {
        "strategy_id": "good_etf",
        "candidate_securities": ["{:06d}.XSHG".format(510000 + n) for n in range(400)],
    })
    assert calls == [("preopen:good_etf", (SECURITY,))]
    assert result["candidate_count"] == 400
    assert result["subscribed_candidates"] == 0
    assert result["subscribed_holdings"] == 1
    assert result["quote_ready"] is True
    assert not service.planner.active_intents() and broker.order_calls == 0


@pytest.mark.asyncio
async def test_empty_holdings_release_old_prewarm_owner_and_probe_index_once(api, monkeypatch):
    service, broker, account, _ = api
    service._preopen_quote_owners.add("good_etf")
    calls = []

    class Data:
        async def replace_execution_quotes(self, owner, symbols):
            calls.append((owner, tuple(symbols)))

        async def get_current_tick(self, security):
            calls.append(("probe", security))
            return {"lastPrice": 10, "time": datetime.now(SHANGHAI_TZ).isoformat()}

    service.data_provider = Data()
    result = await service.prepare_session(account, "default", {
        "strategy_id": "good_etf", "candidate_securities": [SECURITY],
    })
    assert calls == [("preopen:good_etf", ()), ("probe", "000300.XSHG")]
    assert not service._preopen_quote_owners
    assert result["subscribed_candidates"] == result["subscribed_holdings"] == 0
    assert result["quote_ready"] is True and broker.order_calls == 0


@pytest.mark.asyncio
async def test_idle_report_reconciles_without_valuation_or_extra_quote_query(api, monkeypatch):
    service, broker, account, _ = api
    await _seed_sellable_position(api, monkeypatch, 500)
    assert service.planner.active_intents("good_etf") == ()
    synchronized = []
    original = service._synchronize

    async def synchronize(*args):
        synchronized.append(True)
        return await original(*args)

    async def no_valuation(*args, **kwargs):
        pytest.fail("idle report must not fetch valuation quotes")

    monkeypatch.setattr(service, "_synchronize", synchronize)
    monkeypatch.setattr(service, "_marks", no_valuation)
    await service._handle_broker_event("default", "trade")
    await service._handle_broker_event("default", "order")
    assert synchronized == [True, True]
    assert service.startup_ready is True and broker.order_calls == 1
    db = connect_database(service.database_path)
    try:
        assert db.execute("SELECT count(*) FROM fills").fetchone()[0] == 1
        assert db.execute("SELECT total_qty FROM positions").fetchone()[0] == 500
    finally:
        db.close()


@pytest.mark.asyncio
async def test_idle_cash_shortage_remains_blocked_without_valuation_and_recovers(api, monkeypatch):
    service, broker, account, _ = api
    await service.ensure_account(account, 'default', {'strategy_id': 'good_etf'})

    async def no_valuation(*args, **kwargs):
        pytest.fail('Idle cash reconciliation must not fetch valuation prices')

    monkeypatch.setattr(service, '_marks', no_valuation)
    broker.cash = 0
    await service._handle_broker_event('default', 'trade')
    result = service.get_reconciliation('default', {'strategy_id': 'good_etf'})['reconciliation']
    assert result['state'] == 'BLOCKED'
    assert any(item.startswith('broker_cash_insufficient:') for item in result['details']['blockers'])
    assert not service.startup_ready and broker.order_calls == 0 and not broker.cancel_calls
    broker.cash = 20000
    await service._handle_broker_event('default', 'trade')
    assert service.get_reconciliation('default', {'strategy_id': 'good_etf'})['reconciliation']['state'] == 'READY'
    assert broker.order_calls == 0 and not broker.cancel_calls


@pytest.mark.asyncio
@pytest.mark.parametrize("offset", [-5400, 60])
async def test_stale_or_future_valuation_wait_keeps_intent_and_never_dispatches(
    api, monkeypatch, timed_clock, offset,
):
    service, broker, account, _ = api
    timed_clock[0] = timed_clock[0].replace(hour=13, minute=0)
    warnings = []
    monkeypatch.setattr(api_module.logger, 'warning', lambda message, *args: warnings.append(message % args))
    await service.ensure_account(account, "default", {"strategy_id": "good_etf"})
    # Create the ordinary accepted plan, but delay its owned worker to model
    # a target resuming before the first valid afternoon tick.
    monkeypatch.setattr(service, "_queue_target_execution", lambda _, coroutine: coroutine.close())
    result = await service.submit_targets(account, "default", {
        "strategy_id": "good_etf", "idempotency_key": "wait-quote",
        "weights": {SECURITY: 0.5}, "marks": {SECURITY: 10},
    })
    intent_id = result["intent"]["intent_id"]
    original_marks = service._marks
    quote_time = timed_clock[0] + timedelta(seconds=offset)

    class Data:
        async def get_current_tick(self, _):
            return {"lastPrice": 10, "time": quote_time.isoformat()}

    service.data_provider = Data()

    async def current_valuation(raw, as_of, strategy_id, targets):
        return await original_marks(None, as_of, strategy_id, (SECURITY,))

    monkeypatch.setattr(service, "_marks", current_valuation)
    for _ in range(3):
        await service._resume_intent_locked(intent_id)
    assert broker.order_calls == 0
    assert len(service.planner.active_intents("good_etf")) == 1
    assert len([line for line in warnings if "QMT目标等待有效估值行情" in line]) == 1
    # Once existing wakeups observe a current tick, the original plan proceeds
    # exactly once. No added polling, re-selection or replacement target.
    quote_time = timed_clock[0]
    await service._resume_intent_locked(intent_id)
    assert broker.order_calls == 1
    assert not service._valuation_wait_logs
    await service._resume_intent_locked(intent_id)
    assert broker.order_calls == 1


@pytest.mark.parametrize("value", [None, "", "bad\nlog", "x" * 65, "版本"])
def test_runtime_build_labels_do_not_allow_log_injection(value):
    assert api_module._runtime_build_label(value) == "unknown"


@pytest.mark.asyncio
async def test_acceptance_logs_exact_effective_offset_and_build(api, monkeypatch):
    service, _, account, _ = api
    logs = []
    monkeypatch.setattr(api_module.logger, 'info', lambda message, *args: logs.append(message % args))
    await service.ensure_account(account, "default", {"strategy_id": "good_etf"})
    result = await submit_and_drain(service, account, "default", {
        "strategy_id": "good_etf", "idempotency_key": "build-policy",
        "weights": {SECURITY: 0.5}, "marks": {SECURITY: 10},
        "runtime_build_id": "test-helper-v1",
        "execution": {"schema_version": 2, "style": {"type": "LIMIT", "price_band_ppm": 10000},
                      "follow_up": "UNTIL_FILLED_TODAY", "repricing": "KEEP_ORIGINAL"},
    })
    assert result["server_runtime_build_id"] == api_module.STRATEGY_RUNTIME_BUILD_ID
    assert result["effective_execution"]["style"]["price_band_ppm"] == 10000
    assert any("helper_build=test-helper-v1" in line and "买入偏移ppm=10000" in line for line in logs)
