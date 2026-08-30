# -*- coding: utf-8 -*-
"""Account-switch contract for the JoinQuant runtime."""

import importlib
import sys
import types

import pytest

import helpers.bullet_trade_jq_remote_helper as _helper


PROFILE_MODULE = "test_jq_account_switch_config"
STRATEGY_ID = "good_etf"


@pytest.fixture()
def helper():
    return importlib.reload(_helper)


def _context(run_type="sim_trade"):
    return types.SimpleNamespace(
        run_params=types.SimpleNamespace(type=run_type)
    )


def _config(monkeypatch, strategy=None):
    module = types.ModuleType(PROFILE_MODULE)
    module.PROFILE_SCHEMA_VERSION = 3
    module.DEFAULT_PROFILE = "qmt-main"
    module.PROFILES = {
        "qmt-main": {"host": "127.0.0.1", "token": "unit-token"}
    }
    module.STRATEGIES = (
        {} if strategy is None else {STRATEGY_ID: strategy}
    )
    monkeypatch.setitem(sys.modules, PROFILE_MODULE, module)
    monkeypatch.setattr(
        _helper,
        "ensure_runtime_ready",
        lambda initial_capital, context: getattr(context, "portfolio", None),
    )
    return module


def test_missing_strategy_defaults_to_jq_only(helper, monkeypatch):
    _config(monkeypatch)

    switches = helper.get_configured_account_switches(
        STRATEGY_ID, PROFILE_MODULE
    )

    assert switches == {
        "jq_account_enabled": True,
        "qmt_account_enabled": False,
    }


@pytest.mark.parametrize(
    ("jq_enabled", "qmt_enabled", "mode"),
    [
        (True, False, "JQ"),
        (False, True, "QMT_REMOTE"),
        (True, True, "JQ_QMT_PARALLEL"),
    ],
)
def test_sim_runtime_derives_account_combination(
    helper, monkeypatch, jq_enabled, qmt_enabled, mode
):
    _config(
        monkeypatch,
        {
            "jq_account_enabled": jq_enabled,
            "qmt_account_enabled": qmt_enabled,
        },
    )
    namespace = {
        name: (lambda *args, **kwargs: None)
        for name in helper._RUNTIME_MUTATION_NAMES
    }

    runtime = helper.install_joinquant_runtime(
        namespace,
        context=_context(),
        strategy_id=STRATEGY_ID,
        qmt_initial_capital="10000",
        profile_module=PROFILE_MODULE,
    )

    assert runtime.state["mode"] == mode
    assert runtime.jq_account_enabled is jq_enabled
    assert runtime.qmt_account_enabled is qmt_enabled
    if jq_enabled:
        assert namespace["order_target"].__name__ != (
            "runtime_blocked_{}_order_target".format(mode.lower())
        )
    else:
        assert namespace["order_target"].__name__.startswith(
            "runtime_blocked_"
        )


def test_both_accounts_disabled_is_rejected(helper, monkeypatch):
    _config(
        monkeypatch,
        {
            "jq_account_enabled": False,
            "qmt_account_enabled": False,
        },
    )

    with pytest.raises(RuntimeError, match="至少启用一个账户"):
        helper.install_joinquant_runtime(
            {},
            context=_context(),
            strategy_id=STRATEGY_ID,
            qmt_initial_capital="10000",
            profile_module=PROFILE_MODULE,
        )


def test_legacy_mode_field_is_rejected(helper, monkeypatch):
    _config(monkeypatch, {"mode": "QMT_REMOTE"})

    with pytest.raises(RuntimeError, match="未知字段"):
        helper.get_configured_account_switches(
            STRATEGY_ID, PROFILE_MODULE
        )


def _portfolio(total_value, positions=None):
    positions = {} if positions is None else positions
    return types.SimpleNamespace(
        total_value=float(total_value),
        available_cash=float(total_value),
        positions_value=0.0,
        positions=positions,
    )


def _position(price, avg_cost, value):
    return types.SimpleNamespace(
        price=float(price),
        avg_cost=float(avg_cost),
        value=float(value),
        total_amount=int(value / price),
        closeable_amount=int(value / price),
    )


