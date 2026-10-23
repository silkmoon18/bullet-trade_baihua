"""Compact diagnostics preserve execution evidence without any extra queries."""
import json
import pickle
from types import SimpleNamespace as NS

import pytest

from tests.test_jq_strategy_runtime import helper, _state


def test_tiny_name_queries_are_aggregated_and_do_not_evict_order_milestones(helper, monkeypatch):
    clock = [0.]
    monkeypatch.setattr(helper.time, 'perf_counter', lambda: clock[0])
    trace = helper._ExecutionTrace('jq.scheduler_callback', 'route:today')
    for _ in range(425):
        start = clock[0]
        clock[0] += .00001
        trace.record('jq.api.get_security_info', start)
    start = clock[0]
    clock[0] += .2
    trace.record('rpc.strategy.submit_targets', start)
    trace.remote = [{'action': 'strategy.submit_targets', 'phase': 'server.accept_target',
                     'span_id': 'server:1', 'elapsed_ms': 150, 'stages': {'nested': {'ms': 150}},
                     'started_at': '09:26:10.000', 'finished_at': '09:26:10.150'}]
    line = trace.message()
    record = json.loads(line.split(' | ', 1)[1])
    assert record['detail'] == 'compact'
    assert record['stages']['jq.api.get_security_info']['calls'] == 425
    assert record['events_aggregated'] == 425 and record['events_dropped'] == 0
    assert [item['stage'] for item in record['events']] == ['rpc.strategy.submit_targets']
    assert record['events'][0]['start_offset_ms'] < record['events'][0]['finish_offset_ms']
    assert record['remote'][0]['span_id'] == 'server:1' and 'stages' not in record['remote'][0]
    assert 'stages' in trace.remote[0]  # Compact formatting never mutates received evidence.
    assert len(line.encode('utf8')) < 1800


@pytest.mark.parametrize('failed,seconds', [(True, .001), (False, 2.1)])
def test_errors_and_slow_calls_keep_detailed_transport_and_remote_evidence(helper, monkeypatch, failed, seconds):
    clock = [0.]
    monkeypatch.setattr(helper.time, 'perf_counter', lambda: clock[0])
    trace = helper._ExecutionTrace('jq.scheduler_callback', 'route:today')
    clock[0] = seconds
    trace.record('rpc.socket_receive_wait_including_server', 0., 'error' if failed else 'ok')
    trace.remote = [{'action': 'read', 'stages': {'server': {'calls': 1, 'ms': 1}}}]
    record = json.loads(trace.message().split(' | ', 1)[1])
    assert record['detail'] == 'diagnostic'
    assert record['stages']['rpc.socket_receive_wait_including_server']['errors'] == int(failed)
    assert record['remote'] == trace.remote and len(record['events']) == 1


def test_schedule_rebuild_only_repeats_summary_when_schedule_changes(helper):
    logs, registrations = [], []
    g = NS()
    namespace = {'g': g, 'log': NS(info=logs.append),
                 'run_daily': lambda *a, **kw: registrations.append((a, kw)),
                 'unschedule_all': lambda: registrations.clear()}
    callback = lambda context: None
    runtime = helper.JoinQuantRuntime(_state('JQ', 'sim_trade'), namespace)
    args = (callback, callback, callback, ('10:30',), callback)
    runtime.schedule_daily(*args)
    first = list(registrations)
    namespace['g'] = pickle.loads(pickle.dumps(g))
    restored = helper.JoinQuantRuntime(_state('JQ', 'sim_trade'), namespace)
    restored.schedule_daily(*args, replace_existing=True)
    assert registrations == first
    assert sum(line.startswith('任务调度完成 | ') for line in logs) == 1
    assert '日程未变' in logs[-1]
    restored.schedule_daily(*args, market_open_time='09:31', replace_existing=True)
    assert sum(line.startswith('任务调度完成 | ') for line in logs) == 2
    assert '下单=09:31' in logs[-1]


def test_preopen_half_percent_does_not_change_other_execution_factories(helper):
    preopen = helper.default_etf_preopen_execution()
    assert preopen.style == helper.LimitExecution(5000, preopen=True)
    assert preopen.sell_style == helper.LimitExecution(0, preopen=True)
    assert preopen.order_sequence == helper.OrderSequence.INDEPENDENT
    assert helper.default_etf_rebalance_execution().style.price_band_ppm == 2000
    assert helper.default_etf_take_profit_execution().style.price_band_ppm == 2000
