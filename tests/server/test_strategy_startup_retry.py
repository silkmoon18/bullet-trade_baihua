import asyncio

import pytest

from bullet_trade.server.adapters.base import AccountRouter, AdapterBundle
from bullet_trade.server.app import ServerApplication
from bullet_trade.server.config import AccountConfig, ServerConfig


class _BrokerAdapter:
    async def start(self):
        return None

    async def stop(self):
        return None


class _RecoveringStrategyAPI:
    def __init__(self):
        self.calls = 0
        self.recovered = asyncio.Event()

    async def startup_check(self, _account_context, _account_key):
        self.calls += 1
        ready = self.calls >= 2
        if ready:
            self.recovered.set()
        return ready


@pytest.mark.asyncio
async def test_strategy_startup_retries_after_qmt_becomes_ready(monkeypatch):
    monkeypatch.setattr(
        "bullet_trade.server.app._STRATEGY_STARTUP_RETRY_SECONDS", 0.001
    )
    config = ServerConfig(
        server_type="qmt",
        listen="127.0.0.1",
        port=0,
        token="test",
        enable_data=False,
        enable_broker=True,
        accounts=[AccountConfig(key="default", account_id="demo")],
    )
    router = AccountRouter(config.accounts)
    app = ServerApplication(
        config,
        router,
        AdapterBundle(data_adapter=None, broker_adapter=_BrokerAdapter()),
    )
    strategy_api = _RecoveringStrategyAPI()
    app.strategy_api = strategy_api  # type: ignore[assignment]
    app._ensure_runtime_events()

    await app._start_components()
    await asyncio.wait_for(strategy_api.recovered.wait(), timeout=1)
    assert strategy_api.calls == 2

    await app.shutdown()
    assert app._strategy_startup_task is None
