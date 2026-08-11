"""Single-account QMT snapshot synchronization and reconciliation."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Protocol, Tuple, Union, cast
from uuid import uuid4

from .broker_contract import (
    BrokerCapabilityProfile,
    BrokerContractError,
    normalize_trade_evidence,
    require_strategy_ledger_v1,
)
from .domain import (
    SHANGHAI_TZ,
    AccountStatus,
    BrokerFill,
    OrderSide,
    OrderState,
    ReconciliationResult,
    ReconciliationState,
    as_shanghai_time,
    money_to_units,
)
from .fill_booking import SQLiteFillBookingService
from .repository import RepositoryError, SQLiteStrategyRepository
from .schema import connect_database


DatabasePath = Union[str, Path]


class BrokerSnapshotReader(Protocol):
    def get_account_info(self) -> Mapping[str, object]:
        ...

    def get_positions(self) -> Iterable[Mapping[str, object]]:
        ...

    def get_orders(
        self,
        order_id: Optional[str] = None,
        security: Optional[str] = None,
        status: Optional[object] = None,
        from_broker: bool = False,
    ) -> Iterable[Mapping[str, object]]:
        ...

    def get_trades(
        self,
        order_id: Optional[str] = None,
        security: Optional[str] = None,
    ) -> Iterable[Mapping[str, object]]:
        ...


class AsyncBrokerSnapshotReader(Protocol):
    async def get_account_info(self, account: object) -> Mapping[str, object]:
        ...

    async def get_positions(self, account: object) -> List[Mapping[str, object]]:
        ...

    async def list_orders(
        self,
        account: object,
        filters: Optional[Dict[str, Any]] = None,
    ) -> List[Mapping[str, object]]:
        ...

    async def list_trades(
        self,
        account: object,
        filters: Optional[Dict[str, Any]] = None,
    ) -> List[Mapping[str, object]]:
        ...


@dataclass(frozen=True)
class BrokerPositionSnapshot:
    security: str
    total_qty: int
    sellable_qty: int

    def __post_init__(self) -> None:
        if not self.security:
            raise ValueError("broker position security cannot be empty")
        if type(self.total_qty) is not int or self.total_qty < 0:
            raise ValueError("broker position quantity must be non-negative")
        if type(self.sellable_qty) is not int or not 0 <= self.sellable_qty <= self.total_qty:
            raise ValueError("broker sellable quantity is invalid")


@dataclass(frozen=True)
class BrokerAccountSnapshot:
    available_cash_units: int
    positions: Tuple[BrokerPositionSnapshot, ...]
    orders: Tuple[Mapping[str, object], ...]
    trades: Tuple[Mapping[str, object], ...]
    as_of: datetime

    def __post_init__(self) -> None:
        if type(self.available_cash_units) is not int or self.available_cash_units < 0:
            raise ValueError("broker available cash must be non-negative")
        object.__setattr__(self, "as_of", as_shanghai_time(self.as_of))


def _text(value: object) -> str:
    return str(value).strip() if value is not None else ""


def _security(value: object) -> str:
    text = _text(value)
    if text.endswith(".SH"):
        return text[:-3] + ".XSHG"
    if text.endswith(".SZ"):
        return text[:-3] + ".XSHE"
    return text


def _quantity(row: Mapping[str, object], names: Tuple[str, ...]) -> Optional[int]:
    for name in names:
        value = row.get(name)
        if value is None or value == "":
            continue
        if type(value) is int:
            return cast(int, value)
        if type(value) is str:
            try:
                return int(cast(str, value))
            except ValueError:
                pass
        raise ValueError("broker position quantity is invalid")
    return None


def collect_broker_snapshot(
    broker: BrokerSnapshotReader,
    as_of: Optional[datetime] = None,
) -> BrokerAccountSnapshot:
    """Read one coherent-enough polling snapshot from a QMT broker adapter."""

    return _build_broker_snapshot(
        broker.get_account_info(),
        broker.get_positions(),
        broker.get_orders(from_broker=True),
        broker.get_trades(),
        as_of,
    )


async def collect_async_broker_snapshot(
    broker: AsyncBrokerSnapshotReader,
    account_context: object,
    as_of: Optional[datetime] = None,
) -> BrokerAccountSnapshot:
    """Collect the same snapshot from BulletTrade's async server adapter."""

    account = await broker.get_account_info(account_context)
    positions = await broker.get_positions(account_context)
    orders = await broker.list_orders(account_context, {"from_broker": True})
    trades = await broker.list_trades(account_context)
    return _build_broker_snapshot(account, positions, orders, trades, as_of)


