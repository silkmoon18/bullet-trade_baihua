"""Transactional SQLite repository for the StrategyLedger core."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import List, Mapping, Optional, Protocol, Union, cast
from uuid import uuid4

from .domain import (
    AccountStatus,
    LedgerEntry,
    SHANGHAI_TZ,
    StrategyAccount,
    StrategyEvent,
)
from .schema import connect_database, migrate_database


RepositoryPath = Union[str, Path]


class RepositoryError(RuntimeError):
    """Base error for persistent StrategyLedger operations."""


class AccountNotFoundError(RepositoryError):
    pass


class VersionConflictError(RepositoryError):
    pass


class LedgerInvariantError(RepositoryError):
    pass


class StrategyRepository(Protocol):
    def get_strategy_account(self, account_id: str) -> StrategyAccount:
        ...

    def append_account_event(
        self,
        account_id: str,
        expected_ledger_version: int,
        entry_type: str,
        amount_units: int,
        reserved_after_units: int,
        event_type: str,
        payload: Mapping[str, object],
        reference_type: Optional[str] = None,
        reference_id: Optional[str] = None,
    ) -> StrategyAccount:
        ...

    def replay_account(self, account_id: str) -> StrategyAccount:
        ...


def _now_text() -> str:
    return datetime.now(SHANGHAI_TZ).isoformat()


def _jsonable(value: object) -> object:
    if value is None or type(value) in (bool, int, float, str):
        return value
    if isinstance(value, Mapping):
        result = {}
        for key, item in value.items():
            if type(key) is not str:
                raise TypeError("event payload keys must be strings")
            result[key] = _jsonable(item)
        return result
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    raise TypeError("event payload must be JSON-compatible")


def _payload_text(payload: Mapping[str, object]) -> str:
    return json.dumps(
        _jsonable(payload),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _row_to_account(row: sqlite3.Row) -> StrategyAccount:
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


class SQLiteStrategyRepository:
    """Small repository using one SQLite transaction per write operation."""

    def __init__(self, database_path: RepositoryPath):
        self.database_path = Path(database_path)

    def initialize(self) -> int:
        return migrate_database(self.database_path)

    def create_physical_account(
        self,
        physical_account_id: str,
        broker_kind: str,
        broker_account_ref: str,
        unallocated_cash_units: int = 0,
    ) -> None:
        if type(unallocated_cash_units) is not int or unallocated_cash_units < 0:
            raise LedgerInvariantError("unallocated cash must be a non-negative integer")
        connection = connect_database(self.database_path)
        try:
            connection.execute("BEGIN IMMEDIATE")
            timestamp = _now_text()
            connection.execute(
                """
                INSERT INTO physical_accounts(
                    physical_account_id, broker_kind, broker_account_ref,
                    status, created_at, updated_at
                ) VALUES (?, ?, ?, 'READY', ?, ?)
                """,
                (
                    physical_account_id,
                    broker_kind,
                    broker_account_ref,
                    timestamp,
                    timestamp,
                ),
            )
            connection.execute(
                """
                INSERT INTO cash_pools(
                    physical_account_id, unallocated_cash_units,
                    reserved_cash_units, version
                ) VALUES (?, ?, 0, 0)
                """,
                (physical_account_id, unallocated_cash_units),
            )
            connection.commit()
        except sqlite3.DatabaseError as exc:
            connection.rollback()
            raise RepositoryError("failed to create physical account") from exc
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def create_strategy_account(
        self,
        account_id: str,
        strategy_id: str,
        physical_account_id: str,
        initial_capital_units: int,
    ) -> StrategyAccount:
        if type(initial_capital_units) is not int or initial_capital_units <= 0:
            raise LedgerInvariantError("initial capital must be a positive integer")
        connection = connect_database(self.database_path)
        try:
            connection.execute("BEGIN IMMEDIATE")
            timestamp = _now_text()
            pool = connection.execute(
                """
                SELECT unallocated_cash_units, reserved_cash_units, version
                FROM cash_pools
                WHERE physical_account_id = ?
                """,
                (physical_account_id,),
            ).fetchone()
            if pool is None:
                raise AccountNotFoundError("physical account cash pool not found")
            available_pool_cash = (
                pool["unallocated_cash_units"] - pool["reserved_cash_units"]
            )
            if available_pool_cash < initial_capital_units:
                raise LedgerInvariantError("physical account has insufficient unallocated cash")
            pool_update = connection.execute(
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
            if pool_update.rowcount != 1:
                raise VersionConflictError("physical account cash pool changed")
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
                ) VALUES (?, ?, 'ALLOCATE', ?, ?, ?, ?)
                """,
                (
                    str(uuid4()),
                    account_id,
                    initial_capital_units,
                    "initial:{}".format(strategy_id),
                    "initial strategy capital",
                    timestamp,
                ),
            )
            row = self._select_account(connection, account_id)
            connection.commit()
            return _row_to_account(row)
        except (AccountNotFoundError, LedgerInvariantError, VersionConflictError):
            connection.rollback()
            raise
        except sqlite3.DatabaseError as exc:
            connection.rollback()
            raise RepositoryError("failed to create strategy account") from exc
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def get_strategy_account(self, account_id: str) -> StrategyAccount:
        connection = connect_database(self.database_path)
        try:
            return _row_to_account(self._select_account(connection, account_id))
        except AccountNotFoundError:
            raise
        except sqlite3.DatabaseError as exc:
            raise RepositoryError("failed to read strategy account") from exc
        finally:
            connection.close()

    def append_account_event(
        self,
        account_id: str,
        expected_ledger_version: int,
        entry_type: str,
        amount_units: int,
        reserved_after_units: int,
        event_type: str,
        payload: Mapping[str, object],
        reference_type: Optional[str] = None,
        reference_id: Optional[str] = None,
    ) -> StrategyAccount:
        if type(expected_ledger_version) is not int or expected_ledger_version < 0:
            raise LedgerInvariantError("expected ledger version must be non-negative")
        if type(amount_units) is not int:
            raise LedgerInvariantError("ledger amount must be an integer")
        if type(reserved_after_units) is not int or reserved_after_units < 0:
            raise LedgerInvariantError("reserved cash must be a non-negative integer")
        if not entry_type or not event_type:
            raise LedgerInvariantError("entry and event types cannot be empty")
        try:
            serialized_payload = _payload_text(payload)
        except TypeError as exc:
            raise LedgerInvariantError(str(exc)) from exc

        connection = connect_database(self.database_path)
        try:
            connection.execute("BEGIN IMMEDIATE")
            current = _row_to_account(self._select_account(connection, account_id))
            if current.ledger_version != expected_ledger_version:
                raise VersionConflictError(
                    "expected ledger version {}, found {}".format(
                        expected_ledger_version,
                        current.ledger_version,
                    )
                )
            cash_after_units = current.cash_units + amount_units
            if cash_after_units < 0:
                raise LedgerInvariantError("ledger event would make cash negative")
            if reserved_after_units > cash_after_units:
                raise LedgerInvariantError("reserved cash cannot exceed cash")

            next_version = current.ledger_version + 1
            next_event_seq = current.event_seq + 1
            timestamp = _now_text()
            cursor = connection.execute(
                """
                UPDATE strategy_accounts
                SET cash_units = ?, reserved_cash_units = ?, ledger_version = ?,
                    event_seq = ?, updated_at = ?
                WHERE strategy_account_id = ? AND ledger_version = ? AND event_seq = ?
                """,
                (
                    cash_after_units,
                    reserved_after_units,
                    next_version,
                    next_event_seq,
                    timestamp,
                    account_id,
                    expected_ledger_version,
                    current.event_seq,
                ),
            )
            if cursor.rowcount != 1:
                raise VersionConflictError("strategy account changed during commit")
            connection.execute(
                """
                INSERT INTO ledger_entries(
                    strategy_account_id, event_seq, entry_type, amount_units,
                    cash_after_units, reserved_after_units,
                    reference_type, reference_id, payload_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    account_id,
                    next_event_seq,
                    entry_type,
                    amount_units,
                    cash_after_units,
                    reserved_after_units,
                    reference_type,
                    reference_id,
                    serialized_payload,
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
                    serialized_payload,
                    timestamp,
                ),
            )
            row = self._select_account(connection, account_id)
            connection.commit()
            return _row_to_account(row)
        except (AccountNotFoundError, LedgerInvariantError, VersionConflictError):
            connection.rollback()
            raise
        except sqlite3.DatabaseError as exc:
            connection.rollback()
            raise RepositoryError("failed to append account event") from exc
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def list_ledger_entries(self, account_id: str) -> List[LedgerEntry]:
        connection = connect_database(self.database_path)
        try:
            return self._read_ledger_entries(connection, account_id)
        except sqlite3.DatabaseError as exc:
            raise RepositoryError("failed to read ledger entries") from exc
        finally:
            connection.close()

    def list_events(self, account_id: str) -> List[StrategyEvent]:
        connection = connect_database(self.database_path)
        try:
            return self._read_events(connection, account_id)
        except sqlite3.DatabaseError as exc:
            raise RepositoryError("failed to read strategy events") from exc
        finally:
            connection.close()

    def replay_account(self, account_id: str) -> StrategyAccount:
        connection = connect_database(self.database_path)
        try:
            connection.execute("BEGIN")
            materialized = _row_to_account(self._select_account(connection, account_id))
            entries = self._read_ledger_entries(connection, account_id)
            events = self._read_events(connection, account_id)
            replayed = self._replay_snapshot(materialized, entries, events)
            connection.commit()
            return replayed
        except (AccountNotFoundError, LedgerInvariantError):
            connection.rollback()
            raise
        except sqlite3.DatabaseError as exc:
            connection.rollback()
            raise RepositoryError("failed to replay strategy account") from exc
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _replay_snapshot(
        materialized: StrategyAccount,
        entries: List[LedgerEntry],
        events: List[StrategyEvent],
    ) -> StrategyAccount:
        if len(entries) != len(events):
            raise LedgerInvariantError("ledger entry and event counts differ")
        cash_units = materialized.initial_capital_units
        reserved_units = 0
        for expected_seq, (entry, event) in enumerate(zip(entries, events), start=1):
            if entry.event_seq != expected_seq or event.event_seq != expected_seq:
                raise LedgerInvariantError("event sequence is not consecutive")
            cash_units += entry.amount_units
            reserved_units = entry.reserved_after_units
            if cash_units != entry.cash_after_units:
                raise LedgerInvariantError("ledger cash replay does not match entry snapshot")
            if reserved_units > cash_units:
                raise LedgerInvariantError("ledger replay violates reserved cash invariant")
        replayed = StrategyAccount(
            account_id=materialized.account_id,
            strategy_id=materialized.strategy_id,
            physical_account_id=materialized.physical_account_id,
            initial_capital_units=materialized.initial_capital_units,
            cash_units=cash_units,
            reserved_cash_units=reserved_units,
            ledger_version=len(entries),
            event_seq=len(entries),
            status=materialized.status,
        )
        if replayed != materialized:
            raise LedgerInvariantError("replayed account does not match materialized account")
        return replayed

    @staticmethod
    def _read_ledger_entries(
        connection: sqlite3.Connection,
        account_id: str,
    ) -> List[LedgerEntry]:
        rows = connection.execute(
            """
            SELECT event_seq, entry_type, amount_units, cash_after_units,
                   reserved_after_units, reference_type, reference_id
            FROM ledger_entries
            WHERE strategy_account_id = ?
            ORDER BY event_seq
            """,
            (account_id,),
        ).fetchall()
        return [
            LedgerEntry(
                account_id=account_id,
                event_seq=row["event_seq"],
                entry_type=row["entry_type"],
                amount_units=row["amount_units"],
                cash_after_units=row["cash_after_units"],
                reserved_after_units=row["reserved_after_units"],
                reference_type=row["reference_type"],
                reference_id=row["reference_id"],
            )
            for row in rows
        ]

    @staticmethod
    def _read_events(
        connection: sqlite3.Connection,
        account_id: str,
    ) -> List[StrategyEvent]:
        rows = connection.execute(
            """
            SELECT event_seq, event_type, payload_json, created_at
            FROM strategy_events
            WHERE strategy_account_id = ?
            ORDER BY event_seq
            """,
            (account_id,),
        ).fetchall()
        return [
            StrategyEvent(
                account_id=account_id,
                event_seq=row["event_seq"],
                event_type=row["event_type"],
                payload=json.loads(row["payload_json"]),
                created_at=datetime.fromisoformat(row["created_at"]),
            )
            for row in rows
        ]

    @staticmethod
    def _select_account(
        connection: sqlite3.Connection,
        account_id: str,
    ) -> sqlite3.Row:
        row = connection.execute(
            """
            SELECT strategy_account_id, strategy_id, physical_account_id,
                   initial_capital_units, cash_units, reserved_cash_units,
                   ledger_version, event_seq, status
            FROM strategy_accounts
            WHERE strategy_account_id = ?
            """,
            (account_id,),
        ).fetchone()
        if row is None:
            raise AccountNotFoundError("strategy account not found")
        return cast(sqlite3.Row, row)
