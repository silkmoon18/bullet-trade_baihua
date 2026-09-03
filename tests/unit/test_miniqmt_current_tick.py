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


def test_tplus_uses_qmt_t0_fund_sector_and_caches_for_the_day(monkeypatch):
    class SectorXtData:
        def __init__(self):
            self.calls = 0

        def get_stock_list_in_sector(self, sector):
            assert sector == "T+0基金"
            self.calls += 1
            return ["518880.SH", "159985.SZ"]

    xtdata = SectorXtData()
    monkeypatch.setattr(
        MiniQMTProvider,
        "_ensure_xtdata",
        staticmethod(lambda: xtdata),
    )
    provider = MiniQMTProvider({"cache_dir": None, "mode": "live"})

    assert provider.get_tplus("518880.XSHG") == 0
    assert provider.get_tplus("510300.XSHG") == 1
    assert provider.get_tplus("159985.XSHE") == 0
    assert xtdata.calls == 1


def test_etf_metadata_retains_qmt_limits_even_on_listing_day(monkeypatch):
    from datetime import date

    class ListingXtData(_FullTickXtData):
        def get_instrument_detail(self, code):
            return {
                "OpenDate": date.today().strftime("%Y%m%d"),
                "UpStopPrice": 2.750,
                "DownStopPrice": 2.250,
            }

        def get_instrument_type(self, code):
            return {"fund": True, "etf": True}

    xtdata = ListingXtData()
    monkeypatch.setattr(
        MiniQMTProvider, "_ensure_xtdata", staticmethod(lambda: xtdata)
    )
    provider = MiniQMTProvider({"cache_dir": None, "mode": "live"})
    tick = provider.get_current_tick("510050.XSHG")

    assert tick["high_limit"] == 2.750
    assert tick["low_limit"] == 2.250


def test_tplus_missing_qmt_sector_defaults_to_t1(monkeypatch):
    class MissingSectorXtData:
        def get_stock_list_in_sector(self, sector):
            raise RuntimeError("sector unavailable")

    monkeypatch.setattr(
        MiniQMTProvider,
        "_ensure_xtdata",
        staticmethod(lambda: MissingSectorXtData()),
    )
    provider = MiniQMTProvider({"cache_dir": None, "mode": "live"})
    assert provider.get_tplus("518880.XSHG") == 1
