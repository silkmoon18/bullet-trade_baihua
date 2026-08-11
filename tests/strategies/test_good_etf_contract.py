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

import pytest

ROOT = Path(__file__).resolve().parents[2]
STRATEGY_PATH = ROOT / "strategies" / "joinquant" / "good_etf.py"
HELPER_MARKER = "bullet-trade-joinquant-runtime-helper-v2"
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


def _fake_helper(install=None, *, marker=HELPER_MARKER, api_version=2):
    module = types.ModuleType("bullet_trade_jq_remote_helper")
    if marker is not None:
        module.STRATEGY_RUNTIME_HELPER_MARKER = marker
    if api_version is not None:
        module.STRATEGY_RUNTIME_API_VERSION = api_version
    if install is not None:
        module.install_strategy_runtime = install
    return module


def _runtime_state(mode="SHADOW"):
    return {
        "api_version": 2,
        "profile_schema_version": 1,
        "profile": "good_etf-prod",
        "mode": mode,
        "run_type": "sim_trade" if mode != "BACKTEST" else "full_backtest",
        "strategy_id": "good_etf",
        "enabled": mode == "SHADOW",
        "orders_enabled": mode == "BACKTEST",
        "production_ready": False,
        "reason": "shadow_read_only" if mode == "SHADOW" else "backtest",
        "profile_module": "jq_runtime_config",
        "blocked_mutations": BLOCKED_MUTATIONS,
    }


