"""Local end-to-end transport tests with fake QMT APIs, never real orders."""
import asyncio
import importlib.util
import time
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from bullet_trade.server.adapters.base import AccountRouter
from bullet_trade.server.adapters.big_qmt import (
    BigQmtBrokerAdapter, BigQmtGatewayError, _fill_trade_sides_from_orders,
    _normalize_order, build_big_qmt_bundle,
)
from bullet_trade.server.adapters.big_qmt_bridge import BridgeBrokerAdapter, BridgeDataAdapter
import bullet_trade.server.adapters.big_qmt_bridge as bridge_adapter_module
from bullet_trade.server.config import AccountConfig, ServerConfig
from bullet_trade.server.strategy.reconciliation import collect_async_broker_snapshot


def make_bundle(monkeypatch, tmp_path):
    monkeypatch.setenv("BIG_QMT_TRANSPORT", "bridge")
    monkeypatch.setenv("BIG_QMT_GATEWAY_PASSWORD", "test-token")
    monkeypatch.setenv("BIG_QMT_BRIDGE_PORT", "0")
    config = ServerConfig(server_type="big_qmt", accounts=[AccountConfig("default", "fake")],
                          strategy_database_path=str(tmp_path / "ledger.db"))
    router = AccountRouter(config.accounts)
    return build_big_qmt_bundle(config, router), router.get("default")


@asynccontextmanager
async def connected(monkeypatch, tmp_path):
    bundle, account = make_bundle(monkeypatch, tmp_path)
    broker, data = bundle.broker_adapter, bundle.data_adapter
    await broker.start()
    await data.start()
    path = Path(__file__).parents[2] / "helpers/big_qmt_bridge.py"
    spec = importlib.util.spec_from_file_location("qmt_bridge_e2e", path)
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)
    script.BRIDGE_PORT = broker.client.bridge.port
    script.BRIDGE_TOKEN = "test-token"
    script.ENABLE_TRADING = script.ENABLE_CANCEL = True
    orders, trades, writes, subscriptions = [], [], [], []
    def place(*args):
        writes.append(args)
        now = time.localtime()
        orders.append(dict(m_strOrderSysID="native-order-" + str(len(writes)), m_strInstrumentID=args[3].split(".")[0],
            m_strExchangeID=args[3].split(".")[1], m_nOpType=args[0], m_nVolumeTotalOriginal=args[6],
            m_nVolumeTraded=0, m_nOrderStatus=50, m_dLimitPrice=args[5], m_strRemark=args[9],
            m_strInsertDate=time.strftime("%Y%m%d", now), m_strInsertTime=time.strftime("%H:%M:%S", now)))
        script.order_callback(context, orders[-1])
    def cancel(order_id, *args):
        for row in orders:
            if row["m_strOrderSysID"] == order_id:
                row["m_nOrderStatus"] = 54
                script.order_callback(context, row)
    script.passorder, script.cancel = place, cancel
    script.get_trade_detail_data = lambda account, typ, kind: {
        "account": [dict(m_dAvailable=10000, m_dBalance=10000, m_dInstrumentValue=0)],
        "position": [], "order": orders, "deal": trades}[kind]
    def subscribe(code, **kwargs):
        subscriptions.append(code)
        return len(subscriptions)
    context = SimpleNamespace(get_instrument_detail=lambda _: {"UpStopPrice": 1.1, "DownStopPrice": .9, "PriceTick": .001},
        get_full_tick=lambda codes: {c: dict(lastPrice=1, askPrice=[1.001], bidPrice=[.999], time=int(time.time()*1000)) for c in codes},
        subscribe_quote=subscribe, unsubscribe_quote=lambda _: None,
        get_stock_list_in_sector=lambda _: ["511880.SH"])
    runtime = script.BridgeRuntime(context, "fake")
    script._runtime = runtime
    async def pump():
        while True:
            runtime.pump()
            await asyncio.sleep(.005)
    task = asyncio.create_task(pump())
    try:
        for _ in range(100):
            if runtime.welcomed:
                break
            await asyncio.sleep(.01)
        assert runtime.welcomed
        yield SimpleNamespace(bundle=bundle, broker=broker, data=data, account=account, runtime=runtime,
                              script=script, orders=orders, trades=trades, writes=writes, subscriptions=subscriptions)
    finally:
        runtime.close()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await data.stop()
        await broker.stop()


