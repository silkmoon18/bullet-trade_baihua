# -*- coding: utf-8 -*-
"""strategies/joinquant/good_etf.py（L00 精简契约）测试套件。

每个用例用假的 jqdata 环境重新加载策略模块，模块级 _active_mode
随之隔离；helper 以假模块注入 sys.modules。
"""

import ast
import importlib.util
import sys
import types
import uuid
from pathlib import Path

import pandas as pd
import pytest
from helpers import bullet_trade_jq_remote_helper as real_helper

ROOT = Path(__file__).resolve().parents[2]
STRATEGY_PATH = ROOT / "strategies" / "joinquant" / "good_etf.py"
HELPER_MARKER = "bullet-trade-joinquant-runtime-helper-v7"
BLOCKED_MUTATIONS = tuple(sorted({
    "order", "order_value", "order_percent", "order_target",
    "order_target_value", "order_target_percent", "cancel_order",
}))


class _Log:
    def __init__(self):
        self.messages = []

    def info(self, message):
        self.messages.append(str(message))

    def warn(self, message):
        self.messages.append(str(message))

    def error(self, message):
        self.messages.append(str(message))

    def set_level(self, *args, **kwargs):
        return None


class _Context:
    def __init__(self, run_type):
        self.run_params = types.SimpleNamespace(type=run_type)


