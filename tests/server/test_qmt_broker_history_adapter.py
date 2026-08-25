import pytest

from bullet_trade.server.adapters.base import AccountRouter
from bullet_trade.server.adapters.qmt import QmtBrokerAdapter
from bullet_trade.server.config import AccountConfig, ServerConfig


class _EmptyBroker:
    def get_orders(self, **_filters):
        return []

    def get_trades(self, **_filters):
        return []


def _adapter(database):
    config = ServerConfig(
        enable_data=False,
        enable_broker=True,
        accounts=[AccountConfig(key="default", account_id="demo")],
        strategy_database_path=str(database),
    )
    router = AccountRouter(config.accounts)
    adapter = QmtBrokerAdapter(config, router)
    adapter.guard.mark_ready()
    adapter._brokers["default"] = _EmptyBroker()
    return adapter, router.get("default")


@pytest.mark.asyncio
async def test_qmt_callback_history_is_available_after_adapter_restart(tmp_path):
    database = tmp_path / "ledger.db"
    first, _ = _adapter(database)
    first._notify_broker_event(
        "default",
        "order",
        {
            "order_id": "O-previous",
            "security": "510300.XSHG",
            "status": "filled",
        },
    )
    first._notify_broker_event(
        "default",
        "trade",
        {
            "trade_id": "T-previous",
            "order_id": "O-previous",
            "security": "510300.XSHG",
            "amount": 100,
            "price": 4.0,
            "commission_known": False,
            "tax_known": False,
        },
    )

    restarted, context = _adapter(database)
    assert await restarted.list_orders(context, {}) == []
    assert await restarted.list_trades(context, {}) == []

    orders = await restarted.list_orders(context, {"include_history": True})
    trades = await restarted.list_trades(context, {"include_history": True})
    assert [row["order_id"] for row in orders] == ["O-previous"]
    assert [row["trade_id"] for row in trades] == ["T-previous"]
    assert trades[0]["commission_known"] is False
