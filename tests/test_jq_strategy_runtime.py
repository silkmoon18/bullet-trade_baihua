# -*- coding: utf-8 -*-
"""helpers/bullet_trade_jq_remote_helper.py（L00 精简契约）测试套件。

每个用例用 importlib.reload 重置 helper 模块级安装签名/状态与 _MODULE_TOKEN。
"""

import importlib
import socket
import sys
import types

import pytest

import helpers.bullet_trade_jq_remote_helper as _helper

PROFILE = "good_etf-prod"
STRATEGY_ID = "good_etf"
PROFILE_MODULE = "test_jq_runtime_config"
BLOCKED_MUTATIONS = tuple(sorted({
    "order", "order_value", "order_percent", "order_target",
    "order_target_value", "order_target_percent", "cancel_order",
}))


@pytest.fixture()
def helper():
    return importlib.reload(_helper)


def _context(run_type):
    return types.SimpleNamespace(run_params=types.SimpleNamespace(type=run_type))


def _valid_profile(**overrides):
    value = {"strategy_id": STRATEGY_ID, "host": "127.0.0.1", "token": "unit-token"}
    value.update(overrides)
    return value


def _profile_module(
    monkeypatch, *, version=1, profiles=None, execution_modes=None
):
    module = types.ModuleType(PROFILE_MODULE)
    module.PROFILE_SCHEMA_VERSION = version
    module.PROFILES = {PROFILE: _valid_profile()} if profiles is None else profiles
    if execution_modes is not None:
        module.EXECUTION_MODES = execution_modes
    monkeypatch.setitem(sys.modules, PROFILE_MODULE, module)


def _install(helper, namespace=None, context=None, *, mode="JQ", **kwargs):
    kwargs.setdefault("profile", PROFILE)
    kwargs.setdefault("strategy_id", STRATEGY_ID)
    kwargs.setdefault("profile_module", PROFILE_MODULE)
    return helper.install_strategy_runtime(
        {} if namespace is None else namespace,
        context=_context("sim_trade") if context is None else context,
        mode=mode, **kwargs)


def _state(mode, run_type, **extra):
    state = {
        "api_version": 7,
        "profile_schema_version": 1,
        "profile": PROFILE,
        "mode": mode,
        "run_type": run_type,
        "strategy_id": STRATEGY_ID,
        "enabled": mode in ("JQ", "QMT_REMOTE"),
        "orders_enabled": mode in ("BACKTEST", "JQ", "QMT_REMOTE"),
        "production_ready": False,
        "reason": "backtest",
    }
    state.update(extra)
    return state


def test_public_contract_exports_and_constants(helper):
    assert {
        "PROFILE_SCHEMA_VERSION",
        "PortfolioView",
        "PositionView",
        "STRATEGY_RUNTIME_API_VERSION",
        "STRATEGY_RUNTIME_HELPER_MARKER",
        "ensure_account",
        "get_intent",
        "get_portfolio",
        "get_reconciliation",
        "install_strategy_runtime",
        "notify_target_buy_plan",
        "submit_targets",
        "ExecutionRequest",
        "ConditionalLimitExecution",
        "MarketExecution",
        "get_configured_execution_mode",
        "submit_runtime_targets",
        "cancel_runtime_targets",
    }.issubset(set(helper.__all__))
    assert helper.STRATEGY_RUNTIME_API_VERSION == 7
    assert helper.STRATEGY_RUNTIME_HELPER_MARKER == (
        "bullet-trade-joinquant-runtime-helper-v7"
    )
    assert helper.PROFILE_SCHEMA_VERSION == 1


def test_strategy_mode_is_per_strategy_and_missing_key_defaults_to_jq(
    helper, monkeypatch
):
    _profile_module(
        monkeypatch,
        execution_modes={"other": "QMT_REMOTE"},
    )

    assert helper.get_configured_execution_mode(
        STRATEGY_ID, PROFILE_MODULE
    ) == "JQ"

    sys.modules[PROFILE_MODULE].EXECUTION_MODES[STRATEGY_ID] = "QMT_REMOTE"
    assert helper.get_configured_execution_mode(
        STRATEGY_ID, PROFILE_MODULE
    ) == "QMT_REMOTE"