def _build_broker_snapshot(
    account_row: Mapping[str, object],
    position_rows: Iterable[Mapping[str, object]],
    order_rows: Iterable[Mapping[str, object]],
    trade_rows: Iterable[Mapping[str, object]],
    as_of: Optional[datetime],
) -> BrokerAccountSnapshot:
    account = dict(account_row)
    cash = None
    for name in ("available_cash", "cash", "enable_balance", "fund_avail"):
        if account.get(name) is not None:
            cash = account[name]
            break
    if cash is None:
        raise BrokerContractError("broker account snapshot has no available cash")
    try:
        cash_units = money_to_units(str(cash) if type(cash) is float else cash)  # type: ignore[arg-type]
    except (ArithmeticError, TypeError, ValueError) as exc:
        raise BrokerContractError("broker available cash is invalid") from exc

    normalized_positions = []
    seen = set()
    raw_positions = list(position_rows)
    if not raw_positions:
        account_positions = account.get("positions")
        if account_positions is not None:
            if not isinstance(account_positions, (list, tuple)):
                raise BrokerContractError("broker account positions must be a list")
            raw_positions = list(account_positions)
    for row in raw_positions:
        if not isinstance(row, Mapping):
            raise BrokerContractError("broker position row must be a mapping")
        security = _security(row.get("security") or row.get("stock_code") or row.get("code"))
        total = _quantity(row, ("amount", "volume", "current_amount", "total_qty"))
        if not security or total is None:
            raise BrokerContractError("broker position is missing security or quantity")
        sellable = _quantity(
            row,
            (
                "closeable_amount",
                "available_volume",
                "enable_amount",
                "can_sell_volume",
                "sellable_qty",
            ),
        )
        if sellable is None:
            sellable = total
        if security in seen:
            raise BrokerContractError("broker returned duplicate position security")
        seen.add(security)
        try:
            normalized_positions.append(
                BrokerPositionSnapshot(security, total, sellable)
            )
        except ValueError as exc:
            raise BrokerContractError(str(exc)) from exc

    timestamp = as_of or datetime.now(SHANGHAI_TZ)
    return BrokerAccountSnapshot(
        available_cash_units=cash_units,
        positions=tuple(normalized_positions),
        orders=tuple(dict(row) for row in order_rows),
        trades=tuple(dict(row) for row in trade_rows),
        as_of=timestamp,
    )


def _order_state(value: object) -> Optional[OrderState]:
    text = _text(getattr(value, "value", value)).lower()
    if text in ("canceled", "cancelled", "partly_canceled", "withdraw"):
        return OrderState.CANCELED
    if text in ("rejected", "failed", "invalid"):
        return OrderState.REJECTED
    return None


def _fingerprint(evidence: object) -> str:
    text = repr(evidence).encode("utf-8")
    return hashlib.sha256(text).hexdigest()


