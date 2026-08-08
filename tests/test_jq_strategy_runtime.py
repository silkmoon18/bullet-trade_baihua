import builtins
import functools
import os
import subprocess
import sys
import textwrap
import threading
import types

import pytest

from helpers import bullet_trade_jq_remote_helper as helper


@pytest.fixture(autouse=True)
def _reset_runtime_process_gate(monkeypatch):
    monkeypatch.setattr(helper, "_STRATEGY_RUNTIME_ACTIVE_MODE", None)
    monkeypatch.setattr(helper, "_STRATEGY_RUNTIME_PROCESS_SIGNATURE", None)
    monkeypatch.setattr(helper, "_STRATEGY_RUNTIME_CANONICAL_STATE", None)
    monkeypatch.setattr(helper, "_STRATEGY_RUNTIME_INFLIGHT_REQUESTS", 0)
    monkeypatch.setattr(helper, "_STRATEGY_RUNTIME_TRANSITION_OWNER", None)
    monkeypatch.setattr(helper, "_STRATEGY_RUNTIME_TRANSITION_NAMESPACE", None)
    monkeypatch.setattr(helper, "_CLIENT", None)
    monkeypatch.setattr(helper, "_DATA_CLIENT", None)
    monkeypatch.setattr(helper, "_BROKER_CLIENT", None)


class _RunParams:
    def __init__(self, run_type):
        self.type = run_type


class _Context:
    def __init__(self, run_type):
        self.run_params = _RunParams(run_type)


class _MutationProbeClient:
    def __init__(self):
        self.requests = []

    def request(self, action, payload, timeout=None):
        self.requests.append((action, payload, timeout))
        raise AssertionError("只读门禁后不得触达远程client")


def _valid_profile(**overrides):
    value = {
        "strategy_id": "good_etf",
        "host": "127.0.0.1",
        "token": "top-secret-token",
    }
    value.update(overrides)
    return value


def _profile_module(monkeypatch, name="test_jq_runtime_config", *, version=1, profiles=None):
    module = types.ModuleType(name)
    module.PROFILE_SCHEMA_VERSION = version
    module.PROFILES = profiles if profiles is not None else {"good_etf-prod": _valid_profile()}
    monkeypatch.setitem(sys.modules, name, module)
    return name


def _install(namespace, context, profile_module, *, mode="SHADOW", **kwargs):
    return helper.install_strategy_runtime(
        namespace,
        context=context,
        profile="good_etf-prod",
        mode=mode,
        strategy_id="good_etf",
        profile_module=profile_module,
        **kwargs,
    )


def test_runtime_contract_versions_are_exported():
    assert helper.STRATEGY_RUNTIME_API_VERSION == 1
    assert helper.PROFILE_SCHEMA_VERSION == 1
    assert "install_strategy_runtime" in helper.__all__


