"""Feishu interactive cards for strategy order and fill notifications."""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from queue import Queue
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
    title: Optional[str] = None


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
            "RECONCILIATION_BLOCKED": "red",
            "ERROR": "red",
        }.get(notification.event.upper(), "orange")
        title = notification.title or {
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


class FeishuNotifier:
    """Drop-in replacement for the legacy bt_quant notifier.

    Existing ``FeishuNotifier().queue_message(text)`` calls remain valid. New
    code can pass a :class:`TradeNotification` to ``queue_message`` or call
    ``send_trade`` directly.
    """

    WEBHOOK_URL = ""
    SECRET = ""
    MAX_MSG_LENGTH = 10_000
    MAX_MSG_PER_MINUTE = 10
    FLUSH_INTERVAL = 30

    def __init__(
        self,
        webhook_url: Optional[str] = None,
        secret: Optional[str] = None,
    ) -> None:
        self.webhook_url = (
            webhook_url
            or os.environ.get("FEISHU_WEBHOOK_URL", "")
            or self.WEBHOOK_URL
        )
        self.secret = (
            secret
            if secret is not None
            else os.environ.get("FEISHU_SIGNING_SECRET", self.SECRET)
        )
        self._sender = (
            FeishuTradeNotifier(self.webhook_url, self.secret)
            if self.webhook_url
            else None
        )
        self._message_queue: Queue = Queue()
        self._last_flush_minute: Optional[str] = None
        self._sent_this_minute = 0
        self._lock = threading.Lock()
        self._start_auto_flush()

    def _start_auto_flush(self) -> None:
        def loop() -> None:
            while True:
                time.sleep(self.FLUSH_INTERVAL)
                self.flush()

        threading.Thread(target=loop, daemon=True).start()

    def _builder(self) -> FeishuTradeNotifier:
        return self._sender or FeishuTradeNotifier(
            "https://example.invalid/disabled", self.secret
        )

    def _text_notification(
        self, content: str, title: str = "量化日志通知"
    ) -> TradeNotification:
        return TradeNotification(
            event="MESSAGE",
            security="-",
            side="-",
            status="INFO",
            detail=content,
            title=title,
        )

    def _build_payload(
        self, content: Union[str, TradeNotification], msg_type: str = "interactive"
    ) -> Dict[str, Any]:
        del msg_type
        notification = (
            content if isinstance(content, TradeNotification)
            else self._text_notification(content)
        )
        return self._builder().build_payload(notification)

    def _send_request(self, data: Dict[str, Any]) -> bool:
        if not self.webhook_url:
            return False
        try:
            response = requests.post(
                self.webhook_url,
                json=data,
                timeout=10,
            )
            response.raise_for_status()
            result = response.json()
            return int(result.get("code", result.get("StatusCode", -1))) == 0
        except Exception:
            return False

    def send_trade(self, notification: TradeNotification) -> bool:
        return bool(self._sender and self._sender.send(notification))

    def send_text(
        self, content: str, mentioned_list: Optional[list] = None
    ) -> bool:
        if mentioned_list:
            mentions = " ".join(
                "<at user_id='{}'></at>".format(user_id)
                for user_id in mentioned_list
            )
            content = "{}\n{}".format(content, mentions)
        return self._send_request(self._build_payload(content))

    def send_rich_text(self, title: str, content: str) -> bool:
        notification = self._text_notification(content, title=title)
        return self._send_request(self._build_payload(notification))

    def queue_message(self, content: Union[str, TradeNotification]) -> None:
        self._message_queue.put(content)

    def _split_message(self, content: str) -> list:
        return [
            content[index:index + self.MAX_MSG_LENGTH]
            for index in range(0, len(content), self.MAX_MSG_LENGTH)
        ] or [""]

    def flush(self) -> None:
        with self._lock:
            minute = datetime.now().strftime("%Y-%m-%d %H:%M")
            if minute != self._last_flush_minute:
                self._last_flush_minute = minute
                self._sent_this_minute = 0
            while (
                not self._message_queue.empty()
                and self._sent_this_minute < self.MAX_MSG_PER_MINUTE
            ):
                item = self._message_queue.get()
                if isinstance(item, TradeNotification):
                    self.send_trade(item)
                    self._sent_this_minute += 1
                    continue
                for segment in self._split_message(str(item).strip()):
                    if self._sent_this_minute >= self.MAX_MSG_PER_MINUTE:
                        break
                    self.send_text(segment)
                    self._sent_this_minute += 1


def money_units_to_display(units: int) -> Decimal:
    return Decimal(units) / Decimal(MONEY_SCALE)


def price_units_to_display(units: int) -> Decimal:
    return Decimal(units) / Decimal(PRICE_SCALE)