def test_parallel_startup_qmt_failure_keeps_jq_running(helper, monkeypatch):
    _config(monkeypatch, {
        "jq_account_enabled": True,
        "qmt_account_enabled": True,
    })
    context = _context()
    context.portfolio = _portfolio(10_000)
    messages = []
    namespace = {
        "log": types.SimpleNamespace(
            warn=lambda message: messages.append(str(message))
        )
    }
    monkeypatch.setattr(
        helper,
        "ensure_runtime_ready",
        lambda initial_capital, current: (_ for _ in ()).throw(
            RuntimeError("broker sellable quantity not refreshed")
        ),
    )

    runtime = helper.install_joinquant_runtime(
        namespace,
        context=context,
        strategy_id=STRATEGY_ID,
        qmt_initial_capital=10_000,
        profile_module=PROFILE_MODULE,
    )

    assert runtime.state["production_ready"] is False
    assert helper._active_state["production_ready"] is False
    assert "JQ账户继续运行" in messages[0]


def test_qmt_only_startup_failure_remains_fail_closed(helper, monkeypatch):
    _config(monkeypatch, {
        "jq_account_enabled": False,
        "qmt_account_enabled": True,
    })
    context = _context()
    monkeypatch.setattr(
        helper,
        "ensure_runtime_ready",
        lambda initial_capital, current: (_ for _ in ()).throw(
            RuntimeError("position mismatch")
        ),
    )

    with pytest.raises(RuntimeError, match="position mismatch"):
        helper.install_joinquant_runtime(
            {},
            context=context,
            strategy_id=STRATEGY_ID,
            qmt_initial_capital=10_000,
            profile_module=PROFILE_MODULE,
        )


def test_parallel_qmt_readiness_is_retried_before_execution(helper, monkeypatch):
    _config(monkeypatch, {
        "jq_account_enabled": True,
        "qmt_account_enabled": True,
    })
    jq_orders = []
    namespace = {
        "order_target": lambda security, amount: None,
        "order_target_value": (
            lambda security, value, style=None:
            jq_orders.append((security, value))
        ),
        "get_open_orders": lambda: {},
        "cancel_order": lambda order: None,
    }
    for name in helper._RUNTIME_MUTATION_NAMES:
        namespace.setdefault(name, lambda *args, **kwargs: None)
    context = _context()
    context.current_dt = "2026-08-28 09:30:00"
    context.portfolio = _portfolio(10_000)
    readiness_calls = []

    def readiness(initial_capital, current):
        readiness_calls.append(initial_capital)
        if len(readiness_calls) == 1:
            raise RuntimeError("overnight broker snapshot")
        return _portfolio(20_000)

    monkeypatch.setattr(helper, "ensure_runtime_ready", readiness)
    runtime = helper.install_joinquant_runtime(
        namespace,
        context=context,
        strategy_id=STRATEGY_ID,
        qmt_initial_capital=10_000,
        profile_module=PROFILE_MODULE,
    )
    monkeypatch.setattr(helper, "advance_runtime_targets", lambda current: True)
    monkeypatch.setattr(helper, "get_portfolio", lambda **kwargs: _portfolio(20_000))
    monkeypatch.setattr(
        helper,
        "submit_runtime_targets",
        lambda current, weights, marks, key, execution, security_names=None: {
            "intent": {"intent_id": "i-1", "state": "EXECUTING"}
        },
    )
    monkeypatch.setattr(
        helper,
        "notify_target_buy_plan",
        lambda items, occurred_at=None: {
            "accepted": True,
            "item_count": len(items),
            "total_amount": sum(item["amount"] for item in items),
        },
    )

    result = runtime.execute_rebalance(
        context,
        {"510300.XSHG": 0.5},
        {"510300.XSHG": 10.0},
        "open-20260828",
        helper.ExecutionRequest(),
    )

    assert readiness_calls == [10_000, 10_000]
    assert runtime.state["production_ready"] is True
    assert result["qmt"]["intent"]["intent_id"] == "i-1"
    assert jq_orders == [("510300.XSHG", 5_000.0)]


def test_parallel_snapshot_failure_does_not_hide_jq_snapshot(helper, monkeypatch):
    _config(monkeypatch, {
        "jq_account_enabled": True,
        "qmt_account_enabled": True,
    })
    context = _context()
    context.portfolio = _portfolio(10_000)
    runtime = helper.install_joinquant_runtime(
        {},
        context=context,
        strategy_id=STRATEGY_ID,
        qmt_initial_capital=10_000,
        profile_module=PROFILE_MODULE,
    )
    monkeypatch.setattr(
        helper,
        "get_portfolio",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("qmt offline")),
    )

    views = runtime.account_portfolios(context)

    assert [(view.account, view.portfolio) for view in views] == [
        ("JQ", context.portfolio)
    ]
    assert runtime.state["production_ready"] is False


