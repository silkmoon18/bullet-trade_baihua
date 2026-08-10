from datetime import datetime

from bullet_trade.server.feishu_notifier import (
    FeishuNotifier,
    FeishuTradeNotifier,
    TradeNotification,
)
from bullet_trade.server.strategy.domain import SHANGHAI_TZ


def test_trade_notification_uses_interactive_card_with_required_fields():
    notifier = FeishuTradeNotifier("https://example.invalid/hook/test")
    payload = notifier.build_payload(
        TradeNotification(
            event="FILLED",
            security="510050.XSHG",
            side="BUY",
            status="FILLED",
            quantity=1000,
            price="2.5000",
            amount="2505.00",
            order_id="order-1",
            trade_id="trade-1",
            occurred_at=datetime(2026, 8, 10, 10, 0, tzinfo=SHANGHAI_TZ),
        )
    )

    assert payload["msg_type"] == "interactive"
    assert payload["card"]["header"]["template"] == "green"
    content = payload["card"]["elements"][0]["text"]["content"]
    assert "510050.XSHG" in content
    assert "¥2505.00" in content
    assert "1000" in content
    assert "¥2.5000" in content
    assert "order-1" in content
    assert "trade-1" in content


def test_rejected_order_card_keeps_empty_trade_values_visible():
    notifier = FeishuTradeNotifier("https://example.invalid/hook/test")
    payload = notifier.build_payload(
        TradeNotification(
            event="REJECTED",
            security="510300.XSHG",
            side="SELL",
            status="REJECTED",
            quantity=500,
            order_id="order-2",
        )
    )

    assert payload["card"]["header"]["template"] == "red"
    content = payload["card"]["elements"][0]["text"]["content"]
    assert "**金额：** ¥-" in content
    assert "**单价：** ¥-" in content


def test_legacy_notifier_is_drop_in_compatible(monkeypatch):
    monkeypatch.setenv(
        "FEISHU_WEBHOOK_URL", "https://example.invalid/hook/legacy"
    )
    notifier = FeishuNotifier()
    sent = []
    monkeypatch.setattr(notifier, "_send_request", sent.append)

    notifier.queue_message("legacy log message")
    notifier.flush()

    assert notifier.webhook_url.endswith("/legacy")
    assert sent[0]["msg_type"] == "interactive"
    assert "legacy log message" in str(sent[0])


def test_legacy_notifier_accepts_structured_trade_notification(monkeypatch):
    notifier = FeishuNotifier("https://example.invalid/hook/trade")
    sent = []
    monkeypatch.setattr(notifier._sender, "send", sent.append)
    trade = TradeNotification(
        event="FILLED",
        security="510050.XSHG",
        side="BUY",
        status="FILLED",
        quantity=100,
        price="2.50",
        amount="255.00",
    )

    notifier.queue_message(trade)
    notifier.flush()

    assert sent == [trade]