def test_factory_preserves_http_default(monkeypatch, tmp_path):
    bundle, account = make_bundle(monkeypatch, tmp_path)
    assert isinstance(bundle.broker_adapter, BridgeBrokerAdapter)
    assert bundle.broker_adapter.supports_parallel_snapshot_reads is True
    monkeypatch.delenv("BIG_QMT_TRANSPORT")
    cfg = ServerConfig(accounts=[account.config])
    http_broker = build_big_qmt_bundle(cfg, AccountRouter(cfg.accounts)).broker_adapter
    assert type(http_broker) is BigQmtBrokerAdapter
    assert not getattr(http_broker, "supports_parallel_snapshot_reads", False)
    monkeypatch.setenv("BIG_QMT_TRANSPORT", "typo")
    with pytest.raises(ValueError):
        build_big_qmt_bundle(cfg, AccountRouter(cfg.accounts))


@pytest.mark.parametrize("status, expected", [(53, "partly_canceled"), (54, "cancelled"), (55, "partly_filled")])
def test_official_native_statuses(status, expected):
    assert _normalize_order({"raw_status": status})["status"] == expected


def test_missing_trade_side_requires_matching_order_id_security_and_day():
    orders = [
        {"order_id": "O-1", "security": "159118.XSHE", "side": "BUY",
         "order_time": "2026-09-28 10:00:00"},
    ]
    trades = [
        {"order_id": "O-1", "security": "159118.XSHE", "side": "",
         "time": "2026-09-28 10:00:00"},
        {"order_id": "O-1", "security": "159119.XSHE", "side": "",
         "time": "2026-09-28 10:00:00"},
        {"order_id": "O-1", "security": "159118.XSHE", "side": "",
         "time": "2026-09-29 10:00:00"},
        {"order_id": "O-missing", "security": "159118.XSHE", "side": "",
         "time": "2026-09-28 10:00:00"},
        {"order_id": "O-1", "security": "159118.XSHE", "side": "SELL",
         "time": "2026-09-28 10:00:00"},
    ]
    sides = [row["side"] for row in _fill_trade_sides_from_orders(trades, orders)]
    assert sides == ["BUY", "", "", "", "SELL"]
    conflicting = orders + [dict(orders[0], side="SELL")]
    assert _fill_trade_sides_from_orders(trades[:1], conflicting)[0]["side"] == ""


@pytest.mark.asyncio
async def test_live_bridge_fills_missing_trade_sides_from_orders_and_history(monkeypatch, tmp_path):
    async with connected(monkeypatch, tmp_path) as e:
        today = time.strftime("%Y%m%d")
        now = time.strftime("%H:%M:%S")
        for number, op_type, offset in ((1, 23, 48), (2, 24, 49)):
            order_id = "native-order-{}".format(number)
            e.orders.append(dict(
                m_strOrderSysID=order_id, m_strInstrumentID="159118",
                m_strExchangeID="SZ", m_nOpType=op_type,
                m_nVolumeTotalOriginal=100, m_nVolumeTraded=100,
                m_nOrderStatus=56, m_strInsertDate=today, m_strInsertTime=now,
            ))
            e.trades.append(dict(
                m_strOrderSysID=order_id, m_strTradeID="native-fill-{}".format(number),
                m_strInstrumentID="159118", m_strExchangeID="SZ",
                m_nDirection=48, m_nOffsetFlag=offset, m_nVolume=100,
                m_dTradePrice=1.002, m_dTradeAmount=100.2,
                m_strTradeDate=today, m_strTradeTime=now,
            ))
        live = await e.broker.list_trades(e.account)
        assert [row["side"] for row in live] == ["BUY", "SELL"]
        e.orders.clear()
        e.trades.clear()
        history = await e.broker.list_trades(e.account, {"include_history": True})
        assert [row["side"] for row in history] == ["BUY", "SELL"]