def _load_strategy(monkeypatch, helper_module=None):
    jqdata = types.ModuleType("jqdata")
    jqdata.g = types.SimpleNamespace()
    jqdata.log = _Log()
    jqdata.__all__ = ["g", "log"]
    monkeypatch.setitem(sys.modules, "jqdata", jqdata)
    if helper_module is None:
        monkeypatch.delitem(sys.modules, "bullet_trade_jq_remote_helper", raising=False)
    else:
        monkeypatch.setitem(sys.modules, "bullet_trade_jq_remote_helper", helper_module)
    name = "good_etf_contract_{}".format(uuid.uuid4().hex)
    spec = importlib.util.spec_from_file_location(name, STRATEGY_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _fake_helper(
    install=None,
    *,
    marker=HELPER_MARKER,
    api_version=7,
    configured_mode="JQ",
):
    module = types.ModuleType("bullet_trade_jq_remote_helper")
    if marker is not None:
        module.STRATEGY_RUNTIME_HELPER_MARKER = marker
    if api_version is not None:
        module.STRATEGY_RUNTIME_API_VERSION = api_version
    if install is not None:
        module.install_strategy_runtime = install
    module.get_configured_execution_mode = (
        lambda strategy_id, profile_module=None: configured_mode
    )
    module.RuntimeMode = real_helper.RuntimeMode
    for name in (
        "ExecutionRequest",
        "ConditionalLimitExecution",
        "ConditionalLimitPriceMode",
        "MarketExecution",
        "FollowUpPolicy",
        "RepricingPolicy",
    ):
        setattr(module, name, getattr(real_helper, name))
    if install is not None:
        class Runtime:
            def __init__(self, state, namespace):
                self.state = state
                mode = state.get("mode") if isinstance(state, dict) else "JQ"
                self.mode = types.SimpleNamespace(value=mode)
                self.namespace = namespace

            def ensure_ready(self, capital, context):
                result = module.ensure_runtime_ready(capital, context)
                self.state["production_ready"] = True
                return result

            def portfolio(self, context):
                entry = getattr(module, "runtime_portfolio", None)
                return entry(context) if callable(entry) else context.portfolio

            def notify_target_buy_plan(self, items, occurred_at=None):
                return module.notify_target_buy_plan(
                    items, occurred_at=occurred_at
                )

            def cancel_orders(self):
                orders = self.namespace.get("get_open_orders", lambda: {})() or {}
                cancel = self.namespace.get("cancel_order")
                for order in list(orders.values()):
                    cancel(order)
                return len(orders)

            def order_target(self, security, amount):
                return self.namespace["order_target"](security, amount)

            def order_target_value(self, security, value, limit_price=None):
                style = None
                if limit_price is not None:
                    style = self.namespace["LimitOrderStyle"](limit_price)
                return self.namespace["order_target_value"](
                    security, value, style=style
                )

        def install_facade(namespace, **kwargs):
            context = kwargs["context"]
            run_type = context.run_params.type
            if run_type in ("simple_backtest", "full_backtest"):
                mode = "BACKTEST"
                validate_remote = kwargs["validate_remote_during_backtest"]
            else:
                mode = configured_mode
                if mode not in ("JQ", "QMT_REMOTE"):
                    raise RuntimeError(
                        "私有配置中的执行模式必须是JQ或QMT_REMOTE"
                    )
                validate_remote = False
            state = install(
                namespace,
                context=context,
                profile=kwargs["profile"],
                mode=mode,
                strategy_id=kwargs["strategy_id"],
                expected_api_version=kwargs["expected_api_version"],
                profile_module=kwargs["profile_module"],
                validate_remote=validate_remote,
            )
            runtime = Runtime(state, namespace)
            if validate_remote and isinstance(state, dict):
                ensured = module.ensure_account(kwargs["initial_capital"])
                assert ensured["reconciliation"]["state"] == "READY"
                portfolio = module.get_portfolio()
                assert module.get_reconciliation()["reconciliation"]["state"] == "READY"
                runtime.state["remote_validation"] = {
                    "cash": portfolio.available_cash,
                    "positions_value": portfolio.positions_value,
                    "total_value": portfolio.total_value,
                    "position_count": len(portfolio.positions),
                    "reconciliation": "READY",
                }
            return runtime

        module.install_joinquant_runtime = install_facade
    return module


def _runtime_state(mode="JQ"):
    state = {
        "api_version": 7,
        "profile_schema_version": 1,
        "profile": "good_etf-prod",
        "mode": mode,
        "run_type": "sim_trade" if mode != "BACKTEST" else "full_backtest",
        "strategy_id": "good_etf",
        "enabled": mode in ("JQ", "QMT_REMOTE"),
        "orders_enabled": mode in ("BACKTEST", "JQ", "QMT_REMOTE"),
        "production_ready": False,
        "reason": {
            "BACKTEST": "backtest",
            "JQ": "jq",
            "QMT_REMOTE": "qmt_remote_profile_validated",
        }[mode],
    }
    if mode in ("JQ", "QMT_REMOTE"):
        state["profile_module"] = "jq_runtime_config"
    if mode == "QMT_REMOTE":
        state["blocked_mutations"] = BLOCKED_MUTATIONS
        state["mirror_jq_orders"] = False
    return state


def test_strategy_source_compiles():
    source = STRATEGY_PATH.read_text(encoding="utf-8")
    compile(source, str(STRATEGY_PATH), "exec")


def test_risk_check_times_are_top_level_configuration(monkeypatch):
    strategy = _load_strategy(monkeypatch)

    assert strategy.RISK_CHECK_TIMES == ("10:30", "13:30", "14:50")


def test_good_etf_declares_rebalance_and_stop_loss_execution_per_call(
    monkeypatch,
):
    strategy = _load_strategy(monkeypatch, _fake_helper())

    rebalance = strategy._rebalance_execution()
    stop_loss = strategy._stop_loss_execution()

    assert isinstance(
        rebalance.style, real_helper.ConditionalLimitExecution
    )
    assert rebalance.style.price_band_ppm == 2_000
    assert (
        rebalance.style.price_mode
        is real_helper.ConditionalLimitPriceMode.BOUNDARY
    )
    assert isinstance(stop_loss.style, real_helper.MarketExecution)
    assert (
        stop_loss.follow_up
        is real_helper.FollowUpPolicy.UNTIL_FILLED_TODAY
    )


@pytest.mark.parametrize("lifecycle", ["initialize", "process_initialize"])
def test_lifecycle_gate_is_first_executable_statement(lifecycle):
    tree = ast.parse(
        STRATEGY_PATH.read_text(encoding="utf-8"), filename=str(STRATEGY_PATH)
    )
    function = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == lifecycle
    )
    body = list(function.body)
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        body = body[1:]
    first = body[0]
    assert isinstance(first, ast.Expr)
    assert isinstance(first.value, ast.Call)
    assert isinstance(first.value.func, ast.Name)
    assert first.value.func.id == "_install_runtime"


