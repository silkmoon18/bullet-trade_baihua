"""Critical-path ordering and reusable RPC contracts, without broker access."""

import importlib
import pickle
import socket
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from types import SimpleNamespace as NS

import pytest

import helpers.bullet_trade_jq_remote_helper as module
from tests.test_jq_rebalance import JQ
from tests.test_jq_strategy_runtime import _install, _profile_module, _state


@pytest.fixture
def helper():
    module._close_rpc_connection()
    runtime = importlib.reload(module)
    yield runtime
    runtime._close_rpc_connection()


def test_native_orders_precede_slow_reporting_and_plan_keeps_pretrade_holdings(helper, monkeypatch):
    jq = JQ(cash=5000, holdings={"TRIM": 500})
    runtime = jq.runtime(helper)
    events, plans = [], []
    elapsed = [0.0]
    monkeypatch.setattr(helper.time, "perf_counter", lambda: elapsed[0])
    original_order = runtime.order_target_value

    def order(*args, **kwargs):
        events.append("order")
        elapsed[0] += 0.006
        return original_order(*args, **kwargs)

    def notify(items, **kwargs):
        events.append("notify")
        plans.extend(items)
        elapsed[0] += 2.0

    def log(message):
        events.append("log")
        elapsed[0] += 0.05

    runtime.order_target_value = order
    runtime.send_target_buy_plan = notify
    runtime._namespace["log"] = NS(info=log, warn=log, error=log)
    result = runtime.execute_rebalance(
        jq.context, {"BUY": 0.4, "TRIM": 0.2}, jq.prices, "test"
    )
    assert result["errors"] == []
    assert jq.calls == [("BUY", 4000), ("TRIM", 2000)]
    assert events[:3] == ["order", "order", "notify"]
    assert all(event == "log" for event in events[3:])
    assert [(item["security"], item["quantity"]) for item in plans] == [("BUY", 400)]
    timing = jq.g.bt_last_rebalance_timing
    assert timing["first_jq_order_ms"] == 0
    assert timing["orders_ms"] == pytest.approx(12)
    assert timing["notification_ms"] == pytest.approx(2000)
    assert timing["logs_ms"] > 0
    assert timing["total_ms"] > timing["orders_ms"] + timing["notification_ms"]


def test_frozen_logs_survive_restart_and_are_emitted_once_after_submission(helper):
    jq = JQ()
    runtime = jq.runtime(helper)

    def decide(context):
        runtime.log_strategy_event("frozen selection")
        return {"BUY": 0.5}, {"BUY": 10.0}

    runtime.prepare_rebalance(jq.context, decide)
    assert jq.messages == []
    jq.g = pickle.loads(pickle.dumps(jq.g))
    assert "frozen selection" in jq.g.bt_open_decision["decision_logs"][0][1]
    restored = jq.runtime(importlib.reload(helper))
    jq.context.current_dt += timedelta(minutes=5)
    messages_after_calls = []
    restored._namespace["log"].info = lambda message: messages_after_calls.append(
        (message, tuple(jq.calls))
    )
    restored.execute_prepared_rebalance(jq.context)
    assert jq.calls == [("BUY", 5000)]
    matches = [calls for message, calls in messages_after_calls if "frozen selection" in message]
    assert matches == [(('BUY', 5000),)]
    assert jq.g.bt_last_rebalance_timing["decision_ms"] >= 0
    restored.execute_prepared_rebalance(jq.context)
    assert len(jq.calls) == 1


def test_reporting_failure_does_not_repeat_or_fail_native_orders(helper):
    jq = JQ()
    runtime = jq.runtime(helper)

    def broken_log(message):
        raise OSError("logger unavailable")

    runtime._namespace["log"] = NS(info=broken_log, warn=broken_log, error=broken_log)
    result = runtime.execute_rebalance(jq.context, {"BUY": 0.5}, jq.prices, "test")
    assert result["errors"] == []
    assert jq.calls == [("BUY", 5000)]


