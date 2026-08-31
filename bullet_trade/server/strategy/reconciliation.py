"""Strategy-owned QMT synchronization for a shared physical account."""

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
        # QMT can expose signed rows for assets that do not belong to this
        # strategy.  Keep the raw quantities here and apply non-negative
        # capacity checks only to strategy-owned securities during reconcile.
        if type(self.total_qty) is not int:
            raise ValueError("broker position quantity is invalid")
        if type(self.sellable_qty) is not int:
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


def _broker_identifier(value: object) -> str:
    text = _text(value)
    return "" if text == "0" else text


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
    orders = await broker.list_orders(
        account_context, {"from_broker": True, "include_history": True}
    )
    trades = await broker.list_trades(account_context, {"include_history": True})
    return _build_broker_snapshot(account, positions, orders, trades, as_of)


def _build_broker_snapshot(
    account_row: Mapping[str, object],
    position_rows: Iterable[Mapping[str, object]],
    order_rows: Iterable[Mapping[str, object]],
    trade_rows: Iterable[Mapping[str, object]],
    as_of: Optional[datetime],
) -> BrokerAccountSnapshot:
    account = dict(account_row)
    wrapped = account.get("value")
    if account.get("dtype") == "dict" and isinstance(wrapped, Mapping):
        account = dict(wrapped)
    cash = None
    for name in ("available_cash", "cash", "enable_balance", "fund_avail"):
        if account.get(name) is not None:
            cash = account[name]
            break
    if cash is None:
        raise BrokerContractError("broker account snapshot has no available cash")
    try:
        cash_units = money_to_units(  # type: ignore[arg-type]
            str(cash) if type(cash) is float else cash
        )
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
    """Synchronize strategy-owned fills inside a shared broker account."""

    def __init__(
        self,
        database_path: DatabasePath,
        capabilities: BrokerCapabilityProfile,
        notification_handler=None,
        require_verified_capabilities: bool = True,
        durable_broker_history: bool = False,
        unknown_fee_tolerance_units_per_order: int = money_to_units("5"),
    ) -> None:
        if (
            type(unknown_fee_tolerance_units_per_order) is not int
            or unknown_fee_tolerance_units_per_order < 0
        ):
            raise ValueError("unknown fee tolerance must be a non-negative integer")
        self.database_path = Path(database_path)
        self.capabilities = capabilities
        self.require_verified_capabilities = bool(require_verified_capabilities)
        self.durable_broker_history = bool(durable_broker_history)
        self.unknown_fee_tolerance_units_per_order = (
            unknown_fee_tolerance_units_per_order
        )
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
        if self.require_verified_capabilities:
            try:
                require_strategy_ledger_v1(
                    self.capabilities,
                    durable_broker_history=self.durable_broker_history,
                )
            except BrokerContractError as exc:
                return self._persist_result(
                    account_id,
                    physical_account_id,
                    ReconciliationState.BLOCKED,
                    {
                        "blockers": ["capability:{}".format(str(exc))],
                        "capability_verification_required": True,
                    },
                    snapshot.as_of,
                )
        blockers = []
        booked_trade_ids = []
        ignored_broker_order_count = 0
        ignored_broker_trade_count = 0
        (
            orders_by_broker_id,
            orders_by_client_tag,
            all_orders_by_client_tag,
        ) = self._local_orders(account_id)
        broker_orders = {}
        broker_order_ids_by_sysid = {}
        adopted_order_ids = []
        for row in snapshot.orders:
            broker_order_id = _broker_identifier(row.get("order_id"))
            remark = _text(row.get("order_remark") or row.get("remark"))
            remark_matches = self._remark_matches(remark, all_orders_by_client_tag)
            if not broker_order_id:
                if remark_matches:
                    blockers.append("owned_broker_order_missing_id")
                else:
                    ignored_broker_order_count += 1
                continue
            broker_orders[broker_order_id] = row
            order_sysid = _broker_identifier(
                row.get("order_sysid") or row.get("sysid")
            )
            if order_sysid:
                broker_order_ids_by_sysid.setdefault(order_sysid, set()).add(
                    broker_order_id
                )
            local = orders_by_broker_id.get(broker_order_id)
            if local is None:
                matches = {
                    orders_by_client_tag[token]["order_id"]: orders_by_client_tag[token]
                    for token in (item.strip() for item in remark.split("|"))
                    if token in orders_by_client_tag
                }
                if len(matches) == 1:
                    local = next(iter(matches.values()))
                    try:
                        adopted = self._booking.mark_order_submitted(
                            account_id,
                            local["order_id"],
                            broker_order_id,
                        )
                        local = dict(local)
                        local["broker_order_id"] = broker_order_id
                        local["state"] = adopted.state.value
                        orders_by_broker_id[broker_order_id] = local
                        adopted_order_ids.append(local["order_id"])
                    except (RepositoryError, ValueError) as exc:
                        blockers.append(
                            "order_adoption_error:{}:{}".format(
                                broker_order_id, str(exc)
                            )
                        )
                elif len(matches) > 1:
                    blockers.append("ambiguous_order_remark:{}".format(broker_order_id))
            if local is None:
                if remark_matches:
                    blockers.append(
                        "owned_order_broker_id_mismatch:{}".format(broker_order_id)
                    )
                else:
                    ignored_broker_order_count += 1

        for trade in sorted(
            snapshot.trades,
            key=lambda row: _text(row.get("time") or row.get("trade_time")),
        ):
            broker_order_id = _broker_identifier(trade.get("order_id"))
            local = orders_by_broker_id.get(broker_order_id)
            if local is None:
                trade_sysid = _broker_identifier(
                    trade.get("order_sysid") or trade.get("sysid")
                )
                matching_order_ids = broker_order_ids_by_sysid.get(
                    trade_sysid, set()
                )
                if len(matching_order_ids) == 1:
                    candidate_id = next(iter(matching_order_ids))
                    candidate = orders_by_broker_id.get(candidate_id)
                    if candidate is not None:
                        broker_order_id = candidate_id
                        local = candidate
            remark = _text(trade.get("order_remark") or trade.get("remark"))
            if local is None and remark:
                matches = {
                    all_orders_by_client_tag[token]["order_id"]: all_orders_by_client_tag[
                        token
                    ]
                    for token in (item.strip() for item in remark.split("|"))
                    if token in all_orders_by_client_tag
                    and _broker_identifier(
                        all_orders_by_client_tag[token].get("broker_order_id")
                    )
                    in broker_orders
                }
                if len(matches) == 1:
                    candidate = next(iter(matches.values()))
                    broker_order_id = _broker_identifier(
                        candidate.get("broker_order_id")
                    )
                    local = candidate
            if local is None:
                if self._remark_matches(remark, all_orders_by_client_tag):
                    blockers.append(
                        "owned_trade_order_missing:{}".format(broker_order_id or "<empty>")
                    )
                else:
                    ignored_broker_trade_count += 1
                continue
            try:
                linked_trade = dict(trade)
                linked_trade["order_id"] = broker_order_id
                evidence = normalize_trade_evidence(linked_trade, broker_orders)
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
                blockers.append(
                    "trade_error:{}:{}".format(
                        _text(trade.get("trade_id")) or "<empty>", str(exc)
                    )
                )

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

        current_broker_ids = {
            broker_order_id
            for broker_order_id, row in broker_orders.items()
            if row.get("_broker_history_only") is not True
        }
        for broker_order_id, local in orders_by_broker_id.items():
            if local["state"] in (
                OrderState.SUBMITTED.value,
                OrderState.PARTIALLY_FILLED.value,
                OrderState.SUBMIT_UNKNOWN.value,
            ) and broker_order_id not in current_broker_ids:
                blockers.append("missing_working_order:{}".format(broker_order_id))

        required_cash, owned_positions = self._ledger_view(
            account_id, physical_account_id, snapshot.as_of
        )
        unknown_fee_order_count = self._unknown_fee_order_count(physical_account_id)
        unknown_fee_cash_tolerance = (
            unknown_fee_order_count * self.unknown_fee_tolerance_units_per_order
        )
        broker_cash_shortfall = max(
            0, required_cash - snapshot.available_cash_units
        )
        if broker_cash_shortfall > unknown_fee_cash_tolerance:
            blockers.append(
                "broker_cash_insufficient:strategy_required={}:broker={}".format(
                    required_cash, snapshot.available_cash_units
                )
            )
        broker_positions = {
            item.security: (max(item.total_qty, 0), max(item.sellable_qty, 0))
            for item in snapshot.positions
        }
        for security, (owned_total, owned_sellable) in sorted(owned_positions.items()):
            broker_total, broker_sellable = broker_positions.get(security, (0, 0))
            if broker_total < owned_total or broker_sellable < owned_sellable:
                blockers.append(
                    "broker_position_insufficient:{}:strategy=({},{}):broker=({},{})".format(
                        security,
                        owned_total,
                        owned_sellable,
                        broker_total,
                        broker_sellable,
                    )
                )

        details = {
            "blockers": sorted(set(blockers)),
            "booked_trade_ids": booked_trade_ids,
            "adopted_order_ids": adopted_order_ids,
            "broker_order_count": len(snapshot.orders),
            "broker_trade_count": len(snapshot.trades),
            "broker_position_count": len(snapshot.positions),
            "ignored_broker_order_count": ignored_broker_order_count,
            "ignored_broker_trade_count": ignored_broker_trade_count,
            "strategy_required_cash_units": required_cash,
            "broker_cash_shortfall_units": broker_cash_shortfall,
            "unknown_fee_order_count": unknown_fee_order_count,
            "unknown_fee_cash_tolerance_units": unknown_fee_cash_tolerance,
            "strategy_owned_position_count": len(owned_positions),
            "capability_verification_required": self.require_verified_capabilities,
            "durable_broker_history": self.durable_broker_history,
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
            by_broker_id = {
                row["broker_order_id"]: row
                for row in local_orders
                if row["broker_order_id"]
            }
            by_client_tag = {
                row["client_tag"]: row
                for row in local_orders
                if not row["broker_order_id"]
                and row["state"] in (
                    OrderState.PENDING_SUBMIT.value,
                    OrderState.SUBMIT_UNKNOWN.value,
                )
            }
            all_by_client_tag = {
                row["client_tag"]: row
                for row in local_orders
                if row["client_tag"]
            }
            return by_broker_id, by_client_tag, all_by_client_tag
        finally:
            connection.close()

    @staticmethod
    def _remark_matches(remark: str, orders_by_client_tag) -> bool:
        return any(
            token in orders_by_client_tag
            for token in (item.strip() for item in remark.split("|"))
            if token
        )

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
            required_cash = sum(
                row["cash_units"] - row["reserved_cash_units"] for row in accounts
            )
            position_rows = connection.execute(
                """
                SELECT p.strategy_account_id, p.security, p.total_qty
                FROM positions p
                JOIN strategy_accounts a
                  ON a.strategy_account_id = p.strategy_account_id
                WHERE a.physical_account_id = ?
                """,
                (physical_account_id,),
            ).fetchall()
            positions = {}
            for row in position_rows:
                sellable = connection.execute(
                    """
                    SELECT COALESCE(SUM(remaining_qty), 0) FROM position_lots
                    WHERE strategy_account_id = ? AND security = ?
                      AND sellable_from_trade_date <= ?
                    """,
                    (
                        row["strategy_account_id"],
                        row["security"],
                        as_of.date().isoformat(),
                    ),
                ).fetchone()[0]
                if row["total_qty"] or sellable:
                    current_total, current_sellable = positions.get(row["security"], (0, 0))
                    positions[row["security"]] = (
                        current_total + row["total_qty"],
                        current_sellable + sellable,
                    )
            connection.commit()
            return cast(int, required_cash), positions
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _unknown_fee_order_count(self, physical_account_id: str) -> int:
        connection = connect_database(self.database_path)
        try:
            row = connection.execute(
                """
                SELECT COUNT(DISTINCT f.order_id)
                FROM fills f
                JOIN strategy_orders o ON o.order_id = f.order_id
                JOIN strategy_accounts a
                  ON a.strategy_account_id = o.strategy_account_id
                WHERE a.physical_account_id = ?
                  AND (f.commission_known = 0 OR f.tax_known = 0)
                """,
                (physical_account_id,),
            ).fetchone()
            return cast(int, row[0])
        except sqlite3.DatabaseError as exc:
            raise RepositoryError("failed to inspect unknown broker fees") from exc
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
