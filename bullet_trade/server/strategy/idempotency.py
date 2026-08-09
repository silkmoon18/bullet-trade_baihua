"""Persistent request idempotency and a small SQLite outbox."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Mapping, Optional, Union, cast
from uuid import uuid4

from .domain import SHANGHAI_TZ
from .repository import AccountNotFoundError, RepositoryError
from .schema import connect_database


DatabasePath = Union[str, Path]


class IdempotencyConflictError(RepositoryError):
    pass


class OperationState(str, Enum):
    PENDING = "PENDING"
    SUBMITTING = "SUBMITTING"
    SUBMIT_UNKNOWN = "SUBMIT_UNKNOWN"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


@dataclass(frozen=True)
class OperationRecord:
    operation_id: str
    strategy_account_id: str
    strategy_id: str
    endpoint: str
    idempotency_key: str
    payload_hash: str
    state: OperationState
    client_tag: str
    response_json: Optional[str]


@dataclass(frozen=True)
class OperationCreateResult:
    operation: OperationRecord
    replayed: bool


@dataclass(frozen=True)
class OutboxClaim:
    outbox_id: int
    operation_id: str
    topic: str
    payload_json: str
    attempt_count: int
    lease_owner: str
    lease_until: datetime


def _now() -> datetime:
    return datetime.now(SHANGHAI_TZ)


def _now_text() -> str:
    return _now().isoformat()


def _json_value(value: object) -> object:
    if value is None or type(value) in (bool, int, float, str):
        return value
    if isinstance(value, Mapping):
        result = {}
        for key, item in value.items():
            if type(key) is not str:
                raise TypeError("request payload keys must be strings")
            result[key] = _json_value(item)
        return result
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    raise TypeError("request payload must be JSON-compatible")


def _json_text(value: object) -> str:
    return json.dumps(
        _json_value(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _payload_hash(payload_text: str) -> str:
    return hashlib.sha256(payload_text.encode("utf-8")).hexdigest()


def _client_tag(strategy_id: str, operation_id: str) -> str:
    strategy_part = hashlib.sha256(strategy_id.encode("utf-8")).hexdigest()[:6]
    operation_part = operation_id.replace("-", "")[:12]
    return "bt:{}:{}".format(strategy_part, operation_part)


def _operation_from_row(row: sqlite3.Row) -> OperationRecord:
    return OperationRecord(
        operation_id=row["operation_id"],
        strategy_account_id=row["strategy_account_id"],
        strategy_id=row["strategy_id"],
        endpoint=row["endpoint"],
        idempotency_key=row["idempotency_key"],
        payload_hash=row["payload_hash"],
        state=OperationState(row["state"]),
        client_tag=row["client_tag"],
        response_json=row["response_json"],
    )


class SQLiteOperationRepository:
    """Own idempotent operations and their one-to-one submission outbox rows."""

    def __init__(self, database_path: DatabasePath):
        self.database_path = Path(database_path)

    def create_operation(
        self,
        strategy_account_id: str,
        endpoint: str,
        idempotency_key: str,
        payload: Mapping[str, object],
        topic: str = "broker.submit",
    ) -> OperationCreateResult:
        if not endpoint or not idempotency_key or not topic:
            raise ValueError("endpoint, idempotency_key and topic cannot be empty")
        try:
            request_json = _json_text(payload)
        except TypeError as exc:
            raise ValueError(str(exc)) from exc
        payload_hash = _payload_hash(request_json)
        connection = connect_database(self.database_path)
        try:
            connection.execute("BEGIN IMMEDIATE")
            account = connection.execute(
                """
                SELECT strategy_id FROM strategy_accounts
                WHERE strategy_account_id = ?
                """,
                (strategy_account_id,),
            ).fetchone()
            if account is None:
                raise AccountNotFoundError("strategy account not found")
            strategy_id = str(account["strategy_id"])
            existing = connection.execute(
                """
                SELECT operation_id, strategy_account_id, strategy_id, endpoint,
                       idempotency_key, payload_hash, state, client_tag, response_json
                FROM strategy_operations
                WHERE strategy_id = ? AND endpoint = ? AND idempotency_key = ?
                """,
                (strategy_id, endpoint, idempotency_key),
            ).fetchone()
            if existing is not None:
                operation = _operation_from_row(cast(sqlite3.Row, existing))
                if operation.payload_hash != payload_hash:
                    raise IdempotencyConflictError(
                        "idempotency key was already used with a different payload"
                    )
                connection.commit()
                return OperationCreateResult(operation=operation, replayed=True)

            operation_id = str(uuid4())
            client_tag = _client_tag(strategy_id, operation_id)
            timestamp = _now_text()
            connection.execute(
                """
                INSERT INTO strategy_operations(
                    operation_id, strategy_account_id, strategy_id, endpoint,
                    idempotency_key, payload_hash, request_json, state,
                    client_tag, response_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'PENDING', ?, NULL, ?, ?)
                """,
                (
                    operation_id,
                    strategy_account_id,
                    strategy_id,
                    endpoint,
                    idempotency_key,
                    payload_hash,
                    request_json,
                    client_tag,
                    timestamp,
                    timestamp,
                ),
            )
            outbox_payload = _json_text(
                {
                    "operation_id": operation_id,
                    "client_tag": client_tag,
                    "endpoint": endpoint,
                    "payload": json.loads(request_json),
                }
            )
            connection.execute(
                """
                INSERT INTO outbox(
                    strategy_account_id, topic, aggregate_id, payload_json,
                    state, attempt_count, available_at, lease_owner,
                    lease_until, created_at, updated_at, operation_id
                ) VALUES (?, ?, ?, ?, 'PENDING', 0, ?, NULL, NULL, ?, ?, ?)
                """,
                (
                    strategy_account_id,
                    topic,
                    operation_id,
                    outbox_payload,
                    timestamp,
                    timestamp,
                    timestamp,
                    operation_id,
                ),
            )
            row = self._select_operation(connection, operation_id)
            connection.commit()
            return OperationCreateResult(
                operation=_operation_from_row(row),
                replayed=False,
            )
        except (AccountNotFoundError, IdempotencyConflictError):
            connection.rollback()
            raise
        except sqlite3.DatabaseError as exc:
            connection.rollback()
            raise RepositoryError("failed to create idempotent operation") from exc
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def get_operation(self, operation_id: str) -> OperationRecord:
        connection = connect_database(self.database_path)
        try:
            return _operation_from_row(self._select_operation(connection, operation_id))
        except sqlite3.DatabaseError as exc:
            raise RepositoryError("failed to read operation") from exc
        finally:
            connection.close()

    def claim_next(self, worker_id: str, lease_seconds: int = 30) -> Optional[OutboxClaim]:
        if not worker_id or type(lease_seconds) is not int or lease_seconds <= 0:
            raise ValueError("worker_id and a positive lease_seconds are required")
        connection = connect_database(self.database_path)
        try:
            connection.execute("BEGIN IMMEDIATE")
            now = _now()
            now_text = now.isoformat()
            row = connection.execute(
                """
                SELECT o.outbox_id
                FROM outbox AS o
                JOIN strategy_operations AS op ON op.operation_id = o.operation_id
                WHERE op.state = 'PENDING'
                  AND (
                    (o.state = 'PENDING' AND o.available_at <= ?)
                    OR (o.state = 'CLAIMED' AND o.lease_until <= ?)
                  )
                ORDER BY o.outbox_id
                LIMIT 1
                """,
                (now_text, now_text),
            ).fetchone()
            if row is None:
                connection.commit()
                return None
            outbox_id = int(row["outbox_id"])
            lease_until = now + timedelta(seconds=lease_seconds)
            connection.execute(
                """
                UPDATE outbox
                SET state = 'CLAIMED', attempt_count = attempt_count + 1,
                    lease_owner = ?, lease_until = ?, updated_at = ?
                WHERE outbox_id = ?
                """,
                (worker_id, lease_until.isoformat(), now_text, outbox_id),
            )
            claim_row = connection.execute(
                """
                SELECT outbox_id, operation_id, topic, payload_json,
                       attempt_count, lease_owner, lease_until
                FROM outbox WHERE outbox_id = ?
                """,
                (outbox_id,),
            ).fetchone()
            connection.commit()
            claim_row = cast(sqlite3.Row, claim_row)
            return OutboxClaim(
                outbox_id=claim_row["outbox_id"],
                operation_id=claim_row["operation_id"],
                topic=claim_row["topic"],
                payload_json=claim_row["payload_json"],
                attempt_count=claim_row["attempt_count"],
                lease_owner=claim_row["lease_owner"],
                lease_until=datetime.fromisoformat(claim_row["lease_until"]),
            )
        except sqlite3.DatabaseError as exc:
            connection.rollback()
            raise RepositoryError("failed to claim outbox message") from exc
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def begin_submission(self, outbox_id: int, worker_id: str) -> OperationRecord:
        connection = connect_database(self.database_path)
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = self._select_claimed_operation(
                connection,
                outbox_id,
                worker_id,
                require_active_lease=True,
            )
            cursor = connection.execute(
                """
                UPDATE strategy_operations
                SET state = 'SUBMITTING', updated_at = ?
                WHERE operation_id = ? AND state = 'PENDING'
                """,
                (_now_text(), row["operation_id"]),
            )
            if cursor.rowcount != 1:
                raise RepositoryError("operation is not pending submission")
            operation = self._select_operation(connection, row["operation_id"])
            connection.commit()
            return _operation_from_row(operation)
        except RepositoryError:
            connection.rollback()
            raise
        except sqlite3.DatabaseError as exc:
            connection.rollback()
            raise RepositoryError("failed to begin broker submission") from exc
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def finish_submission(
        self,
        outbox_id: int,
        worker_id: str,
        response: Mapping[str, object],
        unknown: bool = False,
    ) -> OperationRecord:
        response_json = _json_text(response)
        operation_state = (
            OperationState.SUBMIT_UNKNOWN if unknown else OperationState.COMPLETED
        )
        outbox_state = "FAILED" if unknown else "DONE"
        connection = connect_database(self.database_path)
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = self._select_claimed_operation(connection, outbox_id, worker_id)
            timestamp = _now_text()
            cursor = connection.execute(
                """
                UPDATE strategy_operations
                SET state = ?, response_json = ?, updated_at = ?
                WHERE operation_id = ? AND state = 'SUBMITTING'
                """,
                (
                    operation_state.value,
                    response_json,
                    timestamp,
                    row["operation_id"],
                ),
            )
            if cursor.rowcount != 1:
                raise RepositoryError("operation is not in submission")
            connection.execute(
                """
                UPDATE outbox
                SET state = ?, lease_owner = NULL, lease_until = NULL, updated_at = ?
                WHERE outbox_id = ?
                """,
                (outbox_state, timestamp, outbox_id),
            )
            operation = self._select_operation(connection, row["operation_id"])
            connection.commit()
            return _operation_from_row(operation)
        except RepositoryError:
            connection.rollback()
            raise
        except sqlite3.DatabaseError as exc:
            connection.rollback()
            raise RepositoryError("failed to finish broker submission") from exc
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def quarantine_inflight(self) -> int:
        connection = connect_database(self.database_path)
        try:
            connection.execute("BEGIN IMMEDIATE")
            timestamp = _now_text()
            operation_ids = [
                row["operation_id"]
                for row in connection.execute(
                    """
                    SELECT operation_id FROM strategy_operations
                    WHERE state = 'SUBMITTING'
                    """
                ).fetchall()
            ]
            for operation_id in operation_ids:
                connection.execute(
                    """
                    UPDATE outbox
                    SET state = 'FAILED', lease_owner = NULL, lease_until = NULL,
                        updated_at = ?
                    WHERE operation_id = ? AND state != 'DONE'
                    """,
                    (timestamp, operation_id),
                )
                connection.execute(
                    """
                    UPDATE strategy_operations
                    SET state = 'SUBMIT_UNKNOWN', updated_at = ?
                    WHERE operation_id = ? AND state = 'SUBMITTING'
                    """,
                    (timestamp, operation_id),
                )
            connection.commit()
            return len(operation_ids)
        except sqlite3.DatabaseError as exc:
            connection.rollback()
            raise RepositoryError("failed to quarantine inflight submissions") from exc
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _select_operation(
        connection: sqlite3.Connection,
        operation_id: str,
    ) -> sqlite3.Row:
        row = connection.execute(
            """
            SELECT operation_id, strategy_account_id, strategy_id, endpoint,
                   idempotency_key, payload_hash, state, client_tag, response_json
            FROM strategy_operations WHERE operation_id = ?
            """,
            (operation_id,),
        ).fetchone()
        if row is None:
            raise RepositoryError("operation not found")
        return cast(sqlite3.Row, row)

    @staticmethod
    def _select_claimed_operation(
        connection: sqlite3.Connection,
        outbox_id: int,
        worker_id: str,
        require_active_lease: bool = False,
    ) -> sqlite3.Row:
        if require_active_lease:
            row = connection.execute(
                """
                SELECT operation_id FROM outbox
                WHERE outbox_id = ? AND state = 'CLAIMED' AND lease_owner = ?
                  AND lease_until > ?
                """,
                (outbox_id, worker_id, _now_text()),
            ).fetchone()
        else:
            row = connection.execute(
                """
                SELECT operation_id FROM outbox
                WHERE outbox_id = ? AND state = 'CLAIMED' AND lease_owner = ?
                """,
                (outbox_id, worker_id),
            ).fetchone()
        if row is None:
            raise RepositoryError("outbox message is not claimed by this worker")
        return cast(sqlite3.Row, row)