def test_joinquant_runtime_facade_resolves_sim_mode_from_strategy_config(
    helper, monkeypatch
):
    _profile_module(
        monkeypatch,
        execution_modes={STRATEGY_ID: "QMT_REMOTE"},
    )
    namespace = {
        name: lambda *args, **kwargs: None for name in BLOCKED_MUTATIONS
    }

    runtime = helper.install_joinquant_runtime(
        namespace,
        context=_context("sim_trade"),
        profile=PROFILE,
        strategy_id=STRATEGY_ID,
        initial_capital="10000",
        profile_module=PROFILE_MODULE,
    )

    assert runtime.mode is helper.RuntimeMode.QMT_REMOTE
    assert runtime.state["mode"] == "QMT_REMOTE"
    assert runtime.state["production_ready"] is False


def test_joinquant_runtime_facade_backtest_does_not_load_profile_by_default(
    helper
):
    runtime = helper.install_joinquant_runtime(
        {},
        context=_context("full_backtest"),
        profile=PROFILE,
        strategy_id=STRATEGY_ID,
        initial_capital="10000",
        profile_module="module_that_does_not_exist",
        validate_remote_during_backtest=False,
    )

    assert runtime.mode is helper.RuntimeMode.BACKTEST
    assert runtime.state["reason"] == "backtest"


@pytest.mark.parametrize("run_type", ["simple_backtest", "full_backtest"])
@pytest.mark.parametrize("context_kind", ["attr", "dict"])
def test_backtest_state_exact_without_profile_or_namespace_mutation(
    helper, run_type, context_kind
):
    if context_kind == "dict":
        run_params = {"type": run_type}
    else:
        run_params = types.SimpleNamespace(type=run_type)
    context = types.SimpleNamespace(run_params=run_params)
    original_order = lambda *args: "native"  # noqa: E731
    namespace = {"order": original_order, "get_orders": lambda: "query"}

    # profile 模块名合法但不存在；BACKTEST 不得导入它。
    state = _install(
        helper, namespace, context,
        mode="BACKTEST", profile_module="module_that_must_not_be_imported")

    assert state == _state("BACKTEST", run_type)
    assert "blocked_mutations" not in state
    assert namespace["order"] is original_order
    assert namespace["get_orders"]() == "query"
    assert namespace["__bt_strategy_runtime_state__"]["mode"] == "BACKTEST"


def test_backtest_rejects_sim_trade_run_type(helper):
    with pytest.raises(RuntimeError, match="仅允许聚宽回测"):
        _install(helper, mode="BACKTEST")


def test_jq_keeps_native_orders_and_loads_notification_profile(helper, monkeypatch):
    _profile_module(monkeypatch)
    original_order = lambda *args: "native"  # noqa: E731
    namespace = {"order": original_order}

    state = _install(
        helper,
        namespace,
        mode="JQ",
    )

    assert state == _state(
        "JQ", "sim_trade", reason="jq", profile_module=PROFILE_MODULE
    )
    assert namespace["order"] is original_order
    assert namespace["order"]("510300.XSHG", 100) == "native"
    assert namespace["__bt_strategy_runtime_state__"]["mode"] == "JQ"
    assert helper._active_profile["host"] == "127.0.0.1"


def test_jq_rejects_backtest_run_type(helper, monkeypatch):
    _profile_module(monkeypatch)
    with pytest.raises(RuntimeError, match="JQ仅允许聚宽模拟交易"):
        _install(helper, context=_context("full_backtest"), mode="JQ")


