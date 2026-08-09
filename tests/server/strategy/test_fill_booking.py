from datetime import date, datetime

import pytest

from bullet_trade.server.strategy import (
    BrokerFill,
    BrokerOrder,
    FillConflictError,
    LedgerInvariantError,
    OrderSide,
    OrderState,
    SQLiteCapitalService,
    SQLiteFillBookingService,
    SQLiteStrategyRepository,
    money_to_units,
    price_to_units,
)
from bullet_trade.server.strategy.repository import RepositoryError
from bullet_trade.server.strategy.domain import SHANGHAI_TZ
from bullet_trade.server.strategy.schema import connect_database


SECURITY = "510050.XSHG"


@pytest.fixture
def services(tmp_path):
    database_path = tmp_path / "fills.db"
    repository = SQLiteStrategyRepository(database_path)
    repository.initialize()
    repository.create_physical_account("qmt-main", "QMT", "account-1")
    capital = SQLiteCapitalService(database_path)
    capital.calibrate_broker_available_cash("qmt-main", money_to_units("20000"))
    capital.ensure_strategy_account(
        "good-etf", "good_etf", "qmt-main", money_to_units("10000")
    )
    return repository, capital, SQLiteFillBookingService(database_path)


def _order(order_id, side, quantity, trading_day=date(2026, 8, 10)):
    return BrokerOrder(
        order_id=order_id,
        account_id="good-etf",
        intent_id=None,
        client_tag="tag-{}".format(order_id),
        broker_order_id="broker-{}".format(order_id),
        security=SECURITY,
        side=side,
        requested_qty=quantity,
        filled_qty=0,
        state=OrderState.SUBMITTED,
        trading_day=trading_day,
    )


def _fill(
    fill_id,
    order_id,
    side,
    quantity,
    price="2.00",
    commission="5.00",
    tax="0",
    traded_day=date(2026, 8, 10),
    traded_hour=10,
    broker_trade_id=None,
):
    return BrokerFill(
        fill_id=fill_id,
        order_id=order_id,
        fingerprint="fp-{}".format(fill_id),
        broker_trade_id=broker_trade_id or "trade-{}".format(fill_id),
        security=SECURITY,
        side=side,
        quantity=quantity,
        price_units=price_to_units(price),
        commission_units=money_to_units(commission),
        tax_units=money_to_units(tax),
        traded_at=datetime(
            traded_day.year,
            traded_day.month,
            traded_day.day,
            traded_hour,
            0,
            tzinfo=SHANGHAI_TZ,
        ),
    )


def _order_row(service, order_id):
    connection = connect_database(service.database_path)
    try:
        return tuple(
            connection.execute(
                "SELECT filled_qty, state FROM strategy_orders WHERE order_id = ?",
                (order_id,),
            ).fetchone()
        )
    finally:
        connection.close()


def test_partial_buy_uses_real_fill_and_cancel_releases_only_remainder(services):
    repository, capital, booking = services
    booking.register_order(_order("buy-1", OrderSide.BUY, 1500))
    capital.reserve_cash(
        "good-etf", money_to_units("3000"), 0, "buy-1"
    )

    booked = booking.book_fill(
        "good-etf",
        _fill("f-1", "buy-1", OrderSide.BUY, 1000),
        expected_ledger_version=1,
        sellable_from_trade_date=date(2026, 8, 11),
    )

    assert booked.account.cash_units == money_to_units("7995")
    assert booked.account.reserved_cash_units == money_to_units("995")
    assert booked.position.total_qty == 1000
    assert booked.position.sellable_qty == 0
    assert booked.order_state is OrderState.PARTIALLY_FILLED
    canceled = booking.finalize_order(
        "good-etf", "buy-1", OrderState.CANCELED, expected_ledger_version=2
    )
    assert canceled.released_cash_units == money_to_units("995")
    assert canceled.account.cash_units == money_to_units("7995")
    assert canceled.account.available_cash_units == money_to_units("7995")
    assert repository.replay_account("good-etf") == canceled.account


def test_full_buy_releases_price_buffer_and_duplicate_fill_is_noop(services):
    repository, capital, booking = services
    booking.register_order(_order("buy-1", OrderSide.BUY, 1000))
    capital.reserve_cash("good-etf", money_to_units("2100"), 0, "buy-1")
    fill = _fill("f-1", "buy-1", OrderSide.BUY, 1000)

    first = booking.book_fill(
        "good-etf", fill, 1, sellable_from_trade_date=date(2026, 8, 11)
    )
    duplicate = booking.book_fill(
        "good-etf", fill, 0, sellable_from_trade_date=date(2026, 8, 11)
    )

    assert first.order_state is OrderState.FILLED
    assert first.account.reserved_cash_units == 0
    assert duplicate.duplicate is True
    assert duplicate.account == first.account
    assert repository.replay_account("good-etf") == first.account


def test_same_broker_trade_id_with_different_fill_is_rejected(services):
    _, capital, booking = services
    booking.register_order(_order("buy-1", OrderSide.BUY, 1000))
    capital.reserve_cash("good-etf", money_to_units("2100"), 0, "buy-1")
    booking.book_fill(
        "good-etf",
        _fill("f-1", "buy-1", OrderSide.BUY, 1000, broker_trade_id="T-1"),
        1,
        sellable_from_trade_date=date(2026, 8, 11),
    )

    with pytest.raises(FillConflictError, match="reused"):
        booking.book_fill(
            "good-etf",
            _fill(
                "f-2", "buy-1", OrderSide.BUY, 999, broker_trade_id="T-1"
            ),
            2,
            sellable_from_trade_date=date(2026, 8, 11),
        )


