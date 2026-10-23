"""Build and accepted-policy evidence add no RPCs or trading decisions."""

from types import SimpleNamespace

import pytest

from tests.test_jq_latency import helper, rpc_harness
from tests.test_jq_strategy_runtime import _state


def test_build_id_is_sent_on_existing_prepare_and_submit_requests(helper, monkeypatch):
    connections, messages, _ = rpc_harness(helper, monkeypatch)
    for action in ("strategy.prepare_session", "strategy.submit_targets"):
        original = {"idempotency_key": "one-target"}
        helper._strategy_request(action, original)
        request = messages[-1][1]
        assert request["payload"]["runtime_build_id"] == helper.STRATEGY_RUNTIME_BUILD_ID
        assert original == {"idempotency_key": "one-target"}
    assert len(connections) == 1
    assert [m["type"] for _, m in messages] == ["handshake", "request", "request"]


def runtime_and_logs(helper):
    logs = []
    runtime = helper.JoinQuantRuntime(_state("QMT_REMOTE", "sim_trade"), {
        "log": SimpleNamespace(info=logs.append, warn=logs.append),
    })
    return runtime, logs


def test_rebuild_log_distinguishes_builds_with_same_api(helper):
    runtime, logs = runtime_and_logs(helper)
    runtime.log_process_initialize()
    assert any("helper API=26" in text and "build=" + helper.STRATEGY_RUNTIME_BUILD_ID in text
               for text in logs)


def test_effective_policy_log_uses_server_echo_not_requested_offset(helper):
    runtime, logs = runtime_and_logs(helper)
    requested = helper.default_etf_preopen_execution()
    old = helper.ExecutionRequest(
        style=helper.LimitExecution(2000, preopen=True),
        sell_style=helper.LimitExecution(0, preopen=True),
        order_sequence=helper.OrderSequence.INDEPENDENT,
    )
    runtime._log_effective_execution({
        "effective_execution": helper._execution_to_wire(old),
        "server_runtime_build_id": "server-test-v1",
    }, requested)
    actual = next(line for line in logs if "QMT实际执行参数 |" in line)
    assert "server_build=server-test-v1" in actual
    assert "买入偏移ppm=2000" in actual and "买入偏移ppm=5000" not in actual
    assert "顺序=INDEPENDENT" in actual
    assert any("不修改旧委托或自动重发" in line for line in logs)


@pytest.mark.parametrize("effective", [None, {"broken": True}])
def test_missing_or_invalid_echo_is_not_claimed_as_verified(helper, effective):
    runtime, logs = runtime_and_logs(helper)
    runtime._log_effective_execution({"effective_execution": effective}, helper.ExecutionRequest())
    assert any("未核验" in line for line in logs)
    assert not any("QMT实际执行参数 |" in line for line in logs)


def test_diagnostic_failure_never_changes_accepted_result_or_repeats_submit(helper, monkeypatch):
    runtime, _ = runtime_and_logs(helper)
    result = {"intent": {"state": "EXECUTING"}}
    calls = []
    monkeypatch.setattr(helper, "submit_runtime_targets", lambda *a, **k: calls.append(True) or result)
    monkeypatch.setattr(runtime, "_log_effective_execution", lambda *a: (_ for _ in ()).throw(OSError("log unavailable")))
    assert runtime.submit_targets(object(), {}, {}, "one-target", helper.ExecutionRequest()) is result
    assert calls == [True]