@pytest.mark.parametrize("run_type", ["simple_backtest", "full_backtest"])
def test_backtest_fallback_without_helper(monkeypatch, run_type):
    strategy = _load_strategy(monkeypatch)
    strategy.VALIDATE_REMOTE_DURING_BACKTEST = False
    state = strategy._install_runtime(_Context(run_type))
    assert state == {
        "api_version": 7,
        "profile_schema_version": 1,
        "profile": "good_etf-prod",
        "mode": "BACKTEST",
        "run_type": run_type,
        "strategy_id": "good_etf",
        "enabled": False,
        "orders_enabled": True,
        "production_ready": False,
        "reason": "backtest",
    }
    assert strategy.g.bt_runtime == state
    assert strategy._runtime_mode() is strategy.ExecutionMode.BACKTEST


def test_sim_trade_without_helper_is_rejected(monkeypatch):
    strategy = _load_strategy(monkeypatch)
    with pytest.raises(RuntimeError, match="需要bullet_trade_jq_remote_helper"):
        strategy._install_runtime(_Context("sim_trade"))


def test_remote_readiness_is_set_only_after_ready_reconciliation(monkeypatch):
    strategy = _load_strategy(monkeypatch)
    state = {"production_ready": False}
    strategy._runtime = types.SimpleNamespace(
        state=state,
        ensure_ready=lambda _capital, _context: state.update(
            production_ready=True
        ),
    )
    strategy.g.bt_runtime = state

    strategy._ensure_remote_ready(_Context("sim_trade"))

    assert strategy.g.bt_runtime["production_ready"] is True


def test_remote_readiness_stays_false_when_reconciliation_is_blocked(monkeypatch):
    strategy = _load_strategy(monkeypatch)
    state = {"production_ready": False}
    strategy._runtime = types.SimpleNamespace(
        state=state,
        ensure_ready=lambda _capital, _context: (_ for _ in ()).throw(
                RuntimeError("真实账户对账未就绪: fee_fields")
            ),
    )
    strategy.g.bt_runtime = state

    with pytest.raises(RuntimeError, match="fee_fields"):
        strategy._ensure_remote_ready(_Context("sim_trade"))

    assert strategy.g.bt_runtime["production_ready"] is False


@pytest.mark.parametrize("marker", ["wrong-marker", None])
def test_helper_marker_mismatch_rejected(monkeypatch, marker):
    install_calls = []
    helper = _fake_helper(
        lambda *args, **kwargs: install_calls.append((args, kwargs)), marker=marker
    )
    strategy = _load_strategy(monkeypatch, helper)
    with pytest.raises(RuntimeError, match="marker不匹配"):
        strategy._install_runtime(_Context("full_backtest"))
    assert install_calls == []


@pytest.mark.parametrize("api_version", [0, 1, 2, 3, 4, 5, 6, 8])
def test_helper_api_version_mismatch_rejected(monkeypatch, api_version):
    helper = _fake_helper(
        lambda *args, **kwargs: pytest.fail("版本不匹配不得进入安装"),
        api_version=api_version,
    )
    strategy = _load_strategy(monkeypatch, helper)
    with pytest.raises(RuntimeError, match="API版本不匹配"):
        strategy._install_runtime(_Context("full_backtest"))


@pytest.mark.parametrize("entry", ["missing", "uncallable"])
def test_helper_invalid_entry_rejected(monkeypatch, entry):
    helper = _fake_helper()
    if entry == "uncallable":
        helper.install_joinquant_runtime = object()
    strategy = _load_strategy(monkeypatch, helper)
    with pytest.raises(RuntimeError, match="运行时入口无效"):
        strategy._install_runtime(_Context("full_backtest"))


def test_remote_installs_strategy_ledger_runtime(monkeypatch):
    calls = []

    def install(namespace, **kwargs):
        calls.append(kwargs)
        return _runtime_state("QMT_REMOTE")

    helper = _fake_helper(install, configured_mode="QMT_REMOTE")
    strategy = _load_strategy(monkeypatch, helper)
    state = strategy._install_runtime(_Context("sim_trade"))
    assert state["mode"] == "QMT_REMOTE"
    assert calls[0]["expected_api_version"] == 7


