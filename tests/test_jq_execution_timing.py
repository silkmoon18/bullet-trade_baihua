import json
import pickle
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS

import pytest

from tests.test_jq_strategy_runtime import helper, _state


def test_separate_schedule_log_reports_real_0926_selection(helper):
    logs = []
    runtime = helper.JoinQuantRuntime(_state('JQ', 'sim_trade'), {
        'run_daily': lambda *a, **k: None, 'log': NS(info=logs.append)})
    fn = lambda context: None
    runtime.schedule_daily(fn, fn, fn, ('10:30',), fn, opening_decision=fn,
                           opening_decision_time='09:26', market_open_time='09:30', sell_then_buy=True)
    assert '选股=09:26 下单=09:30' in logs[0]


def test_observation_preserves_read_calls_results_and_namespace_on_error(helper):
    calls, logs = [], []
    fn = lambda: calls.append('data') or {'price': 1.0}
    finance = NS(run_query=lambda arg: calls.append(arg) or 'rows', FUND_INVEST_TARGET='table')
    namespace = {'get_current_data': fn, 'finance': finance, 'log': NS(info=logs.append)}
    runtime = helper.JoinQuantRuntime(_state('JQ', 'sim_trade'), namespace)
    context = NS(current_dt=datetime(2026, 10, 15, 9, 20))

    def decide(context):
        assert namespace['finance'].FUND_INVEST_TARGET == 'table'
        assert namespace['get_current_data']() == {'price': 1.0}
        assert namespace['finance'].run_query('query') == 'rows'
        raise ValueError('expected')

    with pytest.raises(ValueError, match='expected'):
        runtime.run_decision(context, decide)
    assert calls == ['data', 'query']
    assert namespace['get_current_data'] is fn and namespace['finance'] is finance
    record = json.loads(logs[-1].split(' | ', 1)[1])
    assert record['stages']['jq.api.get_current_data']['calls'] == 1
    assert record['stages']['jq.api.finance.run_query']['calls'] == 1
    assert record['stages']['jq.prepare_candidates']['errors'] == 1
    assert getattr(helper._execution_timing, 'current', None) is None


def test_backtest_observation_does_not_wrap_or_log(helper):
    logs = []
    runtime = helper.JoinQuantRuntime(_state('BACKTEST', 'simple_backtest'), {'log': NS(info=logs.append)})
    assert runtime.run_decision(NS(), lambda context: 'result') == 'result'
    assert logs == []


def test_outer_callback_measures_schedule_delay_and_restores_from_names(helper, monkeypatch):
    logs, schedules, calls = [], {}, []
    planned = datetime(2026, 10, 22, 9, 26, tzinfo=timezone(timedelta(hours=8)))
    monkeypatch.setattr(helper.time, 'time', lambda: planned.timestamp() + 10.120)
    namespace = {'g': NS(), 'STRATEGY_NAME': 'ETF竞价折价轮动_jq',
                 'run_daily': lambda callback, *a, **kw: schedules.setdefault(a[0] if a else kw['time'], callback),
                 'log': NS(info=logs.append), 'get_current_data': lambda: calls.append('existing query') or 123}
    def decide(context):
        assert namespace['get_current_data']() == 123
        return 'same decision'
    namespace['decide'] = decide
    runtime = helper.JoinQuantRuntime(_state('JQ', 'sim_trade'), namespace)
    runtime.schedule_daily(decide, decide, decide, ('10:30',), decide, market_open_time='09:26')
    entry = pickle.loads(pickle.dumps(schedules['09:26']))
    assert entry is helper._scheduled_orders
    namespace['g'] = pickle.loads(pickle.dumps(namespace['g']))
    restored = helper.JoinQuantRuntime(_state('JQ', 'sim_trade'), namespace)
    monkeypatch.setattr(helper, '_active_joinquant_runtime', restored)
    context = NS(current_dt=planned.replace(tzinfo=None))
    assert entry(context) == 'same decision'
    record = json.loads(next(line.split(' | ', 1)[1] for line in reversed(logs)
                             if line.startswith('执行链路耗时 | ')))
    assert record['phase'] == 'jq.scheduler_callback'
    assert record['callback']['scheduled_time'] == '09:26'
    assert record['callback']['entry_delay_ms'] == 10120
    assert record['callback']['name'] == 'decide'
    assert record['callback']['strategy_name'] == 'ETF竞价折价轮动_jq'
    assert record['started_at'] == record['callback']['entered_at']
    assert record['stages']['jq.api.get_current_data']['calls'] == 1
    assert record['events'] and record['process_instance']
    assert calls == ['existing query']


def test_schedule_timing_never_masks_callback_failure_or_adds_backtest_dispatch(helper, monkeypatch):
    schedules, logs = [], []
    def failed(context):
        raise ValueError('business exception')
    namespace = {'run_daily': lambda fn, *a, **k: schedules.append(fn), 'log': NS(info=logs.append)}
    backtest = helper.JoinQuantRuntime(_state('BACKTEST', 'simple_backtest'), namespace)
    backtest.schedule_daily(failed, failed, failed, (), failed)
    assert all(fn is failed for fn in schedules)
    schedules.clear()
    runtime = helper.JoinQuantRuntime(_state('JQ', 'sim_trade'), namespace)
    runtime.schedule_daily(failed, failed, failed, (), failed)
    monkeypatch.setattr(helper, '_active_joinquant_runtime', runtime)
    with pytest.raises(ValueError, match='business exception'):
        schedules[0](NS(current_dt=datetime(2026, 10, 22, 9, 20)))
    record = json.loads(logs[-1].split(' | ', 1)[1])
    assert record['stages']['jq.scheduler_callback']['errors'] == 1
    assert getattr(helper._execution_timing, 'current', None) is None
