import asyncio
from datetime import timedelta

import pytest

from bullet_trade.server.config import AccountConfig
from bullet_trade.server.adapters.base import AccountContext
from bullet_trade.server.strategy import (
    BrokerCashMismatchError,
    BrokerCapabilityProfile,
    CapabilityState,
    ConditionalLimitExecution,
    ExecutionRequest,
    LedgerInvariantError,
    SQLiteStrategyAPI,
    StrategyAPIConfig,
    money_to_units,
    MINI_QMT_CAPABILITIES,
    execution_request_to_wire,
)
from bullet_trade.server.strategy.schema import connect_database
from bullet_trade.server.feishu_notifier import TargetBuyPlanNotification


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
        self.positions = []
        self.orders = []
        self.trades = []
        self.order_calls = 0
        self.cancel_calls = []

    async def get_account_info(self, account):
        return {"available_cash": self.cash}

    async def get_positions(self, account):
        return list(self.positions)

    async def list_orders(self, account, filters=None):
        return list(self.orders)

    async def list_trades(self, account, filters=None):
        return list(self.trades)

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


class CallbackData(FakeData):
    def __init__(self):
        self.listener = None
        self.subscriptions = []
        self.ask_price = 10.03

    def add_tick_listener(self, callback):
        self.listener = callback

    async def subscribe_execution_quotes(self, symbols):
        self.subscriptions.append(tuple(symbols))

    async def replace_execution_quotes(self, owner, symbols):
        replacement = tuple(symbols)
        if not self.subscriptions or self.subscriptions[-1] != replacement:
            self.subscriptions.append(replacement)

    async def get_current_tick(self, security):
        return {
            "last_price": 10.0,
            "bidPrice": [9.99],
            "askPrice": [self.ask_price],
        }

    def emit(self, payload):
        assert self.listener is not None
        self.listener(payload)


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
async def test_new_strategy_rebases_external_cash_and_uses_remaining_pool(api):
    service, broker, account, _ = api
    await service.ensure_account(
        account,
        "default",
        {"strategy_id": "existing", "initial_capital": 10_000},
    )
    broker.cash = 19_999.0

    result = await service.ensure_account(
        account,
        "default",
        {"strategy_id": "new-strategy", "initial_capital": 9_000},
    )

    assert result["created"] is True
    assert result["account"]["available_cash"] == 9_000.0
    connection = connect_database(service.database_path)
    try:
        pool = connection.execute(
            """
            SELECT unallocated_cash_units, reserved_cash_units
            FROM cash_pools WHERE physical_account_id = ?
            """,
            ("qmt:default",),
        ).fetchone()
    finally:
        connection.close()
    assert tuple(pool) == (money_to_units("999"), 0)


@pytest.mark.asyncio
async def test_new_strategy_rejects_when_remaining_real_cash_is_insufficient(api):
    service, broker, account, _ = api
    await service.ensure_account(
        account,
        "default",
        {"strategy_id": "existing", "initial_capital": 10_000},
    )
    broker.cash = 19_999.0

    with pytest.raises(LedgerInvariantError) as exc_info:
        await service.ensure_account(
            account,
            "default",
            {"strategy_id": "new-strategy", "initial_capital": 10_000},
        )

    message = str(exc_info.value)
    assert "ledger_unallocated_available_cash=9999.0000" in message
    assert "requested_initial_capital=10000.0000" in message
    assert "shortfall=1.0000" in message
    assert "broker_available_cash=19999.0000" in message
    assert "new_strategy_id=new-strategy" in message


@pytest.mark.asyncio
async def test_new_strategy_reports_when_broker_cannot_cover_existing_strategies(api):
    service, broker, account, _ = api
    await service.ensure_account(
        account,
        "default",
        {"strategy_id": "existing", "initial_capital": 10_000},
    )
    broker.cash = 9_999.0

    with pytest.raises(BrokerCashMismatchError) as exc_info:
        await service.ensure_account(
            account,
            "default",
            {"strategy_id": "new-strategy", "initial_capital": 1_000},
        )

    message = str(exc_info.value)
    assert "broker_available_cash=9999.0000" in message
    assert "ledger_strategy_available_cash=10000.0000" in message
    assert "shortfall=1.0000" in message
    assert "requested_initial_capital=1000.0000" in message
    assert "new_strategy_id=new-strategy" in message


@pytest.mark.asyncio
async def test_ensure_account_ignores_unrelated_shared_account_activity(api):
    service, broker, account, _ = api
    broker.positions = [
        {
            "security": "159208.SZ",
            "amount": -21949,
            "closeable_amount": -21949,
        },
        {
            "security": "510300.SH",
            "amount": 1000,
            "closeable_amount": 1000,
        },
    ]
    broker.orders = [
        {
            "order_id": "manual-order",
            "security": "510300.XSHG",
            "status": "filled",
            "order_remark": "another-strategy",
        }
    ]
    broker.trades = [
        {
            "trade_id": "manual-trade",
            "order_id": "manual-order",
            "security": "510300.XSHG",
            "amount": 1000,
            "price": 4.0,
        }
    ]

    result = await service.ensure_account(
        account,
        "default",
        {"strategy_id": "good_etf", "initial_capital": "10000"},
    )

    assert result["created"] is True
    assert result["reconciliation"]["state"] == "READY"
    details = result["reconciliation"]["details"]
    assert details["ignored_broker_order_count"] == 1
    assert details["ignored_broker_trade_count"] == 1


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
    assert broker.cancel_calls == []
    assert second["cancel_requested_order_ids"] == []
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