def test_helper_non_dict_state_rejected(monkeypatch):
    helper = _fake_helper(lambda namespace, **kwargs: None)
    strategy = _load_strategy(monkeypatch, helper)
    with pytest.raises(RuntimeError, match="无效的运行时状态"):
        strategy._install_runtime(_Context("sim_trade"))


@pytest.mark.parametrize(
    "tamper",
    [
        {"mode": "BACKTEST"},
        {"strategy_id": "other-strategy"},
        {"api_version": 1},
    ],
)
def test_helper_state_contract_mismatch_rejected(monkeypatch, tamper):
    def install(namespace, **kwargs):
        state = _runtime_state("JQ")
        state.update(tamper)
        return state

    strategy = _load_strategy(monkeypatch, _fake_helper(install))
    with pytest.raises(RuntimeError, match="无效的运行时状态"):
        strategy._install_runtime(_Context("sim_trade"))


def test_jq_install_success_and_call_contract(monkeypatch):
    calls = []

    def install(namespace, **kwargs):
        calls.append((namespace, kwargs))
        return _runtime_state("JQ")

    strategy = _load_strategy(monkeypatch, _fake_helper(install))
    context = _Context("sim_trade")
    state = strategy._install_runtime(context)

    assert len(calls) == 1
    namespace, kwargs = calls[0]
    assert namespace is strategy.__dict__
    assert kwargs == {
        "context": context,
        "profile": "good_etf-prod",
        "mode": "JQ",
        "strategy_id": "good_etf",
        "expected_api_version": 7,
        "profile_module": "jq_runtime_config",
        "validate_remote": False,
    }
    assert state["mode"] == "JQ"
    assert strategy.g.bt_runtime == state
    assert strategy._runtime_mode() is strategy.ExecutionMode.JQ


def test_backtest_install_with_helper(monkeypatch):
    calls = []

    def install(namespace, **kwargs):
        calls.append(kwargs)
        return _runtime_state("BACKTEST")

    strategy = _load_strategy(monkeypatch, _fake_helper(install))
    strategy.VALIDATE_REMOTE_DURING_BACKTEST = False
    state = strategy._install_runtime(_Context("full_backtest"))

    assert [call["mode"] for call in calls] == ["BACKTEST"]
    assert state["mode"] == "BACKTEST"
    assert strategy._runtime_mode() is strategy.ExecutionMode.BACKTEST


def test_backtest_remote_validation_uses_real_snapshot_without_submitting(
    monkeypatch
):
    calls = []
    portfolio = types.SimpleNamespace(
        available_cash=8000.0,
        positions_value=2000.0,
        total_value=10000.0,
        positions={"510300.XSHG": object()},
    )

    def install(namespace, **kwargs):
        calls.append(kwargs)
        state = _runtime_state("BACKTEST")
        state.update(
            remote_validation_enabled=True,
            reason="backtest_remote_validation",
            profile_module="jq_runtime_config",
        )
        return state

    helper = _fake_helper(install)
    helper.ensure_account = lambda capital: {
        "capital": capital,
        "reconciliation": {"state": "READY"},
    }
    helper.get_portfolio = lambda: portfolio
    helper.get_reconciliation = lambda: {
        "reconciliation": {"state": "READY"}
    }
    helper.submit_targets = lambda *args, **kwargs: pytest.fail(
        "回测远程预检不得提交组合目标"
    )
    strategy = _load_strategy(monkeypatch, helper)

    state = strategy._install_runtime(_Context("simple_backtest"))

    assert state["mode"] == "BACKTEST"
    assert calls[0]["mode"] == "BACKTEST"
    assert calls[0]["validate_remote"] is True
    assert strategy._runtime_mode() is strategy.ExecutionMode.BACKTEST
    assert strategy.g.bt_remote_validation == {
        "cash": 8000.0,
        "positions_value": 2000.0,
        "total_value": 10000.0,
        "position_count": 1,
        "reconciliation": "READY",
    }


