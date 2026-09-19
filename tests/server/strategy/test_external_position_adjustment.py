"""An external account position loss must not masquerade as a broker sell."""

import json
from datetime import datetime, timedelta

import pytest

from bullet_trade.server.strategy import (
    BrokerFill,
    BrokerOrder,
    OrderSide,
    OrderState,
    SQLiteCapitalService,
    SQLiteFillBookingService,
    SQLiteStrategyRepository,
    SQLiteValuationService,
    money_to_units,
    price_to_units,
)
from bullet_trade.server.strategy.domain import SHANGHAI_TZ
from bullet_trade.server.strategy.schema import connect_database
from scripts.reconcile_external_positions import (
    apply_adjustment,
    build_plan,
)


ACCOUNT = "good-etf-remote"
STRATEGY = "good_etf_remote"
SECURITY = "159613.XSHE"
REFERENCE = "external-position-adjustment-test"


def _ledger(tmp_path):
    database = tmp_path / "ledger.db"
    repository = SQLiteStrategyRepository(database)
    repository.initialize()
    repository.create_physical_account("qmt-main", "QMT", "sim-account")
    capital = SQLiteCapitalService(database)
    capital.calibrate_broker_available_cash(
        "qmt-main", money_to_units("20000")
    )
    capital.ensure_strategy_account(
        ACCOUNT, STRATEGY, "qmt-main", money_to_units("10000")
    )
    booking = SQLiteFillBookingService(database)
    now = datetime.now(SHANGHAI_TZ)
    traded_at = now - timedelta(days=1)
    booking.register_order(BrokerOrder(
        order_id="buy-1", account_id=ACCOUNT, intent_id=None,
        client_tag="tag-buy-1", broker_order_id="broker-buy-1",
        security=SECURITY, side=OrderSide.BUY, requested_qty=1000,
        filled_qty=0, state=OrderState.SUBMITTED,
        trading_day=traded_at.date(),
    ))
    capital.reserve_cash(ACCOUNT, money_to_units("2100"), 0, "buy-1")
    booking.book_fill(
        ACCOUNT,
        BrokerFill(
            fill_id="fill-buy-1", order_id="buy-1", fingerprint="fp-buy-1",
            broker_trade_id="trade-buy-1", security=SECURITY,
            side=OrderSide.BUY, quantity=1000,
            price_units=price_to_units("2"),
            commission_units=money_to_units("5"),
            tax_units=0, traded_at=traded_at,
        ),
        1, sellable_from_trade_date=now.date(),
    )
    connection = connect_database(database)
    try:
        account = connection.execute(
            "SELECT * FROM strategy_accounts WHERE strategy_id = ?",
            (STRATEGY,),
        ).fetchone()
        details = json.dumps({"blockers": [
            (
                "broker_position_insufficient:{}:"
                "strategy=(1000,1000):broker=(0,0)"
            ).format(SECURITY)
        ]})
        connection.execute(
            """INSERT INTO reconciliation_runs(reconciliation_id,
               physical_account_id, strategy_account_id, state, broker_as_of,
               details_json, started_at, completed_at)
               VALUES (?, ?, ?, 'BLOCKED', ?, ?, ?, ?)""",
            ("recon-1", "qmt-main", account["strategy_account_id"],
             now.isoformat(), details, now.isoformat(), now.isoformat()),
        )
        connection.execute(
            """UPDATE strategy_accounts SET status = 'RECONCILIATION_BLOCKED'
               WHERE strategy_account_id = ?""",
            (account["strategy_account_id"],),
        )
        connection.commit()
    finally:
        connection.close()
    return database


def test_external_position_adjustment_is_cashless_and_audited(tmp_path):
    database = _ledger(tmp_path)
    connection = connect_database(database)
    try:
        before = build_plan(connection, STRATEGY, {SECURITY: 1000}, REFERENCE)
    finally:
        connection.close()
    assert before.cost_units == money_to_units("2005")
    backup, applied = apply_adjustment(
        database, STRATEGY, {SECURITY: 1000}, REFERENCE,
        "simulated account position absent; sell evidence unavailable",
        tmp_path / "backups",
    )
    assert backup.is_file()
    assert applied == before
    connection = connect_database(database)
    try:
        account = connection.execute(
            "SELECT * FROM strategy_accounts WHERE strategy_id = ?",
            (STRATEGY,),
        ).fetchone()
        assert account["cash_units"] == money_to_units("7995")
        assert connection.execute(
            "SELECT total_qty FROM positions WHERE security = ?", (SECURITY,)
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT remaining_qty FROM position_lots WHERE security = ?",
            (SECURITY,),
        ).fetchone()[0] == 0
        flow = connection.execute(
            """SELECT flow_type, amount_units, reason FROM capital_flows
               WHERE external_ref = ?""",
            (REFERENCE,),
        ).fetchone()
        assert (flow["flow_type"], flow["amount_units"]) == (
            "ADJUSTMENT", money_to_units("2005")
        )
        entry = connection.execute(
            """SELECT entry_type, amount_units, payload_json
               FROM ledger_entries WHERE reference_id = ?""",
            (REFERENCE,),
        ).fetchone()
        assert entry["entry_type"] == "EXTERNAL_POSITION_ADJUSTMENT"
        assert entry["amount_units"] == 0
        assert json.loads(entry["payload_json"])["broker_sell_evidence"] == (
            "unavailable"
        )
        assert connection.execute(
            "SELECT COUNT(*) FROM strategy_orders"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM fills"
        ).fetchone()[0] == 1
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        connection.close()
    replayed = SQLiteStrategyRepository(database).replay_account(ACCOUNT)
    assert replayed.cash_units == money_to_units("7995")
    snapshot = SQLiteValuationService(database).create_snapshot(
        ACCOUNT, {}, datetime.now(SHANGHAI_TZ), timedelta(minutes=1)
    )
    assert snapshot.total_pnl_units == 0
    assert snapshot.realized_pnl_units == 0
    assert snapshot.performance_ready is False
    assert "capital_flows_unsupported" in snapshot.performance_blockers
    with pytest.raises(RuntimeError, match="reference already exists"):
        apply_adjustment(
            database, STRATEGY, {SECURITY: 1000}, REFERENCE, "same",
            tmp_path / "backups",
        )


def test_external_adjustment_rejects_changed_broker_evidence(tmp_path):
    database = _ledger(tmp_path)
    connection = connect_database(database)
    try:
        with pytest.raises(RuntimeError, match="positions changed"):
            build_plan(connection, STRATEGY, {SECURITY: 900}, REFERENCE)
        connection.execute(
            "UPDATE reconciliation_runs SET details_json = ?",
            (json.dumps({"blockers": ["broker_cash_insufficient"]}),),
        )
        connection.commit()
        with pytest.raises(RuntimeError, match="blockers differ"):
            build_plan(connection, STRATEGY, {SECURITY: 1000}, REFERENCE)
    finally:
        connection.close()
