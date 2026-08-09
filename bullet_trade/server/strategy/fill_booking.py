"""Book real broker fills into StrategyLedger."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Optional, Tuple, Union, cast

from .domain import (
    MONEY_SCALE,
    PRICE_SCALE,
    SHANGHAI_TZ,
    BrokerFill,
    BrokerOrder,
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
    def __init__(self, database_path: DatabasePath):
        self.database_path = Path(database_path)

    def register_order(self, order: BrokerOrder) -> BrokerOrder:
        connection = connect_database(self.database_path)
        try:
            connection.execute("BEGIN IMMEDIATE")
            self._select_account(connection, order.account_id)
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
                    state, trading_day, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    timestamp,
                    timestamp,
                ),
            )
            connection.commit()
            return order
        except (AccountNotFoundError, FillConflictError):
            connection.rollback()
            raise
        except sqlite3.DatabaseError as exc:
            connection.rollback()
            raise RepositoryError("failed to register strategy order") from exc
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
            or sellable_from_trade_date <= trade_date
        ):
            raise LedgerInvariantError(
                "buy fill requires a later sellable trade date"
            )

        connection = connect_database(self.database_path)
        try:
            connection.execute("BEGIN IMMEDIATE")
            duplicate = self._find_duplicate_fill(connection, fill)
            if duplicate is not None:
                account = _account_from_row(self._select_account(connection, account_id))
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
            fee_units = fill.commission_units + fill.tax_units
            realized_pnl_units = 0
            if fill.side is OrderSide.BUY:
                cash_delta = -(gross_units + fee_units)
                order_reserved = self._order_reserved_units(
                    connection, account_id, fill.order_id
                )
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
                    gross_units + fee_units,
                    cast(date, sellable_from_trade_date),
                )
            else:
                if gross_units < fee_units:
                    raise LedgerInvariantError("sell fees exceed trade value")
                cash_delta = gross_units - fee_units
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
            timestamp = _timestamp()
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
            payload = {
                "fill_id": fill.fill_id,
                "broker_trade_id": fill.broker_trade_id,
                "order_id": fill.order_id,
                "security": fill.security,
                "side": fill.side.value,
                "quantity": fill.quantity,
                "gross_units": gross_units,
                "commission_units": fill.commission_units,
                "tax_units": fill.tax_units,
                "reservation_released_units": reservation_released,
                "realized_pnl_units": realized_pnl_units,
            }
            payload_json = json.dumps(
                payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            )
            connection.execute(
                """
                INSERT INTO fills(
                    fill_id, order_id, broker_trade_id, fill_fingerprint,
                    security, side, quantity, price_units, commission_units,
                    tax_units, traded_at, booked_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    fill.commission_units,
                    fill.tax_units,
                    fill.traded_at.isoformat(),
                    timestamp,
                ),
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
            return FillBookingResult(
                account=updated_account,
                position=position,
                order_state=order_state,
                realized_pnl_units=realized_pnl_units,
                duplicate=False,
            )
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
            return OrderFinalizationResult(updated_account, released, False)
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
            WHERE f.fill_fingerprint = ? OR f.broker_trade_id = ?
            """,
            (fill.fingerprint, fill.broker_trade_id),
        ).fetchone()
        if row is None:
            return None
        expected = (
            fill.fill_id,
            fill.order_id,
            fill.broker_trade_id,
            fill.fingerprint,
            fill.security,
            fill.side.value,
            fill.quantity,
            fill.price_units,
            fill.commission_units,
            fill.tax_units,
            fill.traded_at.isoformat(),
        )
        actual = tuple(
            row[name]
            for name in (
                "fill_id", "order_id", "broker_trade_id", "fill_fingerprint",
                "security", "side", "quantity", "price_units",
                "commission_units", "tax_units", "traded_at",
            )
        )
        if actual != expected:
            raise FillConflictError("broker fill id was reused with different fields")
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
            SELECT * FROM position_lots
            WHERE strategy_account_id = ? AND security = ? AND remaining_qty > 0
              AND sellable_from_trade_date <= ?
            ORDER BY acquired_trade_date, created_at, lot_id
            """,
            (account_id, fill.security, trade_date.isoformat()),
        ).fetchall()
        remaining = fill.quantity
        cost_basis = 0
        for lot in lots:
            consumed = min(remaining, lot["remaining_qty"])
            if not consumed:
                continue
            connection.execute(
                "UPDATE position_lots SET remaining_qty = remaining_qty - ? WHERE lot_id = ?",
                (consumed, lot["lot_id"]),
            )
            cost_basis += _trade_value_units(lot["cost_price_units"], consumed)
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
        aggregates = connection.execute(
            """
            SELECT COALESCE(SUM(remaining_qty), 0) AS total_qty,
                   COALESCE(SUM(CASE WHEN sellable_from_trade_date <= ?
                                     THEN remaining_qty ELSE 0 END), 0) AS sellable_qty,
                   COALESCE(SUM(remaining_qty * cost_price_units), 0) AS weighted_cost
            FROM position_lots
            WHERE strategy_account_id = ? AND security = ?
            """,
            (trade_date.isoformat(), account_id, security),
        ).fetchone()
        total_qty = aggregates["total_qty"]
        sellable_qty = aggregates["sellable_qty"]
        avg_cost = (
            _round_div(aggregates["weighted_cost"], total_qty) if total_qty else 0
        )
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
        )
