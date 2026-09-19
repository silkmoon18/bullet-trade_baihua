import json
from dataclasses import replace
from datetime import date, datetime, timedelta

import pytest

from bullet_trade.server.strategy import (
    BrokerFill,
    BrokerOrder,
    FillConflictError,
    FillPriceSource,
    LedgerInvariantError,
    MarketMark,
    OrderSide,
    OrderState,
    SQLiteCapitalService,
    SQLiteFillBookingService,
    SQLiteStrategyAPI,
    SQLiteStrategyRepository,
    SQLiteValuationService,
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


def _order(
    order_id,
    side,
    quantity,
    trading_day=date(2026, 8, 10),
    limit_price="2.00",
    intent_id=None,
):
    return BrokerOrder(
        order_id=order_id,
        account_id="good-etf",
        intent_id=intent_id,
        client_tag="tag-{}".format(order_id),
        broker_order_id="broker-{}".format(order_id),
        security=SECURITY,
        side=side,
        requested_qty=quantity,
        filled_qty=0,
        state=OrderState.SUBMITTED,
        trading_day=trading_day,
        limit_price_units=(
            price_to_units(limit_price) if limit_price is not None else None
        ),
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


def test_zero_fallback_partial_buy_notifies_estimate_and_preserves_remainder_reservation(services):
    _, capital, original_booking = services
    notifications = []
    booking = SQLiteFillBookingService(original_booking.database_path, notifications.append)
    _insert_intent(booking, "intent-partial", {})
    booking.register_order(_order("zero-buy", OrderSide.BUY, 200, intent_id="intent-partial"))
    capital.reserve_cash("good-etf", money_to_units("405"), 0, "zero-buy")
    fill = replace(_fill("zero-fill", "zero-buy", OrderSide.BUY, 100),
                   price_units=0, price_source=FillPriceSource.ZERO_FALLBACK,
                   price_known=False, commission_units=None, tax_units=None)
    result = booking.book_fill("good-etf", fill, 1, sellable_from_trade_date=date(2026, 8, 11))
    assert result.account.cash_units == money_to_units("9800")
    assert result.account.reserved_cash_units == money_to_units("205")
    assert result.position.total_qty == 100
    assert result.position.avg_cost_price_units == price_to_units("2.00")
    assert result.order_state is OrderState.PARTIALLY_FILLED
    notice = notifications[-1]
    assert notice.price == 2
    assert notice.amount == 200
    assert notice.estimated is True
    assert notice.quantity == 100
    assert "收益为非精确收益" in notice.detail
    assert "佣金 未知" in notice.detail and "税费 未知" in notice.detail
    assert booking.book_fill("good-etf", fill, result.account.ledger_version,
                             sellable_from_trade_date=date(2026, 8, 11)).duplicate
    assert len(notifications) == 2  # Submit + first fill only.
    canceled = booking.finalize_order("good-etf", "zero-buy", OrderState.CANCELED, result.account.ledger_version)
    assert canceled.released_cash_units == money_to_units("205")


def _insert_intent(booking, intent_id, targets):
    payload = {
        "trading_day": "2026-08-10",
        "reference_prices_units": {SECURITY: price_to_units("2.00")},
        "execution_request": {
            "schema_version": 2,
            "style": {"type": "LIMIT", "price_band_ppm": 2000},
            "sell_style": {
                "type": "MARKET",
                "protect_price_band_ppm": 15_000,
            },
            "follow_up": "UNTIL_FILLED_TODAY",
            "repricing": "KEEP_ORIGINAL",
        },
    }
    payload.update(targets)
    connection = connect_database(booking.database_path)
    try:
        connection.execute(
            """
            INSERT INTO portfolio_intents(
                intent_id, strategy_account_id, idempotency_key,
                expected_ledger_version, state, targets_json,
                created_at, updated_at
            ) VALUES (?, 'good-etf', ?, 0, 'EXECUTING', ?, ?, ?)
            """,
            (
                intent_id,
                "key-{}".format(intent_id),
                json.dumps(payload),
                "2026-08-10T09:30:00+08:00",
                "2026-08-10T09:30:00+08:00",
            ),
        )
        connection.commit()
    finally:
        connection.close()


def _ledger_payload(booking, entry_type, order_id):
    connection = connect_database(booking.database_path)
    try:
        row = connection.execute(
            """
            SELECT payload_json FROM ledger_entries
            WHERE entry_type = ? AND reference_id = ?
            ORDER BY event_seq DESC LIMIT 1
            """,
            (entry_type, order_id),
        ).fetchone()
        return json.loads(row["payload_json"]) if row is not None else None
    finally:
        connection.close()


def _seed_long_position(repository, capital, booking, intent_id=None):
    """Buy 1000 shares at 2.00 so the follow-up sell has a cost basis."""

    booking.register_order(
        _order("seed-buy", OrderSide.BUY, 1000, intent_id=intent_id, limit_price="2.00")
    )
    capital.reserve_cash("good-etf", money_to_units("2005"), 0, "seed-buy")
    booked = booking.book_fill(
        "good-etf",
        _fill("seed-fill", "seed-buy", OrderSide.BUY, 1000),
        expected_ledger_version=1,
        sellable_from_trade_date=date(2026, 8, 10),
    )
    assert booked.account.cash_units == money_to_units("7995")
    return booked


def test_unpriced_sell_books_estimated_unit_price(services):
    """零价卖出把参考价作为估算单价入账，但保留非真实标记。"""

    repository, capital, booking = services
    _insert_intent(booking, "intent-sell", {})
    _seed_long_position(repository, capital, booking, intent_id="intent-sell")
    booking.register_order(
        _order(
            "sell-1",
            OrderSide.SELL,
            1000,
            intent_id="intent-sell",
            limit_price=None,
        )
    )
    zero_sell = replace(
        _fill("zero-sell", "sell-1", OrderSide.SELL, 1000),
        price_units=0,
        price_source=FillPriceSource.ZERO_FALLBACK,
        price_known=False,
    )

    result = booking.book_fill(
        "good-etf", zero_sell, expected_ledger_version=2
    )

    # 2.00 参考价暂估 2000 元，使下一笔调仓可以继续。
    assert result.account.cash_units == money_to_units("9990")
    assert result.account.reserved_cash_units == 0
    assert result.position.total_qty == 0
    assert result.realized_pnl_units == money_to_units("-10")
    assert repository.replay_account("good-etf") == result.account

    db = connect_database(booking.database_path)
    try:
        row = tuple(
            db.execute(
                """
                SELECT price_units, price_source, price_known
                FROM fills WHERE fill_id = 'zero-sell'
                """
            ).fetchone()
        )
    finally:
        db.close()
    assert row == (price_to_units("2.00"), "ZERO_PRICE_ESTIMATE", 0)

    payload = _ledger_payload(booking, "SELL_FILL_BOOKED", "sell-1")
    assert payload["gross_units"] == money_to_units("2000")
    assert payload["price_known"] == 0
    assert payload["estimated_proceeds_units"] == money_to_units("2000")
    assert payload["credited_proceeds_estimate_units"] == money_to_units("2000")
    assert payload["price_estimate"] == {
        "basis": "INTENT_REFERENCE_PRICE",
        "price_units": price_to_units("2.00"),
        "capped_to_order_reservation": False,
    }
    snapshot = SQLiteValuationService(booking.database_path).create_snapshot(
        "good-etf", {},
        datetime(2026, 8, 10, 10, 1, tzinfo=SHANGHAI_TZ),
        timedelta(minutes=1),
    )
    assert snapshot.available_cash_units == money_to_units("9990")
    assert snapshot.conservative_cash_units == money_to_units("7990")
    assert snapshot.estimated_sell_proceeds_units == money_to_units("2000")
    assert snapshot.unconfirmed_cash_credit_units == money_to_units("2000")
    assert snapshot.performance_ready is False
    public = SQLiteStrategyAPI._snapshot_payload(snapshot)
    assert public["estimated_sell_proceeds"] == 2000.0
    assert public["unconfirmed_cash_credit"] == 2000.0
    assert public["available_cash"] == 9990.0
    assert public["nav"] is None
    assert public["estimated_nav"] == 0.999
    assert public["estimated_returns"] == pytest.approx(-0.001)
    assert "非精确收益" in public["returns_note"]


def test_unpriced_sell_without_reference_price_uses_position_cost(services):
    """缺少参考价时用已有持仓成本估单价，仍正常记回款。"""

    repository, capital, booking = services
    _seed_long_position(repository, capital, booking)
    booking.register_order(
        _order("sell-1", OrderSide.SELL, 1000, limit_price=None)
    )
    zero_sell = replace(
        _fill("zero-sell", "sell-1", OrderSide.SELL, 1000),
        price_units=0,
        price_source=FillPriceSource.ZERO_FALLBACK,
        price_known=False,
    )

    result = booking.book_fill("good-etf", zero_sell, expected_ledger_version=2)

    assert result.account.cash_units == money_to_units("9995")
    payload = _ledger_payload(booking, "SELL_FILL_BOOKED", "sell-1")
    assert payload["price_estimate"]["basis"] == "POSITION_COST_FALLBACK"
    assert payload["estimated_proceeds_units"] == money_to_units("2005")


def test_order_price_fallback_sell_is_not_estimated(services):
    """委托价兜底（非 0）不进入估算分支，保持原有按委托价记账的行为。"""

    repository, capital, booking = services
    _insert_intent(booking, "intent-sell", {})
    _seed_long_position(repository, capital, booking, intent_id="intent-sell")
    booking.register_order(
        _order("sell-1", OrderSide.SELL, 1000, intent_id="intent-sell")
    )
    estimated_sell = replace(
        _fill("est-sell", "sell-1", OrderSide.SELL, 1000, price="1.50"),
        price_source=FillPriceSource.ORDER_PRICE_FALLBACK,
        price_known=False,
    )

    result = booking.book_fill("good-etf", estimated_sell, expected_ledger_version=2)

    # 只按委托价 1.50 记 1500 元，再扣 5 元费用；不被抬到保护价下沿 1970 元
    assert result.account.cash_units == money_to_units("9490")
    payload = _ledger_payload(booking, "SELL_FILL_BOOKED", "sell-1")
    assert payload["price_known"] == 0
    assert "proceeds_estimate" not in payload
    assert "estimated_proceeds_units" not in payload


def test_trusted_sell_price_is_untouched_by_the_estimate(services):
    """成交价可信时不进入估算分支。"""

    repository, capital, booking = services
    _insert_intent(booking, "intent-sell", {})
    _seed_long_position(repository, capital, booking, intent_id="intent-sell")
    booking.register_order(
        _order("sell-1", OrderSide.SELL, 1000, intent_id="intent-sell")
    )

    result = booking.book_fill(
        "good-etf",
        _fill("real-sell", "sell-1", OrderSide.SELL, 1000, price="2.10"),
        expected_ledger_version=2,
    )

    # 7995 + 2100 - 5
    assert result.account.cash_units == money_to_units("10090")
    assert result.realized_pnl_units == money_to_units("90")
    payload = _ledger_payload(booking, "SELL_FILL_BOOKED", "sell-1")
    assert payload["price_known"] == 1
    assert "proceeds_estimate" not in payload


def test_zero_price_sell_estimate_funds_pending_buy(services):
    """参考价暂估回款可推动卖出后买入，不把成交价伪装为已知。"""

    repository, capital, booking = services
    _insert_intent(booking, "intent-rot", {})
    _seed_long_position(repository, capital, booking, intent_id="intent-rot")
    booking.register_order(
        _order("sell-1", OrderSide.SELL, 1000, intent_id="intent-rot", limit_price=None)
    )
    booking.book_fill(
        "good-etf",
        replace(
            _fill("zero-sell", "sell-1", OrderSide.SELL, 1000),
            price_units=0,
            price_source=FillPriceSource.ZERO_FALLBACK,
            price_known=False,
        ),
        expected_ledger_version=2,
    )

    booking.register_order(_order("buy-2", OrderSide.BUY, 4000, intent_id="intent-rot"))
    account = repository.get_strategy_account("good-etf")
    assert account.available_cash_units == money_to_units("9990")
    reserved = capital.reserve_cash(
        "good-etf", money_to_units("9000"), account.ledger_version, "buy-2"
    )
    assert reserved.reserved_cash_units == money_to_units("9000")


def test_unpriced_buy_books_estimated_unit_price(services):
    """零价买入按参考价估单价，标记为估算。"""

    repository, capital, booking = services
    _insert_intent(booking, "intent-buy", {})
    booking.register_order(
        _order("buy-z", OrderSide.BUY, 1000, intent_id="intent-buy")
    )
    capital.reserve_cash("good-etf", money_to_units("2010"), 0, "buy-z")
    zero_buy = replace(
        _fill("zero-buy", "buy-z", OrderSide.BUY, 1000),
        price_units=0,
        price_source=FillPriceSource.ZERO_FALLBACK,
        price_known=False,
    )

    result = booking.book_fill(
        "good-etf",
        zero_buy,
        expected_ledger_version=1,
        sellable_from_trade_date=date(2026, 8, 11),
    )

    # 2.00 参考价暂估 2000 元；含已知费用扣 2005 元。
    assert result.account.cash_units == money_to_units("7995")
    assert result.account.reserved_cash_units == 0
    assert result.position.total_qty == 1000
    assert result.position.avg_cost_price_units == price_to_units("2.005")
    assert repository.replay_account("good-etf") == result.account

    db = connect_database(booking.database_path)
    try:
        row = tuple(
            db.execute(
                """
                SELECT price_units, price_source, price_known
                FROM fills WHERE fill_id = 'zero-buy'
                """
            ).fetchone()
        )
    finally:
        db.close()
    assert row == (price_to_units("2.00"), "ZERO_PRICE_ESTIMATE", 0)

    payload = _ledger_payload(booking, "BUY_FILL_BOOKED", "buy-z")
    assert payload["gross_units"] == money_to_units("2000")
    assert payload["price_known"] == 0
    assert payload["estimated_cost_units"] == money_to_units("2000")
    assert payload["price_estimate"] == {
        "basis": "INTENT_REFERENCE_PRICE",
        "price_units": price_to_units("2.00"),
        "capped_to_order_reservation": False,
    }


def test_unpriced_buy_estimate_above_reservation_is_capped_not_blocked(services):
    """参考价超出预留资金时，仍记成交数量，估算金额以已预留资金为限。"""

    _, capital, booking = services
    _insert_intent(booking, "intent-buy", {})
    booking.register_order(
        _order("buy-z", OrderSide.BUY, 1000, intent_id="intent-buy")
    )
    capital.reserve_cash("good-etf", money_to_units("1990"), 0, "buy-z")
    zero_buy = replace(
        _fill("zero-buy", "buy-z", OrderSide.BUY, 1000),
        price_units=0,
        price_source=FillPriceSource.ZERO_FALLBACK,
        price_known=False,
    )

    booked = booking.book_fill(
        "good-etf", zero_buy, expected_ledger_version=1,
        sellable_from_trade_date=date(2026, 8, 11),
    )
    assert booked.order_state is OrderState.FILLED
    assert booked.position.total_qty == 1000
    assert booked.account.cash_units == money_to_units("8010")
    assert _ledger_payload(booking, "BUY_FILL_BOOKED", "buy-z")[
        "price_estimate"
    ]["capped_to_order_reservation"] is True


def test_unpriced_buy_uses_local_limit_when_intent_reference_is_missing(services):
    _, capital, booking = services
    booking.register_order(_order("buy-limit", OrderSide.BUY, 1000))
    capital.reserve_cash("good-etf", money_to_units("2010"), 0, "buy-limit")
    zero_buy = replace(
        _fill("zero-limit", "buy-limit", OrderSide.BUY, 1000),
        price_units=0, price_source=FillPriceSource.ZERO_FALLBACK,
        price_known=False,
    )
    result = booking.book_fill(
        "good-etf", zero_buy, 1,
        sellable_from_trade_date=date(2026, 8, 11),
    )
    assert result.account.cash_units == money_to_units("7995")
    assert result.position.avg_cost_price_units == price_to_units("2.005")
    payload = _ledger_payload(booking, "BUY_FILL_BOOKED", "buy-limit")
    assert payload["price_estimate"]["basis"] == "ORDER_LIMIT_PRICE"


def test_unpriced_buy_without_reference_price_uses_reserved_budget(services):
    """参考价也缺失时，按原订单预留资金暂算，不丢成交股数。"""

    _, capital, booking = services
    _insert_intent(booking, "intent-buy", {"reference_prices_units": {}})
    booking.register_order(
        _order("buy-z", OrderSide.BUY, 1000, intent_id="intent-buy", limit_price=None)
    )
    capital.reserve_cash("good-etf", money_to_units("2010"), 0, "buy-z")
    zero_buy = replace(
        _fill("zero-buy", "buy-z", OrderSide.BUY, 1000),
        price_units=0,
        price_source=FillPriceSource.ZERO_FALLBACK,
        price_known=False,
    )

    booked = booking.book_fill(
        "good-etf", zero_buy, expected_ledger_version=1,
        sellable_from_trade_date=date(2026, 8, 11),
    )
    assert booked.order_state is OrderState.FILLED
    assert booked.position.total_qty == 1000
    assert booked.account.cash_units == money_to_units("7990")
    assert _ledger_payload(booking, "BUY_FILL_BOOKED", "buy-z")[
        "price_estimate"
    ]["basis"] == "ORDER_RESERVED_BUDGET"


def test_verified_price_corrects_zero_sell_once(services):
    repository, capital, booking = services
    _insert_intent(booking, "intent-correction", {})
    _seed_long_position(repository, capital, booking, intent_id="intent-correction")
    booking.register_order(_order(
        "sell-correct", OrderSide.SELL, 1000,
        intent_id="intent-correction", limit_price=None,
    ))
    zero = replace(
        _fill("correct-fill", "sell-correct", OrderSide.SELL, 1000),
        price_units=0, price_source=FillPriceSource.ZERO_FALLBACK,
        price_known=False,
    )
    booked = booking.book_fill("good-etf", zero, 2)
    assert booked.account.cash_units == money_to_units("9990")
    assert capital.calibrate_broker_available_cash(
        "qmt-main", money_to_units("20000")
    ) == money_to_units("20000")

    verified = _fill("correct-fill", "sell-correct", OrderSide.SELL, 1000, price="2.10")
    corrected = booking.book_fill(
        "good-etf", verified, booked.account.ledger_version
    )
    assert corrected.corrected is True
    assert corrected.account.cash_units == money_to_units("10090")
    assert corrected.realized_pnl_units == money_to_units("100")
    assert repository.replay_account("good-etf") == corrected.account
    assert capital.calibrate_broker_available_cash(
        "qmt-main", money_to_units("20000")
    ) == money_to_units("20000")
    assert booking.book_fill(
        "good-etf", verified, corrected.account.ledger_version
    ).duplicate is True
    db = connect_database(booking.database_path)
    try:
        assert tuple(db.execute(
            "SELECT price_units, price_source, price_known FROM fills "
            "WHERE fill_id = 'correct-fill'"
        ).fetchone()) == (price_to_units("2.10"), "BROKER_TRADE", 1)
        assert db.execute(
            "SELECT COUNT(*) FROM ledger_entries "
            "WHERE entry_type = 'FILL_PRICE_CORRECTED'"
        ).fetchone()[0] == 1
    finally:
        db.close()


def test_legacy_estimated_sell_credit_is_visible_and_corrected_once(services, monkeypatch):
    repository, capital, booking = services
    _seed_long_position(repository, capital, booking)
    booking.register_order(_order("legacy-sell", OrderSide.SELL, 1000, limit_price=None))
    zero = replace(
        _fill("legacy-fill", "legacy-sell", OrderSide.SELL, 1000),
        price_units=0, price_source=FillPriceSource.ZERO_FALLBACK,
        price_known=False,
    )
    monkeypatch.setattr(booking, "_price_zero_fill", lambda conn, account_id, fill: (fill, None))
    booked = booking.book_fill("good-etf", zero, 2)
    credited = repository.append_account_event(
        "good-etf", booked.account.ledger_version,
        "SELL_PROCEEDS_ESTIMATE_CORRECTION", money_to_units("1970"),
        booked.account.reserved_cash_units, "SELL_PROCEEDS_ESTIMATE_CORRECTED",
        {"estimated_gross_units": money_to_units("1970")},
        reference_type="fill", reference_id="legacy-fill",
    )
    assert credited.cash_units == money_to_units("9960")
    valuation = SQLiteValuationService(booking.database_path)
    as_of = datetime(2026, 8, 10, 10, 1, tzinfo=SHANGHAI_TZ)
    provisional = valuation.create_snapshot("good-etf", {}, as_of, timedelta(minutes=1))
    assert provisional.unconfirmed_cash_credit_units == money_to_units("1970")
    assert provisional.available_cash_units == money_to_units("9960")
    assert provisional.conservative_cash_units == money_to_units("7990")

    verified = _fill("legacy-fill", "legacy-sell", OrderSide.SELL, 1000, price="2.10")
    corrected = booking.book_fill("good-etf", verified, credited.ledger_version)
    assert corrected.account.cash_units == money_to_units("10090")
    assert repository.replay_account("good-etf") == corrected.account
    final = valuation.create_snapshot("good-etf", {}, as_of, timedelta(minutes=1))
    assert final.unconfirmed_cash_credit_units == 0
    assert final.available_cash_units == money_to_units("10090")
    assert final.performance_ready is True


def test_old_inline_sell_estimate_credit_is_corrected_once(services, monkeypatch):
    repository, capital, booking = services
    _insert_intent(booking, "inline-legacy", {})
    _seed_long_position(repository, capital, booking, intent_id="inline-legacy")
    booking.register_order(_order(
        "inline-sell", OrderSide.SELL, 1000,
        intent_id="inline-legacy", limit_price=None,
    ))
    zero = replace(
        _fill("inline-fill", "inline-sell", OrderSide.SELL, 1000),
        price_units=0, price_source=FillPriceSource.ZERO_FALLBACK,
        price_known=False,
    )
    monkeypatch.setattr(booking, "_price_zero_fill", lambda conn, account_id, fill: (fill, None))
    booked = booking.book_fill("good-etf", zero, 2)

    # Recreate the previous release's 1.97 protection-boundary estimate
    # without changing the immutable fill ID or event sequence.
    db = connect_database(booking.database_path)
    try:
        row = db.execute(
            "SELECT event_seq, amount_units, cash_after_units, payload_json "
            "FROM ledger_entries WHERE entry_type = 'SELL_FILL_BOOKED' "
            "AND reference_id = 'inline-sell'"
        ).fetchone()
        payload = json.loads(row["payload_json"])
        payload.pop("credited_proceeds_estimate_units")
        old_estimate = money_to_units("1970")
        difference = old_estimate - money_to_units("2000")
        payload["estimated_proceeds_units"] = old_estimate
        payload["cash_delta_units"] += difference
        payload["realized_pnl_units"] += difference
        append_only_trigger = db.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'trigger' "
            "AND name = 'ledger_entries_no_update'"
        ).fetchone()[0]
        db.execute("DROP TRIGGER ledger_entries_no_update")
        db.execute(
            "UPDATE ledger_entries SET amount_units = ?, cash_after_units = ?, "
            "payload_json = ? WHERE strategy_account_id = 'good-etf' "
            "AND event_seq = ?",
            (
                row["amount_units"] + difference,
                row["cash_after_units"] + difference,
                json.dumps(payload), row["event_seq"],
            ),
        )
        db.execute(
            "UPDATE strategy_accounts SET cash_units = cash_units + ? "
            "WHERE strategy_account_id = 'good-etf'",
            (difference,),
        )
        db.execute(append_only_trigger)
        db.commit()
    finally:
        db.close()

    assert repository.replay_account("good-etf").cash_units == money_to_units("9960")
    valuation = SQLiteValuationService(booking.database_path)
    as_of = datetime(2026, 8, 10, 10, 1, tzinfo=SHANGHAI_TZ)
    provisional = valuation.create_snapshot("good-etf", {}, as_of, timedelta(minutes=1))
    assert provisional.available_cash_units == money_to_units("9960")
    assert provisional.unconfirmed_cash_credit_units == money_to_units("1970")

    verified = _fill("inline-fill", "inline-sell", OrderSide.SELL, 1000, price="2.10")
    corrected = booking.book_fill(
        "good-etf", verified, booked.account.ledger_version
    )
    assert corrected.account.cash_units == money_to_units("10090")
    assert repository.replay_account("good-etf") == corrected.account
    assert valuation.create_snapshot(
        "good-etf", {}, as_of, timedelta(minutes=1)
    ).unconfirmed_cash_credit_units == 0


def test_verified_zero_buy_after_partial_sale_corrects_basis_and_pnl(services):
    repository, capital, booking = services
    _insert_intent(booking, "intent-buy-correction", {})
    booking.register_order(_order(
        "buy-correct", OrderSide.BUY, 1000, intent_id="intent-buy-correction"
    ))
    capital.reserve_cash("good-etf", money_to_units("2010"), 0, "buy-correct")
    zero_buy = replace(
        _fill("correct-buy-fill", "buy-correct", OrderSide.BUY, 1000),
        price_units=0, price_source=FillPriceSource.ZERO_FALLBACK,
        price_known=False,
    )
    booked = booking.book_fill(
        "good-etf", zero_buy, 1, sellable_from_trade_date=date(2026, 8, 10)
    )
    assert booked.position.avg_cost_price_units == price_to_units("2.005")
    booking.register_order(_order("sell-half", OrderSide.SELL, 500, limit_price=None))
    sold = booking.book_fill(
        "good-etf",
        _fill("half-fill", "sell-half", OrderSide.SELL, 500, price="2.10"),
        booked.account.ledger_version,
    )
    assert sold.realized_pnl_units == money_to_units("42.5")

    verified = _fill(
        "correct-buy-fill", "buy-correct", OrderSide.BUY, 1000, price="1.99"
    )
    corrected = booking.book_fill(
        "good-etf", verified, sold.account.ledger_version,
        sellable_from_trade_date=date(2026, 8, 10),
    )
    assert corrected.corrected is True
    assert corrected.account.cash_units == money_to_units("9050")
    assert corrected.position.total_qty == 500
    assert corrected.position.avg_cost_price_units == price_to_units("1.995")
    assert corrected.realized_pnl_units == money_to_units("5")
    assert repository.replay_account("good-etf") == corrected.account

    valuation = SQLiteValuationService(booking.database_path)
    snapshot = valuation.create_snapshot(
        "good-etf",
        {SECURITY: MarketMark(
            security=SECURITY, price_units=price_to_units("2.10"),
            as_of=datetime(2026, 8, 10, 10, 1, tzinfo=SHANGHAI_TZ),
            source="qmt",
        )},
        datetime(2026, 8, 10, 10, 1, tzinfo=SHANGHAI_TZ),
        timedelta(minutes=1),
    )
    assert snapshot.unknown_price_fill_count == 0
    assert snapshot.performance_ready is True
    assert snapshot.realized_pnl_units == money_to_units("47.5")
    assert snapshot.total_pnl_units == (
        snapshot.realized_pnl_units + snapshot.unrealized_pnl_units
    )


def test_order_price_fallback_can_upgrade_to_verified_price(services):
    repository, capital, booking = services
    booking.register_order(_order("fallback-buy", OrderSide.BUY, 1000))
    capital.reserve_cash("good-etf", money_to_units("2100"), 0, "fallback-buy")
    fallback = replace(
        _fill("fallback-fill", "fallback-buy", OrderSide.BUY, 1000, price="1.50"),
        price_source=FillPriceSource.ORDER_PRICE_FALLBACK,
        price_known=False,
    )
    booked = booking.book_fill(
        "good-etf", fallback, 1,
        sellable_from_trade_date=date(2026, 8, 11),
    )
    assert booked.account.cash_units == money_to_units("8495")
    verified = _fill(
        "fallback-fill", "fallback-buy", OrderSide.BUY, 1000, price="2.00"
    )
    corrected = booking.book_fill(
        "good-etf", verified, booked.account.ledger_version,
        sellable_from_trade_date=date(2026, 8, 11),
    )
    assert corrected.corrected is True
    assert corrected.account.cash_units == money_to_units("7995")
    assert corrected.position.avg_cost_price_units == price_to_units("2.005")
    assert repository.replay_account("good-etf") == corrected.account


def test_price_upgrade_rejects_changed_trade_quantity(services):
    repository, capital, booking = services
    _insert_intent(booking, "identity-check", {})
    _seed_long_position(repository, capital, booking, intent_id="identity-check")
    booking.register_order(_order(
        "identity-sell", OrderSide.SELL, 1000,
        intent_id="identity-check", limit_price=None,
    ))
    zero = replace(
        _fill("identity-fill", "identity-sell", OrderSide.SELL, 1000),
        price_units=0, price_source=FillPriceSource.ZERO_FALLBACK,
        price_known=False,
    )
    booked = booking.book_fill("good-etf", zero, 2)
    changed = _fill(
        "identity-fill", "identity-sell", OrderSide.SELL, 900,
        price="2.10",
    )
    with pytest.raises(FillConflictError, match="reused with different fields"):
        booking.book_fill("good-etf", changed, booked.account.ledger_version)
    assert repository.replay_account("good-etf") == booked.account


def test_order_price_fallback_buy_is_not_estimated(services):
    """委托价兜底（非 0）的买入不进入估算分支，成本仍按委托价计。"""

    _, capital, booking = services
    _insert_intent(booking, "intent-buy", {})
    booking.register_order(
        _order("buy-o", OrderSide.BUY, 1000, intent_id="intent-buy")
    )
    capital.reserve_cash("good-etf", money_to_units("2010"), 0, "buy-o")
    fallback_buy = replace(
        _fill("order-buy", "buy-o", OrderSide.BUY, 1000, price="1.50"),
        price_source=FillPriceSource.ORDER_PRICE_FALLBACK,
        price_known=False,
    )

    result = booking.book_fill(
        "good-etf",
        fallback_buy,
        expected_ledger_version=1,
        sellable_from_trade_date=date(2026, 8, 11),
    )

    # 10000 - (1500 + 5) = 8495，成本 1.505；不被抬到保护价上沿 2.004
    assert result.account.cash_units == money_to_units("8495")
    assert result.account.reserved_cash_units == 0
    assert result.position.avg_cost_price_units == price_to_units("1.505")
    payload = _ledger_payload(booking, "BUY_FILL_BOOKED", "buy-o")
    assert payload["price_known"] == 0
    assert "cost_estimate" not in payload


def test_trusted_buy_price_is_untouched_by_the_estimate(services):
    """有价买入完全不受估算影响。"""

    _, capital, booking = services
    _insert_intent(booking, "intent-buy", {})
    booking.register_order(
        _order("buy-k", OrderSide.BUY, 1000, intent_id="intent-buy")
    )
    capital.reserve_cash("good-etf", money_to_units("2005"), 0, "buy-k")

    result = booking.book_fill(
        "good-etf",
        _fill("buy-k-fill", "buy-k", OrderSide.BUY, 1000),
        expected_ledger_version=1,
        sellable_from_trade_date=date(2026, 8, 11),
    )

    assert result.account.cash_units == money_to_units("7995")
    assert result.position.avg_cost_price_units == price_to_units("2.005")
    payload = _ledger_payload(booking, "BUY_FILL_BOOKED", "buy-k")
    assert payload["gross_units"] == money_to_units("2000")
    assert "cost_estimate" not in payload


def test_t0_buy_is_sellable_on_acquisition_day(services):
    repository, capital, booking = services
    booking.register_order(_order("buy-1", OrderSide.BUY, 1000))
    capital.reserve_cash("good-etf", money_to_units("2100"), 0, "buy-1")

    booked = booking.book_fill(
        "good-etf",
        _fill("f-1", "buy-1", OrderSide.BUY, 1000),
        expected_ledger_version=1,
        sellable_from_trade_date=date(2026, 8, 10),
    )

    assert booked.position.total_qty == 1000
    assert booked.position.sellable_qty == 1000
    booking.register_order(_order("sell-1", OrderSide.SELL, 1000))
    sold = booking.book_fill(
        "good-etf",
        _fill("f-sell", "sell-1", OrderSide.SELL, 1000, price="2.10"),
        expected_ledger_version=booked.account.ledger_version,
    )
    assert sold.position.total_qty == 0
    assert sold.position.sellable_qty == 0
    assert sold.account.cash_units == money_to_units("10090")
    assert repository.replay_account("good-etf") == sold.account


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


def test_later_known_fee_does_not_conflict_with_booked_unknown_fill(services):
    repository, capital, booking = services
    booking.register_order(_order("buy-1", OrderSide.BUY, 1000))
    capital.reserve_cash("good-etf", money_to_units("2100"), 0, "buy-1")
    original = _fill(
        "f-1", "buy-1", OrderSide.BUY, 1000, commission="0",
        broker_trade_id="T-1",
    )
    unknown = replace(
        original,
        fingerprint="fp-unknown",
        commission_units=None,
        tax_units=None,
    )
    later_known = replace(
        original,
        fingerprint="fp-known",
        commission_units=money_to_units("5"),
        tax_units=0,
    )

    first = booking.book_fill(
        "good-etf", unknown, 1, sellable_from_trade_date=date(2026, 8, 11)
    )
    duplicate = booking.book_fill(
        "good-etf", later_known, 0, sellable_from_trade_date=date(2026, 8, 11)
    )

    assert duplicate.duplicate is True
    assert duplicate.account == first.account
    assert repository.replay_account("good-etf") == first.account


def test_scoped_fill_id_replays_legacy_fill_without_conflict(services):
    _, capital, booking = services
    booking.register_order(_order("buy-1", OrderSide.BUY, 1000))
    capital.reserve_cash("good-etf", money_to_units("2100"), 0, "buy-1")
    legacy = _fill(
        "broker:T-1",
        "buy-1",
        OrderSide.BUY,
        1000,
        broker_trade_id="T-1",
    )
    current = replace(legacy, fill_id="broker:2026-08-10:T-1")

    booking.book_fill(
        "good-etf",
        legacy,
        1,
        sellable_from_trade_date=date(2026, 8, 11),
    )
    replay = booking.book_fill(
        "good-etf",
        current,
        0,
        sellable_from_trade_date=date(2026, 8, 11),
    )

    assert replay.duplicate is True


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


def test_order_and_fill_emit_structured_trade_notifications(services):
    _, capital, existing_booking = services
    notifications = []
    booking = SQLiteFillBookingService(
        existing_booking.database_path,
        notification_handler=notifications.append,
    )
    booking.register_order(_order("buy-1", OrderSide.BUY, 1000))
    capital.reserve_cash("good-etf", money_to_units("2100"), 0, "buy-1")
    bought = booking.book_fill(
        "good-etf",
        _fill("f-1", "buy-1", OrderSide.BUY, 1000),
        1,
        sellable_from_trade_date=date(2026, 8, 11),
    )

    assert [item.event for item in notifications] == ["ORDER_SUBMITTED", "FILLED"]
    assert [item.strategy_id for item in notifications] == [
        "good_etf", "good_etf"
    ]
    assert notifications[0].security == SECURITY
    assert notifications[0].quantity == 1000
    assert str(notifications[0].price) == "2"
    assert str(notifications[0].amount) == "2000"
    assert str(notifications[1].price) == "2"
    assert str(notifications[1].amount) == "2005"

    booking.register_order(
        _order(
            "sell-1",
            OrderSide.SELL,
            1000,
            trading_day=date(2026, 8, 11),
            limit_price="3.00",
        )
    )
    booking.book_fill(
        "good-etf",
        _fill(
            "f-2",
            "sell-1",
            OrderSide.SELL,
            1000,
            price="3.00",
            commission="5.00",
            tax="1.00",
            traded_day=date(2026, 8, 11),
        ),
        bought.account.ledger_version,
    )

    assert notifications[-1].event == "FILLED"
    assert str(notifications[-1].amount) == "2994"
