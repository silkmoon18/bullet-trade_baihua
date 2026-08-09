import builtins
import functools
import gc
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
    monkeypatch.setattr(helper, "_STRATEGY_RUNTIME_COMMIT_CAPSULE", None)
    monkeypatch.setattr(helper, "_STRATEGY_RUNTIME_CONTRACT_GENERATION", 0)
    helper._set_runtime_commit_anchor(None)
    set.clear(helper._STRATEGY_RUNTIME_REQUEST_LEASES)
    monkeypatch.setattr(helper, "_STRATEGY_RUNTIME_INFLIGHT_REQUESTS", 0)
    monkeypatch.setattr(helper, "_STRATEGY_RUNTIME_TRANSITION_OWNER", None)
    monkeypatch.setattr(helper, "_STRATEGY_RUNTIME_TRANSITION_NAMESPACE", None)
    monkeypatch.setattr(helper, "_STRATEGY_RUNTIME_TRANSITION_MODE", None)
    monkeypatch.setattr(helper, "_STRATEGY_RUNTIME_RELOAD_IN_PROGRESS", False)
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
    assert (
        helper.STRATEGY_RUNTIME_HELPER_MARKER
        == "bullet-trade-joinquant-runtime-helper-v1"
    )
    assert helper.PROFILE_SCHEMA_VERSION == 1
    assert "install_strategy_runtime" in helper.__all__
    assert "STRATEGY_RUNTIME_HELPER_MARKER" in helper.__all__


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


def test_backtest_arms_process_gate_before_reading_context(monkeypatch):
    cached_client = helper._ShortLivedClient(
        "127.0.0.1", 58620, "unit-test-token", retries=0
    )
    socket_calls = []
    monkeypatch.setattr(
        helper.socket,
        "create_connection",
        lambda *args, **kwargs: socket_calls.append((args, kwargs)),
    )

    class SideEffectRunParams:
        @property
        def type(self):
            return cached_client.request("broker.place_order", {"amount": 100})

    class SideEffectContext:
        run_params = SideEffectRunParams()

    namespace = {"order": lambda *args, **kwargs: "native-order"}
    with pytest.raises(RuntimeError, match="TRANSITIONING模式禁止远程访问"):
        _install(
            namespace,
            SideEffectContext(),
            "module_that_must_not_be_imported",
            mode="BACKTEST",
        )

    assert socket_calls == []
    assert helper._STRATEGY_RUNTIME_ACTIVE_MODE == "FAILED"
    with pytest.raises(RuntimeError, match="FAILED模式禁止交易变更"):
        namespace["order"]("000001.XSHE", 100)


def test_unknown_process_state_fails_closed_before_context(monkeypatch):
    class PoisonActiveMode:
        def __str__(self):
            raise AssertionError("无效运行状态不得被格式化")

        def __repr__(self):
            raise AssertionError("无效运行状态不得被格式化")

    cached_client = helper._ShortLivedClient(
        "127.0.0.1", 58620, "unit-test-token", retries=0
    )
    socket_calls = []
    context_reads = []
    monkeypatch.setattr(helper, "_STRATEGY_RUNTIME_ACTIVE_MODE", PoisonActiveMode())
    monkeypatch.setattr(
        helper.socket,
        "create_connection",
        lambda *args, **kwargs: socket_calls.append((args, kwargs)),
    )

    with pytest.raises(RuntimeError, match="INVALID模式禁止远程访问"):
        cached_client.request("broker.place_order", {"amount": 100})

    class SideEffectRunParams:
        @property
        def type(self):
            context_reads.append("type")
            return cached_client.request("broker.place_order", {"amount": 100})

    class SideEffectContext:
        run_params = SideEffectRunParams()

    namespace = {"order": lambda *args, **kwargs: "native-order"}
    with pytest.raises(RuntimeError, match="进程状态无效"):
        _install(
            namespace,
            SideEffectContext(),
            "module_that_must_not_be_imported",
            mode="BACKTEST",
        )

    assert context_reads == []
    assert socket_calls == []
    assert helper._STRATEGY_RUNTIME_ACTIVE_MODE == "FAILED"
    with pytest.raises(RuntimeError, match="FAILED模式禁止交易变更"):
        namespace["order"]("000001.XSHE", 100)


def test_orphaned_transition_state_fails_closed_before_context(monkeypatch):
    context_reads = []

    class UnreadableRunParams:
        @property
        def type(self):
            context_reads.append("type")
            return "full_backtest"

    class UnreadableContext:
        run_params = UnreadableRunParams()

    monkeypatch.setattr(helper, "_STRATEGY_RUNTIME_ACTIVE_MODE", "TRANSITIONING")
    namespace = {"order": lambda *args, **kwargs: "native-order"}

    with pytest.raises(RuntimeError, match="进程状态无效"):
        _install(
            namespace,
            UnreadableContext(),
            "unused_profile",
            mode="BACKTEST",
        )

    assert context_reads == []
    assert helper._STRATEGY_RUNTIME_ACTIVE_MODE == "FAILED"
    with pytest.raises(RuntimeError, match="FAILED模式禁止交易变更"):
        namespace["order"]("000001.XSHE", 100)


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


def test_runtime_rejects_huge_api_version_with_stable_error():
    with pytest.raises(RuntimeError, match="expected=<invalid>"):
        _install(
            {},
            _Context("full_backtest"),
            "unused_profile",
            mode="BACKTEST",
            expected_api_version=10 ** 5000,
        )


