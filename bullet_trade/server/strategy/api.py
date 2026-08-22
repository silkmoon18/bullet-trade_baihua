"""Minimal StrategyLedger RPC facade for one personal QMT account."""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, fields, is_dataclass
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple, Union, cast

from .broker_contract import BrokerCapabilityProfile
from ..feishu_notifier import (
    TargetBuyPlanItem,
    TargetBuyPlanNotification,
    TradeNotification,
)
from .capital import BrokerCashMismatchError, SQLiteCapitalService
from .domain import MONEY_SCALE, NAV_SCALE, PRICE_SCALE, SHANGHAI_TZ, money_to_units, price_to_units
from .execution import (
    ConditionalLimitExecution,
    ExecutionRequest,
    MarketQuote,
    RepricingPolicy,
    execution_request_from_wire,
    execution_request_to_wire,
)
from .planner_executor import (
    PlannerConfig,
    SQLiteTargetExecutionService,
    TargetPlanningError,
)
from .reconciliation import SQLiteReconciliationService, collect_async_broker_snapshot
from .repository import (
    AccountNotFoundError,
    LedgerInvariantError,
    SQLiteStrategyRepository,
)
from .schema import connect_database
from .valuation import MarketMark, SQLiteValuationService


DatabasePath = Union[str, Path]
logger = logging.getLogger(__name__)


def _uses_conditional_execution(request: ExecutionRequest) -> bool:
    return isinstance(request.style, ConditionalLimitExecution) or isinstance(
        request.sell_style, ConditionalLimitExecution
    )


