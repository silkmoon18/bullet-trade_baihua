from types import SimpleNamespace

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
