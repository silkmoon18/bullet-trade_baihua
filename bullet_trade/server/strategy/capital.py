"""Capital calibration, allocation and reservation for StrategyLedger."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Union, cast
from uuid import uuid4

from .domain import SHANGHAI_TZ, AccountStatus, StrategyAccount
from .repository import (
    AccountNotFoundError,
    LedgerInvariantError,
    RepositoryError,
    SQLiteStrategyRepository,
    VersionConflictError,
)
from .schema import connect_database


DatabasePath = Union[str, Path]


class CapitalConfigurationError(RepositoryError):
    pass


class BrokerCashMismatchError(RepositoryError):
    pass


@dataclass(frozen=True)
class StrategyAccountEnsureResult:
    account: StrategyAccount
    created: bool


@dataclass(frozen=True)
class CapitalAdjustmentResult:
    account: StrategyAccount
    replayed: bool


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


def _timestamp() -> str:
    return datetime.now(SHANGHAI_TZ).isoformat()


class SQLiteCapitalService:
    def __init__(self, database_path: DatabasePath):
        self.database_path = Path(database_path)
        self._ledger = SQLiteStrategyRepository(database_path)

    def calibrate_broker_available_cash(
        self,
        physical_account_id: str,
        broker_available_cash_units: int,
    ) -> int:
        if type(broker_available_cash_units) is not int or broker_available_cash_units < 0:
            raise LedgerInvariantError("broker available cash must be non-negative")
        connection = connect_database(self.database_path)
        try:
            connection.execute("BEGIN IMMEDIATE")
            pool = connection.execute(
                """
                SELECT unallocated_cash_units, reserved_cash_units, version
                FROM cash_pools WHERE physical_account_id = ?
                """,
                (physical_account_id,),
            ).fetchone()
            if pool is None:
                raise AccountNotFoundError("physical account cash pool not found")
            accounts = connection.execute(
                """
                SELECT cash_units, reserved_cash_units FROM strategy_accounts
                WHERE physical_account_id = ?
                """,
                (physical_account_id,),
            ).fetchall()
            if not accounts:
                new_total = broker_available_cash_units + pool["reserved_cash_units"]
                connection.execute(
                    """
                    UPDATE cash_pools
                    SET unallocated_cash_units = ?, version = version + 1
                    WHERE physical_account_id = ?
                    """,
                    (new_total, physical_account_id),
                )
                connection.commit()
                return broker_available_cash_units

            expected_available = (
                pool["unallocated_cash_units"] - pool["reserved_cash_units"]
            ) + sum(
                row["cash_units"] - row["reserved_cash_units"] for row in accounts
            )
            if expected_available != broker_available_cash_units:
                raise BrokerCashMismatchError(
                    "broker available cash does not match StrategyLedger"
                )
            connection.commit()
            return cast(int, expected_available)
        except (AccountNotFoundError, BrokerCashMismatchError):
            connection.rollback()
            raise
        except sqlite3.DatabaseError as exc:
            connection.rollback()
            raise RepositoryError("failed to calibrate broker cash") from exc
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def ensure_strategy_account(
        self,
        account_id: str,
        strategy_id: str,
        physical_account_id: str,
        initial_capital_units: int,
    ) -> StrategyAccountEnsureResult:
        if type(initial_capital_units) is not int or initial_capital_units <= 0:
            raise LedgerInvariantError("initial capital must be a positive integer")
        connection = connect_database(self.database_path)
        try:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                """
                SELECT strategy_account_id, strategy_id, physical_account_id,
                       initial_capital_units, cash_units, reserved_cash_units,
                       ledger_version, event_seq, status
                FROM strategy_accounts
                WHERE strategy_account_id = ? OR strategy_id = ?
                """,
                (account_id, strategy_id),
            ).fetchone()
            if existing is not None:
                account = _account_from_row(cast(sqlite3.Row, existing))
                if (
                    account.account_id != account_id
                    or account.strategy_id != strategy_id
                    or account.physical_account_id != physical_account_id
                    or account.initial_capital_units != initial_capital_units
                ):
                    raise CapitalConfigurationError(
                        "existing strategy account does not match requested configuration"
                    )
                connection.commit()
                return StrategyAccountEnsureResult(account=account, created=False)

            pool = connection.execute(
                """
                SELECT unallocated_cash_units, reserved_cash_units, version
                FROM cash_pools WHERE physical_account_id = ?
                """,
                (physical_account_id,),
            ).fetchone()
            if pool is None:
                raise AccountNotFoundError("physical account cash pool not found")
            available = pool["unallocated_cash_units"] - pool["reserved_cash_units"]
            if available < initial_capital_units:
                raise LedgerInvariantError("real account has insufficient available cash")
            updated = connection.execute(
                """
                UPDATE cash_pools
                SET unallocated_cash_units = unallocated_cash_units - ?,
                    version = version + 1
                WHERE physical_account_id = ? AND version = ?
                  AND unallocated_cash_units - reserved_cash_units >= ?
                """,
                (
                    initial_capital_units,
                    physical_account_id,
                    pool["version"],
                    initial_capital_units,
                ),
            )
            if updated.rowcount != 1:
                raise VersionConflictError("physical account cash pool changed")
            timestamp = _timestamp()
            connection.execute(
                """
                INSERT INTO strategy_accounts(
                    strategy_account_id, strategy_id, physical_account_id,
                    initial_capital_units, cash_units, reserved_cash_units,
                    ledger_version, event_seq, status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 0, 0, 0, 'ACTIVE', ?, ?)
                """,
                (
                    account_id,
                    strategy_id,
                    physical_account_id,
                    initial_capital_units,
                    initial_capital_units,
                    timestamp,
                    timestamp,
                ),
            )
            connection.execute(
                """
                INSERT INTO capital_flows(
                    capital_flow_id, strategy_account_id, flow_type,
                    amount_units, external_ref, reason, created_at
                ) VALUES (?, ?, 'ALLOCATE', ?, ?, 'initial strategy capital', ?)
                """,
                (
                    str(uuid4()),
                    account_id,
                    initial_capital_units,
                    "initial:{}".format(strategy_id),
                    timestamp,
                ),
            )
            account = self._select_account(connection, account_id)
            connection.commit()
            return StrategyAccountEnsureResult(
                account=_account_from_row(account),
                created=True,
            )
        except (
            AccountNotFoundError,
            CapitalConfigurationError,
            LedgerInvariantError,
            VersionConflictError,
        ):
            connection.rollback()
            raise
        except sqlite3.DatabaseError as exc:
            connection.rollback()
            raise RepositoryError("failed to ensure strategy account") from exc
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def reserve_cash(
        self,
        account_id: str,
        amount_units: int,
        expected_ledger_version: int,
        order_id: str,
    ) -> StrategyAccount:
        if type(amount_units) is not int or amount_units <= 0:
            raise LedgerInvariantError("reserve amount must be a positive integer")
        if not order_id:
            raise ValueError("order_id cannot be empty")
        return self._change_order_reservation(
            account_id,
            expected_ledger_version=expected_ledger_version,
            reservation_delta_units=amount_units,
            entry_type="CASH_RESERVED",
            event_type="ORDER_FUNDS_RESERVED",
            order_id=order_id,
        )

    def release_cash(
        self,
        account_id: str,
        amount_units: int,
        expected_ledger_version: int,
        order_id: str,
    ) -> StrategyAccount:
        if type(amount_units) is not int or amount_units <= 0:
            raise LedgerInvariantError("release amount must be a positive integer")
        if not order_id:
            raise ValueError("order_id cannot be empty")
        return self._change_order_reservation(
            account_id,
            expected_ledger_version=expected_ledger_version,
            reservation_delta_units=-amount_units,
            entry_type="CASH_RELEASED",
            event_type="ORDER_FUNDS_RELEASED",
            order_id=order_id,
        )

    def _change_order_reservation(
        self,
        account_id: str,
        expected_ledger_version: int,
        reservation_delta_units: int,
        entry_type: str,
        event_type: str,
        order_id: str,
    ) -> StrategyAccount:
        connection = connect_database(self.database_path)
        try:
            connection.execute("BEGIN IMMEDIATE")
            account = _account_from_row(self._select_account(connection, account_id))
            if account.ledger_version != expected_ledger_version:
                raise VersionConflictError("strategy account ledger version changed")

            order_reserved_units = 0
            rows = connection.execute(
                """
                SELECT entry_type, payload_json FROM ledger_entries
                WHERE strategy_account_id = ? AND reference_type = 'order'
                  AND reference_id = ?
                  AND entry_type IN (
                      'CASH_RESERVED', 'CASH_RELEASED', 'BUY_FILL_BOOKED'
                  )
                ORDER BY event_seq
                """,
                (account_id, order_id),
            ).fetchall()
            for row in rows:
                try:
                    payload = json.loads(row["payload_json"])
                    recorded_amount = (
                        payload["reservation_released_units"]
                        if row["entry_type"] == "BUY_FILL_BOOKED"
                        else payload["amount_units"]
                    )
                except (KeyError, TypeError, ValueError) as exc:
                    raise RepositoryError("order reservation ledger is invalid") from exc
                if type(recorded_amount) is not int or recorded_amount <= 0:
                    raise RepositoryError("order reservation ledger is invalid")
                if row["entry_type"] == "CASH_RESERVED":
                    order_reserved_units += recorded_amount
                else:
                    order_reserved_units -= recorded_amount
                if order_reserved_units < 0:
                    raise RepositoryError("order reservation ledger is invalid")

            if reservation_delta_units > 0:
                if account.available_cash_units < reservation_delta_units:
                    raise LedgerInvariantError(
                        "strategy account has insufficient available cash"
                    )
            elif order_reserved_units < -reservation_delta_units:
                raise LedgerInvariantError("release amount exceeds order reserved cash")

            reserved_after = account.reserved_cash_units + reservation_delta_units
            if reserved_after < 0 or reserved_after > account.cash_units:
                raise LedgerInvariantError("reserved cash would leave valid range")
            next_version = account.ledger_version + 1
            next_event_seq = account.event_seq + 1
            timestamp = _timestamp()
            amount_units = abs(reservation_delta_units)
            payload_json = json.dumps(
                {"order_id": order_id, "amount_units": amount_units},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            updated = connection.execute(
                """
                UPDATE strategy_accounts
                SET reserved_cash_units = ?, ledger_version = ?, event_seq = ?,
                    updated_at = ?
                WHERE strategy_account_id = ? AND ledger_version = ? AND event_seq = ?
                """,
                (
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
                INSERT INTO ledger_entries(
                    strategy_account_id, event_seq, entry_type, amount_units,
                    cash_after_units, reserved_after_units, reference_type,
                    reference_id, payload_json, created_at
                ) VALUES (?, ?, ?, 0, ?, ?, 'order', ?, ?, ?)
                """,
                (
                    account_id,
                    next_event_seq,
                    entry_type,
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
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    account_id,
                    next_event_seq,
                    event_type,
                    payload_json,
                    timestamp,
                ),
            )
            result = _account_from_row(self._select_account(connection, account_id))
            connection.commit()
            return result
        except (
            AccountNotFoundError,
            LedgerInvariantError,
            RepositoryError,
            VersionConflictError,
        ):
            connection.rollback()
            raise
        except sqlite3.DatabaseError as exc:
            connection.rollback()
            raise RepositoryError("failed to change order reservation") from exc
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def adjust_capital(
        self,
        account_id: str,
        flow_type: str,
        amount_units: int,
        expected_ledger_version: int,
        external_ref: str,
        reason: str,
    ) -> CapitalAdjustmentResult:
        if flow_type not in ("ALLOCATE", "WITHDRAW"):
            raise ValueError("flow_type must be ALLOCATE or WITHDRAW")
        if type(amount_units) is not int or amount_units <= 0:
            raise LedgerInvariantError("capital amount must be a positive integer")
        if not external_ref or not reason:
            raise ValueError("external_ref and reason cannot be empty")
        connection = connect_database(self.database_path)
        try:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                """
                SELECT strategy_account_id, flow_type, amount_units, reason
                FROM capital_flows WHERE external_ref = ?
                """,
                (external_ref,),
            ).fetchone()
            if existing is not None:
                if (
                    existing["strategy_account_id"] != account_id
                    or existing["flow_type"] != flow_type
                    or existing["amount_units"] != amount_units
                    or existing["reason"] != reason
                ):
                    raise CapitalConfigurationError(
                        "capital external_ref was used with different parameters"
                    )
                replay_account_row = self._select_account(connection, account_id)
                connection.commit()
                return CapitalAdjustmentResult(
                    account=_account_from_row(replay_account_row),
                    replayed=True,
                )

            account_row = self._select_account(connection, account_id)
            account = _account_from_row(account_row)
            if account.ledger_version != expected_ledger_version:
                raise VersionConflictError("strategy account ledger version changed")
            pool = connection.execute(
                """
                SELECT unallocated_cash_units, reserved_cash_units, version
                FROM cash_pools WHERE physical_account_id = ?
                """,
                (account.physical_account_id,),
            ).fetchone()
            pool = cast(sqlite3.Row, pool)
            if flow_type == "ALLOCATE":
                available_pool = pool["unallocated_cash_units"] - pool["reserved_cash_units"]
                if available_pool < amount_units:
                    raise LedgerInvariantError("cash pool has insufficient available cash")
                pool_delta = -amount_units
                cash_delta = amount_units
            else:
                if account.available_cash_units < amount_units:
                    raise LedgerInvariantError("strategy account has insufficient available cash")
                pool_delta = amount_units
                cash_delta = -amount_units
            next_version = account.ledger_version + 1
            next_event_seq = account.event_seq + 1
            cash_after = account.cash_units + cash_delta
            timestamp = _timestamp()
            pool_update = connection.execute(
                """
                UPDATE cash_pools
                SET unallocated_cash_units = unallocated_cash_units + ?,
                    version = version + 1
                WHERE physical_account_id = ? AND version = ?
                  AND unallocated_cash_units + ? >= reserved_cash_units
                """,
                (
                    pool_delta,
                    account.physical_account_id,
                    pool["version"],
                    pool_delta,
                ),
            )
            if pool_update.rowcount != 1:
                raise VersionConflictError("physical account cash pool changed")
            account_update = connection.execute(
                """
                UPDATE strategy_accounts
                SET cash_units = ?, ledger_version = ?, event_seq = ?, updated_at = ?
                WHERE strategy_account_id = ? AND ledger_version = ? AND event_seq = ?
                """,
                (
                    cash_after,
                    next_version,
                    next_event_seq,
                    timestamp,
                    account_id,
                    expected_ledger_version,
                    account.event_seq,
                ),
            )
            if account_update.rowcount != 1:
                raise VersionConflictError("strategy account changed")
            payload_json = json.dumps(
                {
                    "external_ref": external_ref,
                    "flow_type": flow_type,
                    "amount_units": amount_units,
                    "reason": reason,
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            connection.execute(
                """
                INSERT INTO ledger_entries(
                    strategy_account_id, event_seq, entry_type, amount_units,
                    cash_after_units, reserved_after_units, reference_type,
                    reference_id, payload_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'capital_flow', ?, ?, ?)
                """,
                (
                    account_id,
                    next_event_seq,
                    "CAPITAL_ALLOCATED" if flow_type == "ALLOCATE" else "CAPITAL_WITHDRAWN",
                    cash_delta,
                    cash_after,
                    account.reserved_cash_units,
                    external_ref,
                    payload_json,
                    timestamp,
                ),
            )
            connection.execute(
                """
                INSERT INTO strategy_events(
                    strategy_account_id, event_seq, event_type, payload_json, created_at
                ) VALUES (?, ?, 'CAPITAL_FLOW_APPLIED', ?, ?)
                """,
                (account_id, next_event_seq, payload_json, timestamp),
            )
            connection.execute(
                """
                INSERT INTO capital_flows(
                    capital_flow_id, strategy_account_id, flow_type,
                    amount_units, external_ref, reason, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid4()),
                    account_id,
                    flow_type,
                    amount_units,
                    external_ref,
                    reason,
                    timestamp,
                ),
            )
            updated_account = self._select_account(connection, account_id)
            connection.commit()
            return CapitalAdjustmentResult(
                account=_account_from_row(updated_account),
                replayed=False,
            )
        except (
            AccountNotFoundError,
            CapitalConfigurationError,
            LedgerInvariantError,
            VersionConflictError,
        ):
            connection.rollback()
            raise
        except sqlite3.DatabaseError as exc:
            connection.rollback()
            raise RepositoryError("failed to adjust strategy capital") from exc
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _select_account(connection: sqlite3.Connection, account_id: str) -> sqlite3.Row:
        row = connection.execute(
            """
            SELECT strategy_account_id, strategy_id, physical_account_id,
                   initial_capital_units, cash_units, reserved_cash_units,
                   ledger_version, event_seq, status
            FROM strategy_accounts WHERE strategy_account_id = ?
            """,
            (account_id,),
        ).fetchone()
        if row is None:
            raise AccountNotFoundError("strategy account not found")
        return cast(sqlite3.Row, row)