def test_parallel_rebalance_sizes_each_account_from_its_own_total_and_notifies_qmt(
    helper, monkeypatch
):
    _config(monkeypatch, {
        "jq_account_enabled": True,
        "qmt_account_enabled": True,
    })
    jq_orders = []
    namespace = {
        "order_target": lambda security, amount: None,
        "order_target_value": (
            lambda security, value, style=None:
            jq_orders.append((security, value, style))
        ),
        "get_open_orders": lambda: {},
        "cancel_order": lambda order: None,
    }
    for name in helper._RUNTIME_MUTATION_NAMES:
        namespace.setdefault(name, lambda *args, **kwargs: None)
    context = _context()
    context.current_dt = "2026-08-28 09:30:00"
    context.portfolio = _portfolio(10_000)
    runtime = helper.install_joinquant_runtime(
        namespace,
        context=context,
        strategy_id=STRATEGY_ID,
        qmt_initial_capital=10_000,
        profile_module=PROFILE_MODULE,
    )
    qmt_portfolio = _portfolio(20_000)
    submitted = []
    notified = []
    monkeypatch.setattr(helper, "advance_runtime_targets", lambda context: True)
    monkeypatch.setattr(helper, "get_portfolio", lambda **kwargs: qmt_portfolio)

    def submit(
        context, weights, marks, key, execution, security_names=None
    ):
        submitted.append((weights, marks, key, execution, security_names))
        return {"intent": {"intent_id": "i-1", "state": "EXECUTING"}}

    monkeypatch.setattr(helper, "submit_runtime_targets", submit)
    monkeypatch.setattr(
        helper,
        "notify_target_buy_plan",
        lambda items, occurred_at=None: (
            notified.append((items, occurred_at))
            or {
                "accepted": True,
                "item_count": len(items),
                "total_amount": sum(item["amount"] for item in items),
            }
        ),
    )

    runtime.execute_rebalance(
        context,
        {"510300.XSHG": 0.5},
        {"510300.XSHG": 10.0},
        "open-20260828",
        helper.ExecutionRequest(),
    )

    assert submitted[0][0] == {"510300.XSHG": 0.5}
    assert jq_orders == [("510300.XSHG", 5_000.0, None)]
    assert notified[0][0] == [{
        "security": "510300.XSHG",
        "quantity": 1000,
        "amount": 10_000.0,
        "reference_price": 10.0,
    }]


def test_parallel_notification_uses_qmt_channel_only(helper, monkeypatch):
    _config(monkeypatch, {
        "jq_account_enabled": True,
        "qmt_account_enabled": True,
    })
    namespace = {
        name: (lambda *args, **kwargs: None)
        for name in helper._RUNTIME_MUTATION_NAMES
    }
    runtime = helper.install_joinquant_runtime(
        namespace,
        context=_context(),
        strategy_id=STRATEGY_ID,
        qmt_initial_capital=10_000,
        profile_module=PROFILE_MODULE,
    )
    requests = []
    monkeypatch.setattr(
        helper,
        "_strategy_request",
        lambda action, payload: (
            requests.append((action, payload)) or {"accepted": True}
        ),
    )

    runtime.notify_target_buy_plan([
        {
            "security": "510300.XSHG",
            "quantity": 100,
            "amount": 1000.0,
            "reference_price": 10.0,
        }
    ])

    assert requests[0][0] == "strategy.notify_target_buy_plan"
    assert requests[0][1]["mode"] == "QMT_REMOTE"


