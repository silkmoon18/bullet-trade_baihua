"""Strict static contract probe; this module is never uploaded or executed."""

from typing import Mapping, Optional, Tuple

import bullet_trade_jq_remote_helper as remote
from joinquant_typing import Snapshot
from jqdata import (
    Context,
    LimitOrderStyle,
    Order,
    Portfolio,
    Position,
    get_current_data,
    order_target_value,
)


def inspect_strategy_contract(context: Context) -> None:
    portfolio: Portfolio = context.portfolio
    positions: Mapping[str, Position] = portfolio.positions
    position: Optional[Position] = positions.get("510300.XSHG")
    if position is not None:
        market_value: float = position.value
        quantity: int = position.total_amount
        _ = (market_value, quantity)

    snapshots: Mapping[str, Snapshot] = get_current_data()
    snapshot: Optional[Snapshot] = snapshots.get("510300.XSHG")
    if snapshot is not None:
        last_price: float = snapshot.last_price
        _ = last_price

    order: Optional[Order] = order_target_value(
        "510300.XSHG",
        3000.0,
        style=LimitOrderStyle(4.0),
    )
    _ = order

    runtime_state = remote.install_strategy_runtime(
        {},
        context=context,
        profile="good_etf-prod",
        mode="SIGNAL_ONLY",
        strategy_id="good_etf",
    )
    runtime_api: int = runtime_state["api_version"]
    profile_schema: int = runtime_state["profile_schema_version"]
    orders_enabled: bool = runtime_state["orders_enabled"]
    production_ready: bool = runtime_state["production_ready"]
    blocked_mutations: Optional[Tuple[str, ...]] = runtime_state.get(
        "blocked_mutations"
    )
    _ = (
        runtime_api,
        profile_schema,
        orders_enabled,
        production_ready,
        blocked_mutations,
    )