@pytest.mark.parametrize("mode", ["QMT_REMOTE"])
def test_remote_modes_reject_backtest_run_type(helper, monkeypatch, mode):
    _profile_module(monkeypatch)
    with pytest.raises(RuntimeError, match="仅允许聚宽模拟交易"):
        _install(helper, context=_context("full_backtest"), mode=mode)


def test_jq_state_exact(helper, monkeypatch):
    _profile_module(monkeypatch)
    assert _install(helper) == _state(
        "JQ", "sim_trade", reason="jq", profile_module=PROFILE_MODULE
    )


def test_qmt_remote_state_exact(helper, monkeypatch):
    _profile_module(monkeypatch)
    assert _install(helper, mode="QMT_REMOTE") == _state(
        "QMT_REMOTE", "sim_trade",
        reason="qmt_remote_profile_validated",
        profile_module=PROFILE_MODULE,
        mirror_jq_orders=False,
        blocked_mutations=BLOCKED_MUTATIONS)


def test_qmt_remote_replaces_seven_mutations_with_guards(helper, monkeypatch):
    _profile_module(monkeypatch)
    sentinel = object()
    namespace = {"order": sentinel, "get_orders": sentinel}
    state = _install(helper, namespace, mode="QMT_REMOTE")

    assert state["blocked_mutations"] == BLOCKED_MUTATIONS  # 七名排序元组
    for name in BLOCKED_MUTATIONS:
        assert namespace[name] is not sentinel  # 无论原名是否存在都被替换
        with pytest.raises(RuntimeError, match="QMT_REMOTE模式禁止交易变更"):
            namespace[name]("510001.XSHG", 100)
    assert namespace["get_orders"] is sentinel  # 非交易名字不受影响


def test_namespace_must_be_plain_dict(helper):
    class DictSubclass(dict):
        pass

    for candidate in (DictSubclass(), "not-a-dict"):
        with pytest.raises(RuntimeError, match="namespace必须是普通dict"):
            _install(helper, candidate)


@pytest.mark.parametrize("version", [1, 2, 3, 4, 5, 6, 8, "7", True, None])
def test_expected_api_version_must_equal_seven(helper, version):
    with pytest.raises(RuntimeError, match="API版本不匹配"):
        _install(helper, expected_api_version=version)


def test_backtest_remote_validation_loads_profile_without_installing_guards(
    helper, monkeypatch
):
    _profile_module(monkeypatch)
    namespace = {"order": lambda *args: "native"}

    state = _install(
        helper,
        namespace,
        context=_context("simple_backtest"),
        mode="BACKTEST",
        validate_remote=True,
    )

    assert state["remote_validation_enabled"] is True
    assert state["reason"] == "backtest_remote_validation"
    assert namespace["order"]("510300.XSHG", 100) == "native"
    assert helper._active_profile["host"] == "127.0.0.1"


@pytest.mark.parametrize("mode", ["JQ", "QMT_REMOTE"])
def test_validate_remote_is_rejected_outside_backtest(helper, mode):
    with pytest.raises(RuntimeError, match="仅用于BACKTEST"):
        _install(helper, mode=mode, validate_remote=True)


def test_mode_is_normalised(helper, monkeypatch):
    _profile_module(monkeypatch)
    assert _install(helper, mode=" jq ")["mode"] == "JQ"


@pytest.mark.parametrize("mode", ["paper", "JQ_PAPER", "SIGNAL_ONLY", "", None, 1])
def test_invalid_mode_rejected(helper, mode):
    with pytest.raises(RuntimeError, match="运行模式必须是"):
        _install(helper, mode=mode)


@pytest.mark.parametrize("field", ["profile", "strategy_id"])
@pytest.mark.parametrize("value", ["", " padded", "x" * 129, "bad name", 7])
def test_invalid_identifiers_rejected(helper, field, value):
    with pytest.raises(RuntimeError, match=field):
        _install(helper, **{field: value})


