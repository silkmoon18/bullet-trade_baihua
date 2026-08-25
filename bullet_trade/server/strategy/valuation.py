"""Atomic strategy portfolio valuation from StrategyLedger state."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Mapping, Tuple, Union, cast

from .domain import (
    MONEY_SCALE,
    NAV_SCALE,
    PRICE_SCALE,
    AccountStatus,
    StrategyAccount,
    as_shanghai_time,
)
from .repository import AccountNotFoundError, LedgerInvariantError, RepositoryError
from .schema import connect_database


DatabasePath = Union[str, Path]


class ValuationReadinessError(RepositoryError):
    def __init__(self, blockers: Tuple[str, ...]):
        self.blockers = blockers
        super().__init__("portfolio valuation is not ready: {}".format(", ".join(blockers)))


@dataclass(frozen=True)
class MarketMark:
    security: str
    price_units: int
    as_of: datetime
    source: str

    def __post_init__(self) -> None:
        if not self.security or not self.source:
            raise ValueError("mark security and source cannot be empty")
        if type(self.price_units) is not int or self.price_units <= 0:
            raise ValueError("mark price must be a positive integer")
        object.__setattr__(self, "as_of", as_shanghai_time(self.as_of))


@dataclass(frozen=True)
class PortfolioPositionSnapshot:
    security: str
    total_qty: int
    sellable_qty: int
    avg_cost_price_units: int
    mark_price_units: int
    market_value_units: int
    remaining_cost_units: int
    unrealized_pnl_units: int
    position_version: int
    mark_as_of: datetime
    mark_source: str


@dataclass(frozen=True)
class PortfolioSnapshot:
    account_id: str
    strategy_id: str
    as_of: datetime
    snapshot_version: str
    ledger_version: int
    cash_units: int
    reserved_cash_units: int
    available_cash_units: int
    positions_value_units: int
    total_assets_units: int
    net_capital_units: int
    total_pnl_units: int
    realized_pnl_units: int
    unrealized_pnl_units: int
    fees_units: int
    nav_units: int
    fees_known: bool
    unknown_fee_fill_count: int
    performance_blockers: Tuple[str, ...]
    performance_ready: bool
    positions: Tuple[PortfolioPositionSnapshot, ...]


def _round_div(numerator: int, denominator: int) -> int:
    if denominator <= 0:
        raise LedgerInvariantError("valuation denominator must be positive")
    if numerator >= 0:
        return (numerator + denominator // 2) // denominator
    return -((-numerator + denominator // 2) // denominator)


def _trade_value_units(price_units: int, quantity: int) -> int:
    return _round_div(price_units * quantity * MONEY_SCALE, PRICE_SCALE)


def _account_from_row(row: sqlite3.Row) -> StrategyAccount:
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


class SQLiteValuationService:
    def __init__(self, database_path: DatabasePath):
        self.database_path = Path(database_path)

    def create_snapshot(
        self,
        account_id: str,
        marks: Mapping[str, MarketMark],
        as_of: datetime,
        max_mark_age: timedelta,
    ) -> PortfolioSnapshot:
        snapshot_time = as_shanghai_time(as_of)
        if max_mark_age < timedelta(0):
            raise ValueError("max_mark_age cannot be negative")
        captured_marks = dict(marks)
        connection = connect_database(self.database_path)
        try:
            connection.execute("BEGIN")
            account_row = connection.execute(
                "SELECT * FROM strategy_accounts WHERE strategy_account_id = ?",
                (account_id,),
            ).fetchone()
            if account_row is None:
                raise AccountNotFoundError("strategy account not found")
            account = _account_from_row(cast(sqlite3.Row, account_row))
            self._after_account_read(account)
            position_rows = connection.execute(
                """
                SELECT * FROM positions
                WHERE strategy_account_id = ? AND total_qty > 0
                ORDER BY security
                """,
                (account_id,),
            ).fetchall()
            blockers = self._mark_blockers(
                cast(Tuple[sqlite3.Row, ...], tuple(position_rows)),
                captured_marks,
                snapshot_time,
                max_mark_age,
            )
            if blockers:
                raise ValuationReadinessError(blockers)

            positions = []
            positions_value = 0
            unrealized_pnl = 0
            for row in position_rows:
                security = cast(str, row["security"])
                mark = captured_marks[security]
                lot_rows = connection.execute(
                    """
                    SELECT l.original_qty, l.remaining_qty,
                           l.sellable_from_trade_date,
                           f.price_units AS source_price_units,
                           f.commission_units AS source_commission_units,
                           f.tax_units AS source_tax_units
                    FROM position_lots l
                    JOIN fills f ON f.fill_id = l.source_fill_id
                    WHERE l.strategy_account_id = ? AND l.security = ?
                      AND l.remaining_qty > 0
                    """,
                    (account_id, security),
                ).fetchall()
                lot_quantity = sum(cast(int, lot["remaining_qty"]) for lot in lot_rows)
                if lot_quantity != row["total_qty"]:
                    raise LedgerInvariantError(
                        "position quantity does not match remaining lots for {}".format(security)
                    )
                remaining_cost = sum(
                    _round_div(
                        (
                            _trade_value_units(
                                lot["source_price_units"], lot["original_qty"]
                            )
                            + lot["source_commission_units"]
                            + lot["source_tax_units"]
                        )
                        * lot["remaining_qty"],
                        lot["original_qty"],
                    )
                    for lot in lot_rows
                )
                sellable_qty = sum(
                    lot["remaining_qty"]
                    for lot in lot_rows
                    if lot["sellable_from_trade_date"] <= snapshot_time.date().isoformat()
                )
                market_value = _trade_value_units(mark.price_units, row["total_qty"])
                position_unrealized = market_value - remaining_cost
                positions_value += market_value
                unrealized_pnl += position_unrealized
                positions.append(
                    PortfolioPositionSnapshot(
                        security=security,
                        total_qty=row["total_qty"],
                        sellable_qty=sellable_qty,
                        avg_cost_price_units=row["avg_cost_price_units"],
                        mark_price_units=mark.price_units,
                        market_value_units=market_value,
                        remaining_cost_units=remaining_cost,
                        unrealized_pnl_units=position_unrealized,
                        position_version=row["version"],
                        mark_as_of=mark.as_of,
                        mark_source=mark.source,
                    )
                )

            realized_pnl = self._realized_pnl(connection, account_id)
            fee_row = connection.execute(
                """
                SELECT COALESCE(SUM(f.commission_units + f.tax_units), 0),
                       COALESCE(SUM(
                           CASE WHEN f.commission_known = 0 OR f.tax_known = 0
                                THEN 1 ELSE 0 END
                       ), 0)
                FROM fills f
                JOIN strategy_orders o ON o.order_id = f.order_id
                WHERE o.strategy_account_id = ?
                """,
                (account_id,),
            ).fetchone()
            fees = cast(int, fee_row[0])
            unknown_fee_fill_count = cast(int, fee_row[1])
            flow_rows = connection.execute(
                """
                SELECT flow_type, amount_units FROM capital_flows
                WHERE strategy_account_id = ? ORDER BY created_at, capital_flow_id
                """,
                (account_id,),
            ).fetchall()
            net_capital = sum(
                row["amount_units"] if row["flow_type"] == "ALLOCATE" else -row["amount_units"]
                for row in flow_rows
            )
            if net_capital <= 0:
                raise LedgerInvariantError("strategy net capital must be positive")
            total_assets = account.cash_units + positions_value
            total_pnl = total_assets - net_capital
            if total_pnl != realized_pnl + unrealized_pnl:
                raise LedgerInvariantError(
                    "total pnl does not match realized plus unrealized pnl"
                )
            nav_units = _round_div(total_assets * NAV_SCALE, net_capital)
            position_tuple = tuple(positions)
            version_payload = {
                "account_id": account_id,
                "ledger_version": account.ledger_version,
                "event_seq": account.event_seq,
                "as_of": snapshot_time.isoformat(),
                "positions": [
                    {
                        "security": item.security,
                        "version": item.position_version,
                        "quantity": item.total_qty,
                        "sellable_qty": item.sellable_qty,
                        "mark_price_units": item.mark_price_units,
                        "mark_as_of": item.mark_as_of.isoformat(),
                        "mark_source": item.mark_source,
                    }
                    for item in position_tuple
                ],
            }
            snapshot_version = hashlib.sha256(
                json.dumps(
                    version_payload,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()
            performance_blockers = []
            if len(flow_rows) != 1:
                performance_blockers.append("capital_flows_unsupported")
            if unknown_fee_fill_count:
                performance_blockers.append("unknown_fill_fees")
            result = PortfolioSnapshot(
                account_id=account.account_id,
                strategy_id=account.strategy_id,
                as_of=snapshot_time,
                snapshot_version=snapshot_version,
                ledger_version=account.ledger_version,
                cash_units=account.cash_units,
                reserved_cash_units=account.reserved_cash_units,
                available_cash_units=account.available_cash_units,
                positions_value_units=positions_value,
                total_assets_units=total_assets,
                net_capital_units=net_capital,
                total_pnl_units=total_pnl,
                realized_pnl_units=realized_pnl,
                unrealized_pnl_units=unrealized_pnl,
                fees_units=fees,
                nav_units=nav_units,
                fees_known=unknown_fee_fill_count == 0,
                unknown_fee_fill_count=unknown_fee_fill_count,
                performance_blockers=tuple(performance_blockers),
                performance_ready=not performance_blockers,
                positions=position_tuple,
            )
            connection.commit()
            return result
        except (
            AccountNotFoundError,
            LedgerInvariantError,
            ValuationReadinessError,
        ):
            connection.rollback()
            raise
        except sqlite3.DatabaseError as exc:
            connection.rollback()
            raise RepositoryError("failed to create portfolio snapshot") from exc
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _after_account_read(self, account: StrategyAccount) -> None:
        """Test hook invoked after the read transaction snapshot is pinned."""

    @staticmethod
    def _mark_blockers(
        positions: Tuple[sqlite3.Row, ...],
        marks: Mapping[str, MarketMark],
        as_of: datetime,
        max_mark_age: timedelta,
    ) -> Tuple[str, ...]:
        blockers = []
        for row in positions:
            security = cast(str, row["security"])
            mark = marks.get(security)
            if mark is None:
                blockers.append("missing_mark:{}".format(security))
                continue
            if mark.security != security:
                blockers.append("mark_security_mismatch:{}".format(security))
            elif mark.as_of > as_of:
                blockers.append("future_mark:{}".format(security))
            elif as_of - mark.as_of > max_mark_age:
                blockers.append("stale_mark:{}".format(security))
        return tuple(blockers)

    @staticmethod
    def _realized_pnl(connection: sqlite3.Connection, account_id: str) -> int:
        rows = connection.execute(
            """
            SELECT payload_json FROM ledger_entries
            WHERE strategy_account_id = ? AND entry_type = 'SELL_FILL_BOOKED'
            ORDER BY event_seq
            """,
            (account_id,),
        ).fetchall()
        total = 0
        for row in rows:
            try:
                value = json.loads(row["payload_json"])["realized_pnl_units"]
            except (KeyError, TypeError, ValueError) as exc:
                raise LedgerInvariantError("sell fill pnl ledger is invalid") from exc
            if type(value) is not int:
                raise LedgerInvariantError("sell fill pnl ledger is invalid")
            total += value
        return total
