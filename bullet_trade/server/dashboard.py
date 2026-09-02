"""Read-only HTTP surface for the personal BulletTrade strategy dashboard."""

from __future__ import annotations

import asyncio
import hmac
import json
import re
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Awaitable, Callable, Dict, List, Mapping, Optional
from urllib.parse import parse_qs, urlsplit

from bullet_trade.server.strategy.domain import MONEY_SCALE, NAV_SCALE, PRICE_SCALE
from bullet_trade.server.strategy.schema import connect_database


DashboardProvider = Callable[[Optional[str], int], Awaitable[Mapping[str, object]]]


def _scaled(value: object, scale: int) -> Optional[int]:
    if value is None:
        return None
    return int(
        (Decimal(str(value)) * Decimal(scale)).quantize(
            Decimal("1"), rounding=ROUND_HALF_UP
        )
    )


def _money(value: Optional[int]) -> Optional[float]:
    return None if value is None else value / MONEY_SCALE


def _price(value: Optional[int]) -> Optional[float]:
    return None if value is None else value / PRICE_SCALE


def _json_object(raw: object) -> Dict[str, object]:
    try:
        value = json.loads(str(raw or "{}"))
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


class DashboardReadModel:
    """Small SQLite read model used only by the monitoring website."""

    def __init__(self, database_path: str):
        self.database_path = Path(database_path)

    def list_strategies(self) -> List[Dict[str, object]]:
        connection = connect_database(self.database_path)
        try:
            rows = connection.execute(
                """
                SELECT strategy_account_id, strategy_id, physical_account_id,
                       initial_capital_units, cash_units, reserved_cash_units,
                       ledger_version, event_seq, status, created_at, updated_at
                FROM strategy_accounts
                ORDER BY strategy_id
                """
            ).fetchall()
            return [
                {
                    "account_id": row["strategy_account_id"],
                    "strategy_id": row["strategy_id"],
                    "physical_account_id": row["physical_account_id"],
                    "initial_capital": _money(row["initial_capital_units"]),
                    "cash": _money(row["cash_units"]),
                    "reserved_cash": _money(row["reserved_cash_units"]),
                    "ledger_version": row["ledger_version"],
                    "event_seq": row["event_seq"],
                    "status": row["status"],
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"],
                }
                for row in rows
            ]
        finally:
            connection.close()

    def strategy_activity(
        self, strategy_id: str, limit: int = 100
    ) -> Dict[str, object]:
        limit = max(1, min(int(limit), 500))
        connection = connect_database(self.database_path)
        try:
            account = connection.execute(
                "SELECT * FROM strategy_accounts WHERE strategy_id = ?",
                (strategy_id,),
            ).fetchone()
            if account is None:
                raise KeyError("strategy not found: {}".format(strategy_id))
            account_id = str(account["strategy_account_id"])
            intent_rows = connection.execute(
                """
                SELECT intent_id, idempotency_key, state, targets_json,
                       created_at, updated_at
                FROM portfolio_intents
                WHERE strategy_account_id = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (account_id, min(limit, 50)),
            ).fetchall()
            intents = []
            security_names: Dict[str, str] = {}
            for row in intent_rows:
                targets = _json_object(row["targets_json"])
                raw_names = targets.get("security_names")
                if isinstance(raw_names, dict):
                    for security, name in raw_names.items():
                        if security and name:
                            security_names[str(security)] = str(name)
                weights = targets.get("weights_ppm", {})
                intents.append(
                    {
                        "intent_id": row["intent_id"],
                        "idempotency_key": row["idempotency_key"],
                        "state": row["state"],
                        "trading_day": targets.get("trading_day"),
                        "weights": {
                            str(key): float(value) / NAV_SCALE
                            for key, value in weights.items()
                        }
                        if isinstance(weights, dict)
                        else {},
                        "target_quantities": targets.get("target_quantities", {}),
                        "reference_prices": {
                            str(key): _price(int(value))
                            for key, value in targets.get(
                                "reference_prices_units", {}
                            ).items()
                        }
                        if isinstance(targets.get("reference_prices_units"), dict)
                        else {},
                        "execution": targets.get("execution_request", {}),
                        "created_at": row["created_at"],
                        "updated_at": row["updated_at"],
                    }
                )

            order_rows = connection.execute(
                """
                SELECT order_id, intent_id, client_tag, broker_order_id,
                       security, side, requested_qty, filled_qty,
                       limit_price_units, state, trading_day,
                       submitted_at, terminal_at, created_at, updated_at
                FROM strategy_orders
                WHERE strategy_account_id = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (account_id, limit),
            ).fetchall()
            orders = [
                {
                    "order_id": row["order_id"],
                    "intent_id": row["intent_id"],
                    "client_tag": row["client_tag"],
                    "broker_order_id": row["broker_order_id"],
                    "security": row["security"],
                    "name": security_names.get(str(row["security"]), ""),
                    "side": row["side"],
                    "requested_qty": row["requested_qty"],
                    "filled_qty": row["filled_qty"],
                    "limit_price": _price(row["limit_price_units"]),
                    "state": row["state"],
                    "trading_day": row["trading_day"],
                    "submitted_at": row["submitted_at"],
                    "terminal_at": row["terminal_at"],
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"],
                }
                for row in order_rows
            ]

            fill_rows = connection.execute(
                """
                SELECT f.fill_id, f.order_id, f.broker_trade_id,
                       f.security, f.side, f.quantity, f.price_units,
                       f.commission_units, f.tax_units,
                       f.commission_known, f.tax_known,
                       f.price_source, f.price_known,
                       f.traded_at, f.booked_at, o.client_tag
                FROM fills f
                JOIN strategy_orders o ON o.order_id = f.order_id
                WHERE o.strategy_account_id = ?
                ORDER BY f.traded_at DESC
                LIMIT ?
                """,
                (account_id, limit),
            ).fetchall()
            fills = []
            for row in fill_rows:
                price = _price(row["price_units"])
                fills.append(
                    {
                        "fill_id": row["fill_id"],
                        "order_id": row["order_id"],
                        "broker_trade_id": row["broker_trade_id"],
                        "client_tag": row["client_tag"],
                        "security": row["security"],
                        "name": security_names.get(str(row["security"]), ""),
                        "side": row["side"],
                        "quantity": row["quantity"],
                        "price": price,
                        "amount": (price or 0.0) * int(row["quantity"]),
                        "commission": _money(row["commission_units"])
                        if row["commission_known"]
                        else None,
                        "tax": _money(row["tax_units"])
                        if row["tax_known"]
                        else None,
                        "fees_known": bool(
                            row["commission_known"] and row["tax_known"]
                        ),
                        "price_known": bool(row["price_known"]),
                        "price_source": row["price_source"],
                        "traded_at": row["traded_at"],
                        "booked_at": row["booked_at"],
                    }
                )

            history_rows = connection.execute(
                """
                SELECT as_of, total_value_units, cash_units,
                       positions_value_units, total_pnl_units,
                       nav_units, fees_units, performance_ready
                FROM dashboard_snapshots
                WHERE strategy_account_id = ?
                ORDER BY as_of ASC
                LIMIT 2000
                """,
                (account_id,),
            ).fetchall()
            history = [
                {
                    "as_of": row["as_of"],
                    "total_value": _money(row["total_value_units"]),
                    "cash": _money(row["cash_units"]),
                    "positions_value": _money(row["positions_value_units"]),
                    "total_pnl": _money(row["total_pnl_units"]),
                    "nav": None
                    if row["nav_units"] is None
                    else row["nav_units"] / NAV_SCALE,
                    "fees": _money(row["fees_units"]),
                    "performance_ready": bool(row["performance_ready"]),
                }
                for row in history_rows
            ]
            return {
                "account_id": account_id,
                "physical_account_id": account["physical_account_id"],
                "security_names": security_names,
                "intents": intents,
                "orders": orders,
                "fills": fills,
                "history": history,
            }
        finally:
            connection.close()

    def record_snapshot(self, snapshot: Mapping[str, object]) -> None:
        account_id = str(snapshot.get("account_id") or "").strip()
        as_of_text = str(snapshot.get("as_of") or "").strip()
        if not account_id or not as_of_text:
            return
        as_of = datetime.fromisoformat(as_of_text)
        snapshot_minute = as_of.replace(second=0, microsecond=0).isoformat()
        connection = connect_database(self.database_path)
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO dashboard_snapshots(
                    strategy_account_id, snapshot_minute, as_of,
                    total_value_units, cash_units, positions_value_units,
                    total_pnl_units, nav_units, fees_units, performance_ready
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(strategy_account_id, snapshot_minute) DO UPDATE SET
                    as_of = excluded.as_of,
                    total_value_units = excluded.total_value_units,
                    cash_units = excluded.cash_units,
                    positions_value_units = excluded.positions_value_units,
                    total_pnl_units = excluded.total_pnl_units,
                    nav_units = excluded.nav_units,
                    fees_units = excluded.fees_units,
                    performance_ready = excluded.performance_ready
                """,
                (
                    account_id,
                    snapshot_minute,
                    as_of.isoformat(),
                    _scaled(snapshot.get("total_value", 0), MONEY_SCALE) or 0,
                    _scaled(snapshot.get("cash", 0), MONEY_SCALE) or 0,
                    _scaled(snapshot.get("positions_value", 0), MONEY_SCALE) or 0,
                    _scaled(snapshot.get("total_pnl"), MONEY_SCALE),
                    _scaled(snapshot.get("nav"), NAV_SCALE),
                    _scaled(snapshot.get("fees"), MONEY_SCALE),
                    1 if snapshot.get("performance_ready") else 0,
                ),
            )
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()


_SECRET_PATTERN = re.compile(
    r"(?i)(token|secret|password)(\s*[=:]\s*)([^\s,;]+)"
)
_FEISHU_HOOK_PATTERN = re.compile(
    r"https://open\.feishu\.cn/open-apis/bot/v2/hook/[^\s]+",
    re.IGNORECASE,
)


def tail_log_file(path: Optional[str], limit: int = 300) -> List[str]:
    if not path:
        return []
    target = Path(path)
    if not target.is_file():
        return []
    limit = max(1, min(int(limit), 1000))
    with target.open("rb") as handle:
        handle.seek(0, 2)
        size = handle.tell()
        handle.seek(max(0, size - 512 * 1024))
        content = handle.read().decode("utf-8", errors="replace")
    lines = content.splitlines()[-limit:]
    return [
        _FEISHU_HOOK_PATTERN.sub(
            "https://open.feishu.cn/open-apis/bot/v2/hook/[REDACTED]",
            _SECRET_PATTERN.sub(r"\1\2[REDACTED]", line),
        )
        for line in lines
    ]


class DashboardHTTPServer:
    """Minimal GET-only HTTP server with bearer-token authentication."""

    def __init__(
        self,
        listen: str,
        port: int,
        token: str,
        provider: DashboardProvider,
    ):
        if not token:
            raise ValueError("dashboard token is required")
        self.listen = listen
        self.port = int(port)
        self.token = token
        self.provider = provider
        self.server: Optional[asyncio.AbstractServer] = None

    @property
    def bound_port(self) -> int:
        if self.server and self.server.sockets:
            return int(self.server.sockets[0].getsockname()[1])
        return self.port

    async def start(self) -> None:
        self.server = await asyncio.start_server(
            self._handle_client, self.listen, self.port
        )

    async def stop(self) -> None:
        if self.server is not None:
            self.server.close()
            await self.server.wait_closed()
            self.server = None

    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        status = 500
        payload: Mapping[str, object] = {"error": "internal server error"}
        try:
            header_bytes = await asyncio.wait_for(
                reader.readuntil(b"\r\n\r\n"), timeout=5.0
            )
            if len(header_bytes) > 32 * 1024:
                raise ValueError("request headers too large")
            lines = header_bytes.decode("iso-8859-1").split("\r\n")
            method, target, _ = lines[0].split(" ", 2)
            headers = {}
            for line in lines[1:]:
                if ":" in line:
                    key, value = line.split(":", 1)
                    headers[key.strip().lower()] = value.strip()
            if method != "GET":
                status, payload = 405, {"error": "method not allowed"}
            elif not self._authorized(headers.get("authorization", "")):
                status, payload = 401, {"error": "unauthorized"}
            else:
                parsed = urlsplit(target)
                query = parse_qs(parsed.query)
                strategy_id = str(query.get("strategy_id", [""])[0]).strip() or None
                log_limit = int(query.get("log_limit", ["300"])[0])
                if parsed.path == "/health":
                    result = await self.provider(None, 0)
                    payload = {
                        "ok": True,
                        "service": "bullet-trade-dashboard",
                        "server": result.get("server", {}),
                    }
                    status = 200
                elif parsed.path == "/api/v1/dashboard":
                    payload = await self.provider(strategy_id, log_limit)
                    status = 200
                else:
                    status, payload = 404, {"error": "not found"}
        except (asyncio.IncompleteReadError, asyncio.LimitOverrunError):
            status, payload = 400, {"error": "bad request"}
        except (TypeError, ValueError):
            status, payload = 400, {"error": "bad request"}
        except KeyError as exc:
            status, payload = 404, {"error": str(exc)}
        except Exception:
            status, payload = 503, {"error": "dashboard data unavailable"}
        body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        reason = {
            200: "OK",
            400: "Bad Request",
            401: "Unauthorized",
            404: "Not Found",
            405: "Method Not Allowed",
            500: "Internal Server Error",
            503: "Service Unavailable",
        }.get(status, "Error")
        writer.write(
            (
                "HTTP/1.1 {} {}\r\n"
                "Content-Type: application/json; charset=utf-8\r\n"
                "Content-Length: {}\r\n"
                "Cache-Control: no-store\r\n"
                "X-Content-Type-Options: nosniff\r\n"
                "Connection: close\r\n\r\n"
            ).format(status, reason, len(body)).encode("ascii")
            + body
        )
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    def _authorized(self, authorization: str) -> bool:
        scheme, separator, value = authorization.partition(" ")
        return bool(
            separator
            and scheme.lower() == "bearer"
            and hmac.compare_digest(value.strip(), self.token)
        )