@pytest.mark.asyncio
async def test_bridge_snapshot_shares_order_query_for_missing_trade_side(monkeypatch, tmp_path):
    async with connected(monkeypatch, tmp_path) as e:
        day, now = time.strftime("%Y%m%d"), time.strftime("%H:%M:%S")
        e.orders.append(dict(
            m_strOrderSysID="snapshot-order", m_strInstrumentID="159118", m_strExchangeID="SZ",
            m_nOpType=24, m_nVolumeTotalOriginal=100, m_nVolumeTraded=100,
            m_nOrderStatus=56, m_strInsertDate=day, m_strInsertTime=now,
        ))
        e.trades.append(dict(
            m_strOrderSysID="snapshot-order", m_strTradeID="snapshot-trade",
            m_strInstrumentID="159118", m_strExchangeID="SZ", m_nVolume=100,
            m_dTradePrice=1.002, m_dTradeAmount=100.2, m_strTradeDate=day, m_strTradeTime=now,
        ))
        paths = []
        post = e.broker.client.post

        async def counted_post(path, *args, **kwargs):
            paths.append(path)
            return await post(path, *args, **kwargs)

        monkeypatch.setattr(e.broker.client, "post", counted_post)
        snapshot = await collect_async_broker_snapshot(e.broker, e.account)
        assert sorted(paths) == ["/account", "/orders", "/positions", "/trades"]
        assert snapshot.trades[0]["side"] == "SELL" and not e.writes
        assert e.broker._history.list_trades("default")[0]["side"] == "SELL"
        # Across a native query reset the same durable order evidence remains.
        e.orders.clear()
        e.trades.clear()
        paths.clear()
        restored = await collect_async_broker_snapshot(e.broker, e.account)
        assert restored.trades[0]["side"] == "SELL"
        assert sorted(paths) == ["/account", "/orders", "/positions", "/trades"]


@pytest.mark.asyncio
@pytest.mark.parametrize("mismatch", ["security", "day", "id"])
async def test_snapshot_trade_side_keeps_identity_boundaries(monkeypatch, tmp_path, mismatch):
    bundle, account = make_bundle(monkeypatch, tmp_path)
    broker = bundle.broker_adapter
    order = dict(order_id="same", security="159118.XSHE", side="BUY",
                 order_time="2026-10-13 09:30:00")
    trade = dict(order_id="same", trade_id="fill", security="159118.XSHE", side="",
                 time="2026-10-13 09:30:01", amount=100, price=1.002)
    trade.update({"security": "159119.XSHE"} if mismatch == "security" else
                 {"time": "2026-10-14 09:30:00"} if mismatch == "day" else {"order_id": "other"})

    async def read_trades(*args):
        return [trade]

    async def read_orders():
        return [order]

    monkeypatch.setattr(broker, "_query_trades", read_trades)
    result = await broker._list_snapshot_trades(account, asyncio.create_task(read_orders()))
    assert result[0]["side"] == ""


@pytest.mark.asyncio
async def test_snapshot_order_read_failure_is_not_hidden_or_retried(monkeypatch, tmp_path):
    bundle, account = make_bundle(monkeypatch, tmp_path)
    broker = bundle.broker_adapter
    calls = []

    async def post(path, *args, **kwargs):
        calls.append(path)
        if path == "/orders":
            raise BigQmtGatewayError("order query unavailable")
        return {"available_cash": 10000} if path == "/account" else []

    monkeypatch.setattr(broker.client, "post", post)
    with pytest.raises(BigQmtGatewayError, match="order query unavailable"):
        await collect_async_broker_snapshot(broker, account)
    await asyncio.sleep(0)
    assert calls.count("/orders") == 1 and "/place_order" not in calls