@pytest.mark.parametrize("name", ["", "bad name", "foo-bar", 7])
def test_invalid_profile_module_name_rejected(helper, name):
    with pytest.raises(RuntimeError, match="模块名"):
        _install(helper, profile_module=name)


def test_profile_module_import_failure_breaks_exception_chain(helper):
    with pytest.raises(RuntimeError, match="无法加载运行配置模块") as excinfo:
        _install(helper, profile_module="definitely_missing_jq_runtime_config")
    assert excinfo.value.__context__ is None
    assert excinfo.value.__cause__ is None


def test_profile_schema_version_mismatch(helper, monkeypatch):
    _profile_module(monkeypatch, version=2)
    with pytest.raises(RuntimeError, match="schema版本不匹配"):
        _install(helper)


def test_profiles_must_be_plain_dict(helper, monkeypatch):
    _profile_module(monkeypatch, profiles=["not-a-dict"])
    with pytest.raises(RuntimeError, match="必须定义字典 PROFILES"):
        _install(helper)


def test_missing_profile_rejected(helper, monkeypatch):
    _profile_module(monkeypatch, profiles={"other": _valid_profile()})
    with pytest.raises(RuntimeError, match="不存在profile"):
        _install(helper)


def test_unknown_profile_field_rejected_without_echo(helper, monkeypatch):
    _profile_module(monkeypatch, profiles={PROFILE: _valid_profile(secret_field="x")})
    with pytest.raises(RuntimeError, match="包含未知字段") as excinfo:
        _install(helper)
    assert "secret_field" not in str(excinfo.value)


def test_missing_required_field_rejected(helper, monkeypatch):
    profile = _valid_profile()
    del profile["token"]
    _profile_module(monkeypatch, profiles={PROFILE: profile})
    with pytest.raises(RuntimeError, match="缺少必填字段: token"):
        _install(helper)


@pytest.mark.parametrize("host", ["", "has space", "x" * 256])
def test_invalid_host_rejected(helper, monkeypatch, host):
    _profile_module(monkeypatch, profiles={PROFILE: _valid_profile(host=host)})
    with pytest.raises(RuntimeError, match=r"profile\.host"):
        _install(helper)


@pytest.mark.parametrize("token", ["", " padded", 7])
def test_invalid_token_rejected(helper, monkeypatch, token):
    _profile_module(monkeypatch, profiles={PROFILE: _valid_profile(token=token)})
    with pytest.raises(RuntimeError, match=r"profile\.token"):
        _install(helper)


@pytest.mark.parametrize("port", [0, 65536, "58620", True])
def test_invalid_port_rejected(helper, monkeypatch, port):
    _profile_module(monkeypatch, profiles={PROFILE: _valid_profile(port=port)})
    with pytest.raises(RuntimeError, match=r"profile\.port"):
        _install(helper)


def test_profile_strategy_id_mismatch_rejected(helper, monkeypatch):
    _profile_module(
        monkeypatch, profiles={PROFILE: _valid_profile(strategy_id="other_strategy")})
    with pytest.raises(RuntimeError, match="与策略请求不一致"):
        _install(helper)


_NUMERIC_FIELDS = {
    "rpc_timeout": (5.0, 300.0),
}
_OPTIONAL_STRING_FIELDS = ["account_key", "tls_cert"]


@pytest.mark.parametrize("field", sorted(_NUMERIC_FIELDS))
@pytest.mark.parametrize("value", ["10", True, None])
def test_profile_numeric_fields_reject_non_numeric(helper, monkeypatch, field, value):
    _profile_module(monkeypatch, profiles={PROFILE: _valid_profile(**{field: value})})
    with pytest.raises(RuntimeError, match="必须是有限数值"):
        _install(helper)


