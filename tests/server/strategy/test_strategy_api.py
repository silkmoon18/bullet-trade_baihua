import asyncio
from datetime import datetime, timedelta

import pytest

import bullet_trade.server.strategy.api as strategy_api_module
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
    XTQUANT_DIRECT_CAPABILITIES,
    execution_request_to_wire,
)
from bullet_trade.server.strategy.schema import connect_database
from bullet_trade.server.feishu_notifier import TargetBuyPlanNotification
from bullet_trade.server.strategy.domain import SHANGHAI_TZ


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
            "dt": datetime.now(SHANGHAI_TZ).isoformat(),
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
            enabled_strategy_ids=("good_etf",),
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
        "security_names": {SECURITY: "测试ETF"},
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
    assert any(
        item.event == "ORDER_SUBMITTED" and item.security_name == "测试ETF"
        for item in notifications
    )
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
                    "security_name": "上证50ETF",
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
    assert notification.items[0].security_name == "上证50ETF"
    with pytest.raises(Exception, match="not found"):
        service.repository.get_strategy_account("good_etf")


@pytest.mark.asyncio
async def test_conditional_target_is_resumed_by_native_tick_callback(
    tmp_path, monkeypatch
):
    log_messages = []
    monkeypatch.setattr(
        strategy_api_module.logger,
        "info",
        lambda message, *args: log_messages.append(message % args),
    )
    broker = FakeBroker()
    data = CallbackData()
    service = SQLiteStrategyAPI(
        StrategyAPIConfig(
            database_path=tmp_path / "callback.db",
            trading_enabled=True,
            enabled_strategy_ids=("good_etf",),
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
            "510050.SH": {
                "lastPrice": 10.0,
                "bidPrice": [9.99],
                "askPrice": [10.01],
            }
        }
    )
    await asyncio.sleep(0.02)
    assert broker.order_calls == 0

    # xtdata.subscribe_quote sends {stock: [tick, ...]}.  The first tick
    # reaches the fixed boundary while the later tick moves back outside it;
    # the crossing must not be lost when the callback contains a batch.
    data.emit(
        {
            "510050.SH": [
                {
                    "lastPrice": 10.0,
                    "bidPrice": [9.99],
                    "askPrice": [10.01],
                    "time": int(datetime.now(SHANGHAI_TZ).timestamp() * 1000),
                },
                {
                    "lastPrice": 10.03,
                    "bidPrice": [10.02],
                    "askPrice": [10.03],
                    "time": int(datetime.now(SHANGHAI_TZ).timestamp() * 1000),
                },
            ]
        }
    )
    for _ in range(100):
        if broker.order_calls:
            break
        await asyncio.sleep(0.01)

    assert broker.order_calls == 1
    assert len(log_messages) == 1
    assert log_messages[0].startswith(
        "StrategyLedger 首次收到执行行情 | 510050.XSHG | "
    )
    assert "行情时间=" in log_messages[0]
    assert "接收时间=" in log_messages[0]
    assert "延迟=" in log_messages[0]

    last_log_at = service._quote_last_log_at[SECURITY]
    latest_quote = service._quote_cache[SECURITY]
    service._log_execution_quote_heartbeat(
        SECURITY,
        latest_quote,
        last_log_at + timedelta(seconds=59),
        first=False,
    )
    assert len(log_messages) == 1
    service._log_execution_quote_heartbeat(
        SECURITY,
        latest_quote,
        last_log_at + timedelta(seconds=60),
        first=False,
    )
    assert log_messages[1].startswith(
        "StrategyLedger 执行行情心跳 | 510050.XSHG | "
    )