def test_runtime_rejects_huge_actual_api_version_with_stable_error(monkeypatch):
    monkeypatch.setattr(helper, "STRATEGY_RUNTIME_API_VERSION", 10 ** 5000)

    with pytest.raises(RuntimeError, match="进程状态无效"):
        _install(
            {},
            _Context("full_backtest"),
            "unused_profile",
            mode="BACKTEST",
            expected_api_version=1,
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

    with pytest.raises(RuntimeError, match="进程状态无效"):
        _install(namespace, context, module_name)

    assert helper._STRATEGY_RUNTIME_ACTIVE_MODE == "FAILED"
    assert helper._STRATEGY_RUNTIME_STATE_KEY not in namespace


def test_erased_process_authority_cannot_recover_as_fresh_runtime(monkeypatch):
    native_order = lambda *args, **kwargs: "native-order"
    namespace = {"order": native_order}
    first_state = _install(
        namespace,
        _Context("full_backtest"),
        "unused_profile",
        mode="BACKTEST",
    )
    assert first_state["orders_enabled"] is True

    dict.pop(namespace, helper._STRATEGY_RUNTIME_STATE_KEY)
    monkeypatch.setattr(helper, "_STRATEGY_RUNTIME_ACTIVE_MODE", None)
    monkeypatch.setattr(helper, "_STRATEGY_RUNTIME_PROCESS_SIGNATURE", None)
    monkeypatch.setattr(helper, "_STRATEGY_RUNTIME_CANONICAL_STATE", None)
    context_reads = []

    class UnreadableRunParams:
        @property
        def type(self):
            context_reads.append("type")
            return "full_backtest"

    class UnreadableContext:
        run_params = UnreadableRunParams()

    with pytest.raises(RuntimeError, match="进程状态无效"):
        helper.install_strategy_runtime(
            namespace,
            context=UnreadableContext(),
            profile="second-profile",
            mode="BACKTEST",
            strategy_id="second-strategy",
            profile_module="unused_profile",
        )

    assert context_reads == []
    assert helper._STRATEGY_RUNTIME_ACTIVE_MODE == "FAILED"
    with pytest.raises(RuntimeError, match="FAILED模式禁止交易变更"):
        namespace["order"]("000001.XSHE", 100)


def test_failed_closure_anchor_prevents_manual_fresh_reset(monkeypatch):
    namespace = {"order": lambda *args, **kwargs: "native-order"}
    _install(
        namespace,
        _Context("full_backtest"),
        "unused_profile",
        mode="BACKTEST",
    )
    helper._mark_runtime_failed(namespace)
    assert helper._get_runtime_commit_anchor() is helper._STRATEGY_RUNTIME_FAILED_ANCHOR
    monkeypatch.setattr(helper, "_STRATEGY_RUNTIME_ACTIVE_MODE", None)
    monkeypatch.setattr(helper, "_STRATEGY_RUNTIME_CONTRACT_GENERATION", 0)
    context_reads = []

    class UnreadableRunParams:
        @property
        def type(self):
            context_reads.append("type")
            return "full_backtest"

    class UnreadableContext:
        run_params = UnreadableRunParams()

    with pytest.raises(RuntimeError, match="进程状态无效"):
        helper.install_strategy_runtime(
            namespace,
            context=UnreadableContext(),
            profile="second-profile",
            mode="BACKTEST",
            strategy_id="second-strategy",
            profile_module="unused_profile",
        )

    assert context_reads == []
    assert helper._STRATEGY_RUNTIME_ACTIVE_MODE == "FAILED"


@pytest.mark.parametrize(
    "tamper_target",
    [
        "canonical_state",
        "canonical_identity",
        "record_state",
        "record_state_identity",
        "record_shape",
        "record_identity",
        "process_signature",
        "coordinated_signature",
        "contract_generation",
        "capsule_identity",
    ],
)
def test_tampered_process_authority_fails_before_context(
    monkeypatch,
    tamper_target,
):
    namespace = {"order": lambda *args, **kwargs: "native-order"}
    _install(
        namespace,
        _Context("full_backtest"),
        "unused_profile",
        mode="BACKTEST",
    )
    runtime_record = namespace[helper._STRATEGY_RUNTIME_STATE_KEY]
    if tamper_target == "canonical_state":
        monkeypatch.setattr(
            helper,
            "_STRATEGY_RUNTIME_CANONICAL_STATE",
            dict(helper._STRATEGY_RUNTIME_CANONICAL_STATE, reason="tampered"),
        )
    elif tamper_target == "canonical_identity":
        monkeypatch.setattr(
            helper,
            "_STRATEGY_RUNTIME_CANONICAL_STATE",
            dict(helper._STRATEGY_RUNTIME_CANONICAL_STATE),
        )
    elif tamper_target == "record_state":
        runtime_record["state"] = dict(runtime_record["state"], reason="tampered")
    elif tamper_target == "record_state_identity":
        runtime_record["state"] = dict(runtime_record["state"])
    elif tamper_target == "record_shape":
        runtime_record["unexpected"] = True
    elif tamper_target == "record_identity":
        namespace[helper._STRATEGY_RUNTIME_STATE_KEY] = dict(runtime_record)
    elif tamper_target == "process_signature":
        monkeypatch.setattr(
            helper,
            "_STRATEGY_RUNTIME_PROCESS_SIGNATURE",
            tuple(list(helper._STRATEGY_RUNTIME_PROCESS_SIGNATURE)),
        )
    elif tamper_target == "coordinated_signature":
        replacement_signature = tuple(
            list(helper._STRATEGY_RUNTIME_PROCESS_SIGNATURE)
        )
        monkeypatch.setattr(
            helper,
            "_STRATEGY_RUNTIME_PROCESS_SIGNATURE",
            replacement_signature,
        )
        runtime_record["signature"] = replacement_signature
    elif tamper_target == "contract_generation":
        monkeypatch.setattr(
            helper,
            "_STRATEGY_RUNTIME_CONTRACT_GENERATION",
            helper._STRATEGY_RUNTIME_CONTRACT_GENERATION + 7,
        )
    else:
        monkeypatch.setattr(
            helper,
            "_STRATEGY_RUNTIME_COMMIT_CAPSULE",
            tuple(list(helper._STRATEGY_RUNTIME_COMMIT_CAPSULE)),
        )

    context_reads = []

    class UnreadableRunParams:
        @property
        def type(self):
            context_reads.append("type")
            return "full_backtest"

    class UnreadableContext:
        run_params = UnreadableRunParams()

    with pytest.raises(RuntimeError, match="进程状态无效"):
        helper.install_strategy_runtime(
            namespace,
            context=UnreadableContext(),
            profile="unused_profile",
            mode="BACKTEST",
            strategy_id="good-etf-main",
            profile_module="unused_profile",
        )

    assert context_reads == []
    assert helper._STRATEGY_RUNTIME_ACTIVE_MODE == "FAILED"


def test_coordinated_in_place_state_change_fails_before_context(monkeypatch):
    module_name = _profile_module(monkeypatch)
    namespace = {}
    context = _Context("sim_trade")
    _install(namespace, context, module_name)
    runtime_record = namespace[helper._STRATEGY_RUNTIME_STATE_KEY]
    helper._STRATEGY_RUNTIME_CANONICAL_STATE["blocked_mutations"] = (
        "forged_alias",
    )
    runtime_record["state"]["blocked_mutations"] = ("forged_alias",)
    context_reads = []

    class UnreadableRunParams:
        @property
        def type(self):
            context_reads.append("type")
            return "sim_trade"

    context.run_params = UnreadableRunParams()

    with pytest.raises(RuntimeError, match="进程状态无效"):
        _install(namespace, context, module_name)

    assert context_reads == []
    assert helper._STRATEGY_RUNTIME_ACTIVE_MODE == "FAILED"


def test_poisoned_authority_value_is_not_executed_before_failure(monkeypatch):
    namespace = {"order": lambda *args, **kwargs: "native-order"}
    _install(
        namespace,
        _Context("full_backtest"),
        "unused_profile",
        mode="BACKTEST",
    )
    poison_events = []

    class Poison:
        def __eq__(self, other):
            poison_events.append("eq")
            raise AssertionError("poison equality must not execute")

        def __str__(self):
            poison_events.append("str")
            raise AssertionError("poison stringification must not execute")

    helper._STRATEGY_RUNTIME_CANONICAL_STATE["reason"] = Poison()
    context_reads = []

    class UnreadableRunParams:
        @property
        def type(self):
            context_reads.append("type")
            return "full_backtest"

    class UnreadableContext:
        run_params = UnreadableRunParams()

    with pytest.raises(RuntimeError, match="进程状态无效"):
        helper.install_strategy_runtime(
            namespace,
            context=UnreadableContext(),
            profile="unused_profile",
            mode="BACKTEST",
            strategy_id="good-etf-main",
            profile_module="unused_profile",
        )

    assert context_reads == []
    assert poison_events == []


@pytest.mark.parametrize("tampered_version", [True, 2])
def test_tampered_public_runtime_version_fails_before_context(
    monkeypatch,
    tampered_version,
):
    namespace = {"order": lambda *args, **kwargs: "native-order"}
    _install(
        namespace,
        _Context("full_backtest"),
        "unused_profile",
        mode="BACKTEST",
    )
    monkeypatch.setattr(
        helper,
        "STRATEGY_RUNTIME_API_VERSION",
        tampered_version,
    )
    context_reads = []

    class UnreadableRunParams:
        @property
        def type(self):
            context_reads.append("type")
            return "full_backtest"

    class UnreadableContext:
        run_params = UnreadableRunParams()

    with pytest.raises(RuntimeError, match="进程状态无效"):
        helper.install_strategy_runtime(
            namespace,
            context=UnreadableContext(),
            profile="unused_profile",
            mode="BACKTEST",
            strategy_id="good-etf-main",
            profile_module="unused_profile",
        )

    assert context_reads == []


def test_poisoned_public_runtime_version_does_not_execute_magic(monkeypatch):
    namespace = {"order": lambda *args, **kwargs: "native-order"}
    _install(
        namespace,
        _Context("full_backtest"),
        "unused_profile",
        mode="BACKTEST",
    )
    magic_events = []

    class PoisonVersion:
        def __eq__(self, other):
            magic_events.append(("eq", type(other).__name__))
            raise AssertionError("poison equality must not execute")

        def __ne__(self, other):
            magic_events.append(("ne", type(other).__name__))
            raise AssertionError("poison inequality must not execute")

    monkeypatch.setattr(
        helper,
        "STRATEGY_RUNTIME_API_VERSION",
        PoisonVersion(),
    )
    context_reads = []

    class UnreadableRunParams:
        @property
        def type(self):
            context_reads.append("type")
            return "full_backtest"

    class UnreadableContext:
        run_params = UnreadableRunParams()

    with pytest.raises(RuntimeError, match="进程状态无效"):
        helper.install_strategy_runtime(
            namespace,
            context=UnreadableContext(),
            profile="unused_profile",
            mode="BACKTEST",
            strategy_id="good-etf-main",
            profile_module="unused_profile",
        )

    assert context_reads == []
    assert magic_events == []


def test_poisoned_contract_generation_does_not_execute_magic(monkeypatch):
    namespace = {"order": lambda *args, **kwargs: "native-order"}
    _install(
        namespace,
        _Context("full_backtest"),
        "unused_profile",
        mode="BACKTEST",
    )
    magic_events = []

    class PoisonGeneration:
        def __ge__(self, other):
            magic_events.append(("ge", type(other).__name__))
            raise AssertionError("poison comparison must not execute")

        def __add__(self, other):
            magic_events.append(("add", type(other).__name__))
            raise AssertionError("poison arithmetic must not execute")

    monkeypatch.setattr(
        helper,
        "_STRATEGY_RUNTIME_CONTRACT_GENERATION",
        PoisonGeneration(),
    )
    context_reads = []

    class UnreadableRunParams:
        @property
        def type(self):
            context_reads.append("type")
            return "full_backtest"

    class UnreadableContext:
        run_params = UnreadableRunParams()

    with pytest.raises(RuntimeError, match="进程状态无效"):
        helper.install_strategy_runtime(
            namespace,
            context=UnreadableContext(),
            profile="unused_profile",
            mode="BACKTEST",
            strategy_id="good-etf-main",
            profile_module="unused_profile",
        )

    assert context_reads == []
    assert magic_events == []


def test_namespace_state_never_restores_missing_process_authority(monkeypatch):
    module_name = _profile_module(monkeypatch)
    namespace = {}
    context = _Context("sim_trade")
    _install(namespace, context, module_name)
    monkeypatch.setattr(helper, "_STRATEGY_RUNTIME_PROCESS_SIGNATURE", None)

    with pytest.raises(RuntimeError, match="进程状态无效"):
        _install(namespace, context, module_name)

    assert helper._STRATEGY_RUNTIME_ACTIVE_MODE == "FAILED"


def test_process_authority_rejects_missing_namespace_state(monkeypatch):
    module_name = _profile_module(monkeypatch)
    namespace = {}
    context = _Context("sim_trade")
    _install(namespace, context, module_name)
    namespace.pop(helper._STRATEGY_RUNTIME_STATE_KEY)

    with pytest.raises(RuntimeError, match="进程状态无效"):
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


def test_concurrent_backtest_rejection_preserves_native_order(monkeypatch):
    normalise_entered = threading.Event()
    release_normalise = threading.Event()
    original_normalise = helper._normalise_run_type

    def blocking_normalise(context):
        normalise_entered.set()
        assert release_normalise.wait(5)
        return original_normalise(context)

    monkeypatch.setattr(helper, "_normalise_run_type", blocking_normalise)
    first_order = lambda *args, **kwargs: "first-native"
    second_order = lambda *args, **kwargs: "second-native"
    first_namespace = {"order": first_order}
    second_namespace = {"order": second_order}
    results = []
    errors = []

    def first_install():
        try:
            results.append(
                _install(
                    first_namespace,
                    _Context("full_backtest"),
                    "unused_profile",
                    mode="BACKTEST",
                )
            )
        except BaseException as exc:
            errors.append(exc)

    first_thread = threading.Thread(target=first_install)
    first_thread.start()
    assert normalise_entered.wait(5)

    with pytest.raises(RuntimeError, match="正在切换"):
        _install(
            second_namespace,
            _Context("full_backtest"),
            "unused_profile",
            mode="BACKTEST",
        )
    assert second_namespace["order"] is second_order

    release_normalise.set()
    first_thread.join(5)
    assert not first_thread.is_alive()
    assert errors == []
    assert len(results) == 1
    assert results[0]["mode"] == "BACKTEST"
    assert helper._STRATEGY_RUNTIME_ACTIVE_MODE == "BACKTEST"
    assert first_namespace["order"] is first_order
    assert second_namespace["order"] is second_order


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


def test_poisoned_request_generation_never_reaches_socket(monkeypatch):
    magic_events = []
    socket_calls = []

    class PoisonGeneration:
        def __ne__(self, other):
            magic_events.append(("ne", type(other).__name__))
            return False

        def __int__(self):
            magic_events.append(("int", None))
            raise AssertionError("poison conversion must not execute")

    client = helper._ShortLivedClient(
        "127.0.0.1",
        58620,
        "unit-test-token",
        retries=0,
    )
    monkeypatch.setattr(helper, "_CLIENT", client)
    monkeypatch.setattr(
        helper,
        "_STRATEGY_RUNTIME_CONTRACT_GENERATION",
        PoisonGeneration(),
    )
    monkeypatch.setattr(
        helper.socket,
        "create_connection",
        lambda *args, **kwargs: socket_calls.append((args, kwargs)),
    )

    with pytest.raises(RuntimeError, match="远程请求状态无效"):
        client.request("broker.place_order", {"amount": 100})

    assert magic_events == []
    assert socket_calls == []
    assert helper._CLIENT is None
    assert helper._STRATEGY_RUNTIME_ACTIVE_MODE == "FAILED"
    assert helper._STRATEGY_RUNTIME_INFLIGHT_REQUESTS == 0
    assert helper._get_runtime_commit_anchor() is helper._STRATEGY_RUNTIME_FAILED_ANCHOR


def test_poisoned_transition_owner_never_executes_magic_or_reaches_socket(
    monkeypatch,
):
    magic_events = []
    socket_calls = []

    class PoisonOwner:
        def __eq__(self, other):
            magic_events.append(("eq", type(other).__name__))
            raise AssertionError("poison equality must not execute")

        def __ne__(self, other):
            magic_events.append(("ne", type(other).__name__))
            return False

        def __hash__(self):
            magic_events.append(("hash", None))
            raise AssertionError("poison hash must not execute")

    client = helper._ShortLivedClient(
        "127.0.0.1",
        58620,
        "unit-test-token",
        retries=0,
    )
    monkeypatch.setattr(helper, "_CLIENT", client)
    monkeypatch.setattr(helper, "_STRATEGY_RUNTIME_TRANSITION_OWNER", PoisonOwner())
    monkeypatch.setattr(
        helper.socket,
        "create_connection",
        lambda *args, **kwargs: socket_calls.append((args, kwargs)),
    )

    with pytest.raises(RuntimeError, match="transition状态无效"):
        client.request("broker.place_order", {"amount": 1})

    assert magic_events == []
    assert socket_calls == []
    assert helper._STRATEGY_RUNTIME_ACTIVE_MODE == "FAILED"
    assert helper._CLIENT is None
    assert helper._STRATEGY_RUNTIME_INFLIGHT_REQUESTS == 0
    assert helper._STRATEGY_RUNTIME_REQUEST_LEASES == set()


@pytest.mark.parametrize(
    ("lock_name", "replacement_kind"),
    [
        ("_STRATEGY_RUNTIME_LOCK", "proxy"),
        ("_STRATEGY_RUNTIME_OWNER_LOCK", "proxy"),
        ("_STRATEGY_RUNTIME_LOCK", "same_type"),
        ("_STRATEGY_RUNTIME_OWNER_LOCK", "same_type"),
    ],
)
def test_replaced_runtime_lock_identity_never_reaches_socket(
    monkeypatch,
    lock_name,
    replacement_kind,
):
    magic_events = []
    socket_calls = []

    class PoisonLock:
        def __enter__(self):
            magic_events.append("enter")
            return self

        def __exit__(self, *args):
            magic_events.append("exit")

        def acquire(self, *args, **kwargs):
            magic_events.append("acquire")
            return True

        def release(self):
            magic_events.append("release")

    if replacement_kind == "proxy":
        replacement = PoisonLock()
    elif lock_name == "_STRATEGY_RUNTIME_LOCK":
        replacement = threading.RLock()
    else:
        replacement = threading.Lock()
    client = helper._ShortLivedClient(
        "127.0.0.1",
        58620,
        "unit-test-token",
        retries=0,
    )
    monkeypatch.setattr(helper, "_CLIENT", client)
    monkeypatch.setattr(helper, lock_name, replacement)
    monkeypatch.setattr(
        helper.socket,
        "create_connection",
        lambda *args, **kwargs: socket_calls.append((args, kwargs)),
    )

    with pytest.raises(RuntimeError, match="同步原语|helper代际"):
        client.request("broker.place_order", {"amount": 1})

    assert magic_events == []
    assert socket_calls == []
    assert helper._STRATEGY_RUNTIME_ACTIVE_MODE == "FAILED"
    assert helper._CLIENT is None
    assert helper._STRATEGY_RUNTIME_INFLIGHT_REQUESTS == 0
    assert helper._STRATEGY_RUNTIME_REQUEST_LEASES == set()


def test_poisoned_inflight_after_final_connect_log_never_reaches_socket(
    monkeypatch,
):
    magic_events = []
    socket_calls = []

    class PoisonInflight:
        def __eq__(self, other):
            magic_events.append(("eq", type(other).__name__))
            raise AssertionError("poison equality must not execute")

        def __int__(self):
            magic_events.append(("int", None))
            raise AssertionError("poison conversion must not execute")

    def poisoning_log(level, message, *args):
        if "正在连接 TCP" in message:
            helper._STRATEGY_RUNTIME_INFLIGHT_REQUESTS = PoisonInflight()

    client = helper._ShortLivedClient(
        "127.0.0.1",
        58620,
        "unit-test-token",
        retries=0,
    )
    monkeypatch.setattr(helper, "_CLIENT", client)
    monkeypatch.setattr(helper, "_log", poisoning_log)
    monkeypatch.setattr(
        helper.socket,
        "create_connection",
        lambda *args, **kwargs: socket_calls.append((args, kwargs)),
    )

    with pytest.raises(RuntimeError, match="代际无效|契约已失效"):
        client.request("broker.place_order", {"amount": 1})

    assert magic_events == []
    assert socket_calls == []
    assert helper._STRATEGY_RUNTIME_ACTIVE_MODE == "FAILED"
    assert helper._CLIENT is None
    assert helper._STRATEGY_RUNTIME_INFLIGHT_REQUESTS == 0
    assert helper._STRATEGY_RUNTIME_REQUEST_LEASES == set()


def test_positive_inflight_without_own_lease_never_reaches_socket(monkeypatch):
    socket_calls = []
    client = helper._ShortLivedClient(
        "127.0.0.1",
        58620,
        "unit-test-token",
        retries=0,
    )
    monkeypatch.setattr(helper, "_CLIENT", client)
    monkeypatch.setattr(helper, "_STRATEGY_RUNTIME_INFLIGHT_REQUESTS", 1)
    monkeypatch.setattr(
        helper.socket,
        "create_connection",
        lambda *args, **kwargs: socket_calls.append((args, kwargs)),
    )

    with pytest.raises(RuntimeError, match="远程请求状态无效"):
        client.request("broker.place_order", {"amount": 1})

    assert socket_calls == []
    assert helper._STRATEGY_RUNTIME_ACTIVE_MODE == "FAILED"
    assert helper._STRATEGY_RUNTIME_INFLIGHT_REQUESTS == 0
    assert helper._STRATEGY_RUNTIME_REQUEST_LEASES == set()


def test_forged_equal_registry_element_cannot_replace_request_identity(
    monkeypatch,
):
    magic_events = []
    socket_calls = []
    poisoned = []

    class PoisonLease:
        def __init__(self, forged_hash):
            self.forged_hash = forged_hash

        def __hash__(self):
            magic_events.append("hash")
            return self.forged_hash

        def __eq__(self, other):
            magic_events.append(("eq", type(other).__name__))
            return True

    def poisoning_log(level, message, *args):
        if "正在连接 TCP" not in message or poisoned:
            return
        poisoned.append(True)
        registry_values = tuple(
            set.__iter__(helper._STRATEGY_RUNTIME_REQUEST_LEASES)
        )
        assert len(registry_values) == 1
        real_token = registry_values[0]
        forged_token = PoisonLease(hash(real_token))
        set.clear(helper._STRATEGY_RUNTIME_REQUEST_LEASES)
        set.add(helper._STRATEGY_RUNTIME_REQUEST_LEASES, forged_token)
        magic_events.clear()

    client = helper._ShortLivedClient(
        "127.0.0.1",
        58620,
        "unit-test-token",
        retries=0,
    )
    monkeypatch.setattr(helper, "_CLIENT", client)
    monkeypatch.setattr(helper, "_log", poisoning_log)
    monkeypatch.setattr(
        helper.socket,
        "create_connection",
        lambda *args, **kwargs: socket_calls.append((args, kwargs)),
    )

    with pytest.raises(RuntimeError, match="代际无效|契约已失效"):
        client.request("broker.place_order", {"amount": 1})

    assert poisoned == [True]
    assert magic_events == []
    assert socket_calls == []
    assert helper._STRATEGY_RUNTIME_ACTIVE_MODE == "FAILED"
    assert helper._CLIENT is None
    assert helper._STRATEGY_RUNTIME_INFLIGHT_REQUESTS == 0
    assert helper._STRATEGY_RUNTIME_REQUEST_LEASES == set()


def test_request_wrapper_uses_definition_time_module_identity_before_socket(
    monkeypatch,
):
    socket_calls = []
    client = helper._ShortLivedClient(
        "127.0.0.1",
        58620,
        "unit-test-token",
        retries=0,
    )
    monkeypatch.setattr(helper, "_CLIENT", client)
    monkeypatch.setattr(helper, "_STRATEGY_RUNTIME_INSTANCE_TOKEN", object())
    monkeypatch.setattr(
        helper,
        "_STRATEGY_RUNTIME_MODULE_GENERATION",
        helper._STRATEGY_RUNTIME_MODULE_GENERATION + 1,
    )
    monkeypatch.setattr(
        helper.socket,
        "create_connection",
        lambda *args, **kwargs: socket_calls.append((args, kwargs)),
    )

    with pytest.raises(RuntimeError, match="helper代际"):
        client.request("broker.place_order", {"amount": 1})

    assert socket_calls == []
    assert helper._STRATEGY_RUNTIME_ACTIVE_MODE == "FAILED"
    assert helper._CLIENT is None
    assert helper._STRATEGY_RUNTIME_INFLIGHT_REQUESTS == 0
    assert helper._get_runtime_commit_anchor() is helper._STRATEGY_RUNTIME_FAILED_ANCHOR


@pytest.mark.parametrize("tamper_target", ["module_generation", "instance_token"])
def test_committed_runtime_rejects_helper_identity_tamper_before_context(
    monkeypatch,
    tamper_target,
):
    context_reads = []

    class CountingRunParams:
        @property
        def type(self):
            context_reads.append("type")
            return "full_backtest"

    class CountingContext:
        run_params = CountingRunParams()

    namespace = {"order": lambda *args, **kwargs: "native-order"}
    context = CountingContext()
    _install(namespace, context, "unused_profile", mode="BACKTEST")
    reads_before = list(context_reads)
    runtime_record = namespace[helper._STRATEGY_RUNTIME_STATE_KEY]
    if tamper_target == "module_generation":
        monkeypatch.setattr(
            helper,
            "_STRATEGY_RUNTIME_MODULE_GENERATION",
            helper._STRATEGY_RUNTIME_MODULE_GENERATION + 1,
        )
    else:
        replacement_token = object()
        monkeypatch.setattr(
            helper,
            "_STRATEGY_RUNTIME_INSTANCE_TOKEN",
            replacement_token,
        )
        runtime_record["runtime_instance_token"] = replacement_token

    with pytest.raises(RuntimeError, match="helper代际|同步原语|进程状态无效"):
        _install(namespace, context, "unused_profile", mode="BACKTEST")

    assert context_reads == reads_before
    assert helper._STRATEGY_RUNTIME_ACTIVE_MODE == "FAILED"
    assert helper._STRATEGY_RUNTIME_PROCESS_SIGNATURE is None
    assert helper._STRATEGY_RUNTIME_CANONICAL_STATE is None
    assert helper._STRATEGY_RUNTIME_COMMIT_CAPSULE is None
    assert helper._STRATEGY_RUNTIME_STATE_KEY not in namespace
    assert helper._get_runtime_commit_anchor() is helper._STRATEGY_RUNTIME_FAILED_ANCHOR


def test_parallel_requests_keep_lease_registry_and_count_consistent(monkeypatch):
    both_entered = threading.Event()
    release_sockets = threading.Event()
    socket_count_lock = threading.Lock()
    socket_count = []
    request_errors = []

    def blocking_socket(*args, **kwargs):
        with socket_count_lock:
            socket_count.append(1)
            if len(socket_count) == 2:
                both_entered.set()
        assert release_sockets.wait(5), "测试未释放并发socket"
        raise OSError("expected concurrent transport stop")

    client = helper._ShortLivedClient(
        "127.0.0.1",
        58620,
        "unit-test-token",
        retries=0,
    )
    monkeypatch.setattr(helper.socket, "create_connection", blocking_socket)

    def run_request():
        try:
            client.request("broker.account", {})
        except BaseException as exc:
            request_errors.append(exc)

    threads = [threading.Thread(target=run_request) for _ in range(2)]
    for thread in threads:
        thread.start()
    assert both_entered.wait(5), "两个请求未同时进入socket"
    assert helper._STRATEGY_RUNTIME_INFLIGHT_REQUESTS == 2
    assert len(helper._STRATEGY_RUNTIME_REQUEST_LEASES) == 2
    release_sockets.set()
    for thread in threads:
        thread.join(5)
        assert not thread.is_alive()

    assert len(request_errors) == 2
    assert helper._STRATEGY_RUNTIME_INFLIGHT_REQUESTS == 0
    assert helper._STRATEGY_RUNTIME_REQUEST_LEASES == set()
    assert helper._STRATEGY_RUNTIME_ACTIVE_MODE is None


@pytest.mark.parametrize("tamper_target", ["active", "transition_owner"])
def test_request_finally_fails_closed_on_same_generation_state_poison(
    monkeypatch,
    tamper_target,
):
    magic_events = []
    socket_calls = []

    class PoisonState:
        def __eq__(self, other):
            magic_events.append(("eq", type(other).__name__))
            raise AssertionError("poison equality must not execute")

        def __ne__(self, other):
            magic_events.append(("ne", type(other).__name__))
            raise AssertionError("poison inequality must not execute")

        def __str__(self):
            magic_events.append(("str", None))
            raise AssertionError("poison formatting must not execute")

    def poisoning_socket(*args, **kwargs):
        socket_calls.append((args, kwargs))
        if tamper_target == "active":
            helper._STRATEGY_RUNTIME_ACTIVE_MODE = PoisonState()
        else:
            helper._STRATEGY_RUNTIME_TRANSITION_OWNER = PoisonState()
        raise OSError("expected transport stop after state poison")

    client = helper._ShortLivedClient(
        "127.0.0.1",
        58620,
        "unit-test-token",
        retries=0,
    )
    monkeypatch.setattr(helper, "_CLIENT", client)
    monkeypatch.setattr(helper.socket, "create_connection", poisoning_socket)

    with pytest.raises(RuntimeError, match="收尾状态无效"):
        client.request("broker.place_order", {"amount": 1})

    assert len(socket_calls) == 1
    assert magic_events == []
    assert helper._STRATEGY_RUNTIME_ACTIVE_MODE == "FAILED"
    assert helper._CLIENT is None
    assert helper._STRATEGY_RUNTIME_CONTRACT_GENERATION == 1
    assert helper._STRATEGY_RUNTIME_INFLIGHT_REQUESTS == 0
    assert helper._STRATEGY_RUNTIME_REQUEST_LEASES == set()


def test_failed_cleanup_quarantines_poison_lease_destructor(monkeypatch):
    destructor_events = []

    class PoisonLease:
        def __del__(self):
            destructor_events.append("destructor")
            helper._set_runtime_commit_anchor = lambda value: destructor_events.append(
                "hijacked_commit"
            )

    poison = PoisonLease()
    set.add(helper._STRATEGY_RUNTIME_REQUEST_LEASES, poison)
    helper._STRATEGY_RUNTIME_INFLIGHT_REQUESTS = 1
    del poison
    gc.collect()
    assert destructor_events == []

    helper._set_runtime_failed_process_state()
    gc.collect()

    assert destructor_events == []
    assert helper._STRATEGY_RUNTIME_ACTIVE_MODE == "FAILED"
    assert helper._STRATEGY_RUNTIME_CONTRACT_GENERATION == 1
    assert helper._STRATEGY_RUNTIME_INFLIGHT_REQUESTS == 0
    assert helper._STRATEGY_RUNTIME_REQUEST_LEASES == set()
    assert helper._get_runtime_commit_anchor() is helper._STRATEGY_RUNTIME_FAILED_ANCHOR


def test_request_finally_rejects_same_generation_reload_flag_poison():
    class Probe:
        @helper._track_runtime_request
        def request(self, action, **kwargs):
            helper._STRATEGY_RUNTIME_RELOAD_IN_PROGRESS = True
            return 123

    with pytest.raises(RuntimeError, match="收尾状态无效"):
        Probe().request("broker.account")

    assert helper._STRATEGY_RUNTIME_ACTIVE_MODE == "FAILED"
    assert helper._STRATEGY_RUNTIME_CONTRACT_GENERATION == 1
    assert helper._STRATEGY_RUNTIME_INFLIGHT_REQUESTS == 0
    assert helper._STRATEGY_RUNTIME_REQUEST_LEASES == set()


def test_request_finally_does_not_rethrow_unrelated_outer_base_exception():
    class Probe:
        @helper._track_runtime_request
        def request(self, action, **kwargs):
            helper._set_runtime_failed_process_state()
            helper._STRATEGY_RUNTIME_RELOAD_IN_PROGRESS = True
            return 123

    outer_error = KeyboardInterrupt("unrelated outer exception context")
    try:
        raise outer_error
    except KeyboardInterrupt:
        with pytest.raises(RuntimeError, match="正在重载") as caught:
            Probe().request("broker.account")

    assert caught.value is not outer_error
    assert helper._STRATEGY_RUNTIME_ACTIVE_MODE == "FAILED"
    assert helper._STRATEGY_RUNTIME_INFLIGHT_REQUESTS == 0
    assert helper._STRATEGY_RUNTIME_REQUEST_LEASES == set()


def test_socket_gate_cleanup_closes_socket_after_interrupted_notification(
    monkeypatch,
):
    class FakeSocket:
        def __init__(self):
            self.closed = False

        def close(self):
            self.closed = True

    fake_socket = FakeSocket()
    condition = helper._STRATEGY_RUNTIME_SOCKET_CONDITION
    real_notify = condition.notify
    notify_calls = []

    def interrupting_notify(*args, **kwargs):
        notify_calls.append(True)
        condition.notify = real_notify
        raise KeyboardInterrupt("socket gate notification interruption")

    condition.notify = interrupting_notify
    client = helper._ShortLivedClient(
        "127.0.0.1",
        58620,
        "unit-test-token",
        retries=0,
    )
    monkeypatch.setattr(helper, "_CLIENT", client)
    monkeypatch.setattr(
        helper.socket,
        "create_connection",
        lambda *args, **kwargs: fake_socket,
    )
    try:
        with pytest.raises(RuntimeError, match="契约已失效"):
            client.request("broker.place_order", {"amount": 1})
    finally:
        condition.notify = real_notify

    assert notify_calls == [True]
    assert fake_socket.closed is True
    assert helper._STRATEGY_RUNTIME_SOCKET_GATE_AUTHORITY[1]() == (False, ())
    assert helper._STRATEGY_RUNTIME_ACTIVE_MODE == "FAILED"
    assert helper._STRATEGY_RUNTIME_INFLIGHT_REQUESTS == 0


def test_async_interrupt_after_socket_attempt_registration_cannot_leak_gate_token():
    script = textwrap.dedent(
        """
        import importlib
        import inspect
        import sys

        from helpers import bullet_trade_jq_remote_helper as helper

        client = helper._ShortLivedClient(
            "127.0.0.1",
            58620,
            "unit-test-token",
            retries=0,
        )
        socket_calls = []
        helper.socket.create_connection = lambda *args, **kwargs: socket_calls.append(
            (args, kwargs)
        )
        source_lines, first_line = inspect.getsourcelines(
            helper._create_runtime_socket_with_lease
        )
        interrupt_line = first_line + next(
            index
            for index, line in enumerate(source_lines)
            if line.strip() == "attempt_started = True"
        )
        target_code = helper._create_runtime_socket_with_lease.__code__
        interrupted = []

        def trace(frame, event, arg):
            if (
                not interrupted
                and frame.f_code is target_code
                and event == "line"
                and frame.f_lineno == interrupt_line
            ):
                interrupted.append(frame.f_lineno)
                sys.settrace(None)
                raise KeyboardInterrupt("attempt registration boundary")
            return trace

        request_error = None
        sys.settrace(trace)
        try:
            client.request("broker.place_order", {"amount": 1})
        except BaseException as exc:
            request_error = exc
        finally:
            sys.settrace(None)

        assert isinstance(request_error, KeyboardInterrupt)
        assert interrupted == [interrupt_line]
        assert socket_calls == []
        assert helper._STRATEGY_RUNTIME_SOCKET_GATE_AUTHORITY[1]() == (False, ())
        assert helper._STRATEGY_RUNTIME_INFLIGHT_REQUESTS == 0
        helper = importlib.reload(helper)
        assert helper._STRATEGY_RUNTIME_ACTIVE_MODE == "FAILED"
        assert helper._STRATEGY_RUNTIME_CONTRACT_GENERATION == 1
        print("ASYNC_ATTEMPT_CLEANUP_OK")
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
    assert "ASYNC_ATTEMPT_CLEANUP_OK" in result.stdout


def test_old_request_finally_quarantines_poison_registry_after_reload():
    script = textwrap.dedent(
        """
        import gc
        import importlib
        import threading

        from helpers import bullet_trade_jq_remote_helper as helper

        body_entered = threading.Event()
        release_body = threading.Event()
        request_errors = []
        destructor_events = []

        class PoisonLease:
            def __del__(self):
                destructor_events.append("destructor")
                helper._STRATEGY_RUNTIME_ACTIVE_MODE = None
                helper._STRATEGY_RUNTIME_RELOAD_IN_PROGRESS = False
                helper._STRATEGY_RUNTIME_CONTRACT_GENERATION = 0
                helper._set_runtime_commit_anchor(None)

        class Probe:
            @helper._track_runtime_request
            def request(self, action, **kwargs):
                body_entered.set()
                if not release_body.wait(5):
                    raise AssertionError("request body was not released")
                return 123

        def request_worker():
            try:
                Probe().request("broker.account")
            except BaseException as exc:
                request_errors.append(exc)

        request_thread = threading.Thread(target=request_worker)
        request_thread.start()
        assert body_entered.wait(5)
        old_registry = helper._STRATEGY_RUNTIME_REQUEST_LEASES
        assert len(old_registry) == 1
        set.clear(old_registry)
        poison = PoisonLease()
        set.add(old_registry, poison)
        del poison
        gc.collect()
        assert destructor_events == []

        helper = importlib.reload(helper)
        assert helper._STRATEGY_RUNTIME_ACTIVE_MODE == "FAILED"
        assert helper._STRATEGY_RUNTIME_CONTRACT_GENERATION == 1
        release_body.set()
        request_thread.join(5)
        assert not request_thread.is_alive()
        gc.collect()

        assert len(request_errors) == 1
        assert isinstance(request_errors[0], RuntimeError)
        assert destructor_events == []
        assert helper._STRATEGY_RUNTIME_ACTIVE_MODE == "FAILED"
        assert helper._STRATEGY_RUNTIME_RELOAD_IN_PROGRESS is True
        assert helper._STRATEGY_RUNTIME_CONTRACT_GENERATION == 1
        assert helper._get_runtime_commit_anchor() is helper._STRATEGY_RUNTIME_FAILED_ANCHOR
        print("OLD_REQUEST_QUARANTINE_OK")
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
    assert "OLD_REQUEST_QUARANTINE_OK" in result.stdout


def test_reload_gate_lock_is_released_after_async_interrupt_on_first_body_line():
    script = textwrap.dedent(
        """
        import importlib
        import pathlib
        import sys
        import threading
        import time

        from helpers import bullet_trade_jq_remote_helper as helper

        socket_entered = threading.Event()
        release_socket = threading.Event()
        request_errors = []
        reload_errors = []
        socket_calls = []
        old_authority = helper._STRATEGY_RUNTIME_SOCKET_GATE_AUTHORITY
        raw_gate_lock = helper._STRATEGY_RUNTIME_SOCKET_LOCK
        source_path = pathlib.Path(helper.__file__)
        source_lines = source_path.read_text(encoding="utf-8").splitlines()
        interrupt_line = 1 + next(
            index
            for index, line in enumerate(source_lines)
            if line.strip() == "gate_snapshot = _reload_gate_authority[1]"
        )
        interrupted = []

        def blocking_socket(*args, **kwargs):
            socket_calls.append((args, kwargs))
            socket_entered.set()
            if not release_socket.wait(5):
                raise AssertionError("socket attempt was not released")
            raise OSError("expected interrupted reload transport stop")

        def trace(frame, event, arg):
            if (
                not interrupted
                and pathlib.Path(frame.f_code.co_filename).resolve() == source_path.resolve()
                and event == "line"
                and frame.f_lineno == interrupt_line
            ):
                interrupted.append(frame.f_lineno)
                raise KeyboardInterrupt("reload gate first body line")
            return trace

        helper.socket.create_connection = blocking_socket
        client = helper._ShortLivedClient(
            "127.0.0.1", 58620, "unit-test-token", retries=0
        )

        def request_worker():
            try:
                client.request("broker.place_order", {"amount": 1})
            except BaseException as exc:
                request_errors.append(exc)

        def reload_worker():
            global helper
            sys.settrace(trace)
            try:
                helper = importlib.reload(helper)
            except BaseException as exc:
                reload_errors.append(exc)
            finally:
                sys.settrace(None)

        request_thread = threading.Thread(target=request_worker)
        reload_thread = threading.Thread(target=reload_worker)
        request_thread.start()
        assert socket_entered.wait(5)
        reload_thread.start()
        deadline = time.monotonic() + 5
        while not old_authority[1]()[0] and time.monotonic() < deadline:
            time.sleep(0.01)
        assert old_authority[1]()[0] is True
        release_socket.set()
        request_thread.join(5)
        reload_thread.join(5)

        assert not request_thread.is_alive()
        assert not reload_thread.is_alive()
        assert interrupted == [interrupt_line]
        assert len(reload_errors) == 1
        assert isinstance(reload_errors[0], KeyboardInterrupt)
        assert len(request_errors) == 1
        assert len(socket_calls) == 1
        assert old_authority[1]() == (True, ())
        assert raw_gate_lock.acquire(False) is True
        raw_gate_lock.release()
        assert helper._STRATEGY_RUNTIME_ACTIVE_MODE == "FAILED"
        assert helper._STRATEGY_RUNTIME_CONTRACT_GENERATION == 1
        print("RELOAD_GATE_ASYNC_RELEASE_OK")
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
    assert "RELOAD_GATE_ASYNC_RELEASE_OK" in result.stdout


def test_old_request_finally_cannot_double_advance_partial_reload_generation():
    script = textwrap.dedent(
        """
        import importlib
        import pathlib
        import sys
        import threading

        from helpers import bullet_trade_jq_remote_helper as helper

        request_entered = threading.Event()
        release_request = threading.Event()
        reload_paused = threading.Event()
        release_reload = threading.Event()
        request_errors = []
        reload_errors = []
        source_path = pathlib.Path(helper.__file__)
        source_lines = source_path.read_text(encoding="utf-8").splitlines()
        pause_line = 1 + next(
            index
            for index, line in enumerate(source_lines)
            if line.startswith("def _create_runtime_commit_anchor(")
        )

        class Probe:
            @helper._track_runtime_request
            def request(self, action, **kwargs):
                request_entered.set()
                if not release_request.wait(5):
                    raise AssertionError("request was not released")
                return 123

        def trace(frame, event, arg):
            if (
                pathlib.Path(frame.f_code.co_filename).resolve() == source_path.resolve()
                and event == "line"
                and frame.f_lineno == pause_line
                and not reload_paused.is_set()
            ):
                reload_paused.set()
                if not release_reload.wait(5):
                    raise AssertionError("reload was not released")
            return trace

        def request_worker():
            try:
                Probe().request("broker.account")
            except BaseException as exc:
                request_errors.append(exc)

        def reload_worker():
            global helper
            sys.settrace(trace)
            try:
                helper = importlib.reload(helper)
            except BaseException as exc:
                reload_errors.append(exc)
            finally:
                sys.settrace(None)

        request_thread = threading.Thread(target=request_worker)
        reload_thread = threading.Thread(target=reload_worker)
        request_thread.start()
        assert request_entered.wait(5)
        reload_thread.start()
        assert reload_paused.wait(5)
        assert helper._STRATEGY_RUNTIME_ACTIVE_MODE == "FAILED"
        assert helper._STRATEGY_RUNTIME_CONTRACT_GENERATION == 1

        release_request.set()
        request_thread.join(5)
        assert not request_thread.is_alive()
        assert len(request_errors) == 1
        assert isinstance(request_errors[0], RuntimeError)
        assert helper._STRATEGY_RUNTIME_CONTRACT_GENERATION == 1

        release_reload.set()
        reload_thread.join(5)
        assert not reload_thread.is_alive()
        assert reload_errors == []
        assert helper._STRATEGY_RUNTIME_ACTIVE_MODE == "FAILED"
        assert helper._STRATEGY_RUNTIME_CONTRACT_GENERATION == 1
        assert helper._get_runtime_commit_anchor() is helper._STRATEGY_RUNTIME_FAILED_ANCHOR
        print("PARTIAL_RELOAD_GENERATION_IDEMPOTENT_OK")
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
    assert "PARTIAL_RELOAD_GENERATION_IDEMPOTENT_OK" in result.stdout


def test_backtest_inflight_precheck_avoids_install_reload_cycle():
    script = textwrap.dedent(
        """
        import importlib
        import threading
        import time

        from helpers import bullet_trade_jq_remote_helper as helper

        request_at_socket_log = threading.Event()
        release_request = threading.Event()
        request_errors = []
        install_errors = []
        socket_calls = []
        paused = []
        context_reads = []

        def pausing_log(level, message, *args, **kwargs):
            if "正在连接 TCP" not in message or paused:
                return
            paused.append(True)
            request_at_socket_log.set()
            if not release_request.wait(5):
                raise AssertionError("request was not released")

        def socket_probe(*args, **kwargs):
            socket_calls.append((args, kwargs))
            raise OSError("socket must not be reached after reload")

        helper._log = pausing_log
        helper.socket.create_connection = socket_probe
        client = helper._ShortLivedClient(
            "127.0.0.1", 58620, "unit-test-token", retries=0
        )

        class RunParams:
            @property
            def type(self):
                global helper
                context_reads.append("type")
                release_request.set()
                time.sleep(0.1)
                helper = importlib.reload(helper)
                return "full_backtest"

        class Context:
            run_params = RunParams()

        def request_worker():
            try:
                client.request("broker.account", {})
            except BaseException as exc:
                request_errors.append(exc)

        def install_worker():
            try:
                helper.install_strategy_runtime(
                    {},
                    context=Context(),
                    profile="unused-profile",
                    mode="BACKTEST",
                    strategy_id="good_etf",
                )
            except BaseException as exc:
                install_errors.append(exc)

        request_thread = threading.Thread(target=request_worker)
        install_thread = threading.Thread(target=install_worker)
        request_thread.start()
        assert request_at_socket_log.wait(5)
        install_thread.start()
        install_thread.join(5)
        assert not install_thread.is_alive()
        release_request.set()
        request_thread.join(5)

        assert not request_thread.is_alive()
        assert len(request_errors) == 1
        assert isinstance(request_errors[0], RuntimeError)
        assert len(install_errors) == 1
        assert isinstance(install_errors[0], RuntimeError)
        assert "远程请求在途" in str(install_errors[0])
        assert context_reads == []
        assert socket_calls == []
        assert helper._STRATEGY_RUNTIME_ACTIVE_MODE == "FAILED"
        print("BACKTEST_INFLIGHT_PRECHECK_OK")
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
    assert "BACKTEST_INFLIGHT_PRECHECK_OK" in result.stdout


def test_install_callback_reload_cannot_reopen_closed_authority_latch():
    script = textwrap.dedent(
        """
        import importlib

        from helpers import bullet_trade_jq_remote_helper as helper

        class RunParams:
            @property
            def type(self):
                global helper
                helper = importlib.reload(helper)
                helper._STRATEGY_RUNTIME_ACTIVE_MODE = None
                helper._STRATEGY_RUNTIME_RELOAD_IN_PROGRESS = False
                return "full_backtest"

        class Context:
            run_params = RunParams()

        namespace = {"order": lambda *args, **kwargs: "native"}
        install_error = None
        try:
            helper.install_strategy_runtime(
                namespace,
                context=Context(),
                profile="unused-profile",
                mode="BACKTEST",
                strategy_id="good_etf",
            )
        except BaseException as exc:
            install_error = exc

        assert isinstance(install_error, RuntimeError)
        assert helper._STRATEGY_RUNTIME_SOCKET_GATE_AUTHORITY[1]() == (True, ())
        assert helper._STRATEGY_RUNTIME_ACTIVE_MODE == "FAILED"
        assert helper._STRATEGY_RUNTIME_RELOAD_IN_PROGRESS is True
        assert helper._STRATEGY_RUNTIME_CONTRACT_GENERATION == 1
        assert helper._STRATEGY_RUNTIME_STATE_KEY not in namespace
        assert helper._CLIENT is None
        assert helper._DATA_CLIENT is None
        assert helper._BROKER_CLIENT is None
        print("INSTALL_AUTHORITY_LATCH_CANNOT_REOPEN_OK")
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
    assert "INSTALL_AUTHORITY_LATCH_CANNOT_REOPEN_OK" in result.stdout


def test_install_callback_cannot_erase_same_generation_reservation():
    namespace = {"order": lambda *args, **kwargs: "native"}
    context_reads = []

    class RunParams:
        @property
        def type(self):
            context_reads.append("type")
            helper._STRATEGY_RUNTIME_ACTIVE_MODE = None
            helper._STRATEGY_RUNTIME_CONTRACT_GENERATION = 0
            helper._STRATEGY_RUNTIME_TRANSITION_OWNER = None
            helper._STRATEGY_RUNTIME_TRANSITION_NAMESPACE = None
            helper._STRATEGY_RUNTIME_TRANSITION_MODE = None
            return "full_backtest"

    class Context:
        run_params = RunParams()

    with pytest.raises(RuntimeError, match="安装lease已失效"):
        helper.install_strategy_runtime(
            namespace,
            context=Context(),
            profile="unused-profile",
            mode="BACKTEST",
            strategy_id="good_etf",
        )

    assert context_reads == ["type"]
    assert helper._STRATEGY_RUNTIME_ACTIVE_MODE == "FAILED"
    assert helper._STRATEGY_RUNTIME_CONTRACT_GENERATION == 1
    assert helper._STRATEGY_RUNTIME_STATE_KEY not in namespace


@pytest.mark.parametrize("field_name", ["profile", "profile_module"])
def test_backtest_rejects_string_callback_before_postcondition_generation_change(
    field_name,
):
    namespace = {"order": lambda *args, **kwargs: "native"}
    callback_events = []

    class StringCallback:
        def __str__(self):
            callback_events.append(field_name)
            helper._STRATEGY_RUNTIME_ACTIVE_MODE = None
            helper._STRATEGY_RUNTIME_CONTRACT_GENERATION = 0
            return "unused-profile"

    arguments = {
        "context": _Context("full_backtest"),
        "profile": "unused-profile",
        "mode": "BACKTEST",
        "strategy_id": "good_etf",
        "profile_module": "unused_profile_module",
    }
    arguments[field_name] = StringCallback()

    with pytest.raises(RuntimeError, match="必须是普通字符串"):
        helper.install_strategy_runtime(namespace, **arguments)

    assert callback_events == []
    assert helper._STRATEGY_RUNTIME_ACTIVE_MODE == "FAILED"
    assert helper._STRATEGY_RUNTIME_STATE_KEY not in namespace
    assert helper._CLIENT is None
    assert helper._DATA_CLIENT is None
    assert helper._BROKER_CLIENT is None


def test_reload_latch_rejects_second_request_while_first_socket_is_active():
    script = textwrap.dedent(
        """
        import importlib
        import threading
        import time

        from helpers import bullet_trade_jq_remote_helper as helper

        first_socket_entered = threading.Event()
        release_first_socket = threading.Event()
        socket_calls = []
        first_errors = []
        second_errors = []
        reload_errors = []
        old_authority = helper._STRATEGY_RUNTIME_SOCKET_GATE_AUTHORITY

        def blocking_socket(*args, **kwargs):
            socket_calls.append((args, kwargs))
            if len(socket_calls) == 1:
                first_socket_entered.set()
                if not release_first_socket.wait(5):
                    raise AssertionError("first socket was not released")
                raise OSError("expected first transport stop")
            raise AssertionError("reload latch allowed a second socket")

        helper.socket.create_connection = blocking_socket
        client = helper._ShortLivedClient(
            "127.0.0.1", 58620, "unit-test-token", retries=0
        )

        def first_worker():
            try:
                client.request("broker.account", {})
            except BaseException as exc:
                first_errors.append(exc)

        def reload_worker():
            global helper
            try:
                helper = importlib.reload(helper)
            except BaseException as exc:
                reload_errors.append(exc)

        first_thread = threading.Thread(target=first_worker)
        reload_thread = threading.Thread(target=reload_worker)
        first_thread.start()
        assert first_socket_entered.wait(5)
        reload_thread.start()
        deadline = time.monotonic() + 5
        while not old_authority[1]()[0] and time.monotonic() < deadline:
            time.sleep(0.01)
        assert old_authority[1]()[0] is True

        try:
            client.request("broker.account", {})
        except BaseException as exc:
            second_errors.append(exc)
        assert len(second_errors) == 1
        assert isinstance(second_errors[0], RuntimeError)
        assert len(socket_calls) == 1

        release_first_socket.set()
        first_thread.join(5)
        reload_thread.join(5)
        assert not first_thread.is_alive()
        assert not reload_thread.is_alive()
        assert len(first_errors) == 1
        assert reload_errors == []
        assert len(socket_calls) == 1
        assert old_authority[1]() == (True, ())
        assert helper._STRATEGY_RUNTIME_ACTIVE_MODE == "FAILED"
        assert helper._STRATEGY_RUNTIME_CONTRACT_GENERATION == 1
        print("RELOAD_LATCH_SECOND_REQUEST_BLOCKED_OK")
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
    assert "RELOAD_LATCH_SECOND_REQUEST_BLOCKED_OK" in result.stdout


def test_reload_interrupted_on_bootstrap_first_line_uses_minimal_fail_closed_fallback():
    script = textwrap.dedent(
        """
        import importlib
        import pathlib
        import sys

        from helpers import bullet_trade_jq_remote_helper as helper

        old_client = helper._ShortLivedClient(
            "127.0.0.1", 58620, "unit-test-token", retries=0
        )
        helper._CLIENT = old_client
        old_generation = helper._STRATEGY_RUNTIME_MODULE_GENERATION
        socket_calls = []
        source_path = pathlib.Path(helper.__file__).resolve()
        source_lines = source_path.read_text(encoding="utf-8").splitlines()
        interrupt_line = 1 + next(
            index
            for index, line in enumerate(source_lines)
            if line.strip() == "bootstrap_error = None"
        )
        interrupted = []

        def socket_probe(*args, **kwargs):
            socket_calls.append((args, kwargs))
            raise AssertionError("bootstrap interruption reached socket")

        def trace(frame, event, arg):
            if (
                not interrupted
                and event == "line"
                and pathlib.Path(frame.f_code.co_filename).resolve() == source_path
                and frame.f_lineno == interrupt_line
            ):
                interrupted.append(frame.f_lineno)
                raise KeyboardInterrupt("bootstrap first line interruption")
            return trace

        helper.socket.create_connection = socket_probe
        reload_error = None
        sys.settrace(trace)
        try:
            importlib.reload(helper)
        except BaseException as exc:
            reload_error = exc
        finally:
            sys.settrace(None)

        request_error = None
        try:
            old_client.request("broker.place_order", {"amount": 1})
        except BaseException as exc:
            request_error = exc

        assert isinstance(reload_error, KeyboardInterrupt)
        assert interrupted == [interrupt_line]
        assert helper._STRATEGY_RUNTIME_MODULE_GENERATION == old_generation
        assert helper._STRATEGY_RUNTIME_ACTIVE_MODE == "FAILED"
        assert helper._STRATEGY_RUNTIME_RELOAD_IN_PROGRESS is True
        assert helper._CLIENT is None
        assert helper._DATA_CLIENT is None
        assert helper._BROKER_CLIENT is None
        assert helper._STRATEGY_RUNTIME_SOCKET_GATE_AUTHORITY[1]() == (True, ())
        assert (
            helper._get_runtime_commit_anchor()
            is helper._STRATEGY_RUNTIME_FAILED_ANCHOR
        )
        assert isinstance(request_error, RuntimeError)
        assert socket_calls == []
        print("BOOTSTRAP_FIRST_LINE_FAIL_CLOSED_OK")
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
    assert "BOOTSTRAP_FIRST_LINE_FAIL_CLOSED_OK" in result.stdout


def test_missing_reload_bootstrap_fallback_cleans_committed_namespace():
    script = textwrap.dedent(
        """
        import importlib

        from helpers import bullet_trade_jq_remote_helper as helper

        class RunParams:
            type = "full_backtest"

        class Context:
            run_params = RunParams()

        namespace = {"order": lambda *args, **kwargs: "NATIVE_ORDER_EXECUTED"}
        helper.install_strategy_runtime(
            namespace,
            context=Context(),
            profile="unused-profile",
            mode="BACKTEST",
            strategy_id="good_etf",
        )
        assert helper._STRATEGY_RUNTIME_STATE_KEY in namespace
        helper._run_runtime_reload_bootstrap = None

        reload_error = None
        try:
            importlib.reload(helper)
        except BaseException as exc:
            reload_error = exc

        assert isinstance(reload_error, RuntimeError)
        assert helper._STRATEGY_RUNTIME_ACTIVE_MODE == "FAILED"
        assert helper._STRATEGY_RUNTIME_RELOAD_IN_PROGRESS is True
        assert helper._STRATEGY_RUNTIME_STATE_KEY not in namespace
        assert helper._STRATEGY_RUNTIME_SOCKET_GATE_AUTHORITY[1]() == (True, ())
        try:
            namespace["order"]("000001.XSHE", 100)
        except RuntimeError as exc:
            assert "FAILED模式禁止交易变更" in str(exc)
        else:
            raise AssertionError("missing bootstrap fallback left native order callable")
        print("MISSING_BOOTSTRAP_NAMESPACE_CLEANUP_OK")
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
    assert "MISSING_BOOTSTRAP_NAMESPACE_CLEANUP_OK" in result.stdout


def test_forged_noop_reload_bootstrap_is_rejected_before_first_import():
    script = textwrap.dedent(
        """
        import importlib
        import pathlib
        import sys

        from helpers import bullet_trade_jq_remote_helper as helper

        old_client = helper._ShortLivedClient(
            "127.0.0.1", 58620, "unit-test-token", retries=0
        )
        helper._CLIENT = old_client
        socket_calls = []
        source_path = pathlib.Path(helper.__file__).resolve()
        source_lines = source_path.read_text(encoding="utf-8").splitlines()
        first_import_line = 1 + next(
            index
            for index, line in enumerate(source_lines)
            if line.strip() == "import _thread"
        )
        import_lines_reached = []

        def socket_probe(*args, **kwargs):
            socket_calls.append((args, kwargs))
            raise AssertionError("forged reload bootstrap reached socket")

        def trace(frame, event, arg):
            if (
                event == "line"
                and pathlib.Path(frame.f_code.co_filename).resolve() == source_path
                and frame.f_lineno == first_import_line
            ):
                import_lines_reached.append(frame.f_lineno)
                raise KeyboardInterrupt("first import must not be reached")
            return trace

        helper.socket.create_connection = socket_probe
        helper._run_runtime_reload_bootstrap = lambda: None
        reload_error = None
        sys.settrace(trace)
        try:
            importlib.reload(helper)
        except BaseException as exc:
            reload_error = exc
        finally:
            sys.settrace(None)

        request_error = None
        try:
            old_client.request("broker.place_order", {"amount": 1})
        except BaseException as exc:
            request_error = exc

        assert isinstance(reload_error, RuntimeError)
        assert import_lines_reached == []
        assert helper._STRATEGY_RUNTIME_ACTIVE_MODE == "FAILED"
        assert helper._STRATEGY_RUNTIME_RELOAD_IN_PROGRESS is True
        assert helper._CLIENT is None
        assert helper._DATA_CLIENT is None
        assert helper._BROKER_CLIENT is None
        assert helper._STRATEGY_RUNTIME_SOCKET_GATE_AUTHORITY[1]() == (True, ())
        assert isinstance(request_error, RuntimeError)
        assert socket_calls == []
        print("FORGED_BOOTSTRAP_REJECTED_BEFORE_IMPORT_OK")
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
    assert "FORGED_BOOTSTRAP_REJECTED_BEFORE_IMPORT_OK" in result.stdout


def test_reload_fallback_uses_namespace_captured_before_commit_anchor_is_cleared():
    script = textwrap.dedent(
        """
        import importlib
        import pathlib
        import sys

        from helpers import bullet_trade_jq_remote_helper as helper

        class RunParams:
            type = "full_backtest"

        class Context:
            run_params = RunParams()

        namespace = {"order": lambda *args, **kwargs: "NATIVE_ORDER_EXECUTED"}
        helper.install_strategy_runtime(
            namespace,
            context=Context(),
            profile="unused-profile",
            mode="BACKTEST",
            strategy_id="good_etf",
        )
        source_path = pathlib.Path(helper.__file__).resolve()
        source_lines = source_path.read_text(encoding="utf-8").splitlines()
        interrupt_line = 1 + next(
            index
            for index, line in enumerate(source_lines)
            if line.strip()
            == "dict.pop(failure_namespace, _STRATEGY_RUNTIME_STATE_KEY, None)"
        )
        interrupted = []

        def trace(frame, event, arg):
            if (
                not interrupted
                and event == "line"
                and pathlib.Path(frame.f_code.co_filename).resolve() == source_path
                and frame.f_lineno == interrupt_line
            ):
                interrupted.append(frame.f_lineno)
                raise KeyboardInterrupt("namespace removal interruption")
            return trace

        reload_error = None
        sys.settrace(trace)
        try:
            importlib.reload(helper)
        except BaseException as exc:
            reload_error = exc
        finally:
            sys.settrace(None)

        assert isinstance(reload_error, KeyboardInterrupt)
        assert interrupted == [interrupt_line]
        assert helper._STRATEGY_RUNTIME_ACTIVE_MODE == "FAILED"
        assert helper._STRATEGY_RUNTIME_STATE_KEY not in namespace
        assert helper._STRATEGY_RUNTIME_SOCKET_GATE_AUTHORITY[1]() == (True, ())
        try:
            namespace["order"]("000001.XSHE", 100)
        except RuntimeError as exc:
            assert "FAILED模式禁止交易变更" in str(exc)
        else:
            raise AssertionError("fallback lost committed namespace identity")
        print("PRECAPTURED_NAMESPACE_FALLBACK_OK")
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
    assert "PRECAPTURED_NAMESPACE_FALLBACK_OK" in result.stdout


def test_reload_interrupted_at_first_import_is_already_failed_closed():
    script = textwrap.dedent(
        """
        import importlib
        import pathlib
        import sys

        from helpers import bullet_trade_jq_remote_helper as helper

        old_client = helper._ShortLivedClient(
            "127.0.0.1", 58620, "unit-test-token", retries=0
        )
        helper._CLIENT = old_client
        old_generation = helper._STRATEGY_RUNTIME_MODULE_GENERATION
        socket_calls = []
        source_path = pathlib.Path(helper.__file__).resolve()
        source_lines = source_path.read_text(encoding="utf-8").splitlines()
        interrupt_line = 1 + next(
            index
            for index, line in enumerate(source_lines)
            if line.strip() == "import _thread"
        )
        interrupted = []

        def socket_probe(*args, **kwargs):
            socket_calls.append((args, kwargs))
            raise AssertionError("pre-import reload interruption reached socket")

        def trace(frame, event, arg):
            if (
                not interrupted
                and event == "line"
                and pathlib.Path(frame.f_code.co_filename).resolve() == source_path
                and frame.f_lineno == interrupt_line
            ):
                interrupted.append(frame.f_lineno)
                raise KeyboardInterrupt("first import interruption")
            return trace

        helper.socket.create_connection = socket_probe
        reload_error = None
        sys.settrace(trace)
        try:
            importlib.reload(helper)
        except BaseException as exc:
            reload_error = exc
        finally:
            sys.settrace(None)

        request_error = None
        try:
            old_client.request("broker.place_order", {"amount": 1})
        except BaseException as exc:
            request_error = exc

        assert isinstance(reload_error, KeyboardInterrupt)
        assert interrupted == [interrupt_line]
        assert helper._STRATEGY_RUNTIME_MODULE_GENERATION == old_generation
        assert helper._STRATEGY_RUNTIME_ACTIVE_MODE == "FAILED"
        assert helper._STRATEGY_RUNTIME_RELOAD_IN_PROGRESS is True
        assert helper._STRATEGY_RUNTIME_CONTRACT_GENERATION == 1
        assert helper._CLIENT is None
        assert helper._DATA_CLIENT is None
        assert helper._BROKER_CLIENT is None
        assert helper._STRATEGY_RUNTIME_SOCKET_GATE_AUTHORITY[1]() == (True, ())
        assert isinstance(request_error, RuntimeError)
        assert socket_calls == []
        print("PRE_IMPORT_RELOAD_FAIL_CLOSED_OK")
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
    assert "PRE_IMPORT_RELOAD_FAIL_CLOSED_OK" in result.stdout


def test_install_final_check_is_linearized_with_concurrent_reload():
    script = textwrap.dedent(
        """
        import importlib
        import inspect
        import threading
        import time

        from helpers import bullet_trade_jq_remote_helper as helper

        class RunParams:
            type = "full_backtest"

        class Context:
            run_params = RunParams()

        namespace = {"order": lambda *args, **kwargs: "native"}
        final_generation_checked = threading.Event()
        release_install = threading.Event()
        install_results = []
        install_errors = []
        reload_errors = []
        real_generation_matches = helper._runtime_module_generation_matches
        old_authority = helper._STRATEGY_RUNTIME_SOCKET_GATE_AUTHORITY

        def pausing_generation_matches(*args, **kwargs):
            result = real_generation_matches(*args, **kwargs)
            caller = inspect.currentframe().f_back
            if (
                result is True
                and caller.f_code.co_name == "call_with_generation_check"
                and helper._STRATEGY_RUNTIME_STATE_KEY in namespace
                and not final_generation_checked.is_set()
            ):
                final_generation_checked.set()
                if not release_install.wait(5):
                    raise AssertionError("install return boundary was not released")
            return result

        helper._runtime_module_generation_matches = pausing_generation_matches

        def install_worker():
            try:
                install_results.append(
                    helper.install_strategy_runtime(
                        namespace,
                        context=Context(),
                        profile="unused-profile",
                        mode="BACKTEST",
                        strategy_id="good_etf",
                    )
                )
            except BaseException as exc:
                install_errors.append(exc)

        def reload_worker():
            global helper
            try:
                helper = importlib.reload(helper)
            except BaseException as exc:
                reload_errors.append(exc)

        install_thread = threading.Thread(target=install_worker)
        reload_thread = threading.Thread(target=reload_worker)
        install_thread.start()
        assert final_generation_checked.wait(5)
        reload_thread.start()

        deadline = time.monotonic() + 5
        while not old_authority[1]()[0] and time.monotonic() < deadline:
            time.sleep(0.01)
        assert old_authority[1]()[0] is True
        assert reload_thread.is_alive()

        release_install.set()
        install_thread.join(5)
        reload_thread.join(5)

        assert not install_thread.is_alive()
        assert not reload_thread.is_alive()
        assert install_results == []
        assert len(install_errors) == 1
        assert isinstance(install_errors[0], RuntimeError)
        assert reload_errors == []
        assert helper._STRATEGY_RUNTIME_ACTIVE_MODE == "FAILED"
        assert helper._STRATEGY_RUNTIME_RELOAD_IN_PROGRESS is True
        assert helper._STRATEGY_RUNTIME_STATE_KEY not in namespace
        try:
            namespace["order"]("000001.XSHE", 100)
        except RuntimeError as exc:
            assert "FAILED模式禁止交易变更" in str(exc)
        else:
            raise AssertionError("reload后不得残留旧namespace交易入口")
        print("INSTALL_RETURN_RELOAD_LINEARIZED_OK")
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
    assert "INSTALL_RETURN_RELOAD_LINEARIZED_OK" in result.stdout


def test_install_owner_release_rechecks_gate_after_final_latch_check():
    script = textwrap.dedent(
        """
        import importlib
        import pathlib
        import sys
        import threading
        import time

        from helpers import bullet_trade_jq_remote_helper as helper

        class RunParams:
            type = "full_backtest"

        class Context:
            run_params = RunParams()

        namespace = {"order": lambda *args, **kwargs: "native"}
        source_path = pathlib.Path(helper.__file__).resolve()
        source_lines = source_path.read_text(encoding="utf-8").splitlines()
        pause_line = 1 + next(
            index
            for index, line in enumerate(source_lines)
            if line == "            return result"
        )
        final_latch_checked = threading.Event()
        release_install = threading.Event()
        install_results = []
        install_errors = []
        reload_errors = []
        old_authority = helper._STRATEGY_RUNTIME_SOCKET_GATE_AUTHORITY

        def trace(frame, event, arg):
            if (
                event == "line"
                and pathlib.Path(frame.f_code.co_filename).resolve() == source_path
                and frame.f_lineno == pause_line
                and not final_latch_checked.is_set()
            ):
                final_latch_checked.set()
                if not release_install.wait(5):
                    raise AssertionError("post-latch install boundary was not released")
            return trace

        def install_worker():
            sys.settrace(trace)
            try:
                install_results.append(
                    helper.install_strategy_runtime(
                        namespace,
                        context=Context(),
                        profile="unused-profile",
                        mode="BACKTEST",
                        strategy_id="good_etf",
                    )
                )
            except BaseException as exc:
                install_errors.append(exc)
            finally:
                sys.settrace(None)

        def reload_worker():
            global helper
            try:
                helper = importlib.reload(helper)
            except BaseException as exc:
                reload_errors.append(exc)

        install_thread = threading.Thread(target=install_worker)
        reload_thread = threading.Thread(target=reload_worker)
        install_thread.start()
        assert final_latch_checked.wait(5)
        reload_thread.start()

        deadline = time.monotonic() + 5
        while not old_authority[1]()[0] and time.monotonic() < deadline:
            time.sleep(0.01)
        assert old_authority[1]()[0] is True
        assert reload_thread.is_alive()

        release_install.set()
        install_thread.join(5)
        reload_thread.join(5)

        assert not install_thread.is_alive()
        assert not reload_thread.is_alive()
        assert install_results == []
        assert len(install_errors) == 1
        assert isinstance(install_errors[0], RuntimeError)
        assert reload_errors == []
        assert helper._STRATEGY_RUNTIME_ACTIVE_MODE == "FAILED"
        assert helper._STRATEGY_RUNTIME_STATE_KEY not in namespace
        print("INSTALL_OWNER_GATE_RECHECK_OK")
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
    assert "INSTALL_OWNER_GATE_RECHECK_OK" in result.stdout


def test_install_finalization_can_linearize_before_reload_gate_close():
    script = textwrap.dedent(
        """
        import importlib
        import pathlib
        import sys
        import threading

        from helpers import bullet_trade_jq_remote_helper as helper

        class RunParams:
            type = "full_backtest"

        class Context:
            run_params = RunParams()

        namespace = {"order": lambda *args, **kwargs: "native"}
        source_path = pathlib.Path(helper.__file__).resolve()
        source_lines = source_path.read_text(encoding="utf-8").splitlines()
        pause_line = 1 + next(
            index
            for index, line in enumerate(source_lines)
            if line.strip().startswith(
                "gate_state = _runtime_socket_gate_authority_snapshot("
            )
        )
        final_gate_entered = threading.Event()
        release_install = threading.Event()
        install_results = []
        install_errors = []
        reload_errors = []
        old_authority = helper._STRATEGY_RUNTIME_SOCKET_GATE_AUTHORITY

        def trace(frame, event, arg):
            if (
                event == "line"
                and pathlib.Path(frame.f_code.co_filename).resolve() == source_path
                and frame.f_lineno == pause_line
                and not final_gate_entered.is_set()
            ):
                final_gate_entered.set()
                if not release_install.wait(5):
                    raise AssertionError("install-wins boundary was not released")
            return trace

        def install_worker():
            sys.settrace(trace)
            try:
                install_results.append(
                    helper.install_strategy_runtime(
                        namespace,
                        context=Context(),
                        profile="unused-profile",
                        mode="BACKTEST",
                        strategy_id="good_etf",
                    )
                )
            except BaseException as exc:
                install_errors.append(exc)
            finally:
                sys.settrace(None)

        def reload_worker():
            global helper
            try:
                helper = importlib.reload(helper)
            except BaseException as exc:
                reload_errors.append(exc)

        install_thread = threading.Thread(target=install_worker)
        reload_thread = threading.Thread(target=reload_worker)
        install_thread.start()
        assert final_gate_entered.wait(5)
        reload_thread.start()
        reload_thread.join(0.2)

        assert reload_thread.is_alive()
        assert old_authority[1]()[0] is False

        release_install.set()
        install_thread.join(5)
        reload_thread.join(5)

        assert not install_thread.is_alive()
        assert not reload_thread.is_alive()
        assert len(install_results) == 1
        assert install_errors == []
        assert reload_errors == []
        assert helper._STRATEGY_RUNTIME_ACTIVE_MODE == "FAILED"
        assert helper._STRATEGY_RUNTIME_STATE_KEY not in namespace
        try:
            namespace["order"]("000001.XSHE", 100)
        except RuntimeError as exc:
            assert "FAILED模式禁止交易变更" in str(exc)
        else:
            raise AssertionError("reload必须清理先完成安装的namespace")
        print("INSTALL_WINS_THEN_RELOAD_CLEANS_OK")
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
    assert "INSTALL_WINS_THEN_RELOAD_CLEANS_OK" in result.stdout


def test_async_interrupt_in_install_finalization_fails_closed_and_releases_locks():
    script = textwrap.dedent(
        """
        import pathlib
        import sys
        import threading

        from helpers import bullet_trade_jq_remote_helper as helper

        class RunParams:
            type = "full_backtest"

        class Context:
            run_params = RunParams()

        namespace = {"order": lambda *args, **kwargs: "native"}
        source_path = pathlib.Path(helper.__file__).resolve()
        source_lines = source_path.read_text(encoding="utf-8").splitlines()
        interrupt_line = 1 + next(
            index
            for index, line in enumerate(source_lines)
            if line.strip().startswith(
                "gate_state = _runtime_socket_gate_authority_snapshot("
            )
        )
        interrupted = []

        def trace(frame, event, arg):
            if (
                event == "line"
                and pathlib.Path(frame.f_code.co_filename).resolve() == source_path
                and frame.f_lineno == interrupt_line
                and not interrupted
            ):
                interrupted.append(frame.f_lineno)
                raise KeyboardInterrupt("install finalization interruption")
            return trace

        install_error = None
        sys.settrace(trace)
        try:
            helper.install_strategy_runtime(
                namespace,
                context=Context(),
                profile="unused-profile",
                mode="BACKTEST",
                strategy_id="good_etf",
            )
        except BaseException as exc:
            install_error = exc
        finally:
            sys.settrace(None)

        lock_results = []

        def probe_locks():
            for lock in (
                helper._STRATEGY_RUNTIME_LOCK,
                helper._STRATEGY_RUNTIME_OWNER_LOCK,
                helper._STRATEGY_RUNTIME_SOCKET_LOCK,
            ):
                acquired = lock.acquire(False)
                lock_results.append(acquired)
                if acquired:
                    lock.release()

        probe_thread = threading.Thread(target=probe_locks)
        probe_thread.start()
        probe_thread.join(5)

        assert isinstance(install_error, KeyboardInterrupt)
        assert interrupted == [interrupt_line]
        assert helper._STRATEGY_RUNTIME_ACTIVE_MODE == "FAILED"
        assert helper._STRATEGY_RUNTIME_STATE_KEY not in namespace
        assert helper._runtime_transition_snapshot() == (True, None, None, None)
        assert not probe_thread.is_alive()
        assert lock_results == [True, True, True]
        print("INSTALL_FINALIZATION_INTERRUPT_FAIL_CLOSED_OK")
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
    assert "INSTALL_FINALIZATION_INTERRUPT_FAIL_CLOSED_OK" in result.stdout


def test_reload_bootstrap_invalid_gate_state_terminates_without_spin():
    runtime_lock = threading.RLock()
    socket_lock = threading.Lock()
    socket_condition = threading.Condition(socket_lock)
    close_calls = []
    failed_publications = []
    errors = []

    def invalid_snapshot():
        return "invalid-gate-state"

    def failing_close():
        close_calls.append(True)
        raise RuntimeError("invalid gate close")

    def unused_attempt(*args, **kwargs):
        raise AssertionError("attempt function must not run")

    authority = (
        object(),
        invalid_snapshot,
        failing_close,
        unused_attempt,
        unused_attempt,
    )

    bootstrap = helper._create_runtime_reload_bootstrap(
        runtime_lock,
        socket_lock,
        socket_condition,
        authority,
        threading.Condition.wait,
        lambda: None,
        lambda: failed_publications.append(True),
        lambda namespace: failed_publications.append(namespace),
        threading.get_ident,
    )

    def worker():
        try:
            bootstrap()
        except BaseException as exc:
            errors.append(exc)

    thread = threading.Thread(target=worker)
    thread.start()
    thread.join(5)

    assert not thread.is_alive()
    assert len(close_calls) == 3
    assert failed_publications == [True]
    assert len(errors) == 1
    assert isinstance(errors[0], RuntimeError)
    assert str(errors[0]) == "invalid gate close"
    assert runtime_lock.acquire(False) is True
    runtime_lock.release()
    assert socket_lock.acquire(False) is True
    socket_lock.release()


def test_reload_from_own_socket_attempt_fails_without_self_wait_deadlock():
    script = textwrap.dedent(
        """
        import importlib
        import threading

        from helpers import bullet_trade_jq_remote_helper as helper

        client = helper._ShortLivedClient(
            "127.0.0.1", 58620, "unit-test-token", retries=0
        )
        socket_calls = []
        request_errors = []

        def reloading_socket(*args, **kwargs):
            socket_calls.append((args, kwargs))
            importlib.reload(helper)
            raise AssertionError("same-thread reload unexpectedly returned")

        helper.socket.create_connection = reloading_socket

        def request_worker():
            try:
                client.request("broker.place_order", {"amount": 1})
            except BaseException as exc:
                request_errors.append(exc)

        thread = threading.Thread(target=request_worker)
        thread.start()
        thread.join(5)

        assert not thread.is_alive()
        assert len(request_errors) == 1
        assert isinstance(request_errors[0], BaseException)
        assert not isinstance(request_errors[0], Exception)
        assert type(request_errors[0]).__name__ == "RuntimeReloadAbort"
        assert len(socket_calls) == 1
        assert helper._STRATEGY_RUNTIME_SOCKET_GATE_AUTHORITY[1]() == (True, ())
        assert helper._STRATEGY_RUNTIME_ACTIVE_MODE == "FAILED"
        assert helper._STRATEGY_RUNTIME_RELOAD_IN_PROGRESS is True
        assert helper._STRATEGY_RUNTIME_INFLIGHT_REQUESTS == 0
        assert helper._STRATEGY_RUNTIME_REQUEST_LEASES == set()
        assert helper._CLIENT is None
        assert helper._DATA_CLIENT is None
        assert helper._BROKER_CLIENT is None
        print("SAME_THREAD_RELOAD_NO_SELF_WAIT_OK")
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
    assert "SAME_THREAD_RELOAD_NO_SELF_WAIT_OK" in result.stdout


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
        assert helper._STRATEGY_RUNTIME_STATE_KEY not in namespace
        try:
            namespace["order"]("000001.XSHE", 100)
        except RuntimeError as exc:
            assert "FAILED模式禁止交易变更" in str(exc)
        else:
            raise AssertionError("reload完成时必须已经清除旧namespace并安装guard")
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


def test_old_client_cannot_reach_socket_while_reload_is_partially_initialised():
    script = textwrap.dedent(
        """
        import importlib
        import threading

        from helpers import bullet_trade_jq_remote_helper as helper

        old_client = helper._ShortLivedClient(
            "127.0.0.1",
            58620,
            "unit-test-token",
            retries=0,
        )
        helper._CLIENT = old_client
        old_generation = helper._STRATEGY_RUNTIME_MODULE_GENERATION
        socket_calls = []
        request_errors = []
        reload_errors = []
        reload_thread_ids = []
        paused = threading.Event()
        resume = threading.Event()
        real_rlock = threading.RLock
        pause_used = []

        def socket_probe(*args, **kwargs):
            socket_calls.append((args, kwargs))
            raise OSError("socket probe must not run")

        def pausing_rlock():
            if (
                reload_thread_ids
                and threading.get_ident() == reload_thread_ids[0]
                and not pause_used
            ):
                pause_used.append(True)
                paused.set()
                if not resume.wait(5):
                    raise AssertionError("reload pause was not released")
            return real_rlock()

        helper.socket.create_connection = socket_probe
        helper.threading.RLock = pausing_rlock

        def reload_worker():
            global helper
            reload_thread_ids.append(threading.get_ident())
            try:
                helper = importlib.reload(helper)
            except BaseException as exc:
                reload_errors.append(exc)

        reload_thread = threading.Thread(target=reload_worker)
        reload_thread.start()
        assert paused.wait(5), "reload did not reach the deterministic pause"
        assert helper._STRATEGY_RUNTIME_MODULE_GENERATION != old_generation
        try:
            old_client.request("broker.place_order", {"amount": 1})
        except BaseException as exc:
            request_errors.append(exc)
        finally:
            resume.set()
        reload_thread.join(5)
        threading.RLock = real_rlock

        assert not reload_thread.is_alive()
        assert reload_errors == []
        assert len(request_errors) == 1
        assert isinstance(request_errors[0], RuntimeError)
        assert socket_calls == []
        assert helper._STRATEGY_RUNTIME_ACTIVE_MODE == "FAILED"
        assert helper._CLIENT is None
        assert helper._DATA_CLIENT is None
        assert helper._BROKER_CLIENT is None
        assert helper._STRATEGY_RUNTIME_INFLIGHT_REQUESTS == 0
        assert helper._STRATEGY_RUNTIME_REQUEST_LEASES == set()
        print("PARTIAL_RELOAD_FAIL_CLOSED_OK")
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
    assert "PARTIAL_RELOAD_FAIL_CLOSED_OK" in result.stdout


def test_reload_waits_for_atomic_lease_check_and_socket_attempt_start():
    script = textwrap.dedent(
        """
        import importlib
        import threading

        from helpers import bullet_trade_jq_remote_helper as helper

        client = helper._ShortLivedClient(
            "127.0.0.1",
            58620,
            "unit-test-token",
            retries=0,
        )
        old_generation = helper._STRATEGY_RUNTIME_MODULE_GENERATION
        socket_entered = threading.Event()
        release_socket = threading.Event()
        reload_started = threading.Event()
        socket_calls = []
        request_errors = []
        reload_errors = []

        def blocking_socket(*args, **kwargs):
            socket_calls.append((args, kwargs))
            socket_entered.set()
            if not release_socket.wait(5):
                raise AssertionError("socket attempt was not released")
            raise OSError("expected atomic socket stop")

        helper.socket.create_connection = blocking_socket

        def request_worker():
            try:
                client.request("broker.place_order", {"amount": 1})
            except BaseException as exc:
                request_errors.append(exc)

        def reload_worker():
            global helper
            reload_started.set()
            try:
                helper = importlib.reload(helper)
            except BaseException as exc:
                reload_errors.append(exc)

        request_thread = threading.Thread(target=request_worker)
        reload_thread = threading.Thread(target=reload_worker)
        request_thread.start()
        assert socket_entered.wait(5), "request did not enter socket boundary"
        reload_thread.start()
        assert reload_started.wait(5)
        reload_thread.join(0.2)

        # socket probe在最终lease检查的同一RLock内；reload尚不能发布FAILED/generation。
        assert reload_thread.is_alive()
        assert helper._STRATEGY_RUNTIME_ACTIVE_MODE is None
        assert helper._STRATEGY_RUNTIME_MODULE_GENERATION == old_generation

        release_socket.set()
        request_thread.join(5)
        reload_thread.join(5)
        assert not request_thread.is_alive()
        assert not reload_thread.is_alive()
        assert reload_errors == []
        assert len(request_errors) == 1
        assert len(socket_calls) == 1
        assert helper._STRATEGY_RUNTIME_ACTIVE_MODE == "FAILED"
        assert helper._STRATEGY_RUNTIME_MODULE_GENERATION == old_generation + 1
        assert helper._STRATEGY_RUNTIME_CONTRACT_GENERATION == 1
        assert helper._STRATEGY_RUNTIME_INFLIGHT_REQUESTS == 0
        assert helper._STRATEGY_RUNTIME_REQUEST_LEASES == set()
        print("RELOAD_SOCKET_LINEARIZATION_OK")
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
    assert "RELOAD_SOCKET_LINEARIZATION_OK" in result.stdout


def test_interrupted_reload_after_generation_publish_remains_failed_closed():
    script = textwrap.dedent(
        """
        import importlib
        import threading

        from helpers import bullet_trade_jq_remote_helper as helper

        old_client = helper._ShortLivedClient(
            "127.0.0.1",
            58620,
            "unit-test-token",
            retries=0,
        )
        helper._CLIENT = old_client
        helper._DATA_CLIENT = object()
        helper._BROKER_CLIENT = object()
        old_generation = helper._STRATEGY_RUNTIME_MODULE_GENERATION
        socket_calls = []
        request_errors = []
        real_rlock = threading.RLock
        interrupted = []

        def interrupting_rlock():
            if not interrupted:
                interrupted.append(True)
                raise KeyboardInterrupt("reload interruption probe")
            return real_rlock()

        def socket_probe(*args, **kwargs):
            socket_calls.append((args, kwargs))
            raise OSError("socket probe must not run")

        helper.socket.create_connection = socket_probe
        helper.threading.RLock = interrupting_rlock
        reload_error = None
        try:
            importlib.reload(helper)
        except BaseException as exc:
            reload_error = exc
        finally:
            threading.RLock = real_rlock

        assert isinstance(reload_error, KeyboardInterrupt)
        assert helper._STRATEGY_RUNTIME_MODULE_GENERATION != old_generation
        try:
            old_client.request("broker.place_order", {"amount": 1})
        except BaseException as exc:
            request_errors.append(exc)

        assert len(request_errors) == 1
        assert isinstance(request_errors[0], RuntimeError)
        assert socket_calls == []
        assert helper._STRATEGY_RUNTIME_ACTIVE_MODE == "FAILED"
        assert helper._CLIENT is None
        assert helper._DATA_CLIENT is None
        assert helper._BROKER_CLIENT is None
        assert helper._STRATEGY_RUNTIME_PROCESS_SIGNATURE is None
        assert helper._STRATEGY_RUNTIME_CANONICAL_STATE is None
        assert helper._STRATEGY_RUNTIME_COMMIT_CAPSULE is None
        assert helper._STRATEGY_RUNTIME_INFLIGHT_REQUESTS == 0
        assert helper._STRATEGY_RUNTIME_REQUEST_LEASES == set()
        assert (
            helper._get_runtime_commit_anchor()
            is helper._STRATEGY_RUNTIME_FAILED_ANCHOR
        )
        print("INTERRUPTED_RELOAD_FAIL_CLOSED_OK")
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
    assert "INTERRUPTED_RELOAD_FAIL_CLOSED_OK" in result.stdout


def test_helper_reload_with_poisoned_counters_still_completes_failed_initialisation():
    script = textwrap.dedent(
        """
        import importlib

        from helpers import bullet_trade_jq_remote_helper as helper

        events = []

        class PoisonCounter:
            def __int__(self):
                events.append("int")
                raise RuntimeError("poison-int")

            def __index__(self):
                events.append("index")
                raise RuntimeError("poison-index")

        old_token = helper._STRATEGY_RUNTIME_INSTANCE_TOKEN
        old_client = object()
        helper._STRATEGY_RUNTIME_ACTIVE_MODE = None
        helper._CLIENT = old_client
        helper._DATA_CLIENT = object()
        helper._BROKER_CLIENT = object()
        helper._STRATEGY_RUNTIME_MODULE_GENERATION = PoisonCounter()
        helper._STRATEGY_RUNTIME_CONTRACT_GENERATION = PoisonCounter()
        helper._STRATEGY_RUNTIME_INFLIGHT_REQUESTS = PoisonCounter()

        helper = importlib.reload(helper)

        assert events == []
        assert helper._STRATEGY_RUNTIME_ACTIVE_MODE == "FAILED"
        assert helper._CLIENT is None
        assert helper._DATA_CLIENT is None
        assert helper._BROKER_CLIENT is None
        assert helper._STRATEGY_RUNTIME_INSTANCE_TOKEN is not old_token
        assert type(helper._STRATEGY_RUNTIME_MODULE_GENERATION) is int
        assert helper._STRATEGY_RUNTIME_MODULE_GENERATION >= 2
        assert type(helper._STRATEGY_RUNTIME_CONTRACT_GENERATION) is int
        assert helper._STRATEGY_RUNTIME_CONTRACT_GENERATION >= 1
        assert helper._STRATEGY_RUNTIME_INFLIGHT_REQUESTS == 0
        assert (
            helper._get_runtime_commit_anchor()
            is helper._STRATEGY_RUNTIME_FAILED_ANCHOR
        )
        print("POISON_RELOAD_FAIL_CLOSED_OK")
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
    assert "POISON_RELOAD_FAIL_CLOSED_OK" in result.stdout


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
            assert exc.__context__ is None
            assert exc.__cause__ is None
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
    with pytest.raises(RuntimeError, match="未知字段；字段名不予回显") as exc_info:
        _install({}, _Context("sim_trade"), module_name)
    assert "password" not in str(exc_info.value)


def test_unknown_profile_field_never_echoes_secret_name(monkeypatch):
    secret = "credential-must-not-appear-in-field-name"
    profile = _valid_profile()
    profile[secret] = "unexpected"
    module_name = _profile_module(
        monkeypatch,
        profiles={"good_etf-prod": profile},
    )

    with pytest.raises(RuntimeError, match="未知字段") as exc_info:
        _install({}, _Context("sim_trade"), module_name)

    assert secret not in str(exc_info.value)
    assert secret not in repr(exc_info.value)


def test_huge_profile_schema_version_has_stable_runtime_error(monkeypatch):
    module_name = _profile_module(monkeypatch, version=10 ** 5000)

    with pytest.raises(RuntimeError, match="schema版本不匹配") as exc_info:
        _install({}, _Context("sim_trade"), module_name)

    assert "<invalid>" in str(exc_info.value)


def test_huge_profile_numeric_value_has_stable_runtime_error(monkeypatch):
    module_name = _profile_module(
        monkeypatch,
        profiles={
            "good_etf-prod": _valid_profile(retry_interval=10 ** 5000),
        },
    )

    with pytest.raises(RuntimeError, match=r"profile\.retry_interval"):
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
    assert secret not in repr(exc_info.value)
    assert exc_info.value.__context__ is None
    assert exc_info.value.__cause__ is None


@pytest.mark.parametrize("attribute", ["PROFILE_SCHEMA_VERSION", "PROFILES"])
@pytest.mark.parametrize("exception_type", [RuntimeError, SystemExit, KeyboardInterrupt])
def test_profile_attribute_base_exception_never_leaks_token(
    monkeypatch,
    attribute,
    exception_type,
):
    secret = "post-import-profile-secret"
    module_name = "unsafe_profile_attributes_{}_{}".format(
        attribute.lower(), exception_type.__name__.lower()
    )

    class UnsafeProfileModule(types.ModuleType):
        def __getattribute__(self, name):
            if name == attribute:
                raise exception_type("profile attribute failed with token={}".format(secret))
            return types.ModuleType.__getattribute__(self, name)

    module = UnsafeProfileModule(module_name)
    module.PROFILE_SCHEMA_VERSION = 1
    module.PROFILES = {"good_etf-prod": _valid_profile()}
    monkeypatch.setitem(sys.modules, module_name, module)

    with pytest.raises(RuntimeError, match="无法加载运行配置模块") as exc_info:
        _install({}, _Context("sim_trade"), module_name, mode="LIVE")

    assert secret not in str(exc_info.value)
    assert secret not in repr(exc_info.value)
    assert exc_info.value.__context__ is None
    assert exc_info.value.__cause__ is None
    assert helper._STRATEGY_RUNTIME_ACTIVE_MODE == "FAILED"


def _assert_runtime_interrupt_probe(script, marker):
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    probe_prelude = """
import types

def _runtime_impl_from_closure(public_function, implementation_name):
    implementations = [
        cell.cell_contents
        for cell in (public_function.__closure__ or ())
        if type(cell.cell_contents) is types.FunctionType
        and cell.cell_contents.__name__ == implementation_name
    ]
    assert len(implementations) == 1, (
        public_function,
        implementation_name,
        implementations,
    )
    return implementations[0]

"""
    result = subprocess.run(
        [
            sys.executable,
            "-X",
            "utf8",
            "-c",
            textwrap.dedent(probe_prelude) + textwrap.dedent(script),
        ],
        cwd=os.getcwd(),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert marker in result.stdout


def test_async_interrupt_immediately_after_install_reservation_clears_owner():
    _assert_runtime_interrupt_probe(
        """
        import sys

        from helpers import bullet_trade_jq_remote_helper as helper

        class RunParams:
            type = "simple_backtest"

        class Context:
            run_params = RunParams()

        namespace = {"order": lambda *args, **kwargs: "native"}
        target_code = _runtime_impl_from_closure(
            helper.install_strategy_runtime,
            "locked_impl",
        ).__code__
        interrupted = []

        def trace(frame, event, arg):
            if frame.f_code is target_code and event == "line" and not interrupted:
                valid, owner, reserved_namespace, mode = (
                    helper._runtime_transition_snapshot()
                )
                if (
                    valid
                    and owner is not None
                    and reserved_namespace is namespace
                    and mode == "BACKTEST"
                ):
                    interrupted.append(frame.f_lineno)
                    raise KeyboardInterrupt("install reservation interruption")
            return trace

        install_error = None
        sys.settrace(trace)
        try:
            helper.install_strategy_runtime(
                namespace,
                context=Context(),
                profile="unused-profile",
                mode="BACKTEST",
                strategy_id="good_etf",
            )
        except BaseException as exc:
            install_error = exc
        finally:
            sys.settrace(None)

        assert isinstance(install_error, KeyboardInterrupt)
        assert len(interrupted) == 1
        assert helper._STRATEGY_RUNTIME_ACTIVE_MODE == "FAILED"
        assert helper._runtime_transition_snapshot() == (True, None, None, None)
        assert helper._STRATEGY_RUNTIME_STATE_KEY not in namespace
        try:
            namespace["order"]("000001.XSHE", 100)
        except RuntimeError as exc:
            assert "FAILED模式禁止交易变更" in str(exc)
        else:
            raise AssertionError("reservation中断后必须安装FAILED交易guard")
        print("INSTALL_RESERVATION_INTERRUPT_CLEAN_OK")
        """,
        "INSTALL_RESERVATION_INTERRUPT_CLEAN_OK",
    )


def test_async_interrupt_after_install_commit_before_return_fails_closed():
    _assert_runtime_interrupt_probe(
        """
        import sys

        from helpers import bullet_trade_jq_remote_helper as helper

        class RunParams:
            type = "full_backtest"

        class Context:
            run_params = RunParams()

        namespace = {"order": lambda *args, **kwargs: "native"}
        target_code = helper.install_strategy_runtime.__code__
        interrupted = []

        def trace(frame, event, arg):
            if (
                frame.f_code is target_code
                and event == "line"
                and frame.f_locals.get("call_completed") is True
                and not interrupted
            ):
                interrupted.append(frame.f_lineno)
                raise KeyboardInterrupt("install committed-return interruption")
            return trace

        install_error = None
        sys.settrace(trace)
        try:
            helper.install_strategy_runtime(
                namespace,
                context=Context(),
                profile="unused-profile",
                mode="BACKTEST",
                strategy_id="good_etf",
            )
        except BaseException as exc:
            install_error = exc
        finally:
            sys.settrace(None)

        assert isinstance(install_error, KeyboardInterrupt)
        assert len(interrupted) == 1
        assert helper._STRATEGY_RUNTIME_ACTIVE_MODE == "FAILED"
        assert helper._runtime_transition_snapshot() == (True, None, None, None)
        assert helper._STRATEGY_RUNTIME_STATE_KEY not in namespace
        try:
            namespace["order"]("000001.XSHE", 100)
        except RuntimeError as exc:
            assert "FAILED模式禁止交易变更" in str(exc)
        else:
            raise AssertionError("提交返回中断后必须撤销namespace并安装FAILED guard")
        print("INSTALL_COMMITTED_RETURN_INTERRUPT_CLEAN_OK")
        """,
        "INSTALL_COMMITTED_RETURN_INTERRUPT_CLEAN_OK",
    )


def test_async_interrupt_immediately_after_request_registration_cleans_registry():
    _assert_runtime_interrupt_probe(
        """
        import sys

        from helpers import bullet_trade_jq_remote_helper as helper

        body_calls = []

        @helper._track_runtime_request
        def request_probe(self, action, *args, **kwargs):
            body_calls.append(True)
            return "unexpected"

        target_code = _runtime_impl_from_closure(
            request_probe,
            "tracked_impl",
        ).__code__
        interrupted = []

        def trace(frame, event, arg):
            registry = helper._runtime_request_registry_snapshot(
                helper._STRATEGY_RUNTIME_REQUEST_LEASES
            )
            if (
                frame.f_code is target_code
                and event == "line"
                and registry
                and not body_calls
                and not interrupted
            ):
                interrupted.append(frame.f_lineno)
                raise KeyboardInterrupt("request registration interruption")
            return trace

        request_error = None
        sys.settrace(trace)
        try:
            request_probe(None, "broker.place_order")
        except BaseException as exc:
            request_error = exc
        finally:
            sys.settrace(None)

        assert isinstance(request_error, KeyboardInterrupt)
        assert len(interrupted) == 1
        assert body_calls == []
        assert helper._STRATEGY_RUNTIME_INFLIGHT_REQUESTS == 0
        assert helper._runtime_request_registry_snapshot(
            helper._STRATEGY_RUNTIME_REQUEST_LEASES
        ) == ()
        print("REQUEST_REGISTRATION_INTERRUPT_CLEAN_OK")
        """,
        "REQUEST_REGISTRATION_INTERRUPT_CLEAN_OK",
    )


def test_async_interrupt_on_request_cleanup_entry_cleans_registry():
    _assert_runtime_interrupt_probe(
        """
        import sys

        from helpers import bullet_trade_jq_remote_helper as helper

        body_completed = []

        @helper._track_runtime_request
        def request_probe(self, action, *args, **kwargs):
            body_completed.append(True)
            return "completed"

        target_code = _runtime_impl_from_closure(
            request_probe,
            "tracked_impl",
        ).__code__
        interrupted = []

        def trace(frame, event, arg):
            registry = helper._runtime_request_registry_snapshot(
                helper._STRATEGY_RUNTIME_REQUEST_LEASES
            )
            if (
                frame.f_code is target_code
                and event == "line"
                and body_completed
                and registry
                and not interrupted
            ):
                interrupted.append(frame.f_lineno)
                raise KeyboardInterrupt("request cleanup interruption")
            return trace

        request_error = None
        sys.settrace(trace)
        try:
            request_probe(None, "broker.place_order")
        except BaseException as exc:
            request_error = exc
        finally:
            sys.settrace(None)

        assert isinstance(request_error, KeyboardInterrupt)
        assert len(interrupted) == 1
        assert body_completed == [True]
        assert helper._STRATEGY_RUNTIME_INFLIGHT_REQUESTS == 0
        assert helper._runtime_request_registry_snapshot(
            helper._STRATEGY_RUNTIME_REQUEST_LEASES
        ) == ()
        print("REQUEST_CLEANUP_INTERRUPT_CLEAN_OK")
        """,
        "REQUEST_CLEANUP_INTERRUPT_CLEAN_OK",
    )


def test_async_interrupt_on_socket_cleanup_entry_cleans_gate_attempt():
    _assert_runtime_interrupt_probe(
        """
        import inspect
        import sys

        from helpers import bullet_trade_jq_remote_helper as helper

        class FakeSocket:
            def __init__(self):
                self.closed = False

            def close(self):
                self.closed = True

        fake_socket = FakeSocket()
        helper.socket.create_connection = lambda *args, **kwargs: fake_socket
        client = helper._ShortLivedClient(
            "127.0.0.1",
            58620,
            "unit-test-token",
            retries=0,
        )

        target_code = helper._create_runtime_socket_with_lease.__code__
        source_lines, first_line = inspect.getsourcelines(target_code)
        finally_index = max(
            index
            for index, line in enumerate(source_lines)
            if line.strip() == "finally:"
        )
        finally_indent = len(source_lines[finally_index]) - len(
            source_lines[finally_index].lstrip()
        )
        cleanup_index = next(
            index
            for index in range(finally_index + 1, len(source_lines))
            if source_lines[index].strip()
            and len(source_lines[index]) - len(source_lines[index].lstrip())
            > finally_indent
        )
        cleanup_line = first_line + cleanup_index
        interrupted = []

        def trace(frame, event, arg):
            if (
                frame.f_code is target_code
                and event == "line"
                and frame.f_lineno == cleanup_line
                and not interrupted
            ):
                interrupted.append(frame.f_lineno)
                raise KeyboardInterrupt("socket cleanup interruption")
            return trace

        request_error = None
        sys.settrace(trace)
        try:
            client.request("broker.place_order", {"amount": 1})
        except BaseException as exc:
            request_error = exc
        finally:
            sys.settrace(None)

        assert isinstance(request_error, KeyboardInterrupt)
        assert interrupted == [cleanup_line]
        assert helper._STRATEGY_RUNTIME_SOCKET_GATE_AUTHORITY[1]() == (False, ())
        assert helper._STRATEGY_RUNTIME_INFLIGHT_REQUESTS == 0
        assert helper._runtime_request_registry_snapshot(
            helper._STRATEGY_RUNTIME_REQUEST_LEASES
        ) == ()
        assert fake_socket.closed is True
        print("SOCKET_CLEANUP_INTERRUPT_CLEAN_OK")
        """,
        "SOCKET_CLEANUP_INTERRUPT_CLEAN_OK",
    )


def test_async_interrupt_after_configure_publication_clears_clients():
    _assert_runtime_interrupt_probe(
        """
        import sys

        from helpers import bullet_trade_jq_remote_helper as helper

        target_code = _runtime_impl_from_closure(
            helper.configure,
            "locked_impl",
        ).__code__
        interrupted = []

        def trace(frame, event, arg):
            if (
                frame.f_code is target_code
                and event == "line"
                and helper._BROKER_CLIENT is not None
                and not interrupted
            ):
                interrupted.append(frame.f_lineno)
                raise KeyboardInterrupt("configure publication interruption")
            return trace

        configure_error = None
        sys.settrace(trace)
        try:
            helper.configure(
                "127.0.0.1",
                "unit-test-token",
                debug=False,
            )
        except BaseException as exc:
            configure_error = exc
        finally:
            sys.settrace(None)

        assert isinstance(configure_error, KeyboardInterrupt)
        assert len(interrupted) == 1
        assert helper._STRATEGY_RUNTIME_ACTIVE_MODE == "FAILED"
        assert helper._CLIENT is None
        assert helper._DATA_CLIENT is None
        assert helper._BROKER_CLIENT is None
        assert helper._runtime_transition_snapshot() == (True, None, None, None)
        print("CONFIGURE_PUBLICATION_INTERRUPT_CLEAN_OK")
        """,
        "CONFIGURE_PUBLICATION_INTERRUPT_CLEAN_OK",
    )


def test_async_interrupt_on_install_impl_return_handoff_fails_closed():
    _assert_runtime_interrupt_probe(
        """
        import sys

        from helpers import bullet_trade_jq_remote_helper as helper

        class RunParams:
            type = "simple_backtest"

        class Context:
            run_params = RunParams()

        namespace = {"order": lambda *args, **kwargs: "native"}
        target_code = _runtime_impl_from_closure(
            helper.install_strategy_runtime,
            "locked_impl",
        ).__code__
        interrupted = []

        def trace(frame, event, arg):
            if (
                frame.f_code is target_code
                and event == "return"
                and not interrupted
            ):
                interrupted.append(frame.f_lineno)
                raise KeyboardInterrupt("install impl return handoff interruption")
            return trace

        install_error = None
        sys.settrace(trace)
        try:
            helper.install_strategy_runtime(
                namespace,
                context=Context(),
                profile="unused-profile",
                mode="BACKTEST",
                strategy_id="good_etf",
            )
        except BaseException as exc:
            install_error = exc
        finally:
            sys.settrace(None)

        assert isinstance(install_error, KeyboardInterrupt)
        assert len(interrupted) == 1
        assert helper._STRATEGY_RUNTIME_ACTIVE_MODE == "FAILED"
        assert helper._runtime_transition_snapshot() == (True, None, None, None)
        assert helper._STRATEGY_RUNTIME_STATE_KEY not in namespace
        try:
            namespace["order"]("000001.XSHE", 100)
        except RuntimeError as exc:
            assert "FAILED模式禁止交易变更" in str(exc)
        else:
            raise AssertionError("impl return中断后必须撤销namespace并安装FAILED guard")
        print("INSTALL_IMPL_RETURN_HANDOFF_INTERRUPT_CLEAN_OK")
        """,
        "INSTALL_IMPL_RETURN_HANDOFF_INTERRUPT_CLEAN_OK",
    )


def test_async_interrupt_on_request_impl_return_handoff_fails_closed():
    _assert_runtime_interrupt_probe(
        """
        import sys

        from helpers import bullet_trade_jq_remote_helper as helper

        @helper._track_runtime_request
        def request_probe(self, action, *args, **kwargs):
            return "completed"

        target_code = _runtime_impl_from_closure(
            request_probe,
            "tracked_impl",
        ).__code__
        interrupted = []

        def trace(frame, event, arg):
            if (
                frame.f_code is target_code
                and event == "return"
                and not interrupted
            ):
                interrupted.append(frame.f_lineno)
                raise KeyboardInterrupt("request impl return handoff interruption")
            return trace

        request_error = None
        sys.settrace(trace)
        try:
            request_probe(None, "broker.place_order")
        except BaseException as exc:
            request_error = exc
        finally:
            sys.settrace(None)

        assert isinstance(request_error, KeyboardInterrupt)
        assert len(interrupted) == 1
        assert helper._STRATEGY_RUNTIME_ACTIVE_MODE == "FAILED"
        assert helper._STRATEGY_RUNTIME_INFLIGHT_REQUESTS == 0
        assert helper._runtime_request_registry_snapshot(
            helper._STRATEGY_RUNTIME_REQUEST_LEASES
        ) == ()
        print("REQUEST_IMPL_RETURN_HANDOFF_INTERRUPT_CLEAN_OK")
        """,
        "REQUEST_IMPL_RETURN_HANDOFF_INTERRUPT_CLEAN_OK",
    )


def test_async_interrupt_after_failed_request_token_discard_repairs_counter():
    _assert_runtime_interrupt_probe(
        """
        import sys

        from helpers import bullet_trade_jq_remote_helper as helper

        body_entered = []

        @helper._track_runtime_request
        def request_probe(self, action, *args, **kwargs):
            body_entered.append(True)
            raise ValueError("expected request body failure")

        target_code = _runtime_impl_from_closure(
            request_probe,
            "tracked_impl",
        ).__code__
        interrupted = []

        def trace(frame, event, arg):
            if frame.f_code is target_code and event == "line" and not interrupted:
                registry = helper._runtime_request_registry_snapshot(
                    helper._STRATEGY_RUNTIME_REQUEST_LEASES
                )
                if (
                    body_entered
                    and registry == ()
                    and helper._STRATEGY_RUNTIME_INFLIGHT_REQUESTS == 1
                ):
                    interrupted.append(frame.f_lineno)
                    raise KeyboardInterrupt("failed request cleanup counter interruption")
            return trace

        request_error = None
        sys.settrace(trace)
        try:
            request_probe(None, "broker.place_order")
        except BaseException as exc:
            request_error = exc
        finally:
            sys.settrace(None)

        assert isinstance(request_error, KeyboardInterrupt)
        assert len(interrupted) == 1
        assert body_entered == [True]
        assert helper._STRATEGY_RUNTIME_ACTIVE_MODE == "FAILED"
        assert helper._STRATEGY_RUNTIME_INFLIGHT_REQUESTS == 0
        assert helper._runtime_request_registry_snapshot(
            helper._STRATEGY_RUNTIME_REQUEST_LEASES
        ) == ()
        print("FAILED_REQUEST_DISCARD_COUNTER_INTERRUPT_CLEAN_OK")
        """,
        "FAILED_REQUEST_DISCARD_COUNTER_INTERRUPT_CLEAN_OK",
    )


def test_async_interrupt_on_socket_return_handoff_closes_unclaimed_socket():
    _assert_runtime_interrupt_probe(
        """
        import sys

        from helpers import bullet_trade_jq_remote_helper as helper

        class FakeSocket:
            def __init__(self):
                self.closed = False

            def close(self):
                self.closed = True

        fake_socket = FakeSocket()
        helper.socket.create_connection = lambda *args, **kwargs: fake_socket
        client = helper._ShortLivedClient(
            "127.0.0.1",
            58620,
            "unit-test-token",
            retries=0,
        )
        target_code = helper._create_runtime_socket_with_lease.__code__
        interrupted = []

        def trace(frame, event, arg):
            if (
                frame.f_code is target_code
                and event == "return"
                and not interrupted
            ):
                interrupted.append(frame.f_lineno)
                raise KeyboardInterrupt("socket return handoff interruption")
            return trace

        request_error = None
        sys.settrace(trace)
        try:
            client.request("broker.place_order", {"amount": 1})
        except BaseException as exc:
            request_error = exc
        finally:
            sys.settrace(None)

        assert isinstance(request_error, KeyboardInterrupt)
        assert len(interrupted) == 1
        assert fake_socket.closed is True
        assert helper._STRATEGY_RUNTIME_SOCKET_GATE_AUTHORITY[1]() == (False, ())
        assert helper._STRATEGY_RUNTIME_INFLIGHT_REQUESTS == 0
        assert helper._runtime_request_registry_snapshot(
            helper._STRATEGY_RUNTIME_REQUEST_LEASES
        ) == ()
        assert helper._STRATEGY_RUNTIME_ACTIVE_MODE == "FAILED"
        print("SOCKET_RETURN_HANDOFF_INTERRUPT_CLEAN_OK")
        """,
        "SOCKET_RETURN_HANDOFF_INTERRUPT_CLEAN_OK",
    )


def test_async_interrupt_on_request_socket_cleanup_entry_fails_closed():
    _assert_runtime_interrupt_probe(
        """
        import inspect
        import sys

        from helpers import bullet_trade_jq_remote_helper as helper

        class FakeSocket:
            def __init__(self):
                self.closed = False

            def settimeout(self, timeout):
                pass

            def close(self):
                self.closed = True

        fake_socket = FakeSocket()
        sent_messages = []
        helper.socket.create_connection = lambda *args, **kwargs: fake_socket
        client = helper._ShortLivedClient(
            "127.0.0.1",
            58620,
            "unit-test-token",
            retries=0,
        )
        client._send = lambda sock, message: sent_messages.append(message)

        def fake_recv(sock):
            if sent_messages[-1]["type"] == "handshake":
                return {"type": "handshake_ack", "protocol": 1}
            return {
                "type": "response",
                "id": sent_messages[-1]["id"],
                "payload": {"accepted": True},
            }

        client._recv = fake_recv
        target_code = helper._ShortLivedClient.request.__wrapped__.__code__
        source_lines, first_line = inspect.getsourcelines(target_code)
        cleanup_index = next(
            index
            for index, line in enumerate(source_lines)
            if line.strip() == "pending_socket = _runtime_lease_resource_state[0]"
        )
        cleanup_line = first_line + cleanup_index
        interrupted = []

        def trace(frame, event, arg):
            if (
                frame.f_code is target_code
                and event == "line"
                and frame.f_lineno == cleanup_line
                and not interrupted
            ):
                interrupted.append(frame.f_lineno)
                raise KeyboardInterrupt("request socket cleanup interruption")
            return trace

        request_error = None
        sys.settrace(trace)
        try:
            client.request("broker.place_order", {"amount": 1})
        except BaseException as exc:
            request_error = exc
        finally:
            sys.settrace(None)

        assert isinstance(request_error, KeyboardInterrupt)
        assert interrupted == [cleanup_line]
        assert fake_socket.closed is True
        assert helper._STRATEGY_RUNTIME_SOCKET_GATE_AUTHORITY[1]() == (False, ())
        assert helper._STRATEGY_RUNTIME_INFLIGHT_REQUESTS == 0
        assert helper._runtime_request_registry_snapshot(
            helper._STRATEGY_RUNTIME_REQUEST_LEASES
        ) == ()
        assert helper._STRATEGY_RUNTIME_ACTIVE_MODE == "FAILED"
        print("REQUEST_SOCKET_CLEANUP_INTERRUPT_FAIL_CLOSED_OK")
        """,
        "REQUEST_SOCKET_CLEANUP_INTERRUPT_FAIL_CLOSED_OK",
    )


def test_legacy_backtest_context_callback_cannot_publish_remote_clients():
    native_order = lambda *args, **kwargs: "native"
    namespace = {"order": native_order}

    class Context:
        @property
        def run_params(self):
            helper.configure(
                "127.0.0.1",
                "unit-test-token",
                debug=False,
            )
            return {"type": "simple_backtest"}

    with pytest.raises(RuntimeError):
        helper.install_jq_compat(
            namespace,
            context=Context(),
            host="127.0.0.1",
            token="unit-test-token",
            debug=False,
        )
    assert helper._STRATEGY_RUNTIME_ACTIVE_MODE == "FAILED"
    assert helper._CLIENT is None
    assert helper._DATA_CLIENT is None
    assert helper._BROKER_CLIENT is None
    assert helper._STRATEGY_RUNTIME_TRANSITION_OWNER is None
    assert helper._STRATEGY_RUNTIME_TRANSITION_NAMESPACE is None
    assert helper._STRATEGY_RUNTIME_TRANSITION_MODE is None
    with pytest.raises(RuntimeError, match="FAILED"):
        namespace["order"]("000001.XSHE", 100)


def test_configure_callback_cannot_commit_runtime_then_republish_clients():
    namespace = {"order": lambda *args, **kwargs: "native"}

    class RunParams:
        type = "simple_backtest"

    class Context:
        run_params = RunParams()

    class ReentrantDebug:
        def __init__(self):
            self.calls = 0

        def __bool__(self):
            self.calls += 1
            if self.calls == 1:
                helper.install_strategy_runtime(
                    namespace,
                    context=Context(),
                    profile="unused-profile",
                    mode="BACKTEST",
                    strategy_id="good_etf",
                )
            return True

    with pytest.raises(RuntimeError):
        helper.configure(
            "127.0.0.1",
            "unit-test-token",
            debug=ReentrantDebug(),
        )

    assert not (
        helper._STRATEGY_RUNTIME_ACTIVE_MODE == "BACKTEST"
        and helper._CLIENT is not None
        and helper._DATA_CLIENT is not None
        and helper._BROKER_CLIENT is not None
    )


def test_legacy_run_type_comparison_callback_cannot_mix_runtime_and_clients():
    namespace = {"order": lambda *args, **kwargs: "native"}

    class BacktestRunParams:
        type = "simple_backtest"

    class BacktestContext:
        run_params = BacktestRunParams()

    class ReentrantRunType:
        installed = False

        def __eq__(self, other):
            if not self.installed:
                self.installed = True
                namespace.pop(helper._JQ_COMPAT_STATE_KEY, None)
                helper.install_strategy_runtime(
                    namespace,
                    context=BacktestContext(),
                    profile="unused-profile",
                    mode="BACKTEST",
                    strategy_id="good_etf",
                )
            return other == "sim_trade"

        def __ne__(self, other):
            return not self.__eq__(other)

    class Context:
        run_params = {"type": ReentrantRunType()}

    with pytest.raises(RuntimeError):
        helper.install_jq_compat(
            namespace,
            context=Context(),
            host="127.0.0.1",
            token="unit-test-token",
            debug=False,
        )
    assert not (
        helper._STRATEGY_RUNTIME_ACTIVE_MODE == "BACKTEST"
        and helper._CLIENT is not None
        and helper._DATA_CLIENT is not None
        and helper._BROKER_CLIENT is not None
    )


def test_namespace_migration_failure_and_reload_guard_original_namespace():
    _assert_runtime_interrupt_probe(
        """
        import importlib

        from helpers import bullet_trade_jq_remote_helper as helper

        class RunParams:
            type = "simple_backtest"

        class Context:
            run_params = RunParams()

        context = Context()
        original_namespace = {"order": lambda *args, **kwargs: "original"}
        current_namespace = {"order": lambda *args, **kwargs: "current"}
        helper.install_strategy_runtime(
            original_namespace,
            context=context,
            profile="unused-profile",
            mode="BACKTEST",
            strategy_id="good_etf",
        )
        runtime_record = original_namespace.pop(
            helper._STRATEGY_RUNTIME_STATE_KEY
        )
        current_namespace[helper._STRATEGY_RUNTIME_STATE_KEY] = runtime_record

        migration_error = None
        try:
            helper.install_strategy_runtime(
                current_namespace,
                context=context,
                profile="unused-profile",
                mode="BACKTEST",
                strategy_id="good_etf",
            )
        except BaseException as exc:
            migration_error = exc

        assert isinstance(migration_error, RuntimeError)
        assert helper._STRATEGY_RUNTIME_ACTIVE_MODE == "FAILED"
        assert helper._STRATEGY_RUNTIME_STATE_KEY not in original_namespace
        assert helper._STRATEGY_RUNTIME_STATE_KEY not in current_namespace
        for namespace in (original_namespace, current_namespace):
            try:
                namespace["order"]("000001.XSHE", 100)
            except RuntimeError as exc:
                assert "FAILED模式禁止交易变更" in str(exc)
            else:
                raise AssertionError("namespace迁移失败必须同时guard原/当前namespace")

        helper = importlib.reload(helper)
        assert helper._STRATEGY_RUNTIME_ACTIVE_MODE == "FAILED"
        for namespace in (original_namespace, current_namespace):
            assert helper._STRATEGY_RUNTIME_STATE_KEY not in namespace
            try:
                namespace["order"]("000001.XSHE", 100)
            except RuntimeError as exc:
                assert "FAILED模式禁止交易变更" in str(exc)
            else:
                raise AssertionError("reload后原/当前namespace都必须保持FAILED guard")
        print("NAMESPACE_MIGRATION_RELOAD_ORIGINAL_GUARD_OK")
        """,
        "NAMESPACE_MIGRATION_RELOAD_ORIGINAL_GUARD_OK",
    )


def test_async_interrupt_after_tls_holder_update_closes_wrapped_socket():
    _assert_runtime_interrupt_probe(
        """
        import inspect
        import sys

        from helpers import bullet_trade_jq_remote_helper as helper

        class RawSocket:
            def __init__(self):
                self.closed = False

            def close(self):
                self.closed = True

        class WrappedSocket:
            def __init__(self, raw_socket):
                self.raw_socket = raw_socket
                self.closed = False

            def close(self):
                self.closed = True
                self.raw_socket.close()

        raw_socket = RawSocket()
        wrapped_socket = WrappedSocket(raw_socket)

        class TLSContext:
            def wrap_socket(self, sock, server_hostname=None):
                assert sock is raw_socket
                return wrapped_socket

        helper.socket.create_connection = lambda *args, **kwargs: raw_socket
        helper.ssl.create_default_context = lambda **kwargs: TLSContext()
        client = helper._ShortLivedClient(
            "127.0.0.1",
            58620,
            "unit-test-token",
            tls_cert="server.pem",
            retries=0,
        )
        tracked_impl = _runtime_impl_from_closure(
            helper._ShortLivedClient.request,
            "tracked_impl",
        )
        request_body = _runtime_impl_from_closure(tracked_impl, "request")
        target_code = request_body.__code__
        source_lines, first_line = inspect.getsourcelines(target_code)
        handoff_index = next(
            index
            for index, line in enumerate(source_lines)
                if line.strip() == "sock = wrapped_socket"
        )
        handoff_line = first_line + handoff_index
        interrupted = []

        def trace(frame, event, arg):
            if (
                frame.f_code is target_code
                and event == "line"
                and frame.f_lineno == handoff_line
                and not interrupted
            ):
                interrupted.append(frame.f_lineno)
                raise KeyboardInterrupt("TLS holder handoff interruption")
            return trace

        request_error = None
        sys.settrace(trace)
        try:
            client.request("broker.place_order", {"amount": 1})
        except BaseException as exc:
            request_error = exc
        finally:
            sys.settrace(None)

        assert isinstance(request_error, KeyboardInterrupt)
        assert interrupted == [handoff_line]
        assert wrapped_socket.closed is True
        assert raw_socket.closed is True
        assert helper._STRATEGY_RUNTIME_SOCKET_GATE_AUTHORITY[1]() == (False, ())
        assert helper._STRATEGY_RUNTIME_INFLIGHT_REQUESTS == 0
        assert helper._runtime_request_registry_snapshot(
            helper._STRATEGY_RUNTIME_REQUEST_LEASES
        ) == ()
        assert helper._STRATEGY_RUNTIME_ACTIVE_MODE == "FAILED"
        print("TLS_HOLDER_HANDOFF_INTERRUPT_CLOSE_OK")
        """,
        "TLS_HOLDER_HANDOFF_INTERRUPT_CLOSE_OK",
    )


def test_mutation_send_base_exception_fails_closed_without_retry(monkeypatch):
    class FakeSocket:
        def __init__(self):
            self.closed = False
            self.sent = []

        def settimeout(self, timeout):
            pass

        def sendall(self, payload):
            self.sent.append(payload)
            if len(self.sent) == 2:
                raise KeyboardInterrupt("mutation send interrupted")

        def close(self):
            self.closed = True

    fake_socket = FakeSocket()
    connection_calls = []
    monkeypatch.setattr(helper, "_log", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        helper.socket,
        "create_connection",
        lambda *args, **kwargs: connection_calls.append((args, kwargs))
        or fake_socket,
    )
    client = helper._ShortLivedClient(
        "127.0.0.1",
        58620,
        "unit-test-token",
        retries=3,
    )
    monkeypatch.setattr(
        client,
        "_recv",
        lambda sock: {"type": "handshake_ack", "protocol": 1},
    )

    with pytest.raises(KeyboardInterrupt, match="mutation send interrupted"):
        client.request("broker.place_order", {"amount": 1})

    assert len(connection_calls) == 1
    assert len(fake_socket.sent) == 2
    assert fake_socket.closed is True
    assert helper._STRATEGY_RUNTIME_ACTIVE_MODE == "FAILED"
    assert helper._STRATEGY_RUNTIME_SOCKET_GATE_AUTHORITY[1]() == (False, ())
    assert helper._STRATEGY_RUNTIME_INFLIGHT_REQUESTS == 0
    assert helper._runtime_request_registry_snapshot(
        helper._STRATEGY_RUNTIME_REQUEST_LEASES
    ) == ()


def test_mutation_response_base_exception_fails_closed_without_retry(monkeypatch):
    class FakeSocket:
        def __init__(self):
            self.closed = False
            self.sent = []

        def settimeout(self, timeout):
            pass

        def sendall(self, payload):
            self.sent.append(payload)

        def close(self):
            self.closed = True

    fake_socket = FakeSocket()
    connection_calls = []
    receive_calls = []
    monkeypatch.setattr(helper, "_log", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        helper.socket,
        "create_connection",
        lambda *args, **kwargs: connection_calls.append((args, kwargs))
        or fake_socket,
    )
    client = helper._ShortLivedClient(
        "127.0.0.1",
        58620,
        "unit-test-token",
        retries=3,
    )

    def receive(sock):
        receive_calls.append(sock)
        if len(receive_calls) == 1:
            return {"type": "handshake_ack", "protocol": 1}
        raise KeyboardInterrupt("mutation response interrupted")

    monkeypatch.setattr(client, "_recv", receive)

    with pytest.raises(KeyboardInterrupt, match="mutation response interrupted"):
        client.request("broker.place_order", {"amount": 1})

    assert len(connection_calls) == 1
    assert len(fake_socket.sent) == 2
    assert len(receive_calls) == 2
    assert fake_socket.closed is True
    assert helper._STRATEGY_RUNTIME_ACTIVE_MODE == "FAILED"
    assert helper._STRATEGY_RUNTIME_SOCKET_GATE_AUTHORITY[1]() == (False, ())
    assert helper._STRATEGY_RUNTIME_INFLIGHT_REQUESTS == 0
    assert helper._runtime_request_registry_snapshot(
        helper._STRATEGY_RUNTIME_REQUEST_LEASES
    ) == ()


def test_concurrent_reload_registration_blocks_socket_before_helper_first_line():
    _assert_runtime_interrupt_probe(
        """
        import importlib
        import threading

        from helpers import bullet_trade_jq_remote_helper as helper

        cached_client = helper._ShortLivedClient(
            "127.0.0.1",
            58620,
            "unit-test-token",
            retries=0,
        )
        socket_calls = []

        def forbidden_socket(*args, **kwargs):
            socket_calls.append((args, kwargs))
            raise AssertionError("reload登记后不得建立socket")

        helper.socket.create_connection = forbidden_socket
        bootstrap = importlib._bootstrap
        original_exec = bootstrap._exec
        exec_entered = threading.Event()
        release_exec = threading.Event()
        reload_errors = []

        def blocking_exec(spec, module):
            exec_entered.set()
            if not release_exec.wait(5):
                raise AssertionError("timed out waiting to release importlib exec")
            return original_exec(spec, module)

        bootstrap._exec = blocking_exec

        def reload_helper():
            global helper
            try:
                helper = importlib.reload(helper)
            except BaseException as exc:
                reload_errors.append(exc)

        reload_thread = threading.Thread(target=reload_helper)
        reload_thread.start()
        assert exec_entered.wait(5)
        request_error = None
        try:
            cached_client.request("broker.place_order", {"amount": 1})
        except BaseException as exc:
            request_error = exc
        finally:
            bootstrap._exec = original_exec
            release_exec.set()
            reload_thread.join(10)

        assert not reload_thread.is_alive()
        assert reload_errors == []
        assert isinstance(request_error, RuntimeError)
        assert socket_calls == []
        assert helper._STRATEGY_RUNTIME_ACTIVE_MODE == "FAILED"
        assert helper._STRATEGY_RUNTIME_INFLIGHT_REQUESTS == 0
        assert helper._runtime_request_registry_snapshot(
            helper._STRATEGY_RUNTIME_REQUEST_LEASES
        ) == ()
        print("CONCURRENT_RELOAD_REGISTRATION_BLOCKS_SOCKET_OK")
        """,
        "CONCURRENT_RELOAD_REGISTRATION_BLOCKS_SOCKET_OK",
    )


def test_recursive_reload_registration_blocks_socket_before_helper_first_line():
    _assert_runtime_interrupt_probe(
        """
        import importlib

        from helpers import bullet_trade_jq_remote_helper as helper

        cached_client = helper._ShortLivedClient(
            "127.0.0.1",
            58620,
            "unit-test-token",
            retries=0,
        )
        socket_calls = []

        def forbidden_socket(*args, **kwargs):
            socket_calls.append((args, kwargs))
            raise AssertionError("recursive reload登记后不得建立socket")

        helper.socket.create_connection = forbidden_socket
        bootstrap = importlib._bootstrap
        original_exec = bootstrap._exec
        request_errors = []

        def probing_exec(spec, module):
            try:
                cached_client.request("broker.place_order", {"amount": 1})
            except BaseException as exc:
                request_errors.append(exc)
            return original_exec(spec, module)

        bootstrap._exec = probing_exec
        try:
            helper = importlib.reload(helper)
        finally:
            bootstrap._exec = original_exec

        assert len(request_errors) == 1
        assert isinstance(request_errors[0], RuntimeError)
        assert socket_calls == []
        assert helper._STRATEGY_RUNTIME_ACTIVE_MODE == "FAILED"
        assert helper._STRATEGY_RUNTIME_INFLIGHT_REQUESTS == 0
        assert helper._runtime_request_registry_snapshot(
            helper._STRATEGY_RUNTIME_REQUEST_LEASES
        ) == ()
        print("RECURSIVE_RELOAD_REGISTRATION_BLOCKS_SOCKET_OK")
        """,
        "RECURSIVE_RELOAD_REGISTRATION_BLOCKS_SOCKET_OK",
    )


def test_recursive_reload_while_holding_socket_gate_lock_does_not_deadlock():
    _assert_runtime_interrupt_probe(
        """
        import importlib
        import inspect
        import sys
        import threading

        from helpers import bullet_trade_jq_remote_helper as helper

        socket_calls = []

        def forbidden_socket(*args, **kwargs):
            socket_calls.append((args, kwargs))
            raise AssertionError("recursive reload must stop before socket creation")

        helper.socket.create_connection = forbidden_socket
        client = helper._ShortLivedClient(
            "127.0.0.1",
            58620,
            "unit-test-token",
            retries=0,
        )
        target_code = helper._create_runtime_socket_with_lease.__code__
        source_lines, first_line = inspect.getsourcelines(target_code)
        registration_index = next(
            index
            for index, line in enumerate(source_lines)
            if line.strip() == "start_result = gate_start_attempt(attempt_token)"
        )
        registration_line = first_line + registration_index
        reload_errors = []
        request_errors = []
        trace_entered = threading.Event()

        def request_worker():
            interrupted = []

            def trace(frame, event, arg):
                if (
                    frame.f_code is target_code
                    and event == "line"
                    and frame.f_lineno == registration_line
                    and not interrupted
                ):
                    interrupted.append(frame.f_lineno)
                    trace_entered.set()
                    sys.settrace(None)
                    try:
                        importlib.reload(helper)
                    except BaseException as exc:
                        reload_errors.append(exc)
                        raise
                return trace

            sys.settrace(trace)
            try:
                client.request("broker.place_order", {"amount": 1})
            except BaseException as exc:
                request_errors.append(exc)
            finally:
                sys.settrace(None)

        request_thread = threading.Thread(target=request_worker, daemon=True)
        request_thread.start()
        assert trace_entered.wait(5)
        request_thread.join(5)
        assert not request_thread.is_alive(), (
            "recursive reload deadlocked while the same thread held socket gate lock"
        )
        assert socket_calls == []
        assert len(reload_errors) == 1
        assert isinstance(reload_errors[0], BaseException), type(reload_errors[0])
        assert not isinstance(reload_errors[0], Exception), type(reload_errors[0])
        assert type(reload_errors[0]).__name__ == "RuntimeReloadAbort", type(
            reload_errors[0]
        )
        assert len(request_errors) == 1
        final_runtime_state = (
            helper._STRATEGY_RUNTIME_ACTIVE_MODE,
            helper._STRATEGY_RUNTIME_SOCKET_GATE_AUTHORITY[1](),
            helper._STRATEGY_RUNTIME_INFLIGHT_REQUESTS,
            helper._runtime_request_registry_snapshot(
                helper._STRATEGY_RUNTIME_REQUEST_LEASES
            ),
        )
        assert helper._STRATEGY_RUNTIME_ACTIVE_MODE == "FAILED", final_runtime_state
        assert helper._STRATEGY_RUNTIME_SOCKET_GATE_AUTHORITY[1]() == (
            True,
            (),
        ), final_runtime_state
        assert helper._STRATEGY_RUNTIME_INFLIGHT_REQUESTS == 0, final_runtime_state
        assert helper._runtime_request_registry_snapshot(
            helper._STRATEGY_RUNTIME_REQUEST_LEASES
        ) == (), final_runtime_state
        print("RECURSIVE_RELOAD_WITH_SOCKET_LOCK_NO_DEADLOCK_OK")
        """,
        "RECURSIVE_RELOAD_WITH_SOCKET_LOCK_NO_DEADLOCK_OK",
    )


def test_inflight_configure_cannot_return_success_after_reload_registration():
    _assert_runtime_interrupt_probe(
        """
        import importlib
        import threading

        from helpers import bullet_trade_jq_remote_helper as helper

        clients_published = threading.Event()
        release_configure = threading.Event()
        exec_registered = threading.Event()
        release_exec = threading.Event()
        configure_results = []
        configure_errors = []
        reload_errors = []
        original_configure_clients = helper._configure_remote_clients

        def blocking_configure_clients(*args, **kwargs):
            original_configure_clients(*args, **kwargs)
            clients_published.set()
            if not release_configure.wait(5):
                raise AssertionError("timed out waiting to finish configure")

        helper._configure_remote_clients = blocking_configure_clients

        def configure_worker():
            try:
                helper.configure(
                    "127.0.0.1",
                    "unit-test-token",
                    debug=False,
                )
                configure_results.append("success")
            except BaseException as exc:
                configure_errors.append(exc)

        configure_thread = threading.Thread(target=configure_worker)
        configure_thread.start()
        assert clients_published.wait(5)

        bootstrap = importlib._bootstrap
        original_exec = bootstrap._exec

        def blocking_exec(spec, module):
            exec_registered.set()
            if not release_exec.wait(5):
                raise AssertionError("timed out waiting to execute helper reload")
            return original_exec(spec, module)

        bootstrap._exec = blocking_exec

        def reload_worker():
            global helper
            try:
                helper = importlib.reload(helper)
            except BaseException as exc:
                reload_errors.append(exc)

        reload_thread = threading.Thread(target=reload_worker)
        reload_thread.start()
        assert exec_registered.wait(5)
        release_configure.set()
        configure_thread.join(5)
        bootstrap._exec = original_exec
        release_exec.set()
        reload_thread.join(10)

        assert not configure_thread.is_alive()
        assert not reload_thread.is_alive()
        assert configure_results == []
        assert len(configure_errors) == 1
        assert isinstance(configure_errors[0], RuntimeError)
        assert reload_errors == []
        assert helper._STRATEGY_RUNTIME_ACTIVE_MODE == "FAILED"
        assert helper._CLIENT is None
        assert helper._DATA_CLIENT is None
        assert helper._BROKER_CLIENT is None
        print("INFLIGHT_CONFIGURE_RELOAD_REGISTRATION_NO_FALSE_SUCCESS_OK")
        """,
        "INFLIGHT_CONFIGURE_RELOAD_REGISTRATION_NO_FALSE_SUCCESS_OK",
    )


def test_recursive_reload_after_socket_attempt_registration_cannot_resume_socket():
    _assert_runtime_interrupt_probe(
        """
        import importlib
        import inspect
        import sys

        from helpers import bullet_trade_jq_remote_helper as helper

        socket_calls = []

        def forbidden_socket(*args, **kwargs):
            socket_calls.append((args, kwargs))
            raise AssertionError("stale request resumed socket creation after reload")

        helper.socket.create_connection = forbidden_socket
        client = helper._ShortLivedClient(
            "127.0.0.1",
            58620,
            "unit-test-token",
            retries=0,
        )
        target_code = helper._create_runtime_socket_with_lease.__code__
        source_lines, first_line = inspect.getsourcelines(target_code)
        post_registration_index = next(
            index
            for index, line in enumerate(source_lines)
            if line.strip() == "attempt_started = True"
        )
        post_registration_line = first_line + post_registration_index
        reload_errors = []
        request_errors = []
        traced = []

        def trace(frame, event, arg):
            if (
                frame.f_code is target_code
                and event == "line"
                and frame.f_lineno == post_registration_line
                and not traced
            ):
                traced.append(frame.f_lineno)
                sys.settrace(None)
                try:
                    importlib.reload(helper)
                except BaseException as exc:
                    # RuntimeReloadAbort must terminate this stale request stack;
                    # production callers may catch Exception, but must not catch
                    # this process-fatal BaseException and resume remote effects.
                    reload_errors.append(exc)
                    raise
            return trace

        sys.settrace(trace)
        try:
            client.request("broker.place_order", {"amount": 1})
        except BaseException as exc:
            request_errors.append(exc)
        finally:
            sys.settrace(None)

        assert traced == [post_registration_line]
        assert len(reload_errors) == 1
        assert isinstance(reload_errors[0], BaseException)
        assert not isinstance(reload_errors[0], Exception)
        assert type(reload_errors[0]).__name__ == "RuntimeReloadAbort"
        assert socket_calls == []
        assert request_errors
        assert helper._STRATEGY_RUNTIME_ACTIVE_MODE == "FAILED"
        assert helper._STRATEGY_RUNTIME_SOCKET_GATE_AUTHORITY[1]() == (True, ())
        assert helper._STRATEGY_RUNTIME_INFLIGHT_REQUESTS == 0
        assert helper._runtime_request_registry_snapshot(
            helper._STRATEGY_RUNTIME_REQUEST_LEASES
        ) == ()
        print("RECURSIVE_RELOAD_POST_ATTEMPT_CANNOT_RESUME_SOCKET_OK")
        """,
        "RECURSIVE_RELOAD_POST_ATTEMPT_CANNOT_RESUME_SOCKET_OK",
    )


@pytest.mark.parametrize(
    "reload_trace_point",
    (
        "open_transport_call_line",
        "open_transport_first_line",
        "open_transport_final_line",
    ),
)
def test_recursive_reload_during_final_socket_validation_cannot_resume_socket(
    reload_trace_point,
):
    script = """
        import importlib
        import inspect
        import sys
        import types

        from helpers import bullet_trade_jq_remote_helper as helper

        reload_trace_point = __RELOAD_TRACE_POINT__
        socket_calls = []

        def forbidden_socket(*args, **kwargs):
            socket_calls.append((args, kwargs))
            raise AssertionError("stale validated request resumed socket creation")

        helper.socket.create_connection = forbidden_socket
        client = helper._ShortLivedClient(
            "127.0.0.1",
            58620,
            "unit-test-token",
            retries=0,
        )
        socket_code = helper._create_runtime_socket_with_lease.__code__
        open_transport_code = (
            helper._STRATEGY_RUNTIME_SOCKET_GATE_AUTHORITY[5].__code__
        )
        socket_lines, socket_first_line = inspect.getsourcelines(socket_code)
        open_transport_lines, open_transport_first_line = inspect.getsourcelines(
            open_transport_code
        )
        open_transport_call_line = socket_first_line + next(
            index
            for index, line in enumerate(socket_lines)
            if line.strip() == "open_result = gate_open_transport("
        )
        open_transport_entry_line = open_transport_first_line + next(
            index
            for index, line in enumerate(open_transport_lines)
            if line.strip() == "type(attempt_token) is not object"
        )
        open_transport_final_line = open_transport_first_line + next(
            index
            for index, line in enumerate(open_transport_lines)
            if line.strip().startswith("return False if reload_requested")
        )
        reload_errors = []
        request_errors = []
        traced = []

        def trace(frame, event, arg):
            should_reload = (
                reload_trace_point == "open_transport_call_line"
                and frame.f_code is socket_code
                and event == "line"
                and frame.f_lineno == open_transport_call_line
            ) or (
                reload_trace_point == "open_transport_first_line"
                and frame.f_code is open_transport_code
                and event == "line"
                and frame.f_lineno == open_transport_entry_line
            ) or (
                reload_trace_point == "open_transport_final_line"
                and frame.f_code is open_transport_code
                and event == "line"
                and frame.f_lineno == open_transport_final_line
            )
            if should_reload and not traced:
                traced.append((event, frame.f_lineno))
                sys.settrace(None)
                try:
                    importlib.reload(helper)
                except BaseException as exc:
                    reload_errors.append(exc)
                    raise
            return trace

        sys.settrace(trace)
        try:
            client.request("broker.place_order", {"amount": 1})
        except BaseException as exc:
            request_errors.append(exc)
        finally:
            sys.settrace(None)

        assert len(traced) == 1
        assert len(reload_errors) == 1
        assert isinstance(reload_errors[0], BaseException)
        assert not isinstance(reload_errors[0], Exception)
        assert type(reload_errors[0]).__name__ == "RuntimeReloadAbort"
        assert socket_calls == [], (
            reload_trace_point,
            reload_errors,
            request_errors,
            helper._STRATEGY_RUNTIME_SOCKET_GATE_AUTHORITY[1](),
        )
        assert request_errors
        assert helper._STRATEGY_RUNTIME_ACTIVE_MODE == "FAILED"
        assert helper._STRATEGY_RUNTIME_SOCKET_GATE_AUTHORITY[1]() == (True, ())
        assert helper._STRATEGY_RUNTIME_INFLIGHT_REQUESTS == 0
        assert helper._runtime_request_registry_snapshot(
            helper._STRATEGY_RUNTIME_REQUEST_LEASES
        ) == ()
        print("RECURSIVE_RELOAD_FINAL_SOCKET_VALIDATION_OK")
    """.replace("__RELOAD_TRACE_POINT__", repr(reload_trace_point))
    _assert_runtime_interrupt_probe(
        script,
        "RECURSIVE_RELOAD_FINAL_SOCKET_VALIDATION_OK",
    )


def test_reload_after_socket_creation_cannot_send_handshake_or_mutation():
    _assert_runtime_interrupt_probe(
        """
        import importlib
        import json
        import threading

        from helpers import bullet_trade_jq_remote_helper as helper

        request_paused = threading.Event()
        release_request = threading.Event()
        sent_frames = []
        request_errors = []
        reload_errors = []

        class FakeSocket:
            def __init__(self):
                self.closed = False

            def settimeout(self, timeout):
                request_paused.set()
                if not release_request.wait(5):
                    raise AssertionError("timed out waiting for reload")

            def sendall(self, payload):
                sent_frames.append(payload)

            def close(self):
                self.closed = True

        fake_socket = FakeSocket()
        helper.socket.create_connection = lambda *args, **kwargs: fake_socket
        client = helper._ShortLivedClient(
            "127.0.0.1",
            58620,
            "unit-test-token",
            retries=0,
        )
        receive_calls = []

        def receive(sock):
            receive_calls.append(sock)
            if len(receive_calls) == 1:
                return {"type": "handshake_ack", "protocol": 1}
            request_message = json.loads(sent_frames[-1][4:].decode("utf-8"))
            return {
                "type": "response",
                "id": request_message["id"],
                "payload": {"ok": True},
            }

        client._recv = receive

        def request_worker():
            try:
                client.request("broker.place_order", {"amount": 1})
            except BaseException as exc:
                request_errors.append(exc)

        request_thread = threading.Thread(target=request_worker)
        request_thread.start()
        assert request_paused.wait(5)
        assert helper._STRATEGY_RUNTIME_SOCKET_GATE_AUTHORITY[1]() == (False, ())

        try:
            helper = importlib.reload(helper)
        except BaseException as exc:
            reload_errors.append(exc)

        assert reload_errors == []
        assert helper._STRATEGY_RUNTIME_ACTIVE_MODE == "FAILED"
        assert helper._STRATEGY_RUNTIME_SOCKET_GATE_AUTHORITY[1]() == (True, ())
        release_request.set()
        request_thread.join(5)

        sent_messages = [
            json.loads(frame[4:].decode("utf-8"))
            for frame in sent_frames
        ]
        assert not request_thread.is_alive()
        assert sent_messages == []
        assert receive_calls == []
        assert request_errors
        assert fake_socket.closed is True
        assert helper._STRATEGY_RUNTIME_INFLIGHT_REQUESTS == 0
        assert helper._runtime_request_registry_snapshot(
            helper._STRATEGY_RUNTIME_REQUEST_LEASES
        ) == ()
        print("POST_CONNECT_RELOAD_BLOCKS_ALL_SENDS_OK")
        """,
        "POST_CONNECT_RELOAD_BLOCKS_ALL_SENDS_OK",
    )


def test_reload_waits_for_transport_after_final_permit_predicate():
    _assert_runtime_interrupt_probe(
        """
        import importlib
        import sys
        import threading

        from helpers import bullet_trade_jq_remote_helper as helper

        permit_checked = threading.Event()
        release_transport = threading.Event()
        reload_started = threading.Event()
        socket_calls = []
        request_errors = []
        reload_errors = []
        gate_state_before_transport = []

        def forbidden_socket(*args, **kwargs):
            socket_calls.append((args, kwargs))
            raise OSError("expected transport stop")

        connector_code = forbidden_socket.__code__

        helper.socket.create_connection = forbidden_socket
        client = helper._ShortLivedClient(
            "127.0.0.1",
            58620,
            "unit-test-token",
            retries=0,
        )

        def trace(frame, event, arg):
            if (
                frame.f_code is connector_code
                and event == "call"
                and not permit_checked.is_set()
            ):
                permit_checked.set()
                if not release_transport.wait(5):
                    raise AssertionError("timed out waiting to release transport")
            return trace

        def request_worker():
            sys.settrace(trace)
            try:
                client.request("broker.place_order", {"amount": 1})
            except BaseException as exc:
                request_errors.append(exc)
            finally:
                sys.settrace(None)

        def reload_worker():
            global helper
            reload_started.set()
            try:
                helper = importlib.reload(helper)
            except BaseException as exc:
                reload_errors.append(exc)

        request_thread = threading.Thread(target=request_worker)
        reload_thread = threading.Thread(target=reload_worker)
        request_thread.start()
        assert permit_checked.wait(5)
        reload_thread.start()
        assert reload_started.wait(5)
        reload_thread.join(0.2)
        assert reload_thread.is_alive()
        gate_state_before_transport.append(
            helper._STRATEGY_RUNTIME_SOCKET_GATE_AUTHORITY[1]()
        )
        release_transport.set()
        request_thread.join(5)
        reload_thread.join(5)

        assert not request_thread.is_alive()
        assert not reload_thread.is_alive()
        assert gate_state_before_transport
        assert gate_state_before_transport[0][0] is True
        assert len(gate_state_before_transport[0][1]) == 1
        assert len(socket_calls) == 1
        assert request_errors
        assert reload_errors == []
        assert helper._STRATEGY_RUNTIME_ACTIVE_MODE == "FAILED"
        assert helper._STRATEGY_RUNTIME_SOCKET_GATE_AUTHORITY[1]() == (True, ())
        assert helper._STRATEGY_RUNTIME_INFLIGHT_REQUESTS == 0
        print("PERMIT_TO_TRANSPORT_GATE_ATOMIC_OK")
        """,
        "PERMIT_TO_TRANSPORT_GATE_ATOMIC_OK",
    )


@pytest.mark.parametrize(
    "remote_effect_phase",
    ("tls_wrap", "handshake_send", "mutation_send"),
)
@pytest.mark.parametrize(
    "remote_effect_trace_point",
    ("outer_call", "authority_first_line", "authority_final_line"),
)
def test_recursive_reload_after_phase_lease_check_blocks_remote_effect(
    remote_effect_phase,
    remote_effect_trace_point,
):
    script = """
        import importlib
        import inspect
        import json
        import sys

        from helpers import bullet_trade_jq_remote_helper as helper

        remote_effect_phase = __REMOTE_EFFECT_PHASE__
        remote_effect_trace_point = __REMOTE_EFFECT_TRACE_POINT__
        sent_frames = []
        tls_wrap_calls = []
        reload_errors = []
        request_errors = []
        traced = []
        effects_before_reload = []
        gate_states_after_reload = []
        authority_hits = []

        class FakeSocket:
            def __init__(self):
                self.closed = False

            def settimeout(self, timeout):
                pass

            def sendall(self, payload):
                sent_frames.append(payload)

            def close(self):
                self.closed = True

        class FakeTlsContext:
            def wrap_socket(self, sock, server_hostname=None):
                tls_wrap_calls.append((sock, server_hostname))
                return sock

        fake_socket = FakeSocket()
        helper.socket.create_connection = lambda *args, **kwargs: fake_socket
        helper.ssl.create_default_context = lambda **kwargs: FakeTlsContext()
        client = helper._ShortLivedClient(
            "127.0.0.1",
            58620,
            "unit-test-token",
            tls_cert=("unit-test-ca.pem" if remote_effect_phase == "tls_wrap" else None),
            retries=0,
        )

        def receive(sock):
            if len(sent_frames) == 1:
                return {"type": "handshake_ack", "protocol": 1}
            request_message = json.loads(sent_frames[-1][4:].decode("utf-8"))
            return {
                "type": "response",
                "id": request_message["id"],
                "payload": {"ok": True},
            }

        client._recv = receive
        tracked_impl = _runtime_impl_from_closure(
            helper._ShortLivedClient.request,
            "tracked_impl",
        )
        request_function = _runtime_impl_from_closure(
            tracked_impl,
            "request",
        )
        request_code = request_function.__code__
        request_lines, request_first_line = inspect.getsourcelines(request_code)
        run_effect = helper._STRATEGY_RUNTIME_SOCKET_GATE_AUTHORITY[6]
        authority_before_reload = helper._STRATEGY_RUNTIME_SOCKET_GATE_AUTHORITY
        generation_before_reload = helper._STRATEGY_RUNTIME_MODULE_GENERATION
        run_effect_code = run_effect.__code__
        effect_lines, effect_first_line = inspect.getsourcelines(run_effect_code)
        effect_call_indices = [
            index
            for index, line in enumerate(request_lines)
            if line.strip() in {
                "effect_allowed, wrapped_socket = run_remote_effect(",
                "effect_allowed, _ = run_remote_effect(",
            }
        ]
        effect_call_index = {
            "tls_wrap": effect_call_indices[0],
            "handshake_send": effect_call_indices[1],
            "mutation_send": effect_call_indices[2],
        }[remote_effect_phase]
        outer_call_line = request_first_line + effect_call_index
        authority_first_line = effect_first_line + next(
            index
            for index, line in enumerate(effect_lines)
            if line.strip() == "type(effect_args) is not tuple"
        )
        authority_final_line = effect_first_line + next(
            index
            for index, line in enumerate(effect_lines)
            if line.strip().startswith(
                "return (False, None) if reload_requested"
            )
        )
        authority_trace_line = {
            "authority_first_line": authority_first_line,
            "authority_final_line": authority_final_line,
        }.get(remote_effect_trace_point)
        authority_target_occurrence = (
            2 if remote_effect_phase == "mutation_send" else 1
        )

        def trace(frame, event, arg):
            is_authority_trace_event = (
                frame.f_code is run_effect_code
                and event == "line"
                and frame.f_lineno == authority_trace_line
            )
            if is_authority_trace_event:
                # Do not inspect frame.f_locals here.  CPython's trace-frame
                # locals synchronization can write a stale closure-cell value
                # back after recursive reload closes the monotonic latch.
                authority_hits.append(frame.f_lineno)
            should_reload = (
                remote_effect_trace_point == "outer_call"
                and frame.f_code is request_code
                and event == "line"
                and frame.f_lineno == outer_call_line
            ) or (
                is_authority_trace_event
                and len(authority_hits) == authority_target_occurrence
            )
            if should_reload and not traced:
                traced.append(
                    (
                        event,
                        frame.f_lineno,
                        remote_effect_phase
                        if is_authority_trace_event
                        else "outer",
                        (len(tls_wrap_calls), len(sent_frames)),
                        authority_before_reload[1](),
                    )
                )
                effects_before_reload.append(
                    (len(tls_wrap_calls), len(sent_frames))
                )
                sys.settrace(None)
                try:
                    importlib.reload(helper)
                except BaseException as exc:
                    reload_errors.append(exc)
                    gate_states_after_reload.append(
                        (
                            helper._STRATEGY_RUNTIME_SOCKET_GATE_AUTHORITY
                            is authority_before_reload,
                            helper._STRATEGY_RUNTIME_MODULE_GENERATION,
                            generation_before_reload,
                            authority_before_reload[1](),
                            helper._STRATEGY_RUNTIME_SOCKET_GATE_AUTHORITY[1](),
                        )
                    )
                    raise
            return trace

        sys.settrace(trace)
        try:
            client.request("broker.place_order", {"amount": 1})
        except BaseException as exc:
            request_errors.append(exc)
        finally:
            sys.settrace(None)

        assert len(traced) == 1
        assert len(reload_errors) == 1
        assert isinstance(reload_errors[0], BaseException)
        assert not isinstance(reload_errors[0], Exception)
        assert type(reload_errors[0]).__name__ == "RuntimeReloadAbort"
        assert effects_before_reload
        assert (len(tls_wrap_calls), len(sent_frames)) == effects_before_reload[0], (
            remote_effect_phase,
            remote_effect_trace_point,
            traced,
            gate_states_after_reload,
        )
        assert request_errors
        assert request_errors[0] is reload_errors[0]
        assert fake_socket.closed is True
        assert helper._STRATEGY_RUNTIME_ACTIVE_MODE == "FAILED"
        assert helper._STRATEGY_RUNTIME_SOCKET_GATE_AUTHORITY[1]() == (
            True,
            (),
        ), (
            remote_effect_phase,
            remote_effect_trace_point,
            helper._STRATEGY_RUNTIME_SOCKET_GATE_AUTHORITY
            is authority_before_reload,
            helper._STRATEGY_RUNTIME_SOCKET_GATE_AUTHORITY[1](),
            gate_states_after_reload,
            request_errors,
        )
        assert helper._STRATEGY_RUNTIME_INFLIGHT_REQUESTS == 0, (
            remote_effect_phase,
            remote_effect_trace_point,
            helper._STRATEGY_RUNTIME_INFLIGHT_REQUESTS,
            helper._runtime_request_registry_snapshot(
                helper._STRATEGY_RUNTIME_REQUEST_LEASES
            ),
            request_errors,
            gate_states_after_reload,
        )
        assert helper._runtime_request_registry_snapshot(
            helper._STRATEGY_RUNTIME_REQUEST_LEASES
        ) == ()
        print("PHASE_LEASE_CHECK_TO_REMOTE_EFFECT_ATOMIC_OK")
    """.replace(
        "__REMOTE_EFFECT_PHASE__",
        repr(remote_effect_phase),
    ).replace(
        "__REMOTE_EFFECT_TRACE_POINT__",
        repr(remote_effect_trace_point),
    )
    _assert_runtime_interrupt_probe(
        script,
        "PHASE_LEASE_CHECK_TO_REMOTE_EFFECT_ATOMIC_OK",
    )


def test_reload_waits_for_linearized_mutation_effect_to_finish():
    _assert_runtime_interrupt_probe(
        """
        import importlib
        import json
        import threading

        from helpers import bullet_trade_jq_remote_helper as helper

        effect_entered = threading.Event()
        release_effect = threading.Event()
        reload_started = threading.Event()
        sent_frames = []
        request_errors = []
        reload_errors = []

        class FakeSocket:
            def __init__(self):
                self.closed = False

            def settimeout(self, timeout):
                pass

            def sendall(self, payload):
                if len(sent_frames) == 1:
                    effect_entered.set()
                    if not release_effect.wait(5):
                        raise AssertionError("timed out waiting to finish mutation send")
                sent_frames.append(payload)

            def close(self):
                self.closed = True

        fake_socket = FakeSocket()
        helper.socket.create_connection = lambda *args, **kwargs: fake_socket
        client = helper._ShortLivedClient(
            "127.0.0.1",
            58620,
            "unit-test-token",
            retries=0,
        )

        def receive(sock):
            if len(sent_frames) == 1:
                return {"type": "handshake_ack", "protocol": 1}
            request_message = json.loads(sent_frames[-1][4:].decode("utf-8"))
            return {
                "type": "response",
                "id": request_message["id"],
                "payload": {"ok": True},
            }

        client._recv = receive

        def request_worker():
            try:
                client.request("broker.place_order", {"amount": 1})
            except BaseException as exc:
                request_errors.append(exc)

        def reload_worker():
            global helper
            reload_started.set()
            try:
                helper = importlib.reload(helper)
            except BaseException as exc:
                reload_errors.append(exc)

        request_thread = threading.Thread(target=request_worker)
        reload_thread = threading.Thread(target=reload_worker)
        request_thread.start()
        assert effect_entered.wait(5)
        reload_thread.start()
        assert reload_started.wait(5)
        reload_thread.join(0.2)
        reload_waited_for_effect = reload_thread.is_alive()
        release_effect.set()
        request_thread.join(5)
        reload_thread.join(5)

        sent_messages = [
            json.loads(frame[4:].decode("utf-8"))
            for frame in sent_frames
        ]
        assert reload_waited_for_effect is True
        assert not request_thread.is_alive()
        assert not reload_thread.is_alive()
        assert [message["type"] for message in sent_messages] == [
            "handshake",
            "request",
        ]
        assert sent_messages[1]["action"] == "broker.place_order"
        assert request_errors
        assert reload_errors == []
        assert fake_socket.closed is True
        assert helper._STRATEGY_RUNTIME_ACTIVE_MODE == "FAILED"
        assert helper._STRATEGY_RUNTIME_SOCKET_GATE_AUTHORITY[1]() == (True, ())
        assert helper._STRATEGY_RUNTIME_INFLIGHT_REQUESTS == 0
        print("RELOAD_WAITS_LINEARIZED_MUTATION_EFFECT_OK")
        """,
        "RELOAD_WAITS_LINEARIZED_MUTATION_EFFECT_OK",
    )
