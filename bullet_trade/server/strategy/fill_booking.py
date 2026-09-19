"""Book real broker fills into StrategyLedger."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, replace
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple, Union, cast

from ..feishu_notifier import (
    TradeNotification,
    money_units_to_display,
    price_units_to_display,
)

from .domain import (
    MONEY_SCALE,
    PRICE_SCALE,
    SHANGHAI_TZ,
    BrokerFill,
    BrokerOrder,
    FillPriceSource,
    OrderSide,
    OrderState,
    Position,
    StrategyAccount,
)
from .repository import (
    AccountNotFoundError,
    LedgerInvariantError,
    RepositoryError,
    VersionConflictError,
)
from .schema import connect_database


DatabasePath = Union[str, Path]


class FillBookingError(RepositoryError):
    pass


class FillConflictError(FillBookingError):
    pass


@dataclass(frozen=True)
class FillBookingResult:
    account: StrategyAccount
    position: Position
    order_state: OrderState
    realized_pnl_units: int
    duplicate: bool
    corrected: bool = False


@dataclass(frozen=True)
class OrderFinalizationResult:
    account: StrategyAccount
    released_cash_units: int
    replayed: bool


def _timestamp() -> str:
    return datetime.now(SHANGHAI_TZ).isoformat()


def _round_div(numerator: int, denominator: int) -> int:
    return (numerator + denominator // 2) // denominator


def _trade_value_units(price_units: int, quantity: int) -> int:
    return _round_div(price_units * quantity * MONEY_SCALE, PRICE_SCALE)


def _known_fee_units(fill: BrokerFill) -> int:
    return (fill.commission_units or 0) + (fill.tax_units or 0)


def _intent_payload(connection: sqlite3.Connection, intent_id: object) -> dict:
    if not intent_id:
        return {}
    row = connection.execute(
        "SELECT targets_json FROM portfolio_intents WHERE intent_id = ?",
        (str(intent_id),),
    ).fetchone()
    if row is None:
        return {}
    try:
        payload = json.loads(row["targets_json"])
    except (TypeError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _estimate_unpriced_sell_proceeds(
    payload: dict,
    security: str,
    quantity: int,
    gross_units: int,
    local_limit_price_units: Optional[int] = None,
) -> Tuple[int, Optional[dict]]:
    """Use the recorded reference only for provisional cash, never as a fill price."""

    if type(quantity) is not int or quantity <= 0:
        return 0, None
    references = payload.get("reference_prices_units") if isinstance(payload, dict) else None
    reference_units = references.get(security) if isinstance(references, dict) else None
    if type(reference_units) is int and reference_units > 0:
        estimate_price_units = reference_units
        basis = "INTENT_REFERENCE_PRICE"
    elif type(local_limit_price_units) is int and local_limit_price_units > 0:
        estimate_price_units = local_limit_price_units
        basis = "ORDER_LIMIT_PRICE"
    else:
        return 0, None
    estimated_gross_units = _trade_value_units(estimate_price_units, quantity)
    shortfall_units = estimated_gross_units - gross_units
    if shortfall_units <= 0:
        return 0, None
    return shortfall_units, {
        "basis": basis,
        "reference_price_units": reference_units,
        "estimate_price_units": estimate_price_units,
        "estimated_gross_units": estimated_gross_units,
    }


def _estimate_unpriced_buy_cost(
    payload: dict,
    security: str,
    quantity: int,
    gross_units: int,
    max_gross_units: int,
    local_limit_price_units: Optional[int] = None,
    remaining_order_qty: Optional[int] = None,
) -> Tuple[int, Optional[dict]]:
    """Estimate an unpriced buy from its reference, bounded by reserved cash."""

    if type(quantity) is not int or quantity <= 0:
        return 0, None
    references = payload.get("reference_prices_units") if isinstance(payload, dict) else None
    reference_units = references.get(security) if isinstance(references, dict) else None
    if type(reference_units) is int and reference_units > 0:
        estimate_price_units = reference_units
        basis = "INTENT_REFERENCE_PRICE"
    elif type(local_limit_price_units) is int and local_limit_price_units > 0:
        estimate_price_units = local_limit_price_units
        basis = "ORDER_LIMIT_PRICE"
    else:
        estimate_price_units = None
        basis = "ORDER_RESERVED_BUDGET"
    if type(max_gross_units) is not int or max_gross_units < 0:
        max_gross_units = 0
    if estimate_price_units is not None:
        estimated_gross_units = _trade_value_units(estimate_price_units, quantity)
    elif type(remaining_order_qty) is int and remaining_order_qty >= quantity:
        estimated_gross_units = _round_div(
            max_gross_units * quantity, remaining_order_qty
        )
    else:
        estimated_gross_units = max_gross_units
    capped = estimated_gross_units > max_gross_units
    if capped:
        estimated_gross_units = max_gross_units
    extra_units = estimated_gross_units - gross_units
    return extra_units, {
        "basis": basis,
        "reference_price_units": reference_units,
        "estimate_price_units": estimate_price_units,
        "estimated_gross_units": estimated_gross_units,
        "capped_to_order_reservation": capped,
    }


def _fee_notification_detail(
    fill: BrokerFill,
    proceeds_estimate: Optional[dict] = None,
    cost_estimate: Optional[dict] = None,
    price_estimate: Optional[dict] = None,
) -> str:
    def display(label: str, value: Optional[int]) -> str:
        if value is None:
            return "{} 未知".format(label)
        return "{} ¥{}".format(label, money_units_to_display(value))

    detail = "；".join(
        (
            display("佣金", fill.commission_units),
            display("税费", fill.tax_units),
        )
    )
    if fill.commission_units is None or fill.tax_units is None:
        detail += "；成交金额仅计已知费用"
    if price_estimate is not None:
        detail += "；券商回报价为0，单价和金额按{}估算；收益为非精确收益".format(
            {
                "INTENT_REFERENCE_PRICE": "目标参考价",
                "ORDER_LIMIT_PRICE": "委托限价",
                "ORDER_RESERVED_BUDGET": "委托预留资金",
                "POSITION_COST_FALLBACK": "已有持仓成本",
            }.get(price_estimate["basis"], "已记录价格")
        )
    elif fill.price_source is FillPriceSource.ZERO_FALLBACK:
        detail += "；成交价和成交金额缺失"
        if proceeds_estimate is not None:
            basis = (
                "委托限价" if proceeds_estimate["basis"] == "ORDER_LIMIT_PRICE"
                else "目标参考价"
            )
            detail += "，按{}暂估回款 ¥{} 供调仓使用；非真实成交价，收益不可用".format(
                basis,
                money_units_to_display(proceeds_estimate["estimated_gross_units"]),
            )
        elif cost_estimate is not None:
            basis = {
                "ORDER_LIMIT_PRICE": "委托限价",
                "ORDER_RESERVED_BUDGET": "委托预留资金",
            }.get(cost_estimate["basis"], "目标参考价")
            detail += "，按{}暂估成本 ¥{}；非真实成交价，收益不可用".format(
                basis,
                money_units_to_display(cost_estimate["estimated_gross_units"]),
            )
        else:
            detail += "，无可用参考价；成交股数已入账，金额和收益未知"
    elif not fill.price_known:
        detail += "；成交价缺失，使用委托保护价保守估算"
    return detail


def _cost_price_units(total_cost_units: int, quantity: int) -> int:
    return _round_div(total_cost_units * PRICE_SCALE, quantity * MONEY_SCALE)


def _account_from_row(row: sqlite3.Row) -> StrategyAccount:
    from .domain import AccountStatus

    return StrategyAccount(
        account_id=row["strategy_account_id"],
        strategy_id=row["strategy_id"],
        physical_account_id=row["physical_account_id"],
        initial_capital_units=row["initial_capital_units"],
        cash_units=row["cash_units"],
        reserved_cash_units=row["reserved_cash_units"],
        ledger_version=row["ledger_version"],
        event_seq=row["event_seq"],
        status=AccountStatus(row["status"]),
    )


def _position_from_row(row: sqlite3.Row) -> Position:
    return Position(
        account_id=row["strategy_account_id"],
        security=row["security"],
        total_qty=row["total_qty"],
        sellable_qty=row["sellable_qty"],
        avg_cost_price_units=row["avg_cost_price_units"],
        version=row["version"],
    )


class SQLiteFillBookingService:
    def __init__(
        self,
        database_path: DatabasePath,
        notification_handler: Optional[Callable[[TradeNotification], object]] = None,
    ):
        self.database_path = Path(database_path)
        self._notification_handler = notification_handler

    def register_order(self, order: BrokerOrder) -> BrokerOrder:
        connection = connect_database(self.database_path)
        try:
            connection.execute("BEGIN IMMEDIATE")
            account = _account_from_row(
                self._select_account(connection, order.account_id)
            )
            existing = connection.execute(
                "SELECT * FROM strategy_orders WHERE order_id = ?",
                (order.order_id,),
            ).fetchone()
            if existing is not None:
                current = self._order_from_row(cast(sqlite3.Row, existing))
                if current != order:
                    raise FillConflictError("order id was reused with different fields")
                connection.commit()
                return current
            timestamp = _timestamp()
            connection.execute(
                """
                INSERT INTO strategy_orders(
                    order_id, strategy_account_id, intent_id, client_tag,
                    broker_order_id, security, side, requested_qty, filled_qty,
                    state, trading_day, limit_price_units, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    order.order_id,
                    order.account_id,
                    order.intent_id,
                    order.client_tag,
                    order.broker_order_id,
                    order.security,
                    order.side.value,
                    order.requested_qty,
                    order.filled_qty,
                    order.state.value,
                    order.trading_day.isoformat(),
                    order.limit_price_units,
                    timestamp,
                    timestamp,
                ),
            )
            connection.commit()
            if order.state is OrderState.SUBMITTED:
                self._notify(
                    TradeNotification(
                        event="ORDER_SUBMITTED",
                        strategy_id=account.strategy_id,
                        security=order.security,
                        security_name=self._security_name(
                            connection, order.intent_id, order.security
                        ),
                        side=order.side.value,
                        status=order.state.value,
                        quantity=order.requested_qty,
                        price=(
                            price_units_to_display(order.limit_price_units)
                            if order.limit_price_units is not None
                            else None
                        ),
                        amount=(
                            money_units_to_display(
                                _trade_value_units(
                                    order.limit_price_units, order.requested_qty
                                )
                            )
                            if order.limit_price_units is not None
                            else None
                        ),
                        order_id=order.order_id,
                    )
                )
            return order
        except (AccountNotFoundError, FillConflictError):
            connection.rollback()
            raise
        except sqlite3.DatabaseError as exc:
            connection.rollback()
            raise RepositoryError("failed to register strategy order") from exc
        finally:
            connection.close()

    def mark_order_submitted(
        self,
        account_id: str,
        order_id: str,
        broker_order_id: str,
    ) -> BrokerOrder:
        if not broker_order_id:
            raise ValueError("broker_order_id cannot be empty")
        connection = connect_database(self.database_path)
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = self._select_order(connection, order_id)
            if row["strategy_account_id"] != account_id:
                raise FillConflictError("order belongs to another strategy account")
            account = _account_from_row(
                self._select_account(connection, account_id)
            )
            if row["broker_order_id"] not in (None, broker_order_id):
                raise FillConflictError("order already has another broker order id")
            if row["state"] in (
                OrderState.SUBMITTED.value,
                OrderState.PARTIALLY_FILLED.value,
                OrderState.FILLED.value,
                OrderState.CANCELED.value,
                OrderState.REJECTED.value,
            ):
                current = self._order_from_row(row)
                if current.broker_order_id != broker_order_id:
                    raise FillConflictError("submitted order broker id changed")
                connection.commit()
                return current
            if row["state"] not in (
                OrderState.PENDING_SUBMIT.value,
                OrderState.SUBMIT_UNKNOWN.value,
            ):
                raise FillConflictError("order cannot become submitted")
            timestamp = _timestamp()
            connection.execute(
                """
                UPDATE strategy_orders
                SET broker_order_id = ?, state = 'SUBMITTED',
                    submitted_at = ?, updated_at = ?
                WHERE order_id = ?
                """,
                (broker_order_id, timestamp, timestamp, order_id),
            )
            current = self._order_from_row(self._select_order(connection, order_id))
            connection.commit()
            self._notify(
                TradeNotification(
                    event="ORDER_SUBMITTED",
                    strategy_id=account.strategy_id,
                    security=current.security,
                    security_name=self._security_name(
                        connection, current.intent_id, current.security
                    ),
                    side=current.side.value,
                    status=current.state.value,
                    quantity=current.requested_qty,
                    price=(
                        price_units_to_display(current.limit_price_units)
                        if current.limit_price_units is not None
                        else None
                    ),
                    amount=(
                        money_units_to_display(
                            _trade_value_units(
                                current.limit_price_units, current.requested_qty
                            )
                        )
                        if current.limit_price_units is not None
                        else None
                    ),
                    order_id=current.order_id,
                    detail="券商订单号 {}".format(broker_order_id),
                )
            )
            return current
        except (FillBookingError, FillConflictError):
            connection.rollback()
            raise
        except sqlite3.DatabaseError as exc:
            connection.rollback()
            raise RepositoryError("failed to mark order submitted") from exc
        finally:
            connection.close()

    def mark_order_submit_unknown(
        self,
        account_id: str,
        order_id: str,
        detail: str = "券商提交结果未知",
    ) -> BrokerOrder:
        connection = connect_database(self.database_path)
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = self._select_order(connection, order_id)
            if row["strategy_account_id"] != account_id:
                raise FillConflictError("order belongs to another strategy account")
            account = _account_from_row(
                self._select_account(connection, account_id)
            )
            if row["state"] == OrderState.SUBMIT_UNKNOWN.value:
                connection.commit()
                return self._order_from_row(row)
            if row["state"] != OrderState.PENDING_SUBMIT.value:
                raise FillConflictError("only pending order can become submit unknown")
            timestamp = _timestamp()
            connection.execute(
                """
                UPDATE strategy_orders
                SET state = 'SUBMIT_UNKNOWN', updated_at = ? WHERE order_id = ?
                """,
                (timestamp, order_id),
            )
            current = self._order_from_row(self._select_order(connection, order_id))
            connection.commit()
            self._notify(
                TradeNotification(
                    event="ERROR",
                    strategy_id=account.strategy_id,
                    security=current.security,
                    security_name=self._security_name(
                        connection, current.intent_id, current.security
                    ),
                    side=current.side.value,
                    status=current.state.value,
                    quantity=current.requested_qty,
                    order_id=current.order_id,
                    detail=detail,
                )
            )
            return current
        except (FillBookingError, FillConflictError):
            connection.rollback()
            raise
        except sqlite3.DatabaseError as exc:
            connection.rollback()
            raise RepositoryError("failed to mark submit unknown") from exc
        finally:
            connection.close()

    def book_fill(
        self,
        account_id: str,
        fill: BrokerFill,
        expected_ledger_version: int,
        sellable_from_trade_date: Optional[date] = None,
    ) -> FillBookingResult:
        if type(expected_ledger_version) is not int or expected_ledger_version < 0:
            raise LedgerInvariantError("expected ledger version must be non-negative")
        trade_date = fill.traded_at.date()
        if fill.side is OrderSide.BUY and (
            sellable_from_trade_date is None
            or sellable_from_trade_date < trade_date
        ):
            raise LedgerInvariantError(
                "buy fill requires a valid sellable trade date"
            )

        connection = connect_database(self.database_path)
        try:
            connection.execute("BEGIN IMMEDIATE")
            price_estimate = None
            if fill.price_source is FillPriceSource.ZERO_FALLBACK:
                fill, price_estimate = self._price_zero_fill(connection, account_id, fill)
            duplicate = self._find_duplicate_fill(connection, fill)
            if duplicate is not None:
                account = _account_from_row(self._select_account(connection, account_id))
                if not duplicate["price_known"] and fill.price_known:
                    corrected = self._correct_fill_price(
                        connection, account_id, account, duplicate, fill,
                        expected_ledger_version,
                    )
                    order = self._select_order(connection, fill.order_id)
                    connection.commit()
                    self._notify(
                        TradeNotification(
                            event="FILL_PRICE_CORRECTED",
                            strategy_id=corrected.account.strategy_id,
                            security=fill.security,
                            security_name=self._security_name(
                                connection, order["intent_id"], fill.security
                            ),
                            side=fill.side.value,
                            status=corrected.order_state.value,
                            quantity=fill.quantity,
                            price=price_units_to_display(fill.price_units),
                            amount=money_units_to_display(
                                _trade_value_units(fill.price_units, fill.quantity)
                            ),
                            order_id=fill.order_id,
                            trade_id=fill.broker_trade_id,
                            detail="券商补回真实成交价，策略资金和成本已按成交回报更正",
                            occurred_at=fill.traded_at,
                        )
                    )
                    return corrected
                position = self._select_position(connection, account_id, fill.security)
                order = self._select_order(connection, fill.order_id)
                connection.commit()
                return FillBookingResult(
                    account=account,
                    position=position,
                    order_state=OrderState(order["state"]),
                    realized_pnl_units=cast(int, duplicate["realized_pnl_units"]),
                    duplicate=True,
                )

            account = _account_from_row(self._select_account(connection, account_id))
            if account.ledger_version != expected_ledger_version:
                raise VersionConflictError("strategy account ledger version changed")
            order = self._select_order(connection, fill.order_id)
            if order["strategy_account_id"] != account_id:
                raise FillConflictError("fill order belongs to another strategy account")
            if order["security"] != fill.security or order["side"] != fill.side.value:
                raise FillConflictError("fill does not match its strategy order")
            if order["state"] in ("CANCELED", "REJECTED"):
                raise FillConflictError("terminal order cannot receive a fill")
            filled_after = order["filled_qty"] + fill.quantity
            if filled_after > order["requested_qty"]:
                raise FillConflictError("fill quantity exceeds requested quantity")

            gross_units = _trade_value_units(fill.price_units, fill.quantity)
            fee_units = _known_fee_units(fill)
            stored_commission_units = fill.commission_units or 0
            stored_tax_units = fill.tax_units or 0
            timestamp = _timestamp()
            connection.execute(
                """
                INSERT INTO fills(
                    fill_id, order_id, broker_trade_id, fill_fingerprint,
                    security, side, quantity, price_units, commission_units,
                    tax_units, commission_known, tax_known, traded_at, booked_at,
                    price_source, price_known
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    fill.fill_id,
                    fill.order_id,
                    fill.broker_trade_id,
                    fill.fingerprint,
                    fill.security,
                    fill.side.value,
                    fill.quantity,
                    fill.price_units,
                    stored_commission_units,
                    stored_tax_units,
                    int(fill.commission_units is not None),
                    int(fill.tax_units is not None),
                    fill.traded_at.isoformat(),
                    timestamp,
                    fill.price_source.value,
                    int(fill.price_known),
                ),
            )
            realized_pnl_units = 0
            proceeds_estimate_units = 0
            proceeds_estimate = None
            cost_estimate_units = 0
            cost_estimate = None
            if fill.side is OrderSide.BUY:
                order_reserved = self._order_reserved_units(
                    connection, account_id, fill.order_id
                )
                if fill.price_source is FillPriceSource.ZERO_FALLBACK:
                    cost_estimate_units, cost_estimate = _estimate_unpriced_buy_cost(
                        _intent_payload(connection, order["intent_id"]),
                        fill.security,
                        fill.quantity,
                        gross_units,
                        max(0, order_reserved - fee_units),
                        order["limit_price_units"],
                        order["requested_qty"] - order["filled_qty"],
                    )
                booked_cost_units = gross_units + cost_estimate_units
                cash_delta = -(booked_cost_units + fee_units)
                consumed_reservation = -cash_delta
                if consumed_reservation > order_reserved:
                    raise LedgerInvariantError(
                        "buy fill exceeds order reserved cash"
                    )
                terminal_surplus = (
                    order_reserved - consumed_reservation
                    if filled_after == order["requested_qty"]
                    else 0
                )
                reservation_released = consumed_reservation + terminal_surplus
                reserved_after = account.reserved_cash_units - reservation_released
                position = self._book_buy_position(
                    connection,
                    account_id,
                    fill,
                    booked_cost_units + fee_units,
                    cast(date, sellable_from_trade_date),
                )
            else:
                if (
                    gross_units < fee_units
                    and fill.price_source is not FillPriceSource.ZERO_FALLBACK
                ):
                    raise LedgerInvariantError("sell fees exceed trade value")
                if fill.price_source is FillPriceSource.ZERO_FALLBACK:
                    proceeds_estimate_units, proceeds_estimate = (
                        _estimate_unpriced_sell_proceeds(
                            _intent_payload(connection, order["intent_id"]),
                            fill.security,
                            fill.quantity,
                            gross_units,
                            order["limit_price_units"],
                        )
                    )
                # The estimate keeps target execution moving but is not a
                # broker-confirmed price or an accurate performance input.
                cash_delta = gross_units + proceeds_estimate_units - fee_units
                reservation_released = 0
                reserved_after = account.reserved_cash_units
                position, cost_basis_units = self._book_sell_position(
                    connection, account_id, fill, trade_date
                )
                realized_pnl_units = cash_delta - cost_basis_units

            cash_after = account.cash_units + cash_delta
            if cash_after < 0 or reserved_after < 0 or reserved_after > cash_after:
                raise LedgerInvariantError("fill would leave invalid strategy cash")
            order_state = (
                OrderState.FILLED
                if filled_after == order["requested_qty"]
                else OrderState.PARTIALLY_FILLED
            )
            next_version = account.ledger_version + 1
            next_event_seq = account.event_seq + 1
            updated = connection.execute(
                """
                UPDATE strategy_accounts
                SET cash_units = ?, reserved_cash_units = ?, ledger_version = ?,
                    event_seq = ?, updated_at = ?
                WHERE strategy_account_id = ? AND ledger_version = ? AND event_seq = ?
                """,
                (
                    cash_after,
                    reserved_after,
                    next_version,
                    next_event_seq,
                    timestamp,
                    account_id,
                    expected_ledger_version,
                    account.event_seq,
                ),
            )
            if updated.rowcount != 1:
                raise VersionConflictError("strategy account changed")
            connection.execute(
                """
                UPDATE strategy_orders
                SET filled_qty = ?, state = ?, terminal_at = ?, updated_at = ?
                WHERE order_id = ?
                """,
                (
                    filled_after,
                    order_state.value,
                    timestamp if order_state is OrderState.FILLED else None,
                    timestamp,
                    fill.order_id,
                ),
            )
            payload: Dict[str, Any] = {
                "fill_id": fill.fill_id,
                "broker_trade_id": fill.broker_trade_id,
                "order_id": fill.order_id,
                "security": fill.security,
                "side": fill.side.value,
                "quantity": fill.quantity,
                "gross_units": gross_units,
                "commission_units": fill.commission_units,
                "tax_units": fill.tax_units,
                "commission_known": fill.commission_units is not None,
                "tax_known": fill.tax_units is not None,
                "price_source": fill.price_source.value,
                "price_known": fill.price_known,
                "reservation_released_units": reservation_released,
                "realized_pnl_units": realized_pnl_units,
            }
            if price_estimate is not None:
                payload["price_estimate"] = price_estimate
                payload["broker_reported_price_units"] = 0
                if fill.side is OrderSide.BUY:
                    payload["estimated_cost_units"] = gross_units
                else:
                    payload["estimated_proceeds_units"] = gross_units
                    payload["credited_proceeds_estimate_units"] = gross_units
            if cost_estimate is not None:
                payload["cash_delta_units"] = cash_delta
                payload["estimated_cost_units"] = cost_estimate_units
                payload["cost_estimate"] = cost_estimate
            if proceeds_estimate is not None:
                payload["cash_delta_units"] = cash_delta
                payload["estimated_proceeds_units"] = proceeds_estimate_units
                payload["credited_proceeds_estimate_units"] = proceeds_estimate_units
                payload["proceeds_estimate"] = proceeds_estimate
            payload_json = json.dumps(
                payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            )
            connection.execute(
                """
                INSERT INTO ledger_entries(
                    strategy_account_id, event_seq, entry_type, amount_units,
                    cash_after_units, reserved_after_units, reference_type,
                    reference_id, payload_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'order', ?, ?, ?)
                """,
                (
                    account_id,
                    next_event_seq,
                    "BUY_FILL_BOOKED" if fill.side is OrderSide.BUY else "SELL_FILL_BOOKED",
                    cash_delta,
                    cash_after,
                    reserved_after,
                    fill.order_id,
                    payload_json,
                    timestamp,
                ),
            )
            connection.execute(
                """
                INSERT INTO strategy_events(
                    strategy_account_id, event_seq, event_type,
                    payload_json, created_at
                ) VALUES (?, ?, 'BROKER_FILL_BOOKED', ?, ?)
                """,
                (account_id, next_event_seq, payload_json, timestamp),
            )
            updated_account = _account_from_row(
                self._select_account(connection, account_id)
            )
            connection.commit()
            result = FillBookingResult(
                account=updated_account,
                position=position,
                order_state=order_state,
                realized_pnl_units=realized_pnl_units,
                duplicate=False,
            )
            self._notify(
                TradeNotification(
                    event=order_state.value,
                    strategy_id=updated_account.strategy_id,
                    security=fill.security,
                    security_name=self._security_name(
                        connection, order["intent_id"], fill.security
                    ),
                    side=fill.side.value,
                    status=order_state.value,
                    quantity=fill.quantity,
                    price=(
                        None if not fill.price_known and price_estimate is None
                        else price_units_to_display(fill.price_units)
                    ),
                    amount=(
                        None if not fill.price_known and price_estimate is None
                        else money_units_to_display(
                            gross_units + fee_units
                            if fill.side is OrderSide.BUY
                            else cash_delta
                        )
                    ),
                    order_id=fill.order_id,
                    trade_id=fill.broker_trade_id,
                    detail=_fee_notification_detail(
                        fill,
                        proceeds_estimate=proceeds_estimate,
                        cost_estimate=cost_estimate,
                        price_estimate=price_estimate,
                    ),
                    estimated=price_estimate is not None,
                    occurred_at=fill.traded_at,
                )
            )
            return result
        except (
            AccountNotFoundError,
            FillBookingError,
            LedgerInvariantError,
            VersionConflictError,
        ):
            connection.rollback()
            raise
        except sqlite3.DatabaseError as exc:
            connection.rollback()
            raise RepositoryError("failed to book broker fill") from exc
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _price_zero_fill(
        self,
        connection: sqlite3.Connection,
        account_id: str,
        fill: BrokerFill,
    ) -> Tuple[BrokerFill, Optional[dict]]:
        """Book a positive estimated price without changing the broker evidence ID."""

        existing = connection.execute(
            "SELECT price_units, price_source FROM fills WHERE fill_id = ?",
            (fill.fill_id,),
        ).fetchone()
        if existing is not None:
            if existing["price_source"] != FillPriceSource.ZERO_PRICE_ESTIMATE.value:
                return fill, None  # Historical raw-zero fill: preserve replay.
            return replace(
                fill,
                price_units=existing["price_units"],
                price_source=FillPriceSource.ZERO_PRICE_ESTIMATE,
            ), {"basis": "EXISTING_LEDGER_ESTIMATE", "price_units": existing["price_units"]}

        order = self._select_order(connection, fill.order_id)
        if order["strategy_account_id"] != account_id:
            raise FillConflictError("fill order belongs to another strategy account")
        payload = _intent_payload(connection, order["intent_id"])
        references = payload.get("reference_prices_units")
        reference = references.get(fill.security) if isinstance(references, dict) else None
        if type(reference) is int and reference > 0:
            estimated_price = reference
            basis = "INTENT_REFERENCE_PRICE"
        elif type(order["limit_price_units"]) is int and order["limit_price_units"] > 0:
            estimated_price = order["limit_price_units"]
            basis = "ORDER_LIMIT_PRICE"
        elif fill.side is OrderSide.SELL:
            position = self._select_position(connection, account_id, fill.security)
            estimated_price = position.avg_cost_price_units
            basis = "POSITION_COST_FALLBACK"
        else:
            estimated_price = 0
            basis = "ORDER_RESERVED_BUDGET"

        capped = False
        if fill.side is OrderSide.BUY:
            reserved = self._order_reserved_units(connection, account_id, fill.order_id)
            budget = max(0, reserved - _known_fee_units(fill))
            remaining = order["requested_qty"] - order["filled_qty"]
            if estimated_price <= 0 and remaining > 0:
                budget = budget * fill.quantity // remaining
            affordable_price = budget * PRICE_SCALE // (fill.quantity * MONEY_SCALE)
            if estimated_price > affordable_price:
                estimated_price = affordable_price
                capped = True
            elif estimated_price <= 0:
                estimated_price = affordable_price
        if estimated_price <= 0:
            # No defensible monetary estimate exists.  Keep the pre-existing
            # raw-zero path rather than inventing a nominal price.
            return fill, None
        return replace(
            fill,
            price_units=estimated_price,
            price_source=FillPriceSource.ZERO_PRICE_ESTIMATE,
        ), {
            "basis": basis,
            "price_units": estimated_price,
            "capped_to_order_reservation": capped,
        }

    def _correct_fill_price(
        self,
        connection: sqlite3.Connection,
        account_id: str,
        account: StrategyAccount,
        original: sqlite3.Row,
        fill: BrokerFill,
        expected_ledger_version: int,
    ) -> FillBookingResult:
        """Upgrade one previously estimated fill using the same broker trade ID."""

        if account.ledger_version != expected_ledger_version:
            raise VersionConflictError("strategy account ledger version changed")
        entry = connection.execute(
            """
            SELECT amount_units, payload_json FROM ledger_entries
            WHERE strategy_account_id = ? AND reference_type = 'order'
              AND reference_id = ?
              AND json_extract(payload_json, '$.fill_id') = ?
              AND entry_type IN ('BUY_FILL_BOOKED', 'SELL_FILL_BOOKED')
            """,
            (account_id, fill.order_id, fill.fill_id),
        ).fetchone()
        if entry is None:
            raise LedgerInvariantError("estimated fill ledger entry is missing")
        original_payload = json.loads(entry["payload_json"])
        if not isinstance(original_payload, dict):
            raise LedgerInvariantError("estimated fill ledger payload is invalid")

        new_commission = (
            fill.commission_units
            if fill.commission_units is not None
            else original["commission_units"] if original["commission_known"] else None
        )
        new_tax = (
            fill.tax_units
            if fill.tax_units is not None
            else original["tax_units"] if original["tax_known"] else None
        )
        known_fees = (new_commission or 0) + (new_tax or 0)
        actual_gross = _trade_value_units(fill.price_units, fill.quantity)
        actual_cash_delta = (
            -(actual_gross + known_fees)
            if fill.side is OrderSide.BUY
            else actual_gross - known_fees
        )
        prior_proceeds_correction = 0
        if fill.side is OrderSide.SELL:
            prior_proceeds_correction = connection.execute(
                """
                SELECT COALESCE(SUM(amount_units), 0) FROM ledger_entries
                WHERE strategy_account_id = ? AND reference_type = 'fill'
                  AND reference_id = ?
                  AND entry_type = 'SELL_PROCEEDS_ESTIMATE_CORRECTION'
                """,
                (account_id, fill.fill_id),
            ).fetchone()[0]
        cash_correction = (
            actual_cash_delta - entry["amount_units"] - prior_proceeds_correction
        )
        cash_after = account.cash_units + cash_correction
        if cash_after < account.reserved_cash_units:
            raise LedgerInvariantError(
                "verified fill price exceeds available strategy cash"
            )

        realized_correction = cash_correction if fill.side is OrderSide.SELL else 0
        if fill.side is OrderSide.BUY:
            lot = connection.execute(
                "SELECT * FROM position_lots WHERE source_fill_id = ?",
                (fill.fill_id,),
            ).fetchone()
            if lot is None or lot["strategy_account_id"] != account_id:
                raise LedgerInvariantError("estimated buy lot is missing")
            original_qty = lot["original_qty"]
            remaining_qty = lot["remaining_qty"]
            old_cost = (
                _trade_value_units(lot["cost_price_units"], original_qty)
                if original["price_source"] == FillPriceSource.ZERO_FALLBACK.value
                else _trade_value_units(original["price_units"], original_qty)
                + original["commission_units"] + original["tax_units"]
            )
            new_cost = actual_gross + known_fees
            old_remaining = _round_div(old_cost * remaining_qty, original_qty)
            new_remaining = _round_div(new_cost * remaining_qty, original_qty)
            realized_correction = -(
                (new_cost - new_remaining) - (old_cost - old_remaining)
            )
            connection.execute(
                "UPDATE position_lots SET cost_price_units = ? WHERE lot_id = ?",
                (_cost_price_units(new_cost, original_qty), lot["lot_id"]),
            )

        connection.execute(
            """
            UPDATE fills SET price_units = ?, price_source = 'BROKER_TRADE',
                price_known = 1, fill_fingerprint = ?,
                commission_units = ?, commission_known = ?,
                tax_units = ?, tax_known = ?
            WHERE fill_id = ?
            """,
            (
                fill.price_units, fill.fingerprint,
                new_commission or 0, int(new_commission is not None),
                new_tax or 0, int(new_tax is not None), fill.fill_id,
            ),
        )
        if fill.side is OrderSide.BUY:
            position = self._refresh_position(
                connection, account_id, fill.security, fill.traded_at.date()
            )
        else:
            position = self._select_position(connection, account_id, fill.security)

        next_version = account.ledger_version + 1
        next_event_seq = account.event_seq + 1
        timestamp = _timestamp()
        updated = connection.execute(
            """
            UPDATE strategy_accounts
            SET cash_units = ?, ledger_version = ?, event_seq = ?, updated_at = ?
            WHERE strategy_account_id = ? AND ledger_version = ? AND event_seq = ?
            """,
            (
                cash_after, next_version, next_event_seq, timestamp,
                account_id, account.ledger_version, account.event_seq,
            ),
        )
        if updated.rowcount != 1:
            raise VersionConflictError("strategy account changed")
        payload = {
            "fill_id": fill.fill_id,
            "broker_trade_id": fill.broker_trade_id,
            "previous_price_source": original["price_source"],
            "verified_price_units": fill.price_units,
            "cash_correction_units": cash_correction,
            "realized_pnl_units": realized_correction,
        }
        payload_json = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        connection.execute(
            """
            INSERT INTO ledger_entries(
                strategy_account_id, event_seq, entry_type, amount_units,
                cash_after_units, reserved_after_units, reference_type,
                reference_id, payload_json, created_at
            ) VALUES (?, ?, 'FILL_PRICE_CORRECTED', ?, ?, ?, 'fill', ?, ?, ?)
            """,
            (
                account_id, next_event_seq, cash_correction, cash_after,
                account.reserved_cash_units, fill.fill_id, payload_json, timestamp,
            ),
        )
        connection.execute(
            """
            INSERT INTO strategy_events(
                strategy_account_id, event_seq, event_type, payload_json, created_at
            ) VALUES (?, ?, 'BROKER_FILL_PRICE_CORRECTED', ?, ?)
            """,
            (account_id, next_event_seq, payload_json, timestamp),
        )
        updated_account = _account_from_row(
            self._select_account(connection, account_id)
        )
        order = self._select_order(connection, fill.order_id)
        return FillBookingResult(
            account=updated_account,
            position=position,
            order_state=OrderState(order["state"]),
            realized_pnl_units=realized_correction,
            duplicate=False,
            corrected=True,
        )

    def finalize_order(
        self,
        account_id: str,
        order_id: str,
        terminal_state: OrderState,
        expected_ledger_version: int,
    ) -> OrderFinalizationResult:
        if terminal_state not in (OrderState.CANCELED, OrderState.REJECTED):
            raise ValueError("terminal_state must be CANCELED or REJECTED")
        connection = connect_database(self.database_path)
        try:
            connection.execute("BEGIN IMMEDIATE")
            order = self._select_order(connection, order_id)
            if order["strategy_account_id"] != account_id:
                raise FillConflictError("order belongs to another strategy account")
            account = _account_from_row(self._select_account(connection, account_id))
            if order["state"] == terminal_state.value:
                connection.commit()
                return OrderFinalizationResult(account, 0, True)
            if order["state"] in ("FILLED", "CANCELED", "REJECTED"):
                raise FillConflictError("order already has another terminal state")
            if account.ledger_version != expected_ledger_version:
                raise VersionConflictError("strategy account ledger version changed")
            released = (
                self._order_reserved_units(connection, account_id, order_id)
                if order["side"] == OrderSide.BUY.value
                else 0
            )
            timestamp = _timestamp()
            if released:
                reserved_after = account.reserved_cash_units - released
                next_version = account.ledger_version + 1
                next_event_seq = account.event_seq + 1
                payload_json = json.dumps(
                    {"order_id": order_id, "amount_units": released},
                    sort_keys=True,
                    separators=(",", ":"),
                )
                connection.execute(
                    """
                    UPDATE strategy_accounts
                    SET reserved_cash_units = ?, ledger_version = ?, event_seq = ?,
                        updated_at = ?
                    WHERE strategy_account_id = ? AND ledger_version = ?
                    """,
                    (
                        reserved_after,
                        next_version,
                        next_event_seq,
                        timestamp,
                        account_id,
                        expected_ledger_version,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO ledger_entries(
                        strategy_account_id, event_seq, entry_type, amount_units,
                        cash_after_units, reserved_after_units, reference_type,
                        reference_id, payload_json, created_at
                    ) VALUES (?, ?, 'CASH_RELEASED', 0, ?, ?, 'order', ?, ?, ?)
                    """,
                    (
                        account_id,
                        next_event_seq,
                        account.cash_units,
                        reserved_after,
                        order_id,
                        payload_json,
                        timestamp,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO strategy_events(
                        strategy_account_id, event_seq, event_type,
                        payload_json, created_at
                    ) VALUES (?, ?, 'ORDER_TERMINATED', ?, ?)
                    """,
                    (account_id, next_event_seq, payload_json, timestamp),
                )
            connection.execute(
                """
                UPDATE strategy_orders
                SET state = ?, terminal_at = ?, updated_at = ? WHERE order_id = ?
                """,
                (terminal_state.value, timestamp, timestamp, order_id),
            )
            updated_account = _account_from_row(
                self._select_account(connection, account_id)
            )
            connection.commit()
            result = OrderFinalizationResult(updated_account, released, False)
            self._notify(
                TradeNotification(
                    event=terminal_state.value,
                    strategy_id=updated_account.strategy_id,
                    security=order["security"],
                    security_name=self._security_name(
                        connection, order["intent_id"], order["security"]
                    ),
                    side=order["side"],
                    status=terminal_state.value,
                    quantity=order["requested_qty"] - order["filled_qty"],
                    order_id=order_id,
                    detail="释放冻结资金 ¥{}".format(
                        money_units_to_display(released)
                    ),
                )
            )
            return result
        except (
            AccountNotFoundError,
            FillBookingError,
            LedgerInvariantError,
            VersionConflictError,
        ):
            connection.rollback()
            raise
        except sqlite3.DatabaseError as exc:
            connection.rollback()
            raise RepositoryError("failed to finalize strategy order") from exc
        finally:
            connection.close()

    def _find_duplicate_fill(
        self, connection: sqlite3.Connection, fill: BrokerFill
    ) -> Optional[sqlite3.Row]:
        row = connection.execute(
            """
            SELECT f.*, json_extract(e.payload_json, '$.realized_pnl_units')
                   AS realized_pnl_units
            FROM fills f
            JOIN strategy_orders o ON o.order_id = f.order_id
            JOIN ledger_entries e
              ON e.strategy_account_id = o.strategy_account_id
             AND e.reference_type = 'order' AND e.reference_id = f.order_id
             AND json_extract(e.payload_json, '$.fill_id') = f.fill_id
            WHERE f.fill_fingerprint = ?
               OR (
                    f.broker_trade_id = ?
                    AND substr(f.traded_at, 1, 10) = ?
               )
            """,
            (
                fill.fingerprint,
                fill.broker_trade_id,
                fill.traded_at.date().isoformat(),
            ),
        ).fetchone()
        if row is None:
            return None
        expected = (
            fill.order_id,
            fill.broker_trade_id,
            fill.security,
            fill.side.value,
            fill.quantity,
            fill.price_units,
            fill.traded_at.isoformat(),
            fill.price_source.value,
            int(fill.price_known),
        )
        actual = tuple(
            row[name]
            for name in (
                "order_id", "broker_trade_id", "security", "side",
                "quantity", "price_units", "traded_at",
                "price_source", "price_known",
            )
        )
        price_upgrade = (
            not row["price_known"]
            and fill.price_known
            and fill.price_source is FillPriceSource.BROKER_TRADE
            and row["fill_id"] == fill.fill_id
            and actual[:5] == expected[:5]
            and actual[6] == expected[6]
        )
        if not price_upgrade and (actual != expected or (
            fill.broker_trade_id is None
            and row["fill_fingerprint"] != fill.fingerprint
        )):
            raise FillConflictError("broker fill id was reused with different fields")
        for units_field, known_field, incoming in (
            ("commission_units", "commission_known", fill.commission_units),
            ("tax_units", "tax_known", fill.tax_units),
        ):
            if (
                row[known_field]
                and incoming is not None
                and row[units_field] != incoming
            ):
                raise FillConflictError(
                    "broker fill id was reused with different known fees"
                )
        return cast(sqlite3.Row, row)

    def _book_buy_position(
        self,
        connection: sqlite3.Connection,
        account_id: str,
        fill: BrokerFill,
        total_cost_units: int,
        sellable_from: date,
    ) -> Position:
        current = connection.execute(
            "SELECT * FROM positions WHERE strategy_account_id = ? AND security = ?",
            (account_id, fill.security),
        ).fetchone()
        lot_cost = _cost_price_units(total_cost_units, fill.quantity)
        timestamp = _timestamp()
        if current is None:
            total_after = fill.quantity
            avg_after = lot_cost
            version_after = 0
            connection.execute(
                """
                INSERT INTO positions(
                    strategy_account_id, security, total_qty, sellable_qty,
                    avg_cost_price_units, version, updated_at
                ) VALUES (?, ?, ?, 0, ?, 0, ?)
                """,
                (account_id, fill.security, total_after, avg_after, timestamp),
            )
        else:
            current = cast(sqlite3.Row, current)
            total_after = current["total_qty"] + fill.quantity
            avg_after = _round_div(
                current["avg_cost_price_units"] * current["total_qty"]
                + lot_cost * fill.quantity,
                total_after,
            )
            version_after = current["version"] + 1
            connection.execute(
                """
                UPDATE positions
                SET total_qty = ?, avg_cost_price_units = ?, version = ?, updated_at = ?
                WHERE strategy_account_id = ? AND security = ?
                """,
                (
                    total_after,
                    avg_after,
                    version_after,
                    timestamp,
                    account_id,
                    fill.security,
                ),
            )
        connection.execute(
            """
            INSERT INTO position_lots(
                lot_id, strategy_account_id, security, acquired_trade_date,
                sellable_from_trade_date, original_qty, remaining_qty,
                cost_price_units, source_fill_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "lot:{}".format(fill.fill_id),
                account_id,
                fill.security,
                fill.traded_at.date().isoformat(),
                sellable_from.isoformat(),
                fill.quantity,
                fill.quantity,
                lot_cost,
                fill.fill_id,
                fill.traded_at.isoformat(),
            ),
        )
        return self._refresh_position(connection, account_id, fill.security, fill.traded_at.date())

    def _book_sell_position(
        self,
        connection: sqlite3.Connection,
        account_id: str,
        fill: BrokerFill,
        trade_date: date,
    ) -> Tuple[Position, int]:
        current = connection.execute(
            "SELECT * FROM positions WHERE strategy_account_id = ? AND security = ?",
            (account_id, fill.security),
        ).fetchone()
        if current is None:
            raise LedgerInvariantError("sell fill has no strategy position")
        lots = connection.execute(
            """
            SELECT l.*, f.price_units AS source_price_units,
                   f.commission_units AS source_commission_units,
                   f.tax_units AS source_tax_units
            FROM position_lots l
            JOIN fills f ON f.fill_id = l.source_fill_id
            WHERE l.strategy_account_id = ? AND l.security = ?
              AND l.remaining_qty > 0 AND l.sellable_from_trade_date <= ?
            ORDER BY l.acquired_trade_date, l.created_at, l.lot_id
            """,
            (account_id, fill.security, trade_date.isoformat()),
        ).fetchall()
        remaining = fill.quantity
        cost_basis = 0
        for lot in lots:
            consumed = min(remaining, lot["remaining_qty"])
            if not consumed:
                continue
            remaining_after = lot["remaining_qty"] - consumed
            connection.execute(
                "UPDATE position_lots SET remaining_qty = remaining_qty - ? WHERE lot_id = ?",
                (consumed, lot["lot_id"]),
            )
            original_cost = (
                _trade_value_units(lot["cost_price_units"], lot["original_qty"])
                if lot["source_price_units"] == 0
                else _trade_value_units(lot["source_price_units"], lot["original_qty"])
                + lot["source_commission_units"]
                + lot["source_tax_units"]
            )
            cost_before = _round_div(
                original_cost * lot["remaining_qty"], lot["original_qty"]
            )
            cost_after = _round_div(
                original_cost * remaining_after, lot["original_qty"]
            )
            cost_basis += cost_before - cost_after
            remaining -= consumed
            if not remaining:
                break
        if remaining:
            raise LedgerInvariantError("sell fill exceeds sellable strategy position")
        return (
            self._refresh_position(connection, account_id, fill.security, trade_date),
            cost_basis,
        )

    def _refresh_position(
        self,
        connection: sqlite3.Connection,
        account_id: str,
        security: str,
        trade_date: date,
    ) -> Position:
        lot_rows = connection.execute(
            """
            SELECT l.*, f.price_units AS source_price_units,
                   f.commission_units AS source_commission_units,
                   f.tax_units AS source_tax_units
            FROM position_lots l
            JOIN fills f ON f.fill_id = l.source_fill_id
            WHERE l.strategy_account_id = ? AND l.security = ?
              AND l.remaining_qty > 0
            """,
            (account_id, security),
        ).fetchall()
        total_qty = sum(row["remaining_qty"] for row in lot_rows)
        sellable_qty = sum(
            row["remaining_qty"]
            for row in lot_rows
            if row["sellable_from_trade_date"] <= trade_date.isoformat()
        )
        remaining_cost = 0
        for row in lot_rows:
            source_price_units = row["source_price_units"]
            if source_price_units == 0:
                # 成交价未知的买入已在入账时按保护价上沿估算过成本，结论存在批次
                # 上；若仍拿 0 价重算，成本会被错误地压成只剩费用。
                per_lot_cost = _trade_value_units(
                    row["cost_price_units"], row["remaining_qty"]
                )
            else:
                per_lot_cost = _round_div(
                    (
                        _trade_value_units(source_price_units, row["original_qty"])
                        + row["source_commission_units"]
                        + row["source_tax_units"]
                    )
                    * row["remaining_qty"],
                    row["original_qty"],
                )
            remaining_cost += per_lot_cost
        avg_cost = _cost_price_units(remaining_cost, total_qty) if total_qty else 0
        connection.execute(
            """
            UPDATE positions
            SET total_qty = ?, sellable_qty = ?, avg_cost_price_units = ?,
                version = version + 1, updated_at = ?
            WHERE strategy_account_id = ? AND security = ?
            """,
            (total_qty, sellable_qty, avg_cost, _timestamp(), account_id, security),
        )
        return self._select_position(connection, account_id, security)

    @staticmethod
    def _order_reserved_units(
        connection: sqlite3.Connection, account_id: str, order_id: str
    ) -> int:
        balance = 0
        rows = connection.execute(
            """
            SELECT entry_type, payload_json FROM ledger_entries
            WHERE strategy_account_id = ? AND reference_type = 'order'
              AND reference_id = ?
              AND entry_type IN ('CASH_RESERVED', 'CASH_RELEASED', 'BUY_FILL_BOOKED')
            ORDER BY event_seq
            """,
            (account_id, order_id),
        ).fetchall()
        for row in rows:
            payload = json.loads(row["payload_json"])
            amount = (
                payload["reservation_released_units"]
                if row["entry_type"] == "BUY_FILL_BOOKED"
                else payload["amount_units"]
            )
            balance += amount if row["entry_type"] == "CASH_RESERVED" else -amount
        if balance < 0:
            raise RepositoryError("order reservation ledger is invalid")
        return cast(int, balance)

    @staticmethod
    def _select_account(connection: sqlite3.Connection, account_id: str) -> sqlite3.Row:
        row = connection.execute(
            "SELECT * FROM strategy_accounts WHERE strategy_account_id = ?",
            (account_id,),
        ).fetchone()
        if row is None:
            raise AccountNotFoundError("strategy account not found")
        return cast(sqlite3.Row, row)

    @staticmethod
    def _select_order(connection: sqlite3.Connection, order_id: str) -> sqlite3.Row:
        row = connection.execute(
            "SELECT * FROM strategy_orders WHERE order_id = ?", (order_id,)
        ).fetchone()
        if row is None:
            raise FillBookingError("strategy order not found")
        return cast(sqlite3.Row, row)

    @staticmethod
    def _select_position(
        connection: sqlite3.Connection, account_id: str, security: str
    ) -> Position:
        row = connection.execute(
            "SELECT * FROM positions WHERE strategy_account_id = ? AND security = ?",
            (account_id, security),
        ).fetchone()
        if row is None:
            raise FillBookingError("strategy position not found")
        return _position_from_row(cast(sqlite3.Row, row))

    @staticmethod
    def _security_name(
        connection: sqlite3.Connection,
        intent_id: str,
        security: str,
    ) -> str:
        row = connection.execute(
            "SELECT targets_json FROM portfolio_intents WHERE intent_id = ?",
            (intent_id,),
        ).fetchone()
        if row is None:
            return ""
        try:
            payload = json.loads(row["targets_json"])
            return str(payload.get("security_names", {}).get(security, ""))
        except (TypeError, ValueError, KeyError):
            return ""

    @staticmethod
    def _order_from_row(row: sqlite3.Row) -> BrokerOrder:
        return BrokerOrder(
            order_id=row["order_id"],
            account_id=row["strategy_account_id"],
            intent_id=row["intent_id"],
            client_tag=row["client_tag"],
            broker_order_id=row["broker_order_id"],
            security=row["security"],
            side=OrderSide(row["side"]),
            requested_qty=row["requested_qty"],
            filled_qty=row["filled_qty"],
            state=OrderState(row["state"]),
            trading_day=date.fromisoformat(row["trading_day"]),
            limit_price_units=row["limit_price_units"],
        )

    def _notify(self, notification: TradeNotification) -> None:
        if self._notification_handler is None:
            return
        try:
            self._notification_handler(notification)
        except Exception:
            pass
