"""Observability must not add broker calls or alter failures."""
import asyncio
import json

import pytest

from bullet_trade.server import timing
from tests.server.strategy.test_strategy_api import api, drain_execution, SECURITY


@pytest.mark.asyncio
async def test_trace_contains_stages_and_keeps_parallel_requests_isolated(monkeypatch):
    logs = []
    monkeypatch.setattr('builtins.print', lambda *a, **k: logs.append(a))
    @timing.traced('test.request')
    async def request(payload):
        with timing.span('local.work'):
            await asyncio.sleep(0)
            assert timing.current_trace().trace_id == payload['trace_id']
        return {'ok': True}

    replies = await asyncio.gather(*(request({'trace_id': str(i)}) for i in range(2)))
    assert [r['_bt_server_timing']['trace_id'] for r in replies] == ['0', '1']
    assert all(r['ok'] and r['_bt_server_timing']['stages']['local.work']['calls'] == 1 for r in replies)
    assert timing.current_trace() is None
    assert len(logs) == 2


@pytest.mark.asyncio
async def test_logger_failure_does_not_mask_success_or_business_exception(monkeypatch):
    monkeypatch.setattr('builtins.print', lambda *a, **k: (_ for _ in ()).throw(OSError('output unavailable')))

    @timing.traced('test.request')
    async def request(payload):
        if payload['fail']:
            raise ValueError('business failure')
        return {'accepted': True}

    assert (await request({'fail': False}))['accepted']
    with pytest.raises(ValueError, match='business failure'):
        await request({'fail': True})
    assert timing.current_trace() is None


def test_trace_memory_is_bounded_and_counts_are_complete():
    trace = timing.Trace('test', 'public-key')
    for _ in range(1000):
        trace.record('qmt.query', trace.started)
    assert len(trace.events) == 128
    assert trace.summary()['stages']['qmt.query']['calls'] == 1000
    assert trace.summary()['events_dropped'] == 872
    json.dumps(trace.summary(), allow_nan=False)


@pytest.mark.asyncio
async def test_parent_span_links_and_closed_inherited_context_is_not_reused():
    @timing.traced('nested.child')
    async def child(payload):
        return {'ok': True}
    @timing.traced('nested.parent')
    async def parent(payload):
        return {'child': await child({})}
    result = await parent({})
    assert result['child']['_bt_server_timing']['parent_span_id'] == result['_bt_server_timing']['span_id']
    trace = timing.Trace('completed')
    token = timing._current.set(trace)
    try:
        trace.flush()
        assert timing.current_trace() is None
    finally:
        timing._current.reset(token)


def test_timing_only_prints_without_file_or_application_log_handlers(monkeypatch, capsys):
    def forbidden(*args, **kwargs):
        pytest.fail('Timing diagnostics must not write files or invoke log handlers')

    monkeypatch.setattr('builtins.open', forbidden)
    monkeypatch.setattr('logging.Logger._log', forbidden)
    trace = timing.Trace('test.stdout', 'public-key')
    trace.record('local.work', trace.started)
    trace.flush()
    timing.print_timing('QMT首笔快照 | strategy_id=%s | 快照年龄=%.0fms', 'test', 2.)
    output = capsys.readouterr().out
    assert '执行链路耗时 | ' in output and 'test.stdout' in output
    assert 'QMT首笔快照 | strategy_id=test | 快照年龄=2ms' in output


def test_idle_trace_does_not_print(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('Idle account heartbeat must not flood timing output')

    monkeypatch.setattr('builtins.print', forbidden)
    trace = timing.Trace('test.idle')
    trace.tags['had_active_intent'] = False
    trace.flush()


def test_unchanged_background_is_aggregated_but_actions_errors_and_progress_keep_detail(monkeypatch):
    clock, printed = [0.], []
    monkeypatch.setattr(timing.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(timing, 'print_timing', lambda *a, **k: printed.append(a))
    policy = timing.QuietTracePolicy()
    def trace(state=0):
        row = timing.Trace('server.resume_target', 'strategy:today')
        row.progress['strategy'] = state
        return row
    assert policy.detail(trace())
    for _ in range(1000):
        assert not policy.detail(trace())
    assert not printed
    clock[0] = 1800
    assert not policy.detail(trace())
    assert len(printed) == 1 and '汇总' in printed[0][0]
    assert policy.detail(trace(1))
    failed = trace(1)
    failed.failed = True
    assert policy.detail(failed)
    submitted = trace(1)
    submitted.record('bridge./place_order', submitted.started)
    assert policy.detail(submitted)
    for i in range(300):
        row = trace()
        row.trace_id = str(i)
        policy.detail(row)
    assert len(policy.rows) == 256


def test_progress_observation_propagates_to_open_parent_without_database_or_broker():
    parent = timing.Trace('parent')
    token = timing._current.set(parent)
    try:
        child = timing.Trace('child')
        child_token = timing._current.set(child)
        try:
            timing.observe_progress('strategy', ('READY', 1))
            assert child.progress == parent.progress == {'strategy': ('READY', 1)}
        finally:
            timing._current.reset(child_token)
    finally:
        timing._current.reset(token)


@pytest.mark.asyncio
async def test_prepare_advertises_scheduler_and_ack_reports_processing_time(api, monkeypatch):
    service, broker, account, _ = api
    monkeypatch.setattr(service, '_can_dispatch_now', lambda: True)
    prepared = await service.prepare_session(account, 'default', {'strategy_id': 'good_etf'})
    assert prepared['server_features']['scheduled_targets_v1'] is True
    assert broker.order_calls == 0
    result = await service.submit_targets(account, 'default', {
        'strategy_id': 'good_etf', 'idempotency_key': 'timing-test', 'trace_id': 'good_etf:timing-test',
        'weights': {SECURITY: .5}, 'marks': {SECURITY: 10}})
    detail = result['_bt_server_timing']
    assert detail['trace_id'] == 'good_etf:timing-test'
    assert detail['stages']['local.plan_and_persist_target']['calls'] == 1
    assert detail['stages']['broker.snapshot_reads']['calls'] == 1
    assert 'events' not in detail  # Full detail stays local, not on the network hot path.
    await drain_execution(service)
    assert broker.order_calls == 1
    await service.close()
