from bullet_trade.core import pricing
from bullet_trade.core.orders import MarketOrderStyle


def _assert_close(actual: float, expected: float, tol: float = 1e-6):
    assert abs(actual - expected) <= tol, f"{actual} != {expected}"


def test_min_price_step_etf():
    _assert_close(pricing.get_min_price_step("510050.XSHG", 3.0), 0.001)


def test_min_price_step_a_share_brackets():
    _assert_close(pricing.get_min_price_step("600000.XSHG", 12.0), 0.01)
    _assert_close(pricing.get_min_price_step("600000.XSHG", 0.8), 0.01)


def test_price_bounds_mainboard():
    buy_upper, sell_lower = pricing.compute_price_bounds("600000.XSHG", 10.0, 0.01)
    _assert_close(buy_upper, 10.2)
    _assert_close(sell_lower, 9.8)


def test_price_bounds_beijing():
    buy_upper, sell_lower = pricing.compute_price_bounds("430047.BJ", 10.0, 0.01)
    _assert_close(buy_upper, max(10 * 1.05, 10 + 0.1))
    _assert_close(sell_lower, min(10 * 0.95, 10 - 0.1))


def test_price_cage_uses_board_rules_and_does_not_treat_etf_as_stock():
    assert pricing.infer_price_cage_board("600000.XSHG") is pricing.PriceCageBoard.SH_MAIN
    assert pricing.infer_price_cage_board("300001.XSHE") is pricing.PriceCageBoard.GEM
    assert pricing.infer_price_cage_board("688001.XSHG") is pricing.PriceCageBoard.STAR
    assert pricing.compute_price_bounds("510050.XSHG", 3.0, 0.001) == (None, None)


def test_fixed_limit_waits_for_stock_cage_without_repricing():
    assert not pricing.limit_price_within_cage(
        "600000.XSHG", True, 10.02, ask_price=9.70
    )
    assert pricing.limit_price_within_cage(
        "600000.XSHG", True, 10.02, ask_price=9.90
    )
    assert not pricing.limit_price_within_cage(
        "600000.XSHG", False, 9.98, bid_price=10.30
    )
    assert pricing.limit_price_within_cage(
        "600000.XSHG", False, 9.98, bid_price=10.10
    )


def test_etf_fixed_limit_does_not_wait_for_counterparty_or_stock_cage():
    assert pricing.limit_price_within_cage("510050.XSHG", True, 1.002)
    assert pricing.limit_price_within_cage(
        "510050.XSHG", True, 1.002, ask_price=1.050
    )
    assert pricing.limit_price_within_cage(
        "159001.XSHE", False, 0.998, bid_price=0.950, instrument_type="etf"
    )


def test_cage_phase_and_low_price_stock_fallback():
    assert pricing.limit_price_within_cage(
        "600000.XSHG", True, 3.09, ask_price=3.0
    )
    assert not pricing.limit_price_within_cage(
        "688001.XSHG", True, 3.09, ask_price=3.0
    )
    assert pricing.limit_price_within_cage(
        "688001.XSHG", True, 3.09, ask_price=3.0, continuous_auction=False
    )
    assert not pricing.limit_price_within_cage("600000.XSHG", True, 3.09)


def test_compute_market_protect_price_defaults():
    price = pricing.compute_market_protect_price("600000.XSHG", 10.0, 11.0, 9.0, 0.015, True)
    _assert_close(price, 10.15)
    sell_price = pricing.compute_market_protect_price("600000.XSHG", 10.0, 11.0, 9.0, -0.015, False)
    _assert_close(sell_price, 9.85)


def test_compute_market_protect_price_clamped_by_cage():
    # 将保护价拉高至超出笼子，结果需要裁剪到 10.2
    price = pricing.compute_market_protect_price("600000.XSHG", 10.0, 10.4, 9.2, 0.5, True)
    _assert_close(price, 10.2)


def test_clamp_price_to_trade_bounds_uses_limit_and_price_cage():
    buy_price = pricing.clamp_price_to_trade_bounds(
        "600000.XSHG",
        10.5,
        10.0,
        10.15,
        9.0,
        True,
    )
    _assert_close(buy_price, 10.15)

    sell_price = pricing.clamp_price_to_trade_bounds(
        "600000.XSHG",
        9.5,
        10.0,
        11.0,
        9.85,
        False,
    )
    _assert_close(sell_price, 9.85)


def test_resolve_market_percent_priority():
    cfg_buy = 0.015
    cfg_sell = -0.015
    style = MarketOrderStyle(buy_price_percent=0.02, sell_price_percent=-0.02)
    _assert_close(pricing.resolve_market_percent(style, True, cfg_buy, cfg_sell), 0.02)
    _assert_close(pricing.resolve_market_percent(style, False, cfg_buy, cfg_sell), -0.02)
    # 无策略覆盖时应回落到配置
    default_style = MarketOrderStyle()
    _assert_close(pricing.resolve_market_percent(default_style, True, cfg_buy, cfg_sell), cfg_buy)
    _assert_close(pricing.resolve_market_percent(default_style, False, cfg_buy, cfg_sell), cfg_sell)
