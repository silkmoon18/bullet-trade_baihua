"""Broker capability contract required by the StrategyLedger live path."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Dict, Iterable, Mapping, Optional, Tuple, Union, cast

from .domain import SHANGHAI_TZ, OrderSide, as_shanghai_time, money_to_units, price_to_units


class BrokerContractError(RuntimeError):
    """Raised when broker evidence cannot safely feed StrategyLedger."""


class CapabilityState(str, Enum):
    SUPPORTED = "SUPPORTED"
    PROBE_REQUIRED = "PROBE_REQUIRED"
    UNSUPPORTED = "UNSUPPORTED"


@dataclass(frozen=True)
class BrokerCapabilityProfile:
    adapter_kind: str
    client_tag_roundtrip: CapabilityState
    stable_order_id: CapabilityState
    stable_trade_id: CapabilityState
    trade_order_link: CapabilityState
    direct_trade_side: CapabilityState
    order_side_for_trade: CapabilityState
    fee_fields: CapabilityState
    order_status: CapabilityState
    current_orders_query: CapabilityState
    current_trades_query: CapabilityState
    working_orders_query: CapabilityState
    order_lookback_days: Optional[int] = None
    trade_lookback_days: Optional[int] = None

    def __post_init__(self) -> None:
        if not self.adapter_kind:
            raise ValueError("adapter_kind cannot be empty")
        for name in ("order_lookback_days", "trade_lookback_days"):
            value = getattr(self, name)
            if value is not None and (type(value) is not int or value < 0):
                raise ValueError("{} must be a non-negative integer or None".format(name))


XTQUANT_DIRECT_CAPABILITIES = BrokerCapabilityProfile(
    adapter_kind="XTQUANT_DIRECT",
    client_tag_roundtrip=CapabilityState.PROBE_REQUIRED,
    stable_order_id=CapabilityState.PROBE_REQUIRED,
    stable_trade_id=CapabilityState.PROBE_REQUIRED,
    trade_order_link=CapabilityState.PROBE_REQUIRED,
    direct_trade_side=CapabilityState.UNSUPPORTED,
    order_side_for_trade=CapabilityState.PROBE_REQUIRED,
    fee_fields=CapabilityState.PROBE_REQUIRED,
    order_status=CapabilityState.PROBE_REQUIRED,
    current_orders_query=CapabilityState.PROBE_REQUIRED,
    current_trades_query=CapabilityState.PROBE_REQUIRED,
    working_orders_query=CapabilityState.PROBE_REQUIRED,
)


BIG_QMT_CAPABILITIES = BrokerCapabilityProfile(
    adapter_kind="BIG_QMT",
    client_tag_roundtrip=CapabilityState.PROBE_REQUIRED,
    stable_order_id=CapabilityState.PROBE_REQUIRED,
    stable_trade_id=CapabilityState.PROBE_REQUIRED,
    trade_order_link=CapabilityState.PROBE_REQUIRED,
    direct_trade_side=CapabilityState.UNSUPPORTED,
    order_side_for_trade=CapabilityState.PROBE_REQUIRED,
    fee_fields=CapabilityState.PROBE_REQUIRED,
    order_status=CapabilityState.PROBE_REQUIRED,
    current_orders_query=CapabilityState.PROBE_REQUIRED,
    current_trades_query=CapabilityState.PROBE_REQUIRED,
    working_orders_query=CapabilityState.PROBE_REQUIRED,
)


def load_verified_capabilities(
    evidence_path: Union[str, Path],
    expected_adapter_kind: str,
) -> BrokerCapabilityProfile:
    """Load a user-reviewed capability probe result for one QMT environment."""

    try:
        payload = json.loads(Path(evidence_path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise BrokerContractError("cannot read capability evidence") from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise BrokerContractError("capability evidence schema is invalid")
    if payload.get("adapter_kind") != expected_adapter_kind:
        raise BrokerContractError("capability evidence adapter does not match server")
    if not payload.get("verified_at") or not payload.get("probe_report"):
        raise BrokerContractError("capability evidence has no probe reference")
    capabilities = payload.get("capabilities")
    if not isinstance(capabilities, dict):
        raise BrokerContractError("capability evidence is incomplete")
    state_fields = (
        "client_tag_roundtrip",
        "stable_order_id",
        "stable_trade_id",
        "trade_order_link",
        "direct_trade_side",
        "order_side_for_trade",
        "fee_fields",
        "order_status",
        "current_orders_query",
        "current_trades_query",
        "working_orders_query",
    )
    if any(type(capabilities.get(name)) is not bool for name in state_fields):
        raise BrokerContractError("capability evidence contains non-boolean results")
    profile = BrokerCapabilityProfile(
        adapter_kind=expected_adapter_kind,
        **{
            name: (
                CapabilityState.SUPPORTED
                if capabilities[name]
                else CapabilityState.UNSUPPORTED
            )
            for name in state_fields
        },
        order_lookback_days=payload.get("order_lookback_days"),
        trade_lookback_days=payload.get("trade_lookback_days"),
    )
    require_strategy_ledger_v1(profile)
    return profile


def strategy_ledger_v1_blockers(profile: BrokerCapabilityProfile) -> Tuple[str, ...]:
    blockers = []
    required = (
        ("client_tag_roundtrip", profile.client_tag_roundtrip),
        ("stable_order_id", profile.stable_order_id),
        ("stable_trade_id", profile.stable_trade_id),
        ("trade_order_link", profile.trade_order_link),
        ("fee_fields", profile.fee_fields),
        ("order_status", profile.order_status),
        ("current_orders_query", profile.current_orders_query),
        ("current_trades_query", profile.current_trades_query),
        ("working_orders_query", profile.working_orders_query),
    )
    for name, state in required:
        if state is not CapabilityState.SUPPORTED:
            blockers.append("{}={}".format(name, state.value))
    if (
        profile.direct_trade_side is not CapabilityState.SUPPORTED
        and profile.order_side_for_trade is not CapabilityState.SUPPORTED
    ):
        blockers.append("trade_side_has_no_order_mapping")
    if profile.order_lookback_days is None or profile.order_lookback_days < 1:
        blockers.append("order_lookback_days<1")
    if profile.trade_lookback_days is None or profile.trade_lookback_days < 1:
        blockers.append("trade_lookback_days<1")
    return tuple(blockers)


def require_strategy_ledger_v1(profile: BrokerCapabilityProfile) -> None:
    blockers = strategy_ledger_v1_blockers(profile)
    if blockers:
        raise BrokerContractError(
            "{} does not satisfy strategy_ledger_v1: {}".format(
                profile.adapter_kind,
                ", ".join(blockers),
            )
        )


@dataclass(frozen=True)
class BrokerTradeEvidence:
    broker_trade_id: str
    broker_order_id: str
    security: str
    side: OrderSide
    quantity: int
    price_units: int
    commission_units: int
    tax_units: int
    traded_at: datetime

    def __post_init__(self) -> None:
        if not self.broker_trade_id or not self.broker_order_id or not self.security:
            raise ValueError("broker trade identifiers and security cannot be empty")
        if type(self.quantity) is not int or self.quantity <= 0:
            raise ValueError("broker trade quantity must be a positive integer")
        if type(self.price_units) is not int or self.price_units <= 0:
            raise ValueError("broker trade price must be a positive integer")
        if type(self.commission_units) is not int or self.commission_units < 0:
            raise ValueError("broker trade commission must be a non-negative integer")
        if type(self.tax_units) is not int or self.tax_units < 0:
            raise ValueError("broker trade tax must be a non-negative integer")
        object.__setattr__(self, "traded_at", as_shanghai_time(self.traded_at))


def _text(value: object) -> str:
    return str(value).strip() if value is not None else ""


def _side_from_row(row: Mapping[str, object]) -> Optional[OrderSide]:
    raw_side = row.get("side")
    if raw_side is None:
        raw_side = row.get("order_side")
    if raw_side is not None:
        value = _text(raw_side).upper()
        if value in ("BUY", "B"):
            return OrderSide.BUY
        if value in ("SELL", "S"):
            return OrderSide.SELL
    is_buy = row.get("is_buy")
    if type(is_buy) is bool:
        return OrderSide.BUY if is_buy else OrderSide.SELL
    return None


def _known_fee(
    row: Mapping[str, object],
    known_key: str,
    value_keys: Tuple[str, ...],
) -> object:
    known = row.get(known_key)
    if known is False:
        raise BrokerContractError("broker trade fee fields are incomplete")
    for key in value_keys:
        if key in row and row.get(key) is not None:
            if known is None or known is True:
                return row.get(key)
    raise BrokerContractError("broker trade fee fields are incomplete")


def _decimal_input(value: object) -> Union[str, int, Decimal]:
    if type(value) is float:
        return str(value)
    if type(value) in (str, int):
        return cast(Union[str, int], value)
    if isinstance(value, Decimal):
        return value
    raise BrokerContractError("broker numeric field has an unsupported type")


def _broker_money_to_units(value: object) -> int:
    try:
        return cast(int, money_to_units(_decimal_input(value)))
    except (ArithmeticError, TypeError, ValueError) as exc:
        raise BrokerContractError("broker money field is invalid") from exc


def _broker_price_to_units(value: object) -> int:
    try:
        return cast(int, price_to_units(_decimal_input(value)))
    except (ArithmeticError, TypeError, ValueError) as exc:
        raise BrokerContractError("broker price field is invalid") from exc


def _positive_quantity(value: object) -> int:
    if type(value) is int:
        quantity = cast(int, value)
    elif type(value) is str:
        try:
            quantity = int(cast(str, value))
        except ValueError as exc:
            raise BrokerContractError("broker trade quantity is invalid") from exc
    else:
        raise BrokerContractError("broker trade quantity is invalid")
    if quantity <= 0:
        raise BrokerContractError("broker trade quantity is invalid")
    return quantity


def _broker_trade_time(value: object) -> datetime:
    parsed: Optional[datetime]
    if isinstance(value, datetime):
        parsed = value
    elif type(value) in (int, float):
        timestamp = float(cast(Union[int, float], value))
        if timestamp > 10_000_000_000:
            timestamp /= 1000
        if timestamp < 946_684_800:
            raise BrokerContractError("broker trade time is invalid")
        try:
            parsed = datetime.fromtimestamp(timestamp, tz=SHANGHAI_TZ)
        except (OSError, OverflowError, ValueError) as exc:
            raise BrokerContractError("broker trade time is invalid") from exc
    elif type(value) is str:
        text = cast(str, value).strip()
        parsed = None
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            for pattern in ("%Y%m%d%H%M%S", "%Y-%m-%d %H:%M:%S"):
                try:
                    parsed = datetime.strptime(text, pattern)
                    break
                except ValueError:
                    continue
        if parsed is None:
            raise BrokerContractError("broker trade time is invalid")
    else:
        raise BrokerContractError("broker trade time is missing")
    if parsed is None:
        raise BrokerContractError("broker trade time is invalid")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        parsed = parsed.replace(tzinfo=SHANGHAI_TZ)
    return cast(datetime, as_shanghai_time(parsed))


def normalize_trade_evidence(
    trade: Mapping[str, object],
    orders_by_id: Mapping[str, Mapping[str, object]],
) -> BrokerTradeEvidence:
    trade_id = _text(trade.get("trade_id"))
    trade_id_source = _text(trade.get("trade_id_source")).lower()
    if not trade_id or trade_id_source != "broker":
        raise BrokerContractError("broker trade id is missing or synthetic")
    order_id = _text(trade.get("order_id"))
    if not order_id:
        raise BrokerContractError("broker trade has no broker order id")
    security = _text(trade.get("security"))
    if not security:
        raise BrokerContractError("broker trade has no security")
    quantity = _positive_quantity(trade.get("amount") or trade.get("quantity"))
    price = trade.get("price")
    if price is None:
        price = trade.get("traded_price")
    price_units = _broker_price_to_units(price)
    if price_units <= 0:
        raise BrokerContractError("broker trade price is invalid")

    side = _side_from_row(trade)
    if side is None:
        order = orders_by_id.get(order_id)
        if order is None:
            raise BrokerContractError("broker trade side cannot be mapped to its order")
        side = _side_from_row(order)
    if side is None:
        raise BrokerContractError("broker trade side cannot be mapped to its order")

    commission = _known_fee(
        trade,
        "commission_known",
        ("commission_fee", "commission"),
    )
    tax = _known_fee(trade, "tax_known", ("tax", "stamp_tax"))
    commission_units = _broker_money_to_units(commission)
    tax_units = _broker_money_to_units(tax)
    if commission_units < 0 or tax_units < 0:
        raise BrokerContractError("broker trade fees cannot be negative")
    traded_at = _broker_trade_time(trade.get("time") or trade.get("trade_time"))
    return BrokerTradeEvidence(
        broker_trade_id=trade_id,
        broker_order_id=order_id,
        security=security,
        side=side,
        quantity=quantity,
        price_units=price_units,
        commission_units=commission_units,
        tax_units=tax_units,
        traded_at=traded_at,
    )


def normalize_trade_batch(
    trades: Iterable[Mapping[str, object]],
    orders: Iterable[Mapping[str, object]],
) -> Tuple[BrokerTradeEvidence, ...]:
    orders_by_id: Dict[str, Mapping[str, object]] = {}
    for order in orders:
        order_id = _text(order.get("order_id"))
        if order_id:
            orders_by_id[order_id] = order

    unique: Dict[str, BrokerTradeEvidence] = {}
    result = []
    for trade in trades:
        evidence = normalize_trade_evidence(trade, orders_by_id)
        previous = unique.get(evidence.broker_trade_id)
        if previous is not None:
            if previous != evidence:
                raise BrokerContractError("duplicate broker trade id has conflicting data")
            continue
        unique[evidence.broker_trade_id] = evidence
        result.append(evidence)
    return tuple(result)
