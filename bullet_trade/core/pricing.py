"""
实盘价格辅助工具：最小价差、价格笼子、保护价计算等。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
from enum import Enum
from typing import Optional, Tuple, Any, Dict, Mapping


class PriceCageBoard(str, Enum):
    SH_MAIN = "SH_MAIN"
    SZ_MAIN = "SZ_MAIN"
    GEM = "GEM"
    STAR = "STAR"
    BSE = "BSE"


@dataclass(frozen=True)
class PriceCageRule:
    """Continuous-auction limit-order rule for one stock board."""

    buy_ratio: float
    sell_ratio: float
    fallback_ticks: int


# Daily limits come from QMT's UpStopPrice/DownStopPrice, not board defaults.
CAGE_CONFIG: Mapping[PriceCageBoard, PriceCageRule] = {
    PriceCageBoard.SH_MAIN: PriceCageRule(1.02, 0.98, 10),
    PriceCageBoard.SZ_MAIN: PriceCageRule(1.02, 0.98, 10),
    PriceCageBoard.GEM: PriceCageRule(1.02, 0.98, 10),
    PriceCageBoard.STAR: PriceCageRule(1.02, 0.98, 0),
    PriceCageBoard.BSE: PriceCageRule(1.05, 0.95, 10),
}


def _split_security(security: str) -> Tuple[str, str]:
    parts = security.split(".")
    if len(parts) == 2:
        return parts[0], parts[1].upper()
    return security, ""


_LOT_RULES_LOADED = False
_LOT_RULES: Dict[str, Any] = {}


def _config_base_dir() -> str:
    return os.path.dirname(os.path.dirname(__file__))


def _lot_rules_path() -> str:
    return os.path.join(_config_base_dir(), "config", "security_overrides.json")


def _load_lot_rules_if_needed() -> None:
    global _LOT_RULES_LOADED, _LOT_RULES
    if _LOT_RULES_LOADED:
        return
    path = _lot_rules_path()
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    _LOT_RULES = data.get("lot_rules") or {}
    except Exception:
        _LOT_RULES = {}
    finally:
        _LOT_RULES_LOADED = True


def _normalize_market(market: str) -> str:
    upper = (market or "").upper()
    if upper in ("XSHG", "SH"):
        return "SH"
    if upper in ("XSHE", "SZ"):
        return "SZ"
    if upper in ("BJ", "BSE", "XBSE"):
        return "BJ"
    return upper


def _candidate_codes(code: str, market: str, raw: str) -> Tuple[str, ...]:
    candidates = []
    if raw:
        candidates.append(raw)
    if not code or not market:
        return tuple(candidates)
    if market == "SH":
        candidates.extend([f"{code}.XSHG", f"{code}.SH"])
    elif market == "SZ":
        candidates.extend([f"{code}.XSHE", f"{code}.SZ"])
    elif market == "BJ":
        candidates.extend([f"{code}.BJ", f"{code}.BSE"])
    return tuple(dict.fromkeys(candidates))


def _pick_lot_rule(security: str) -> Dict[str, Any]:
    _load_lot_rules_if_needed()
    rules = _LOT_RULES if isinstance(_LOT_RULES, dict) else {}
    if not rules:
        return {"min_lot": 100, "step": 100}
    by_code = rules.get("by_code") or {}
    by_prefix = rules.get("by_prefix") or {}
    by_market = rules.get("by_market") or {}
    default = rules.get("default") or {"min_lot": 100, "step": 100}

    code, market_raw = _split_security(security)
    market = _normalize_market(market_raw)
    if isinstance(by_code, dict):
        for candidate in _candidate_codes(code, market, security):
            item = by_code.get(candidate)
            if isinstance(item, dict):
                return item
    if isinstance(by_prefix, dict):
        for prefix, item in by_prefix.items():
            if code.startswith(prefix) and isinstance(item, dict):
                return item
    if isinstance(by_market, dict):
        item = by_market.get(market)
        if isinstance(item, dict):
            return item
    return default if isinstance(default, dict) else {"min_lot": 100, "step": 100}


def infer_lot_rule(security: str) -> Tuple[int, int]:
    rule = _pick_lot_rule(security)
    min_lot = int(rule.get("min_lot") or 0)
    step = int(rule.get("step") or 0)
    if min_lot <= 0:
        min_lot = 1
    if step <= 0:
        step = min_lot
    return min_lot, step


def adjust_order_amount(
    security: str,
    amount: int,
    is_buy: bool,
    closeable: Optional[int] = None,
) -> int:
    raw = int(amount or 0)
    if raw <= 0:
        return 0
    min_lot, step = infer_lot_rule(security)
    if is_buy:
        adjusted = (raw // step) * step
        return adjusted if adjusted >= min_lot else 0
    if closeable is not None:
        closeable = int(closeable)
        if closeable < min_lot:
            return min(raw, closeable)
    adjusted = (raw // step) * step
    if adjusted < min_lot:
        return 0
    if closeable is not None:
        return min(adjusted, closeable)
    return adjusted


def is_etf(security: str) -> bool:
    code, market = _split_security(security)
    return (market in ("XSHG", "SH") and code.startswith("5")) or (
        market in ("XSHE", "SZ") and code.startswith("1")
    )


def infer_lot_size(security: str) -> int:
    min_lot, _ = infer_lot_rule(security)
    return min_lot


def get_min_price_step(security: str, price: float) -> float:
    """
    根据标的和当前价格推断最小价差（tick size）。
    规则参考交易所公开信息，覆盖主板/创业板/ETF/北交所等常见场景。
    """
    code, market = _split_security(security)
    price = float(price) if price and price > 0 else 1.0

    if is_etf(security):
        return 0.001

    # B 股
    if (market in ("XSHG", "SH") and code.startswith("9")) or (
        market in ("XSHE", "SZ") and code.startswith("2")
    ):
        return 0.001

    # 沪深北 A 股的常规最小价格变动单位为 0.01 元；不要因为股价低于
    # 1 元就把股票误判成基金的 0.001 元报价单位。
    return 0.01


def infer_price_cage_board(
    security: str, instrument_type: Optional[str] = None
) -> Optional[PriceCageBoard]:
    """Resolve the stock board, preferring QMT's instrument type.

    Funds, bonds and indexes do not inherit a stock board's dynamic 2%/5%
    price cage merely because their code is listed on the same exchange.
    Prefixes are only the board fallback after the product type is known to be
    a stock (or when QMT omitted that metadata).
    """

    kind = str(instrument_type or "").strip().lower()
    if any(token in kind for token in ("fund", "etf", "lof", "bond", "index")):
        return None
    if is_etf(security):
        return None
    code, market = _split_security(security)
    normalized_market = _normalize_market(market)
    if normalized_market == "BJ":
        return PriceCageBoard.BSE
    if normalized_market == "SH":
        if code.startswith("688"):
            return PriceCageBoard.STAR
        return PriceCageBoard.SH_MAIN
    if normalized_market == "SZ":
        if code.startswith("30"):
            return PriceCageBoard.GEM
        return PriceCageBoard.SZ_MAIN
    return None


def _infer_price_rule(security: str) -> str:
    """Backward-compatible internal label for older callers/tests."""

    board = infer_price_cage_board(security)
    if board is PriceCageBoard.BSE:
        return "beijing"
    if board is PriceCageBoard.STAR:
        return "sci"
    if board in (
        PriceCageBoard.SH_MAIN,
        PriceCageBoard.SZ_MAIN,
        PriceCageBoard.GEM,
    ):
        return "main"
    return "other"


def compute_price_bounds(
    security: str,
    base_price: float,
    tick_size: float,
    instrument_type: Optional[str] = None,
) -> Tuple[Optional[float], Optional[float]]:
    """
    返回 (买入上限, 卖出下限)，用于价格笼子裁剪。
    base_price 来自交易所“基准价”的近似值（通常取 last_price）。
    """
    board = infer_price_cage_board(security, instrument_type)
    if board is None:
        return None, None
    rule = CAGE_CONFIG[board]
    tick = tick_size if tick_size > 0 else 0.01
    buy_bound = base_price * rule.buy_ratio
    sell_bound = base_price * rule.sell_ratio
    if rule.fallback_ticks:
        extra = rule.fallback_ticks * tick
        buy_bound = max(buy_bound, base_price + extra)
        sell_bound = min(sell_bound, base_price - extra)
    return buy_bound, sell_bound


def limit_price_within_cage(
    security: str,
    is_buy: bool,
    limit_price: float,
    *,
    bid_price: Optional[float] = None,
    ask_price: Optional[float] = None,
    last_price: Optional[float] = None,
    preclose: Optional[float] = None,
    instrument_type: Optional[str] = None,
    continuous_auction: bool = True,
) -> bool:
    """Check a caller's fixed limit; never replace it with the cage boundary.

    ETFs have no stock dynamic cage. For stocks, quotes supply only the
    exchange base used to decide when the original limit may be submitted.
    Daily limits and tick precision remain separate adapter validations.
    """

    if _to_positive_float(limit_price) is None:
        return False
    if (
        infer_price_cage_board(security, instrument_type) is None
        or not continuous_auction
    ):
        return True
    raw_values = (
        (ask_price, bid_price, last_price, preclose)
        if is_buy
        else (bid_price, ask_price, last_price, preclose)
    )
    base = next(
        (value for value in (_to_positive_float(item) for item in raw_values) if value),
        None,
    )
    if base is None:
        return False
    tick = get_min_price_step(security, base)
    upper, lower = compute_price_bounds(security, base, tick, instrument_type)
    boundary = _round_inside_bound(
        upper if is_buy else lower, tick, upper_bound=is_buy
    )
    return (
        limit_price <= boundary + 1e-9
        if is_buy
        else limit_price >= boundary - 1e-9
    )


def _clamp(value: float, lower: Optional[float], upper: Optional[float]) -> float:
    if lower is not None:
        value = max(value, lower)
    if upper is not None:
        value = min(value, upper)
    return value


def _to_positive_float(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _merge_lower(*values: Optional[float]) -> Optional[float]:
    present = [value for value in values if value is not None]
    return max(present) if present else None


def _merge_upper(*values: Optional[float]) -> Optional[float]:
    present = [value for value in values if value is not None]
    return min(present) if present else None


def _round_to_tick(price: float, tick_size: float) -> float:
    if tick_size <= 0:
        return price
    rounded = round(price / tick_size) * tick_size
    # 避免浮点尾差
    return float(f"{rounded:.6f}")


def _round_inside_bound(
    price: float, tick_size: float, *, upper_bound: bool
) -> float:
    """Round without crossing an exchange upper/lower price boundary."""

    if tick_size <= 0:
        return price
    raw = Decimal(str(price)) / Decimal(str(tick_size))
    mode = ROUND_FLOOR if upper_bound else ROUND_CEILING
    ticks = raw.to_integral_value(rounding=mode)
    return float(ticks * Decimal(str(tick_size)))


def compute_trade_price_bounds(
    security: str,
    last_price: float,
    high_limit: Optional[float],
    low_limit: Optional[float],
    is_buy: bool,
) -> Tuple[Optional[float], Optional[float]]:
    """
    计算委托价/保护价/模拟成交价共同适用的边界。

    Args:
        security: 标的代码。
        last_price: 价格笼子的参考价，通常为当前最新价或回测当前 bar 价格。
        high_limit: 当日涨停价；缺失或非正数时不参与上界约束。
        low_limit: 当日跌停价；缺失或非正数时不参与下界约束。
        is_buy: True 表示买入，False 表示卖出。

    Returns:
        (lower, upper)：买入时 upper 为 min(涨停价, 买入笼子上沿)，
        卖出时 lower 为 max(跌停价, 卖出笼子下沿)。缺失的边界返回 None。
    """
    current_high = _to_positive_float(high_limit)
    current_low = _to_positive_float(low_limit)

    base_price = _to_positive_float(last_price)
    cage_buy: Optional[float] = None
    cage_sell: Optional[float] = None
    if base_price is not None:
        tick = get_min_price_step(security, base_price)
        cage_buy, cage_sell = compute_price_bounds(security, base_price, tick)

    if is_buy:
        return current_low, _merge_upper(current_high, cage_buy)
    return _merge_lower(current_low, cage_sell), current_high


def clamp_price_to_trade_bounds(
    security: str,
    price: float,
    last_price: float,
    high_limit: Optional[float],
    low_limit: Optional[float],
    is_buy: bool,
) -> float:
    """
    将委托价、保护价或模拟成交价裁剪到涨跌停与价格笼子的共同边界内。

    Args:
        security: 标的代码。
        price: 待裁剪价格。
        last_price: 价格笼子的参考价，通常为当前最新价或回测当前 bar 价格。
        high_limit: 当日涨停价。
        low_limit: 当日跌停价。
        is_buy: True 表示买入，False 表示卖出。

    Returns:
        已按 tick、涨跌停和价格笼子裁剪后的价格。
    """
    base_price = _to_positive_float(last_price) or _to_positive_float(price) or 1.0
    tick = get_min_price_step(security, base_price)
    rounded = _round_to_tick(float(price), tick)
    lower, upper = compute_trade_price_bounds(security, base_price, high_limit, low_limit, is_buy)
    clamped = _clamp(rounded, lower, upper)
    return _round_to_tick(clamped, tick)


def compute_market_protect_price(
    security: str,
    last_price: float,
    high_limit: Optional[float],
    low_limit: Optional[float],
    percent: float,
    is_buy: bool,
) -> float:
    """
    计算市价保护价：last_price*(1+percent) 经价格笼子/涨跌停/最小价差裁剪。
    """
    base_price = float(last_price)
    if base_price <= 0:
        # 尝试使用涨跌停作为基准
        fallback = high_limit if high_limit and high_limit > 0 else low_limit
        if not fallback:
            raise ValueError(f"{security} 缺少可用价格，无法计算保护价")
        base_price = float(fallback)

    protect_price = base_price * (1.0 + percent)
    rounded = clamp_price_to_trade_bounds(
        security,
        protect_price,
        base_price,
        high_limit,
        low_limit,
        is_buy,
    )

    if rounded <= 0:
        raise ValueError(f"{security} 保护价无效: {rounded}")
    return rounded


def resolve_market_percent(
    style: Any, is_buy: bool, default_buy: float, default_sell: float
) -> float:
    """
    计算市价单使用的比例：策略 style > 配置 > 默认。
    style 只要带有 buy_price_percent/sell_price_percent 属性即可（鸭子类型）。
    """
    if style and hasattr(style, "buy_price_percent") and hasattr(style, "sell_price_percent"):
        percent = style.buy_price_percent if is_buy else style.sell_price_percent
        if percent is not None:
            return float(percent)
    return float(default_buy if is_buy else default_sell)
