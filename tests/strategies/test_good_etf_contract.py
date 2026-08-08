import ast
import builtins
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
        monkeypatch.delitem(
            sys.modules,
            "helpers.bullet_trade_jq_remote_helper",
            raising=False,
        )
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


@pytest.mark.parametrize(
    "failure_kind",
    ["import_error", "missing_dependency", "self_named_missing"],
)
def test_helper_internal_import_failure_never_downgrades_to_backtest(
    monkeypatch,
    failure_kind,
):
    original_import = builtins.__import__

    def broken_helper_import(name, *args, **kwargs):
        if name != "bullet_trade_jq_remote_helper":
            return original_import(name, *args, **kwargs)
        if failure_kind == "missing_dependency":
            raise ModuleNotFoundError(
                "No module named 'helper_internal_dependency'",
                name="helper_internal_dependency",
            )
        if failure_kind == "self_named_missing":
            raise ModuleNotFoundError(
                "helper body failed after execution started",
                name="bullet_trade_jq_remote_helper",
            )
        raise ImportError("helper body import failed")

    monkeypatch.setattr(builtins, "__import__", broken_helper_import)

    with pytest.raises(ImportError):
        _load_strategy(monkeypatch)


def test_backtest_without_helper_rejects_old_remote_portfolio(monkeypatch):
    class OldRemotePortfolio:
        _bt_remote_portfolio_marker = "bullet-trade-remote-jq-portfolio-v1"

    strategy = _load_strategy(monkeypatch)
    context = _Context("full_backtest", portfolio=OldRemotePortfolio())

    with pytest.raises(RuntimeError, match="旧远程portfolio"):
        strategy._install_runtime(context)


def test_backtest_without_top_level_helper_rejects_loaded_helper_alias(monkeypatch):
    from helpers import bullet_trade_jq_remote_helper as runtime_helper

    strategy = _load_strategy(monkeypatch)
    monkeypatch.setitem(
        sys.modules,
        "helpers.bullet_trade_jq_remote_helper",
        runtime_helper,
    )
    monkeypatch.setattr(runtime_helper, "_STRATEGY_RUNTIME_ACTIVE_MODE", None)
    cached_client = runtime_helper._ShortLivedClient(
        "127.0.0.1", 58620, "unit-test-token", retries=0
    )
    socket_calls = []
    portfolio_reads = []
    monkeypatch.setattr(
        runtime_helper.socket,
        "create_connection",
        lambda *args, **kwargs: socket_calls.append((args, kwargs)),
    )

    class ContextWithSideEffect:
        run_params = _RunParams("full_backtest")

        @property
        def portfolio(self):
            portfolio_reads.append("portfolio")
            try:
                cached_client.request("broker.place_order", {"amount": 100})
            except RuntimeError:
                pass
            return None

    with pytest.raises(RuntimeError, match="已加载的远程helper"):
        strategy._install_runtime(ContextWithSideEffect())

    assert portfolio_reads == []
    assert socket_calls == []


def test_backtest_uses_versioned_helper_before_strategy_reads_context(monkeypatch):
    calls = []
    helper = types.ModuleType("bullet_trade_jq_remote_helper")
    helper.STRATEGY_RUNTIME_API_VERSION = 1

    def install(namespace, **kwargs):
        calls.append((namespace, kwargs))
        return {
            "mode": "BACKTEST",
            "run_type": "full_backtest",
            "strategy_id": "good_etf",
            "enabled": False,
            "orders_enabled": True,
            "production_ready": False,
        }

    helper.install_strategy_runtime = install
    strategy = _load_strategy(monkeypatch, helper)

    class UnreadableRunParams:
        @property
        def type(self):
            pytest.fail("策略层不得在helper建立门禁前读取context")

    context = types.SimpleNamespace(run_params=UnreadableRunParams())
    state = strategy._install_runtime(context)

    assert state["mode"] == "BACKTEST"
    assert len(calls) == 1
    _, kwargs = calls[0]
    assert kwargs["context"] is context
    assert kwargs["mode"] == "BACKTEST"


def test_backtest_rejects_old_helper_api(monkeypatch):
    old_helper = types.ModuleType("bullet_trade_jq_remote_helper")
    old_helper.STRATEGY_RUNTIME_API_VERSION = 0
    strategy = _load_strategy(monkeypatch, old_helper)

    with pytest.raises(RuntimeError, match="API版本不匹配"):
        strategy._install_runtime(_Context("full_backtest"))


