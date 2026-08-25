from datetime import datetime

from bullet_trade.server.strategy.broker_history import (
    SQLiteBrokerHistoryStore,
    merge_broker_rows,
)
from bullet_trade.server.strategy.domain import SHANGHAI_TZ


def test_broker_history_survives_reopen_and_updates_order_state(tmp_path):
    database = tmp_path / "ledger.db"
    first = SQLiteBrokerHistoryStore(database)
    observed = datetime(2026, 8, 24, 9, 35, tzinfo=SHANGHAI_TZ)
    assert first.record_order(
        "default",
        {
            "order_id": "O-1",
            "security": "510300.XSHG",
            "status": "open",
            "order_remark": "good_etf|one",
        },
        observed,
    )
    assert first.record_order(
        "default", {"order_id": "O-1", "status": "filled"}, observed
    )

    reopened = SQLiteBrokerHistoryStore(database)
    rows = reopened.list_orders("default")
    assert len(rows) == 1
    assert rows[0]["security"] == "510300.XSHG"
    assert rows[0]["status"] == "filled"


def test_trade_history_preserves_known_fee_when_later_callback_omits_it(tmp_path):
    store = SQLiteBrokerHistoryStore(tmp_path / "ledger.db")
    store.record_trade(
        "default",
        {
            "trade_id": "T-1",
            "order_id": "O-1",
            "commission_known": True,
            "commission_fee": 1.25,
            "tax_known": False,
            "tax": 0.0,
        },
    )
    store.record_trade(
        "default",
        {
            "trade_id": "T-1",
            "order_id": "O-1",
            "commission_known": False,
            "commission_fee": 0.0,
            "tax_known": False,
            "tax": 0.0,
        },
    )

    trade = store.list_trades("default")[0]
    assert trade["commission_known"] is True
    assert trade["commission_fee"] == 1.25
    assert trade["tax_known"] is False


def test_history_filters_and_current_rows_win_merge(tmp_path):
    store = SQLiteBrokerHistoryStore(tmp_path / "ledger.db")
    store.record_order(
        "default",
        {"order_id": "O-1", "security": "510300.XSHG", "status": "open"},
    )
    store.record_order(
        "default",
        {"order_id": "O-2", "security": "159915.XSHE", "status": "filled"},
    )

    assert [row["order_id"] for row in store.list_orders("default", status="open")] == [
        "O-1"
    ]
    merged = merge_broker_rows(
        [{"order_id": "O-1", "status": "filled"}],
        store.list_orders("default"),
        "order_id",
    )
    by_id = {row["order_id"]: row for row in merged}
    assert by_id["O-1"]["status"] == "filled"
    assert by_id["O-1"]["_broker_history_only"] is False
    assert by_id["O-2"]["status"] == "filled"
    assert by_id["O-2"]["_broker_history_only"] is True