def test_target_buy_plan_notification_does_not_trade_or_write_ledger(api):
    service, broker, _, notifications = api

    result = service.notify_target_buy_plan(
        {
            "strategy_id": "good_etf",
            "mode": "JQ",
            "occurred_at": "2026-08-13T09:30:00+08:00",
            "items": [
                {
                    "security": "510050.XSHG",
                    "quantity": 1000,
                    "amount": "2500.00",
                    "reference_price": "2.5000",
                },
                {
                    "security": "159915.XSHE",
                    "quantity": 500,
                    "amount": "750.00",
                    "reference_price": "1.5000",
                },
            ],
        }
    )

    assert result == {
        "accepted": True,
        "item_count": 2,
        "total_amount": 3250.0,
    }
    assert broker.order_calls == 0
    assert len(notifications) == 1
    notification = notifications[0]
    assert isinstance(notification, TargetBuyPlanNotification)
    assert notification.mode == "JQ"
    assert notification.items[0].quantity == 1000
    with pytest.raises(Exception, match="not found"):
        service.repository.get_strategy_account("good_etf")


@pytest.mark.asyncio
async def test_conditional_target_is_resumed_by_native_tick_callback(tmp_path):
    broker = FakeBroker()
    data = CallbackData()
    service = SQLiteStrategyAPI(
        StrategyAPIConfig(
            database_path=tmp_path / "callback.db",
            trading_enabled=True,
            cash_buffer_units=0,
            max_age=timedelta(minutes=5),
        ),
        broker,
        _capabilities(),
        data,
    )
    account = AccountContext(AccountConfig("default", "qmt-account"))
    await service.ensure_account(
        account,
        "default",
        {"strategy_id": "good_etf", "initial_capital": "10000"},
    )

    result = await service.submit_targets(
        account,
        "default",
        {
            "strategy_id": "good_etf",
            "idempotency_key": "conditional-callback",
            "weights": {SECURITY: "0.5"},
            "marks": {SECURITY: "10"},
            "execution": execution_request_to_wire(
                ExecutionRequest(
                    style=ConditionalLimitExecution(2_000)
                )
            ),
        },
    )

    assert result["planned_orders"] == []
    assert broker.order_calls == 0
    assert data.subscriptions == [(SECURITY,)]

    data.emit(
        {
            "stockCode": "510050.SH",
            "lastPrice": 10.0,
            "bidPrice": [9.99],
            "askPrice": [10.01],
        }
    )
    for _ in range(100):
        if broker.order_calls:
            break
        await asyncio.sleep(0.01)

    assert broker.order_calls == 1


@pytest.mark.asyncio
async def test_idle_intent_can_be_canceled_before_risk_replacement(tmp_path):
    broker = FakeBroker()
    data = CallbackData()
    service = SQLiteStrategyAPI(
        StrategyAPIConfig(
            database_path=tmp_path / "strategy-cancel.db",
            trading_enabled=True,
            cash_buffer_units=0,
            max_age=timedelta(minutes=5),
        ),
        broker,
        _capabilities(),
        data,
    )
    account = AccountContext(AccountConfig("default", "qmt-account"))
    await service.ensure_account(
        account,
        "default",
        {"strategy_id": "good_etf", "initial_capital": 10_000},
    )
    submitted = await service.submit_targets(
        account,
        "default",
        {
            "strategy_id": "good_etf",
            "idempotency_key": "waiting-to-cancel",
            "weights": {SECURITY: "0.5"},
            "marks": {SECURITY: "10"},
            "execution": execution_request_to_wire(
                ExecutionRequest(
                    style=ConditionalLimitExecution(2_000)
                )
            ),
        },
    )

    canceled = await service.cancel_intent(
        account,
        "default",
        {
            "strategy_id": "good_etf",
            "intent_id": submitted["intent"]["intent_id"],
        },
    )

    assert canceled["canceled"] is True
    assert canceled["intent"]["state"] == "CANCELED"
    assert data.subscriptions[-1] == ()


@pytest.mark.parametrize(
    ("legacy_mode", "normalised_mode"),
    [
        ("SHADOW", "JQ"),
        ("SIGNAL_ONLY", "JQ"),
        ("JQ_PAPER", "JQ"),
        ("REMOTE", "QMT_REMOTE"),
        ("LIVE", "QMT_REMOTE"),
    ],
)
def test_target_buy_plan_accepts_legacy_mode_aliases(
    api, legacy_mode, normalised_mode
):
    service, broker, _, notifications = api

    service.notify_target_buy_plan(
        {
            "strategy_id": "good_etf",
            "mode": legacy_mode,
            "items": [{"security": SECURITY, "quantity": 100, "amount": "250.00"}],
        }
    )

    assert broker.order_calls == 0
    assert notifications[0].mode == normalised_mode


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
async def test_unverified_capabilities_are_deferred_while_trading_is_disabled(tmp_path):
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
    assert result["reconciliation"]["state"] == "READY"
    assert result["reconciliation"]["details"][
        "capability_verification_required"
    ] is False
    assert notifications == []


@pytest.mark.asyncio
async def test_unverified_capabilities_block_when_trading_is_enabled(tmp_path):
    broker = FakeBroker()
    notifications = []
    service = SQLiteStrategyAPI(
        StrategyAPIConfig(tmp_path / "blocked-live.db", trading_enabled=True),
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
    assert notifications[-1].strategy_id == "good_etf"
    assert "capability" in notifications[-1].detail