def test_qmt_rebalance_failure_does_not_block_jq_account(helper, monkeypatch):
    _config(monkeypatch, {
        "jq_account_enabled": True,
        "qmt_account_enabled": True,
    })
    jq_orders = []
    namespace = {
        "order_target": lambda security, amount: None,
        "order_target_value": (
            lambda security, value, style=None:
            jq_orders.append((security, value))
        ),
        "get_open_orders": lambda: {},
        "cancel_order": lambda order: None,
    }
    for name in helper._RUNTIME_MUTATION_NAMES:
        namespace.setdefault(name, lambda *args, **kwargs: None)
    context = _context()
    context.current_dt = "2026-08-28 09:30:00"
    context.portfolio = _portfolio(10_000)
    runtime = helper.install_joinquant_runtime(
        namespace,
        context=context,
        strategy_id=STRATEGY_ID,
        qmt_initial_capital=10_000,
        profile_module=PROFILE_MODULE,
    )
    monkeypatch.setattr(helper, "advance_runtime_targets", lambda context: True)
    monkeypatch.setattr(
        helper, "get_portfolio", lambda **kwargs: (_ for _ in ()).throw(
            RuntimeError("qmt unavailable")
        )
    )

    result = runtime.execute_rebalance(
        context,
        {"510300.XSHG": 0.5},
        {"510300.XSHG": 10.0},
        "open-20260828",
        helper.ExecutionRequest(),
    )

    assert result["errors"][0][0] == "QMT"
    assert jq_orders == [("510300.XSHG", 5_000.0)]


def test_active_qmt_intent_does_not_block_parallel_jq_rebalance(
    helper, monkeypatch
):
    _config(monkeypatch, {
        "jq_account_enabled": True,
        "qmt_account_enabled": True,
    })
    jq_orders = []
    namespace = {
        "order_target": lambda security, amount: None,
        "order_target_value": (
            lambda security, value, style=None:
            jq_orders.append((security, value))
        ),
        "get_open_orders": lambda: {},
        "cancel_order": lambda order: None,
    }
    for name in helper._RUNTIME_MUTATION_NAMES:
        namespace.setdefault(name, lambda *args, **kwargs: None)
    context = _context()
    context.current_dt = "2026-08-28 09:30:00"
    context.portfolio = _portfolio(10_000)
    runtime = helper.install_joinquant_runtime(
        namespace,
        context=context,
        strategy_id=STRATEGY_ID,
        qmt_initial_capital=10_000,
        profile_module=PROFILE_MODULE,
    )
    monkeypatch.setattr(helper, "advance_runtime_targets", lambda context: False)

    result = runtime.execute_rebalance(
        context,
        {"510300.XSHG": 0.5},
        {"510300.XSHG": 10.0},
        "open-20260828",
        helper.ExecutionRequest(),
    )

    assert result["qmt"] == {"skipped_active_intent": True}
    assert jq_orders == [("510300.XSHG", 5_000.0)]


def test_parallel_risk_uses_each_accounts_own_cost_basis(helper, monkeypatch):
    _config(monkeypatch, {
        "jq_account_enabled": True,
        "qmt_account_enabled": True,
    })
    jq_exits = []
    namespace = {
        "order_target": lambda security, amount: jq_exits.append((security, amount)),
    }
    for name in helper._RUNTIME_MUTATION_NAMES:
        namespace.setdefault(name, lambda *args, **kwargs: None)
    context = _context()
    context.current_dt = "2026-08-28 10:30:00"
    context.portfolio = _portfolio(
        10_000,
        {"510300.XSHG": _position(9.0, 8.5, 4_500)},
    )
    runtime = helper.install_joinquant_runtime(
        namespace,
        context=context,
        strategy_id=STRATEGY_ID,
        qmt_initial_capital=10_000,
        profile_module=PROFILE_MODULE,
    )
    qmt_portfolio = _portfolio(
        20_000,
        {"510300.XSHG": _position(9.0, 10.0, 9_000)},
    )
    monkeypatch.setattr(helper, "get_portfolio", lambda **kwargs: qmt_portfolio)
    monkeypatch.setattr(helper, "advance_runtime_targets", lambda context: True)
    submitted = []
    monkeypatch.setattr(
        helper,
        "submit_runtime_targets",
        lambda context, weights, marks, key, execution, security_names=None: (
            submitted.append((weights, key, execution))
            or {"intent": {"intent_id": "risk-1", "state": "EXECUTING"}}
        ),
    )

    result = runtime.execute_risk_management(
        context,
        0.95,
        1.10,
        "risk-20260828-1030",
        helper.ExecutionRequest(style=helper.MarketExecution()),
        helper.ExecutionRequest(),
    )

    assert result["accounts"][0]["stop_loss"] == ["510300.XSHG"]
    assert result["accounts"][1]["stop_loss"] == []
    assert submitted[0][0] == {"510300.XSHG": 0.0}
    assert jq_exits == []
