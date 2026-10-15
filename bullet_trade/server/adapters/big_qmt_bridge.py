"""Full QMT transport adapter. Execution policy stays in StrategyLedger.

The HTTP backend remains the default. This optional same-host bridge reuses its
field normalization and submission confirmation, and the existing history DB.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime

from bullet_trade.data.api import get_security_tplus_override
from bullet_trade.utils.env_loader import get_env
from ..big_qmt_bridge import QmtBridge
from ..strategy.broker_history import SQLiteBrokerHistoryStore, merge_broker_rows
from .base import AdapterBundle, BROKER_CALL_MARKER_KEY
from .big_qmt import (
    BigQmtBrokerAdapter, BigQmtDataAdapter, BigQmtGatewayClient, BigQmtGatewayError,
    _fill_trade_sides_from_orders, _filter_orders, _filter_trades,
    _normalize_order, _normalize_trade,
    _normalize_snapshot_tick, load_big_qmt_gateway_config,
    _matches_confirmed_big_qmt_submission,
)

log = logging.getLogger(__name__)
CALLBACK_CONFIRM_GRACE_SECONDS = 0.5


@dataclass
class _SubmissionCallbacks:
    changed: asyncio.Event = field(default_factory=asyncio.Event)
    orders: dict = field(default_factory=dict)
    wait_until: float = 0.0
    source: str = "query"
    disconnected: bool = False


def _is_operation_tag(tag):
    return re.fullmatch(r"bt:[0-9a-f]{6}:[0-9a-f]{12}", tag) is not None


class BridgeClient(BigQmtGatewayClient):
    def __init__(self, config, bridge):
        super().__init__(config)
        self.bridge = bridge

    async def get(self, path):
        return await self.post(path)

    async def post(self, path, payload=None, *, timeout_seconds=None):
        payload = dict(payload or {})
        payload.pop(BROKER_CALL_MARKER_KEY, None)
        try:
            result = await self.bridge.request(path, payload, timeout_seconds or self.config.timeout_seconds)
        except asyncio.TimeoutError as exc:
            raise BigQmtGatewayError("Big QMT bridge reply timed out; reconcile before retry",
                                     code="SUBMIT_UNKNOWN" if path in {"/place_order", "/cancel_order"} else "BRIDGE_TIMEOUT") from exc
        self._record_success()
        return result

    def qmt_status(self):
        health = self.bridge.health
        return dict(backend_type="big_qmt", transport="bridge", ready=self.bridge.ready,
                    state="ready" if self.bridge.ready else "unavailable",
                    gateway_url="tcp://127.0.0.1:%s" % self.bridge.port,
                    trading_enabled=health.get("trading_enabled"),
                    cancel_order_enabled=health.get("cancel_enabled"),
                    health_cache_age_seconds=max(0, time.monotonic() - self.bridge.last_seen),
                    actions=self.config.action_status, big_qmt_gateway=dict(health))


class BridgeDataAdapter(BigQmtDataAdapter):
    # Independent reads can be queued in one bridge pump; the QMT script
    # still executes each request on its own thread in arrival order.
    supports_parallel_data_reads = True

    def __init__(self, client):
        super().__init__(client)
        self._tick_listeners = []
        self._owners = {}
        self._applied = None
        self._subscription_lock = asyncio.Lock()
        self._restore_task = None
        client.bridge.listeners.append(self._event)

    async def start(self):
        await self.client.bridge.start()

    async def stop(self):
        if self._restore_task:
            self._restore_task.cancel()
            await asyncio.gather(self._restore_task, return_exceptions=True)
            self._restore_task = None
        await self.client.bridge.stop()

    def add_tick_listener(self, callback):
        if callback not in self._tick_listeners:
            self._tick_listeners.append(callback)

    def _event(self, event, payload):
        if event == "tick":
            for listener in tuple(self._tick_listeners):
                try:
                    listener(payload)
                except Exception:
                    log.exception("Big QMT tick listener failed")
        elif event == "connected":
            self._applied = None
            if self._restore_task is None or self._restore_task.done():
                self._restore_task = asyncio.create_task(self._restore_subscriptions())
        elif event == "disconnected":
            self._applied = None

    async def _restore_subscriptions(self):
        try:
            await self._sync_subscriptions()
        except Exception:
            log.exception("Big QMT quote subscriptions not ready; execution will retry")

    async def _sync_subscriptions(self):
        async with self._subscription_lock:
            desired = set().union(*self._owners.values()) if self._owners else set()
            if desired != self._applied:
                await self.client.post("/data/subscriptions", {"symbols": sorted(desired)})
                self._applied = desired

    async def replace_execution_quotes(self, owner, symbols):
        cleaned = {str(symbol).strip().upper() for symbol in symbols if str(symbol).strip()}
        if cleaned:
            self._owners[str(owner)] = cleaned
        else:
            self._owners.pop(str(owner), None)
        await self._sync_subscriptions()

    async def get_current_tick(self, symbol):
        raw = await self.client.post("/data/current_tick", {"security": symbol})
        # Keep exchange limits/tick size and source timestamp alongside base fields.
        return dict(raw, **_normalize_snapshot_tick(raw, symbol)) if raw else None

    async def get_snapshot(self, payload):
        return await self.get_current_tick(payload["security"])

    async def get_tplus(self, security):
        override = get_security_tplus_override(security)
        if override is not None:
            return int(override)
        return int(await self.client.post("/data/tplus", {"security": security}))

    async def get_history(self, payload):
        raise BigQmtGatewayError("Bridge is for live execution; keep historical strategy data in JQ",
                                 code="NOT_IMPLEMENTED", broker_called=False)


class BridgeBrokerAdapter(BigQmtBrokerAdapter):
    supports_parallel_snapshot_reads = True

    def __init__(self, config, router, client):
        super().__init__(config, router, client)
        self._listeners = []
        self._history = SQLiteBrokerHistoryStore(config.strategy_database_path) if config.strategy_database_path else None
        self._account = router.list_accounts()[0]
        self._observed_order_ids = set()
        self._submission_callbacks = {}
        client.bridge.listeners.append(self._event)

    async def start(self):
        await self.client.bridge.start()

    async def stop(self):
        await self.client.bridge.stop()

    def add_event_listener(self, callback):
        if callback not in self._listeners:
            self._listeners.append(callback)

    def has_durable_broker_history(self):
        return self._history is not None

    def remember_trade_market_quote(self, trade):
        """Keep the fill-time quote beside the broker report across restarts."""
        if self._history is None:
            return False
        return self._history.record_trade(
            self._account.config.key or "default", trade
        )

    def _event(self, event, payload):
        if event == "tick":
            return
        key = self._account.config.key or "default"
        if isinstance(payload, dict) and event in {"order", "trade"}:
            payload = (_normalize_order if event == "order" else _normalize_trade)(payload)
            try:
                log.info('QMT原生回报时点 | event=%s security=%s tag=%s status=%s | QMT接收=%s 服务器接收=%s 券商时间=%s',
                         event, payload.get('security'), payload.get('qmt_user_order_id') or payload.get('remark'),
                         payload.get('status'), payload.get('_bt_event_received_at_ms'),
                         datetime.now().astimezone().isoformat(timespec='milliseconds'),
                         payload.get('order_time') or payload.get('time'))
            except Exception:
                pass
            if self._history:
                # A persistence failure is surfaced, never silently treated as stored.
                getattr(self._history, "record_" + event)(key, payload)
            if event == "order":
                order_id = str(payload.get("order_id") or "").strip()
                if order_id:
                    self._observed_order_ids.add(order_id)
                for tag, pending in tuple(self._submission_callbacks.items()):
                    if pending.disconnected:
                        continue
                    if tag in (payload.get("qmt_user_order_id"), payload.get("order_remark"), payload.get("remark")):
                        pending.orders[order_id] = dict(payload)
                        pending.changed.set()
        elif event == "disconnected":
            # A callback from a replacement connection cannot acknowledge an
            # in-flight write on the old one. Use the normal query recovery.
            for pending in self._submission_callbacks.values():
                pending.orders.clear()
                pending.disconnected = True
                pending.wait_until = 0
                pending.changed.set()
        for callback in tuple(self._listeners):
            try:
                callback(key, event, payload)
            except Exception:
                log.exception("Big QMT broker listener failed: %s", event)

    async def list_orders(self, account, filters=None):
        current = await super().list_orders(account, {})
        if self._history:
            self._history.record_orders(account.config.key or "default", current)
            if (filters or {}).get("include_history"):
                current = list(merge_broker_rows(current, self._history.list_orders(account.config.key or "default"), "order_id"))
        self._observed_order_ids.update(str(row["order_id"]) for row in current if row.get("order_id"))
        return _filter_orders(current, filters or {})

    async def list_trades(self, account, filters=None):
        current = await super().list_trades(account, {})
        return self._merge_trade_history(account, current, filters)

    async def _list_snapshot_trades(self, account, orders_read):
        # Both reads are already queued in the same bridge pump. Missing
        # directions use that exact observation, not a second /orders RPC.
        current = await self._query_trades(account, {})
        current = _fill_trade_sides_from_orders(current, await orders_read)
        return self._merge_trade_history(account, current, {"include_history": True})

    def _merge_trade_history(self, account, current, filters):
        if self._history:
            self._history.record_trades(account.config.key or "default", current)
            if (filters or {}).get("include_history"):
                current = list(merge_broker_rows(current, self._history.list_trades(account.config.key or "default"), "trade_id"))
                current = _fill_trade_sides_from_orders(
                    current, list(self._history.list_orders(account.config.key or "default"))
                )
        return _filter_trades(current, filters or {})

    async def place_order(self, account, payload):
        request = dict(payload)
        # Fork client_tag already fits QMT's 23-byte field. Preserve it verbatim,
        # so callbacks still identify the operation after both processes restart.
        tag = str(request.get("order_remark") or request.get("remark") or "")
        if _is_operation_tag(tag):
            request["qmt_user_order_id"] = tag
        style = dict(request.get("style") or {})
        if style.get("type") == "market" and not (style.get("market_type") or request.get("market_type")):
            # Match the former xtquant default: SH=42 / SZ=47 five-level IOC.
            style["market_type"] = "five_level_ioc"
            request["style"] = style
        # This only waits for acknowledgement, not fills. Chasing stays server-side.
        if float(request.get("wait_timeout") or 0) <= 0:
            request["wait_timeout"] = 3.0
        if not _is_operation_tag(tag):
            return await super().place_order(account, request)
        if tag in self._submission_callbacks:
            raise BigQmtGatewayError("Submission already in progress for this client tag", broker_called=False)
        pending = _SubmissionCallbacks()
        self._submission_callbacks[tag] = pending  # Before passorder: early callbacks count.
        started = time.monotonic()
        requested_wall = datetime.now().astimezone().isoformat(timespec="milliseconds")
        try:
            result = await super().place_order(account, request)
            log.info(
                "QMT委托确认 | 标的=%s 方向=%s | 确认来源=%s 提交至确认=%.0fms | 请求时间=%s 确认时间=%s | 原生时点=%s",
                request.get("security"), request.get("side"),
                "unknown" if result.get("submit_unknown") else pending.source,
                (time.monotonic() - started) * 1000,
                requested_wall, datetime.now().astimezone().isoformat(timespec="milliseconds"),
                result.get("_bt_submit_timing", {}),
            )
            return result
        finally:
            self._submission_callbacks.pop(tag, None)

    async def _snapshot_order_ids(self, account, request):
        if _is_operation_tag(str(request.get("qmt_user_order_id") or "")):
            # Durable operation tags are unique per attempt. Startup/submit
            # reconciliation already observed this physical account's orders.
            # Keep its old IDs; don't make a second native /orders round-trip.
            return set(self._observed_order_ids)
        return await super()._snapshot_order_ids(account, request)

    async def _confirm_place_order_submission(self, account, request, order, wait_timeout,
                                             known_order_ids=None, submitted_at=None):
        pending = self._submission_callbacks.get(str(request.get("qmt_user_order_id") or ""))
        if pending is not None:
            pending.wait_until = time.monotonic() + min(CALLBACK_CONFIRM_GRACE_SECONDS, wait_timeout)
        result = await super()._confirm_place_order_submission(
            account, request, order, wait_timeout, known_order_ids, submitted_at
        )
        if "_bt_submit_timing" in order:
            result["_bt_submit_timing"] = order["_bt_submit_timing"]
        return result

    async def _find_submitted_order(self, account, request, order, known_order_ids=None, submitted_at=None):
        tag = str(request.get("qmt_user_order_id") or "")
        pending = self._submission_callbacks.get(tag)
        if pending is not None and not pending.disconnected:
            while True:
                matches = [row for row in pending.orders.values() if _matches_confirmed_big_qmt_submission(
                    row, request, tag, known_order_ids or set(), submitted_at
                )]
                if len(matches) == 1:
                    pending.source = "callback"
                    return matches[0]
                remaining = pending.wait_until - time.monotonic()
                if remaining <= 0:
                    break
                pending.changed.clear()
                try:
                    await asyncio.wait_for(pending.changed.wait(), remaining)
                except asyncio.TimeoutError:
                    break
        # Missing, incomplete or conflicting callbacks are not proof. Query
        # using the same identity checks; never send the native order again.
        return await super()._find_submitted_order(account, request, order, known_order_ids, submitted_at)


def build_bridge_bundle(config, router):
    accounts = router.list_accounts()
    if len(accounts) != 1 or accounts[0].config.account_type.upper() != "STOCK":
        raise ValueError("Big QMT bridge requires exactly one STOCK broker account; multiple strategy ledgers are supported")
    gateway = load_big_qmt_gateway_config(config)
    for name, status in gateway.action_status.items():
        if name in {"data.history", "data.ensure_cache", "data.get_all_securities", "data.get_index_stocks", "data.get_split_dividend"}:
            status.update(status="unavailable", reason="not exposed by live QMT bridge")
    account = accounts[0].config
    bridge = QmtBridge(gateway.password, account.account_id, account.account_type,
                       int(get_env("BIG_QMT_BRIDGE_PORT", "9001")))
    client = BridgeClient(gateway, bridge)
    return AdapterBundle(data_adapter=BridgeDataAdapter(client) if config.enable_data else None,
                         broker_adapter=BridgeBrokerAdapter(config, router, client) if config.enable_broker else None)