@pytest.mark.asyncio
async def test_qmt_mark_preserves_timestamp_and_rejects_stale_tick(
    api, monkeypatch
):
    service, _, _, _ = api
    request_time = datetime(2026, 9, 2, 10, 30, tzinfo=SHANGHAI_TZ)

    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return request_time if tz is not None else request_time.replace(
                tzinfo=None
            )

    monkeypatch.setattr(strategy_api_module, "datetime", FixedDatetime)

    class TimestampedData:
        def __init__(self, tick_time):
            self.tick_time = tick_time

        async def get_current_tick(self, security):
            return {"last_price": 10.0, "time": self.tick_time}

    fresh_time = request_time - timedelta(seconds=30)
    service.data_provider = TimestampedData(
        int(fresh_time.timestamp() * 1000)
    )
    marks = await service._marks(
        None, request_time, "good_etf", (SECURITY,)
    )
    assert marks[SECURITY].as_of == fresh_time

    stale_time = request_time - timedelta(minutes=6)
    service.data_provider = TimestampedData(stale_time.isoformat())
    with pytest.raises(ValueError, match="QMT mark is stale"):
        await service._marks(
            None, request_time, "good_etf", (SECURITY,)
        )


def test_valuation_marks_allow_session_break_but_execution_quotes_stay_strict(
    api,
):
    service, _, _, _ = api
    lunch = datetime(2026, 9, 2, 12, 56, tzinfo=SHANGHAI_TZ)
    lunch_mark = datetime(2026, 9, 2, 11, 29, 59, tzinfo=SHANGHAI_TZ)
    after_close = datetime(2026, 9, 2, 16, 57, tzinfo=SHANGHAI_TZ)
    close_mark = datetime(2026, 9, 2, 15, 0, 38, tzinfo=SHANGHAI_TZ)

    assert service._valuation_mark_is_fresh(lunch_mark, lunch) is True
    assert service._valuation_mark_is_fresh(close_mark, after_close) is True
    assert service._market_time_is_fresh(lunch_mark, lunch) is False
    assert service._market_time_is_fresh(close_mark, after_close) is False


@pytest.mark.asyncio
async def test_idle_intent_can_be_canceled_before_risk_replacement(tmp_path):
    broker = FakeBroker()
    data = CallbackData()
    service = SQLiteStrategyAPI(
        StrategyAPIConfig(
            database_path=tmp_path / "strategy-cancel.db",
            trading_enabled=True,
            enabled_strategy_ids=("good_etf",),
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


@pytest.mark.asyncio
async def test_midnight_expiry_cancels_working_order_and_closes_intent(tmp_path):
    class CancelingBroker(FakeBroker):
        def __init__(self):
            super().__init__()
            self.reserved_by_order = {}

        async def place_order(self, account, payload):
            before = self.cash
            result = await super().place_order(account, payload)
            self.reserved_by_order[result["order_id"]] = before - self.cash
            return result

        async def cancel_order(self, account, order_id):
            result = await super().cancel_order(account, order_id)
            for row in self.orders:
                if row["order_id"] == order_id:
                    row["status"] = "canceled"
            self.cash += self.reserved_by_order.pop(order_id, 0.0)
            return result

    broker = CancelingBroker()
    service = SQLiteStrategyAPI(
        StrategyAPIConfig(
            database_path=tmp_path / "strategy-midnight.db",
            trading_enabled=True,
            enabled_strategy_ids=("good_etf",),
            cash_buffer_units=0,
            max_age=timedelta(minutes=5),
        ),
        broker,
        _capabilities(),
        FakeData(),
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
            "idempotency_key": "midnight-expiry",
            "weights": {SECURITY: "0.5"},
            "marks": {SECURITY: "10"},
        },
    )
    result = await service.expire_previous_day_intents(
        datetime.now(SHANGHAI_TZ) + timedelta(days=1)
    )

    assert result.expired == 1
    assert result.cancel_requests == 1
    assert result.canceled == 1
    assert result.pending == 0
    assert len(broker.cancel_calls) == 1
    restored = service.get_intent(
        {
            "strategy_id": "good_etf",
            "intent_id": submitted["intent"]["intent_id"],
        }
    )
    assert restored["state"] == "CANCELED"
    assert restored["orders"][0]["state"] == "CANCELED"
    assert service.repository.get_strategy_account(
        "good_etf"
    ).reserved_cash_units == 0


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
        XTQUANT_DIRECT_CAPABILITIES,
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
        StrategyAPIConfig(
            tmp_path / "blocked-live.db",
            trading_enabled=True,
            enabled_strategy_ids=("good_etf",),
        ),
        broker,
        XTQUANT_DIRECT_CAPABILITIES,
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