@pytest.mark.asyncio
async def test_timing_uses_stdout_without_file_logger_and_keeps_native_order(monkeypatch, tmp_path, capsys):
    async with connected(monkeypatch, tmp_path) as e:
        def forbidden(*args, **kwargs):
            pytest.fail('Timing must bypass application/file log handlers')

        monkeypatch.setattr(bridge_adapter_module.log, 'info', forbidden)
        result = await e.broker.place_order(e.account, dict(
            security='510300.XSHG', amount=100, side='BUY',
            style={'type': 'limit', 'price': '1.002'},
            order_remark='bt:abcdef:123456789abc', wait_timeout=0))
        assert result['order_id'] == 'native-order-1' and not result.get('submit_unknown')
        assert len(e.writes) == 1 and e.writes[0][4:7] == (11, 1.002, 100)
        assert e.broker._history.list_orders('default')[0]['order_id'] == 'native-order-1'
        output = capsys.readouterr().out
        assert 'QMT原生回报时点 | ' in output and 'QMT委托确认 | ' in output


@pytest.mark.asyncio
async def test_roundtrip_native_query_submit_partial_fill_cancel_and_history(monkeypatch, tmp_path):
    async with connected(monkeypatch, tmp_path) as e:
        events = []
        e.broker.add_event_listener(lambda *args: events.append(args))
        info = await e.broker.get_account_info(e.account)
        assert info["available_cash"] == 10000
        assert e.broker.qmt_status()["ready"] and e.broker.qmt_status()["cancel_order_enabled"]
        tag = "bt:abcdef:123456789abc"
        result = await e.broker.place_order(e.account, dict(security="510300.XSHG", amount=100, side="BUY",
            style={"type": "limit", "price": "1.002"}, order_remark=tag, wait_timeout=0))
        assert result["order_id"] == "native-order-1" and result["status"] == "open"
        assert len(e.writes) == 1 and e.writes[0][4:7] == (11, 1.002, 100)
        assert result["order_remark"] == tag
        e.orders[0].update(m_nVolumeTraded=40, m_nOrderStatus=55)
        e.script.order_callback(None, e.orders[0])
        trade = dict(m_strOrderSysID="native-order-1", m_strTradeID="native-fill", m_strInstrumentID="510300",
            m_strExchangeID="SH", m_nOpType=23, m_nVolume=40, m_dTradePrice=1.002,
            m_strTradeDate=time.strftime("%Y%m%d"), m_strTradeTime=time.strftime("%H:%M:%S"), m_strRemark=tag)
        e.trades.append(trade)
        e.script.deal_callback(None, trade)
        e.script.deal_callback(None, trade)  # Duplicate callback: durable history remains unique.
        await asyncio.sleep(.05)
        assert any(kind == "trade" for _, kind, _ in events)
        assert (await e.broker.list_orders(e.account))[0]["status"] == "partly_filled"
        fills = await e.broker.list_trades(e.account)
        assert len(fills) == 1 and fills[0]["commission_known"] is False
        assert e.broker.remember_trade_market_quote(dict(
            fills[0], _bt_market_last_price=1.003,
            _bt_market_quote_time="{}+08:00".format(time.strftime("%Y-%m-%dT%H:%M:%S")),
        ))
        assert (await e.broker.list_trades(e.account, {"include_history": True}))[0]["_bt_market_last_price"] == 1.003
        cancel = await e.broker.cancel_order_request(e.account, {"order_id": result["order_id"]})
        assert cancel["value"] is True and cancel["status"] == "cancelled"
        e.orders.clear()
        e.trades.clear()  # Simulate native next-day query returning no previous records.
        assert await e.broker.list_orders(e.account) == []
        assert len(await e.broker.list_orders(e.account, {"include_history": True})) == 1
        assert len(await e.broker.list_trades(e.account, {"include_history": True})) == 1
        # A fresh adapter reads the SAME existing history; no second ledger file.
        rebuilt, account = make_bundle(monkeypatch, tmp_path)
        store = rebuilt.broker_adapter._history
        assert store.list_orders("default")[0]["order_remark"] == tag
        assert len(store.list_trades("default")) == 1
        assert store.list_trades("default")[0]["_bt_market_last_price"] == 1.003