@pytest.mark.parametrize("field", sorted(_NUMERIC_FIELDS))
@pytest.mark.parametrize("value", [float("nan"), float("inf")])
def test_profile_numeric_fields_reject_non_finite(helper, monkeypatch, field, value):
    _profile_module(monkeypatch, profiles={PROFILE: _valid_profile(**{field: value})})
    with pytest.raises(RuntimeError, match="必须是有限数值"):
        _install(helper)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("rpc_timeout", 4.9),
        ("rpc_timeout", 300.1),
    ],
)
def test_profile_numeric_fields_reject_out_of_range(helper, monkeypatch, field, value):
    _profile_module(monkeypatch, profiles={PROFILE: _valid_profile(**{field: value})})
    with pytest.raises(RuntimeError, match="必须在"):
        _install(helper)


@pytest.mark.parametrize(
    ("field", "value"),
    [(f, bound) for f, bounds in _NUMERIC_FIELDS.items() for bound in bounds],
)
def test_profile_numeric_fields_accept_boundaries(helper, monkeypatch, field, value):
    _profile_module(monkeypatch, profiles={PROFILE: _valid_profile(**{field: value})})
    assert _install(helper)["mode"] == "JQ"


@pytest.mark.parametrize("field", _OPTIONAL_STRING_FIELDS)
@pytest.mark.parametrize("value", [None, "main"])
def test_profile_optional_strings_accept_none_and_strings(
    helper, monkeypatch, field, value
):
    _profile_module(monkeypatch, profiles={PROFILE: _valid_profile(**{field: value})})
    assert _install(helper)["mode"] == "JQ"


@pytest.mark.parametrize("field", _OPTIONAL_STRING_FIELDS)
@pytest.mark.parametrize("value", ["", " padded", 7])
def test_profile_optional_strings_reject_invalid(helper, monkeypatch, field, value):
    _profile_module(monkeypatch, profiles={PROFILE: _valid_profile(**{field: value})})
    with pytest.raises(RuntimeError, match=r"profile\.{}".format(field)):
        _install(helper)


@pytest.mark.parametrize(
    ("field", "value"),
    [("port", 1), ("port", 65535)],
)
def test_profile_port_accepts_boundaries(helper, monkeypatch, field, value):
    _profile_module(monkeypatch, profiles={PROFILE: _valid_profile(**{field: value})})
    assert _install(helper)["mode"] == "JQ"


def test_profile_entry_must_be_plain_dict(helper, monkeypatch):
    _profile_module(monkeypatch, profiles={PROFILE: ["not-a-dict"]})
    with pytest.raises(RuntimeError, match="必须是字典"):
        _install(helper)


def test_profiles_names_must_be_plain_strings(helper, monkeypatch):
    _profile_module(monkeypatch, profiles={1: _valid_profile()})
    with pytest.raises(RuntimeError, match="profile名称必须是普通字符串"):
        _install(helper)


def test_profile_field_names_must_be_plain_strings(helper, monkeypatch):
    profile = _valid_profile()
    profile[1] = "x"
    _profile_module(monkeypatch, profiles={PROFILE: profile})
    with pytest.raises(RuntimeError, match="非普通字符串字段"):
        _install(helper)


def test_idempotent_reinstall_returns_equal_state(helper, monkeypatch):
    _profile_module(monkeypatch)
    namespace = {}
    first = _install(helper, namespace)
    second = _install(helper, namespace)
    assert first == second
    assert first is not second


def test_signature_drift_rejected(helper, monkeypatch):
    _profile_module(monkeypatch)
    namespace = {}
    _install(helper, namespace)
    with pytest.raises(RuntimeError, match="签名漂移"):
        _install(helper, namespace, profile="other-profile")


def test_previous_generation_record_rejected(helper, monkeypatch):
    _profile_module(monkeypatch)
    namespace = {"__bt_strategy_runtime_state__": {"token": object(), "mode": "JQ"}}
    with pytest.raises(RuntimeError, match="上一代helper"):
        _install(helper, namespace)


def test_missing_namespace_record_rejected(helper, monkeypatch):
    _profile_module(monkeypatch)
    namespace = {}
    _install(helper, namespace)
    del namespace["__bt_strategy_runtime_state__"]
    with pytest.raises(RuntimeError, match="运行记录缺失"):
        _install(helper, namespace)


