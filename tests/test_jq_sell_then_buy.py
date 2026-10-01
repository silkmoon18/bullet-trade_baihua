"""Explicit staged execution, isolated from the default native JQ path."""

import importlib
import pickle
from datetime import timedelta
from types import SimpleNamespace as NS

import pytest

import helpers.bullet_trade_jq_remote_helper as module
from tests.test_jq_rebalance import JQ


@pytest.fixture
def setup():
    helper = importlib.reload(module)
    jq = JQ(cash=1000, holdings={"OLD": 400, "TRIM": 500})
    jq.context.current_dt = jq.context.current_dt.replace(hour=9, minute=26)
    runtime = jq.runtime(helper)
    namespace = helper._active_namespace
    namespace["LimitOrderStyle"] = lambda price: NS(limit_price=price)
    namespace["get_current_data"] = lambda: {
        code: NS(low_limit=9.0, last_price=10.0) for code in jq.prices
    }
    jq.sell_calls = []

    def sell(code, qty, style=None):
        assert qty == 0 and style.limit_price == 9.0
        jq.sell_calls.append((code, qty, style.limit_price))
        order = NS(order_id="sell-" + code, security=code,
                   amount=jq.positions[code].total_amount, filled=0)
        jq.open[order.order_id] = order
        return order

    namespace["order_target"] = sell
    return helper, jq, runtime


def prepare(jq, runtime, weights=None):
    weights = {"TRIM": 0.4, "BUY": 0.5} if weights is None else weights
    runtime.prepare_sell_then_buy(
        jq.context, weights, {code: 10.0 for code in weights}, "open-20260908"
    )


def clear(jq):
    for code, position in list(jq.positions.items()):
        jq.fill(code, -position.total_amount)
    jq.open.clear()


@pytest.mark.parametrize("mode", ["JQ", "BACKTEST"])
def test_sell_all_including_selected_then_buy_once_after_0930(setup, mode):
    helper, jq, runtime = setup
    runtime.mode = helper.RuntimeMode(mode)
    runtime.state["mode"] = mode
    prepare(jq, runtime)
    assert jq.sell_calls == [("OLD", 0, 9.0), ("TRIM", 0, 9.0)]
    assert jq.calls == []  # No 09:26 buys.
    prepare(jq, runtime)  # Duplicate decision never duplicates orders.
    assert len(jq.sell_calls) == 2
    clear(jq)
    runtime.advance_sell_then_buy(jq.context)
    assert jq.calls == []  # Flat early still waits for the clock.
    jq.context.current_dt = jq.context.current_dt.replace(minute=30)
    runtime.advance_sell_then_buy(jq.context)
    assert jq.calls == [("TRIM", 4000), ("BUY", 5000)]
    runtime.advance_sell_then_buy(jq.context)
    assert len(jq.calls) == 2


def test_partial_sell_and_active_orders_both_prevent_buys(setup):
    _, jq, runtime = setup
    prepare(jq, runtime)
    jq.fill("OLD", -200)
    jq.context.current_dt = jq.context.current_dt.replace(minute=30)
    runtime.advance_sell_then_buy(jq.context)
    assert jq.calls == []
    for code, pos in list(jq.positions.items()):
        jq.fill(code, -pos.total_amount)
    runtime.advance_sell_then_buy(jq.context)
    assert jq.calls == []  # Flat snapshot alone does not finish a working order.
    jq.open.clear()
    jq.context.current_dt += timedelta(minutes=1)
    runtime.advance_sell_then_buy(jq.context)
    assert len(jq.calls) == 2


def test_restarts_restore_plan_without_reselecting_or_repeating_buys(setup):
    helper, jq, runtime = setup
    prepare(jq, runtime)
    jq.g.bt_sell_then_buy = pickle.loads(pickle.dumps(jq.g.bt_sell_then_buy))
    restored = helper.JoinQuantRuntime(runtime.state, helper._active_namespace)
    restored.send_target_buy_plan = lambda *args, **kwargs: None
    clear(jq)
    jq.context.current_dt = jq.context.current_dt.replace(minute=30)
    restored.advance_sell_then_buy(jq.context)
    assert len(jq.calls) == 2
    restored = helper.JoinQuantRuntime(runtime.state, helper._active_namespace)
    restored.advance_sell_then_buy(jq.context)
    assert len(jq.calls) == 2