def test_sell_consumes_t1_lot_and_returns_real_proceeds(services):
    _, capital, booking = services
    booking.register_order(_order("buy-1", OrderSide.BUY, 1000))
    capital.reserve_cash("good-etf", money_to_units("2100"), 0, "buy-1")
    buy = booking.book_fill(
        "good-etf",
        _fill("f-buy", "buy-1", OrderSide.BUY, 1000),
        1,
        sellable_from_trade_date=date(2026, 8, 11),
    )
    booking.register_order(
        _order("sell-1", OrderSide.SELL, 600, trading_day=date(2026, 8, 11))
    )

    sold = booking.book_fill(
        "good-etf",
        _fill(
            "f-sell",
            "sell-1",
            OrderSide.SELL,
            600,
            price="2.50",
            commission="3.00",
            tax="1.00",
            traded_day=date(2026, 8, 11),
        ),
        expected_ledger_version=buy.account.ledger_version,
    )

    assert sold.account.cash_units == money_to_units("9491")
    assert sold.position.total_qty == 400
    assert sold.position.sellable_qty == 400
    assert sold.realized_pnl_units == money_to_units("293")


def test_same_day_sell_is_rejected_without_partial_writes(services):
    repository, capital, booking = services
    booking.register_order(_order("buy-1", OrderSide.BUY, 1000))
    capital.reserve_cash("good-etf", money_to_units("2100"), 0, "buy-1")
    buy = booking.book_fill(
        "good-etf",
        _fill("f-buy", "buy-1", OrderSide.BUY, 1000),
        1,
        sellable_from_trade_date=date(2026, 8, 11),
    )
    booking.register_order(_order("sell-1", OrderSide.SELL, 100))

    with pytest.raises(LedgerInvariantError, match="sellable"):
        booking.book_fill(
            "good-etf",
            _fill("f-sell", "sell-1", OrderSide.SELL, 100),
            buy.account.ledger_version,
        )
    assert repository.get_strategy_account("good-etf") == buy.account
    assert _order_row(booking, "sell-1") == (0, "SUBMITTED")


def test_rejected_sell_without_position_keeps_cash_unchanged(services):
    repository, _, booking = services
    booking.register_order(_order("sell-1", OrderSide.SELL, 100))
    before = repository.get_strategy_account("good-etf")

    rejected = booking.finalize_order(
        "good-etf", "sell-1", OrderState.REJECTED, before.ledger_version
    )

    assert rejected.released_cash_units == 0
    assert rejected.account == before
    assert _order_row(booking, "sell-1") == (0, "REJECTED")


def test_fill_insert_failure_rolls_back_cash_order_position_and_lot(services):
    repository, capital, booking = services
    booking.register_order(_order("buy-1", OrderSide.BUY, 1000))
    capital.reserve_cash("good-etf", money_to_units("2100"), 0, "buy-1")
    before = repository.get_strategy_account("good-etf")
    connection = connect_database(booking.database_path)
    try:
        connection.execute(
            """
            CREATE TRIGGER test_abort_fill BEFORE INSERT ON fills
            BEGIN SELECT RAISE(ABORT, 'injected fill failure'); END
            """
        )
    finally:
        connection.close()

    with pytest.raises(RepositoryError, match="book broker fill"):
        booking.book_fill(
            "good-etf",
            _fill("f-1", "buy-1", OrderSide.BUY, 1000),
            1,
            sellable_from_trade_date=date(2026, 8, 11),
        )

    assert repository.get_strategy_account("good-etf") == before
    assert _order_row(booking, "buy-1") == (0, "SUBMITTED")
    connection = connect_database(booking.database_path)
    try:
        assert connection.execute("SELECT COUNT(*) FROM positions").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM position_lots").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM fills").fetchone()[0] == 0
    finally:
        connection.close()


def test_same_day_lots_use_trade_time_fifo_even_when_fills_arrive_out_of_order(services):
    _, capital, booking = services
    booking.register_order(_order("buy-late", OrderSide.BUY, 100))
    booking.register_order(_order("buy-early", OrderSide.BUY, 100))
    capital.reserve_cash("good-etf", money_to_units("300"), 0, "buy-late")
    late = booking.book_fill(
        "good-etf",
        _fill(
            "f-late", "buy-late", OrderSide.BUY, 100,
            price="3", commission="0", traded_hour=11,
        ),
        1,
        sellable_from_trade_date=date(2026, 8, 11),
    )
    capital.reserve_cash(
        "good-etf", money_to_units("200"), late.account.ledger_version, "buy-early"
    )
    early = booking.book_fill(
        "good-etf",
        _fill(
            "f-early", "buy-early", OrderSide.BUY, 100,
            price="2", commission="0", traded_hour=10,
        ),
        late.account.ledger_version + 1,
        sellable_from_trade_date=date(2026, 8, 11),
    )
    booking.register_order(
        _order("sell-1", OrderSide.SELL, 100, trading_day=date(2026, 8, 11))
    )

    sold = booking.book_fill(
        "good-etf",
        _fill(
            "f-sell", "sell-1", OrderSide.SELL, 100,
            price="4", commission="0", traded_day=date(2026, 8, 11),
        ),
        early.account.ledger_version,
    )

    assert sold.realized_pnl_units == money_to_units("200")
    assert sold.position.total_qty == 100
    assert sold.position.avg_cost_price_units == price_to_units("3")
