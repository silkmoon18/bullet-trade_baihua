import asyncio
from dataclasses import replace
from datetime import date, datetime

from bullet_trade.server.strategy import (
    BrokerAccountSnapshot,
    BrokerCapabilityProfile,
    BrokerOrder,
    BrokerPositionSnapshot,
    CapabilityState,
    OrderSide,
    OrderState,
    ReconciliationState,
    SQLiteCapitalService,
    SQLiteFillBookingService,
    SQLiteReconciliationService,
    SQLiteStrategyRepository,
    collect_async_broker_snapshot,
    money_to_units,
    price_to_units,
)
from bullet_trade.server.strategy.domain import AccountStatus, SHANGHAI_TZ
from bullet_trade.server.strategy.schema import connect_database


ACCOUNT_ID = "good-etf"
PHYSICAL_ID = "qmt-main"
SECURITY = "510050.XSHG"


def _capabilities():
    supported = CapabilityState.SUPPORTED
    return BrokerCapabilityProfile(
        adapter_kind="TEST_QMT",
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


def _services(tmp_path):
    database = tmp_path / "reconciliation.db"
    repository = SQLiteStrategyRepository(database)
    repository.initialize()
    repository.create_physical_account(PHYSICAL_ID, "QMT", "account-1")
    capital = SQLiteCapitalService(database)
    capital.calibrate_broker_available_cash(PHYSICAL_ID, money_to_units("20000"))
    capital.ensure_strategy_account(
        ACCOUNT_ID,
        "good_etf",
        PHYSICAL_ID,
        money_to_units("10000"),
    )
    reconciliation = SQLiteReconciliationService(database, _capabilities())
    return database, repository, capital, reconciliation


def _snapshot(cash, positions=(), orders=(), trades=(), day=date(2026, 8, 11)):
    return BrokerAccountSnapshot(
        available_cash_units=money_to_units(cash),
        positions=tuple(positions),
        orders=tuple(orders),
        trades=tuple(trades),
        as_of=datetime(day.year, day.month, day.day, 15, 0, tzinfo=SHANGHAI_TZ),
    )


def _order(order_id="buy-1", broker_order_id="broker-buy-1"):
    return BrokerOrder(
        order_id=order_id,
        account_id=ACCOUNT_ID,
        intent_id=None,
        client_tag="bt:test:{}".format(order_id),
        security=SECURITY,
        side=OrderSide.BUY,
        requested_qty=1000,
        filled_qty=0,
        state=OrderState.SUBMITTED,
        trading_day=date(2026, 8, 11),
        broker_order_id=broker_order_id,
        limit_price_units=price_to_units("2"),
    )


def _broker_order(status="filled"):
    return {
        "order_id": "broker-buy-1",
        "security": SECURITY,
        "status": status,
        "is_buy": True,
    }


def _broker_trade():
    return {
        "trade_id": "trade-1",
        "trade_id_source": "broker",
        "order_id": "broker-buy-1",
        "security": SECURITY,
        "side": "BUY",
        "amount": 1000,
        "price": 2.0,
        "commission_fee": 5.0,
        "commission_known": True,
        "tax": 0.0,
        "tax_known": True,
        "time": "2026-08-11 10:00:00",
    }


def test_matching_empty_account_is_ready(tmp_path):
    _, repository, _, reconciliation = _services(tmp_path)

    result = reconciliation.synchronize(
        ACCOUNT_ID,
        PHYSICAL_ID,
        _snapshot("20000"),
    )

    assert result.state is ReconciliationState.READY
    assert result.details["blockers"] == ()
    assert repository.get_strategy_account(ACCOUNT_ID).status is AccountStatus.ACTIVE
    assert reconciliation.latest(PHYSICAL_ID) == result


def test_known_fill_is_booked_then_reconciled_and_replay_is_noop(tmp_path):
    database, repository, capital, reconciliation = _services(tmp_path)
    booking = SQLiteFillBookingService(database)
    booking.register_order(_order())
    capital.reserve_cash(ACCOUNT_ID, money_to_units("2100"), 0, "buy-1")
    snapshot = _snapshot(
        "17995",
        positions=(BrokerPositionSnapshot(SECURITY, 1000, 0),),
        orders=(_broker_order(),),
        trades=(_broker_trade(),),
    )

    first = reconciliation.synchronize(ACCOUNT_ID, PHYSICAL_ID, snapshot)
    second = reconciliation.synchronize(ACCOUNT_ID, PHYSICAL_ID, snapshot)

    assert first.state is ReconciliationState.READY
    assert first.details["booked_trade_ids"] == ("trade-1",)
    assert second.state is ReconciliationState.READY
    assert second.details["booked_trade_ids"] == ()
    account = repository.get_strategy_account(ACCOUNT_ID)
    assert account.cash_units == money_to_units("7995")


def test_unknown_broker_order_blocks_new_trading(tmp_path):
    _, repository, _, reconciliation = _services(tmp_path)
    snapshot = _snapshot(
        "20000",
        orders=(
            {
                "order_id": "manual-order",
                "security": SECURITY,
                "status": "open",
                "is_buy": True,
            },
        ),
    )

    result = reconciliation.synchronize(ACCOUNT_ID, PHYSICAL_ID, snapshot)

    assert result.state is ReconciliationState.BLOCKED
    assert "unknown_order:manual-order" in result.details["blockers"]
    assert (
        repository.get_strategy_account(ACCOUNT_ID).status
        is AccountStatus.RECONCILIATION_BLOCKED
    )


def test_cash_or_position_drift_blocks(tmp_path):
    _, _, _, reconciliation = _services(tmp_path)

    result = reconciliation.synchronize(
        ACCOUNT_ID,
        PHYSICAL_ID,
        _snapshot(
            "19999",
            positions=(BrokerPositionSnapshot(SECURITY, 100, 100),),
        ),
    )

    assert result.state is ReconciliationState.BLOCKED
    blockers = result.details["blockers"]
    assert any(item.startswith("cash_mismatch:") for item in blockers)
    assert any(item.startswith("position_mismatch:") for item in blockers)


def test_canceled_buy_releases_reservation_before_cash_compare(tmp_path):
    database, _, capital, reconciliation = _services(tmp_path)
    booking = SQLiteFillBookingService(database)
    booking.register_order(_order())
    capital.reserve_cash(ACCOUNT_ID, money_to_units("2100"), 0, "buy-1")

    result = reconciliation.synchronize(
        ACCOUNT_ID,
        PHYSICAL_ID,
        _snapshot("20000", orders=(_broker_order("canceled"),)),
    )

    assert result.state is ReconciliationState.READY


def test_unknown_trade_is_not_guessed_or_booked(tmp_path):
    _, _, _, reconciliation = _services(tmp_path)
    trade = dict(_broker_trade())
    trade["order_id"] = "manual-order"

    result = reconciliation.synchronize(
        ACCOUNT_ID,
        PHYSICAL_ID,
        _snapshot("20000", trades=(trade,)),
    )

    assert result.state is ReconciliationState.BLOCKED
    assert "unknown_trade_order:manual-order" in result.details["blockers"]


def test_ready_reconciliation_does_not_clear_manual_kill_switch(tmp_path):
    database, repository, _, reconciliation = _services(tmp_path)
    connection = connect_database(database)
    try:
        connection.execute(
            "UPDATE strategy_accounts SET status = 'TRADING_BLOCKED' WHERE strategy_account_id = ?",
            (ACCOUNT_ID,),
        )
        connection.commit()
    finally:
        connection.close()

    result = reconciliation.synchronize(
        ACCOUNT_ID,
        PHYSICAL_ID,
        _snapshot("20000"),
    )

    assert result.state is ReconciliationState.READY
    assert (
        repository.get_strategy_account(ACCOUNT_ID).status
        is AccountStatus.TRADING_BLOCKED
    )


def test_unproven_qmt_capabilities_persist_blocked_result(tmp_path):
    database, _, _, _ = _services(tmp_path)
    unproven = replace(
        _capabilities(),
        stable_trade_id=CapabilityState.PROBE_REQUIRED,
    )
    reconciliation = SQLiteReconciliationService(database, unproven)

    result = reconciliation.synchronize(
        ACCOUNT_ID,
        PHYSICAL_ID,
        _snapshot("20000"),
    )

    assert result.state is ReconciliationState.BLOCKED
    assert result.details["blockers"][0].startswith("capability:")


def test_async_server_adapter_snapshot_is_supported():
    class Adapter:
        async def get_account_info(self, account):
            return {"available_cash": 10000.0}

        async def get_positions(self, account):
            return [{"security": "510050.SH", "amount": 100, "closeable_amount": 50}]

        async def list_orders(self, account, filters=None):
            assert filters == {"from_broker": True}
            return []

        async def list_trades(self, account, filters=None):
            return []

    snapshot = asyncio.run(
        collect_async_broker_snapshot(
            Adapter(),
            object(),
            datetime(2026, 8, 11, 15, 0, tzinfo=SHANGHAI_TZ),
        )
    )

    assert snapshot.available_cash_units == money_to_units("10000")
    assert snapshot.positions == (BrokerPositionSnapshot(SECURITY, 100, 50),)


def test_async_server_adapter_wrapped_account_snapshot_is_supported():
    class Adapter:
        async def get_account_info(self, account):
            return {"dtype": "dict", "value": {"available_cash": 10000.0}}

        async def get_positions(self, account):
            return []

        async def list_orders(self, account, filters=None):
            return []

        async def list_trades(self, account, filters=None):
            return []

    snapshot = asyncio.run(collect_async_broker_snapshot(Adapter(), object()))

    assert snapshot.available_cash_units == money_to_units("10000")


def test_submit_unknown_order_is_adopted_by_exact_client_tag(tmp_path):
    database, _, capital, reconciliation = _services(tmp_path)
    booking = SQLiteFillBookingService(database)
    local = replace(_order(), state=OrderState.SUBMIT_UNKNOWN, broker_order_id=None)
    booking.register_order(local)
    capital.reserve_cash(ACCOUNT_ID, money_to_units("2100"), 0, local.order_id)
    broker_order = {
        "order_id": "broker-recovered-1",
        "security": SECURITY,
        "status": "open",
        "is_buy": True,
        "order_remark": "sub:personal|{}".format(local.client_tag),
    }

    result = reconciliation.synchronize(
        ACCOUNT_ID, PHYSICAL_ID, _snapshot("17900", orders=(broker_order,))
    )

    assert result.state is ReconciliationState.READY
    assert result.details["adopted_order_ids"] == (local.order_id,)
    connection = connect_database(database)
    try:
        row = connection.execute(
            "SELECT state, broker_order_id FROM strategy_orders WHERE order_id = ?",
            (local.order_id,),
        ).fetchone()
    finally:
        connection.close()
    assert row["state"] == OrderState.SUBMITTED.value
    assert row["broker_order_id"] == "broker-recovered-1"
