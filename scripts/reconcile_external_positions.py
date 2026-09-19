"""One-time, audited removal of strategy positions absent from a QMT account.

This is an in-kind capital adjustment, not a sell.  It never creates an order,
fill, cash receipt, or realized P&L.  Run with the server stopped; the default
is a read-only preview.  ``--apply`` creates a SQLite backup before writing.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Optional, Sequence, Tuple
from uuid import uuid4

from bullet_trade.server.strategy.domain import SHANGHAI_TZ
from bullet_trade.server.strategy.repository import SQLiteStrategyRepository
from bullet_trade.server.strategy.schema import connect_database
from bullet_trade.server.strategy.valuation import (
    SQLiteValuationService,
    _round_div,
    _trade_value_units,
)
try:
    from scripts.strategy_ledger_backup import backup_database
except ModuleNotFoundError:
    from strategy_ledger_backup import backup_database


@dataclass(frozen=True)
class RemovedPosition:
    security: str
    quantity: int
    cost_units: int
    lot_ids: Tuple[str, ...]


@dataclass(frozen=True)
class AdjustmentPlan:
    account_id: str
    physical_account_id: str
    ledger_version: int
    event_seq: int
    cash_units: int
    net_capital_units: int
    reconciliation_id: str
    broker_as_of: str
    positions: Tuple[RemovedPosition, ...]

    @property
    def cost_units(self) -> int:
        return sum(item.cost_units for item in self.positions)


def _parse_positions(values: Sequence[str]) -> Dict[str, int]:
    result = {}
    for value in values:
        security, separator, quantity = value.partition("=")
        if not separator or not security or not quantity.isdecimal():
            raise ValueError("position must be SECURITY=positive_quantity")
        if security in result or int(quantity) <= 0:
            raise ValueError(
                "duplicate or zero-quantity position: {}".format(security)
            )
        result[security] = int(quantity)
    if not result:
        raise ValueError("at least one position is required")
    return result


def _open_readonly(database: Path) -> sqlite3.Connection:
    uri = "file:{}?mode=ro".format(database.resolve().as_posix())
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def build_plan(
    connection: sqlite3.Connection,
    strategy_id: str,
    expected: Dict[str, int],
    reference: str,
    *,
    max_snapshot_age: timedelta = timedelta(minutes=30),
) -> AdjustmentPlan:
    if not strategy_id or not reference:
        raise ValueError("strategy ID and unique reference are required")
    if connection.execute(
        "SELECT 1 FROM capital_flows WHERE external_ref = ?", (reference,)
    ).fetchone():
        raise RuntimeError(
            "adjustment reference already exists; do not apply twice"
        )
    account = connection.execute(
        "SELECT * FROM strategy_accounts WHERE strategy_id = ?", (strategy_id,)
    ).fetchone()
    if account is None:
        raise RuntimeError("strategy account not found")
    if account["status"] != "RECONCILIATION_BLOCKED":
        raise RuntimeError("strategy account must be reconciliation-blocked")
    account_id = account["strategy_account_id"]
    active_orders = connection.execute(
        """SELECT COUNT(*) FROM strategy_orders WHERE strategy_account_id = ?
           AND state IN (
               'PENDING_SUBMIT','SUBMIT_UNKNOWN',
               'SUBMITTED','PARTIALLY_FILLED'
           )""",
        (account_id,),
    ).fetchone()[0]
    active_intents = connection.execute(
        """SELECT COUNT(*) FROM portfolio_intents WHERE strategy_account_id = ?
           AND state IN ('CREATED','PLANNED','EXECUTING','RECONCILING')""",
        (account_id,),
    ).fetchone()[0]
    if active_orders or active_intents or account["reserved_cash_units"]:
        raise RuntimeError("strategy has active execution or reserved cash")
    positions = connection.execute(
        """SELECT security, total_qty FROM positions
           WHERE strategy_account_id = ? AND total_qty > 0""",
        (account_id,),
    ).fetchall()
    actual = {row["security"]: row["total_qty"] for row in positions}
    if actual != expected:
        raise RuntimeError(
            "strategy positions changed; expected {} found {}".format(
                expected, actual
            )
        )
    latest = connection.execute(
        """SELECT * FROM reconciliation_runs
           WHERE strategy_account_id = ? ORDER BY started_at DESC LIMIT 1""",
        (account_id,),
    ).fetchone()
    if latest is None or latest["state"] != "BLOCKED":
        raise RuntimeError("a fresh BLOCKED reconciliation is required")
    as_of = datetime.fromisoformat(latest["broker_as_of"])
    now = datetime.now(SHANGHAI_TZ)
    if (
        as_of.tzinfo is None
        or as_of > now + timedelta(minutes=1)
        or now - as_of > max_snapshot_age
    ):
        raise RuntimeError("QMT reconciliation snapshot is stale")
    blockers = json.loads(latest["details_json"]).get("blockers", [])
    wanted = {
        "broker_position_insufficient:{}:strategy=({},{}):broker=(0,0)".format(
            security, quantity, quantity
        )
        for security, quantity in expected.items()
    }
    if set(blockers) != wanted:
        raise RuntimeError(
            "latest reconciliation blockers differ from requested positions"
        )
    removed = []
    for security, quantity in sorted(expected.items()):
        lots = connection.execute(
            """SELECT l.lot_id, l.original_qty, l.remaining_qty,
                      l.cost_price_units, f.price_units, f.commission_units,
                      f.tax_units
               FROM position_lots l
               JOIN fills f ON f.fill_id = l.source_fill_id
               WHERE l.strategy_account_id = ? AND l.security = ?
                 AND l.remaining_qty > 0 ORDER BY l.lot_id""",
            (account_id, security),
        ).fetchall()
        if sum(lot["remaining_qty"] for lot in lots) != quantity:
            raise RuntimeError(
                "position lots do not match: {}".format(security)
            )
        cost = 0
        for lot in lots:
            if lot["price_units"] == 0:
                cost += _trade_value_units(
                    lot["cost_price_units"], lot["remaining_qty"]
                )
            else:
                original_cost = (
                    _trade_value_units(lot["price_units"], lot["original_qty"])
                    + lot["commission_units"] + lot["tax_units"]
                )
                cost += _round_div(
                    original_cost * lot["remaining_qty"], lot["original_qty"]
                )
        if cost <= 0:
            raise RuntimeError(
                "position cost is unavailable: {}".format(security)
            )
        removed.append(RemovedPosition(
            security, quantity, cost, tuple(lot["lot_id"] for lot in lots)
        ))
    flows = connection.execute(
        """SELECT flow_type, amount_units FROM capital_flows
           WHERE strategy_account_id = ?""",
        (account_id,),
    ).fetchall()
    net_capital = sum(
        row["amount_units"] if row["flow_type"] == "ALLOCATE"
        else -row["amount_units"] for row in flows
    )
    total_cost = sum(item.cost_units for item in removed)
    if net_capital - total_cost <= 0:
        raise RuntimeError(
            "in-kind adjustment would exhaust strategy net capital"
        )
    realized = SQLiteValuationService._realized_pnl(connection, account_id)
    if account["cash_units"] - (net_capital - total_cost) != realized:
        raise RuntimeError("book-cost adjustment would violate P&L invariant")
    return AdjustmentPlan(
        account_id, account["physical_account_id"], account["ledger_version"],
        account["event_seq"], account["cash_units"], net_capital,
        latest["reconciliation_id"], latest["broker_as_of"], tuple(removed),
    )


def apply_adjustment(
    database: Path,
    strategy_id: str,
    expected: Dict[str, int],
    reference: str,
    reason: str,
    backup_dir: Path,
) -> Tuple[Path, AdjustmentPlan]:
    if not reason.strip():
        raise ValueError("an explicit audit reason is required")
    preview = _open_readonly(database)
    try:
        build_plan(preview, strategy_id, expected, reference)
    finally:
        preview.close()
    backup = backup_database(database, backup_dir)
    connection = connect_database(database)
    try:
        connection.execute("BEGIN IMMEDIATE")
        plan = build_plan(connection, strategy_id, expected, reference)
        timestamp = datetime.now(SHANGHAI_TZ).isoformat()
        for position in plan.positions:
            for lot_id in position.lot_ids:
                updated = connection.execute(
                    """UPDATE position_lots SET remaining_qty = 0
                       WHERE lot_id = ? AND strategy_account_id = ?
                         AND security = ? AND remaining_qty > 0""",
                    (lot_id, plan.account_id, position.security),
                )
                if updated.rowcount != 1:
                    raise RuntimeError("position lot changed while adjusting")
            updated = connection.execute(
                """UPDATE positions SET total_qty = 0, sellable_qty = 0,
                      avg_cost_price_units = 0, version = version + 1,
                      updated_at = ? WHERE strategy_account_id = ?
                      AND security = ? AND total_qty = ?""",
                (
                    timestamp, plan.account_id, position.security,
                    position.quantity,
                ),
            )
            if updated.rowcount != 1:
                raise RuntimeError("position changed while adjusting")
        payload = json.dumps({
            "reference": reference,
            "reason": reason,
            "accounting": "in_kind_withdrawal_at_book_cost",
            "broker_reconciliation_id": plan.reconciliation_id,
            "broker_as_of": plan.broker_as_of,
            "broker_sell_evidence": "unavailable",
            "cash_delta_units": 0,
            "realized_pnl_change_units": 0,
            "cost_units": plan.cost_units,
            "positions": [
                {"security": item.security, "quantity": item.quantity,
                 "book_cost_units": item.cost_units, "lot_ids": item.lot_ids}
                for item in plan.positions
            ],
        }, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        updated = connection.execute(
            """UPDATE strategy_accounts
               SET ledger_version = ledger_version + 1,
               event_seq = event_seq + 1, updated_at = ?
               WHERE strategy_account_id = ? AND ledger_version = ?
               AND event_seq = ?
               AND cash_units = ?""",
            (timestamp, plan.account_id, plan.ledger_version, plan.event_seq,
             plan.cash_units),
        )
        if updated.rowcount != 1:
            raise RuntimeError("strategy account changed while adjusting")
        sequence = plan.event_seq + 1
        connection.execute(
            """INSERT INTO capital_flows(capital_flow_id, strategy_account_id,
               flow_type, amount_units, external_ref, reason, created_at)
               VALUES (?, ?, 'ADJUSTMENT', ?, ?, ?, ?)""",
            (str(uuid4()), plan.account_id, plan.cost_units, reference, reason,
             timestamp),
        )
        connection.execute(
            """INSERT INTO ledger_entries(
               strategy_account_id, event_seq, entry_type, amount_units,
               cash_after_units, reserved_after_units,
               reference_type, reference_id, payload_json, created_at)
               VALUES (?, ?, 'EXTERNAL_POSITION_ADJUSTMENT', 0, ?, 0,
               'capital_flow', ?, ?, ?)""",
            (plan.account_id, sequence, plan.cash_units, reference, payload,
             timestamp),
        )
        connection.execute(
            """INSERT INTO strategy_events(strategy_account_id, event_seq,
               event_type, payload_json, created_at)
               VALUES (?, ?, 'EXTERNAL_POSITION_ADJUSTED', ?, ?)""",
            (plan.account_id, sequence, payload, timestamp),
        )
        # With all strategy positions removed, no market mark is needed.  The
        # book-cost withdrawal must preserve the pre-existing realized P&L.
        realized = SQLiteValuationService._realized_pnl(
            connection, plan.account_id
        )
        if (
            plan.cash_units - (plan.net_capital_units - plan.cost_units)
            != realized
        ):
            raise RuntimeError("post-adjustment P&L invariant failed")
        if connection.execute("PRAGMA foreign_key_check").fetchone():
            raise RuntimeError("foreign key check failed")
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()
    SQLiteStrategyRepository(database).replay_account(plan.account_id)
    SQLiteValuationService(database).create_snapshot(
        plan.account_id, {}, datetime.now(SHANGHAI_TZ), timedelta(minutes=1)
    )
    return backup, plan


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--strategy-id", required=True)
    parser.add_argument("--position", action="append", required=True)
    parser.add_argument("--reference", required=True)
    parser.add_argument("--reason", default="")
    parser.add_argument("--backup-dir", type=Path)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    expected = _parse_positions(args.position)
    if args.apply:
        if args.backup_dir is None:
            parser.error("--apply requires --backup-dir")
        backup, plan = apply_adjustment(
            args.database, args.strategy_id, expected, args.reference,
            args.reason, args.backup_dir,
        )
        print("APPLIED backup={} reference={} positions={}".format(
            backup, args.reference, len(plan.positions)
        ))
    else:
        connection = _open_readonly(args.database)
        try:
            plan = build_plan(
                connection, args.strategy_id, expected, args.reference
            )
        finally:
            connection.close()
        print("PREVIEW reference={} positions={} book_cost_units={} "
              "cash_delta_units=0 realized_pnl_units=0 broker_as_of={}".format(
                  args.reference, len(plan.positions), plan.cost_units,
                  plan.broker_as_of,
              ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