def test_idempotent_reinstall_restores_guards(helper, monkeypatch):
    _profile_module(monkeypatch)
    namespace = {}
    first = _install(helper, namespace, mode="QMT_REMOTE")
    # 模拟平台重建 namespace 后 guard 丢失，恢复出原生 order。
    namespace["order"] = lambda *args: "native"

    second = _install(helper, namespace, mode="QMT_REMOTE")

    assert second == first
    with pytest.raises(RuntimeError, match="QMT_REMOTE模式禁止交易变更"):
        namespace["order"]("510001.XSHG", 100)


def test_reinstall_with_changed_run_type_rejected(helper, monkeypatch):
    _profile_module(monkeypatch)
    namespace = {}
    _install(helper, namespace)
    with pytest.raises(RuntimeError, match="签名漂移"):
        _install(helper, namespace, context=_context("full_backtest"))


class _FakeSocket(object):
    def settimeout(self, timeout):
        self.timeout = timeout

    def close(self):
        self.closed = True


def test_read_only_rpc_retries_connection_then_succeeds(helper, monkeypatch):
    _profile_module(monkeypatch)
    _install(helper)
    attempts = []
    sent = []
    sleeps = []

    def connect(address, timeout):
        attempts.append((address, timeout))
        if len(attempts) < 3:
            raise OSError("temporary network failure")
        return _FakeSocket()

    def read_message(sock):
        if not sent or sent[-1]["type"] == "handshake":
            return {"type": "handshake_ack"}
        return {"type": "response", "id": sent[-1]["id"], "payload": {"ok": True}}

    monkeypatch.setattr(helper.socket, "create_connection", connect)
    monkeypatch.setattr(helper, "_send_message", lambda sock, message: sent.append(message))
    monkeypatch.setattr(helper, "_read_message", read_message)
    monkeypatch.setattr(helper.time, "sleep", sleeps.append)

    assert helper._strategy_request("strategy.get_snapshot", {}) == {"ok": True}
    assert len(attempts) == 3
    assert sleeps == [0.5, 0.5]


def test_submit_targets_does_not_retry_after_request_may_be_sent(
    helper, monkeypatch
):
    _profile_module(monkeypatch)
    _install(helper, mode="QMT_REMOTE")
    attempts = []
    reads = []

    def connect(address, timeout):
        attempts.append((address, timeout))
        return _FakeSocket()

    def read_message(sock):
        reads.append(True)
        if len(reads) == 1:
            return {"type": "handshake_ack"}
        raise socket.timeout("response lost")

    monkeypatch.setattr(helper.socket, "create_connection", connect)
    monkeypatch.setattr(helper, "_send_message", lambda sock, message: None)
    monkeypatch.setattr(helper, "_read_message", read_message)
    monkeypatch.setattr(
        helper.time, "sleep", lambda seconds: pytest.fail("must not retry")
    )

    with pytest.raises(RuntimeError, match="请求可能已执行.*停止自动重发"):
        helper.submit_targets({"510300.XSHG": 1}, "same-key")
    assert len(attempts) == 1


def test_jq_cannot_submit_qmt_targets(helper, monkeypatch):
    _profile_module(monkeypatch)
    _install(helper, mode="JQ")

    with pytest.raises(RuntimeError, match="只有QMT_REMOTE模式"):
        helper.submit_targets({"510300.XSHG": 1}, "jq-must-not-submit")