class SQLiteReconciliationService:
    """Synchronize known fills and block trading on any broker/ledger drift."""

    def __init__(
        self,
        database_path: DatabasePath,
        capabilities: BrokerCapabilityProfile,
        notification_handler=None,
    ) -> None:
        self.database_path = Path(database_path)
        self.capabilities = capabilities
        self._ledger = SQLiteStrategyRepository(database_path)
        self._booking = SQLiteFillBookingService(
            database_path,
            notification_handler=notification_handler,
        )

    def synchronize(
        self,
        account_id: str,
        physical_account_id: str,
        snapshot: BrokerAccountSnapshot,
    ) -> ReconciliationResult:
        try:
            require_strategy_ledger_v1(self.capabilities)
        except BrokerContractError as exc:
            return self._persist_result(
                account_id,
                physical_account_id,
                ReconciliationState.BLOCKED,
                {"blockers": ["capability:{}".format(str(exc))]},
                snapshot.as_of,
            )
        blockers = []
        booked_trade_ids = []
        orders_by_broker_id = self._local_orders(account_id)
        broker_orders = {}
        for row in snapshot.orders:
            broker_order_id = _text(row.get("order_id"))
            if not broker_order_id:
                blockers.append("broker_order_missing_id")
                continue
            broker_orders[broker_order_id] = row
            if broker_order_id not in orders_by_broker_id:
                blockers.append("unknown_order:{}".format(broker_order_id))

        for trade in sorted(snapshot.trades, key=lambda row: _text(row.get("time") or row.get("trade_time"))):
            broker_order_id = _text(trade.get("order_id"))
            local = orders_by_broker_id.get(broker_order_id)
            if local is None:
                blockers.append("unknown_trade_order:{}".format(broker_order_id or "<empty>"))
                continue
            try:
                evidence = normalize_trade_evidence(trade, broker_orders)
                fill = BrokerFill(
                    fill_id="broker:{}".format(evidence.broker_trade_id),
                    broker_trade_id=evidence.broker_trade_id,
                    order_id=local["order_id"],
                    fingerprint=_fingerprint(evidence),
                    security=evidence.security,
                    side=evidence.side,
                    quantity=evidence.quantity,
                    price_units=evidence.price_units,
                    commission_units=evidence.commission_units,
                    tax_units=evidence.tax_units,
                    traded_at=evidence.traded_at,
                )
                account = self._ledger.get_strategy_account(account_id)
                result = self._booking.book_fill(
                    account_id,
                    fill,
                    account.ledger_version,
                    sellable_from_trade_date=(
                        evidence.traded_at.date() + timedelta(days=1)
                        if evidence.side is OrderSide.BUY
                        else None
                    ),
                )
                if not result.duplicate:
                    booked_trade_ids.append(evidence.broker_trade_id)
            except (BrokerContractError, RepositoryError, ValueError) as exc:
                blockers.append("trade_error:{}:{}".format(_text(trade.get("trade_id")) or "<empty>", str(exc)))

        for broker_order_id, row in broker_orders.items():
            local = orders_by_broker_id.get(broker_order_id)
            terminal = _order_state(row.get("status"))
            if local is None or terminal is None:
                continue
            try:
                account = self._ledger.get_strategy_account(account_id)
                self._booking.finalize_order(
                    account_id,
                    local["order_id"],
                    terminal,
                    account.ledger_version,
                )
            except RepositoryError as exc:
                blockers.append("order_error:{}:{}".format(broker_order_id, str(exc)))

        broker_ids = set(broker_orders)
        for broker_order_id, local in orders_by_broker_id.items():
            if local["state"] in (
                OrderState.SUBMITTED.value,
                OrderState.PARTIALLY_FILLED.value,
                OrderState.SUBMIT_UNKNOWN.value,
            ) and broker_order_id not in broker_ids:
                blockers.append("missing_working_order:{}".format(broker_order_id))

        expected_cash, local_positions = self._ledger_view(account_id, physical_account_id, snapshot.as_of)
        if expected_cash != snapshot.available_cash_units:
            blockers.append(
                "cash_mismatch:ledger={}:broker={}".format(
                    expected_cash, snapshot.available_cash_units
                )
            )
        broker_positions = {
            item.security: (item.total_qty, item.sellable_qty)
            for item in snapshot.positions
            if item.total_qty or item.sellable_qty
        }
        if local_positions != broker_positions:
            blockers.append(
                "position_mismatch:ledger={}:broker={}".format(
                    sorted(local_positions.items()), sorted(broker_positions.items())
                )
            )

        details = {
            "blockers": sorted(set(blockers)),
            "booked_trade_ids": booked_trade_ids,
            "broker_order_count": len(snapshot.orders),
            "broker_trade_count": len(snapshot.trades),
            "broker_position_count": len(snapshot.positions),
        }
        state = ReconciliationState.BLOCKED if blockers else ReconciliationState.READY
        return self._persist_result(
            account_id,
            physical_account_id,
            state,
            details,
            snapshot.as_of,
        )

    def latest(self, physical_account_id: str) -> Optional[ReconciliationResult]:
        connection = connect_database(self.database_path)
        try:
            row = connection.execute(
                """
                SELECT * FROM reconciliation_runs
                WHERE physical_account_id = ?
                ORDER BY started_at DESC LIMIT 1
                """,
                (physical_account_id,),
            ).fetchone()
            if row is None:
                return None
            return ReconciliationResult(
                reconciliation_id=row["reconciliation_id"],
                physical_account_id=row["physical_account_id"],
                state=ReconciliationState(row["state"]),
                details=json.loads(row["details_json"]),
                broker_as_of=datetime.fromisoformat(row["broker_as_of"]),
            )
        finally:
            connection.close()

    def _local_orders(self, account_id: str):
        connection = connect_database(self.database_path)
        try:
            rows = connection.execute(
                "SELECT * FROM strategy_orders WHERE strategy_account_id = ?",
                (account_id,),
            ).fetchall()
            local_orders = [dict(row) for row in rows]
            return {
                row["broker_order_id"]: row
                for row in local_orders
                if row["broker_order_id"]
            }
        finally:
            connection.close()

    def _ledger_view(
        self,
        account_id: str,
        physical_account_id: str,
        as_of: datetime,
    ) -> Tuple[int, Dict[str, Tuple[int, int]]]:
        connection = connect_database(self.database_path)
        try:
            connection.execute("BEGIN")
            pool = connection.execute(
                "SELECT * FROM cash_pools WHERE physical_account_id = ?",
                (physical_account_id,),
            ).fetchone()
            if pool is None:
                raise RepositoryError("physical cash pool not found")
            accounts = connection.execute(
                "SELECT * FROM strategy_accounts WHERE physical_account_id = ?",
                (physical_account_id,),
            ).fetchall()
            if not any(row["strategy_account_id"] == account_id for row in accounts):
                raise RepositoryError("strategy account does not use physical account")
            available = pool["unallocated_cash_units"] - pool["reserved_cash_units"]
            available += sum(
                row["cash_units"] - row["reserved_cash_units"] for row in accounts
            )
            position_rows = connection.execute(
                "SELECT security, total_qty FROM positions WHERE strategy_account_id = ?",
                (account_id,),
            ).fetchall()
            positions = {}
            for row in position_rows:
                sellable = connection.execute(
                    """
                    SELECT COALESCE(SUM(remaining_qty), 0) FROM position_lots
                    WHERE strategy_account_id = ? AND security = ?
                      AND sellable_from_trade_date <= ?
                    """,
                    (account_id, row["security"], as_of.date().isoformat()),
                ).fetchone()[0]
                if row["total_qty"] or sellable:
                    positions[row["security"]] = (row["total_qty"], sellable)
            connection.commit()
            return cast(int, available), positions
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _persist_result(
        self,
        account_id: str,
        physical_account_id: str,
        state: ReconciliationState,
        details: Mapping[str, object],
        broker_as_of: datetime,
    ) -> ReconciliationResult:
        reconciliation_id = str(uuid4())
        timestamp = datetime.now(SHANGHAI_TZ).isoformat()
        details_json = json.dumps(details, ensure_ascii=False, sort_keys=True)
        account_status = (
            AccountStatus.RECONCILIATION_BLOCKED
            if state is ReconciliationState.BLOCKED
            else AccountStatus.ACTIVE
        )
        physical_status = "BLOCKED" if state is ReconciliationState.BLOCKED else "READY"
        connection = connect_database(self.database_path)
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO reconciliation_runs(
                    reconciliation_id, physical_account_id, state, broker_as_of,
                    details_json, started_at, completed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    reconciliation_id,
                    physical_account_id,
                    state.value,
                    broker_as_of.isoformat(),
                    details_json,
                    timestamp,
                    timestamp,
                ),
            )
            connection.execute(
                """
                UPDATE strategy_accounts
                SET status = CASE
                    WHEN status IN ('TRADING_BLOCKED','CLOSED') THEN status
                    WHEN ? = 'RECONCILIATION_BLOCKED' THEN 'RECONCILIATION_BLOCKED'
                    WHEN status = 'RECONCILIATION_BLOCKED' THEN 'ACTIVE'
                    ELSE status
                END,
                updated_at = ?
                WHERE strategy_account_id = ?
                """,
                (account_status.value, timestamp, account_id),
            )
            connection.execute(
                """
                UPDATE physical_accounts
                SET status = CASE
                    WHEN status = 'CLOSED' THEN status
                    ELSE ?
                END,
                updated_at = ?
                WHERE physical_account_id = ?
                """,
                (physical_status, timestamp, physical_account_id),
            )
            connection.commit()
        except sqlite3.DatabaseError as exc:
            connection.rollback()
            raise RepositoryError("failed to persist reconciliation result") from exc
        finally:
            connection.close()
        return ReconciliationResult(
            reconciliation_id=reconciliation_id,
            physical_account_id=physical_account_id,
            state=state,
            details=details,
            broker_as_of=broker_as_of,
        )
