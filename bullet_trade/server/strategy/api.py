"""Minimal StrategyLedger RPC facade for one personal QMT account."""

from __future__ import annotations

import json
from dataclasses import dataclass, fields, is_dataclass
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence, Union, cast

from .broker_contract import BrokerCapabilityProfile
from .capital import SQLiteCapitalService
from .domain import MONEY_SCALE, NAV_SCALE, PRICE_SCALE, SHANGHAI_TZ, money_to_units, price_to_units
from .planner_executor import PlannerConfig, SQLiteTargetExecutionService
from .reconciliation import SQLiteReconciliationService, collect_async_broker_snapshot
from .repository import AccountNotFoundError, SQLiteStrategyRepository
from .schema import connect_database
from .valuation import MarketMark, SQLiteValuationService


DatabasePath = Union[str, Path]


@dataclass(frozen=True)
class StrategyAPIConfig:
    database_path: DatabasePath
    trading_enabled: bool = False
    allow_buys: bool = True
    max_age: timedelta = timedelta(minutes=5)
    cash_buffer_units: int = money_to_units("100")
    minimum_order_units: int = 0
    buy_fee_buffer_units: int = money_to_units("5")


def _json_value(value: object) -> object:
    if is_dataclass(value):
        return {
            field.name: _json_value(getattr(value, field.name))
            for field in fields(value)
        }
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_value(item) for item in value]
    return value


def _money(units: int) -> float:
    return units / MONEY_SCALE


def _price(units: int) -> float:
    return units / PRICE_SCALE


