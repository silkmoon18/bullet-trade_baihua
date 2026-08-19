import time

import pytest


from bullet_trade.broker.qmt import QmtBroker


class DummyTrader:
    pass


class _TraceTrader:
    """提供订单和成交查询的 QMT 测试桩。"""

    def query_stock_orders(self, _account):
        """返回一条测试订单。

        Args:
            _account: 测试账号对象。

        Returns:
            list: QMT 风格订单列表。
        """

        return [
            {
                "order_id": "OID-TRACE",
                "stock_code": "588000.SH",
                "order_volume": 12000,
                "price": 1.756,
                "order_status": 50,
                "traded_volume": 0,
                "order_type": 23,
                "order_remark": "bt:frank_byf_b4:trace",
                "strategy_name": "bullet-trade",
            }
        ]

    def query_stock_trades(self, _account):
        """返回一条测试成交。

        Args:
            _account: 测试账号对象。

        Returns:
            list: QMT 风格成交列表。
        """

        return [
            {
                "order_id": "OID-TRACE",
                "trade_id": "T-TRACE",
                "stock_code": "588000.SH",
                "trade_volume": 12000,
                "trade_price": 1.756,
                "trade_time": "09:45:31",
            }
        ]


@pytest.mark.asyncio
async def test_split_volume_and_async_wait(monkeypatch):
    broker = QmtBroker(account_id="test")
    broker._connected = True

    calls = []

    def fake_send_order(security, amount, price, side):
        calls.append((security, amount, price, side))
        return f"id_{len(calls)}"

    # 配置：每单最大 1000，异步等待（0）
    monkeypatch.setattr(
        "bullet_trade.utils.env_loader.get_live_trade_config",
        lambda: {"order_max_volume": 1000, "trade_max_wait_time": 0},
    )

    broker._send_order = fake_send_order  # type: ignore

    # 触发拆单：2500 = 1000 + 1000 + 500
    first_id = await broker.buy("000001.XSHE", amount=2500, price=10.0)
    assert first_id == "id_1"
    assert calls == [
        ("000001.SZ", 1000, 10.0, "buy"),
        ("000001.SZ", 1000, 10.0, "buy"),
        ("000001.SZ", 500, 10.0, "buy"),
    ]


@pytest.mark.asyncio
async def test_sync_wait_breaks_early(monkeypatch):
    broker = QmtBroker(account_id="test")
    broker._connected = True

    # 配置：同步等待 1s
    monkeypatch.setattr(
        "bullet_trade.utils.env_loader.get_live_trade_config",
        lambda: {"order_max_volume": 1000000, "trade_max_wait_time": 1},
    )

    # 立即返回已成，_maybe_wait 应很快退出
    async def _status(_oid):
        return {"status": "filled"}

    broker.get_order_status = _status  # type: ignore
    t0 = time.time()
    await broker._maybe_wait("abc")
    assert time.time() - t0 < 1.0


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal_status", ["failed", "error"])
async def test_sync_wait_failed_like_status_breaks_early(monkeypatch, terminal_status):
    """同步等待遇到异常终态时应立即结束。

    Args:
        monkeypatch: pytest monkeypatch fixture。
        terminal_status: 测试用异常终态。

    Returns:
        None: 断言不会继续等待到超时。
    """
    broker = QmtBroker(account_id="test")
    broker._connected = True
    monkeypatch.setattr(
        "bullet_trade.utils.env_loader.get_live_trade_config",
        lambda: {"order_max_volume": 1000000, "trade_max_wait_time": 1},
    )

    async def _status(_oid):
        """返回异常终态订单状态。

        Args:
            _oid: 订单号。

        Returns:
            dict: 包含异常终态的订单快照。
        """
        return {"status": terminal_status}

    broker.get_order_status = _status  # type: ignore
    result = await broker._maybe_wait("abc")

    assert result["timed_out"] is False
    assert result["status"] == terminal_status


@pytest.mark.asyncio
async def test_zero_wait_override_skips_global_sync_wait(monkeypatch):
    broker = QmtBroker(account_id="test")
    broker._connected = True
    called = False

    monkeypatch.setattr(
        "bullet_trade.utils.env_loader.get_live_trade_config",
        lambda: {"order_max_volume": 1000000, "trade_max_wait_time": 16},
    )

    async def _status(_oid):
        nonlocal called
        called = True
        return {"status": "open"}

    broker.get_order_status = _status  # type: ignore
    t0 = time.time()
    result = await broker._maybe_wait("abc", override_timeout=0)
    assert time.time() - t0 < 0.2
    assert called is False
    assert result["status"] == "submitted"
    assert result["async_tracking"] is True


