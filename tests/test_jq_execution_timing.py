import json
from datetime import datetime
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