@pytest.mark.asyncio
@pytest.mark.parametrize("security, pr_type", [("510300.XSHG", 42), ("159919.XSHE", 47)])
async def test_market_type_and_protection_unchanged(monkeypatch, tmp_path, security, pr_type):
    async with connected(monkeypatch, tmp_path) as e:
        await e.broker.place_order(e.account, dict(security=security, amount=100, side="SELL",
            style={"type": "market", "protect_price": "0.985"}, order_remark="bt:abcdef:123456789abc"))
        assert e.writes[0][0] == 24 and e.writes[0][4:7] == (pr_type, .985, 100)


@pytest.mark.asyncio
async def test_quotes_callbacks_multi_strategy_and_reconnect(monkeypatch, tmp_path):
    async with connected(monkeypatch, tmp_path) as e:
        ticks = []
        e.data.add_tick_listener(ticks.append)
        await e.data.replace_execution_quotes("one", ["510300.XSHG"])
        await e.data.replace_execution_quotes("two", ["159919.XSHE"])
        assert set(e.runtime.subscriptions) == {"510300.SH", "159919.SZ"}
        e.runtime.on_quote({"510300.SH": [{"lastPrice": 1, "time": 100}, {"lastPrice": 2, "time": 200}]})
        await asyncio.sleep(.05)
        assert [r["time"] for r in ticks[-1]["510300.XSHG"]] == [100, 200]
        tick = await e.data.get_current_tick("510300.XSHG")
        assert tick["UpStopPrice"] == 1.1 and tick["PriceTick"] == .001
        e.broker.client.bridge._disconnect()
        await asyncio.sleep(.03)
        assert not e.broker.qmt_status()["ready"]
        e.runtime.next_connect = 0
        for _ in range(100):
            if e.broker.qmt_status()["ready"] and e.data._applied is not None:
                break
            await asyncio.sleep(.01)
        assert e.broker.qmt_status()["ready"]
        await e.data.replace_execution_quotes("one", [])
        assert set(e.runtime.subscriptions) == {"159919.SZ"}
        with pytest.raises(BigQmtGatewayError, match="historical"):
            await e.data.get_history({})


@pytest.mark.asyncio
async def test_disabled_native_gate_is_explicit_not_unknown(monkeypatch, tmp_path):
    async with connected(monkeypatch, tmp_path) as e:
        e.script.ENABLE_TRADING = False
        with pytest.raises(BigQmtGatewayError) as error:
            await e.broker.place_order(e.account, dict(security="510300.XSHG", amount=100, side="BUY",
                style={"type": "limit", "price": "1.002"}))
        assert error.value.broker_called is False and not e.writes
        assert not e.broker._submission_callbacks


def trace_commands(e, monkeypatch):
    paths = []
    original = e.runtime.dispatch
    def dispatch(action, payload, boundary):
        if action in {"/place_order", "/orders"}:
            paths.append(action)
        return original(action, payload, boundary)
    monkeypatch.setattr(e.runtime, "dispatch", dispatch)
    return paths


def buy_request(**kwargs):
    return dict(security="510300.XSHG", amount=100, side="BUY",
                style={"type": "limit", "price": "1.002"},
                order_remark="bt:abcdef:123456789abc", **kwargs)