def test_parallel_accounts_submit_before_qmt_notification_snapshot_read(helper, monkeypatch):
    jq = JQ()
    runtime = jq.runtime(helper)
    runtime.qmt_account_enabled = True
    runtime._qmt_callback_allowed = lambda *args: True
    runtime.advance_targets = lambda context: True
    events = []
    original_order = runtime.order_target_value
    runtime.order_target_value = lambda *args: events.append("jq") or original_order(*args)
    runtime.submit_targets = lambda *args: events.append("qmt") or {}
    monkeypatch.setattr(helper, "get_portfolio", lambda **kwargs: (
        events.append("card_snapshot") or NS(total_value=20000, positions={})
    ))
    runtime.send_target_buy_plan = lambda *args, **kwargs: events.append("notify")
    result = runtime.execute_rebalance(jq.context, {"BUY": 0.5}, jq.prices, "test")
    assert result["errors"] == []
    assert events == ["qmt", "jq", "card_snapshot", "notify"]
    assert jq.calls == [("BUY", 5000)]
    assert jq.g.bt_last_rebalance_timing["qmt_accepted_ms"] is not None


def test_candidate_codes_and_names_are_same_day_plain_data_and_never_prices(helper, monkeypatch):
    g = NS()
    names = []
    namespace = {"g": g, "get_security_info": lambda code: (
        names.append(code) or NS(display_name="ETF " + code)
    )}
    helper._active_namespace = namespace
    runtime = helper.JoinQuantRuntime(_state("QMT_REMOTE", "sim_trade"), namespace)
    context = NS(current_dt=datetime(2026, 9, 8, 9, 20))
    runtime.prepare_rebalance_candidates(context, ["510050.XSHG", "510300.XSHG", "510050.XSHG"])
    saved = pickle.loads(pickle.dumps(g.bt_rebalance_candidates))
    assert set(saved) == {"date", "securities", "security_names"}
    assert saved["securities"] == ("510050.XSHG", "510300.XSHG")
    helper._security_name_cache.clear()
    g.bt_rebalance_candidates = saved
    assert runtime._prepared_candidates(context) == saved["securities"]
    assert helper._security_name("510050.XSHG") == "ETF 510050.XSHG"
    assert names == ["510050.XSHG", "510300.XSHG"]
    context.current_dt += timedelta(days=1)
    assert runtime._prepared_candidates(context) == ()


def test_prewarm_sends_only_same_day_candidates_and_retains_no_target(helper, monkeypatch):
    namespace = {"g": NS()}
    runtime = helper.JoinQuantRuntime(_state("QMT_REMOTE", "sim_trade"), namespace)
    context = NS(current_dt=datetime(2026, 9, 8, 9, 20))
    runtime.prepare_rebalance_candidates(context, ["510050.XSHG"])
    runtime._qmt_callback_allowed = lambda *args: True
    requests = []
    monkeypatch.setattr(helper, "_strategy_request", lambda action, payload: (
        requests.append((action, payload)) or {"reconciliation": {"state": "READY"}}
    ))
    monkeypatch.setattr(helper, "_restore_runtime_targets", lambda: None)
    runtime.prewarm_qmt(context)
    assert requests == [("strategy.prepare_session", {
        "initial_capital": None, "candidate_securities": ["510050.XSHG"]
    })]
    assert not hasattr(namespace["g"], "bt_open_decision")
    context.current_dt += timedelta(days=1)
    runtime.prewarm_qmt(context)
    assert requests[-1][1] == {"initial_capital": None}


class Socket:
    def __init__(self):
        self.closed = False
        self.options = []

    def settimeout(self, timeout):
        self.timeout = timeout

    def setsockopt(self, *args):
        self.options.append(args)

    def close(self):
        self.closed = True


def rpc_harness(helper, monkeypatch):
    _profile_module(monkeypatch)
    _install(helper, mode="QMT_REMOTE")
    connections, messages = [], []

    def connect(address, timeout):
        sock = Socket()
        connections.append(sock)
        return sock

    def send(sock, message):
        messages.append((sock, message))

    def read(sock):
        current = messages[-1][1]
        if current["type"] == "handshake":
            return {"type": "handshake_ack"}
        if current["type"] == "ping":
            return {"type": "pong", "id": current["id"]}
        return {"type": "response", "id": current["id"], "payload": {"ok": True}}

    monkeypatch.setattr(helper.socket, "create_connection", connect)
    monkeypatch.setattr(helper, "_send_message", send)
    monkeypatch.setattr(helper, "_read_message", read)
    return connections, messages, read


