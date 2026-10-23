"""The half-invested route declares policy, not infrastructure in its strategy."""
import pickle

import pytest

from tests.strategies.test_good_etf_v2 import setup_rotation


@pytest.mark.parametrize('supported', [True, False])
def test_independent_preopen_request_is_durable_and_feature_gated(monkeypatch, supported):
    helper, jq, runtime, _, _, submitted = setup_rotation(monkeypatch)
    if not supported:
        runtime.state['server_features'].pop('independent_preopen_rotation_v1')
    seen, original = [], runtime.submit_targets
    def submit(*args, **kwargs):
        seen.append(kwargs)
        return original(*args)
    runtime.submit_targets = submit
    jq.context.current_dt = jq.context.current_dt.replace(minute=26)
    runtime.prepare_rebalance(jq.context, lambda ctx: ({'BUY': .5}, jq.prices),
                              qmt_execute_time='09:30',
                              qmt_execution=helper.default_etf_preopen_execution())
    assert bool(submitted) is supported
    assert not jq.calls
    assert pickle.loads(pickle.dumps(jq.g)).bt_open_decision['qmt_scheduled'] is supported
    if supported:
        execution = submitted[0][3]
        assert execution.order_sequence is helper.OrderSequence.INDEPENDENT
        assert execution.style == helper.LimitExecution(5000, preopen=True)
        assert execution.sell_style.limit_prices == {'QMT_OLD': 7200000, 'QMT_TRIM': 9000000}
        assert execution.sell_style.preopen and seen == [{}]
        from bullet_trade.server.strategy.execution import execution_request_from_wire
        assert execution_request_from_wire(helper._execution_to_wire(execution)).order_sequence.value == 'INDEPENDENT'
    runtime.submit_targets = lambda *a, **k: pytest.fail('No 09:30 fallback')
    monkeypatch.setattr(helper, 'get_portfolio', lambda **k: pytest.fail('No QMT query'))
    jq.context.current_dt = jq.context.current_dt.replace(minute=30)
    runtime.execute_prepared_rebalance(jq.context, jq_wait_for_sells=True, qmt_server_rotation=True)
    assert 'BUY' in jq.positions