@pytest.mark.asyncio
@pytest.mark.parametrize("delay", [0, .03])
async def test_stable_native_callback_confirms_without_order_queries(monkeypatch, tmp_path, delay):
    async with connected(monkeypatch, tmp_path) as e:
        paths = trace_commands(e, monkeypatch)
        original = e.script.order_callback
        def callback(context, row):
            # Same-ID repeats update one observation, not two candidates.
            asyncio.get_running_loop().call_later(delay, original, context, row)
            asyncio.get_running_loop().call_later(delay, original, context, row)
        monkeypatch.setattr(e.script, "order_callback", callback)
        result = await e.broker.place_order(e.account, buy_request())
        assert result["order_id"] == "native-order-1"
        assert paths == ["/place_order"] and len(e.writes) == 1
        assert result["_bt_submit_timing"]["qmt_passorder_at_ms"] > 0
        assert not e.broker._submission_callbacks


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["missing", "tag", "security", "side", "amount", "id", "time", "conflict"])
async def test_unusable_callback_falls_back_to_query_never_resubmits(monkeypatch, tmp_path, bad):
    monkeypatch.setattr(bridge_adapter_module, "CALLBACK_CONFIRM_GRACE_SECONDS", .02)
    async with connected(monkeypatch, tmp_path) as e:
        paths = trace_commands(e, monkeypatch)
        original = e.script.order_callback
        def callback(context, row):
            if bad == "missing":
                return
            fields = {
                "tag": {"m_strRemark": "bt:000000:000000000000"},
                "security": {"m_strInstrumentID": "159919"},
                "side": {"m_nOpType": 24}, "amount": {"m_nVolumeTotalOriginal": 200},
                "id": {"m_strOrderSysID": "0"},
                "time": {"m_strInsertDate": "20200101"},
                "conflict": {"m_strUserOrderId": "different-tag"},
            }
            original(context, dict(row, **fields[bad]))
        monkeypatch.setattr(e.script, "order_callback", callback)
        result = await e.broker.place_order(e.account, buy_request())
        assert result["order_id"] == "native-order-1"
        assert paths == ["/place_order", "/orders"] and len(e.writes) == 1
        assert not e.broker._submission_callbacks


@pytest.mark.asyncio
async def test_observed_old_id_is_not_acknowledgement_of_new_operation(monkeypatch, tmp_path):
    monkeypatch.setattr(bridge_adapter_module, "CALLBACK_CONFIRM_GRACE_SECONDS", .01)
    async with connected(monkeypatch, tmp_path) as e:
        e.broker._observed_order_ids.add("native-order-1")
        result = await e.broker.place_order(e.account, buy_request(wait_timeout=.02))
        assert result["submit_unknown"] is True
        assert len(e.writes) == 1 and not e.broker._submission_callbacks


@pytest.mark.asyncio
async def test_legacy_non_operation_tag_keeps_query_confirmation(monkeypatch, tmp_path):
    async with connected(monkeypatch, tmp_path) as e:
        paths = trace_commands(e, monkeypatch)
        request = buy_request()
        request["order_remark"] = "legacy-order"
        result = await e.broker.place_order(e.account, request)
        assert result["order_id"] == "native-order-1"
        assert paths == ["/orders", "/place_order", "/orders"]
        assert len(e.writes) == 1


@pytest.mark.asyncio
async def test_old_bridge_reply_without_native_timestamps_is_compatible(monkeypatch, tmp_path):
    async with connected(monkeypatch, tmp_path) as e:
        original = e.runtime.send
        def send(message):
            if message.get("type") == "response" and isinstance(message.get("value"), dict):
                message["value"].pop("_bt_submit_timing", None)
            original(message)
        monkeypatch.setattr(e.runtime, "send", send)
        result = await e.broker.place_order(e.account, buy_request())
        assert result["order_id"] == "native-order-1"
        assert "_bt_submit_timing" not in result and len(e.writes) == 1


@pytest.mark.asyncio
async def test_conflicting_stable_callback_ids_remain_unknown(monkeypatch, tmp_path):
    monkeypatch.setattr(bridge_adapter_module, "CALLBACK_CONFIRM_GRACE_SECONDS", .01)
    async with connected(monkeypatch, tmp_path) as e:
        original = e.script.passorder
        def place(*args):
            original(*args)
            other = dict(e.orders[-1], m_strOrderSysID="other-order")
            e.orders.append(other)
            e.script.order_callback(None, other)
        monkeypatch.setattr(e.script, "passorder", place)
        result = await e.broker.place_order(e.account, buy_request(wait_timeout=.03))
        assert result["submit_unknown"] is True and len(e.writes) == 1
        assert not e.broker._submission_callbacks