@pytest.mark.asyncio
async def test_sync_wait_timeout_records_last_snapshot(monkeypatch):
    """同步等待超时时应保留最后一次订单快照。"""
    broker = QmtBroker(account_id="test")
    broker._connected = True

    async def _status(_oid):
        """返回测试用在途订单状态。

        Args:
            _oid: 订单号。

        Returns:
            dict: 固定 open 状态快照。
        """
        return {"order_id": "abc", "status": "open", "raw_status": 50}

    async def _sleep(_interval):
        """跳过测试中的轮询睡眠。

        Args:
            _interval: 原计划睡眠时间。

        Returns:
            None: 测试中不真实等待。
        """

    broker.get_order_status = _status  # type: ignore
    monkeypatch.setattr("bullet_trade.broker.qmt.asyncio.sleep", _sleep)

    result = await broker._maybe_wait("abc", override_timeout=0.01)
    stored = broker.get_last_order_wait_result("abc")

    assert result["timed_out"] is True
    assert result["async_tracking"] is True
    assert result["status"] == "open"
    assert result["last_snapshot"]["raw_status"] == 50
    assert stored["last_snapshot"]["order_id"] == "abc"


@pytest.mark.asyncio
async def test_invalid_wait_timeout_falls_back_to_async_tracking():
    """无效等待窗口不应掩盖已经提交的远端订单号。"""

    broker = QmtBroker(account_id="test")
    result = await broker._maybe_wait("abc", override_timeout="bad-timeout")

    assert result["order_id"] == "abc"
    assert result["async_tracking"] is True
    assert result["wait_timeout"] == 0.0
    assert broker.get_last_order_wait_result("abc")["async_tracking"] is True


def test_qmt_symbol_mapping_roundtrip():
    broker = QmtBroker(account_id="test")
    assert broker._map_security("000001.XSHE") == "000001.SZ"
    assert broker._map_security("600000.XSHG") == "600000.SH"
    assert broker._map_to_jq_symbol("000001.SZ") == "000001.XSHE"
    assert broker._map_to_jq_symbol("300750.SZ") == "300750.XSHE"


def test_qmt_orders_and_trades_include_wait_trace_fields():
    """订单和成交查询应保留下单等待超时追踪字段。"""

    broker = QmtBroker(account_id="test")
    broker._connected = True
    broker._xt_account = object()
    broker._xt_trader = _TraceTrader()
    broker._last_order_wait_results["OID-TRACE"] = {
        "timed_out": True,
        "async_tracking": True,
        "wait_timeout": 30.0,
        "elapsed": 30.18,
        "last_snapshot": {
            "order_id": "OID-TRACE",
            "status": "open",
            "raw_status": 50,
            "order_remark": "bt:frank_byf_b4:trace",
            "strategy_name": "bullet-trade",
        },
    }

    orders = broker.get_orders(order_id="OID-TRACE")
    trades = broker.get_trades(order_id="OID-TRACE")

    assert orders[0]["security"] == "588000.XSHG"
    assert orders[0]["timed_out"] is True
    assert orders[0]["async_tracking"] is True
    assert orders[0]["last_snapshot"]["raw_status"] == 50
    assert trades[0]["timed_out"] is True
    assert trades[0]["order_remark"] == "bt:frank_byf_b4:trace"
    assert trades[0]["strategy_name"] == "bullet-trade"


def test_qmt_market_price_type_prefers_exchange_five_cancel_for_shenzhen_and_shanghai():
    broker = QmtBroker(account_id="test")

    class Const:
        MARKET_PEER_PRICE_FIRST = 11
        MARKET_MINE_PRICE_FIRST = 12
        MARKET_SH_CONVERT_5_CANCEL = 13
        MARKET_SZ_CONVERT_5_CANCEL = 14
        ANY_PRICE = 15
        FIX_PRICE = 16

    assert broker._choose_market_price_type("600000.SH", Const) == Const.MARKET_SH_CONVERT_5_CANCEL
    assert broker._choose_market_price_type("000001.SZ", Const) == Const.MARKET_SZ_CONVERT_5_CANCEL
    assert broker._choose_market_price_type("600000.XSHG", Const) == Const.MARKET_SH_CONVERT_5_CANCEL
    assert broker._choose_market_price_type("000001.XSHE", Const) == Const.MARKET_SZ_CONVERT_5_CANCEL
    assert broker._choose_market_price_type("430047.BJ", Const) == Const.MARKET_PEER_PRICE_FIRST


def test_qmt_market_price_type_falls_back_to_exchange_specific_five_cancel():
    broker = QmtBroker(account_id="test")

    class Const:
        MARKET_PEER_PRICE_FIRST = None
        MARKET_MINE_PRICE_FIRST = None
        MARKET_SH_CONVERT_5_CANCEL = 13
        MARKET_SZ_CONVERT_5_CANCEL = 14
        ANY_PRICE = 15
        FIX_PRICE = 16

    assert broker._choose_market_price_type("600000.SH", Const) == Const.MARKET_SH_CONVERT_5_CANCEL
    assert broker._choose_market_price_type("000001.SZ", Const) == Const.MARKET_SZ_CONVERT_5_CANCEL
    assert broker._choose_market_price_type("430047.BJ", Const) == Const.MARKET_SH_CONVERT_5_CANCEL


def test_qmt_market_price_type_never_silently_falls_back_to_fixed_price():
    broker = QmtBroker(account_id="test")

    class Const:
        FIX_PRICE = 11
        PRICE_LIMIT = 12
        ORDER_PRICE_TYPE_LIMIT = 13

    assert broker._choose_market_price_type("600000.SH", Const) is None