def test_successive_rpcs_use_one_authenticated_connection(helper, monkeypatch):
    connections, messages, _ = rpc_harness(helper, monkeypatch)
    for _ in range(3):
        assert helper._strategy_request("strategy.get_snapshot", {}) == {"ok": True}
    assert len(connections) == 1
    assert [message["type"] for _, message in messages] == ["handshake", "request", "request", "request"]
    assert not connections[0].closed
    assert (socket.IPPROTO_TCP, socket.TCP_NODELAY, 1) in connections[0].options
    assert (socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1) in connections[0].options
    assert helper._rpc_stats == {"connections": 1, "reused": 2}


@pytest.mark.parametrize("changed", ["host", "port", "token", "tls_cert", "account_key", "expired"])
def test_rpc_connection_is_closed_on_identity_change_or_idle_expiry(helper, monkeypatch, changed):
    connections, _, _ = rpc_harness(helper, monkeypatch)
    clock = [100.0]
    monkeypatch.setattr(helper.time, "monotonic", lambda: clock[0])
    helper._strategy_request("strategy.get_snapshot", {})
    if changed == "expired":
        clock[0] += 91
    elif changed == "tls_cert":
        helper._active_profile[changed] = "unit-certificate"
        monkeypatch.setattr(helper.ssl, "create_default_context", lambda **kwargs: NS(
            wrap_socket=lambda sock, **options: sock
        ))
    else:
        old = helper._active_profile.get(changed)
        helper._active_profile[changed] = old + 1 if isinstance(old, int) else "new-value"
    helper._strategy_request("strategy.get_snapshot", {})
    assert len(connections) == 2
    assert connections[0].closed
    assert not connections[1].closed


@pytest.mark.parametrize("action", ["strategy.submit_targets", "strategy.cancel_intent", "strategy.notify_target_buy_plan"])
def test_lost_write_response_on_reused_connection_is_not_automatically_resent(helper, monkeypatch, action):
    connections, messages, read = rpc_harness(helper, monkeypatch)
    helper._strategy_request("strategy.get_snapshot", {})

    def lost(sock):
        if messages[-1][1].get("action") == action:
            raise socket.timeout("response lost")
        return read(sock)

    monkeypatch.setattr(helper, "_read_message", lost)
    monkeypatch.setattr(helper.time, "sleep", lambda _: pytest.fail("unsafe retry"))
    with pytest.raises(helper._AmbiguousRequestError):
        helper._strategy_request(action, {"idempotency_key": "stable-key"})
    assert len(connections) == 1
    assert connections[0].closed
    assert helper._rpc_connection is None
    assert sum(message.get("action") == action for _, message in messages) == 1
    helper._strategy_request("strategy.get_intent", {})
    assert len(connections) == 2


def test_read_rpc_reconnects_after_lost_reused_connection(helper, monkeypatch):
    connections, messages, read = rpc_harness(helper, monkeypatch)
    helper._strategy_request("strategy.get_snapshot", {})
    failed = [False]

    def read_once(sock):
        if len(messages) == 3 and not failed[0]:
            failed[0] = True
            raise EOFError("idle server closed")
        return read(sock)

    monkeypatch.setattr(helper, "_read_message", read_once)
    monkeypatch.setattr(helper.time, "sleep", lambda _: None)
    assert helper._strategy_request("strategy.get_snapshot", {}) == {"ok": True}
    assert len(connections) == 2
    assert connections[0].closed