def test_no_discounts_only_liquidates_and_never_buys(setup):
    _, jq, runtime = setup
    prepare(jq, runtime, {})
    clear(jq)
    jq.context.current_dt = jq.context.current_dt.replace(minute=30)
    runtime.advance_sell_then_buy(jq.context)
    assert jq.g.bt_sell_then_buy["jq"] == "DONE"
    assert jq.calls == []


def test_yesterday_plan_does_not_buy_today(setup):
    _, jq, runtime = setup
    prepare(jq, runtime)
    clear(jq)
    jq.context.current_dt += timedelta(days=1, minutes=4)
    runtime.advance_sell_then_buy(jq.context)
    assert jq.calls == []


def test_buy_not_before_accepts_and_normalizes_non_padded_time(setup):
    _, jq, runtime = setup
    runtime.prepare_sell_then_buy(jq.context, {"BUY": 0.5}, {"BUY": 10.0},
                                  "open-20260908", buy_not_before="9:30")
    assert jq.g.bt_sell_then_buy["buy_not_before"] == "09:30"
    clear(jq)
    jq.context.current_dt = jq.context.current_dt.replace(minute=30)
    runtime.advance_sell_then_buy(jq.context)
    assert len(jq.calls) == 1


def test_native_buy_partial_fill_is_not_chased(setup):
    _, jq, runtime = setup
    prepare(jq, runtime)
    clear(jq)
    jq.partial_once["BUY"] = 100
    jq.context.current_dt = jq.context.current_dt.replace(minute=30)
    runtime.advance_sell_then_buy(jq.context)
    assert jq.positions["BUY"].total_amount == 100
    for _ in range(3):
        jq.context.current_dt += timedelta(minutes=1)
        runtime.advance_sell_then_buy(jq.context)
    assert len(jq.calls) == 2


def test_native_sell_unknown_does_not_blindly_resubmit_or_buy(setup):
    helper, jq, runtime = setup
    calls = []

    def uncertain(*args, **kwargs):
        calls.append(args)
        raise RuntimeError("submission unknown")

    helper._active_namespace["order_target"] = uncertain
    prepare(jq, runtime)
    clear(jq)
    jq.context.current_dt = jq.context.current_dt.replace(minute=30)
    runtime.advance_sell_then_buy(jq.context)
    assert len(calls) == 1
    assert jq.calls == []
    assert any("submission unknown" in message for message in jq.messages)


def with_qmt(setup, monkeypatch):
    helper, jq, runtime = setup
    runtime.qmt_account_enabled = True
    runtime.state["qmt_account_enabled"] = True
    runtime._qmt_callback_allowed = lambda *args: True
    runtime.cancel_targets = lambda: True
    broker = NS(portfolio=NS(total_value=20000.0, positions={
        "OLD": NS(total_amount=100, price=10.0),
    }), working=False, submissions=[])
    monkeypatch.setattr(helper, "get_portfolio", lambda **kwargs: broker.portfolio)
    runtime.advance_targets = lambda context: not broker.working

    def submit(context, weights, marks, key, execution, names=None):
        broker.submissions.append((dict(weights), dict(marks), key, execution))
        return {"intent": {"state": "EXECUTING"}}

    runtime.submit_targets = submit
    return helper, jq, runtime, broker


def test_qmt_liquidates_owned_positions_then_uses_original_marks_and_premium(setup, monkeypatch):
    helper, jq, runtime, broker = with_qmt(setup, monkeypatch)
    prepare(jq, runtime)
    weights, marks, key, execution = broker.submissions[0]
    assert weights == {"OLD": 0.0}
    assert marks == {"OLD": 10.0}  # Never use lower-limit 9 as a valuation mark.
    assert key == "open-20260908:sell"
    assert execution.style.limit_prices == {"OLD": 9_000_000}
    assert execution.style.preopen is True
    assert execution.sell_style is None  # Sell uses the explicit limit, not market.
    broker.portfolio.positions.clear()
    runtime.advance_sell_then_buy(jq.context)
    assert len(broker.submissions) == 1  # Early liquidation cannot buy early.
    jq.context.current_dt = jq.context.current_dt.replace(minute=30)
    runtime.advance_sell_then_buy(jq.context)
    assert len(broker.submissions) == 2
    weights, marks, key, execution = broker.submissions[1]
    assert weights == {"TRIM": 0.4, "BUY": 0.5}
    assert marks == {"TRIM": 10.0, "BUY": 10.0}
    assert key == "open-20260908:buy"
    assert execution == helper.default_etf_rebalance_execution()
    assert execution.style.price_band_ppm == 2000
    assert execution.style.limit_prices == {}
    assert execution.style.preopen is False
    assert jq.calls == []  # JQ still selling does not prevent QMT buying.
    runtime.advance_sell_then_buy(jq.context)
    assert len(broker.submissions) == 2


