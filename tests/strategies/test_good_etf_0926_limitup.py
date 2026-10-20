"""Independent JQ 09:26 frozen selection and native opening execution."""

import ast
import builtins
import importlib
import pickle
from pathlib import Path
from types import SimpleNamespace as NS

import pandas as pd
import pytest

from helpers import bullet_trade_jq_remote_helper as helper_module
from tests.strategies.test_good_etf_contract import _Runtime, _load_strategy
from tests.test_jq_rebalance import JQ


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "strategies/joinquant/good_etf_0926_limitup.py"


class FrozenRuntime(_Runtime):
    def prepare_rebalance(self, context, decide):
        self.prepared_rebalances.append((context, decide(context)))

    def execute_prepared_rebalance(self, context, **options):
        self.staged_advances.append((context, options))


def load_strategy(monkeypatch):
    strategy = _load_strategy(monkeypatch, strategy_path=SOURCE)
    strategy._runtime = FrozenRuntime(helper_module.RuntimeMode.BACKTEST)
    return strategy


def context():
    return NS(current_dt=pd.Timestamp("2026-10-14 09:26:00"))


def test_only_declared_price_time_and_order_decisions_change(monkeypatch):
    original = ast.parse((SOURCE.parent / "good_etf.py").read_text(encoding="utf-8"))
    variant = ast.parse(SOURCE.read_text(encoding="utf-8"), feature_version=(3, 8))
    functions = lambda tree: {
        node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)
    }
    old, new = functions(original), functions(variant)
    for name in ("_load_tracked_index_names", "before_market_open",
                 "initialize", "process_initialize"):
        assert ast.dump(old[name]) == ast.dump(new[name])
    for name in ("handle_risk_management", "after_market_check"):
        # Their only changes are explanatory docstrings.
        assert [ast.dump(node) for node in old[name].body[1:]] == [
            ast.dump(node) for node in new[name].body[1:]
        ]
    strategy = load_strategy(monkeypatch)
    original_strategy = _load_strategy(monkeypatch)
    for name in ("MIN_MONEY", "MAX_MONEY", "DEPLOY_RATIO",
                 "STOP_LOSS_RATIO", "TAKE_PROFIT_RATIO", "SKIP_SUSPENDED_LIMITUP",
                 "RISK_CHECK_TIMES"):
        assert getattr(strategy, name) == getattr(original_strategy, name)
    assert strategy.MAX_HOLD_NUM == 3  # Independent backtest selection count is unchanged.
    assert not hasattr(strategy, "STRATEGY_ID")
    assert strategy.VALIDATE_REMOTE_DURING_BACKTEST is False
    direct_calls = {
        node.func.id for node in ast.walk(variant)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert not direct_calls.intersection({
        "run_daily", "set_order_cost", "set_slippage", "order_target_value",
        "order_target", "LimitOrderStyle",
    })
    assert "last_price" not in SOURCE.read_text(encoding="utf-8")


@pytest.mark.parametrize("run_type", ["simple_backtest", "full_backtest", "sim_trade"])
def test_anonymous_install_never_reads_profile_or_contacts_server(monkeypatch, run_type):
    helper = importlib.reload(helper_module)
    strategy = load_strategy(monkeypatch)
    original_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        if name == "jq_runtime_config":
            pytest.fail("Anonymous JQ experiment must not read remote config")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    monkeypatch.setattr(helper, "_strategy_request",
                        lambda *args, **kwargs: pytest.fail("No remote requests"))
    strategy._install_runtime(NS(run_params=NS(type=run_type)))
    assert strategy._runtime.jq_account_enabled is True
    assert strategy._runtime.qmt_account_enabled is False
    assert strategy._runtime.state["strategy_id"] is None


def test_schedule_freezes_at_0926_sells_at_0930_and_confirms_on_each_bar(monkeypatch):
    helper = importlib.reload(helper_module)
    strategy = load_strategy(monkeypatch)
    jq = JQ()
    runtime = jq.runtime(helper)
    scheduled = []
    runtime._namespace["run_daily"] = lambda callback, time, **kwargs: scheduled.append(
        (callback, time)
    )
    runtime._namespace["unschedule_all"] = lambda: None
    strategy._runtime = runtime
    strategy._declare_schedule()
    assert scheduled == [
        (helper._scheduled_prepare, "09:20"),
        (helper._scheduled_decision, "09:26"),
        (helper._scheduled_orders, "09:30"),
        (helper._scheduled_minute_confirm, "every_bar"),
        (helper._scheduled_risk, "10:30"),
        (helper._scheduled_risk, "13:30"),
        (helper._scheduled_risk, "14:50"),
        (helper._scheduled_snapshot, "14:55"),
    ]
    for slot, callback in (('prepare', strategy.before_market_open), ('decision', strategy.market_open),
                           ('orders', strategy.execute_opening_plan), ('risk', strategy.handle_risk_management)):
        assert runtime._scheduled_entries[slot]['callback'] is callback
    assert not hasattr(runtime, "on_bar")


def test_open_prices_drive_rank_weights_and_marks_not_latest_quotes(monkeypatch):
    strategy = load_strategy(monkeypatch)
    codes = ["51000{}.XSHG".format(i) for i in range(1, 8)]
    strategy.g.fund_list = pd.DataFrame({"unit_net_value": [2.0] * 7}, index=codes)
    # Last prices deliberately reverse the opening-price order.
    quotes = {
        code: NS(day_open=price, last_price=last, high_limit=limit, paused=paused)
        for code, (price, last, limit, paused) in zip(codes, [
            (1.0, 1.9, 2.2, False), (1.5, 1.1, 2.4, False),
            (1.8, 1.0, 2.6, False), (1.9, 0.9, 2.8, False),
            (2.1, 0.8, 3.0, False), (0.5, 0.5, 3.0, True),
            (0.6, 0.6, 0.6, False),
        ])
    }
    monkeypatch.setattr(strategy, "get_current_data", lambda: quotes, raising=False)
    weights, marks = strategy.select_opening_etfs(context())
    assert list(weights) == codes[:3]
    assert weights == pytest.approx({
        codes[0]: 50.0 / 85.0 * .95,
        codes[1]: 25.0 / 85.0 * .95,
        codes[2]: 10.0 / 85.0 * .95,
    })
    assert marks == {codes[0]: 1.0, codes[1]: 1.5, codes[2]: 1.8}


@pytest.mark.parametrize("field", ["day_open", "high_limit"])
@pytest.mark.parametrize("value", [None, 0, -1, float("nan"), float("inf")])
def test_invalid_open_or_limit_is_skipped_without_price_fallback(monkeypatch, field, value):
    strategy = load_strategy(monkeypatch)
    code = "510001.XSHG"
    strategy.g.fund_list = pd.DataFrame({"unit_net_value": [2.0]}, index=[code])
    quote = NS(day_open=1.0, high_limit=2.2, last_price=1.0, paused=False)
    setattr(quote, field, value)
    monkeypatch.setattr(strategy, "get_current_data", lambda: {code: quote}, raising=False)
    strategy.market_open(context())
    assert strategy._runtime.rebalances == []
    assert len(strategy._runtime.prepared_rebalances) == 1
    assert strategy._runtime.prepared_rebalances[0][1] is None


def test_empty_discount_result_is_a_sell_all_decision_not_a_data_failure(monkeypatch):
    strategy = load_strategy(monkeypatch)
    code = "510001.XSHG"
    strategy.g.fund_list = pd.DataFrame({"unit_net_value": [2.0]}, index=[code])
    monkeypatch.setattr(strategy, "get_current_data", lambda: {
        code: NS(day_open=2.1, high_limit=2.2, paused=False)
    }, raising=False)
    strategy.market_open(context())
    assert len(strategy._runtime.prepared_rebalances) == 1
    assert strategy._runtime.prepared_rebalances[0][1] == ({}, {})


def test_strategy_only_declares_frozen_decision_and_native_execution(monkeypatch):
    strategy = load_strategy(monkeypatch)
    strategy.g.fund_list = pd.DataFrame({"unit_net_value": [2.0, 2.0]},
                                        index=["BUY", "TRIM"])
    monkeypatch.setattr(strategy, "get_current_data", lambda: {
        "BUY": NS(day_open=1.0, high_limit=2.2, paused=False),
        "TRIM": NS(day_open=1.5, high_limit=2.4, paused=False),
    }, raising=False)
    strategy.market_open(context())
    prepared = strategy._runtime.prepared_rebalances
    assert len(prepared) == 1
    weights, marks = prepared[0][1]
    assert weights == pytest.approx({"BUY": .95 * 2 / 3, "TRIM": .95 / 3})
    assert marks == {"BUY": 1.0, "TRIM": 1.5}
    assert strategy._runtime.rebalances == []
    strategy.execute_opening_plan(context())
    assert len(strategy._runtime.staged_advances) == 1
    assert strategy._runtime.staged_advances[0][1] == {
        "jq_wait_for_sells": True,
        "backtest_buy_price_increase": .005,
    }


def native_runtime(jq, helper):
    runtime = jq.runtime(helper)
    sells = []
    runtime._namespace["LimitOrderStyle"] = lambda price: NS(limit_price=price)
    runtime._namespace["get_current_data"] = lambda: {
        code: NS(low_limit=9.001) for code in jq.prices
    }
    if not hasattr(jq, "orders"):
        jq.orders = {}
    runtime._namespace["get_orders"] = lambda: jq.orders

    def sell(code, quantity, style=None):
        sells.append((code, quantity, getattr(style, "limit_price", None)))
        order = jq.order(code, quantity * jq.prices[code])
        jq.orders[order.order_id] = order
        return order

    runtime._namespace["order_target"] = sell
    return runtime, sells


def test_real_helper_freezes_without_orders_then_sells_excess_before_market_buys():
    helper = importlib.reload(helper_module)
    jq = JQ(cash=1000, holdings={"OLD": 400, "TRIM": 500})
    jq.context.current_dt = jq.context.current_dt.replace(minute=26)
    runtime, sells = native_runtime(jq, helper)
    decisions = []

    def decide(context):
        decisions.append(context.current_dt)
        return {"BUY": .5, "TRIM": .2}, {"BUY": 10, "TRIM": 10}

    runtime.prepare_rebalance(jq.context, decide)
    assert not jq.calls and not sells
    jq.g = pickle.loads(pickle.dumps(jq.g))
    runtime, sells = native_runtime(jq, helper)  # Restore serialized decision only.
    jq.context.current_dt = jq.context.current_dt.replace(minute=30)
    runtime.execute_prepared_rebalance(jq.context, jq_wait_for_sells=True)
    assert sells == [("OLD", 0, 9.001), ("TRIM", 200, 9.001)]
    assert jq.calls == [("OLD", 0), ("TRIM", 2000), ("BUY", 5000)]
    assert jq.positions["TRIM"].total_amount == 200
    assert jq.positions["BUY"].total_amount == 500
    assert len(decisions) == 1
    assert jq.g.bt_sell_then_buy["jq"] == "BUY_SENT"
    runtime.advance_sell_then_buy(jq.context)
    assert len(jq.calls) == 3  # Once only, no staged continuation or re-selection.


def test_working_sell_blocks_buys_until_own_fill_and_position_update():
    helper = importlib.reload(helper_module)
    jq = JQ(cash=1000, holdings={"OLD": 900})
    jq.pending.add("OLD")
    runtime, sells = native_runtime(jq, helper)
    jq.context.current_dt = jq.context.current_dt.replace(minute=26)
    runtime.prepare_rebalance(jq.context, lambda context: ({"BUY": .8}, {"BUY": 10}))
    jq.context.current_dt = jq.context.current_dt.replace(minute=30)
    runtime.execute_prepared_rebalance(jq.context, jq_wait_for_sells=True)
    assert sells == [("OLD", 0, 9.001)]
    assert jq.calls == [("OLD", 0)]
    assert "BUY" not in jq.positions
    assert len(jq.open) == 1
    jq.fill("OLD", -900)
    jq.open.clear()
    jq.context.current_dt = jq.context.current_dt.replace(minute=31)
    runtime.advance_sell_then_buy(jq.context)
    assert len(jq.calls) == 1  # A manual position change is not this sell's fill.
    jq.orders["1"].filled = 900
    runtime.advance_sell_then_buy(jq.context)
    assert jq.calls == [("OLD", 0), ("BUY", 8000)]
    assert jq.positions["BUY"].total_amount == 800
    runtime.advance_sell_then_buy(jq.context)
    assert len(jq.calls) == 2


def stage(jq, runtime, weights):
    jq.context.current_dt = jq.context.current_dt.replace(minute=26)
    runtime.prepare_rebalance(jq.context, lambda context: (weights, jq.prices))
    jq.context.current_dt = jq.context.current_dt.replace(minute=30)
    runtime.execute_prepared_rebalance(jq.context, jq_wait_for_sells=True)


def test_holdings_at_or_below_target_are_not_liquidated():
    helper = importlib.reload(helper_module)
    jq = JQ(cash=1000, holdings={"OLD": 400, "TRIM": 500})
    runtime, sells = native_runtime(jq, helper)
    stage(jq, runtime, {"TRIM": .5, "BUY": .4})
    assert sells == [("OLD", 0, 9.001)]
    assert jq.positions["TRIM"].total_amount == 500
    assert jq.positions["BUY"].total_amount == 400


def test_reduction_quantity_does_not_use_lower_limit_as_valuation_price():
    helper = importlib.reload(helper_module)
    jq = JQ(cash=5000, holdings={"TRIM": 500})
    runtime, sells = native_runtime(jq, helper)
    stage(jq, runtime, {"TRIM": .25})
    # Native adjustment truncates 2.5 lots toward zero: sell 200, keep 300.
    assert sells == [("TRIM", 300, 9.001)]
    assert jq.calls == [("TRIM", 3000)]


@pytest.mark.parametrize("low", [0, -1, float("nan"), float("inf")])
def test_invalid_lower_limit_does_not_fall_back_to_market_sells(low):
    helper = importlib.reload(helper_module)
    jq = JQ(cash=1000, holdings={"OLD": 900})
    runtime, sells = native_runtime(jq, helper)
    runtime._namespace["get_current_data"] = lambda: {"OLD": NS(low_limit=low)}
    stage(jq, runtime, {"BUY": .8})
    assert not jq.calls and not sells


def test_zero_quantity_rows_are_ignored_and_zero_weight_holding_is_cleared():
    helper = importlib.reload(helper_module)
    jq = JQ(cash=1000, holdings={"OLD": 0, "TRIM": 500})
    runtime, sells = native_runtime(jq, helper)
    stage(jq, runtime, {"TRIM": 0, "BUY": .5})
    assert sells == [("TRIM", 0, 9.001)]
    assert jq.calls == [("TRIM", 0), ("BUY", 3000)]


def test_native_buy_none_is_not_reported_as_accepted_or_retried():
    helper = importlib.reload(helper_module)
    jq = JQ()
    runtime, _ = native_runtime(jq, helper)
    runtime._namespace["order_target_value"] = lambda *args, **kwargs: None
    stage(jq, runtime, {"BUY": .5})
    assert any("JQ买入接口未返回委托" in message for message in jq.messages)
    assert not any("JQ目标已提交" in message for message in jq.messages)
    assert jq.g.bt_sell_then_buy["jq"] == "BUY_SENT"


def fill_pending_sell(jq, code, quantity=None, remove_open=True):
    order = next(order for order in jq.orders.values() if order.security == code)
    quantity = order.amount - order.filled if quantity is None else quantity
    jq.fill(code, -quantity)
    order.filled += quantity
    if remove_open and order.filled == order.amount:
        jq.open.pop(order.order_id, None)


def test_all_required_sells_must_fill_including_selected_trim_and_restart():
    helper = importlib.reload(helper_module)
    jq = JQ(cash=1000, holdings={"OLD": 400, "TRIM": 500})
    jq.pending.update({"OLD", "TRIM"})
    runtime, sells = native_runtime(jq, helper)
    stage(jq, runtime, {"BUY": .5, "TRIM": .2})
    assert sells == [("OLD", 0, 9.001), ("TRIM", 200, 9.001)]
    assert len(jq.calls) == 2 and "BUY" not in jq.positions
    fill_pending_sell(jq, "OLD")
    fill_pending_sell(jq, "TRIM", 100)
    jq.context.current_dt = jq.context.current_dt.replace(minute=31)
    runtime.advance_sell_then_buy(jq.context)
    assert len(jq.calls) == 2  # One full sell and one partial sell are insufficient.
    jq.g = pickle.loads(pickle.dumps(jq.g))
    restored, restarted_sells = native_runtime(jq, importlib.reload(helper_module))
    restored.advance_sell_then_buy(jq.context)
    assert not restarted_sells
    fill_pending_sell(jq, "TRIM", remove_open=False)
    restored.advance_sell_then_buy(jq.context)
    assert len(jq.calls) == 2  # Full fills still await the working-order view.
    jq.open.clear()
    restored.advance_sell_then_buy(jq.context)
    assert jq.calls[-1] == ("BUY", 5000)
    assert jq.positions["TRIM"].total_amount == 200
    assert jq.positions["BUY"].total_amount == 500
    restored.advance_sell_then_buy(jq.context)
    assert len(jq.calls) == 3


@pytest.mark.parametrize("status", ["canceled", "rejected", "partly_canceled", "filled", "held"])
def test_partial_sell_terminal_state_stops_this_round_without_buying_or_resending(status):
    helper = importlib.reload(helper_module)
    jq = JQ(cash=1000, holdings={"OLD": 900})
    jq.pending.add("OLD")
    runtime, sells = native_runtime(jq, helper)
    stage(jq, runtime, {"BUY": .8})
    fill_pending_sell(jq, "OLD", 100)
    jq.orders["1"].status = NS(name=status)
    jq.open.clear()
    runtime.advance_sell_then_buy(jq.context)
    assert jq.g.bt_sell_then_buy["jq"] == "FAILED"
    assert jq.calls == [("OLD", 0)]
    runtime.advance_sell_then_buy(jq.context)
    assert len(sells) == 1
    assert any("本轮不买入" in message for message in jq.messages)


def test_missing_sell_receipt_does_not_infer_fill_from_zero_position():
    helper = importlib.reload(helper_module)
    jq = JQ(cash=1000, holdings={"OLD": 900})
    jq.pending.add("OLD")
    runtime, _ = native_runtime(jq, helper)
    stage(jq, runtime, {"BUY": .8})
    fill_pending_sell(jq, "OLD")
    receipts = jq.orders
    jq.orders = {}
    runtime.advance_sell_then_buy(jq.context)
    assert jq.calls == [("OLD", 0)]
    jq.orders = receipts
    runtime.advance_sell_then_buy(jq.context)
    assert jq.calls[-1] == ("BUY", 8000)


@pytest.mark.parametrize("unknown", [False, True])
def test_unaccepted_or_uncertain_sell_never_causes_buys_or_blind_resubmission(unknown):
    helper = importlib.reload(helper_module)
    jq = JQ(cash=1000, holdings={"OLD": 900})
    runtime, _ = native_runtime(jq, helper)
    calls = []

    def fail(*args, **kwargs):
        calls.append(args)
        if unknown:
            raise RuntimeError("submission unknown")
        return None

    runtime._namespace["order_target"] = fail
    stage(jq, runtime, {"BUY": .8})
    runtime.advance_sell_then_buy(jq.context)
    assert len(calls) == 1
    assert "BUY" not in jq.positions
    assert jq.g.bt_sell_then_buy["jq"] == "FAILED"


def test_scheduled_confirmation_waits_then_submits_one_native_partial_buy(monkeypatch):
    helper = importlib.reload(helper_module)
    jq = JQ(cash=1000, holdings={"OLD": 900})
    jq.pending.add("OLD")
    jq.partial_once["BUY"] = 100
    runtime, _ = native_runtime(jq, helper)
    monkeypatch.setattr(helper, "_active_joinquant_runtime", runtime)
    stage(jq, runtime, {"BUY": .8})
    helper.advance_joinquant_sell_then_buy(jq.context)
    assert jq.calls == [("OLD", 0)]
    fill_pending_sell(jq, "OLD")
    jq.context.current_dt = jq.context.current_dt.replace(minute=31)
    helper.advance_joinquant_sell_then_buy(jq.context)
    assert jq.positions["BUY"].total_amount == 100
    helper.advance_joinquant_sell_then_buy(jq.context)
    assert jq.calls == [("OLD", 0), ("BUY", 8000)]  # Never supplement native partial buys.


def test_prior_day_waiting_sells_are_not_bought_today():
    helper = importlib.reload(helper_module)
    jq = JQ(cash=1000, holdings={"OLD": 900})
    jq.pending.add("OLD")
    runtime, _ = native_runtime(jq, helper)
    stage(jq, runtime, {"BUY": .8})
    fill_pending_sell(jq, "OLD")
    jq.context.current_dt += pd.Timedelta(days=1)
    runtime.advance_sell_then_buy(jq.context)
    assert jq.calls == [("OLD", 0)]


def test_no_required_sells_buy_immediately_without_order_queries():
    helper = importlib.reload(helper_module)
    jq = JQ()
    runtime, sells = native_runtime(jq, helper)
    runtime._namespace["get_orders"] = lambda: pytest.fail("No sells to confirm")
    stage(jq, runtime, {"BUY": .5})
    assert not sells
    assert jq.calls == [("BUY", 5000)]


def test_risk_exit_while_waiting_cannot_be_bought_back_by_frozen_plan():
    helper = importlib.reload(helper_module)
    jq = JQ(cash=1000, holdings={"OLD": 800, "BUY": 100})
    jq.pending.add("OLD")
    runtime, _ = native_runtime(jq, helper)
    stage(jq, runtime, {"BUY": .8})
    jq.prices["BUY"] = 9
    jq.refresh()
    result = runtime.execute_risk_management(jq.context, .95, 1.1, "risk")
    assert result["errors"] == []
    assert jq.g.bt_sell_then_buy["jq_risk_exits"] == ["BUY"]
    fill_pending_sell(jq, "OLD")
    runtime.advance_sell_then_buy(jq.context)
    assert "BUY" not in jq.positions
    assert jq.calls == [("OLD", 0), ("BUY", 0)]


def test_empty_selection_only_sells_and_finishes_after_all_fills():
    helper = importlib.reload(helper_module)
    jq = JQ(cash=1000, holdings={"OLD": 900})
    jq.pending.add("OLD")
    runtime, _ = native_runtime(jq, helper)
    stage(jq, runtime, {})
    assert jq.g.bt_sell_then_buy["jq"] == "SELLING"
    fill_pending_sell(jq, "OLD")
    runtime.advance_sell_then_buy(jq.context)
    assert jq.g.bt_sell_then_buy["jq"] == "DONE"
    assert jq.calls == [("OLD", 0)]


def backtest_runtime(jq, helper):
    runtime, sells = native_runtime(jq, helper)
    runtime.mode = helper.RuntimeMode.BACKTEST
    runtime.state["mode"] = "BACKTEST"
    settings = NS(spread=.002, changes=[], orders=[], sell_spreads=[])

    def set_slippage(model):
        settings.spread = model.value
        settings.changes.append(model.value)

    runtime._namespace["set_slippage"] = set_slippage
    runtime._namespace["FixedSlippage"] = lambda spread: NS(value=spread)
    runtime._namespace["get_current_data"] = lambda: {
        code: NS(day_open=price, last_price=price, low_limit=9.001)
        for code, price in jq.prices.items()
    }
    original_buy = runtime._namespace["order_target_value"]

    def buy(code, target_value, style=None):
        assert style is None  # A higher limit is not a worse execution price.
        price = jq.prices[code] + settings.spread / 2
        settings.orders.append((code, target_value, price))
        # Small synchronous market-model oracle, not a real JQ backtest.
        quote = jq.prices[code]
        try:
            jq.prices[code] = price
            order = original_buy(code, target_value)
            order.price = price
            return order
        finally:
            jq.prices[code] = quote
            jq.refresh()

    runtime._namespace["order_target_value"] = buy
    original_sell = runtime._namespace["order_target"]

    def sell(*args, **kwargs):
        settings.sell_spreads.append(settings.spread)
        return original_sell(*args, **kwargs)

    runtime._namespace["order_target"] = sell
    return runtime, settings, sells


def stage_with_increase(jq, runtime, weights, increase=.005):
    jq.context.current_dt = jq.context.current_dt.replace(minute=26)
    runtime.prepare_rebalance(jq.context, lambda context: (weights, jq.prices))
    jq.context.current_dt = jq.context.current_dt.replace(minute=30)
    runtime.execute_prepared_rebalance(
        jq.context, jq_wait_for_sells=True, backtest_buy_price_increase=increase,
    )


def test_extra_buy_slippage_is_multiplicative_per_security_and_sells_stay_original():
    helper = importlib.reload(helper_module)
    jq = JQ(cash=5000, holdings={"OLD": 500})
    jq.prices.update(BUY=1.0, SECOND=2.5)
    runtime, settings, sells = backtest_runtime(jq, helper)
    stage_with_increase(jq, runtime, {"BUY": .5, "SECOND": .4})
    assert sells == [("OLD", 0, 9.001)]
    assert settings.sell_spreads == [.002]
    assert [(code, target) for code, target, price in settings.orders] == [
        ("BUY", 5000), ("SECOND", 4000),
    ]
    assert [price for code, target, price in settings.orders] == pytest.approx([
        1.001 * 1.005, 2.501 * 1.005,
    ])
    assert settings.changes == pytest.approx([
        .002 + 2 * 1.001 * .005, .002, .002 + 2 * 2.501 * .005, .002,
    ])
    assert jq.positions["BUY"].avg_cost == pytest.approx(1.001 * 1.005)
    assert settings.spread == .002
    assert any("回测买入涨价" in message and "0.500%" in message for message in jq.messages)
    runtime.order_target("BUY", 0)
    assert settings.sell_spreads[-1] == .002  # Later risk sells see the baseline.
    runtime.advance_sell_then_buy(jq.context)
    assert len(settings.orders) == 2  # No buy supplementation.


@pytest.mark.parametrize("mode,increase", [("BACKTEST", 0), ("JQ", .005)])
def test_zero_or_sim_trade_keeps_native_behavior_without_extra_quote_or_settings(mode, increase):
    helper = importlib.reload(helper_module)
    jq = JQ()
    runtime, _ = native_runtime(jq, helper)
    runtime.mode = helper.RuntimeMode(mode)
    runtime._namespace["set_slippage"] = lambda *args: pytest.fail("No setting changes")
    runtime._namespace["get_current_data"] = lambda: pytest.fail("No extra quotes")
    stage_with_increase(jq, runtime, {"BUY": .5}, increase)
    assert jq.calls == [("BUY", 5000)]
    assert jq.positions["BUY"].total_amount == 500


def test_late_sell_completion_uses_buy_time_price_and_recovers_saved_increase():
    helper = importlib.reload(helper_module)
    jq = JQ(cash=1000, holdings={"OLD": 900})
    jq.pending.add("OLD")
    runtime, settings, _ = backtest_runtime(jq, helper)
    stage_with_increase(jq, runtime, {"BUY": .8})
    assert settings.changes == [] and settings.orders == []
    assert jq.g.bt_sell_then_buy["backtest_buy_price_increase"] == .005
    jq.g = pickle.loads(pickle.dumps(jq.g))
    fill_pending_sell(jq, "OLD")
    jq.context.current_dt = jq.context.current_dt.replace(minute=32)
    jq.prices["BUY"] = 11.0
    runtime, settings, _ = backtest_runtime(jq, importlib.reload(helper_module))
    runtime._namespace["get_current_data"] = lambda: {
        "BUY": NS(day_open=10.0, last_price=11.0),
    }
    runtime.advance_sell_then_buy(jq.context)
    assert settings.orders[0][1] == 8000  # Capital/weights frozen at 09:26.
    assert settings.orders[0][2] == pytest.approx(11.001 * 1.005)
    assert settings.spread == .002
    runtime.advance_sell_then_buy(jq.context)
    assert len(settings.orders) == 1


def test_0930_buy_uses_open_not_last_or_frozen_marks():
    helper = importlib.reload(helper_module)
    jq = JQ()
    runtime, settings, _ = backtest_runtime(jq, helper)
    runtime._namespace["get_current_data"] = lambda: {
        "BUY": NS(day_open=10.0, last_price=30.0),
    }
    runtime.prepare_sell_then_buy(
        jq.context, {"BUY": .5}, {"BUY": 7.0}, "open-test",
        liquidate_all=False, backtest_buy_price_increase=.005,
    )
    assert settings.orders[0][2] == pytest.approx(10.001 * 1.005)


@pytest.mark.parametrize("raises", [False, True])
def test_slippage_restores_after_rejected_or_failed_order_without_retry(raises):
    helper = importlib.reload(helper_module)
    jq = JQ()
    runtime, settings, _ = backtest_runtime(jq, helper)
    calls = []

    def fail(*args, **kwargs):
        calls.append(args)
        if raises:
            raise RuntimeError("native rejected")
        return None

    runtime._namespace["order_target_value"] = fail
    stage_with_increase(jq, runtime, {"BUY": .5})
    assert settings.spread == .002
    assert len(settings.changes) == 2
    runtime.advance_sell_then_buy(jq.context)
    assert len(calls) == 1


@pytest.mark.parametrize("invalid", [-.001, float("nan"), float("inf"), True])
def test_invalid_increase_is_rejected_before_any_sell_or_buy(invalid):
    helper = importlib.reload(helper_module)
    jq = JQ(holdings={"OLD": 100})
    runtime, _, _ = backtest_runtime(jq, helper)
    runtime.prepare_rebalance(jq.context, lambda context: ({"BUY": .5}, jq.prices))
    with pytest.raises(ValueError, match="有限非负数"):
        runtime.execute_prepared_rebalance(
            jq.context, jq_wait_for_sells=True, backtest_buy_price_increase=invalid,
        )
    assert not jq.calls
    assert jq.g.bt_open_decision is not None


@pytest.mark.parametrize("price", [0, -1, float("nan"), float("inf")])
def test_invalid_matching_quote_does_not_force_a_fill_or_reuse_frozen_price(price):
    helper = importlib.reload(helper_module)
    jq = JQ()
    runtime, settings, _ = backtest_runtime(jq, helper)
    runtime._namespace["get_current_data"] = lambda: {"BUY": NS(day_open=price)}
    stage_with_increase(jq, runtime, {"BUY": .5})
    assert not jq.calls and not settings.changes
    assert any("撮合参考价无效" in message for message in jq.messages)


def test_increase_cannot_be_enabled_on_qmt_or_default_execution():
    helper = importlib.reload(helper_module)
    jq = JQ()
    runtime, _, _ = backtest_runtime(jq, helper)
    with pytest.raises(ValueError, match="仅支持独立JQ"):
        runtime.execute_prepared_rebalance(jq.context, backtest_buy_price_increase=.005)
    with pytest.raises(ValueError, match="仅支持独立JQ"):
        runtime.prepare_sell_then_buy(
            jq.context, {"BUY": .5}, jq.prices, "all", backtest_buy_price_increase=.005,
        )
    runtime.qmt_account_enabled = True
    with pytest.raises(ValueError, match="仅支持JQ账户"):
        runtime.execute_prepared_rebalance(
            jq.context, jq_wait_for_sells=True, backtest_buy_price_increase=.005,
        )
    assert not jq.calls


def test_existing_unmodified_plan_without_new_field_retains_original_behavior():
    helper = importlib.reload(helper_module)
    jq = JQ(cash=1000, holdings={"OLD": 900})
    jq.pending.add("OLD")
    runtime, _ = native_runtime(jq, helper)
    stage(jq, runtime, {"BUY": .8})
    jq.g.bt_sell_then_buy.pop("backtest_buy_price_increase")
    fill_pending_sell(jq, "OLD")
    runtime.advance_sell_then_buy(jq.context)
    assert jq.calls == [("OLD", 0), ("BUY", 8000)]
