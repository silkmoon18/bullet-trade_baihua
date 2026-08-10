"""Feishu interactive cards for strategy order and fill notifications."""

from __future__ import annotations

import base64
import hashlib
import hmac
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Dict, Optional, Union

import requests  # type: ignore[import-untyped]


Number = Union[str, int, float, Decimal]
SHANGHAI_TZ = timezone(timedelta(hours=8), "Asia/Shanghai")
MONEY_SCALE = 10_000
PRICE_SCALE = 1_000_000


@dataclass(frozen=True)
class TradeNotification:
    event: str
    security: str
    side: str
    status: str
    quantity: Optional[int] = None
    price: Optional[Number] = None
    amount: Optional[Number] = None
    order_id: Optional[str] = None
    trade_id: Optional[str] = None
    detail: str = ""
    occurred_at: Optional[datetime] = None


def _signature(timestamp: int, secret: str) -> str:
    text = "{}\n{}".format(timestamp, secret)
    digest = hmac.new(
        text.encode("utf-8"), msg=b"", digestmod=hashlib.sha256
    ).digest()
    return base64.b64encode(digest).decode("utf-8")


def _display(value: Optional[Number], digits: int = 2) -> str:
    if value is None:
        return "-"
    try:
        number = Decimal(str(value))
    except Exception:
        return str(value)
    return "{:.{}f}".format(number, digits)


class FeishuTradeNotifier:
    def __init__(
        self,
        webhook_url: str,
        secret: str = "",
        timeout_seconds: float = 10.0,
    ) -> None:
        if not webhook_url:
            raise ValueError("Feishu webhook_url cannot be empty")
        self.webhook_url = webhook_url
        self.secret = secret
        self.timeout_seconds = timeout_seconds

    def build_payload(self, notification: TradeNotification) -> Dict[str, Any]:
        color = {
            "ORDER_SUBMITTED": "blue",
            "FILL": "green",
            "FILLED": "green",
            "PARTIALLY_FILLED": "turquoise",
            "CANCELED": "grey",
            "REJECTED": "red",
            "ERROR": "red",
        }.get(notification.event.upper(), "orange")
        title = {
            "ORDER_SUBMITTED": "交易委托已提交",
            "FILL": "收到成交回报",
            "FILLED": "订单全部成交",
            "PARTIALLY_FILLED": "订单部分成交",
            "CANCELED": "订单已撤销",
            "REJECTED": "订单被拒绝",
        }.get(notification.event.upper(), "量化交易通知")
        occurred_at = notification.occurred_at or datetime.now(SHANGHAI_TZ)
        if occurred_at.tzinfo is not None:
            occurred_at = occurred_at.astimezone(SHANGHAI_TZ)
        lines = [
            "**标的：** `{}`".format(notification.security or "-"),
            "**方向：** {}　　**状态：** {}".format(
                notification.side or "-", notification.status or "-"
            ),
            "**金额：** ¥{}　　**数量：** {}".format(
                _display(notification.amount, 2),
                notification.quantity if notification.quantity is not None else "-",
            ),
            "**单价：** ¥{}　　**时间：** {}".format(
                _display(notification.price, 4),
                occurred_at.strftime("%Y-%m-%d %H:%M:%S"),
            ),
        ]
        if notification.order_id:
            lines.append("**订单号：** `{}`".format(notification.order_id))
        if notification.trade_id:
            lines.append("**成交号：** `{}`".format(notification.trade_id))
        if notification.detail:
            lines.append("**说明：** {}".format(notification.detail))
        payload: Dict[str, Any] = {
            "msg_type": "interactive",
            "card": {
                "config": {"wide_screen_mode": True},
                "header": {
                    "template": color,
                    "title": {"tag": "plain_text", "content": title},
                },
                "elements": [
                    {
                        "tag": "div",
                        "text": {"tag": "lark_md", "content": "\n".join(lines)},
                    }
                ],
            },
        }
        if self.secret:
            timestamp = int(time.time())
            payload["timestamp"] = str(timestamp)
            payload["sign"] = _signature(timestamp, self.secret)
        return payload

    def send(self, notification: TradeNotification) -> bool:
        try:
            response = requests.post(
                self.webhook_url,
                json=self.build_payload(notification),
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
            result = response.json()
            return int(result.get("code", result.get("StatusCode", -1))) == 0
        except Exception:
            return False


def money_units_to_display(units: int) -> Decimal:
    return Decimal(units) / Decimal(MONEY_SCALE)


def price_units_to_display(units: int) -> Decimal:
    return Decimal(units) / Decimal(PRICE_SCALE)
