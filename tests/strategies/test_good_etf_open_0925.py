# -*- coding: utf-8 -*-
"""09:26 decisions and liquidation; 09:30 buys after independent confirmation."""

import ast
import types
from pathlib import Path

import pandas as pd
import pytest

from helpers import bullet_trade_jq_remote_helper as helper
from tests.strategies.test_good_etf_contract import _Runtime, _load_strategy


ROOT = Path(__file__).resolve().parents[2]
ORIGINAL = ROOT / "strategies" / "joinquant" / "good_etf.py"
OPEN_VARIANT = ROOT / "strategies" / "joinquant" / "good_etf_open_0925.py"


def _load_open_strategy(monkeypatch):
    return _load_strategy(monkeypatch, strategy_path=OPEN_VARIANT)


def test_open_variant_is_standalone_and_preserves_other_decisions(monkeypatch):
    original = ast.parse(ORIGINAL.read_text(encoding="utf-8"))
    variant = ast.parse(
        OPEN_VARIANT.read_text(encoding="utf-8"), feature_version=(3, 8)
    )
    original_functions = {
        node.name: node for node in original.body if isinstance(node, ast.FunctionDef)
    }
    variant_functions = {
        node.name: node for node in variant.body if isinstance(node, ast.FunctionDef)
    }
    assert set(variant_functions) == set(original_functions) | {"execute_opening_plan"}
    for name in set(original_functions) - {"initialize", "market_open"}:
        assert ast.dump(variant_functions[name]) == ast.dump(original_functions[name])

    source = OPEN_VARIANT.read_text(encoding="utf-8")
    assert "import bullet_trade_jq_remote_helper as bt" in source
    assert "from ." not in source
    assert "run_daily(" not in source
    assert "last_price" not in source
    assert "BUY_PRICE_OFFSET" not in source
    assert "buy_limit_prices" not in source
    assert "LimitOrderStyle" not in source
    strategy = _load_open_strategy(monkeypatch)
    assert strategy.STRATEGY_ID == "good_etf_open_0925"
    for name in (
        "MAX_HOLD_NUM", "MIN_MONEY", "MAX_MONEY", "STOP_LOSS_RATIO",
        "TAKE_PROFIT_RATIO", "DEPLOY_RATIO", "SKIP_SUSPENDED_LIMITUP",
        "RISK_CHECK_TIMES", "QMT_INITIAL_CAPITAL",
    ):
        original_strategy = _load_strategy(monkeypatch)
        assert getattr(strategy, name) == getattr(original_strategy, name)


def test_open_variant_registers_0926_decision_and_0930_execution(monkeypatch):
    strategy = _load_open_strategy(monkeypatch)
    runtime = _Runtime(helper.RuntimeMode.JQ)
    strategy._runtime = runtime
    monkeypatch.setattr(strategy, "_install_runtime", lambda context: None)

    strategy.initialize(object())

    assert runtime.opening_decision is strategy.market_open
    assert runtime.opening_decision_time == strategy.OPEN_DECISION_TIME == "09:26"
    assert runtime.market_open_time == strategy.BUY_START_TIME == "09:30"
    assert runtime.sell_then_buy is True
    assert runtime.schedules[0][1] is strategy.execute_opening_plan


def test_original_selects_and_submits_at_0930_without_staged_execution(monkeypatch):
    strategy = _load_strategy(monkeypatch)
    runtime = _Runtime(helper.RuntimeMode.JQ)
    strategy._runtime = runtime
    monkeypatch.setattr(strategy, "_install_runtime", lambda context: None)
    strategy.initialize(object())
    assert runtime.market_open_time == "09:30"
    assert runtime.schedules[0][1] is strategy.market_open
    assert runtime.opening_decision is None
    assert runtime.sell_then_buy is False