@pytest.mark.asyncio
async def test_disconnect_does_not_use_replacement_callback_or_resend(monkeypatch, tmp_path):
    async with connected(monkeypatch, tmp_path) as e:
        monkeypatch.setattr(e.script, "order_callback", lambda *args: None)
        task = asyncio.create_task(e.broker.place_order(e.account, buy_request(wait_timeout=.1)))
        for _ in range(100):
            if e.writes:
                break
            await asyncio.sleep(.005)
        assert len(e.writes) == 1
        pending = e.broker._submission_callbacks["bt:abcdef:123456789abc"]
        e.broker.client.bridge._disconnect()
        e.broker._event("order", e.script._order(e.orders[0]))
        assert pending.disconnected and not pending.orders
        try:
            result = await task
        except BigQmtGatewayError:
            pass
        else:
            assert result["submit_unknown"] is True
        assert len(e.writes) == 1 and not e.broker._submission_callbacks


@pytest.mark.asyncio
async def test_concurrent_same_tag_is_not_sent_twice_and_waiter_is_cleaned(monkeypatch, tmp_path):
    async with connected(monkeypatch, tmp_path) as e:
        monkeypatch.setattr(e.script, "order_callback", lambda *args: None)
        task = asyncio.create_task(e.broker.place_order(e.account, buy_request()))
        for _ in range(100):
            if e.writes:
                break
            await asyncio.sleep(.005)
        with pytest.raises(BigQmtGatewayError) as error:
            await e.broker.place_order(e.account, buy_request())
        assert error.value.broker_called is False and len(e.writes) == 1
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not e.broker._submission_callbacks


@pytest.mark.asyncio
@pytest.mark.parametrize('cycle', [0, 1])
async def test_tplus_static_metadata_is_shared_and_reused_not_financial_data(monkeypatch, cycle):
    calls, ready, release = [], asyncio.Event(), asyncio.Event()
    async def post(path, payload):
        calls.append((path, payload))
        ready.set()
        await release.wait()
        return cycle
    data = BridgeDataAdapter(SimpleNamespace(bridge=SimpleNamespace(listeners=[]), post=post))
    monkeypatch.setattr(bridge_adapter_module, 'get_security_tplus_override', lambda _: None)
    first = asyncio.create_task(data.get_tplus('TEST.XSHG'))
    await ready.wait()
    readers = [asyncio.create_task(data.get_tplus('TEST.XSHG')) for _ in range(9)]
    await asyncio.sleep(0)
    assert len(calls) == 1
    release.set()
    assert await first == cycle and await asyncio.gather(*readers) == [cycle]*9
    assert [await data.get_tplus('TEST.XSHG') for _ in range(10)] == [cycle]*10
    assert len(calls) == 1 and not data._tplus_inflight
    assert calls[0][0] == '/data/tplus'  # Never caches cash/positions/orders/trades.


@pytest.mark.asyncio
@pytest.mark.parametrize('invalidate', ['day', 'reconnect', 'disconnect'])
async def test_tplus_cache_is_invalidated_by_session_or_china_date(monkeypatch, invalidate):
    calls, day = [], ['2026-10-19']
    async def post(path, payload):
        calls.append(path)
        return 0
    data = BridgeDataAdapter(SimpleNamespace(bridge=SimpleNamespace(listeners=[]), post=post))
    monkeypatch.setattr(bridge_adapter_module, 'get_security_tplus_override', lambda _: None)
    monkeypatch.setattr(data, '_metadata_day', lambda: day[0])
    assert await data.get_tplus('TEST') == 0
    if invalidate == 'day':
        day[0] = '2026-10-20'
    else:
        data._event('disconnected' if invalidate == 'disconnect' else 'connected', None)
        if data._restore_task is not None:
            await data._restore_task
    assert await data.get_tplus('TEST') == 0
    assert calls.count('/data/tplus') == 2