def test_strategy_source_compiles():
    source = STRATEGY_PATH.read_text(encoding="utf-8")
    compile(source, str(STRATEGY_PATH), "exec")


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
    state = strategy._install_runtime(_Context(run_type))
    assert state == {
        "api_version": 2,
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
    assert strategy._runtime_mode() == "BACKTEST"


@pytest.mark.parametrize("mode", ["SHADOW", "LIVE"])
def test_fallback_without_helper_rejects_remote_modes(monkeypatch, mode):
    strategy = _load_strategy(monkeypatch)
    strategy.MODE = mode
    run_type = "sim_trade"
    with pytest.raises(RuntimeError, match="需要bullet_trade_jq_remote_helper"):
        strategy._install_runtime(_Context(run_type))


def test_fallback_backtest_rejects_sim_trade_run_type(monkeypatch):
    strategy = _load_strategy(monkeypatch)
    with pytest.raises(RuntimeError, match="仅允许聚宽回测"):
        strategy._install_runtime(_Context("sim_trade"))


def test_live_readiness_is_set_only_after_ready_reconciliation(monkeypatch):
    strategy = _load_strategy(monkeypatch)
    strategy.g.bt_runtime = {"production_ready": False}
    monkeypatch.setattr(
        strategy,
        "bt",
        types.SimpleNamespace(
            ensure_account=lambda _capital: {
                "reconciliation": {"state": "READY"}
            }
        ),
    )
    monkeypatch.setattr(strategy, "_portfolio", lambda _context: object())
    monkeypatch.setattr(strategy, "_restore_live_intent", lambda: None)

    strategy._ensure_live_ready(_Context("sim_trade"))

    assert strategy.g.bt_runtime["production_ready"] is True


def test_live_readiness_stays_false_when_reconciliation_is_blocked(monkeypatch):
    strategy = _load_strategy(monkeypatch)
    strategy.g.bt_runtime = {"production_ready": False}
    monkeypatch.setattr(
        strategy,
        "bt",
        types.SimpleNamespace(
            ensure_account=lambda _capital: {
                "reconciliation": {
                    "state": "BLOCKED",
                    "details": {"blockers": ["fee_fields"]},
                }
            }
        ),
    )

    with pytest.raises(RuntimeError, match="fee_fields"):
        strategy._ensure_live_ready(_Context("sim_trade"))

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


@pytest.mark.parametrize("api_version", [0, 1, 3])
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
        helper.install_strategy_runtime = object()
    strategy = _load_strategy(monkeypatch, helper)
    with pytest.raises(RuntimeError, match="运行时入口无效"):
        strategy._install_runtime(_Context("full_backtest"))


def test_live_installs_strategy_ledger_runtime(monkeypatch):
    calls = []

    def install(namespace, **kwargs):
        calls.append(kwargs)
        return _runtime_state("LIVE")

    helper = _fake_helper(install)
    strategy = _load_strategy(monkeypatch, helper)
    strategy.MODE = "LIVE"
    state = strategy._install_runtime(_Context("sim_trade"))
    assert state["mode"] == "LIVE"
    assert calls[0]["expected_api_version"] == 2


def test_helper_non_dict_state_rejected(monkeypatch):
    helper = _fake_helper(lambda namespace, **kwargs: None)
    strategy = _load_strategy(monkeypatch, helper)
    strategy.MODE = "SHADOW"
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
        state = _runtime_state("SHADOW")
        state.update(tamper)
        return state

    strategy = _load_strategy(monkeypatch, _fake_helper(install))
    strategy.MODE = "SHADOW"
    with pytest.raises(RuntimeError, match="无效的运行时状态"):
        strategy._install_runtime(_Context("sim_trade"))


def test_shadow_install_success_and_call_contract(monkeypatch):
    calls = []

    def install(namespace, **kwargs):
        calls.append((namespace, kwargs))
        return _runtime_state("SHADOW")

    strategy = _load_strategy(monkeypatch, _fake_helper(install))
    strategy.MODE = "SHADOW"
    context = _Context("sim_trade")
    state = strategy._install_runtime(context)

    assert len(calls) == 1
    namespace, kwargs = calls[0]
    assert namespace is strategy.__dict__
    assert kwargs == {
        "context": context,
        "profile": "good_etf-prod",
        "mode": "SHADOW",
        "strategy_id": "good_etf",
        "expected_api_version": 2,
    }
    assert state["mode"] == "SHADOW"
    assert strategy.g.bt_runtime == state
    assert strategy._runtime_mode() == "SHADOW"


def test_backtest_install_with_helper(monkeypatch):
    calls = []

    def install(namespace, **kwargs):
        calls.append(kwargs)
        return _runtime_state("BACKTEST")

    strategy = _load_strategy(monkeypatch, _fake_helper(install))
    state = strategy._install_runtime(_Context("full_backtest"))

    assert [call["mode"] for call in calls] == ["BACKTEST"]
    assert state["mode"] == "BACKTEST"
    assert strategy._runtime_mode() == "BACKTEST"


def _shadow_strategy(monkeypatch):
    strategy = _load_strategy(
        monkeypatch, _fake_helper(lambda namespace, **kwargs: _runtime_state("SHADOW"))
    )
    strategy.MODE = "SHADOW"
    strategy._install_runtime(_Context("sim_trade"))
    return strategy


def test_shadow_submit_target_amount_only_logs(monkeypatch):
    strategy = _shadow_strategy(monkeypatch)
    orders = []
    strategy.order_target = lambda *args: orders.append(args)

    assert strategy._submit_target_amount("510001.XSHG", 100) is None
    assert orders == []
    assert any("SHADOW目标数量" in message for message in strategy.log.messages)


def test_shadow_submit_target_value_only_logs(monkeypatch):
    strategy = _shadow_strategy(monkeypatch)
    orders = []
    strategy.order_target_value = lambda *args, **kwargs: orders.append(args)

    result = strategy._submit_target_value(
        "510001.XSHG", 1000.0, last_price=10.0, current_value=0.0
    )
    assert result is None
    assert orders == []
    assert any("SHADOW目标市值" in message for message in strategy.log.messages)


def test_shadow_cancel_open_orders_only_logs(monkeypatch):
    strategy = _shadow_strategy(monkeypatch)
    strategy.get_open_orders = lambda: pytest.fail("SHADOW 不得查询/撤销挂单")
    strategy.cancel_order = lambda order: pytest.fail("SHADOW 不得撤单")

    assert strategy._cancel_open_orders_for_runtime() == 0
    assert any("SHADOW计划" in message for message in strategy.log.messages)


def test_runtime_mode_raises_before_install(monkeypatch):
    strategy = _load_strategy(monkeypatch)
    with pytest.raises(RuntimeError, match="尚未安装"):
        strategy._runtime_mode()
    with pytest.raises(RuntimeError, match="拒绝执行交易动作"):
        strategy._submit_target_amount("510001.XSHG", 100)