def test_jq_can_buy_while_qmt_still_selling(setup, monkeypatch):
    _, jq, runtime, broker = with_qmt(setup, monkeypatch)
    prepare(jq, runtime)
    clear(jq)
    broker.working = True
    jq.context.current_dt = jq.context.current_dt.replace(minute=30)
    runtime.advance_sell_then_buy(jq.context)
    assert len(jq.calls) == 2
    assert len(broker.submissions) == 1
    broker.working = False
    runtime.advance_sell_then_buy(jq.context)
    assert len(broker.submissions) == 1  # Terminal order but nonzero holdings.


def test_prior_qmt_intent_must_be_canceled_before_liquidation(setup, monkeypatch):
    _, jq, runtime, broker = with_qmt(setup, monkeypatch)
    runtime.cancel_targets = lambda: False
    prepare(jq, runtime)
    assert broker.submissions == []
    assert len(jq.sell_calls) == 2  # Accounts remain independent.
    runtime.cancel_targets = lambda: True
    runtime.advance_sell_then_buy(jq.context)
    assert len(broker.submissions) == 1


def test_historical_replay_plan_never_becomes_live_qmt_orders(setup, monkeypatch):
    _, jq, runtime, broker = with_qmt(setup, monkeypatch)
    runtime._qmt_callback_allowed = lambda *args: False
    prepare(jq, runtime)
    assert broker.submissions == []
    runtime._qmt_callback_allowed = lambda *args: True
    jq.context.current_dt = jq.context.current_dt.replace(hour=13, minute=1)
    runtime.advance_sell_then_buy(jq.context)
    assert broker.submissions == []


@pytest.mark.parametrize("buy", [False, True])
def test_qmt_unknown_rpc_retries_same_key_and_does_not_recancel(setup, monkeypatch, buy):
    _, jq, runtime, broker = with_qmt(setup, monkeypatch)
    if buy:
        prepare(jq, runtime)
        broker.portfolio.positions.clear()
        jq.context.current_dt = jq.context.current_dt.replace(minute=30)
    submit = runtime.submit_targets
    attempts = []

    def uncertain(*args):
        attempts.append(args[3])
        if len(attempts) == 1:
            raise RuntimeError("RPC timeout after acceptance")
        return submit(*args)

    runtime.submit_targets = uncertain
    if buy:
        runtime.advance_sell_then_buy(jq.context)
    else:
        prepare(jq, runtime)
    runtime.cancel_targets = lambda: pytest.fail("must not cancel this plan's own target")
    runtime.advance_sell_then_buy(jq.context)
    expected = "open-20260908:" + ("buy" if buy else "sell")
    assert attempts == [expected, expected]


def test_minute_confirmation_is_registered_only_when_opted_in(setup):
    helper, _, runtime = setup
    schedules = []
    runtime._namespace["run_daily"] = lambda fn, *args, **kwargs: schedules.append((fn, kwargs))
    callbacks = [lambda context: None for _ in range(5)]
    runtime.schedule_daily(*callbacks[:3], ("10:30",), callbacks[3],
                           opening_decision=callbacks[4], opening_decision_time="09:26",
                           sell_then_buy=True)
    confirmations = [row for row in schedules if row[1].get("time") == "every_bar"]
    assert confirmations == [(helper.advance_joinquant_sell_then_buy, {
        "time": "every_bar", "reference_security": "000300.XSHG",
    })]


def test_scheduled_confirmation_restores_current_runtime(setup):
    helper, jq, runtime = setup
    prepare(jq, runtime)
    clear(jq)
    jq.context.current_dt = jq.context.current_dt.replace(minute=30)
    helper._active_joinquant_runtime = runtime
    helper.advance_joinquant_sell_then_buy(jq.context)
    assert len(jq.calls) == 2
