import asyncio
import json
from types import SimpleNamespace

import pytest

from bullet_trade.server.config import build_server_config
from bullet_trade.server.dashboard import (
    DashboardHTTPServer,
    DashboardReadModel,
    tail_log_file,
)
from bullet_trade.server.strategy import SQLiteStrategyRepository, money_to_units


def _read_model(tmp_path):
    database = tmp_path / "ledger.db"
    repository = SQLiteStrategyRepository(database)
    repository.initialize()
    repository.create_physical_account(
        "qmt:default", "QMT", "masked", money_to_units("20000")
    )
    repository.create_strategy_account(
        "good_etf_remote",
        "good_etf_remote",
        "qmt:default",
        money_to_units("10000"),
    )
    return DashboardReadModel(str(database))


def test_dashboard_read_model_records_and_lists_snapshot(tmp_path):
    model = _read_model(tmp_path)
    model.record_snapshot(
        {
            "account_id": "good_etf_remote",
            "as_of": "2026-09-02T09:31:08+08:00",
            "cash": 500.25,
            "positions_value": 9600.75,
            "total_value": 10101.0,
            "total_pnl": 101.0,
            "nav": 1.0101,
            "fees": None,
            "performance_ready": False,
        }
    )
    # Same minute is an update, not a duplicate point.
    model.record_snapshot(
        {
            "account_id": "good_etf_remote",
            "as_of": "2026-09-02T09:31:59+08:00",
            "cash": 500.25,
            "positions_value": 9610.75,
            "total_value": 10111.0,
            "total_pnl": None,
            "nav": None,
            "fees": None,
            "performance_ready": False,
        }
    )

    strategies = model.list_strategies()
    activity = model.strategy_activity("good_etf_remote")

    assert [item["strategy_id"] for item in strategies] == ["good_etf_remote"]
    assert len(activity["history"]) == 1
    assert activity["history"][0]["total_value"] == 10111.0
    assert activity["history"][0]["nav"] is None
    assert activity["orders"] == []
    assert activity["fills"] == []


async def _request(port, authorization=""):
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    auth = "Authorization: {}\r\n".format(authorization) if authorization else ""
    writer.write(
        (
            "GET /api/v1/dashboard?strategy_id=good_etf_remote HTTP/1.1\r\n"
            "Host: localhost\r\n"
            + auth
            + "Connection: close\r\n\r\n"
        ).encode("ascii")
    )
    await writer.drain()
    response = await reader.read()
    writer.close()
    await writer.wait_closed()
    head, body = response.split(b"\r\n\r\n", 1)
    return head.decode("ascii"), json.loads(body.decode("utf-8"))


@pytest.mark.asyncio
async def test_dashboard_http_is_get_only_and_token_protected():
    calls = []

    async def provider(strategy_id, log_limit):
        calls.append((strategy_id, log_limit))
        return {"selected_strategy_id": strategy_id, "server": {"process_alive": True}}

    server = DashboardHTTPServer("127.0.0.1", 0, "test-token", provider)
    await server.start()
    try:
        unauthorized_head, unauthorized = await _request(server.bound_port)
        authorized_head, authorized = await _request(
            server.bound_port, "Bearer test-token"
        )
    finally:
        await server.stop()

    assert " 401 " in unauthorized_head
    assert unauthorized == {"error": "unauthorized"}
    assert " 200 " in authorized_head
    assert authorized["selected_strategy_id"] == "good_etf_remote"
    assert calls == [("good_etf_remote", 300)]


def test_tail_log_redacts_credentials(tmp_path):
    log_file = tmp_path / "server.log"
    log_file.write_text(
        "INFO ready\n"
        "WARNING token=abc password: xyz\n"
        "INFO https://open.feishu.cn/open-apis/bot/v2/hook/private\n",
        encoding="utf-8",
    )

    lines = tail_log_file(str(log_file), 10)

    assert lines[0] == "INFO ready"
    assert "abc" not in lines[1]
    assert "xyz" not in lines[1]
    assert lines[2].endswith("/hook/[REDACTED]")


def test_dashboard_config_requires_ledger_and_token(monkeypatch, tmp_path):
    monkeypatch.setenv("QMT_DASHBOARD_ENABLED", "true")
    monkeypatch.setenv("QMT_STRATEGY_LEDGER_DB", str(tmp_path / "ledger.db"))
    monkeypatch.setenv("QMT_DASHBOARD_TOKEN", "dashboard-token")
    monkeypatch.setenv("QMT_DASHBOARD_PORT", "8080")

    config = build_server_config(SimpleNamespace())

    assert config.dashboard_enabled is True
    assert config.dashboard_port == 8080
    assert config.dashboard_token == "dashboard-token"

    monkeypatch.delenv("QMT_DASHBOARD_TOKEN")
    with pytest.raises(ValueError, match="QMT_DASHBOARD_TOKEN"):
        build_server_config(SimpleNamespace())