@pytest.mark.parametrize("mode_name", ["JQ", "QMT_REMOTE"])
def test_backtest_validation_flag_has_no_effect_in_sim_trade(
    monkeypatch, mode_name
):
    calls = []

    def install(namespace, **kwargs):
        calls.append(kwargs)
        return _runtime_state(mode_name)

    strategy = _load_strategy(
        monkeypatch, _fake_helper(install, configured_mode=mode_name)
    )
    # 即使模拟交易时该名字被设置为无效值，也必须完全不参与执行路径。
    strategy.VALIDATE_REMOTE_DURING_BACKTEST = "ignored-in-sim-trade"

    strategy._install_runtime(_Context("sim_trade"))

    assert calls[0]["validate_remote"] is False


def test_invalid_backtest_validation_flag_rejected(monkeypatch):
    strategy = _load_strategy(monkeypatch)
    strategy.VALIDATE_REMOTE_DURING_BACKTEST = "true"
    with pytest.raises(RuntimeError, match="必须是bool"):
        strategy._install_runtime(_Context("simple_backtest"))


def test_invalid_configured_execution_mode_is_rejected(monkeypatch):
    helper = _fake_helper(
        lambda namespace, **kwargs: _runtime_state("JQ"),
        configured_mode="INVALID",
    )
    strategy = _load_strategy(monkeypatch, helper)
    with pytest.raises(RuntimeError, match="执行模式必须是JQ或QMT_REMOTE"):
        strategy._install_runtime(_Context("sim_trade"))


def test_target_buy_plan_item_uses_increment_and_round_lot(monkeypatch):
    strategy = _load_strategy(monkeypatch)

    assert strategy._target_buy_plan_item(
        "510001.XSHG", 3000.0, 1000.0, 2.0
    ) == {
        "security": "510001.XSHG",
        "quantity": 1000,
        "amount": 2000.0,
        "reference_price": 2.0,
    }
    assert strategy._target_buy_plan_item(
        "510001.XSHG", 1100.0, 1000.0, 2.0
    ) is None


def test_market_open_logs_the_same_pre_trade_asset_snapshot_used_for_targets(
    monkeypatch,
):
    strategy = _load_strategy(monkeypatch)
    strategy.g.fund_list = pd.DataFrame(
        {"unit_net_value": [2.0]}, index=["510001.XSHG"]
    )
    current_data = {
        "510001.XSHG": types.SimpleNamespace(
            last_price=1.0, paused=False, high_limit=1.1
        )
    }
    old_position = types.SimpleNamespace(total_amount=100, avg_cost=1.0)
    portfolio = types.SimpleNamespace(
        total_value=10000.0,
        positions={"510002.XSHG": old_position},
    )
    monkeypatch.setattr(strategy, "get_current_data", lambda: current_data, raising=False)
    monkeypatch.setattr(strategy, "_advance_remote_intent", lambda context: True)
    monkeypatch.setattr(strategy, "_cancel_open_orders_for_runtime", lambda: 0)
    monkeypatch.setattr(strategy, "_portfolio", lambda context: portfolio)
    monkeypatch.setattr(strategy, "_notify", lambda message: None)
    monkeypatch.setattr(strategy, "_send_target_buy_plan", lambda context, items: None)
    monkeypatch.setattr(
        strategy, "_runtime_mode", lambda: strategy.ExecutionMode.JQ
    )

    def sell_old_position(security, amount):
        portfolio.total_value = 9800.0
        return types.SimpleNamespace(order_id="sell-1")

    monkeypatch.setattr(strategy, "_submit_target_amount", sell_old_position)
    monkeypatch.setattr(
        strategy,
        "_submit_target_value",
        lambda *args, **kwargs: types.SimpleNamespace(order_id="buy-1"),
    )

    strategy.market_open(types.SimpleNamespace(current_dt="2026-08-19 09:30:00"))

    assert any(
        "计划时组合总资产=10000.00 目标部署=9500.00 计划现金缓冲=500.00"
        in message
        for message in strategy.log.messages
    )


