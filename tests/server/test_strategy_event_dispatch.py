from bullet_trade.server import app as app_module
from bullet_trade.server.app import ServerApplication
from bullet_trade.server.feishu_notifier import TradeNotification


def test_strategy_event_is_logged_when_feishu_is_disabled(monkeypatch):
    messages = []
    monkeypatch.setattr(app_module.log, "info", messages.append)
    app = object.__new__(ServerApplication)
    app.feishu_notifier = None

    delivery = app._publish_strategy_event(TradeNotification(
        event="FILLED", strategy_id="good_etf",
        security="510050.XSHG", security_name="上证50ETF",
        side="BUY", status="FILLED", quantity=100,
        price="2.5", amount="250",
    ))

    assert delivery == {"local_logged": True, "feishu_queued": False}
    assert "event=FILLED" in messages[0]
    assert "上证50ETF（510050.XSHG）" in messages[0]


def test_strategy_event_is_logged_before_feishu_is_queued(monkeypatch):
    sequence = []
    monkeypatch.setattr(
        app_module.log, "info", lambda message: sequence.append(("log", message))
    )
    app = object.__new__(ServerApplication)
    app.feishu_notifier = type("Notifier", (), {
        "queue_message": lambda self, notice: sequence.append(("feishu", notice))
    })()
    notice = TradeNotification(
        event="ORDER_SUBMITTED", strategy_id="good_etf",
        security="510050.XSHG", side="BUY", status="SUBMITTED",
    )

    delivery = app._publish_strategy_event(notice)

    assert delivery == {"local_logged": True, "feishu_queued": True}
    assert [item[0] for item in sequence] == ["log", "feishu"]
