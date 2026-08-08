import ast
import importlib.util
import math
import runpy
import sys
import types
import uuid
from pathlib import Path

import pandas as pd
import pytest


ROOT = Path(__file__).resolve().parents[2]
STRATEGY_PATH = ROOT / "strategies" / "joinquant" / "good_etf.py"
PROFILE_EXAMPLE_PATH = ROOT / "jq_runtime" / "jq_runtime_config.example.py"


class _Log:
    def __init__(self):
        self.messages = []

    def _capture(self, level, message):
        self.messages.append((level, str(message)))

    def info(self, message):
        self._capture("INFO", message)

    def warn(self, message):
        self._capture("WARN", message)

    def error(self, message):
        self._capture("ERROR", message)

    def set_level(self, *args, **kwargs):
        return None


class _RunParams:
    def __init__(self, run_type):
        self.type = run_type


class _Context:
    def __init__(self, run_type, portfolio=None):
        self.run_params = _RunParams(run_type)
        self.portfolio = portfolio


def _fake_jqdata():
    module = types.ModuleType("jqdata")
    module.g = types.SimpleNamespace()
    module.log = _Log()
    module.__all__ = ["g", "log"]
    return module


def _load_strategy(monkeypatch, helper_module=None):
    jqdata = _fake_jqdata()
    monkeypatch.setitem(sys.modules, "jqdata", jqdata)
    if helper_module is None:
        monkeypatch.delitem(sys.modules, "bullet_trade_jq_remote_helper", raising=False)
    else:
        monkeypatch.setitem(sys.modules, "bullet_trade_jq_remote_helper", helper_module)

    module_name = "good_etf_contract_{}".format(uuid.uuid4().hex)
    spec = importlib.util.spec_from_file_location(module_name, STRATEGY_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _source_and_tree():
    source = STRATEGY_PATH.read_text(encoding="utf-8")
    return source, ast.parse(source, filename=str(STRATEGY_PATH))


def test_strategy_source_compiles_as_one_joinquant_file():
    source = STRATEGY_PATH.read_text(encoding="utf-8")
    compile(source, str(STRATEGY_PATH), "exec")


def test_strategy_deployment_contract_has_no_legacy_connection_assignments():
    source, tree = _source_and_tree()
    assigned_names = {
        target.id
        for node in tree.body
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name)
    }

    assert {"PROFILE", "MODE", "STRATEGY_ID"} <= assigned_names
    assert "PROFILE = 'good_etf-prod'" in source
    assert "MODE = 'BACKTEST'" in source
    assert "STRATEGY_ID = 'good_etf'" in source
    assert not assigned_names.intersection(
        {
            "DEBUG",
            "SEND_SIGNALS",
            "FEISHU_WEBHOOK_URL",
            "BT_REMOTE_HOST",
            "BT_REMOTE_PORT",
            "BT_REMOTE_TOKEN",
            "ACCOUNT_KEY",
            "SUB_ACCOUNT",
            "STRATEGY_NAME",
            "BT_TLS_CERT",
        }
    )


def test_strategy_calls_only_the_versioned_helper_entrypoint():
    _, tree = _source_and_tree()
    helper_calls = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "bt"
    }
    assert helper_calls == {"install_strategy_runtime"}


@pytest.mark.parametrize("run_type", ["simple_backtest", "full_backtest"])
def test_backtest_installs_without_helper_profile_or_network(monkeypatch, run_type):
    strategy = _load_strategy(monkeypatch)

    state = strategy._install_runtime(_Context(run_type))

    assert state["mode"] == "BACKTEST"
    assert state["run_type"] == run_type
    assert state["enabled"] is False
    assert state["orders_enabled"] is True
    assert state["production_ready"] is False
    assert strategy.g.bt_runtime == state


def test_backtest_mode_rejects_joinquant_sim_trade(monkeypatch):
    strategy = _load_strategy(monkeypatch)

    with pytest.raises(RuntimeError, match="MODE=BACKTEST"):
        strategy._install_runtime(_Context("sim_trade"))


def test_shadow_requires_uploaded_helper(monkeypatch):
    strategy = _load_strategy(monkeypatch)
    strategy.MODE = "SHADOW"

    with pytest.raises(RuntimeError, match="需要bullet_trade_jq_remote_helper"):
        strategy._install_runtime(_Context("sim_trade"))


def test_shadow_rejects_old_helper_api_before_profile_or_network(monkeypatch):
    old_helper = types.ModuleType("bullet_trade_jq_remote_helper")
    old_helper.STRATEGY_RUNTIME_API_VERSION = 0
    strategy = _load_strategy(monkeypatch, old_helper)
    strategy.MODE = "SHADOW"

    with pytest.raises(RuntimeError, match="API版本不匹配"):
        strategy._install_runtime(_Context("sim_trade"))