@dataclass(frozen=True)
class StrategyAPIConfig:
    database_path: DatabasePath
    trading_enabled: bool = False
    enabled_strategy_ids: Tuple[str, ...] = ()
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
        notification_handler=None,
    ) -> None:
        self.config = config
        self.database_path = Path(config.database_path)
        self.broker = broker
        self.data_provider = data_provider
        self.notification_handler = notification_handler
        self.startup_ready = False
        self._event_loop = None
        self._runtime_bindings = {}
        self._quote_cache = {}
        self._resume_locks = {}
        self._background_tasks = set()
        self.repository = SQLiteStrategyRepository(self.database_path)
        self.repository.initialize()
        self.capital = SQLiteCapitalService(self.database_path)
        self.reconciliation = SQLiteReconciliationService(
            self.database_path,
            capabilities,
            notification_handler,
            require_verified_capabilities=config.trading_enabled,
        )
        self.valuation = SQLiteValuationService(self.database_path)
        self.planner = SQLiteTargetExecutionService(
            self.database_path,
            PlannerConfig(
                trading_enabled=config.trading_enabled,
                enabled_strategy_ids=config.enabled_strategy_ids,
                allow_buys=config.allow_buys,
                max_age=config.max_age,
                cash_buffer_units=config.cash_buffer_units,
                minimum_order_units=config.minimum_order_units,
                buy_fee_buffer_units=config.buy_fee_buffer_units,
            ),
            notification_handler,
        )
        add_tick_listener = getattr(
            self.data_provider, "add_tick_listener", None
        )
        if callable(add_tick_listener):
            add_tick_listener(self._on_tick_event)
        add_broker_listener = getattr(
            self.broker, "add_event_listener", None
        )
        if callable(add_broker_listener):
            add_broker_listener(self._on_broker_event)

    async def ensure_account(
        self,
        account_context: object,
        account_key: str,
        payload: Mapping[str, object],
    ) -> Dict[str, object]:
        strategy_id = self._strategy_id(payload)
        self._bind_runtime(strategy_id, account_context, account_key)
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
            try:
                self.capital.calibrate_broker_available_cash(
                    physical_id, broker_snapshot.available_cash_units
                )
            except BrokerCashMismatchError as exc:
                raise BrokerCashMismatchError(
                    "{}; requested_initial_capital={:.4f}, new_strategy_id={}".format(
                        exc,
                        initial_units / MONEY_SCALE,
                        strategy_id,
                    )
                ) from exc
            try:
                ensured = self.capital.ensure_strategy_account(
                    strategy_id, strategy_id, physical_id, initial_units
                )
            except LedgerInvariantError as exc:
                raise LedgerInvariantError(
                    "{}; broker_available_cash={:.4f}, new_strategy_id={}".format(
                        exc,
                        broker_snapshot.available_cash_units / MONEY_SCALE,
                        strategy_id,
                    )
                ) from exc
            account, created = ensured.account, ensured.created
        result = self._synchronize(strategy_id, physical_id, broker_snapshot)
        account = self.repository.get_strategy_account(strategy_id)
        self.startup_ready = result.state.value == "READY"
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
        self._bind_runtime(strategy_id, account_context, account_key)
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
        self._bind_runtime(strategy_id, account_context, account_key)
        if not self.startup_ready:
            raise RuntimeError("StrategyLedger startup reconciliation is not READY")
        key = str(payload.get("idempotency_key") or "").strip()
        weights = payload.get("weights")
        if not key or not isinstance(weights, Mapping):
            raise ValueError("idempotency_key and weights are required")
        raw_execution = payload.get("execution")
        if raw_execution is None:
            execution_request = ExecutionRequest()
        elif isinstance(raw_execution, Mapping):
            execution_request = execution_request_from_wire(raw_execution)
        else:
            raise ValueError("execution must be an object")
        if _uses_conditional_execution(execution_request):
            replace = getattr(
                self.data_provider, "replace_execution_quotes", None
            )
            subscribe = getattr(
                self.data_provider, "subscribe_execution_quotes", None
            )
            if not callable(replace) and not callable(subscribe):
                raise RuntimeError(
                    "current data adapter has no native quote callback support"
                )
        snapshot, _, marks = await self._refresh(
            account_context, account_key, strategy_id, payload
        )
        cancel_requested_order_ids = []
        for broker_order_id in self.planner.stale_broker_order_ids(strategy_id):
            await cast(Any, self.broker).cancel_order(
                account_context, broker_order_id
            )
            cancel_requested_order_ids.append(broker_order_id)
        if cancel_requested_order_ids:
            snapshot, _, marks = await self._refresh(
                account_context, account_key, strategy_id, payload
            )
        quotes = await self._execution_quotes(
            execution_request, tuple(marks), snapshot.as_of
        )
        advance = self.planner.submit_target_weights(
            strategy_id,
            key,
            cast(Any, weights),
            snapshot,
            marks,
            snapshot.as_of,
            execution_request=execution_request,
            quotes=quotes,
        )
        if _uses_conditional_execution(execution_request):
            await self._sync_quote_subscriptions(strategy_id)
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
        await self._sync_quote_subscriptions(strategy_id)
        return {
            "intent": _json_value(self.planner.get_intent(advance.intent.intent_id)),
            "planned_orders": _json_value(advance.orders),
            "dispatched_orders": _json_value(dispatched),
            "cancel_requested_order_ids": cancel_requested_order_ids,
            "snapshot": self._snapshot_payload(refreshed),
            "reconciliation": _json_value(reconciliation),
        }

    async def startup_check(self, account_context: object, account_key: str) -> bool:
        physical_id = self._physical_id(account_key)
        connection = connect_database(self.database_path)
        try:
            row = connection.execute(
                "SELECT strategy_account_id FROM strategy_accounts WHERE physical_account_id = ?",
                (physical_id,),
            ).fetchone()
        finally:
            connection.close()
        if row is None:
            self.startup_ready = False
            return False
        self._bind_runtime(str(row[0]), account_context, account_key)
        snapshot = await collect_async_broker_snapshot(
            cast(Any, self.broker), account_context
        )
        result = self._synchronize(str(row[0]), physical_id, snapshot)
        self.startup_ready = result.state.value == "READY"
        return self.startup_ready

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
        result["execution"] = execution_request_to_wire(
            intent.execution_request
        )
        result["trading_day"] = (
            intent.trading_day.isoformat() if intent.trading_day else None
        )
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

    async def cancel_intent(
        self,
        account_context: object,
        account_key: str,
        payload: Mapping[str, object],
    ) -> Dict[str, object]:
        """Cancel one strategy intent without allowing overlapping targets."""

        strategy_id = self._strategy_id(payload)
        self._bind_runtime(strategy_id, account_context, account_key)
        intent_id = str(payload.get("intent_id") or "").strip()
        if not intent_id:
            raise ValueError("intent_id is required")
        intent = self.planner.get_intent(intent_id)
        if intent.account_id != strategy_id:
            raise ValueError("intent does not belong to strategy")
        requested = []
        broker_order_ids = self.planner.cancelable_broker_order_ids(intent_id)
        self.planner.request_intent_cancellation(intent_id)
        for broker_order_id in broker_order_ids:
            await cast(Any, self.broker).cancel_order(
                account_context, broker_order_id
            )
            requested.append(broker_order_id)
        if requested:
            await self._refresh(
                account_context, account_key, strategy_id, {}
            )
        canceled = False
        try:
            intent = self.planner.cancel_intent_if_idle(intent_id)
            canceled = intent.state.value == "CANCELED"
        except TargetPlanningError as exc:
            if not requested:
                raise
            logger.info(
                "intent %s cancellation still pending: %s",
                intent_id,
                exc,
            )
            intent = self.planner.get_intent(intent_id)
        await self._sync_quote_subscriptions(strategy_id)
        result = cast(Dict[str, object], _json_value(intent))
        result["execution"] = execution_request_to_wire(
            intent.execution_request
        )
        return {
            "intent": result,
            "canceled": canceled,
            "cancel_requested_order_ids": requested,
        }

    def get_reconciliation(
        self, account_key: str, payload: Mapping[str, object]
    ) -> Dict[str, object]:
        self._strategy_id(payload)
        result = self.reconciliation.latest(self._physical_id(account_key))
        return {"reconciliation": _json_value(result)}

    def notify_target_buy_plan(
        self, payload: Mapping[str, object]
    ) -> Dict[str, object]:
        strategy_id = self._strategy_id(payload)
        mode = str(payload.get("mode") or "").strip().upper()
        mode = {
            "SHADOW": "JQ",
            "SIGNAL_ONLY": "JQ",
            "JQ_PAPER": "JQ",
            "REMOTE": "QMT_REMOTE",
            "LIVE": "QMT_REMOTE",
        }.get(mode, mode)
        if mode not in ("JQ", "QMT_REMOTE"):
            raise ValueError("mode must be JQ or QMT_REMOTE")
        raw_items = payload.get("items")
        if not isinstance(raw_items, (list, tuple)) or not raw_items:
            raise ValueError("items must be a non-empty list")
        items = []
        total = Decimal("0")
        for raw in raw_items:
            if not isinstance(raw, Mapping):
                raise ValueError("target buy plan item must be a mapping")
            security = str(raw.get("security") or "").strip()
            quantity = raw.get("quantity")
            if not security or type(quantity) is not int or quantity <= 0:
                raise ValueError("security and positive integer quantity are required")
            amount = self._positive_decimal(raw.get("amount"), "amount")
            reference_price = raw.get("reference_price")
            if reference_price is not None:
                reference_price = self._positive_decimal(
                    reference_price, "reference_price"
                )
            items.append(
                TargetBuyPlanItem(
                    security=security,
                    quantity=quantity,
                    amount=amount,
                    reference_price=reference_price,
                )
            )
            total += amount
        occurred_at = self._as_of(
            payload.get("occurred_at"), datetime.now(SHANGHAI_TZ)
        )
        accepted = False
        if self.notification_handler is not None:
            try:
                self.notification_handler(
                    TargetBuyPlanNotification(
                        strategy_id=strategy_id,
                        mode=mode,
                        items=tuple(items),
                        occurred_at=occurred_at,
                    )
                )
                accepted = True
            except Exception:
                accepted = False
        return {
            "accepted": accepted,
            "item_count": len(items),
            "total_amount": float(total),
        }

    async def _refresh(self, account_context, account_key, strategy_id, payload):
        physical_id = self._physical_id(account_key)
        broker_snapshot = await collect_async_broker_snapshot(
            cast(Any, self.broker), account_context
        )
        reconciliation = self._synchronize(strategy_id, physical_id, broker_snapshot)
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

    def _synchronize(self, strategy_id, physical_id, broker_snapshot):
        previous = self.reconciliation.latest(physical_id)
        result = self.reconciliation.synchronize(
            strategy_id, physical_id, broker_snapshot
        )
        self.startup_ready = result.state.value == "READY"
        if (
            self.notification_handler is not None
            and result.state.value == "BLOCKED"
            and (previous is None or previous.state.value != "BLOCKED" or previous.details != result.details)
        ):
            try:
                blockers = result.details.get("blockers", ())
                if not isinstance(blockers, (list, tuple)):
                    blockers = (blockers,)
                self.notification_handler(
                    TradeNotification(
                        event="RECONCILIATION_BLOCKED",
                        strategy_id=strategy_id,
                        security="-",
                        side="-",
                        status="BLOCKED",
                        detail="; ".join(str(item) for item in blockers),
                        title="实盘账实对账已阻断",
                    )
                )
            except Exception:
                pass
        return result

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

    async def _execution_quotes(
        self,
        execution_request: ExecutionRequest,
        securities: Sequence[str],
        as_of: datetime,
    ) -> Mapping[str, MarketQuote]:
        if not _uses_conditional_execution(execution_request):
            return {}
        tick_fn = getattr(self.data_provider, "get_current_tick", None)
        if tick_fn is None:
            return {}
        quotes = {}
        for security in securities:
            tick = await tick_fn(security)
            if not isinstance(tick, Mapping):
                continue
            bid = self._best_quote_price(tick, "bid")
            ask = self._best_quote_price(tick, "ask")
            last = self._tick_price_or_none(tick)
            quotes[security] = MarketQuote(
                security=security,
                as_of=as_of,
                bid_price_units=(
                    price_to_units(str(bid)) if bid is not None else None
                ),
                ask_price_units=(
                    price_to_units(str(ask)) if ask is not None else None
                ),
                last_price_units=(
                    price_to_units(str(last)) if last is not None else None
                ),
            )
            self._quote_cache[security] = quotes[security]
        return quotes

    def _bind_runtime(
        self, strategy_id: str, account_context: object, account_key: str
    ) -> None:
        try:
            self._event_loop = asyncio.get_running_loop()
        except RuntimeError:
            pass
        self._runtime_bindings[strategy_id] = (
            account_context,
            account_key,
        )
        self._resume_locks.setdefault(strategy_id, asyncio.Lock())

    def _on_tick_event(self, payload: object) -> None:
        loop = self._event_loop
        if loop is None or loop.is_closed():
            return
        safe_payload = dict(payload) if isinstance(payload, Mapping) else payload
        loop.call_soon_threadsafe(self._accept_tick_event, safe_payload)

    def _on_broker_event(
        self, account_key: str, event: str, payload: object = None
    ) -> None:
        loop = self._event_loop
        if loop is None or loop.is_closed():
            return
        loop.call_soon_threadsafe(
            self._schedule_background,
            self._handle_broker_event(account_key, event),
        )

    def _schedule_background(self, awaitable) -> None:
        task = asyncio.create_task(awaitable)
        self._background_tasks.add(task)
        task.add_done_callback(self._background_done)

    def _background_done(self, task: "asyncio.Task[object]") -> None:
        self._background_tasks.discard(task)
        try:
            task.result()
        except asyncio.CancelledError:
            return
        except Exception:
            logger.exception("StrategyLedger callback task failed")

    def _accept_tick_event(self, payload: object) -> None:
        for security, tick in self._tick_items(payload):
            bid = self._best_quote_price(tick, "bid")
            ask = self._best_quote_price(tick, "ask")
            last = self._tick_price_or_none(tick)
            quote = MarketQuote(
                security=security,
                as_of=datetime.now(SHANGHAI_TZ),
                bid_price_units=(
                    price_to_units(str(bid)) if bid is not None else None
                ),
                ask_price_units=(
                    price_to_units(str(ask)) if ask is not None else None
                ),
                last_price_units=(
                    price_to_units(str(last)) if last is not None else None
                ),
            )
            self._quote_cache[security] = quote
            self._schedule_background(self._handle_quote(security))

    async def _handle_quote(self, security: str) -> None:
        quote = self._quote_cache.get(security)
        if quote is None:
            return
        for intent in self.planner.active_intents():
            if (
                not _uses_conditional_execution(intent.execution_request)
                or security not in self.planner.reference_prices(intent.intent_id)
                or intent.account_id not in self._runtime_bindings
            ):
                continue
            if not self.planner.quote_triggers_intent(
                intent.intent_id, security, quote
            ):
                continue
            await self._resume_intent(intent.intent_id)

    async def _handle_broker_event(
        self, account_key: str, event: str
    ) -> None:
        if event == "disconnected":
            self.startup_ready = False
            return
        strategy_ids = [
            strategy_id
            for strategy_id, (_, key) in self._runtime_bindings.items()
            if key == account_key
        ]
        for strategy_id in strategy_ids:
            lock = self._resume_locks[strategy_id]
            async with lock:
                binding = self._runtime_bindings[strategy_id]
                intents = self.planner.active_intents(strategy_id)
                if not intents:
                    await self._refresh(
                        binding[0], binding[1], strategy_id, {}
                    )
                for intent in intents:
                    await self._resume_intent_locked(intent.intent_id)
                await self._sync_quote_subscriptions(strategy_id)

    async def _resume_intent(self, intent_id: str) -> None:
        intent = self.planner.get_intent(intent_id)
        lock = self._resume_locks.get(intent.account_id)
        if lock is None:
            return
        async with lock:
            await self._resume_intent_locked(intent_id)

    async def _resume_intent_locked(self, intent_id: str) -> None:
        intent = self.planner.get_intent(intent_id)
        binding = self._runtime_bindings.get(intent.account_id)
        if binding is None:
            return
        now = datetime.now(SHANGHAI_TZ)
        references = self.planner.reference_prices(intent_id)
        if (
            intent.execution_request.repricing
            is RepricingPolicy.RECOMPUTE
        ):
            references = {
                security: (
                    self._quote_cache[security].last_price_units
                    if security in self._quote_cache
                    and self._quote_cache[security].last_price_units
                    is not None
                    else price_units
                )
                for security, price_units in references.items()
            }
        marks = {
            security: {
                "price": _price(price_units),
                "as_of": now.isoformat(),
            }
            for security, price_units in references.items()
        }
        snapshot, _, market_marks = await self._refresh(
            binding[0],
            binding[1],
            intent.account_id,
            {
                "marks": marks,
                "weights": {security: 0 for security in intent.targets},
                "as_of": now,
            },
        )
        advance = self.planner.advance_intent(
            intent_id,
            snapshot,
            market_marks,
            now,
            quotes=self._quote_cache,
        )
        if not advance.orders:
            await self._sync_quote_subscriptions(intent.account_id)
            return

        async def submitter(order_payload: Mapping[str, object]):
            return await cast(Any, self.broker).place_order(
                binding[0], dict(order_payload)
            )

        while await self.planner.dispatch_next(submitter) is not None:
            pass
        await self._sync_quote_subscriptions(intent.account_id)

    async def _sync_quote_subscriptions(self, strategy_id: str) -> None:
        symbols = set()
        for intent in self.planner.active_intents(strategy_id):
            if _uses_conditional_execution(
                intent.execution_request
            ) and not self.planner.intent_cancel_requested(intent.intent_id):
                symbols.update(self.planner.reference_prices(intent.intent_id))
        replace = getattr(
            self.data_provider, "replace_execution_quotes", None
        )
        if callable(replace):
            await replace(strategy_id, tuple(sorted(symbols)))
            return
        subscribe = getattr(
            self.data_provider, "subscribe_execution_quotes", None
        )
        if symbols and callable(subscribe):
            await subscribe(tuple(sorted(symbols)))

    @classmethod
    def _tick_items(cls, payload: object):
        if not isinstance(payload, Mapping):
            return ()
        direct_code = (
            payload.get("stockCode")
            or payload.get("stock_code")
            or payload.get("security")
            or payload.get("code")
        )
        if direct_code:
            return ((cls._joinquant_security(str(direct_code)), payload),)
        items = []
        for raw_security, tick in payload.items():
            if isinstance(tick, Mapping):
                items.append(
                    (cls._joinquant_security(str(raw_security)), tick)
                )
        return tuple(items)

    @staticmethod
    def _joinquant_security(value: str) -> str:
        upper = value.strip().upper()
        if upper.endswith(".SH"):
            return upper[:-3] + ".XSHG"
        if upper.endswith(".SZ"):
            return upper[:-3] + ".XSHE"
        if upper.endswith(".BJ"):
            return upper[:-3] + ".XBSE"
        return upper

    @staticmethod
    def _best_quote_price(tick: Mapping[str, object], side: str):
        names = (
            ("ask_price1", "ask1", "askPrice", "ask_price")
            if side == "ask"
            else ("bid_price1", "bid1", "bidPrice", "bid_price")
        )
        for name in names:
            value = tick.get(name)
            if isinstance(value, (list, tuple)):
                value = value[0] if value else None
            elif value is not None and not isinstance(value, (str, bytes)):
                try:
                    value = value[0]  # type: ignore[index]
                except (IndexError, KeyError, TypeError):
                    pass
            try:
                number = float(value) if value is not None else 0.0
            except (TypeError, ValueError):
                continue
            if number > 0:
                return number
        return None

    @staticmethod
    def _tick_price_or_none(tick: Mapping[str, object]):
        try:
            return SQLiteStrategyAPI._tick_price(tick)
        except ValueError:
            return None

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
    def _positive_decimal(value: object, field: str) -> Decimal:
        if isinstance(value, bool):
            raise ValueError("{} must be a positive number".format(field))
        try:
            number = Decimal(str(value))
        except (InvalidOperation, ValueError) as exc:
            raise ValueError("{} must be a positive number".format(field)) from exc
        if not number.is_finite() or number <= 0:
            raise ValueError("{} must be a positive number".format(field))
        return number

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