@pytest.mark.asyncio
async def test_tplus_failure_is_not_cached_and_explicit_override_is_checked_first(monkeypatch):
    calls, override = [], [None]
    async def post(*args):
        calls.append(True)
        if len(calls) == 1:
            raise OSError('temporary read failure')
        return 1
    data = BridgeDataAdapter(SimpleNamespace(bridge=SimpleNamespace(listeners=[]), post=post))
    monkeypatch.setattr(bridge_adapter_module, 'get_security_tplus_override', lambda _: override[0])
    with pytest.raises(OSError):
        await data.get_tplus('TEST')
    assert await data.get_tplus('TEST') == 1
    override[0] = 0
    assert await data.get_tplus('TEST') == 0 and len(calls) == 2


@pytest.mark.asyncio
async def test_invalid_tplus_cycle_is_not_cached(monkeypatch):
    calls = []
    async def post(*args):
        calls.append(True)
        return 2 if len(calls) == 1 else 1
    data = BridgeDataAdapter(SimpleNamespace(bridge=SimpleNamespace(listeners=[]), post=post))
    monkeypatch.setattr(bridge_adapter_module, 'get_security_tplus_override', lambda _: None)
    assert await data.get_tplus('TEST') == 2  # Preserve the original result for caller validation.
    assert not data._tplus_cache
    assert await data.get_tplus('TEST') == 1 and len(calls) == 2


@pytest.mark.asyncio
async def test_stop_cancels_pending_metadata_read_and_clears_cache(monkeypatch):
    entered, canceled, stopped = asyncio.Event(), asyncio.Event(), []
    async def post(*args):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            canceled.set()
    async def stop_bridge():
        stopped.append(True)
    client = SimpleNamespace(bridge=SimpleNamespace(listeners=[], stop=stop_bridge), post=post)
    data = BridgeDataAdapter(client)
    monkeypatch.setattr(bridge_adapter_module, 'get_security_tplus_override', lambda _: None)
    data._tplus_cache['previous'] = 1
    reader = asyncio.create_task(data.get_tplus('TEST'))
    await entered.wait()
    await data.stop()
    with pytest.raises(asyncio.CancelledError):
        await reader
    assert canceled.is_set() and not data._tplus_inflight and not data._tplus_cache
    assert stopped == [True]


@pytest.mark.asyncio
async def test_stale_tplus_inflight_response_cannot_fill_new_connection_cache(monkeypatch):
    calls, entered, release = [], asyncio.Event(), asyncio.Event()
    async def post(*args):
        calls.append(True)
        if len(calls) == 1:
            entered.set()
            await release.wait()
            return 0
        return 1
    data = BridgeDataAdapter(SimpleNamespace(bridge=SimpleNamespace(listeners=[]), post=post))
    monkeypatch.setattr(bridge_adapter_module, 'get_security_tplus_override', lambda _: None)
    pending = asyncio.create_task(data.get_tplus('TEST'))
    await entered.wait()
    data._event('disconnected', None)
    assert await data.get_tplus('TEST') == 1
    release.set()
    assert await pending == 0  # The old caller can observe its read, never seed the new cache.
    assert await data.get_tplus('TEST') == 1 and len(calls) == 2


@pytest.mark.asyncio
async def test_canceling_one_metadata_reader_does_not_cancel_other_readers(monkeypatch):
    entered, release = asyncio.Event(), asyncio.Event()
    async def post(*args):
        entered.set()
        await release.wait()
        return 0
    data = BridgeDataAdapter(SimpleNamespace(bridge=SimpleNamespace(listeners=[]), post=post))
    monkeypatch.setattr(bridge_adapter_module, 'get_security_tplus_override', lambda _: None)
    first = asyncio.create_task(data.get_tplus('TEST'))
    await entered.wait()
    other = asyncio.create_task(data.get_tplus('TEST'))
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    release.set()
    assert await other == 0 and await data.get_tplus('TEST') == 0
