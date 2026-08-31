from types import SimpleNamespace

import pytest

from bullet_trade.server.config import build_server_config


def test_strategy_enabled_ids_are_parsed_and_deduplicated(monkeypatch):
    monkeypatch.setenv(
        "QMT_STRATEGY_ENABLED_IDS",
        "good_etf_remote; good_etf_remote,another_strategy",
    )

    config = build_server_config(SimpleNamespace())

    assert config.strategy_enabled_ids == [
        "good_etf_remote",
        "another_strategy",
    ]


def test_unpriced_fill_policy_is_validated(monkeypatch):
    monkeypatch.setenv(
        "QMT_STRATEGY_UNPRICED_FILL_POLICY",
        "conservative_order_price",
    )
    assert (
        build_server_config(SimpleNamespace()).strategy_unpriced_fill_policy
        == "CONSERVATIVE_ORDER_PRICE"
    )

    monkeypatch.setenv("QMT_STRATEGY_UNPRICED_FILL_POLICY", "guess")
    with pytest.raises(ValueError, match="UNPRICED_FILL_POLICY"):
        build_server_config(SimpleNamespace())
