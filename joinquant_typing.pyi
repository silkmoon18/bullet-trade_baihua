"""Typing-only structural models for JoinQuant strategy development.

This module has no runtime implementation on the JoinQuant platform. Import it
only below ``if TYPE_CHECKING`` so the strategy file remains directly uploadable.
"""

from datetime import date, datetime
from typing import Any, Mapping, MutableMapping, Optional, Protocol, Sequence


class RunParams(Protocol):
    type: str


class Position(Protocol):
    security: str
    total_amount: int
    closeable_amount: int
    avg_cost: float
    price: float
    value: float


class Portfolio(Protocol):
    available_cash: float
    transferable_cash: float
    locked_cash: float
    starting_cash: float
    positions: MutableMapping[str, Position]
    positions_value: float
    total_value: float


class Snapshot(Protocol):
    last_price: float
    high_limit: float
    low_limit: float
    paused: bool
    is_st: bool
    name: str


class Context(Protocol):
    portfolio: Portfolio
    subportfolios: Sequence[Portfolio]
    current_dt: datetime
    previous_dt: Optional[datetime]
    previous_date: Optional[date]
    run_params: RunParams


PositionMap = Mapping[str, Position]
SnapshotMap = Mapping[str, Snapshot]
JsonObject = Mapping[str, Any]
