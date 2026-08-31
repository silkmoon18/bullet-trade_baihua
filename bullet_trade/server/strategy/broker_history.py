"""Small durable cache for broker order and trade observations."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, Mapping, Optional, Tuple, Union, cast

from .domain import SHANGHAI_TZ, as_shanghai_time
from .schema import connect_database, migrate_database


DatabasePath = Union[str, Path]


class BrokerHistoryError(RuntimeError):
    """Raised when a broker observation cannot be persisted or read."""


@dataclass(frozen=True)
class BrokerHistorySnapshot:
    orders: Tuple[Mapping[str, object], ...]
    trades: Tuple[Mapping[str, object], ...]


def _json_default(value: object) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    enum_value = getattr(value, "value", None)
    if enum_value is not None:
        return str(enum_value)
    return str(value)


def _payload_json(payload: Mapping[str, object]) -> str:
    return json.dumps(
        dict(payload),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=_json_default,
    )


def _merge_payload(
    previous: Mapping[str, object], current: Mapping[str, object]
) -> Mapping[str, object]:
    """Keep later state while retaining useful fields absent from a callback."""

    merged = dict(previous)
    for key, value in current.items():
        if (
            key in ("order_id", "trade_id", "order_sysid")
            and str(value or "").strip() in ("", "0")
            and str(previous.get(key) or "").strip() not in ("", "0")
        ):
            continue
        if key in ("price", "traded_price", "deal_balance"):
            try:
                previous_number = float(previous.get(key) or 0.0)
            except (TypeError, ValueError):
                previous_number = 0.0
            try:
                current_number = float(value) if value not in (None, "") else 0.0
            except (TypeError, ValueError):
                current_number = 0.0
            if previous_number > 0 and current_number <= 0:
                continue
        if value not in (None, "") or key not in merged:
            merged[key] = value
    for known_key, value_keys in (
        ("commission_known", ("commission", "commission_fee")),
        ("tax_known", ("tax", "stamp_tax")),
    ):
        if previous.get(known_key) is True and current.get(known_key) is not True:
            merged[known_key] = True
            for key in value_keys:
                if key in previous:
                    merged[key] = previous[key]
    return merged


class SQLiteBrokerHistoryStore:
    """Persist every normalized QMT observation that reached this server."""

    def __init__(self, database_path: DatabasePath) -> None:
        self.database_path = Path(database_path)
        migrate_database(self.database_path)

    def record_order(
        self,
        account_key: str,
        order: Mapping[str, object],
        observed_at: Optional[datetime] = None,
    ) -> bool:
        return self._record(
            "broker_order_history",
            "broker_order_id",
            "order_id",
            account_key,
            order,
            observed_at,
        )

    def record_trade(
        self,
        account_key: str,
        trade: Mapping[str, object],
        observed_at: Optional[datetime] = None,
    ) -> bool:
        return self._record(
            "broker_trade_history",
            "broker_trade_id",
            "trade_id",
            account_key,
            trade,
            observed_at,
        )

    def record_orders(
        self, account_key: str, orders: Iterable[Mapping[str, object]]
    ) -> int:
        return sum(self.record_order(account_key, row) for row in orders)

    def record_trades(
        self, account_key: str, trades: Iterable[Mapping[str, object]]
    ) -> int:
        return sum(self.record_trade(account_key, row) for row in trades)

    def snapshot(self, account_key: str) -> BrokerHistorySnapshot:
        return BrokerHistorySnapshot(
            orders=self.list_orders(account_key),
            trades=self.list_trades(account_key),
        )

    def list_orders(
        self,
        account_key: str,
        *,
        order_id: Optional[str] = None,
        security: Optional[str] = None,
        status: Optional[object] = None,
    ) -> Tuple[Mapping[str, object], ...]:
        rows = self._read("broker_order_history", account_key)
        expected_status = getattr(status, "value", status)
        return tuple(
            row
            for row in rows
            if (order_id is None or str(row.get("order_id")) == str(order_id))
            and (security is None or row.get("security") == security)
            and (
                status is None
                or str(getattr(row.get("status"), "value", row.get("status")))
                == str(expected_status)
            )
        )

    def list_trades(
        self,
        account_key: str,
        *,
        order_id: Optional[str] = None,
        security: Optional[str] = None,
    ) -> Tuple[Mapping[str, object], ...]:
        rows = self._read("broker_trade_history", account_key)
        return tuple(
            row
            for row in rows
            if (order_id is None or str(row.get("order_id")) == str(order_id))
            and (security is None or row.get("security") == security)
        )

    def _record(
        self,
        table: str,
        id_column: str,
        payload_id_key: str,
        account_key: str,
        payload: Mapping[str, object],
        observed_at: Optional[datetime],
    ) -> bool:
        account = str(account_key).strip()
        broker_id = str(payload.get(payload_id_key) or "").strip()
        if not account or not broker_id:
            return False
        timestamp = as_shanghai_time(
            observed_at or datetime.now(SHANGHAI_TZ)
        ).isoformat()
        connection = connect_database(self.database_path)
        try:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT payload_json FROM {} WHERE account_key = ? AND {} = ?".format(
                    table, id_column
                ),
                (account, broker_id),
            ).fetchone()
            merged = dict(payload)
            if existing is not None:
                previous = json.loads(cast(str, existing["payload_json"]))
                merged = dict(_merge_payload(previous, payload))
            connection.execute(
                """
                INSERT INTO {table}(
                    account_key, {id_column}, payload_json,
                    first_seen_at, last_seen_at
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(account_key, {id_column}) DO UPDATE SET
                    payload_json = excluded.payload_json,
                    last_seen_at = excluded.last_seen_at
                """.format(table=table, id_column=id_column),
                (account, broker_id, _payload_json(merged), timestamp, timestamp),
            )
            connection.commit()
            return True
        except (sqlite3.DatabaseError, TypeError, ValueError) as exc:
            connection.rollback()
            raise BrokerHistoryError("failed to persist broker history") from exc
        finally:
            connection.close()

    def _read(
        self, table: str, account_key: str
    ) -> Tuple[Mapping[str, object], ...]:
        connection = connect_database(self.database_path)
        try:
            rows = connection.execute(
                "SELECT payload_json FROM {} WHERE account_key = ? ORDER BY first_seen_at".format(
                    table
                ),
                (account_key,),
            ).fetchall()
            return tuple(json.loads(cast(str, row["payload_json"])) for row in rows)
        except (sqlite3.DatabaseError, TypeError, ValueError) as exc:
            raise BrokerHistoryError("failed to read broker history") from exc
        finally:
            connection.close()


def merge_broker_rows(
    current: Iterable[Mapping[str, object]],
    history: Iterable[Mapping[str, object]],
    id_key: str,
) -> list[Mapping[str, object]]:
    """Return durable history with the newest current observation winning."""

    merged = {}
    for row in history:
        broker_id = str(row.get(id_key) or "").strip()
        if not broker_id:
            continue
        historical = dict(row)
        historical["_broker_history_only"] = True
        merged[broker_id] = historical
    for row in current:
        broker_id = str(row.get(id_key) or "").strip()
        if broker_id:
            merged[broker_id] = dict(_merge_payload(merged.get(broker_id, {}), row))
            merged[broker_id]["_broker_history_only"] = False
    return list(merged.values())