@pytest.mark.parametrize("dead_idle", [False, True])
def test_idle_write_probes_transport_before_sending_business_request(helper, monkeypatch, dead_idle):
    connections, messages, read = rpc_harness(helper, monkeypatch)
    clock = [100.0]
    monkeypatch.setattr(helper.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(helper.time, "sleep", lambda _: None)
    helper._strategy_request("strategy.get_snapshot", {})
    clock[0] += 21

    def idle_response(sock):
        if dead_idle and sock is connections[0] and messages[-1][1]["type"] == "ping":
            raise EOFError("NAT closed idle connection")
        return read(sock)

    monkeypatch.setattr(helper, "_read_message", idle_response)
    assert helper._strategy_request("strategy.submit_targets", {"idempotency_key": "stable"}) == {"ok": True}
    assert sum(msg.get("action") == "strategy.submit_targets" for _, msg in messages) == 1
    assert [msg["type"] for _, msg in messages[:3]] == ["handshake", "request", "ping"]
    assert len(connections) == (2 if dead_idle else 1)
    assert connections[0].closed is dead_idle


def test_rpc_connection_has_one_response_reader_under_concurrent_queries(helper, monkeypatch):
    connections, messages, read = rpc_harness(helper, monkeypatch)

    def slow_read(sock):
        time.sleep(0.002)
        return read(sock)

    monkeypatch.setattr(helper, "_read_message", slow_read)
    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(lambda _: helper._strategy_request("strategy.get_snapshot", {}), range(8)))
    assert results == [{"ok": True}] * 8
    assert len(connections) == 1
    assert len(messages) == 9
    assert len({message["id"] for _, message in messages[1:]}) == 8


def test_0929_transport_prewarm_uses_ping_only_and_reuses_connection_for_target(helper, monkeypatch):
    connections, messages, _ = rpc_harness(helper, monkeypatch)
    runtime = helper.JoinQuantRuntime(_state("QMT_REMOTE", "sim_trade"), {"g": NS()})
    runtime._qmt_callback_allowed = lambda *args: True
    helper._active_joinquant_runtime = runtime
    callback = pickle.loads(pickle.dumps(helper._prewarm_joinquant_rpc))
    callback(NS(current_dt=datetime(2026, 9, 8, 9, 29)))
    assert [message["type"] for _, message in messages] == ["handshake", "ping"]
    helper._strategy_request("strategy.submit_targets", {"idempotency_key": "test"})
    assert len(connections) == 1
    assert [message["type"] for _, message in messages] == ["handshake", "ping", "request"]


def test_transport_prewarm_rejects_historical_callback_and_jq_only_mode(helper, monkeypatch):
    monkeypatch.setattr(helper, "_get_rpc_connection", lambda _: pytest.fail("historical connection"))
    monkeypatch.setattr(helper, "_local_wall_clock", lambda: time.struct_time((2026, 9, 8, 9, 29, 0, 1, 251, -1)))
    for mode in ("JQ", "BACKTEST", "QMT_REMOTE", "JQ_QMT_PARALLEL"):
        runtime = helper.JoinQuantRuntime(_state(mode, "sim_trade"), {})
        runtime._prewarm_rpc(NS(current_dt=datetime(2026, 9, 7, 9, 29)))


@pytest.mark.parametrize("decision,order,expected", [
    ("09:30", "09:30", ("09:29",)),
    ("09:30", "09:35", ("09:29", "09:34")),
])
def test_transport_warmup_follows_split_decision_and_order_times(helper, decision, order, expected):
    calls = []
    runtime = helper.JoinQuantRuntime(_state("QMT_REMOTE", "sim_trade"), {
        "run_daily": lambda callback, *args, **kwargs: calls.append((callback, args)),
    })
    callback = lambda context: None
    runtime.schedule_daily(callback, callback, callback, (), callback,
                           open_decision_time=decision, open_order_time=order)
    assert tuple(args[0] for fn, args in calls if fn is helper._prewarm_joinquant_rpc) == expected


def test_transport_warmup_covers_both_explicit_stages_without_enabling_stages_for_default(helper):
    calls = []
    runtime = helper.JoinQuantRuntime(_state("QMT_REMOTE", "sim_trade"), {
        "run_daily": lambda callback, *args, **kwargs: calls.append((callback, args)),
    })
    callback = lambda context: None
    runtime.schedule_daily(callback, callback, callback, (), callback,
                           opening_decision=callback, opening_decision_time="09:26", sell_then_buy=True)
    assert tuple(args[0] for fn, args in calls if fn is helper._prewarm_joinquant_rpc) == ("09:25", "09:29")