def test_shadow_rejects_boolean_helper_api_version(monkeypatch):
    malformed_helper = types.ModuleType("bullet_trade_jq_remote_helper")
    malformed_helper.STRATEGY_RUNTIME_API_VERSION = True
    strategy = _load_strategy(monkeypatch, malformed_helper)
    strategy.MODE = "SHADOW"

    with pytest.raises(RuntimeError, match="API版本不匹配"):
        strategy._install_runtime(_Context("sim_trade"))


def test_strategy_passes_only_deployment_identity_to_runtime(monkeypatch):
    calls = []
    helper = types.ModuleType("bullet_trade_jq_remote_helper")
    helper.STRATEGY_RUNTIME_API_VERSION = 1

    def install(namespace, **kwargs):
        calls.append((namespace, kwargs))
        return {
            "mode": kwargs["mode"],
            "run_type": "sim_trade",
            "strategy_id": kwargs["strategy_id"],
            "production_ready": False,
        }

    helper.install_strategy_runtime = install
    strategy = _load_strategy(monkeypatch, helper)
    strategy.MODE = "SHADOW"
    context = _Context("sim_trade")

    state = strategy._install_runtime(context)

    assert state["mode"] == "SHADOW"
    assert len(calls) == 1
    _, kwargs = calls[0]
    assert kwargs == {
        "context": context,
        "profile": "good_etf-prod",
        "mode": "SHADOW",
        "strategy_id": "good_etf",
        "expected_api_version": 1,
    }


def test_strategy_rejects_malformed_runtime_state(monkeypatch):
    helper = types.ModuleType("bullet_trade_jq_remote_helper")
    helper.STRATEGY_RUNTIME_API_VERSION = 1
    helper.install_strategy_runtime = lambda namespace, **kwargs: None
    strategy = _load_strategy(monkeypatch, helper)
    strategy.MODE = "SHADOW"

    with pytest.raises(RuntimeError, match="无效的运行时状态"):
        strategy._install_runtime(_Context("sim_trade"))


def test_good_etf_refuses_transitional_live_runtime(monkeypatch):
    helper = types.ModuleType("bullet_trade_jq_remote_helper")
    helper.STRATEGY_RUNTIME_API_VERSION = 1
    helper.install_strategy_runtime = lambda namespace, **kwargs: {
        "mode": "LIVE",
        "run_type": "sim_trade",
        "strategy_id": "good_etf",
        "production_ready": False,
    }
    strategy = _load_strategy(monkeypatch, helper)
    strategy.MODE = "LIVE"

    with pytest.raises(RuntimeError, match="禁止真实资金运行"):
        strategy._install_runtime(_Context("sim_trade"))


def test_target_values_use_total_nav_not_available_cash(monkeypatch):
    strategy = _load_strategy(monkeypatch)
    orders = []
    current = {
        "510001.XSHG": types.SimpleNamespace(last_price=9.0, high_limit=11.0, paused=False),
        "510002.XSHG": types.SimpleNamespace(last_price=9.5, high_limit=11.0, paused=False),
    }
    strategy.g.bt_runtime = {"mode": "BACKTEST"}
    strategy.g.fund_list = pd.DataFrame(
        {"unit_net_value": [10.0, 10.0]},
        index=["510001.XSHG", "510002.XSHG"],
    )
    strategy.get_current_data = lambda: current
    strategy.get_open_orders = lambda: {}
    strategy.cancel_order = lambda order: pytest.fail("没有挂单时不得撤单")
    strategy.LimitOrderStyle = lambda price: ("limit", price)
    strategy.order_target_value = lambda security, value, style=None: orders.append(
        (security, value, style)
    )
    portfolio = types.SimpleNamespace(
        positions={},
        available_cash=2_000.0,
        total_value=10_000.0,
        positions_value=8_000.0,
    )

    strategy.market_open(_Context("simple_backtest", portfolio))

    assert len(orders) == 2
    expected_investable = 10_000.0 * strategy.DEPLOY_RATIO
    # 折价绝对值约为10%与5%，目标权重为2/3与1/3。
    assert math.isclose(orders[0][1], expected_investable * 2 / 3, rel_tol=1e-9)
    assert math.isclose(orders[1][1], expected_investable * 1 / 3, rel_tol=1e-9)
    assert math.isclose(sum(order[1] for order in orders), expected_investable, rel_tol=1e-9)
    assert sum(order[1] for order in orders) > portfolio.available_cash


def test_profile_example_is_versioned_and_deliberately_has_no_credentials():
    values = runpy.run_path(str(PROFILE_EXAMPLE_PATH))

    assert values["PROFILE_SCHEMA_VERSION"] == 1
    profile = values["PROFILES"]["good_etf-prod"]
    assert profile["strategy_id"] == "good_etf"
    assert profile["host"] == ""
    assert profile["token"] == ""
    assert profile["debug"] is False