def test_open_variant_uses_auction_open_for_filter_rank_weights_and_marks(monkeypatch):
    strategy = _load_open_strategy(monkeypatch)
    codes = ["510001.XSHG", "510002.XSHG", "510003.XSHG",
             "510004.XSHG", "510005.XSHG", "510006.XSHG"]
    strategy.g.fund_list = pd.DataFrame(
        {"unit_net_value": [2.0] * len(codes)}, index=codes
    )
    values = [
        (1.0, 1.9, False, 3.0),
        (1.5, 1.1, False, 3.0),
        (1.8, 1.2, False, 3.0),
        (2.1, 1.0, False, 3.0),  # Premium at the auction open.
        (0.5, 0.5, True, 3.0),  # Still excludes paused securities.
        (1.9, 1.5, False, 1.9),  # Limit-up at the auction open.
    ]
    current_data = {
        code: types.SimpleNamespace(
            day_open=open_price,
            last_price=last_price,
            paused=paused,
            high_limit=high_limit,
        )
        for code, (open_price, last_price, paused, high_limit)
        in zip(codes, values)
    }
    runtime = _Runtime(
        helper.RuntimeMode.JQ,
        types.SimpleNamespace(total_value=10000.0, positions={}),
    )
    strategy._runtime = runtime
    monkeypatch.setattr(strategy, "get_current_data", lambda: current_data, raising=False)

    decision_context = types.SimpleNamespace(
        current_dt=pd.Timestamp("2026-09-30 09:26:00")
    )
    strategy.market_open(decision_context)

    assert runtime.rebalances == []
    assert len(runtime.prepared_rebalances) == 1
    _, weights, marks, key, buy_time = runtime.prepared_rebalances[0]
    assert list(weights) == codes[:3]
    assert weights == pytest.approx({
        codes[0]: 50.0 / 85.0 * 0.95,
        codes[1]: 25.0 / 85.0 * 0.95,
        codes[2]: 10.0 / 85.0 * 0.95,
    })
    assert marks == {codes[0]: 1.0, codes[1]: 1.5, codes[2]: 1.8}
    assert buy_time == "09:30"
    assert key == "open-20260930"


@pytest.mark.parametrize("open_price", [None, 0.0, float("nan"), float("inf")])
def test_open_variant_does_not_fallback_when_auction_price_unavailable(
    monkeypatch, open_price
):
    strategy = _load_open_strategy(monkeypatch)
    code = "510001.XSHG"
    strategy.g.fund_list = pd.DataFrame(
        {"unit_net_value": [2.0]}, index=[code]
    )
    runtime = _Runtime(
        helper.RuntimeMode.JQ,
        types.SimpleNamespace(total_value=10000.0, positions={}),
    )
    strategy._runtime = runtime
    monkeypatch.setattr(strategy, "get_current_data", lambda: {
        code: types.SimpleNamespace(
            day_open=open_price, last_price=1.0,
            paused=False, high_limit=3.0,
        )
    }, raising=False)

    strategy.market_open(
        types.SimpleNamespace(current_dt=pd.Timestamp("2026-09-30 09:26:00"))
    )

    assert runtime.rebalances == []
    assert runtime.prepared_rebalances == []


def test_open_variant_does_not_reselect_or_submit_at_0930(monkeypatch):
    strategy = _load_open_strategy(monkeypatch)
    runtime = _Runtime(
        helper.RuntimeMode.JQ,
        types.SimpleNamespace(total_value=10000.0, positions={}),
    )
    strategy._runtime = runtime
    context = types.SimpleNamespace(current_dt=pd.Timestamp("2026-09-30 09:30:00"))

    monkeypatch.setattr(strategy, "get_current_data",
                        lambda: pytest.fail("09:30 must not reselect"), raising=False)
    strategy.market_open(context)
    strategy.execute_opening_plan(context)

    assert runtime.rebalances == []
    assert runtime.prepared_rebalances == []
    assert runtime.staged_advances == [context]


def test_open_variant_submits_empty_sell_all_target_when_no_discount(monkeypatch):
    strategy = _load_open_strategy(monkeypatch)
    code = "510001.XSHG"
    strategy.g.fund_list = pd.DataFrame({"unit_net_value": [2.0]}, index=[code])
    runtime = _Runtime(
        helper.RuntimeMode.JQ,
        types.SimpleNamespace(total_value=10000.0, positions={}),
    )
    strategy._runtime = runtime
    monkeypatch.setattr(strategy, "get_current_data", lambda: {
        code: types.SimpleNamespace(day_open=2.1, paused=False, high_limit=3.0)
    }, raising=False)

    strategy.market_open(
        types.SimpleNamespace(current_dt=pd.Timestamp("2026-09-30 09:26:00"))
    )

    assert len(runtime.prepared_rebalances) == 1
    assert runtime.prepared_rebalances[0][1:4] == ({}, {}, "open-20260930")
    assert runtime.rebalances == []