def test_typed_execution_request_is_explicitly_encoded_at_rpc_boundary(
    helper, monkeypatch
):
    _profile_module(monkeypatch)
    _install(helper, mode="QMT_REMOTE")
    calls = []
    monkeypatch.setattr(
        helper,
        "_strategy_request",
        lambda action, payload: calls.append((action, payload)) or {},
    )
    execution = helper.ExecutionRequest(
        style=helper.ConditionalLimitExecution(
            2_000, helper.ConditionalLimitPriceMode.BOUNDARY
        ),
        follow_up=helper.FollowUpPolicy.UNTIL_FILLED_TODAY,
        repricing=helper.RepricingPolicy.KEEP_ORIGINAL,
    )

    helper.submit_targets(
        {"510300.XSHG": 1}, "typed-execution", execution=execution
    )

    assert calls == [
        (
            "strategy.submit_targets",
            {
                "weights": {"510300.XSHG": 1},
                "idempotency_key": "typed-execution",
                "execution": {
                    "schema_version": 1,
                    "style": {
                        "type": "CONDITIONAL_LIMIT",
                        "price_band_ppm": 2_000,
                        "price_mode": "BOUNDARY",
                    },
                    "follow_up": "UNTIL_FILLED_TODAY",
                    "repricing": "KEEP_ORIGINAL",
                },
            },
        )
    ]


def test_cancel_runtime_targets_clears_confirmed_remote_intent(
    helper, monkeypatch
):
    _profile_module(monkeypatch)
    _install(helper, mode="QMT_REMOTE")
    helper._runtime_target_state = {"intent_id": "intent-1"}
    calls = []
    monkeypatch.setattr(
        helper,
        "_strategy_request",
        lambda action, payload: calls.append((action, payload))
        or {"canceled": True},
    )

    assert helper.cancel_runtime_targets() is True
    assert helper._runtime_target_state is None
    assert calls == [
        ("strategy.cancel_intent", {"intent_id": "intent-1"})
    ]


def test_notify_target_buy_plan_uses_jq_mode_and_no_retry_after_send(
    helper, monkeypatch
):
    _profile_module(monkeypatch)
    _install(helper, mode="JQ")
    sent = []
    reads = []

    monkeypatch.setattr(
        helper.socket,
        "create_connection",
        lambda address, timeout: _FakeSocket(),
    )
    monkeypatch.setattr(
        helper, "_send_message", lambda sock, message: sent.append(message)
    )

    def read_message(sock):
        reads.append(True)
        if len(reads) == 1:
            return {"type": "handshake_ack"}
        raise socket.timeout("response lost")

    monkeypatch.setattr(helper, "_read_message", read_message)
    monkeypatch.setattr(
        helper.time, "sleep", lambda seconds: pytest.fail("must not retry")
    )

    with pytest.raises(RuntimeError, match="请求可能已执行.*停止自动重发"):
        helper.notify_target_buy_plan(
            [{"security": "510300.XSHG", "quantity": 1000, "amount": 2500}]
        )
    requests = [item for item in sent if item.get("type") == "request"]
    assert len(requests) == 1
    assert requests[0]["action"] == "strategy.notify_target_buy_plan"
    assert requests[0]["payload"]["mode"] == "JQ"


def test_server_error_is_not_retried_or_echoes_request_payload(helper, monkeypatch):
    _profile_module(monkeypatch)
    _install(helper)
    attempts = []
    reads = []

    monkeypatch.setattr(
        helper.socket,
        "create_connection",
        lambda address, timeout: attempts.append(address) or _FakeSocket(),
    )

    def read_message(sock):
        reads.append(True)
        if len(reads) == 1:
            return {"type": "handshake_ack"}
        return {"type": "error", "code": "ACCOUNT_NOT_READY", "message": "rejected"}

    monkeypatch.setattr(helper, "_send_message", lambda sock, message: None)
    monkeypatch.setattr(helper, "_read_message", read_message)
    monkeypatch.setattr(
        helper.time, "sleep", lambda seconds: pytest.fail("must not retry")
    )

    with pytest.raises(RuntimeError, match="ACCOUNT_NOT_READY: rejected") as excinfo:
        helper._strategy_request("strategy.ensure_account", {"secret": "do-not-echo"})
    assert "do-not-echo" not in str(excinfo.value)
    assert len(attempts) == 1