def test_jq_target_buy_plan_calls_helper(monkeypatch):
    notifications = []
    helper = _fake_helper(
        lambda namespace, **kwargs: _runtime_state("JQ")
    )
    helper.notify_target_buy_plan = lambda items, occurred_at=None: (
        notifications.append((items, occurred_at))
        or {"accepted": True, "item_count": len(items), "total_amount": 2000.0}
    )
    strategy = _load_strategy(monkeypatch, helper)
    strategy._install_runtime(_Context("sim_trade"))
    context = types.SimpleNamespace(current_dt="2026-08-13 09:30:00")
    items = [{"security": "510001.XSHG", "quantity": 1000, "amount": 2000.0}]

    strategy._send_target_buy_plan(context, items)

    assert notifications == [(items, context.current_dt)]
    assert any("计划卡片已提交" in message for message in strategy.log.messages)


def test_jq_uses_native_order_and_cancel(monkeypatch):
    helper = _fake_helper(lambda namespace, **kwargs: _runtime_state("JQ"))
    helper.notify_target_buy_plan = lambda items, occurred_at=None: {
        "accepted": True,
        "item_count": len(items),
        "total_amount": sum(float(item["amount"]) for item in items),
    }
    strategy = _load_strategy(monkeypatch, helper)
    strategy._install_runtime(_Context("sim_trade"))
    calls = []
    open_order = types.SimpleNamespace(order_id="jq-open-1")
    strategy.get_open_orders = lambda: {"jq-open-1": open_order}
    strategy.cancel_order = lambda order: calls.append(("cancel", order))
    strategy.order_target = lambda security, amount: (
        calls.append(("amount", security, amount)) or "amount-order"
    )
    strategy.order_target_value = lambda security, value, style=None: (
        calls.append(("value", security, value, style)) or "value-order"
    )
    strategy.BUY_PRICE_FLOAT_PCT = 0

    assert strategy._cancel_open_orders_for_runtime() == 1
    assert strategy._submit_target_amount("510001.XSHG", 100) == "amount-order"
    assert strategy._submit_target_value("510001.XSHG", 2000.0) == "value-order"
    strategy._send_target_buy_plan(
        types.SimpleNamespace(current_dt="2026-08-18 09:30:00"),
        [{"security": "510001.XSHG", "quantity": 100, "amount": 2000.0}],
    )

    assert calls == [
        ("cancel", open_order),
        ("amount", "510001.XSHG", 100),
        ("value", "510001.XSHG", 2000.0, None),
    ]


def test_remote_stop_loss_preempts_waiting_rebalance(monkeypatch):
    strategy = _load_strategy(monkeypatch, _fake_helper())
    position = types.SimpleNamespace(
        price=9.0,
        avg_cost=10.0,
        value=9000.0,
    )
    portfolio = types.SimpleNamespace(
        total_value=10000.0,
        positions={"510001.XSHG": position},
    )
    submissions = []
    monkeypatch.setattr(
        strategy,
        "_runtime_mode",
        lambda: strategy.ExecutionMode.QMT_REMOTE,
    )
    monkeypatch.setattr(strategy, "_portfolio", lambda context: portfolio)
    monkeypatch.setattr(strategy, "_advance_remote_intent", lambda context: False)
    monkeypatch.setattr(strategy, "_cancel_remote_intent", lambda: True)
    monkeypatch.setattr(strategy, "_notify", lambda message: None)
    monkeypatch.setattr(
        strategy,
        "_submit_remote_targets",
        lambda *args: submissions.append(args)
        or {"intent": {"intent_id": "risk-1"}},
    )

    strategy.handle_risk_management(
        types.SimpleNamespace(
            current_dt=pd.Timestamp("2026-08-20 10:30:00")
        )
    )

    assert len(submissions) == 1
    assert submissions[0][1] == {"510001.XSHG": 0.0}
    assert isinstance(submissions[0][4].style, real_helper.MarketExecution)


def test_runtime_mode_raises_before_install(monkeypatch):
    strategy = _load_strategy(monkeypatch)
    with pytest.raises(RuntimeError, match="尚未安装"):
        strategy._runtime_mode()
    with pytest.raises(RuntimeError, match="拒绝执行交易动作"):
        strategy._submit_target_amount("510001.XSHG", 100)
