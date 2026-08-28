from bullet_trade.data.providers.miniqmt import MiniQMTProvider


class _FullTickXtData:
    def __init__(self):
        self.full_tick_calls = []
        self.last_quote_calls = []

    def get_full_tick(self, codes):
        self.full_tick_calls.append(tuple(codes))
        return {
            "510050.SH": {
                "lastPrice": 2.501,
                "time": 1783043331000,
                "bidPrice": [2.5],
                "askPrice": [2.502],
            }
        }

    def get_last_quote(self, code):
        self.last_quote_calls.append(code)
        raise AssertionError("fresh full tick must be preferred")


def test_current_tick_prefers_full_tick_and_preserves_market_time(monkeypatch):
    xtdata = _FullTickXtData()
    monkeypatch.setattr(
        MiniQMTProvider,
        "_ensure_xtdata",
        staticmethod(lambda: xtdata),
    )
    provider = MiniQMTProvider({"cache_dir": None, "mode": "live"})

    tick = provider.get_current_tick("510050.XSHG")

    assert tick == {
        "sid": "510050.XSHG",
        "last_price": 2.501,
        "dt": "2026-07-03T09:48:51",
        "bidPrice": [2.5],
        "askPrice": [2.502],
    }
    assert xtdata.full_tick_calls == [("510050.SH",)]
    assert xtdata.last_quote_calls == []


def test_current_tick_never_invents_timestamp(monkeypatch):
    class MissingTimeXtData:
        def get_full_tick(self, codes):
            return {codes[0]: {"lastPrice": 2.501}}

        def get_last_quote(self, code):
            return {"lastPrice": 2.501}

    monkeypatch.setattr(
        MiniQMTProvider,
        "_ensure_xtdata",
        staticmethod(lambda: MissingTimeXtData()),
    )
    provider = MiniQMTProvider({"cache_dir": None, "mode": "live"})

    def no_kline(*args, **kwargs):
        raise RuntimeError("no kline")

    monkeypatch.setattr(
        provider,
        "get_price",
        no_kline,
    )

    assert provider.get_current_tick("510050.XSHG") is None
