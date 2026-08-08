import sys
import types

import pytest

from helpers import bullet_trade_jq_remote_helper as helper


class _RunParams:
    def __init__(self, run_type):
        self.type = run_type


class _Context:
    def __init__(self, run_type):
        self.run_params = _RunParams(run_type)


class _Broker:
    def __init__(self):
        self.order_calls = 0

    def get_account(self):
        return {"available_cash": 1}

    def order(self, *args, **kwargs):
        self.order_calls += 1
        return "unsafe-order"


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


def test_shadow_strictly_validates_profile_but_never_configures_or_installs_compat(monkeypatch):
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
    broker = _Broker()
    monkeypatch.setattr(helper, "_BROKER_CLIENT", broker)
    query = lambda: "query-ok"
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

    # helper模块的便捷下单也必须被只读broker代理阻断。
    with pytest.raises(RuntimeError, match="SHADOW模式禁止交易变更"):
        helper.order("000001.XSHE", 100)
    assert broker.order_calls == 0
    assert helper.get_broker_client().get_account() == {"available_cash": 1}


def test_live_uses_jq_compat_with_forced_no_mirror_and_profile_defaults(monkeypatch):
    module_name = _profile_module(monkeypatch)
    calls = []

    def fake_install(namespace, **kwargs):
        calls.append(kwargs)
        return {"enabled": True, "run_type": "sim_trade"}

    monkeypatch.setattr(helper, "install_jq_compat", fake_install)
    state = _install({}, _Context("sim_trade"), module_name, mode="LIVE")

    assert len(calls) == 1
    call = calls[0]
    assert call["host"] == "127.0.0.1"
    assert call["token"] == "top-secret-token"
    assert call["port"] == 58620
    assert call["retries"] == 2
    assert call["retry_interval"] == 0.5
    assert call["rpc_timeout"] == helper.DEFAULT_RPC_TIMEOUT_SECONDS
    assert call["place_order_timeout_margin"] == helper.DEFAULT_PLACE_ORDER_TIMEOUT_MARGIN_SECONDS
    assert call["default_wait_timeout"] == helper.DEFAULT_JQ_COMPAT_WAIT_TIMEOUT_SECONDS
    assert call["mirror_jq_orders"] is False
    assert state["mode"] == "LIVE"
    assert state["orders_enabled"] is True
    assert state["production_ready"] is False
    assert state["reason"] == "live_compatibility_only"
    assert "token" not in state
    assert "host" not in state


def test_live_passes_explicit_optional_profile_values(monkeypatch):
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
    calls = []
    monkeypatch.setattr(
        helper,
        "install_jq_compat",
        lambda namespace, **kwargs: calls.append(kwargs) or {"enabled": True},
    )

    _install({}, _Context("sim_trade"), module_name, mode="LIVE")

    call = calls[0]
    assert call["port"] == 60000
    assert call["account_key"] == "main"
    assert call["sub_account_id"] == "good_etf@main"
    assert call["tls_cert"] == "server.pem"
    assert call["retries"] == 4
    assert call["retry_interval"] == 1.0
    assert call["rpc_timeout"] == 21.0
    assert call["place_order_timeout_margin"] == 7.0
    assert call["default_wait_timeout"] == 9.0
    assert call["debug"] is False


@pytest.mark.parametrize("mode", ["SHADOW", "LIVE"])
def test_remote_modes_are_idempotent(monkeypatch, mode):
    module_name = _profile_module(monkeypatch)
    calls = []
    monkeypatch.setattr(
        helper,
        "install_jq_compat",
        lambda namespace, **kwargs: calls.append(kwargs) or {"enabled": True},
    )
    namespace = {}
    context = _Context("sim_trade")

    first = _install(namespace, context, module_name, mode=mode)
    monkeypatch.setattr(
        helper,
        "_load_runtime_profile",
        lambda *args, **kwargs: pytest.fail("幂等安装不得重新加载profile"),
    )
    second = _install(namespace, context, module_name, mode=mode)

    assert first == second
    assert len(calls) == (1 if mode == "LIVE" else 0)


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
        {"rpc_timeout": float("nan")},
        {"retry_interval": -1},
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


def test_live_initialisation_error_never_leaks_token(monkeypatch):
    secret = "unique-super-secret-token"
    module_name = _profile_module(
        monkeypatch,
        profiles={"good_etf-prod": _valid_profile(token=secret)},
    )

    def unsafe_failure(namespace, **kwargs):
        raise RuntimeError("connection failed with token={}".format(kwargs["token"]))

    monkeypatch.setattr(helper, "install_jq_compat", unsafe_failure)
    with pytest.raises(RuntimeError, match="LIVE兼容层初始化失败") as exc_info:
        _install({}, _Context("sim_trade"), module_name, mode="LIVE")

    assert secret not in str(exc_info.value)
    assert exc_info.value.__cause__ is None
