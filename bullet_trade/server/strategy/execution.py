"""Typed execution policy shared by strategy planning and the JSON boundary."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from datetime import datetime
from typing import Dict, Mapping, Optional, Union


EXECUTION_WIRE_SCHEMA_VERSION = 1
_MAX_PRICE_BAND_PPM = 100_000


class ExecutionType(str, Enum):
    LIMIT = "LIMIT"
    CONDITIONAL_LIMIT = "CONDITIONAL_LIMIT"
    MARKET = "MARKET"
    MARKETABLE_LIMIT = "MARKETABLE_LIMIT"


class FollowUpPolicy(str, Enum):
    NONE = "NONE"
    UNTIL_FILLED_TODAY = "UNTIL_FILLED_TODAY"


class RepricingPolicy(str, Enum):
    KEEP_ORIGINAL = "KEEP_ORIGINAL"
    RECOMPUTE = "RECOMPUTE"


class ConditionalLimitPriceMode(str, Enum):
    BOUNDARY = "BOUNDARY"
    COUNTERPARTY = "COUNTERPARTY"


def _require_band(value: int, field_name: str) -> None:
    if type(value) is not int or not 0 <= value <= _MAX_PRICE_BAND_PPM:
        raise ValueError(
            "{} must be an integer between 0 and {}".format(
                field_name, _MAX_PRICE_BAND_PPM
            )
        )


@dataclass(frozen=True)
class LimitExecution:
    """Limit price derived from each security's reference price."""

    price_band_ppm: int = 0
    execution_type: ExecutionType = field(
        default=ExecutionType.LIMIT, init=False
    )

    def __post_init__(self) -> None:
        _require_band(self.price_band_ppm, "price_band_ppm")


@dataclass(frozen=True)
class ConditionalLimitExecution:
    """Wait for the quote to enter a fixed reference-price boundary."""

    price_band_ppm: int = 2_000
    price_mode: ConditionalLimitPriceMode = ConditionalLimitPriceMode.BOUNDARY
    execution_type: ExecutionType = field(
        default=ExecutionType.CONDITIONAL_LIMIT, init=False
    )

    def __post_init__(self) -> None:
        _require_band(self.price_band_ppm, "price_band_ppm")
        if type(self.price_mode) is not ConditionalLimitPriceMode:
            raise TypeError("price_mode must be ConditionalLimitPriceMode")


@dataclass(frozen=True)
class MarketExecution:
    """QMT market order with an explicit protection band for cash reservation."""

    protect_price_band_ppm: int = 15_000
    execution_type: ExecutionType = field(
        default=ExecutionType.MARKET, init=False
    )

    def __post_init__(self) -> None:
        _require_band(self.protect_price_band_ppm, "protect_price_band_ppm")


@dataclass(frozen=True)
class MarketableLimitExecution:
    """Aggressive limit order, kept distinct from a broker market order."""

    price_band_ppm: int = 15_000
    execution_type: ExecutionType = field(
        default=ExecutionType.MARKETABLE_LIMIT, init=False
    )

    def __post_init__(self) -> None:
        _require_band(self.price_band_ppm, "price_band_ppm")


ExecutionStyle = Union[
    LimitExecution,
    ConditionalLimitExecution,
    MarketExecution,
    MarketableLimitExecution,
]


@dataclass(frozen=True)
class ExecutionRequest:
    style: ExecutionStyle = field(
        default_factory=lambda: LimitExecution(price_band_ppm=2_000)
    )
    follow_up: FollowUpPolicy = FollowUpPolicy.UNTIL_FILLED_TODAY
    repricing: RepricingPolicy = RepricingPolicy.KEEP_ORIGINAL

    def __post_init__(self) -> None:
        if not isinstance(
            self.style,
            (
                LimitExecution,
                ConditionalLimitExecution,
                MarketExecution,
                MarketableLimitExecution,
            ),
        ):
            raise TypeError("style must be a supported execution style")
        if type(self.follow_up) is not FollowUpPolicy:
            raise TypeError("follow_up must be FollowUpPolicy")
        if type(self.repricing) is not RepricingPolicy:
            raise TypeError("repricing must be RepricingPolicy")