@pytest.mark.parametrize("run_type", ["simple_backtest", "full_backtest"])
def test_backtest_returns_before_profile_import_or_remote_install(monkeypatch, run_type):
    def forbidden(*args, **kwargs):
        raise AssertionError("BACKTEST不得触达远程安装")

    monkeypatch.setattr(helper, "configure", forbidden)
    monkeypatch.setattr(helper, "install_jq_compat", forbidden)
    monkeypatch.setattr(helper, "_load_runtime_profile", forbidden)

    state = _install(
        {},
        _Context(run_type),
        "module_that_must_not_be_imported",
        mode="BACKTEST",
    )

    assert state == {
        "api_version": 1,
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


@pytest.mark.parametrize(
    ("mode", "run_type"),
    [
        ("BACKTEST", "sim_trade"),
        ("SHADOW", "full_backtest"),
        ("LIVE", "simple_backtest"),
        ("LIVE", ""),
    ],
)
def test_runtime_rejects_mode_and_joinquant_run_type_mismatch(mode, run_type):
    with pytest.raises(RuntimeError, match="仅允许"):
        _install({}, _Context(run_type), "unused_profile", mode=mode)


def test_runtime_rejects_unknown_mode_before_profile_import(monkeypatch):
    monkeypatch.setattr(
        helper,
        "_load_runtime_profile",
        lambda *args, **kwargs: pytest.fail("非法模式不得导入profile"),
    )
    namespace = {"order": lambda *args, **kwargs: "native-order"}
    with pytest.raises(RuntimeError, match="BACKTEST、SHADOW 或 LIVE"):
        _install(namespace, _Context("sim_trade"), "unused_profile", mode="paper")
    assert helper._STRATEGY_RUNTIME_ACTIVE_MODE == "FAILED"
    with pytest.raises(RuntimeError, match="FAILED模式禁止交易变更"):
        namespace["order"]("000001.XSHE", 100)


def test_runtime_requires_exact_globals_dict_namespace():
    class OverriddenNamespace(dict):
        def __getitem__(self, key):
            if key == "order":
                return lambda *args, **kwargs: "forged-native-order"
            return super().__getitem__(key)

    namespace = OverriddenNamespace({"order": lambda *args, **kwargs: "native"})
    with pytest.raises(RuntimeError, match=r"globals\(\)字典"):
        helper.install_strategy_runtime(
            namespace,
            context=_Context("sim_trade"),
            profile="good_etf-prod",
            mode="SHADOW",
            strategy_id="good_etf",
            profile_module="profile_must_not_load",
        )


def test_runtime_rejects_api_version_mismatch_before_profile_import(monkeypatch):
    monkeypatch.setattr(
        helper,
        "_load_runtime_profile",
        lambda *args, **kwargs: pytest.fail("版本不匹配不得导入profile"),
    )
    with pytest.raises(RuntimeError, match="API版本不匹配"):
        _install(
            {},
            _Context("sim_trade"),
            "unused_profile",
            expected_api_version=2,
        )


def test_shadow_validates_profile_clears_existing_clients_and_never_installs_compat(monkeypatch):
    module_name = _profile_module(monkeypatch)
    monkeypatch.setattr(
        helper,
        "configure",
        lambda **kwargs: pytest.fail("SHADOW不得configure或建socket"),
    )
    monkeypatch.setattr(
        helper,
        "install_jq_compat",
        lambda *args, **kwargs: pytest.fail("SHADOW不得调用install_jq_compat"),
    )
    monkeypatch.setattr(helper, "_CLIENT", object())
    monkeypatch.setattr(helper, "_DATA_CLIENT", object())
    monkeypatch.setattr(helper, "_BROKER_CLIENT", object())

    def query():
        return "query-ok"

    namespace = {
        "order": lambda *args: "unsafe",
        "order_batch": lambda *args: "unsafe",
        "cancel_all_open_orders": lambda: "unsafe",
        "get_orders": query,
    }

    state = _install(namespace, _Context("sim_trade"), module_name)

    assert state["mode"] == "SHADOW"
    assert state["orders_enabled"] is False
    assert state["production_ready"] is False
    assert state["reason"] == "shadow_read_only"
    assert namespace["get_orders"] is query
    for name in (
        "order",
        "order_value",
        "order_percent",
        "order_target",
        "order_target_value",
        "order_target_percent",
        "order_batch",
        "cancel_order",
        "cancel_all_open_orders",
    ):
        with pytest.raises(RuntimeError, match="SHADOW模式禁止交易变更"):
            namespace[name]()

    # helper模块的便捷下单必须由进程级门禁阻断，且旧客户端引用被清除。
    with pytest.raises(RuntimeError, match="SHADOW模式禁止交易变更"):
        helper.order("000001.XSHE", 100)
    assert helper._CLIENT is None
    assert helper._DATA_CLIENT is None
    assert helper._BROKER_CLIENT is None
    with pytest.raises(RuntimeError, match="尚未调用 configure"):
        helper.get_broker_client()


def test_shadow_clean_install_blocks_late_configure_and_direct_mutations(monkeypatch):
    module_name = _profile_module(monkeypatch)
    monkeypatch.setattr(helper, "_BROKER_CLIENT", None)
    namespace = {}

    _install(namespace, _Context("sim_trade"), module_name)

    with pytest.raises(RuntimeError, match="SHADOW模式禁止交易变更: configure"):
        helper.configure(host="127.0.0.1", token="unit-test-token")
    for mutation, args in (
        (helper.order, ("000001.XSHE", 100)),
        (helper.order_value, ("000001.XSHE", 1000)),
        (helper.order_percent, ("000001.XSHE", 0.1)),
        (helper.order_target, ("000001.XSHE", 100)),
        (helper.order_target_value, ("000001.XSHE", 1000)),
        (helper.order_target_percent, ("000001.XSHE", 0.1)),
        (helper.cancel_order, ("order-1",)),
    ):
        with pytest.raises(RuntimeError, match="SHADOW模式禁止交易变更"):
            mutation(*args)


def test_shadow_idempotent_reinstall_repairs_namespace_and_client_guards(monkeypatch):
    module_name = _profile_module(monkeypatch)
    namespace = {"order": lambda *args: "unsafe"}
    context = _Context("sim_trade")
    first = _install(namespace, context, module_name)

    namespace["order"] = lambda *args: "rebound-unsafe"
    helper._BROKER_CLIENT = object()
    second = _install(namespace, context, module_name)

    assert first == second
    assert helper._BROKER_CLIENT is None
    with pytest.raises(RuntimeError, match="SHADOW模式禁止交易变更"):
        namespace["order"]("000001.XSHE", 100)


@pytest.mark.parametrize("mode", ["SHADOW", "LIVE"])
def test_remote_modes_quarantine_legacy_compat_originals_and_aliases(monkeypatch, mode):
    module_name = _profile_module(monkeypatch)
    context = _Context("sim_trade")
    client = _MutationProbeClient()
    raw_broker = helper.RemoteBrokerClient(client)
    cached_client = helper._ShortLivedClient("127.0.0.1", 58620, "unit-test-token")
    monkeypatch.setattr(helper, "_BROKER_CLIENT", raw_broker)
    socket_calls = []
    monkeypatch.setattr(
        helper.socket,
        "create_connection",
        lambda *args, **kwargs: socket_calls.append((args, kwargs)),
    )

    def native_mutation(*args, **kwargs):
        raise AssertionError("远程context拒绝后不得恢复原生交易入口")

    def cached_compat_order(*args, **kwargs):
        return raw_broker.order("000001.XSHE", 100)

    originals = {
        "order": native_mutation,
        "order_target": native_mutation,
        "cancel_order": native_mutation,
    }
    legacy_state = {"installed": True, "originals": originals}
    namespace = {
        helper._JQ_COMPAT_STATE_KEY: legacy_state,
        "order": cached_compat_order,
        "order_target": lambda *args, **kwargs: "remote-order-target",
        "cancel_order": lambda *args, **kwargs: "remote-cancel",
        "trade_alias": native_mutation,
        "compat_alias": cached_compat_order,
    }

    with pytest.raises(RuntimeError, match="旧聚宽兼容层"):
        _install(namespace, context, module_name, mode=mode)
    assert helper._STRATEGY_RUNTIME_ACTIVE_MODE == "FAILED"
    assert helper._BROKER_CLIENT is None
    assert helper._JQ_COMPAT_STATE_KEY not in namespace
    assert originals == {}
    assert legacy_state == {"quarantined": True}
    for name in ("order", "order_target", "cancel_order", "trade_alias", "compat_alias"):
        with pytest.raises(RuntimeError, match="FAILED模式禁止交易变更"):
            namespace[name]()
    with pytest.raises(RuntimeError, match="FAILED模式禁止交易变更"):
        raw_broker.order("000001.XSHE", 100)
    with pytest.raises(RuntimeError, match="FAILED模式禁止交易变更"):
        cached_compat_order()
    with pytest.raises(RuntimeError, match="FAILED模式禁止远程访问"):
        cached_client.request("broker.account", {})
    assert client.requests == []
    assert socket_calls == []


@pytest.mark.parametrize("mode", ["SHADOW", "LIVE"])
def test_remote_modes_reject_inherited_remote_context_without_compat_state(monkeypatch, mode):
    module_name = _profile_module(monkeypatch)
    context = _Context("sim_trade")
    context.portfolio = object.__new__(helper._RemoteJQPortfolio)
    namespace = {"order": lambda *args, **kwargs: "native-order"}

    with pytest.raises(RuntimeError, match="不能复用.*远程兼容层"):
        _install(namespace, context, module_name, mode=mode)

    assert helper._STRATEGY_RUNTIME_ACTIVE_MODE == "FAILED"
    with pytest.raises(RuntimeError, match="FAILED模式禁止交易变更"):
        namespace["order"]("000001.XSHE", 100)


def test_shadow_guards_direct_namespace_alias_of_native_order(monkeypatch):
    module_name = _profile_module(monkeypatch)

    def native_order(*args, **kwargs):
        raise AssertionError("SHADOW不得调用原生order或其直接别名")

    @functools.wraps(native_order)
    def wrapped_alias(*args, **kwargs):
        return native_order(*args, **kwargs)

    def closure_alias(*args, **kwargs):
        return native_order(*args, **kwargs)

    namespace = {
        "order": native_order,
        "trade_alias": native_order,
        "partial_alias": functools.partial(native_order),
        "wrapped_alias": wrapped_alias,
        "closure_alias": closure_alias,
    }
    state = _install(namespace, _Context("sim_trade"), module_name)

    assert {
        "trade_alias",
        "partial_alias",
        "wrapped_alias",
        "closure_alias",
    }.issubset(state["blocked_mutations"])
    for name in (
        "order",
        "trade_alias",
        "partial_alias",
        "wrapped_alias",
        "closure_alias",
    ):
        with pytest.raises(RuntimeError, match="SHADOW模式禁止交易变更"):
            namespace[name]("000001.XSHE", 100)


def test_shadow_guards_imported_order_alias_without_canonical_binding(monkeypatch):
    module_name = _profile_module(monkeypatch)

    def imported_jq_order(*args, **kwargs):
        raise AssertionError("仅保留import alias时也不得执行原生order")

    imported_jq_order.__name__ = "order"
    namespace = {
        "trade": imported_jq_order,
        "partial_trade": functools.partial(imported_jq_order),
    }
    state = _install(namespace, _Context("sim_trade"), module_name)

    assert {"trade", "partial_trade"}.issubset(state["blocked_mutations"])
    for name in ("trade", "partial_trade"):
        with pytest.raises(RuntimeError, match="SHADOW模式禁止交易变更"):
            namespace[name]("000001.XSHE", 100)


def test_unrelated_callable_getattr_cannot_break_fail_closed_guards(monkeypatch):
    module_name = _profile_module(monkeypatch)

    class PoisonCallable:
        def __call__(self, *args, **kwargs):
            return "unrelated"

        def __getattr__(self, name):
            if name == "_bt_runtime_mutation_guard":
                raise RuntimeError("POISON_GUARD_MARKER_LOOKUP")
            raise AttributeError(name)

    class PoisonPartial(functools.partial):
        def __getattribute__(self, name):
            if name in {"func", "args", "keywords"}:
                raise RuntimeError("POISON_PARTIAL_METADATA")
            return super().__getattribute__(name)

    def native_order(*args, **kwargs):
        raise AssertionError("无关callable不得阻止原生order被guard")

    poison = PoisonCallable()
    poison_partial = PoisonPartial(native_order)
    namespace = {
        "order": native_order,
        "unrelated_callable": poison,
        "unrelated_partial": poison_partial,
    }
    state = _install(namespace, _Context("sim_trade"), module_name)

    assert state["mode"] == "SHADOW"
    assert namespace["unrelated_callable"] is poison
    assert "unrelated_partial" in state["blocked_mutations"]
    with pytest.raises(RuntimeError, match="SHADOW模式禁止交易变更"):
        namespace["unrelated_partial"]("000001.XSHE", 100)
    with pytest.raises(RuntimeError, match="SHADOW模式禁止交易变更"):
        namespace["order"]("000001.XSHE", 100)


def test_poisoned_legacy_dict_and_str_key_cannot_break_failed_cleanup(monkeypatch):
    class PoisonDict(dict):
        def get(self, *args, **kwargs):
            raise RuntimeError("POISON_DICT_GET")

        def items(self, *args, **kwargs):
            raise RuntimeError("POISON_DICT_ITEMS")

        def clear(self, *args, **kwargs):
            raise RuntimeError("POISON_DICT_CLEAR")

        def pop(self, *args, **kwargs):
            raise RuntimeError("POISON_DICT_POP")

        def __setitem__(self, key, value):
            raise RuntimeError("POISON_DICT_SETITEM")

    class PoisonKey(str):
        def startswith(self, *args, **kwargs):
            raise RuntimeError("POISON_STR_STARTSWITH")

    class PoisonCallable:
        def __call__(self, *args, **kwargs):
            return "unrelated"

        def __getattr__(self, name):
            raise RuntimeError("POISON_CALLABLE_GETATTR")

    def native_order(*args, **kwargs):
        raise AssertionError("FAILED清理后不得执行原生order")

    originals = PoisonDict({"order": native_order})
    legacy_state = PoisonDict({"installed": True, "originals": originals})
    namespace = {
        helper._JQ_COMPAT_STATE_KEY: legacy_state,
        "order": native_order,
        PoisonKey("unrelated_key"): PoisonCallable(),
    }

    with pytest.raises(RuntimeError, match="旧聚宽兼容层"):
        _install(
            namespace,
            _Context("sim_trade"),
            "profile_must_not_load_after_legacy_detection",
            mode="SHADOW",
        )

    assert helper._STRATEGY_RUNTIME_ACTIVE_MODE == "FAILED"
    assert not dict.__contains__(namespace, helper._JQ_COMPAT_STATE_KEY)
    assert dict(originals) == {}
    assert dict(legacy_state) == {"quarantined": True}
    with pytest.raises(RuntimeError, match="FAILED模式禁止交易变更"):
        dict.__getitem__(namespace, "order")("000001.XSHE", 100)


def test_shadow_blocks_mutations_on_previously_cached_raw_broker(monkeypatch):
    module_name = _profile_module(monkeypatch)
    client = _MutationProbeClient()
    raw_broker = helper.RemoteBrokerClient(client)
    monkeypatch.setattr(helper, "_BROKER_CLIENT", raw_broker)

    _install({}, _Context("sim_trade"), module_name)

    for mutation, args in (
        (raw_broker.order, ("000001.XSHE", 100)),
        (raw_broker.order_value, ("000001.XSHE", 1000)),
        (raw_broker.order_percent, ("000001.XSHE", 0.1)),
        (raw_broker.order_target, ("000001.XSHE", 100)),
        (raw_broker.order_target_value, ("000001.XSHE", 1000)),
        (raw_broker.order_target_percent, ("000001.XSHE", 0.1)),
        (raw_broker.cancel_order, ("order-1",)),
    ):
        with pytest.raises(RuntimeError, match="SHADOW模式禁止交易变更"):
            mutation(*args)
    assert client.requests == []


@pytest.mark.parametrize(
    ("mode", "run_type", "profile_module"),
    [
        ("BACKTEST", "full_backtest", "unused_profile"),
        ("SHADOW", "sim_trade", "profile"),
        ("LIVE", "sim_trade", "profile"),
    ],
)
def test_runtime_modes_block_previously_cached_short_lived_client(
    monkeypatch, mode, run_type, profile_module
):
    cached_client = helper._ShortLivedClient("127.0.0.1", 58620, "unit-test-token")
    if profile_module == "profile":
        profile_module = _profile_module(monkeypatch)
    _install({}, _Context(run_type), profile_module, mode=mode)
    monkeypatch.setattr(
        helper.socket,
        "create_connection",
        lambda *args, **kwargs: pytest.fail("runtime门禁后不得创建socket"),
    )

    with pytest.raises(RuntimeError, match="禁止远程访问"):
        cached_client.request("broker.account", {})


def test_live_validates_profile_without_remote_takeover_and_installs_local_guards(monkeypatch):
    module_name = _profile_module(monkeypatch)
    real_configure = helper.configure
    monkeypatch.setattr(
        helper,
        "configure",
        lambda **kwargs: pytest.fail("S01 LIVE不得configure或建socket"),
    )
    monkeypatch.setattr(
        helper,
        "install_jq_compat",
        lambda *args, **kwargs: pytest.fail("S01 LIVE不得安装兼容层"),
    )

    def native_order(*args):
        return "native"

    namespace = {"order": native_order}
    native_portfolio = object()
    context = _Context("sim_trade")
    context.portfolio = native_portfolio

    state = _install(namespace, context, module_name, mode="LIVE")

    assert state["mode"] == "LIVE"
    assert state["enabled"] is False
    assert state["orders_enabled"] is False
    assert state["production_ready"] is False
    assert state["reason"] == "live_blocked_until_strategy_ledger"
    assert state["mirror_jq_orders"] is False
    assert "token" not in state
    assert "host" not in state
    assert "order" in state["blocked_mutations"]
    assert namespace["order"] is not native_order
    with pytest.raises(RuntimeError, match="LIVE_BLOCKED模式禁止交易变更"):
        namespace["order"]("000001.XSHE", 100)
    assert context.portfolio is native_portfolio
    assert helper._CLIENT is None
    assert helper._DATA_CLIENT is None
    assert helper._BROKER_CLIENT is None
    with pytest.raises(RuntimeError, match="LIVE_BLOCKED模式禁止交易变更"):
        real_configure(host="127.0.0.1", token="unit-test-token")


def test_profile_loader_normalises_explicit_optional_values(monkeypatch):
    profile = _valid_profile(
        port=60000,
        account_key="main",
        sub_account_id="good_etf@main",
        tls_cert="server.pem",
        retries=4,
        retry_interval=1,
        rpc_timeout=21,
        place_order_timeout_margin=7,
        default_wait_timeout=9,
        debug=False,
    )
    module_name = _profile_module(monkeypatch, profiles={"good_etf-prod": profile})

    loaded = helper._load_runtime_profile(module_name, "good_etf-prod")

    assert loaded["port"] == 60000
    assert loaded["account_key"] == "main"
    assert loaded["sub_account_id"] == "good_etf@main"
    assert loaded["tls_cert"] == "server.pem"
    assert loaded["retries"] == 4
    assert loaded["retry_interval"] == 1.0
    assert loaded["rpc_timeout"] == 21.0
    assert loaded["place_order_timeout_margin"] == 7.0
    assert loaded["default_wait_timeout"] == 9.0
    assert loaded["debug"] is False


@pytest.mark.parametrize("mode", ["SHADOW", "LIVE"])
def test_remote_modes_are_idempotent(monkeypatch, mode):
    module_name = _profile_module(monkeypatch)
    monkeypatch.setattr(
        helper,
        "install_jq_compat",
        lambda *args, **kwargs: pytest.fail("S01远程模式不得安装旧兼容层"),
    )
    namespace = {"order": lambda *args, **kwargs: "unsafe"}
    context = _Context("sim_trade")

    first = _install(namespace, context, module_name, mode=mode)
    namespace["order"] = lambda *args, **kwargs: "rebound-unsafe"
    monkeypatch.setattr(
        helper,
        "_load_runtime_profile",
        lambda *args, **kwargs: pytest.fail("幂等安装不得重新加载profile"),
    )
    second = _install(namespace, context, module_name, mode=mode)

    assert first == second
    expected_active = "SHADOW" if mode == "SHADOW" else "LIVE_BLOCKED"
    assert helper._STRATEGY_RUNTIME_ACTIVE_MODE == expected_active
    with pytest.raises(RuntimeError, match="{}模式禁止交易变更".format(expected_active)):
        namespace["order"]("000001.XSHE", 100)


@pytest.mark.parametrize(
    ("mode", "expected_active"),
    [("SHADOW", "SHADOW"), ("LIVE", "LIVE_BLOCKED")],
)
def test_remote_gate_is_armed_before_profile_import_side_effects(
    monkeypatch,
    mode,
    expected_active,
):
    module_name = "side_effect_jq_runtime_config_{}".format(mode.lower())
    module = types.ModuleType(module_name)
    module.PROFILE_SCHEMA_VERSION = 1
    module.PROFILES = {"good_etf-prod": _valid_profile()}
    cached_client = helper._ShortLivedClient("127.0.0.1", 58620, "unit-test-token")
    probe_client = _MutationProbeClient()
    raw_broker = helper.RemoteBrokerClient(probe_client)
    monkeypatch.setattr(helper, "_CLIENT", cached_client)
    monkeypatch.setattr(helper, "_DATA_CLIENT", object())
    monkeypatch.setattr(helper, "_BROKER_CLIENT", raw_broker)
    namespace = {"order": lambda *args, **kwargs: "native-mutation"}
    socket_calls = []

    def forbidden_socket(*args, **kwargs):
        socket_calls.append((args, kwargs))
        raise AssertionError("profile导入副作用不得触达socket")

    monkeypatch.setattr(helper.socket, "create_connection", forbidden_socket)
    original_import = builtins.__import__
    observations = {}

    def import_with_side_effect(name, *args, **kwargs):
        if name != module_name:
            return original_import(name, *args, **kwargs)
        observations["active"] = helper._STRATEGY_RUNTIME_ACTIVE_MODE
        observations["clients"] = (
            helper._CLIENT,
            helper._DATA_CLIENT,
            helper._BROKER_CLIENT,
        )
        with pytest.raises(RuntimeError, match="{}模式禁止交易变更".format(expected_active)):
            helper.configure(host="127.0.0.1", token="unit-test-token")
        with pytest.raises(RuntimeError, match="{}模式禁止远程访问".format(expected_active)):
            cached_client.request("broker.account", {})
        with pytest.raises(RuntimeError, match="{}模式禁止交易变更".format(expected_active)):
            raw_broker.order("000001.XSHE", 100)
        with pytest.raises(RuntimeError, match="{}模式禁止交易变更".format(expected_active)):
            namespace["order"]("000001.XSHE", 100)
        return module

    monkeypatch.setattr(builtins, "__import__", import_with_side_effect)

    state = _install(namespace, _Context("sim_trade"), module_name, mode=mode)

    assert state["mode"] == mode
    assert observations == {"active": expected_active, "clients": (None, None, None)}
    assert probe_client.requests == []
    assert socket_calls == []


@pytest.mark.parametrize("mode", ["SHADOW", "LIVE"])
def test_remote_profile_failure_keeps_failed_gate_and_invalidates_cached_clients(
    monkeypatch,
    mode,
):
    cached_client = helper._ShortLivedClient("127.0.0.1", 58620, "unit-test-token")
    probe_client = _MutationProbeClient()
    raw_broker = helper.RemoteBrokerClient(probe_client)
    monkeypatch.setattr(helper, "_CLIENT", cached_client)
    monkeypatch.setattr(helper, "_DATA_CLIENT", object())
    monkeypatch.setattr(helper, "_BROKER_CLIENT", raw_broker)
    namespace = {
        "order": lambda *args, **kwargs: "native-order",
        "order_target": lambda *args, **kwargs: "native-order-target",
        "cancel_order": lambda *args, **kwargs: "native-cancel",
    }
    socket_calls = []
    monkeypatch.setattr(
        helper.socket,
        "create_connection",
        lambda *args, **kwargs: socket_calls.append((args, kwargs)),
    )

    with pytest.raises(RuntimeError, match="无法加载运行配置模块"):
        _install(
            namespace,
            _Context("sim_trade"),
            "definitely_missing_profile_with_old_clients_{}".format(mode.lower()),
            mode=mode,
        )

    assert helper._STRATEGY_RUNTIME_ACTIVE_MODE == "FAILED"
    assert helper._CLIENT is None
    assert helper._DATA_CLIENT is None
    assert helper._BROKER_CLIENT is None
    for name in ("order", "order_target", "cancel_order"):
        with pytest.raises(RuntimeError, match="FAILED模式禁止交易变更"):
            namespace[name]()
    with pytest.raises(RuntimeError, match="FAILED模式禁止交易变更"):
        raw_broker.order("000001.XSHE", 100)
    with pytest.raises(RuntimeError, match="FAILED模式禁止远程访问"):
        cached_client.request("broker.account", {})
    assert probe_client.requests == []
    assert socket_calls == []


def test_backtest_after_failed_remote_attempt_requires_clean_process(monkeypatch):
    namespace = {"order": lambda *args, **kwargs: "native-order"}

    with pytest.raises(RuntimeError, match="无法加载运行配置模块"):
        _install(
            namespace,
            _Context("sim_trade"),
            "definitely_missing_profile_before_backtest",
            mode="SHADOW",
        )
    with pytest.raises(RuntimeError, match="必须使用干净运行进程重启"):
        _install(
            namespace,
            _Context("full_backtest"),
            "unused_profile",
            mode="BACKTEST",
        )

    assert helper._STRATEGY_RUNTIME_ACTIVE_MODE == "FAILED"
    with pytest.raises(RuntimeError, match="FAILED模式禁止交易变更"):
        namespace["order"]("000001.XSHE", 100)


def test_failed_remote_install_cannot_retry_in_same_process(monkeypatch):
    namespace = {"order": lambda *args, **kwargs: "native-order"}
    context = _Context("sim_trade")

    with pytest.raises(RuntimeError, match="无法加载运行配置模块"):
        _install(namespace, context, "missing_profile_before_retry", mode="SHADOW")
    module_name = _profile_module(monkeypatch)
    monkeypatch.setattr(
        helper,
        "_load_runtime_profile",
        lambda *args, **kwargs: pytest.fail("FAILED进程不得再次加载profile"),
    )

    with pytest.raises(RuntimeError, match="必须使用干净运行进程重启"):
        _install(namespace, context, module_name, mode="SHADOW")

    assert helper._STRATEGY_RUNTIME_ACTIVE_MODE == "FAILED"
    assert helper._STRATEGY_RUNTIME_STATE_KEY not in namespace
    with pytest.raises(RuntimeError, match="FAILED模式禁止交易变更"):
        namespace["order"]("000001.XSHE", 100)


@pytest.mark.parametrize("installed", [False, True])
def test_backtest_rejects_any_legacy_compat_trace(monkeypatch, installed):
    def native_order(*args, **kwargs):
        raise AssertionError("污染进程不得返回orders_enabled=True")

    originals = {"order": native_order}
    legacy_state = {"installed": installed, "originals": originals}
    namespace = {
        helper._JQ_COMPAT_STATE_KEY: legacy_state,
        "order": lambda *args, **kwargs: "old-wrapper",
        "trade_alias": native_order,
    }

    with pytest.raises(RuntimeError, match="BACKTEST检测到旧远程运行状态"):
        _install(
            namespace,
            _Context("full_backtest"),
            "unused_profile",
            mode="BACKTEST",
        )

    assert helper._STRATEGY_RUNTIME_ACTIVE_MODE == "FAILED"
    assert helper._JQ_COMPAT_STATE_KEY not in namespace
    assert originals == {}
    assert legacy_state == {"quarantined": True}
    for name in ("order", "trade_alias"):
        with pytest.raises(RuntimeError, match="FAILED模式禁止交易变更"):
            namespace[name]()


def test_idempotent_install_rejects_tampered_public_state(monkeypatch):
    module_name = _profile_module(monkeypatch)
    namespace = {}
    context = _Context("sim_trade")
    _install(namespace, context, module_name)
    cached_state = namespace[helper._STRATEGY_RUNTIME_STATE_KEY]["state"]
    cached_state.update(
        {
            "mode": "LIVE",
            "orders_enabled": True,
            "production_ready": True,
        }
    )

    with pytest.raises(RuntimeError, match="缓存.*不一致"):
        _install(namespace, context, module_name)

    assert helper._STRATEGY_RUNTIME_ACTIVE_MODE == "FAILED"
    assert helper._STRATEGY_RUNTIME_STATE_KEY not in namespace


def test_namespace_state_never_restores_missing_process_authority(monkeypatch):
    module_name = _profile_module(monkeypatch)
    namespace = {}
    context = _Context("sim_trade")
    _install(namespace, context, module_name)
    monkeypatch.setattr(helper, "_STRATEGY_RUNTIME_PROCESS_SIGNATURE", None)

    with pytest.raises(RuntimeError, match="无进程权威状态"):
        _install(namespace, context, module_name)

    assert helper._STRATEGY_RUNTIME_ACTIVE_MODE == "FAILED"


def test_process_authority_rejects_missing_namespace_state(monkeypatch):
    module_name = _profile_module(monkeypatch)
    namespace = {}
    context = _Context("sim_trade")
    _install(namespace, context, module_name)
    namespace.pop(helper._STRATEGY_RUNTIME_STATE_KEY)

    with pytest.raises(RuntimeError, match="namespace缓存缺失"):
        _install(namespace, context, module_name)

    assert helper._STRATEGY_RUNTIME_ACTIVE_MODE == "FAILED"


def test_runtime_blocks_late_install_jq_compat_before_state_write(monkeypatch):
    module_name = _profile_module(monkeypatch)
    namespace = {}
    context = _Context("sim_trade")
    _install(namespace, context, module_name)

    with pytest.raises(RuntimeError, match="SHADOW模式禁止交易变更"):
        helper.install_jq_compat(
            namespace,
            context=context,
            host="127.0.0.1",
            token="unit-test-token",
        )

    assert helper._JQ_COMPAT_STATE_KEY not in namespace


def test_reinstall_with_different_contract_fails_closed(monkeypatch):
    module_name = _profile_module(monkeypatch)
    namespace = {}
    context = _Context("sim_trade")
    _install(namespace, context, module_name)

    with pytest.raises(RuntimeError, match="不一致|不同配置"):
        helper.install_strategy_runtime(
            namespace,
            context=context,
            profile="other-profile",
            mode="SHADOW",
            strategy_id="good_etf",
            profile_module=module_name,
        )


def test_concurrent_runtime_install_cannot_enter_second_contract(monkeypatch):
    profile_entered = threading.Event()
    release_profile = threading.Event()
    loader_calls = []

    def blocking_loader(*args, **kwargs):
        loader_calls.append((args, kwargs))
        profile_entered.set()
        assert release_profile.wait(5), "测试未释放profile loader"
        return _valid_profile()

    monkeypatch.setattr(helper, "_load_runtime_profile", blocking_loader)
    first_namespace = {"order": lambda *args, **kwargs: "first-native"}
    second_namespace = {"order": lambda *args, **kwargs: "second-native"}
    results = []
    errors = []

    def run_install(namespace, context):
        try:
            results.append(
                _install(
                    namespace,
                    context,
                    "unused_because_loader_is_patched",
                    mode="SHADOW",
                )
            )
        except BaseException as exc:
            errors.append(exc)

    first_thread = threading.Thread(
        target=run_install,
        args=(first_namespace, _Context("sim_trade")),
    )
    second_thread = threading.Thread(
        target=run_install,
        args=(second_namespace, _Context("sim_trade")),
    )
    first_thread.start()
    assert profile_entered.wait(5), "首个安装未进入profile loader"
    second_thread.start()
    second_thread.join(5)
    assert not second_thread.is_alive(), "并发安装不得等待profile线程形成死锁"
    release_profile.set()
    first_thread.join(5)
    assert not first_thread.is_alive()

    assert len(results) == 1
    assert results[0]["mode"] == "SHADOW"
    assert len(errors) == 1
    assert "正在切换" in str(errors[0])
    assert len(loader_calls) == 1
    assert helper._STRATEGY_RUNTIME_ACTIVE_MODE == "SHADOW"
    with pytest.raises(RuntimeError, match="TRANSITIONING模式禁止交易变更"):
        second_namespace["order"]("000001.XSHE", 100)


def test_same_namespace_is_guarded_before_concurrent_install_rejection(monkeypatch):
    normalise_entered = threading.Event()
    release_normalise = threading.Event()
    original_normalise = helper._normalise_runtime_mode
    normalise_calls = []

    def blocking_normalise(mode):
        normalise_calls.append(mode)
        if len(normalise_calls) == 1:
            normalise_entered.set()
            assert release_normalise.wait(5)
        return original_normalise(mode)

    monkeypatch.setattr(helper, "_normalise_runtime_mode", blocking_normalise)
    monkeypatch.setattr(helper, "_load_runtime_profile", lambda *args, **kwargs: _valid_profile())
    namespace = {"order": lambda *args, **kwargs: "native-order"}
    results = []
    errors = []

    def first_install():
        try:
            results.append(
                _install(
                    namespace,
                    _Context("sim_trade"),
                    "unused_because_loader_is_patched",
                    mode="SHADOW",
                )
            )
        except BaseException as exc:
            errors.append(exc)

    first_thread = threading.Thread(target=first_install)
    first_thread.start()
    assert normalise_entered.wait(5)

    with pytest.raises(RuntimeError, match="正在切换"):
        _install(
            namespace,
            _Context("sim_trade"),
            "unused_because_loader_is_patched",
            mode="SHADOW",
        )
    with pytest.raises(RuntimeError, match="TRANSITIONING模式禁止交易变更"):
        namespace["order"]("000001.XSHE", 100)

    release_normalise.set()
    first_thread.join(5)
    assert not first_thread.is_alive()
    assert errors == []
    assert len(results) == 1
    assert results[0]["mode"] == "SHADOW"
    assert helper._STRATEGY_RUNTIME_ACTIVE_MODE == "SHADOW"
    with pytest.raises(RuntimeError, match="SHADOW模式禁止交易变更"):
        namespace["order"]("000001.XSHE", 100)


def test_recursive_install_guards_different_nested_namespace(monkeypatch):
    nested_namespace = {"order": lambda *args, **kwargs: "nested-native-order"}
    recursive_errors = []

    def recursive_profile_loader(*args, **kwargs):
        try:
            _install(
                nested_namespace,
                _Context("sim_trade"),
                "recursive_profile_must_not_load",
                mode="SHADOW",
            )
        except RuntimeError as exc:
            recursive_errors.append(exc)
        return _valid_profile()

    monkeypatch.setattr(helper, "_load_runtime_profile", recursive_profile_loader)
    outer_namespace = {"order": lambda *args, **kwargs: "outer-native-order"}
    state = _install(
        outer_namespace,
        _Context("sim_trade"),
        "unused_because_loader_is_patched",
        mode="SHADOW",
    )

    assert state["mode"] == "SHADOW"
    assert len(recursive_errors) == 1
    assert "不允许递归调用" in str(recursive_errors[0])
    with pytest.raises(RuntimeError, match="TRANSITIONING模式禁止交易变更"):
        nested_namespace["order"]("000001.XSHE", 100)
    with pytest.raises(RuntimeError, match="SHADOW模式禁止交易变更"):
        outer_namespace["order"]("000001.XSHE", 100)


def test_simultaneous_installers_cannot_queue_before_transition_owner_is_set(monkeypatch):
    start = threading.Barrier(3)
    profile_entered = threading.Event()
    release_profile = threading.Event()
    rejection_finished = threading.Event()
    loader_calls = []

    def profile_loader(*args, **kwargs):
        loader_calls.append((args, kwargs))
        profile_entered.set()
        assert release_profile.wait(5)
        return _valid_profile()

    monkeypatch.setattr(helper, "_load_runtime_profile", profile_loader)
    namespaces = [
        {"order": lambda *args, **kwargs: "first-native"},
        {"order": lambda *args, **kwargs: "second-native"},
    ]
    results = []
    errors = []

    def run_install(index):
        start.wait(5)
        try:
            results.append(
                (
                    index,
                    helper.install_strategy_runtime(
                        namespace=namespaces[index],
                        context=_Context("sim_trade"),
                        profile="good_etf-prod",
                        mode="SHADOW",
                        strategy_id="good_etf",
                        profile_module="unused_because_loader_is_patched",
                    ),
                )
            )
        except BaseException as exc:
            errors.append((index, exc))
            rejection_finished.set()

    threads = [threading.Thread(target=run_install, args=(index,)) for index in range(2)]
    for thread in threads:
        thread.start()
    start.wait(5)
    assert profile_entered.wait(5)
    assert rejection_finished.wait(5), "第二个安装必须立即拒绝，不能在主锁上排队"
    release_profile.set()
    for thread in threads:
        thread.join(5)
        assert not thread.is_alive()

    assert len(results) == 1
    assert results[0][1]["mode"] == "SHADOW"
    assert len(errors) == 1
    assert "正在切换" in str(errors[0][1])
    assert len(loader_calls) == 1
    assert helper._STRATEGY_RUNTIME_ACTIVE_MODE == "SHADOW"
    rejected_index = errors[0][0]
    with pytest.raises(RuntimeError, match="TRANSITIONING模式禁止交易变更"):
        namespaces[rejected_index]["order"]("000001.XSHE", 100)


def test_inflight_request_prevents_shadow_from_becoming_active(monkeypatch):
    socket_entered = threading.Event()
    release_socket = threading.Event()
    socket_modes = []
    request_errors = []
    install_errors = []
    client = helper._ShortLivedClient(
        "127.0.0.1",
        58620,
        "unit-test-token",
        retries=2,
        retry_interval=0.1,
    )

    def blocking_socket(*args, **kwargs):
        socket_modes.append(helper._STRATEGY_RUNTIME_ACTIVE_MODE)
        socket_entered.set()
        assert release_socket.wait(5), "测试未释放socket"
        raise OSError("expected test transport stop")

    monkeypatch.setattr(helper.socket, "create_connection", blocking_socket)

    def run_request():
        try:
            client.request("broker.account", {})
        except BaseException as exc:
            request_errors.append(exc)

    namespace = {"order": lambda *args, **kwargs: "native-order"}

    def run_install():
        try:
            _install(
                namespace,
                _Context("sim_trade"),
                "profile_must_not_load_while_request_is_inflight",
                mode="SHADOW",
            )
        except BaseException as exc:
            install_errors.append(exc)

    request_thread = threading.Thread(target=run_request)
    install_thread = threading.Thread(target=run_install)
    request_thread.start()
    assert socket_entered.wait(5), "旧请求未进入socket"
    install_thread.start()
    install_thread.join(5)
    assert not install_thread.is_alive(), "runtime应对在途请求立即失败关闭"
    assert len(install_errors) == 1
    assert "远程请求在途" in str(install_errors[0])
    assert helper._STRATEGY_RUNTIME_ACTIVE_MODE == "FAILED"
    release_socket.set()
    request_thread.join(5)
    assert not request_thread.is_alive()

    assert len(request_errors) == 1
    assert helper._STRATEGY_RUNTIME_INFLIGHT_REQUESTS == 0
    assert socket_modes == [None]
    with pytest.raises(RuntimeError, match="FAILED模式禁止交易变更"):
        namespace["order"]("000001.XSHE", 100)


@pytest.mark.parametrize("exception_type", [SystemExit, KeyboardInterrupt])
def test_request_base_exception_always_releases_inflight_lease(monkeypatch, exception_type):
    client = helper._ShortLivedClient(
        "127.0.0.1",
        58620,
        "unit-test-token",
        retries=0,
    )
    monkeypatch.setattr(
        helper.socket,
        "create_connection",
        lambda *args, **kwargs: (_ for _ in ()).throw(exception_type("transport-stop")),
    )

    with pytest.raises(exception_type, match="transport-stop"):
        client.request("broker.account", {})

    assert helper._STRATEGY_RUNTIME_INFLIGHT_REQUESTS == 0


def test_helper_reload_fails_closed_and_recognises_old_remote_portfolio():
    script = textwrap.dedent(
        """
        import importlib
        import sys
        import types

        from helpers import bullet_trade_jq_remote_helper as helper

        class RunParams:
            type = "sim_trade"

        class Context:
            run_params = RunParams()

        profile_module = types.ModuleType("reload_runtime_profile")
        profile_module.PROFILE_SCHEMA_VERSION = 1
        profile_module.PROFILES = {
            "good_etf-prod": {
                "strategy_id": "good_etf",
                "host": "127.0.0.1",
                "token": "unit-test-token",
            }
        }
        sys.modules[profile_module.__name__] = profile_module
        namespace = {"order": lambda *args, **kwargs: "native"}
        context = Context()
        helper.install_strategy_runtime(
            namespace,
            context=context,
            profile="good_etf-prod",
            mode="SHADOW",
            strategy_id="good_etf",
            profile_module=profile_module.__name__,
        )
        old_token = namespace[helper._STRATEGY_RUNTIME_STATE_KEY]["runtime_instance_token"]
        old_portfolio = object.__new__(helper._RemoteJQPortfolio)
        helper = importlib.reload(helper)
        assert helper._STRATEGY_RUNTIME_ACTIVE_MODE == "FAILED"
        assert helper._STRATEGY_RUNTIME_INSTANCE_TOKEN is not old_token
        probe_context = Context()
        probe_context.portfolio = old_portfolio
        assert helper._context_uses_remote_snapshot(probe_context)
        try:
            helper.install_strategy_runtime(
                namespace,
                context=context,
                profile="good_etf-prod",
                mode="SHADOW",
                strategy_id="good_etf",
                profile_module=profile_module.__name__,
            )
        except RuntimeError as exc:
            assert "干净运行进程重启" in str(exc)
        else:
            raise AssertionError("reload后的旧namespace不得恢复runtime")
        assert helper._STRATEGY_RUNTIME_STATE_KEY not in namespace
        try:
            namespace["order"]("000001.XSHE", 100)
        except RuntimeError as exc:
            assert "FAILED模式禁止交易变更" in str(exc)
        else:
            raise AssertionError("reload失败后namespace必须保持guard")
        print("RELOAD_FAIL_CLOSED_OK")
        """
    )
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    result = subprocess.run(
        [sys.executable, "-X", "utf8", "-c", script],
        cwd=os.getcwd(),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "RELOAD_FAIL_CLOSED_OK" in result.stdout


def test_profile_reload_during_runtime_install_cannot_return_false_success():
    script = textwrap.dedent(
        """
        import importlib

        from helpers import bullet_trade_jq_remote_helper as helper

        class RunParams:
            type = "sim_trade"

        class Context:
            run_params = RunParams()

        namespace = {"order": lambda *args, **kwargs: "native"}

        def reloading_profile_loader(*args, **kwargs):
            importlib.reload(helper)
            return {"strategy_id": "good_etf"}

        helper._load_runtime_profile = reloading_profile_loader
        try:
            helper.install_strategy_runtime(
                namespace,
                context=Context(),
                profile="good_etf-prod",
                mode="SHADOW",
                strategy_id="good_etf",
                profile_module="reload_during_profile_import",
            )
        except RuntimeError as exc:
            assert "发生重载" in str(exc)
        else:
            raise AssertionError("profile导入期间reload不得返回SHADOW成功状态")

        assert helper._STRATEGY_RUNTIME_ACTIVE_MODE == "FAILED"
        assert helper._STRATEGY_RUNTIME_STATE_KEY not in namespace
        try:
            namespace["order"]("000001.XSHE", 100)
        except RuntimeError as exc:
            assert "FAILED模式禁止交易变更" in str(exc)
        else:
            raise AssertionError("reload失败后namespace必须保持guard")
        print("PROFILE_RELOAD_FAIL_CLOSED_OK")
        """
    )
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    result = subprocess.run(
        [sys.executable, "-X", "utf8", "-c", script],
        cwd=os.getcwd(),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "PROFILE_RELOAD_FAIL_CLOSED_OK" in result.stdout


def test_non_string_mode_is_rejected_without_executing_custom_str():
    calls = []

    class SideEffectMode:
        def __str__(self):
            calls.append("custom-str-called")
            return "SHADOW"

    namespace = {"order": lambda *args, **kwargs: "native"}
    with pytest.raises(RuntimeError, match="普通字符串"):
        helper.install_strategy_runtime(
            namespace,
            context=_Context("sim_trade"),
            profile="good_etf-prod",
            mode=SideEffectMode(),
            strategy_id="good_etf",
            profile_module="profile_must_not_load",
        )

    assert calls == []
    assert helper._STRATEGY_RUNTIME_ACTIVE_MODE == "FAILED"
    with pytest.raises(RuntimeError, match="FAILED模式禁止交易变更"):
        namespace["order"]("000001.XSHE", 100)


def test_standalone_configure_cannot_publish_clients_across_helper_reload():
    script = textwrap.dedent(
        """
        import importlib

        from helpers import bullet_trade_jq_remote_helper as helper

        original_client_class = helper._ShortLivedClient

        def reloading_client_factory(*args, **kwargs):
            importlib.reload(helper)
            return original_client_class(*args, **kwargs)

        helper._ShortLivedClient = reloading_client_factory
        secret = "configure-reload-secret"
        try:
            helper.configure(
                host="127.0.0.1",
                token=secret,
                retries=0,
                debug=False,
            )
        except RuntimeError as exc:
            assert "调用期间发生重载" in str(exc)
            assert secret not in str(exc)
        else:
            raise AssertionError("跨reload的旧configure调用不得返回成功")

        assert helper._STRATEGY_RUNTIME_ACTIVE_MODE == "FAILED"
        assert helper._CLIENT is None
        assert helper._DATA_CLIENT is None
        assert helper._BROKER_CLIENT is None
        print("CONFIGURE_RELOAD_FAIL_CLOSED_OK")
        """
    )
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    result = subprocess.run(
        [sys.executable, "-X", "utf8", "-c", script],
        cwd=os.getcwd(),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "CONFIGURE_RELOAD_FAIL_CLOSED_OK" in result.stdout


def test_missing_profile_module_has_clear_error_without_import_details():
    with pytest.raises(RuntimeError, match="无法加载运行配置模块") as exc_info:
        _install({}, _Context("sim_trade"), "definitely_missing_jq_runtime_config")
    assert "No module named" not in str(exc_info.value)


@pytest.mark.parametrize("version", [None, True, 0, 2, "1"])
def test_profile_schema_version_is_strict(monkeypatch, version):
    module_name = _profile_module(monkeypatch, version=version)
    with pytest.raises(RuntimeError, match="schema版本不匹配"):
        _install({}, _Context("sim_trade"), module_name)


def test_missing_named_profile_is_rejected(monkeypatch):
    module_name = _profile_module(monkeypatch, profiles={"another": _valid_profile()})
    with pytest.raises(RuntimeError, match="不存在profile"):
        _install({}, _Context("sim_trade"), module_name)


@pytest.mark.parametrize("field", ["strategy_id", "host", "token"])
def test_required_profile_fields_are_strict(monkeypatch, field):
    profile = _valid_profile()
    profile.pop(field)
    module_name = _profile_module(monkeypatch, profiles={"good_etf-prod": profile})
    with pytest.raises(RuntimeError, match="缺少必填字段"):
        _install({}, _Context("sim_trade"), module_name)


def test_unknown_profile_field_is_rejected(monkeypatch):
    module_name = _profile_module(
        monkeypatch,
        profiles={"good_etf-prod": _valid_profile(password="must-not-be-accepted")},
    )
    with pytest.raises(RuntimeError, match="未知字段: password"):
        _install({}, _Context("sim_trade"), module_name)


@pytest.mark.parametrize("port", [0, 65536, True, 58620.0, "58620"])
def test_profile_port_is_strict(monkeypatch, port):
    module_name = _profile_module(
        monkeypatch,
        profiles={"good_etf-prod": _valid_profile(port=port)},
    )
    with pytest.raises(RuntimeError, match="profile.port"):
        _install({}, _Context("sim_trade"), module_name)


@pytest.mark.parametrize(
    "overrides",
    [
        {"host": ""},
        {"host": "bad host"},
        {"token": ""},
        {"token": " token-with-space"},
        {"debug": 1},
        {"retries": 11},
        {"rpc_timeout": float("nan")},
        {"rpc_timeout": 4.99},
        {"rpc_timeout": 301},
        {"retry_interval": -1},
        {"retry_interval": 31},
        {"place_order_timeout_margin": 301},
        {"default_wait_timeout": 301},
        {"account_key": ""},
    ],
)
def test_profile_value_validation_is_strict(monkeypatch, overrides):
    module_name = _profile_module(
        monkeypatch,
        profiles={"good_etf-prod": _valid_profile(**overrides)},
    )
    with pytest.raises(RuntimeError, match=r"profile\."):
        _install({}, _Context("sim_trade"), module_name)


def test_profile_strategy_id_must_match_strategy(monkeypatch):
    module_name = _profile_module(
        monkeypatch,
        profiles={"good_etf-prod": _valid_profile(strategy_id="other_strategy")},
    )
    with pytest.raises(RuntimeError, match="不一致"):
        _install({}, _Context("sim_trade"), module_name)


@pytest.mark.parametrize("exception_type", [RuntimeError, SystemExit, KeyboardInterrupt])
def test_profile_import_base_exception_never_leaks_token(monkeypatch, exception_type):
    secret = "unique-super-secret-token"
    original_import = __import__

    def unsafe_import(name, *args, **kwargs):
        if name == "unsafe_profile_module":
            raise exception_type("profile failed with token={}".format(secret))
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", unsafe_import)
    with pytest.raises(RuntimeError, match="无法加载运行配置模块") as exc_info:
        _install({}, _Context("sim_trade"), "unsafe_profile_module", mode="LIVE")

    assert secret not in str(exc_info.value)
    assert exc_info.value.__cause__ is None