def test_backtest_rejects_huge_helper_api_with_stable_error(monkeypatch):
    malformed_helper = types.ModuleType("bullet_trade_jq_remote_helper")
    malformed_helper.STRATEGY_RUNTIME_API_VERSION = 10 ** 5000
    malformed_helper.install_strategy_runtime = lambda *args, **kwargs: pytest.fail(
        "API版本错误不得进入runtime"
    )
    strategy = _load_strategy(monkeypatch, malformed_helper)

    with pytest.raises(RuntimeError, match="actual=<invalid>"):
        strategy._install_runtime(_Context("full_backtest"))


def test_backtest_rejects_huge_expected_api_with_stable_error(monkeypatch):
    helper = types.ModuleType("bullet_trade_jq_remote_helper")
    helper.STRATEGY_RUNTIME_API_VERSION = 1
    helper.install_strategy_runtime = lambda *args, **kwargs: pytest.fail(
        "API版本错误不得进入runtime"
    )
    strategy = _load_strategy(monkeypatch, helper)
    strategy._EXPECTED_RUNTIME_API_VERSION = 10 ** 5000

    with pytest.raises(RuntimeError, match="expected=<invalid>"):
        strategy._install_runtime(_Context("full_backtest"))


def test_backtest_with_helper_rejects_remote_process_contamination(monkeypatch):
    from helpers import bullet_trade_jq_remote_helper as runtime_helper

    monkeypatch.setattr(runtime_helper, "_STRATEGY_RUNTIME_ACTIVE_MODE", None)
    monkeypatch.setattr(runtime_helper, "_STRATEGY_RUNTIME_PROCESS_SIGNATURE", None)
    monkeypatch.setattr(runtime_helper, "_STRATEGY_RUNTIME_CANONICAL_STATE", None)
    monkeypatch.setattr(runtime_helper, "_STRATEGY_RUNTIME_INFLIGHT_REQUESTS", 0)
    monkeypatch.setattr(runtime_helper, "_STRATEGY_RUNTIME_TRANSITION_OWNER", None)
    monkeypatch.setattr(runtime_helper, "_STRATEGY_RUNTIME_TRANSITION_NAMESPACE", None)
    monkeypatch.setattr(runtime_helper, "_STRATEGY_RUNTIME_TRANSITION_MODE", None)
    monkeypatch.setattr(runtime_helper, "_CLIENT", object())
    monkeypatch.setattr(runtime_helper, "_DATA_CLIENT", None)
    monkeypatch.setattr(runtime_helper, "_BROKER_CLIENT", None)

    strategy = _load_strategy(monkeypatch, runtime_helper)
    remote_portfolio = object.__new__(runtime_helper._RemoteJQPortfolio)
    context = _Context("full_backtest", portfolio=remote_portfolio)

    with pytest.raises(RuntimeError, match="BACKTEST检测到旧远程运行状态"):
        strategy._install_runtime(context)

    assert runtime_helper._STRATEGY_RUNTIME_ACTIVE_MODE == "FAILED"
    assert runtime_helper._CLIENT is None
    assert runtime_helper._DATA_CLIENT is None
    assert runtime_helper._BROKER_CLIENT is None


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
    helper.install_strategy_runtime = lambda namespace, **kwargs: pytest.fail(
        "S01 LIVE必须在安装helper runtime前失败关闭"
    )
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


def test_target_value_reduction_does_not_use_buy_side_limit_price(monkeypatch):
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
    strategy.LimitOrderStyle = lambda price: ("limit", price)
    strategy.order_target_value = (
        lambda security, value, style=None: orders.append((security, value, style))
    )
    oversized = types.SimpleNamespace(value=9_000.0, total_amount=1_000)
    portfolio = types.SimpleNamespace(
        positions={"510001.XSHG": oversized},
        available_cash=1_000.0,
        total_value=10_000.0,
        positions_value=9_000.0,
    )

    strategy.market_open(_Context("simple_backtest", portfolio))

    orders_by_security = {security: (value, style) for security, value, style in orders}
    assert orders_by_security["510001.XSHG"][0] < oversized.value
    assert orders_by_security["510001.XSHG"][1] is None
    assert orders_by_security["510002.XSHG"][1][0] == "limit"


def test_profile_example_is_versioned_and_deliberately_has_no_credentials():
    values = runpy.run_path(str(PROFILE_EXAMPLE_PATH))

    assert values["PROFILE_SCHEMA_VERSION"] == 1
    profile = values["PROFILES"]["good_etf-prod"]
    assert profile["strategy_id"] == "good_etf"
    assert profile["host"] == ""
    assert profile["token"] == ""
    assert profile["debug"] is False