@dataclass(frozen=True)
class MarketQuote:
    """Small quote view used by conditional execution decisions."""

    security: str
    as_of: datetime
    bid_price_units: Optional[int] = None
    ask_price_units: Optional[int] = None
    last_price_units: Optional[int] = None

    def __post_init__(self) -> None:
        if type(self.security) is not str or not self.security:
            raise ValueError("security cannot be empty")
        if not isinstance(self.as_of, datetime):
            raise TypeError("as_of must be datetime")
        for name in ("bid_price_units", "ask_price_units", "last_price_units"):
            value = getattr(self, name)
            if value is not None and (type(value) is not int or value <= 0):
                raise ValueError("{} must be a positive integer".format(name))


def execution_request_to_wire(request: ExecutionRequest) -> Dict[str, object]:
    """Encode the typed request at the TCP/SQLite JSON boundary."""

    if type(request) is not ExecutionRequest:
        raise TypeError("request must be ExecutionRequest")
    style = request.style
    style_wire: Dict[str, object] = {"type": style.execution_type.value}
    if isinstance(style, (LimitExecution, MarketableLimitExecution)):
        style_wire["price_band_ppm"] = style.price_band_ppm
    elif isinstance(style, ConditionalLimitExecution):
        style_wire["price_band_ppm"] = style.price_band_ppm
        style_wire["price_mode"] = style.price_mode.value
    elif isinstance(style, MarketExecution):
        style_wire["protect_price_band_ppm"] = style.protect_price_band_ppm
    else:  # pragma: no cover - guarded by ExecutionRequest validation
        raise TypeError("unsupported execution style")
    return {
        "schema_version": EXECUTION_WIRE_SCHEMA_VERSION,
        "style": style_wire,
        "follow_up": request.follow_up.value,
        "repricing": request.repricing.value,
    }


def _require_exact_fields(
    value: Mapping[str, object], allowed: frozenset, label: str
) -> None:
    unknown = set(value) - allowed
    if unknown:
        raise ValueError("{} has unknown fields: {}".format(label, sorted(unknown)))


def _enum_value(enum_type, raw: object, field_name: str):
    if type(raw) is not str:
        raise ValueError("{} must be a string".format(field_name))
    try:
        return enum_type(raw)
    except ValueError as exc:
        raise ValueError("unknown {}: {}".format(field_name, raw)) from exc


def _wire_band(style: Mapping[str, object], field_name: str) -> int:
    value = style.get(field_name)
    _require_band(value, field_name)  # type: ignore[arg-type]
    return value  # type: ignore[return-value]


def execution_request_from_wire(value: Mapping[str, object]) -> ExecutionRequest:
    """Decode and strictly validate a JSON-compatible execution request."""

    if not isinstance(value, Mapping):
        raise ValueError("execution request must be an object")
    _require_exact_fields(
        value,
        frozenset({"schema_version", "style", "follow_up", "repricing"}),
        "execution request",
    )
    if value.get("schema_version") != EXECUTION_WIRE_SCHEMA_VERSION:
        raise ValueError("unsupported execution request schema_version")
    raw_style = value.get("style")
    if not isinstance(raw_style, Mapping):
        raise ValueError("execution style must be an object")
    execution_type = _enum_value(
        ExecutionType, raw_style.get("type"), "execution style type"
    )
    if execution_type is ExecutionType.LIMIT:
        _require_exact_fields(
            raw_style, frozenset({"type", "price_band_ppm"}), "limit style"
        )
        style: ExecutionStyle = LimitExecution(
            _wire_band(raw_style, "price_band_ppm")
        )
    elif execution_type is ExecutionType.CONDITIONAL_LIMIT:
        _require_exact_fields(
            raw_style,
            frozenset({"type", "price_band_ppm", "price_mode"}),
            "conditional limit style",
        )
        style = ConditionalLimitExecution(
            _wire_band(raw_style, "price_band_ppm"),
            _enum_value(
                ConditionalLimitPriceMode,
                raw_style.get("price_mode"),
                "conditional price_mode",
            ),
        )
    elif execution_type is ExecutionType.MARKET:
        _require_exact_fields(
            raw_style,
            frozenset({"type", "protect_price_band_ppm"}),
            "market style",
        )
        style = MarketExecution(
            _wire_band(raw_style, "protect_price_band_ppm")
        )
    else:
        _require_exact_fields(
            raw_style,
            frozenset({"type", "price_band_ppm"}),
            "marketable limit style",
        )
        style = MarketableLimitExecution(
            _wire_band(raw_style, "price_band_ppm")
        )
    return ExecutionRequest(
        style=style,
        follow_up=_enum_value(
            FollowUpPolicy, value.get("follow_up"), "follow_up"
        ),
        repricing=_enum_value(
            RepricingPolicy, value.get("repricing"), "repricing"
        ),
    )
