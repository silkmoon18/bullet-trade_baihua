"""Personal-account target planner and single-process order dispatcher."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Awaitable, Callable, Mapping, Optional, Tuple, Union, cast
from uuid import uuid4

from .capital import SQLiteCapitalService
from .domain import (
    MONEY_SCALE,
    NAV_SCALE,
    PRICE_SCALE,
    SHANGHAI_TZ,
    AccountStatus,
    BrokerOrder,
    IntentState,
    OrderSide,
    OrderState,
    PortfolioIntent,
)
from .fill_booking import SQLiteFillBookingService
from .idempotency import SQLiteOperationRepository
from .repository import RepositoryError, SQLiteStrategyRepository
from .schema import connect_database
from .valuation import MarketMark, PortfolioSnapshot


DatabasePath = Union[str, Path]
Weight = Union[str, int, float, Decimal]


class TargetPlanningError(RepositoryError):
    pass


@dataclass(frozen=True)
class PlannerConfig:
    lot_size: int = 100
    cash_buffer_units: int = 1_000_000
    minimum_order_units: int = 0
    buy_fee_buffer_units: int = 50_000
    limit_price_offset_ppm: int = 2_000
    max_age: timedelta = timedelta(minutes=5)
    working_order_timeout: timedelta = timedelta(minutes=10)
    order_wait_timeout_seconds: float = 16.0
    trading_enabled: bool = False
    allow_buys: bool = True

    def __post_init__(self) -> None:
        if type(self.lot_size) is not int or self.lot_size <= 0:
            raise ValueError("lot_size must be positive")
        for name in ("cash_buffer_units", "minimum_order_units", "buy_fee_buffer_units"):
            if type(getattr(self, name)) is not int or getattr(self, name) < 0:
                raise ValueError("{} must be non-negative".format(name))
        if (
            type(self.limit_price_offset_ppm) is not int
            or not 0 <= self.limit_price_offset_ppm <= 50_000
        ):
            raise ValueError("limit_price_offset_ppm must be between 0 and 50000")
        if self.max_age < timedelta(0):
            raise ValueError("max_age cannot be negative")
        if self.working_order_timeout <= timedelta(0):
            raise ValueError("working_order_timeout must be positive")
        if (
            type(self.order_wait_timeout_seconds) not in (int, float)
            or not 0 < self.order_wait_timeout_seconds <= 120
        ):
            raise ValueError("order_wait_timeout_seconds must be in (0, 120]")


@dataclass(frozen=True)
class PlannedOrder:
    order_id: str
    security: str
    side: OrderSide
    quantity: int
    limit_price_units: int


@dataclass(frozen=True)
class IntentAdvanceResult:
    intent: PortfolioIntent
    orders: Tuple[PlannedOrder, ...]
    waiting_for_fills: bool


@dataclass(frozen=True)
class DispatchResult:
    order_id: str
    broker_order_id: Optional[str]
    unknown: bool
    error: str = ""


def _trade_value(price_units: int, quantity: int) -> int:
    return (price_units * quantity * MONEY_SCALE + PRICE_SCALE // 2) // PRICE_SCALE


def _weight_units(value: Weight) -> int:
    if isinstance(value, bool):
        raise TargetPlanningError("weight cannot be bool")
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise TargetPlanningError("invalid target weight") from exc
    if not number.is_finite() or not 0 <= number <= 1:
        raise TargetPlanningError("target weight must be between 0 and 1")
    return int((number * NAV_SCALE).quantize(Decimal("1")))


def _intent(row: sqlite3.Row) -> PortfolioIntent:
    payload = json.loads(row["targets_json"])
    targets = payload.get("target_quantities", payload)
    return PortfolioIntent(
        intent_id=row["intent_id"],
        account_id=row["strategy_account_id"],
        idempotency_key=row["idempotency_key"],
        expected_ledger_version=row["expected_ledger_version"],
        targets={key: int(value) for key, value in targets.items()},
        state=IntentState(row["state"]),
    )


class SQLiteTargetExecutionService:
    def __init__(self, database_path: DatabasePath, config: PlannerConfig, notification_handler=None):
        self.database_path = Path(database_path)
        self.config = config
        self._ledger = SQLiteStrategyRepository(database_path)
        self._capital = SQLiteCapitalService(database_path)
        self._operations = SQLiteOperationRepository(database_path)
        self._booking = SQLiteFillBookingService(database_path, notification_handler)

    def submit_target_weights(
        self,
        account_id: str,
        idempotency_key: str,
        weights: Mapping[str, Weight],
        snapshot: PortfolioSnapshot,
        marks: Mapping[str, MarketMark],
        now: Optional[datetime] = None,
    ) -> IntentAdvanceResult:
        normalized = {security: _weight_units(value) for security, value in weights.items()}
        if not normalized or any(not security for security in normalized):
            raise TargetPlanningError("target weights cannot be empty")
        if sum(normalized.values()) > NAV_SCALE:
            raise TargetPlanningError("target weights exceed 100%")
        existing = self._intent_by_key(account_id, idempotency_key)
        if existing is not None:
            stored = self._intent_payload(existing.intent_id)["weights_ppm"]
            if stored != dict(sorted(normalized.items())):
                raise TargetPlanningError("idempotency key has different weights")
            return self.advance_intent(existing.intent_id, snapshot, marks, now)
        self._require_ready(account_id, snapshot, now or datetime.now(SHANGHAI_TZ))
        # Weights describe the whole portfolio. Enforce the cash buffer only
        # when buys are made; subtracting it here shrinks existing positions
        # and double-applies a strategy-level reserve such as DEPLOY_RATIO.
        investable = snapshot.total_assets_units
        quantities = {}
        for security, weight in normalized.items():
            mark = marks.get(security)
            if mark is None:
                raise TargetPlanningError("missing target mark: {}".format(security))
            raw = (investable * weight // NAV_SCALE) * PRICE_SCALE
            raw //= mark.price_units * MONEY_SCALE
            quantities[security] = raw // self.config.lot_size * self.config.lot_size
        intent = self._create_intent(
            account_id,
            idempotency_key,
            snapshot.ledger_version,
            normalized,
            quantities,
        )
        return self.advance_intent(intent.intent_id, snapshot, marks, now)

    def advance_intent(
        self,
        intent_id: str,
        snapshot: PortfolioSnapshot,
        marks: Mapping[str, MarketMark],
        now: Optional[datetime] = None,
    ) -> IntentAdvanceResult:
        intent = self.get_intent(intent_id)
        if intent.state in (IntentState.COMPLETED, IntentState.CANCELED, IntentState.FAILED):
            return IntentAdvanceResult(intent, (), False)
        working = self._working_orders(intent.account_id)
        if any(row["state"] == OrderState.SUBMIT_UNKNOWN.value for row in working):
            raise TargetPlanningError("submit-unknown order blocks execution")
        if working:
            return IntentAdvanceResult(intent, (), True)
        self._require_ready(intent.account_id, snapshot, now or datetime.now(SHANGHAI_TZ))

        positions = {item.security: item for item in snapshot.positions}
        sells = []
        buys = []
        sell_pending = False
        for security in sorted(set(intent.targets) | set(positions)):
            position = positions.get(security)
            current = position.total_qty if position else 0
            target = int(intent.targets.get(security, 0))
            delta = target - current
            if delta == 0:
                continue
            mark = marks.get(security)
            if mark is None:
                raise TargetPlanningError("missing execution mark: {}".format(security))
            if delta < 0:
                sell_pending = True
                desired = min(-delta, position.sellable_qty if position else 0)
                quantity = desired if target == 0 and desired == current else desired // self.config.lot_size * self.config.lot_size
                if quantity:
                    sells.append(
                        (
                            security,
                            quantity,
                            self._limit_price(mark.price_units, OrderSide.SELL),
                        )
                    )
            elif self.config.allow_buys:
                quantity = delta // self.config.lot_size * self.config.lot_size
                if quantity:
                    buys.append(
                        (
                            security,
                            quantity,
                            self._limit_price(mark.price_units, OrderSide.BUY),
                        )
                    )

        if sell_pending and not sells:
            return IntentAdvanceResult(
                self._set_state(intent_id, IntentState.EXECUTING), (), True
            )
        selected = sells or self._affordable_buys(snapshot, buys)
        if not selected:
            return IntentAdvanceResult(self._set_state(intent_id, IntentState.COMPLETED), (), False)
        side = OrderSide.SELL if sells else OrderSide.BUY
        orders = tuple(
            self._enqueue(intent, side, security, quantity, price, snapshot.as_of)
            for security, quantity, price in selected
        )
        return IntentAdvanceResult(
            self._set_state(intent_id, IntentState.EXECUTING),
            orders,
            True,
        )

    async def dispatch_next(
        self,
        submitter: Callable[[Mapping[str, object]], Awaitable[Mapping[str, object]]],
    ) -> Optional[DispatchResult]:
        claim = self._operations.claim_next()
        if claim is None:
            return None
        envelope = json.loads(claim.payload_json)
        payload = dict(envelope["payload"])
        payload.setdefault("order_remark", envelope["client_tag"])
        payload.setdefault("wait_timeout", self.config.order_wait_timeout_seconds)
        account_id = self._order_account(claim.operation_id)
        self._operations.begin_submission(claim.outbox_id)
        try:
            response = dict(await submitter(payload))
            broker_id = str(response.get("order_id") or response.get("broker_order_id") or "").strip()
            if not broker_id:
                raise RuntimeError("broker response has no order id")
            self._operations.finish_submission(claim.outbox_id, response)
            self._booking.mark_order_submitted(account_id, claim.operation_id, broker_id)
            return DispatchResult(claim.operation_id, broker_id, False)
        except BaseException as exc:
            error = "{}: {}".format(type(exc).__name__, str(exc))
            self._operations.finish_submission(claim.outbox_id, {"error": error}, unknown=True)
            self._booking.mark_order_submit_unknown(account_id, claim.operation_id, error)
            if not isinstance(exc, Exception):
                raise
            return DispatchResult(claim.operation_id, None, True, error)

    def get_intent(self, intent_id: str) -> PortfolioIntent:
        connection = connect_database(self.database_path)
        try:
            row = connection.execute("SELECT * FROM portfolio_intents WHERE intent_id = ?", (intent_id,)).fetchone()
            if row is None:
                raise TargetPlanningError("target intent not found")
            return _intent(cast(sqlite3.Row, row))
        finally:
            connection.close()

    def stale_broker_order_ids(
        self,
        account_id: str,
        now: Optional[datetime] = None,
    ) -> Tuple[str, ...]:
        current = now or datetime.now(SHANGHAI_TZ)
        result = []
        for row in self._working_orders(account_id):
            if row["state"] not in (
                OrderState.SUBMITTED.value,
                OrderState.PARTIALLY_FILLED.value,
            ) or not row["broker_order_id"]:
                continue
            submitted_at = row["submitted_at"] or row["updated_at"]
            if not submitted_at:
                continue
            timestamp = datetime.fromisoformat(submitted_at)
            if timestamp.tzinfo is None or timestamp.utcoffset() is None:
                timestamp = timestamp.replace(tzinfo=SHANGHAI_TZ)
            if current - timestamp >= self.config.working_order_timeout:
                result.append(str(row["broker_order_id"]))
        return tuple(result)

    def _create_intent(self, account_id, key, version, weights, quantities):
        if not key:
            raise TargetPlanningError("idempotency key cannot be empty")
        payload = {"weights_ppm": dict(sorted(weights.items())), "target_quantities": dict(sorted(quantities.items()))}
        text = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        connection = connect_database(self.database_path)
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM portfolio_intents WHERE strategy_account_id = ? AND idempotency_key = ?",
                (account_id, key),
            ).fetchone()
            if row is not None:
                if json.loads(row["targets_json"]).get("weights_ppm") != payload["weights_ppm"]:
                    raise TargetPlanningError("idempotency key has different weights")
                connection.commit()
                return _intent(cast(sqlite3.Row, row))
            active = connection.execute(
                """
                SELECT intent_id FROM portfolio_intents
                WHERE strategy_account_id = ?
                  AND state IN ('CREATED','PLANNED','EXECUTING','RECONCILING')
                LIMIT 1
                """,
                (account_id,),
            ).fetchone()
            if active is not None:
                raise TargetPlanningError("another target intent is still active")
            intent_id = str(uuid4())
            timestamp = datetime.now(SHANGHAI_TZ).isoformat()
            connection.execute(
                """
                INSERT INTO portfolio_intents(
                    intent_id, strategy_account_id, idempotency_key,
                    expected_ledger_version, state, targets_json,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, 'PLANNED', ?, ?, ?)
                """,
                (intent_id, account_id, key, version, text, timestamp, timestamp),
            )
            row = connection.execute("SELECT * FROM portfolio_intents WHERE intent_id = ?", (intent_id,)).fetchone()
            connection.commit()
            return _intent(cast(sqlite3.Row, row))
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _enqueue(self, intent, side, security, quantity, price, as_of):
        retry = self._order_count(intent.intent_id, security, side)
        key = "{}:{}:{}:{}".format(intent.intent_id, security, side.value, retry)
        payload = {
            "intent_id": intent.intent_id,
            "security": security,
            "side": side.value,
            "amount": quantity,
            "style": {"type": "limit", "price": str(Decimal(price) / PRICE_SCALE)},
        }
        operation = self._operations.create_operation(
            intent.account_id, "broker.place_order", key, payload, topic="strategy.order"
        ).operation
        if self._find_order(operation.operation_id) is None:
            if side is OrderSide.BUY:
                account = self._ledger.get_strategy_account(intent.account_id)
                self._capital.reserve_cash(
                    intent.account_id,
                    _trade_value(price, quantity) + self.config.buy_fee_buffer_units,
                    account.ledger_version,
                    operation.operation_id,
                )
            self._booking.register_order(
                BrokerOrder(
                    order_id=operation.operation_id,
                    account_id=intent.account_id,
                    intent_id=intent.intent_id,
                    client_tag=operation.client_tag,
                    security=security,
                    side=side,
                    requested_qty=quantity,
                    filled_qty=0,
                    state=OrderState.PENDING_SUBMIT,
                    trading_day=as_of.date(),
                    limit_price_units=price,
                )
            )
        return PlannedOrder(operation.operation_id, security, side, quantity, price)

    def _affordable_buys(self, snapshot, buys):
        available = max(0, snapshot.available_cash_units - self.config.cash_buffer_units)
        result = []
        for security, desired, price in buys:
            lot_value = _trade_value(price, self.config.lot_size)
            quantity = min(desired, max(0, available - self.config.buy_fee_buffer_units) // lot_value * self.config.lot_size)
            value = _trade_value(price, quantity)
            if quantity and value >= self.config.minimum_order_units:
                result.append((security, quantity, price))
                available -= value + self.config.buy_fee_buffer_units
        return result

    def _limit_price(self, mark_price_units, side):
        offset = self.config.limit_price_offset_ppm
        if side is OrderSide.BUY:
            return (mark_price_units * (NAV_SCALE + offset) + NAV_SCALE - 1) // NAV_SCALE
        return max(1, mark_price_units * (NAV_SCALE - offset) // NAV_SCALE)

    def _require_ready(self, account_id, snapshot, now):
        if not self.config.trading_enabled:
            raise TargetPlanningError("global trading switch is disabled")
        account = self._ledger.get_strategy_account(account_id)
        if account.status is not AccountStatus.ACTIVE or snapshot.account_id != account_id:
            raise TargetPlanningError("strategy account is not ready")
        if snapshot.ledger_version != account.ledger_version:
            raise TargetPlanningError("portfolio snapshot ledger version is stale")
        if snapshot.as_of > now + timedelta(seconds=5):
            raise TargetPlanningError("portfolio snapshot is from the future")
        if now - snapshot.as_of > self.config.max_age:
            raise TargetPlanningError("portfolio snapshot is stale")
        connection = connect_database(self.database_path)
        try:
            row = connection.execute(
                "SELECT state, broker_as_of FROM reconciliation_runs WHERE physical_account_id = ? ORDER BY started_at DESC LIMIT 1",
                (account.physical_account_id,),
            ).fetchone()
        finally:
            connection.close()
        if row is None or row["state"] != "READY":
            raise TargetPlanningError("latest reconciliation is not READY")
        if now - datetime.fromisoformat(row["broker_as_of"]) > self.config.max_age:
            raise TargetPlanningError("latest reconciliation is stale")

    def _working_orders(self, account_id):
        connection = connect_database(self.database_path)
        try:
            return connection.execute(
                "SELECT * FROM strategy_orders WHERE strategy_account_id = ? AND state IN ('PENDING_SUBMIT','SUBMIT_UNKNOWN','SUBMITTED','PARTIALLY_FILLED')",
                (account_id,),
            ).fetchall()
        finally:
            connection.close()

    def _intent_by_key(self, account_id, key):
        connection = connect_database(self.database_path)
        try:
            row = connection.execute(
                "SELECT * FROM portfolio_intents WHERE strategy_account_id = ? AND idempotency_key = ?",
                (account_id, key),
            ).fetchone()
            return _intent(cast(sqlite3.Row, row)) if row is not None else None
        finally:
            connection.close()

    def _intent_payload(self, intent_id):
        connection = connect_database(self.database_path)
        try:
            row = connection.execute(
                "SELECT targets_json FROM portfolio_intents WHERE intent_id = ?",
                (intent_id,),
            ).fetchone()
            if row is None:
                raise TargetPlanningError("target intent not found")
            return json.loads(row["targets_json"])
        finally:
            connection.close()

    def _find_order(self, order_id):
        connection = connect_database(self.database_path)
        try:
            return connection.execute("SELECT * FROM strategy_orders WHERE order_id = ?", (order_id,)).fetchone()
        finally:
            connection.close()

    def _order_count(self, intent_id, security, side):
        connection = connect_database(self.database_path)
        try:
            return connection.execute(
                "SELECT COUNT(*) FROM strategy_orders WHERE intent_id = ? AND security = ? AND side = ?",
                (intent_id, security, side.value),
            ).fetchone()[0]
        finally:
            connection.close()

    def _order_account(self, order_id):
        row = self._find_order(order_id)
        if row is None:
            raise TargetPlanningError("outbox order is missing")
        return cast(str, row["strategy_account_id"])

    def _set_state(self, intent_id, state):
        connection = connect_database(self.database_path)
        try:
            connection.execute(
                "UPDATE portfolio_intents SET state = ?, updated_at = ? WHERE intent_id = ?",
                (state.value, datetime.now(SHANGHAI_TZ).isoformat(), intent_id),
            )
            connection.commit()
        finally:
            connection.close()
        return self.get_intent(intent_id)
