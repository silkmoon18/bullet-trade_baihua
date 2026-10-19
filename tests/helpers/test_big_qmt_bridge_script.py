"""Full QMT API doubles only: these tests never contact a trading account."""
import importlib.util
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

SOURCE = Path(__file__).parents[2] / "helpers" / "big_qmt_bridge.py"


@pytest.fixture
def script():
    spec = importlib.util.spec_from_file_location("qmt_bridge_test", SOURCE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def command(key="one", action="/place_order", **payload):
    return dict(id=key, action=action, deadline=time.time() + 10, payload=dict(
        security="510300.XSHG", side="BUY", amount=100, price=1.002,
        qmt_user_order_id="bt:abcdef:123456789abc", **payload))


def runtime(script, context=None):
    instance = script.BridgeRuntime(context or SimpleNamespace(), "test-account")
    messages = []
    instance.send = messages.append
    return instance, messages


@pytest.mark.parametrize("price_type", [11, 42, 47])
def test_write_once_preserves_server_price_and_type(script, price_type):
    script.ENABLE_TRADING = True
    calls = []
    script.passorder = lambda *args: calls.append(args) or 0
    bridge, messages = runtime(script)
    msg = command(pr_type=price_type)
    bridge.command(msg)
    bridge.command(msg)
    assert len(calls) == 1
    assert calls[0][3:7] == ("510300.SH", price_type, 1.002, 100)
    assert calls[0][8:10] == (2, "bt:abcdef:123456789abc")
    assert messages[0] == messages[1]
    assert messages[0]["value"]["order_id"] == ""
    assert messages[0]["value"]["submit_unknown"] is True
    timing = messages[0]["value"]["_bt_submit_timing"]
    assert timing["qmt_received_at_ms"] <= timing["qmt_passorder_at_ms"] <= timing["qmt_passorder_returned_at_ms"]
    observed = messages[0]['timing']
    assert observed['qmt_api_calls']['passorder']['calls'] == 1
    assert observed['qmt_api_calls']['passorder']['ms'] >= 0
    assert observed['qmt_command_ms'] >= observed['qmt_api_calls']['passorder']['ms']


@pytest.mark.parametrize("action", ["/place_order", "/cancel_order"])
def test_writes_disabled_by_default(script, action):
    bridge, messages = runtime(script)
    bridge.command(command(action=action, order_id="test-order"))
    assert messages[-1]["ok"] is False
    assert messages[-1]["broker_called"] is False


def test_expired_no_write_and_read_commands_not_cached(script):
    bridge, messages = runtime(script)
    msg = command()
    msg["deadline"] = 0
    bridge.command(msg)
    assert not bridge.seen
    script.get_trade_detail_data = lambda *args: []
    bridge.command(command("read", "/orders"))
    assert messages[-1]["value"] == []
    assert not bridge.seen


def test_post_call_failure_is_unknown_and_not_replayed(script):
    script.ENABLE_TRADING = True
    calls = []
    def fail(*args):
        calls.append(args)
        raise RuntimeError("lost acknowledgement")
    script.passorder = fail
    bridge, messages = runtime(script)
    bridge.command(command())
    bridge.command(command())
    assert len(calls) == 1
    assert messages[-1]["broker_called"] is True


def test_native_fields_and_unknown_fee_are_not_invented(script):
    row = dict(m_strOrderSysID="real-order", m_strTradeID="real-trade", m_strInstrumentID="510300",
               m_strExchangeID="SH", m_nOpType=23, m_nVolume=100, m_dTradePrice=1.002,
               m_strTradeDate="20260912", m_strTradeTime="09:30:01", m_strRemark="bt:test")
    trade = script._trade(row)
    assert (trade["trade_id"], trade["order_id"], trade["time"]) == ("real-trade", "real-order", "2026-09-12 09:30:01")
    assert trade["commission_fee"] is None and trade["commission_known"] is False
    assert trade["tax"] is None and trade["tax_known"] is False
    assert script._trade({})["time"] is None
    assert script._order({})["order_id"] == ""
    assert script._order({"m_strOrderSysID": "0"})["order_id"] == ""
    assert script._trade({"m_strTradeID": -1})["trade_id"] == ""
    assert script._trade({"m_dTradeAmount": 1398.0})["deal_balance"] == 1398.0
    order = script._order({"m_dLimitPrice": 1.002, "m_dTradedPrice": 1.001, "m_strOrderRemark": "bt:tag"})
    assert (order["price"], order["order_price"], order["order_remark"]) == (1.001, 1.002, "bt:tag")


def test_unconvertible_native_raw_field_is_skipped(script):
    class NativeOrder:
        m_strOrderSysID = "native-order"
        m_strInstrumentID = "510300"
        m_strExchangeID = "SH"
        m_nOpType = 23

        @property
        def m_oOrderTag(self):
            raise TypeError("No to_python converter for CXtOrderTag")

    order = script._order(NativeOrder())
    assert order["order_id"] == "native-order"
    assert order["security"] == "510300.XSHG"
    assert "m_oOrderTag" not in order["raw"]


def test_tick_batch_subscription_lifecycle_and_callback_filter(script):
    context = SimpleNamespace(get_instrument_detail=lambda _: {"PriceTick": 0.001},
        subscribe_quote=lambda *args, **kwargs: 1, unsubscribe_quote=lambda _: None)
    bridge, events = runtime(script, context)
    bridge.welcomed = True
    bridge.subscriptions_to(["510300.XSHG"])
    bridge.on_quote({"510300.SH": [{"lastPrice": 1, "time": 100}, {"lastPrice": 2, "time": 200}]})
    assert [row["time"] for row in events[-1]["payload"]["510300.XSHG"]] == [100, 200]
    script._runtime = bridge
    script.order_callback(context, {"m_strAccountID": "another"})
    assert len(events) == 1
    script.stop(context)
    script.deal_callback(context, {})
    assert len(events) == 1 and bridge.closed and not bridge.subscriptions


def test_init_timer_and_historical_bars_do_not_order(script):
    calls = []
    script.BRIDGE_TOKEN = "local-test"
    context = SimpleNamespace(accountID="test-account", set_account=lambda _: None,
                              run_time=lambda *args: calls.append(args))
    script.init(context)
    script.handlebar(context)
    assert calls == [("on_timer", "100nMilliSecond", "20200101000000")]
    assert script._runtime.sock is None
    script.stop(context)
    script.init(context)  # Stop/start releases local resources.


@pytest.mark.parametrize('account,token,typ,fields', [
    ('', 'secret-not-for-logs', 'STOCK', ['ACCOUNT_ID:']),
    ('test-account', '', 'STOCK', ['BRIDGE_TOKEN:']),
    ('test-account', 'secret-not-for-logs', 'CREDIT', ['ACCOUNT_TYPE:']),
    ('', '', 'CREDIT', ['ACCOUNT_ID:', 'BRIDGE_TOKEN:', 'ACCOUNT_TYPE:']),
])
def test_init_reports_exact_missing_configuration_without_binding_or_secret(script, account, token, typ, fields):
    script.ACCOUNT_ID, script.BRIDGE_TOKEN, script.ACCOUNT_TYPE = account, token, typ
    context = SimpleNamespace(set_account=lambda _: pytest.fail('Invalid config must not bind account'))
    with pytest.raises(RuntimeError) as caught:
        script.init(context)
    assert all(field in str(caught.value) for field in fields)
    assert 'secret-not-for-logs' not in str(caught.value)
    assert script._runtime is None


def test_context_account_id_is_accepted_without_guessing_or_enabling_trade(script):
    script.BRIDGE_TOKEN = 'private-test-token'
    bound = []
    context = SimpleNamespace(account_id='test-selected-account', set_account=bound.append,
                              run_time=lambda *args: None)
    script.init(context)
    assert bound == ['test-selected-account']
    assert script.ENABLE_TRADING is False and script.ENABLE_CANCEL is False
    assert script._runtime.sock is None
    script.stop(context)


class SendingSocket:
    def __init__(self, result=None):
        self.result, self.frames, self.closed = result, [], False

    def send(self, data):
        if isinstance(self.result, Exception):
            raise self.result
        size = len(data) if self.result is None else self.result
        self.frames.append(data[:size])
        return size

    def close(self):
        self.closed = True


def test_callback_is_flushed_without_waiting_for_another_timer(script):
    import json
    bridge = script.BridgeRuntime(SimpleNamespace(), 'test-account')
    bridge.sock = SendingSocket()
    bridge.connected = bridge.welcomed = True
    bridge.event('order', {'security': '510300.XSHG', 'status': 'open'})
    assert len(bridge.sock.frames) == 1 and bridge.outgoing == b''
    assert json.loads(bridge.sock.frames[0])['event'] == 'order'


def test_partial_or_blocked_send_keeps_bytes_without_blocking_or_duplication(script):
    bridge = script.BridgeRuntime(SimpleNamespace(), 'test-account')
    sock = bridge.sock = SendingSocket(3)
    bridge.connected = True
    bridge.send({'type': 'response', 'ok': True})
    remaining = bridge.outgoing
    assert remaining and len(sock.frames) == 1
    sock.result = BlockingIOError()
    bridge.flush_outgoing()
    assert bridge.outgoing == remaining and not sock.closed
    sock.result = None
    bridge.flush_outgoing()
    assert bridge.outgoing == b''
    import json
    assert json.loads(b''.join(sock.frames)) == {'type': 'response', 'ok': True}


def test_send_failure_after_passorder_preserves_dedup_and_never_replays_write(script):
    calls = []
    script.ENABLE_TRADING = True
    script.passorder = lambda *args: calls.append(args)
    bridge = script.BridgeRuntime(SimpleNamespace(), 'test-account')
    sock = bridge.sock = SendingSocket(OSError('lost acknowledgement'))
    bridge.connected = True
    bridge.command(command())
    assert len(calls) == 1 and sock.closed and bridge.sock is None
    assert 'one' in bridge.seen and bridge.seen['one']['ok']
    bridge.command(command())
    assert len(calls) == 1


def test_client_socket_uses_nonblocking_tcp_nodelay(script, monkeypatch):
    options = []
    sock = SimpleNamespace(setblocking=lambda value: options.append(('blocking', value)),
        setsockopt=lambda *args: options.append(args), connect_ex=lambda addr: 10035,
        close=lambda: None)
    monkeypatch.setattr(script.socket, 'socket', lambda: sock)
    monkeypatch.setattr(script.select, 'select', lambda *args: ([], [], []))
    bridge = script.BridgeRuntime(SimpleNamespace(), 'test-account')
    bridge.pump()
    assert ('blocking', False) in options
    assert (script.socket.IPPROTO_TCP, script.socket.TCP_NODELAY, 1) in options
    bridge.close()


def test_timing_reports_actual_pump_gap_without_extra_qmt_calls(script, monkeypatch):
    bridge, messages = runtime(script)
    reads, clock = [], [1.]
    script.get_trade_detail_data = lambda *args: reads.append(args) or []
    bridge._pump = lambda: bridge.command(command('read', '/orders'))
    monkeypatch.setattr(script.time, 'monotonic', lambda: clock[0])
    bridge.pump()
    assert messages[-1]['timing']['qmt_pump_gap_ms'] is None
    clock[0] = 3.05
    bridge.pump()
    assert messages[-1]['timing']['qmt_pump_gap_ms'] == pytest.approx(2050.)
    assert len(reads) == 2


def test_portable_single_file_has_no_strategy_or_external_runtime():
    raw = SOURCE.read_bytes()
    assert raw.decode("ascii") == raw.decode("gbk") == raw.decode("utf-8")
    for forbidden in ("import xtquant", "import bullet_trade", "get_etf_info", "threading", "StrategyLedger"):
        assert forbidden not in raw.decode("ascii")
