import builtins
import sys
import types

import pytest

from helpers import bullet_trade_jq_remote_helper as helper


@pytest.fixture(autouse=True)
def _reset_runtime_process_gate(monkeypatch):
    monkeypatch.setattr(helper, "_STRATEGY_RUNTIME_ACTIVE_MODE", None)
    monkeypatch.setattr(helper, "_STRATEGY_RUNTIME_PROCESS_SIGNATURE", None)
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
    with pytest.raises(RuntimeError, match="BACKTEST、SHADOW 或 LIVE"):
        _install({}, _Context("sim_trade"), "unused_profile", mode="paper")


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
def test_remote_modes_reject_context_inherited_from_remote_compat(monkeypatch, mode):
    module_name = _profile_module(monkeypatch)
    context = _Context("sim_trade")
    context.portfolio = object.__new__(helper._RemoteJQPortfolio)
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

    namespace = {
        helper._JQ_COMPAT_STATE_KEY: {
            "installed": True,
            "originals": {
                "order": native_mutation,
                "order_target": native_mutation,
                "cancel_order": native_mutation,
            },
        },
        "order": cached_compat_order,
        "order_target": lambda *args, **kwargs: "remote-order-target",
        "cancel_order": lambda *args, **kwargs: "remote-cancel",
    }

    with pytest.raises(RuntimeError, match="不能复用.*远程兼容层"):
        _install(namespace, context, module_name, mode=mode)
    assert helper._STRATEGY_RUNTIME_ACTIVE_MODE == "FAILED"
    assert helper._BROKER_CLIENT is None
    for name in ("order", "order_target", "cancel_order"):
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


def test_reinstall_with_different_contract_fails_closed(monkeypatch):
    module_name = _profile_module(monkeypatch)
    namespace = {}
    context = _Context("sim_trade")
    _install(namespace, context, module_name)

    with pytest.raises(RuntimeError, match="不同配置"):
        helper.install_strategy_runtime(
            namespace,
            context=context,
            profile="other-profile",
            mode="SHADOW",
            strategy_id="good_etf",
            profile_module=module_name,
        )


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


def test_profile_import_error_never_leaks_token(monkeypatch):
    secret = "unique-super-secret-token"
    original_import = __import__

    def unsafe_import(name, *args, **kwargs):
        if name == "unsafe_profile_module":
            raise RuntimeError("profile failed with token={}".format(secret))
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", unsafe_import)
    with pytest.raises(RuntimeError, match="无法加载运行配置模块") as exc_info:
        _install({}, _Context("sim_trade"), "unsafe_profile_module", mode="LIVE")

    assert secret not in str(exc_info.value)
    assert exc_info.value.__cause__ is None
