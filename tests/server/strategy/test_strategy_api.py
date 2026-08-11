from datetime import timedelta

import pytest

from bullet_trade.server.config import AccountConfig
from bullet_trade.server.adapters.base import AccountContext
from bullet_trade.server.strategy import (
    BrokerCapabilityProfile,
    CapabilityState,
    SQLiteStrategyAPI,
    StrategyAPIConfig,
    money_to_units,
    MINI_QMT_CAPABILITIES,
)
from bullet_trade.server.strategy.schema import connect_database


SECURITY = "510050.XSHG"


def _capabilities():
    supported = CapabilityState.SUPPORTED
    return BrokerCapabilityProfile(
        adapter_kind="TEST",
        client_tag_roundtrip=supported,
        stable_order_id=supported,
        stable_trade_id=supported,
        trade_order_link=supported,
        direct_trade_side=supported,
        order_side_for_trade=supported,
        fee_fields=supported,
        order_status=supported,
        current_orders_query=supported,
        current_trades_query=supported,
        working_orders_query=supported,
        order_lookback_days=1,
        trade_lookback_days=1,
    )


class FakeBroker:
    def __init__(self):
        self.cash = 20_000.0
        self.orders = []
        self.order_calls = 0
        self.cancel_calls = []

    async def get_account_info(self, account):
        return {"available_cash": self.cash}

    async def get_positions(self, account):
        return []

    async def list_orders(self, account, filters=None):
        return list(self.orders)

    async def list_trades(self, account, filters=None):
        return []

    async def place_order(self, account, payload):
        self.order_calls += 1
        order_id = "broker-{}".format(self.order_calls)
        price = float(payload["style"]["price"])
        amount = int(payload["amount"])
        self.cash -= price * amount + 5.0
        self.orders.append(
            {
                "order_id": order_id,
                "security": payload["security"],
                "status": "open",
                "side": payload["side"],
                "order_remark": payload["order_remark"],
            }
        )
        return {"order_id": order_id}

    async def cancel_order(self, account, order_id):
        self.cancel_calls.append(order_id)
        return {"order_id": order_id, "canceled": True}


class FakeData:
    async def get_current_tick(self, security):
        return {"last_price": 10.0}


@pytest.fixture
def api(tmp_path):
    broker = FakeBroker()
    notifications = []
    service = SQLiteStrategyAPI(
        StrategyAPIConfig(
            database_path=tmp_path / "strategy.db",
            trading_enabled=True,
            cash_buffer_units=0,
            max_age=timedelta(minutes=5),
        ),
        broker,
        _capabilities(),
        FakeData(),
        notifications.append,
    )
    account = AccountContext(AccountConfig("default", "qmt-account"))
    return service, broker, account, notifications


@pytest.mark.asyncio
async def test_ensure_account_and_real_snapshot(api):
    service, _, account, _ = api

    ensured = await service.ensure_account(
        account,
        "default",
        {"strategy_id": "good_etf", "initial_capital": 10_000},
    )
    snapshot = await service.get_snapshot(
        account, "default", {"strategy_id": "good_etf"}
    )

    assert ensured["created"] is True
    assert ensured["account"]["available_cash"] == 10_000.0
    assert snapshot["available_cash"] == 10_000.0
    assert snapshot["total_value"] == 10_000.0
    assert snapshot["nav"] == 1.0
    assert snapshot["reconciliation"]["state"] == "READY"
    service.startup_ready = False
    assert await service.startup_check(account, "default") is True


@pytest.mark.asyncio
async def test_submit_targets_is_idempotent_and_exposes_queries(api):
    service, broker, account, notifications = api
    await service.ensure_account(
        account,
        "default",
        {"strategy_id": "good_etf", "initial_capital": "10000"},
    )
    request = {
        "strategy_id": "good_etf",
        "idempotency_key": "jq-20260811-open",
        "weights": {SECURITY: "0.5"},
        "marks": {SECURITY: "10"},
    }

    first = await service.submit_targets(account, "default", request)
    connection = connect_database(service.database_path)
    try:
        connection.execute(
            "UPDATE strategy_orders SET submitted_at = ?",
            ("2020-01-01T10:00:00+08:00",),
        )
        connection.commit()
    finally:
        connection.close()
    second = await service.submit_targets(account, "default", request)
    intent_id = first["intent"]["intent_id"]
    restored = service.get_intent({"strategy_id": "good_etf"})

    assert broker.order_calls == 1
    assert broker.cancel_calls == ["broker-1"]
    assert second["cancel_requested_order_ids"] == ["broker-1"]
    assert any(item.event == "ORDER_SUBMITTED" for item in notifications)
    assert first["intent"]["intent_id"] == second["intent"]["intent_id"]
    assert restored["intent_id"] == intent_id
    assert restored["weights"] == {SECURITY: 0.5}
    assert first["snapshot"]["available_cash"] == 4_985.0
    assert first["snapshot"]["reserved_cash"] == 5_015.0
    assert service.get_intent(
        {"strategy_id": "good_etf", "intent_id": intent_id}
    )["intent_id"] == intent_id
    assert service.get_reconciliation(
        "default", {"strategy_id": "good_etf"}
    )["reconciliation"]["state"] == "READY"


@pytest.mark.asyncio
async def test_initial_cash_shortage_fails_without_strategy_account(api):
    service, broker, account, _ = api
    broker.cash = 9_999.0

    with pytest.raises(Exception, match="insufficient"):
        await service.ensure_account(
            account,
            "default",
            {"strategy_id": "good_etf", "initial_capital": "10000"},
        )

    with pytest.raises(Exception, match="not found"):
        service.repository.get_strategy_account("good_etf")


def test_money_scale_is_still_exact():
    assert money_to_units("10000") == 100_000_000


@pytest.mark.asyncio
async def test_unverified_capabilities_send_reconciliation_block_card(tmp_path):
    broker = FakeBroker()
    notifications = []
    service = SQLiteStrategyAPI(
        StrategyAPIConfig(tmp_path / "blocked.db"),
        broker,
        MINI_QMT_CAPABILITIES,
        FakeData(),
        notifications.append,
    )
    account = AccountContext(AccountConfig("default", "qmt-account"))
    result = await service.ensure_account(
        account,
        "default",
        {"strategy_id": "good_etf", "initial_capital": "10000"},
    )
    assert result["reconciliation"]["state"] == "BLOCKED"
    assert notifications[-1].event == "RECONCILIATION_BLOCKED"
    assert "capability" in notifications[-1].detail