class SQLiteStrategyAPI:
    """Thin orchestration layer reused by the existing TCP server."""

    def __init__(
        self,
        config: StrategyAPIConfig,
        broker: object,
        capabilities: BrokerCapabilityProfile,
        data_provider: Optional[object] = None,
    ) -> None:
        self.config = config
        self.database_path = Path(config.database_path)
        self.broker = broker
        self.data_provider = data_provider
        self.repository = SQLiteStrategyRepository(self.database_path)
        self.repository.initialize()
        self.capital = SQLiteCapitalService(self.database_path)
        self.reconciliation = SQLiteReconciliationService(
            self.database_path, capabilities
        )
        self.valuation = SQLiteValuationService(self.database_path)
        self.planner = SQLiteTargetExecutionService(
            self.database_path,
            PlannerConfig(
                trading_enabled=config.trading_enabled,
                allow_buys=config.allow_buys,
                max_age=config.max_age,
                cash_buffer_units=config.cash_buffer_units,
                minimum_order_units=config.minimum_order_units,
                buy_fee_buffer_units=config.buy_fee_buffer_units,
            ),
        )

    async def ensure_account(
        self,
        account_context: object,
        account_key: str,
        payload: Mapping[str, object],
    ) -> Dict[str, object]:
        strategy_id = self._strategy_id(payload)
        initial = payload.get("initial_capital", "10000")
        initial_units = money_to_units(str(initial) if type(initial) is float else initial)  # type: ignore[arg-type]
        physical_id = self._physical_id(account_key)
        broker_snapshot = await collect_async_broker_snapshot(
            cast(Any, self.broker), account_context
        )
        self._ensure_physical_account(physical_id, account_context)
        try:
            account = self.repository.get_strategy_account(strategy_id)
            created = False
        except AccountNotFoundError:
            self.capital.calibrate_broker_available_cash(
                physical_id, broker_snapshot.available_cash_units
            )
            ensured = self.capital.ensure_strategy_account(
                strategy_id, strategy_id, physical_id, initial_units
            )
            account, created = ensured.account, ensured.created
        result = self.reconciliation.synchronize(
            strategy_id, physical_id, broker_snapshot
        )
        account = self.repository.get_strategy_account(strategy_id)
        return {
            "account": self._account_payload(account),
            "created": created,
            "broker_available_cash": _money(broker_snapshot.available_cash_units),
            "broker_positions": _json_value(broker_snapshot.positions),
            "reconciliation": _json_value(result),
        }

    async def get_snapshot(
        self,
        account_context: object,
        account_key: str,
        payload: Mapping[str, object],
    ) -> Dict[str, object]:
        strategy_id = self._strategy_id(payload)
        snapshot, reconciliation, marks = await self._refresh(
            account_context, account_key, strategy_id, payload
        )
        result = self._snapshot_payload(snapshot)
        result["reconciliation"] = _json_value(reconciliation)
        result["marks"] = {
            security: {"price": _price(mark.price_units), "as_of": mark.as_of.isoformat()}
            for security, mark in marks.items()
        }
        return result

    async def submit_targets(
        self,
        account_context: object,
        account_key: str,
        payload: Mapping[str, object],
    ) -> Dict[str, object]:
        strategy_id = self._strategy_id(payload)
        key = str(payload.get("idempotency_key") or "").strip()
        weights = payload.get("weights")
        if not key or not isinstance(weights, Mapping):
            raise ValueError("idempotency_key and weights are required")
        snapshot, _, marks = await self._refresh(
            account_context, account_key, strategy_id, payload
        )
        advance = self.planner.submit_target_weights(
            strategy_id,
            key,
            cast(Any, weights),
            snapshot,
            marks,
            snapshot.as_of,
        )
        dispatched = []

        async def submitter(order_payload: Mapping[str, object]) -> Mapping[str, object]:
            return await cast(Any, self.broker).place_order(
                account_context, dict(order_payload)
            )

        while True:
            dispatch = await self.planner.dispatch_next(submitter)
            if dispatch is None:
                break
            dispatched.append(dispatch)

        # Pick up immediate fills/rejections and make the returned view real.
        refreshed, reconciliation, _ = await self._refresh(
            account_context, account_key, strategy_id, payload
        )
        return {
            "intent": _json_value(self.planner.get_intent(advance.intent.intent_id)),
            "planned_orders": _json_value(advance.orders),
            "dispatched_orders": _json_value(dispatched),
            "snapshot": self._snapshot_payload(refreshed),
            "reconciliation": _json_value(reconciliation),
        }

    def get_intent(self, payload: Mapping[str, object]) -> Dict[str, object]:
        strategy_id = self._strategy_id(payload)
        intent_id = str(payload.get("intent_id") or "").strip()
        idempotency_key = str(payload.get("idempotency_key") or "").strip()
        if not intent_id:
            connection = connect_database(self.database_path)
            try:
                if idempotency_key:
                    row = connection.execute(
                        "SELECT intent_id FROM portfolio_intents WHERE strategy_account_id = ? AND idempotency_key = ?",
                        (strategy_id, idempotency_key),
                    ).fetchone()
                else:
                    row = connection.execute(
                        """
                        SELECT intent_id FROM portfolio_intents
                        WHERE strategy_account_id = ?
                          AND state NOT IN ('COMPLETED','CANCELED','FAILED')
                        ORDER BY created_at DESC LIMIT 1
                        """,
                        (strategy_id,),
                    ).fetchone()
            finally:
                connection.close()
            if row is None:
                return {}
            intent_id = str(row[0])
        intent = self.planner.get_intent(intent_id)
        if intent.account_id != strategy_id:
            raise ValueError("intent does not belong to strategy")
        result = cast(Dict[str, object], _json_value(intent))
        connection = connect_database(self.database_path)
        try:
            row = connection.execute(
                "SELECT targets_json FROM portfolio_intents WHERE intent_id = ?",
                (intent_id,),
            ).fetchone()
        finally:
            connection.close()
        stored = json.loads(row[0]) if row is not None else {}
        result["weights"] = {
            security: value / NAV_SCALE
            for security, value in stored.get("weights_ppm", {}).items()
        }
        return result

    def get_events(self, payload: Mapping[str, object]) -> Dict[str, object]:
        strategy_id = self._strategy_id(payload)
        raw_after_seq = payload.get("after_seq")
        after_seq = int(str(raw_after_seq)) if raw_after_seq is not None else 0
        events = [
            event
            for event in self.repository.list_events(strategy_id)
            if event.event_seq > after_seq
        ]
        return {"events": _json_value(events), "last_seq": events[-1].event_seq if events else after_seq}

    def get_reconciliation(
        self, account_key: str, payload: Mapping[str, object]
    ) -> Dict[str, object]:
        self._strategy_id(payload)
        result = self.reconciliation.latest(self._physical_id(account_key))
        return {"reconciliation": _json_value(result)}

    async def _refresh(self, account_context, account_key, strategy_id, payload):
        physical_id = self._physical_id(account_key)
        broker_snapshot = await collect_async_broker_snapshot(
            cast(Any, self.broker), account_context
        )
        reconciliation = self.reconciliation.synchronize(
            strategy_id, physical_id, broker_snapshot
        )
        as_of = self._as_of(payload.get("as_of"), broker_snapshot.as_of)
        raw_weights = payload.get("weights")
        target_securities = (
            tuple(str(key) for key in raw_weights)
            if isinstance(raw_weights, Mapping)
            else ()
        )
        marks = await self._marks(
            payload.get("marks"), as_of, strategy_id, target_securities
        )
        snapshot = self.valuation.create_snapshot(
            strategy_id, marks, as_of, self.config.max_age
        )
        return snapshot, reconciliation, marks

    async def _marks(
        self,
        raw,
        as_of: datetime,
        strategy_id: str,
        target_securities: Sequence[str] = (),
    ):
        marks: Dict[str, MarketMark] = {}
        if raw is not None:
            if not isinstance(raw, Mapping):
                raise ValueError("marks must be a mapping")
            for security, item in raw.items():
                price = item.get("price") if isinstance(item, Mapping) else item
                mark_as_of = self._as_of(
                    item.get("as_of") if isinstance(item, Mapping) else None,
                    as_of,
                )
                marks[str(security)] = MarketMark(
                    str(security),
                    price_to_units(str(price) if type(price) is float else price),  # type: ignore[arg-type]
                    mark_as_of,
                    "joinquant",
                )
        required = set(self._held_securities(strategy_id)) | {
            str(item) for item in target_securities
        }
        missing = [security for security in sorted(required) if security not in marks]
        for security in missing:
            tick_fn = getattr(self.data_provider, "get_current_tick", None)
            if tick_fn is None:
                raise ValueError("missing mark: {}".format(security))
            tick = await tick_fn(security)
            price = self._tick_price(tick)
            marks[security] = MarketMark(
                security, price_to_units(str(price)), as_of, "qmt"
            )
        return marks

    def _ensure_physical_account(self, physical_id: str, account_context: object) -> None:
        connection = connect_database(self.database_path)
        try:
            exists = connection.execute(
                "SELECT 1 FROM physical_accounts WHERE physical_account_id = ?",
                (physical_id,),
            ).fetchone()
        finally:
            connection.close()
        if exists is None:
            config = getattr(account_context, "config", None)
            broker_ref = str(getattr(config, "account_id", physical_id))
            self.repository.create_physical_account(
                physical_id, "QMT", broker_ref
            )

    def _held_securities(self, strategy_id: str) -> Sequence[str]:
        connection = connect_database(self.database_path)
        try:
            rows = connection.execute(
                "SELECT security FROM positions WHERE strategy_account_id = ? AND total_qty > 0",
                (strategy_id,),
            ).fetchall()
            return tuple(row[0] for row in rows)
        finally:
            connection.close()

    @staticmethod
    def _tick_price(tick: object) -> object:
        if not isinstance(tick, Mapping):
            raise ValueError("QMT mark response is invalid")
        for name in ("last_price", "lastPrice", "price", "last"):
            value = tick.get(name)
            if value is not None and float(value) > 0:
                return value
        raise ValueError("QMT mark response has no valid price")

    @staticmethod
    def _as_of(value: object, default: datetime) -> datetime:
        if value is None:
            return default
        if isinstance(value, datetime):
            result = value
        else:
            result = datetime.fromisoformat(str(value))
        if result.tzinfo is None or result.utcoffset() is None:
            result = result.replace(tzinfo=SHANGHAI_TZ)
        return result.astimezone(SHANGHAI_TZ)

    @staticmethod
    def _strategy_id(payload: Mapping[str, object]) -> str:
        strategy_id = str(payload.get("strategy_id") or "").strip()
        if not strategy_id:
            raise ValueError("strategy_id is required")
        return strategy_id

    @staticmethod
    def _physical_id(account_key: str) -> str:
        return "qmt:{}".format(account_key or "default")

    @staticmethod
    def _account_payload(account) -> Dict[str, object]:
        return {
            "account_id": account.account_id,
            "strategy_id": account.strategy_id,
            "physical_account_id": account.physical_account_id,
            "initial_capital": _money(account.initial_capital_units),
            "cash": _money(account.cash_units),
            "reserved_cash": _money(account.reserved_cash_units),
            "available_cash": _money(account.available_cash_units),
            "ledger_version": account.ledger_version,
            "event_seq": account.event_seq,
            "status": account.status.value,
        }

    @staticmethod
    def _snapshot_payload(snapshot) -> Dict[str, object]:
        positions = {}
        for item in snapshot.positions:
            positions[item.security] = {
                "security": item.security,
                "total_amount": item.total_qty,
                "closeable_amount": item.sellable_qty,
                "avg_cost": _price(item.avg_cost_price_units),
                "price": _price(item.mark_price_units),
                "value": _money(item.market_value_units),
                "unrealized_pnl": _money(item.unrealized_pnl_units),
            }
        return {
            "account_id": snapshot.account_id,
            "strategy_id": snapshot.strategy_id,
            "as_of": snapshot.as_of.isoformat(),
            "snapshot_version": snapshot.snapshot_version,
            "ledger_version": snapshot.ledger_version,
            "cash": _money(snapshot.cash_units),
            "reserved_cash": _money(snapshot.reserved_cash_units),
            "available_cash": _money(snapshot.available_cash_units),
            "positions_value": _money(snapshot.positions_value_units),
            "total_value": _money(snapshot.total_assets_units),
            "starting_cash": _money(snapshot.net_capital_units),
            "total_pnl": _money(snapshot.total_pnl_units),
            "realized_pnl": _money(snapshot.realized_pnl_units),
            "unrealized_pnl": _money(snapshot.unrealized_pnl_units),
            "fees": _money(snapshot.fees_units),
            "nav": snapshot.nav_units / NAV_SCALE,
            "returns": snapshot.nav_units / NAV_SCALE - 1.0,
            "performance_ready": snapshot.performance_ready,
            "positions": positions,
        }
