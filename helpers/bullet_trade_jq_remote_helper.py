# -*- coding: utf-8 -*-
"""BulletTrade 聚宽策略运行 helper（L00 精简版）。

上传到聚宽研究根目录后，策略通过版本化入口安装运行模式：

    import bullet_trade_jq_remote_helper as bt

    state = bt.install_strategy_runtime(
        globals(),
        context=context,
        profile=PROFILE,
        mode="BACKTEST",
        strategy_id=STRATEGY_ID,
        validate_remote=True,
    )

职责边界：

- 版本校验：helper marker、运行时 API 版本和 profile schema 版本固定校验，
  不匹配即失败关闭。
- 模式校验：BACKTEST保留聚宽历史回测；JQ在聚宽模拟盘调用原生下单
  并通过服务器发送计划通知；QMT_REMOTE阻断聚宽原生交易函数，真实目标只
  允许经StrategyLedger接口提交。
- 冷启动升级：helper/config/策略文件变更必须先停止聚宽策略、确认旧进程
  退出，再由平台启动全新进程。同一进程内重复安装仅在签名完全一致时幂等
  返回；签名漂移或检测到上一代 helper 遗留记录即失败关闭。
- 本 helper 运行在用户自有、可信的策略进程中，不防御同进程恶意 Python
  代码、monkey patch 或热重载攻击（docs/live-ledger/02-decisions.md D021）。
"""

import json
import math
import socket
import ssl
import struct
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, Optional, Tuple

__all__ = [
    "STRATEGY_RUNTIME_API_VERSION",
    "STRATEGY_RUNTIME_HELPER_MARKER",
    "PROFILE_SCHEMA_VERSION",
    "ExecutionType",
    "RuntimeMode",
    "FollowUpPolicy",
    "RepricingPolicy",
    "ConditionalLimitPriceMode",
    "LimitExecution",
    "ConditionalLimitExecution",
    "MarketExecution",
    "MarketableLimitExecution",
    "ExecutionRequest",
    "PortfolioView",
    "PositionView",
    "JoinQuantRuntime",
    "install_joinquant_runtime",
    "install_strategy_runtime",
    "ensure_account",
    "get_portfolio",
    "submit_targets",
    "notify_target_buy_plan",
    "get_intent",
    "get_reconciliation",
    "get_configured_execution_mode",
    "runtime_portfolio",
    "ensure_runtime_ready",
    "submit_runtime_targets",
    "advance_runtime_targets",
    "cancel_runtime_targets",
    "cancel_runtime_orders",
    "runtime_order_target",
    "runtime_order_target_value",
]

STRATEGY_RUNTIME_API_VERSION = 7
STRATEGY_RUNTIME_HELPER_MARKER = "bullet-trade-joinquant-runtime-helper-v7"
PROFILE_SCHEMA_VERSION = 1
EXECUTION_WIRE_SCHEMA_VERSION = 1

DEFAULT_RPC_TIMEOUT_SECONDS = 60.0
_RPC_ATTEMPTS = 3
_RPC_RETRY_INTERVAL_SECONDS = 0.5

# 策略 namespace 中的安装记录键；用于识别上一代 helper 遗留状态。
_RUNTIME_STATE_KEY = "__bt_strategy_runtime_state__"

_RUNTIME_MUTATION_NAMES = frozenset(
    {
        "order",
        "order_value",
        "order_percent",
        "order_target",
        "order_target_value",
        "order_target_percent",
        "cancel_order",
    }
)

_PROFILE_REQUIRED_FIELDS = frozenset({"strategy_id", "host", "token"})
_PROFILE_OPTIONAL_FIELDS = frozenset(
    {
        "port",
        "account_key",
        "tls_cert",
        "rpc_timeout",
    }
)
_PROFILE_ALLOWED_FIELDS = _PROFILE_REQUIRED_FIELDS | _PROFILE_OPTIONAL_FIELDS

# 本代 helper 实例标记；namespace 记录中的 token 不同即为上一代遗留。
_MODULE_TOKEN = object()

# 模块级安装记录：同一进程只允许一种安装签名。
_active_signature = None  # type: Optional[Tuple[Any, ...]]
_active_state = None  # type: Optional[Dict[str, Any]]
_active_profile = None  # type: Optional[Dict[str, Any]]
_active_namespace = None  # type: Optional[Dict[str, Any]]
_runtime_target_state = None  # type: Optional[Dict[str, Any]]


class ExecutionType(str, Enum):
    LIMIT = "LIMIT"
    CONDITIONAL_LIMIT = "CONDITIONAL_LIMIT"
    MARKET = "MARKET"
    MARKETABLE_LIMIT = "MARKETABLE_LIMIT"


class RuntimeMode(str, Enum):
    BACKTEST = "BACKTEST"
    JQ = "JQ"
    QMT_REMOTE = "QMT_REMOTE"


class FollowUpPolicy(str, Enum):
    NONE = "NONE"
    UNTIL_FILLED_TODAY = "UNTIL_FILLED_TODAY"


class RepricingPolicy(str, Enum):
    KEEP_ORIGINAL = "KEEP_ORIGINAL"
    RECOMPUTE = "RECOMPUTE"


class ConditionalLimitPriceMode(str, Enum):
    BOUNDARY = "BOUNDARY"
    COUNTERPARTY = "COUNTERPARTY"


def _check_band(value: int, field_name: str) -> None:
    if type(value) is not int or not 0 <= value <= 100_000:
        raise ValueError("{}必须是0到100000之间的整数".format(field_name))


@dataclass(frozen=True)
class LimitExecution:
    price_band_ppm: int = 0
    execution_type: ExecutionType = field(
        default=ExecutionType.LIMIT, init=False
    )

    def __post_init__(self) -> None:
        _check_band(self.price_band_ppm, "price_band_ppm")


@dataclass(frozen=True)
class ConditionalLimitExecution:
    price_band_ppm: int = 2_000
    price_mode: ConditionalLimitPriceMode = ConditionalLimitPriceMode.BOUNDARY
    execution_type: ExecutionType = field(
        default=ExecutionType.CONDITIONAL_LIMIT, init=False
    )

    def __post_init__(self) -> None:
        _check_band(self.price_band_ppm, "price_band_ppm")
        if type(self.price_mode) is not ConditionalLimitPriceMode:
            raise TypeError("price_mode必须是ConditionalLimitPriceMode")


@dataclass(frozen=True)
class MarketExecution:
    protect_price_band_ppm: int = 15_000
    execution_type: ExecutionType = field(
        default=ExecutionType.MARKET, init=False
    )

    def __post_init__(self) -> None:
        _check_band(self.protect_price_band_ppm, "protect_price_band_ppm")


@dataclass(frozen=True)
class MarketableLimitExecution:
    price_band_ppm: int = 15_000
    execution_type: ExecutionType = field(
        default=ExecutionType.MARKETABLE_LIMIT, init=False
    )

    def __post_init__(self) -> None:
        _check_band(self.price_band_ppm, "price_band_ppm")


@dataclass(frozen=True)
class ExecutionRequest:
    style: Any = field(default_factory=lambda: LimitExecution(2_000))
    follow_up: FollowUpPolicy = FollowUpPolicy.UNTIL_FILLED_TODAY
    repricing: RepricingPolicy = RepricingPolicy.KEEP_ORIGINAL

    def __post_init__(self) -> None:
        if not isinstance(
            self.style,
            (
                LimitExecution,
                ConditionalLimitExecution,
                MarketExecution,
                MarketableLimitExecution,
            ),
        ):
            raise TypeError("style不是支持的执行类型")
        if type(self.follow_up) is not FollowUpPolicy:
            raise TypeError("follow_up必须是FollowUpPolicy")
        if type(self.repricing) is not RepricingPolicy:
            raise TypeError("repricing必须是RepricingPolicy")


def _execution_to_wire(request: ExecutionRequest) -> Dict[str, Any]:
    if type(request) is not ExecutionRequest:
        raise TypeError("execution必须是ExecutionRequest")
    style = request.style
    style_wire = {"type": style.execution_type.value}
    if isinstance(style, (LimitExecution, MarketableLimitExecution)):
        style_wire["price_band_ppm"] = style.price_band_ppm
    elif isinstance(style, ConditionalLimitExecution):
        style_wire["price_band_ppm"] = style.price_band_ppm
        style_wire["price_mode"] = style.price_mode.value
    else:
        style_wire["protect_price_band_ppm"] = style.protect_price_band_ppm
    return {
        "schema_version": EXECUTION_WIRE_SCHEMA_VERSION,
        "style": style_wire,
        "follow_up": request.follow_up.value,
        "repricing": request.repricing.value,
    }


def _execution_from_wire(value: Dict[str, Any]) -> ExecutionRequest:
    if type(value) is not dict or value.get("schema_version") != 1:
        raise RuntimeError("服务器执行请求版本无效")
    raw_style = value.get("style")
    if type(raw_style) is not dict:
        raise RuntimeError("服务器执行类型无效")
    try:
        execution_type = ExecutionType(raw_style.get("type"))
        if execution_type is ExecutionType.LIMIT:
            style = LimitExecution(int(raw_style["price_band_ppm"]))
        elif execution_type is ExecutionType.CONDITIONAL_LIMIT:
            style = ConditionalLimitExecution(
                int(raw_style["price_band_ppm"]),
                ConditionalLimitPriceMode(raw_style["price_mode"]),
            )
        elif execution_type is ExecutionType.MARKET:
            style = MarketExecution(
                int(raw_style["protect_price_band_ppm"])
            )
        else:
            style = MarketableLimitExecution(
                int(raw_style["price_band_ppm"])
            )
        return ExecutionRequest(
            style=style,
            follow_up=FollowUpPolicy(value["follow_up"]),
            repricing=RepricingPolicy(value["repricing"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError("服务器执行请求内容无效") from exc


class _AmbiguousRequestError(RuntimeError):
    """请求可能已执行但响应丢失，禁止在当前调用中盲目重发。"""


class _ServerResponseError(RuntimeError):
    """服务端明确拒绝请求，结果是确定的。"""


class PositionView(object):
    """聚宽策略可直接读取的真实持仓只读视图。"""

    def __init__(self, payload: Dict[str, Any]) -> None:
        self.security = payload["security"]
        self.total_amount = int(payload["total_amount"])
        self.closeable_amount = int(payload["closeable_amount"])
        self.avg_cost = float(payload["avg_cost"])
        self.price = float(payload["price"])
        self.value = float(payload["value"])
        self.unrealized_pnl = float(payload.get("unrealized_pnl", 0.0))


class PortfolioView(object):
    """由真实成交账本生成，不修改聚宽原生模拟账户。"""

    def __init__(self, payload: Dict[str, Any]) -> None:
        self.account_id = payload["account_id"]
        self.as_of = payload["as_of"]
        self.snapshot_version = payload["snapshot_version"]
        self.ledger_version = int(payload["ledger_version"])
        self.cash = float(payload["cash"])
        self.reserved_cash = float(payload["reserved_cash"])
        self.available_cash = float(payload["available_cash"])
        self.positions_value = float(payload["positions_value"])
        self.total_value = float(payload["total_value"])
        self.starting_cash = float(payload["starting_cash"])
        self.total_pnl = float(payload["total_pnl"])
        self.realized_pnl = float(payload["realized_pnl"])
        self.unrealized_pnl = float(payload["unrealized_pnl"])
        self.fees = float(payload["fees"])
        self.nav = float(payload["nav"])
        self.returns = float(payload["returns"])
        self.performance_ready = bool(payload["performance_ready"])
        self.positions = {
            security: PositionView(item)
            for security, item in payload.get("positions", {}).items()
        }


def _json_default(value: Any) -> Any:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if hasattr(value, "item"):
        return value.item()
    raise TypeError("unsupported JSON value: {}".format(type(value).__name__))


def _recv_exact(sock: socket.socket, size: int) -> bytes:
    chunks = []
    remaining = size
    while remaining:
        chunk = sock.recv(remaining)
        if not chunk:
            raise RuntimeError("服务器连接提前关闭")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _send_message(sock: socket.socket, message: Dict[str, Any]) -> None:
    body = json.dumps(
        message, ensure_ascii=False, default=_json_default
    ).encode("utf-8")
    sock.sendall(struct.pack(">I", len(body)) + body)


def _read_message(sock: socket.socket) -> Dict[str, Any]:
    size = struct.unpack(">I", _recv_exact(sock, 4))[0]
    if size > 32 * 1024 * 1024:
        raise RuntimeError("服务器响应过大")
    result = json.loads(_recv_exact(sock, size).decode("utf-8"))
    if not isinstance(result, dict):
        raise RuntimeError("服务器响应格式无效")
    return result


def _strategy_request(action: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    if _active_profile is None or _active_state is None:
        raise RuntimeError("策略运行时尚未安装")
    profile = _active_profile
    timeout = float(profile["rpc_timeout"])
    connect_timeout = min(timeout, 10.0)
    safe_retry = action not in (
        "strategy.submit_targets",
        "strategy.cancel_intent",
        "strategy.notify_target_buy_plan",
    )
    last_error = None  # type: Optional[Exception]

    for attempt in range(1, _RPC_ATTEMPTS + 1):
        raw_sock = None
        sock = None
        phase = "连接"
        request_may_have_been_sent = False
        try:
            raw_sock = socket.create_connection(
                (profile["host"], profile["port"]), timeout=connect_timeout
            )
            sock = raw_sock
            tls_cert = profile.get("tls_cert")
            if tls_cert:
                phase = "TLS握手"
                context = ssl.create_default_context(cafile=tls_cert)
                sock = context.wrap_socket(
                    raw_sock, server_hostname=profile["host"]
                )
            sock.settimeout(timeout)

            phase = "应用握手"
            _send_message(
                sock,
                {
                    "type": "handshake",
                    "token": profile["token"],
                    "protocol": 1,
                    "features": ["strategy_ledger_v1"],
                    "account_key": profile.get("account_key"),
                },
            )
            handshake = _read_message(sock)
            if handshake.get("type") != "handshake_ack":
                raise RuntimeError("服务器握手失败")

            request_id = uuid.uuid4().hex
            request_payload = dict(payload)
            request_payload["strategy_id"] = _active_state["strategy_id"]
            if profile.get("account_key"):
                request_payload["account_key"] = profile["account_key"]

            phase = "发送请求"
            # sendall 失败时也可能已经发送了部分数据；有副作用的请求必须按未知结果处理。
            request_may_have_been_sent = True
            _send_message(
                sock,
                {
                    "type": "request",
                    "id": request_id,
                    "action": action,
                    "payload": request_payload,
                },
            )
            phase = "接收响应"
            response = _read_message(sock)
            if response.get("type") == "error":
                raise _ServerResponseError(
                    "{}: {}".format(
                        response.get("code", "REQUEST_FAILED"),
                        response.get("message", "server error"),
                    )
                )
            if (
                response.get("type") != "response"
                or response.get("id") != request_id
            ):
                raise RuntimeError("服务器响应与请求不匹配")
            result = response.get("payload")
            if not isinstance(result, dict):
                raise RuntimeError("服务器响应payload无效")
            return result
        except _ServerResponseError as exc:
            raise RuntimeError(str(exc)) from None
        except Exception as exc:
            if not safe_retry and request_may_have_been_sent:
                raise _AmbiguousRequestError(
                    "{}在{}阶段失败：请求可能已执行，已停止自动重发；"
                    "请使用原idempotency_key查询意图和对账结果".format(
                        action, phase
                    )
                ) from None
            last_error = exc
            if attempt < _RPC_ATTEMPTS:
                print(
                    "[BulletTrade RPC] {}阶段失败（第{}/{}次），0.5秒后重试: {}".format(
                        phase, attempt, _RPC_ATTEMPTS, type(exc).__name__
                    )
                )
                time.sleep(_RPC_RETRY_INTERVAL_SECONDS)
        finally:
            if sock is not None:
                try:
                    sock.close()
                except Exception:
                    pass

    raise RuntimeError(
        "{}请求失败：{}阶段连续尝试{}次仍未成功（{}）".format(
            action, phase, _RPC_ATTEMPTS,
            type(last_error).__name__ if last_error is not None else "unknown",
        )
    ) from None


def ensure_account(initial_capital: Any = "10000") -> Dict[str, Any]:
    """校验真实账户资金并幂等建立策略账户。"""

    return _strategy_request(
        "strategy.ensure_account", {"initial_capital": initial_capital}
    )


def get_portfolio(
    marks: Optional[Dict[str, Any]] = None,
    as_of: Any = None,
) -> PortfolioView:
    payload = {}  # type: Dict[str, Any]
    if marks is not None:
        payload["marks"] = marks
    if as_of is not None:
        payload["as_of"] = as_of
    return PortfolioView(_strategy_request("strategy.get_snapshot", payload))


def submit_targets(
    weights: Dict[str, Any],
    idempotency_key: str,
    marks: Optional[Dict[str, Any]] = None,
    as_of: Any = None,
    execution: Optional[ExecutionRequest] = None,
) -> Dict[str, Any]:
    if _active_state is None or _active_state.get("mode") != "QMT_REMOTE":
        raise RuntimeError("只有QMT_REMOTE模式可以提交真实组合目标")
    payload = {"weights": weights, "idempotency_key": idempotency_key}
    if execution is not None:
        payload["execution"] = _execution_to_wire(execution)
    if marks is not None:
        payload["marks"] = marks
    if as_of is not None:
        payload["as_of"] = as_of
    return _strategy_request("strategy.submit_targets", payload)


def notify_target_buy_plan(
    items: Any,
    occurred_at: Any = None,
) -> Dict[str, Any]:
    """发送策略目标买入计划；只通知，不提交订单或修改账本。"""

    if _active_state is None or _active_state.get("mode") not in (
        "JQ", "QMT_REMOTE"
    ):
        raise RuntimeError(
            "只有JQ或QMT_REMOTE模式可以发送策略目标计划"
        )
    payload = {
        "mode": _active_state.get("mode"),
        "items": items,
    }
    if occurred_at is not None:
        payload["occurred_at"] = occurred_at
    return _strategy_request("strategy.notify_target_buy_plan", payload)


def get_intent(
    intent_id: Optional[str] = None,
    idempotency_key: Optional[str] = None,
) -> Dict[str, Any]:
    payload = {}  # type: Dict[str, Any]
    if intent_id:
        payload["intent_id"] = intent_id
    if idempotency_key:
        payload["idempotency_key"] = idempotency_key
    return _strategy_request("strategy.get_intent", payload)


def get_reconciliation() -> Dict[str, Any]:
    return _strategy_request("strategy.get_reconciliation", {})


def _record_runtime_portfolio(portfolio: PortfolioView) -> None:
    if _active_namespace is None:
        return
    recorder = _active_namespace.get("record")
    if not callable(recorder):
        return
    recorder(
        real_cash=portfolio.available_cash,
        real_total=portfolio.total_value,
        real_positions=portfolio.positions_value,
        real_nav=portfolio.nav,
        real_return=portfolio.returns,
        real_fees=portfolio.fees,
    )


def runtime_portfolio(context: Any) -> Any:
    """Return the native JQ portfolio or the StrategyLedger real view."""

    if _active_state is None:
        raise RuntimeError("策略运行时尚未安装")
    if _active_state.get("mode") != "QMT_REMOTE":
        return context.portfolio
    portfolio = get_portfolio(as_of=getattr(context, "current_dt", None))
    if not portfolio.performance_ready:
        raise RuntimeError("真实组合发生过运行中增减资，简单NAV指标不可用")
    _record_runtime_portfolio(portfolio)
    return portfolio


def _restore_runtime_targets() -> None:
    global _runtime_target_state
    if _active_state is None or _active_state.get("mode") != "QMT_REMOTE":
        _runtime_target_state = None
        return
    intent = get_intent()
    if not intent or intent.get("state") in (
        "COMPLETED", "CANCELED", "FAILED"
    ):
        _runtime_target_state = None
        return
    raw_execution = intent.get("execution")
    execution = (
        _execution_from_wire(raw_execution)
        if type(raw_execution) is dict
        else ExecutionRequest()
    )
    _runtime_target_state = {
        "intent_id": intent["intent_id"],
        "idempotency_key": intent["idempotency_key"],
        "weights": dict(intent.get("weights", {})),
        "marks": {},
        "execution": execution,
    }


def ensure_runtime_ready(initial_capital: Any, context: Any) -> Any:
    """Validate the remote account and restore the current daily intent."""

    if _active_state is None or _active_state.get("mode") != "QMT_REMOTE":
        return runtime_portfolio(context)
    ensured = ensure_account(initial_capital)
    reconciliation = ensured.get("reconciliation", {})
    if reconciliation.get("state") != "READY":
        raise RuntimeError(
            "真实账户对账未就绪: {}".format(
                reconciliation.get("details", {}).get("blockers", [])
            )
        )
    portfolio = runtime_portfolio(context)
    _restore_runtime_targets()
    _active_state["production_ready"] = True
    return portfolio


def submit_runtime_targets(
    context: Any,
    weights: Dict[str, Any],
    marks: Dict[str, Any],
    idempotency_key: str,
    execution: ExecutionRequest,
) -> Dict[str, Any]:
    """Submit one typed daily target and retain only restart state."""

    global _runtime_target_state
    existing = get_intent(idempotency_key=idempotency_key)
    if existing:
        weights = dict(existing.get("weights", weights))
        if type(existing.get("execution")) is dict:
            execution = _execution_from_wire(existing["execution"])
    result = submit_targets(
        weights,
        idempotency_key,
        marks=marks,
        as_of=getattr(context, "current_dt", None),
        execution=execution,
    )
    _runtime_target_state = {
        "intent_id": result["intent"]["intent_id"],
        "idempotency_key": idempotency_key,
        "weights": dict(weights),
        "marks": dict(marks),
        "execution": execution,
    }
    portfolio = PortfolioView(result["snapshot"])
    _record_runtime_portfolio(portfolio)
    return result


def advance_runtime_targets(context: Any) -> bool:
    """Advance the restored daily intent; return False while it is active."""

    global _runtime_target_state
    if _active_state is None or _active_state.get("mode") != "QMT_REMOTE":
        return True
    if _runtime_target_state is None:
        _restore_runtime_targets()
    if _runtime_target_state is None:
        return True
    intent = get_intent(_runtime_target_state["intent_id"])
    if intent.get("state") in ("COMPLETED", "CANCELED", "FAILED"):
        _runtime_target_state = None
        return True
    result = submit_runtime_targets(
        context,
        _runtime_target_state["weights"],
        _runtime_target_state["marks"],
        _runtime_target_state["idempotency_key"],
        _runtime_target_state["execution"],
    )
    if result["intent"]["state"] == "COMPLETED":
        _runtime_target_state = None
        return True
    return False


def cancel_runtime_targets() -> bool:
    """Cancel the active remote target; return True after it is terminal."""

    global _runtime_target_state
    if _active_state is None or _active_state.get("mode") != "QMT_REMOTE":
        return True
    if _runtime_target_state is None:
        _restore_runtime_targets()
    if _runtime_target_state is None:
        return True
    result = _strategy_request(
        "strategy.cancel_intent",
        {"intent_id": _runtime_target_state["intent_id"]},
    )
    if result.get("canceled"):
        _runtime_target_state = None
        return True
    return False


def cancel_runtime_orders() -> int:
    if _active_state is None:
        raise RuntimeError("策略运行时尚未安装")
    if _active_state.get("mode") == "QMT_REMOTE":
        return 0
    if _active_namespace is None:
        raise RuntimeError("策略namespace不可用")
    getter = _active_namespace.get("get_open_orders")
    cancel = _active_namespace.get("cancel_order")
    if not callable(getter) or not callable(cancel):
        raise RuntimeError("聚宽订单函数不可用")
    orders = getter() or {}
    for order_obj in list(orders.values()):
        cancel(order_obj)
    return len(orders)


def runtime_order_target(security: str, amount: int) -> Any:
    if _active_namespace is None:
        raise RuntimeError("策略namespace不可用")
    order_target_fn = _active_namespace.get("order_target")
    if not callable(order_target_fn):
        raise RuntimeError("聚宽order_target不可用")
    return order_target_fn(security, amount)


def runtime_order_target_value(
    security: str, target_value: float, limit_price: Optional[float] = None
) -> Any:
    if _active_namespace is None:
        raise RuntimeError("策略namespace不可用")
    order_target_value_fn = _active_namespace.get("order_target_value")
    if not callable(order_target_value_fn):
        raise RuntimeError("聚宽order_target_value不可用")
    style = None
    if limit_price is not None:
        style_type = _active_namespace.get("LimitOrderStyle")
        if not callable(style_type):
            raise RuntimeError("聚宽LimitOrderStyle不可用")
        style = style_type(limit_price)
    return order_target_value_fn(security, target_value, style=style)


def _run_type_from_context(context: Any) -> Optional[str]:
    run_params = getattr(context, "run_params", None)
    if isinstance(run_params, dict):
        return run_params.get("type")
    return getattr(run_params, "type", None)


def _normalise_runtime_mode(mode: Any) -> str:
    if type(mode) is not str:
        raise RuntimeError(
            "运行模式必须是普通字符串BACKTEST、JQ或QMT_REMOTE"
        )
    value = str.upper(str.strip(mode))
    if value not in ("BACKTEST", "JQ", "QMT_REMOTE"):
        raise RuntimeError(
            "运行模式必须是BACKTEST、JQ或QMT_REMOTE"
        )
    return value


def _validate_runtime_identifier(value: Any, field: str) -> str:
    if type(value) is not str:
        raise RuntimeError("{} 必须是非空字符串".format(field))
    if not value or value != str.strip(value) or len(value) > 128:
        raise RuntimeError("{} 必须是非空、无首尾空白且不超过128字符的字符串".format(field))
    if not all(str.isalnum(char) or char in "._-" for char in value):
        raise RuntimeError("{} 只能包含字母、数字、点、下划线和连字符".format(field))
    return value


def _validate_profile_module_name(value: Any) -> str:
    if type(value) is not str:
        raise RuntimeError("profile_module 必须是Python模块名")
    if not value or value != str.strip(value):
        raise RuntimeError("profile_module 必须是Python模块名")
    if not all(str.isidentifier(part) for part in str.split(value, ".")):
        raise RuntimeError("profile_module 必须是合法的Python模块名")
    return value


def get_configured_execution_mode(
    strategy_id: str,
    profile_module: str = "jq_runtime_config",
) -> str:
    """Read the per-strategy mode; a missing key deliberately defaults to JQ."""

    strategy_id = _validate_runtime_identifier(strategy_id, "strategy_id")
    profile_module = _validate_profile_module_name(profile_module)
    try:
        module = __import__(profile_module, fromlist=["*"])
        schema_version = getattr(module, "PROFILE_SCHEMA_VERSION", None)
        configured = getattr(module, "EXECUTION_MODES", {})
    except BaseException:
        raise RuntimeError(
            "无法加载运行配置模块 {}；请确认文件已上传且配置可读取".format(
                profile_module
            )
        ) from None
    if type(schema_version) is not int or schema_version != PROFILE_SCHEMA_VERSION:
        raise RuntimeError(
            "运行配置schema版本不匹配: expected={}".format(
                PROFILE_SCHEMA_VERSION
            )
        )
    if type(configured) is not dict:
        raise RuntimeError("运行配置EXECUTION_MODES必须是字典")
    if any(type(key) is not str or type(value) is not str for key, value in configured.items()):
        raise RuntimeError("运行配置EXECUTION_MODES的键和值必须是普通字符串")
    mode = configured.get(strategy_id, "JQ").strip().upper()
    if mode not in ("JQ", "QMT_REMOTE"):
        raise RuntimeError(
            "策略{}的执行模式必须是JQ或QMT_REMOTE".format(strategy_id)
        )
    return mode


def _load_runtime_profile(
    profile_module: str,
    profile: str,
    strategy_id: str,
) -> Dict[str, Any]:
    """加载并校验聚宽私有运行 profile；错误信息不回显凭据或未知字段名。"""

    load_failed = False
    schema_version = None
    profiles = None
    try:
        module = __import__(profile_module, fromlist=["*"])
        schema_version = getattr(module, "PROFILE_SCHEMA_VERSION", None)
        profiles = getattr(module, "PROFILES", None)
    except BaseException:
        # 配置模块导入或属性读取异常可能包含密钥；断开异常链后抛出固定错误。
        load_failed = True
    if load_failed:
        raise RuntimeError(
            "无法加载运行配置模块 {}；请确认文件已上传且配置可读取".format(
                profile_module
            )
        ) from None

    if type(schema_version) is not int or schema_version != PROFILE_SCHEMA_VERSION:
        raise RuntimeError(
            "运行配置schema版本不匹配: expected={}".format(PROFILE_SCHEMA_VERSION)
        )

    if type(profiles) is not dict:
        raise RuntimeError("运行配置模块必须定义字典 PROFILES")
    if any(type(name) is not str for name in profiles):
        raise RuntimeError("运行配置 PROFILES 的profile名称必须是普通字符串")
    if profile not in profiles:
        raise RuntimeError("运行配置中不存在profile: {}".format(profile))
    raw = profiles[profile]
    if type(raw) is not dict:
        raise RuntimeError("profile {} 必须是字典".format(profile))

    if any(type(key) is not str for key in raw):
        raise RuntimeError("profile {} 包含非普通字符串字段".format(profile))
    if any(key not in _PROFILE_ALLOWED_FIELDS for key in raw):
        raise RuntimeError("profile {} 包含未知字段；字段名不予回显".format(profile))
    missing = sorted(_PROFILE_REQUIRED_FIELDS - set(raw))
    if missing:
        raise RuntimeError(
            "profile {} 缺少必填字段: {}".format(profile, ", ".join(missing))
        )

    configured_strategy_id = _validate_runtime_identifier(
        raw.get("strategy_id"), "profile.strategy_id"
    )
    if configured_strategy_id != strategy_id:
        raise RuntimeError("profile.strategy_id 与策略请求不一致")

    host = raw.get("host")
    if (
        type(host) is not str
        or not host
        or host != str.strip(host)
        or any(str.isspace(ch) for ch in host)
    ):
        raise RuntimeError("profile.host 必须是非空且不含空白的字符串")
    if len(host) > 255:
        raise RuntimeError("profile.host 长度不能超过255字符")

    token = raw.get("token")
    if type(token) is not str or not token or token != str.strip(token):
        raise RuntimeError("profile.token 必须是非空且无首尾空白的字符串")

    port = raw.get("port", 58620)
    if type(port) is not int or not 1 <= port <= 65535:
        raise RuntimeError("profile.port 必须是1到65535之间的整数")

    numeric_rules = {
        "rpc_timeout": (DEFAULT_RPC_TIMEOUT_SECONDS, 5.0, 300.0),
    }
    numeric_values = {}  # type: Dict[str, float]
    for field, (default, minimum, maximum) in numeric_rules.items():
        value = raw.get(field, default)
        if type(value) not in (int, float):
            raise RuntimeError("profile.{} 必须是有限数值".format(field))
        if type(value) is float and not math.isfinite(value):
            raise RuntimeError("profile.{} 必须是有限数值".format(field))
        if value < minimum or value > maximum:
            raise RuntimeError(
                "profile.{} 必须在{}到{}之间".format(field, minimum, maximum)
            )
        numeric_values[field] = float(value)

    optional_strings = {}  # type: Dict[str, Optional[str]]
    for field in ("account_key", "tls_cert"):
        value = raw.get(field)
        if value is not None and (
            type(value) is not str or not value or value != str.strip(value)
        ):
            raise RuntimeError(
                "profile.{} 必须是非空且无首尾空白的字符串或None".format(field)
            )
        optional_strings[field] = value

    return {
        "strategy_id": configured_strategy_id,
        "host": host,
        "token": token,
        "port": port,
        "account_key": optional_strings["account_key"],
        "tls_cert": optional_strings["tls_cert"],
        "rpc_timeout": numeric_values["rpc_timeout"],
    }


def _runtime_mutation_guard(name: str, active_mode: str) -> Callable[..., Any]:
    def blocked(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("{}模式禁止交易变更: {}".format(active_mode, name))

    blocked.__name__ = "runtime_blocked_{}_{}".format(active_mode.lower(), name)
    return blocked


def _install_runtime_guards(
    namespace: Dict[str, Any],
    active_mode: str,
) -> Tuple[str, ...]:
    """把聚宽交易函数统一替换为失败关闭的 guard，返回排序后的阻断名单。"""

    names = tuple(sorted(_RUNTIME_MUTATION_NAMES))
    for name in names:
        namespace[name] = _runtime_mutation_guard(name, active_mode)
    return names


def _build_strategy_runtime_state(
    *,
    mode: str,
    run_type: str,
    strategy_id: str,
    profile: str,
    profile_module: Optional[str] = None,
    blocked_mutations: Tuple[str, ...] = (),
    validate_remote: bool = False,
) -> Dict[str, Any]:
    state = {
        "api_version": STRATEGY_RUNTIME_API_VERSION,
        "profile_schema_version": PROFILE_SCHEMA_VERSION,
        "profile": profile,
        "mode": mode,
        "run_type": run_type,
        "strategy_id": strategy_id,
        "enabled": False,
        "orders_enabled": mode in ("BACKTEST", "JQ"),
        "production_ready": False,
        "reason": "backtest",
    }
    if mode == "BACKTEST" and validate_remote:
        state.update(
            {
                "profile_module": profile_module,
                "remote_validation_enabled": True,
                "reason": "backtest_remote_validation",
            }
        )
    elif mode == "JQ":
        state.update(
            {
                "profile_module": profile_module,
                "enabled": True,
                "orders_enabled": True,
                "reason": "jq",
            }
        )
    elif mode == "QMT_REMOTE":
        state.update(
            {
                "profile_module": profile_module,
                "enabled": True,
                "orders_enabled": True,
                "production_ready": False,
                "reason": "qmt_remote_profile_validated",
                "mirror_jq_orders": False,
                "blocked_mutations": blocked_mutations,
            }
        )
    return state


def install_strategy_runtime(
    namespace: Dict[str, Any],
    *,
    context: Any,
    profile: str,
    mode: str,
    strategy_id: str,
    expected_api_version: int = STRATEGY_RUNTIME_API_VERSION,
    profile_module: str = "jq_runtime_config",
    validate_remote: bool = False,
) -> Dict[str, Any]:
    """安装策略运行模式并返回运行时状态；任何校验失败都抛出异常。

    同一进程内重复安装仅在签名完全一致时幂等返回原状态；签名漂移、
    上一代 helper 遗留记录或记录缺失均失败关闭，必须使用干净进程重启。
    """

    global _active_signature, _active_state, _active_profile, _active_namespace

    if type(namespace) is not dict:
        raise RuntimeError("策略namespace必须是普通dict（请传入globals()）")
    if (
        type(expected_api_version) is not int
        or expected_api_version != STRATEGY_RUNTIME_API_VERSION
    ):
        raise RuntimeError(
            "helper运行时API版本不匹配: expected={}".format(STRATEGY_RUNTIME_API_VERSION)
        )
    mode = _normalise_runtime_mode(mode)
    if type(validate_remote) is not bool:
        raise RuntimeError("validate_remote必须是bool")
    if validate_remote and mode != "BACKTEST":
        raise RuntimeError("validate_remote仅用于BACKTEST远程预检")
    profile = _validate_runtime_identifier(profile, "profile")
    strategy_id = _validate_runtime_identifier(strategy_id, "strategy_id")
    profile_module = _validate_profile_module_name(profile_module)

    record = namespace.get(_RUNTIME_STATE_KEY)
    if record is not None:
        if type(record) is not dict or record.get("token") is not _MODULE_TOKEN:
            raise RuntimeError(
                "检测到上一代helper运行记录；必须使用干净运行进程重启"
            )
    elif _active_state is not None:
        raise RuntimeError("策略namespace运行记录缺失；必须使用干净运行进程重启")

    run_type = str(_run_type_from_context(context) or "").strip().lower()
    signature = (
        mode,
        profile,
        strategy_id,
        profile_module,
        expected_api_version,
        run_type,
        validate_remote,
    )
    if _active_state is not None:
        if _active_signature != signature:
            raise RuntimeError("策略运行安装签名漂移；必须使用干净运行进程重启")
        if mode == "QMT_REMOTE":
            # 幂等重装仍补齐交易 guard，防止平台重建 namespace 后 guard 丢失
            _install_runtime_guards(namespace, mode)
        return dict(_active_state)

    blocked_mutations = ()  # type: Tuple[str, ...]
    runtime_profile = None  # type: Optional[Dict[str, Any]]
    if mode == "BACKTEST":
        if run_type not in (
            "simple_backtest", "full_backtest"
        ):
            raise RuntimeError(
                "MODE=BACKTEST仅允许聚宽回测，当前run_type={}".format(
                    run_type or "<empty>"
                )
            )
        if validate_remote:
            runtime_profile = _load_runtime_profile(
                profile_module, profile, strategy_id
            )
        state = _build_strategy_runtime_state(
            mode=mode,
            run_type=run_type,
            strategy_id=strategy_id,
            profile=profile,
            profile_module=(profile_module if validate_remote else None),
            validate_remote=validate_remote,
        )
    elif mode == "JQ":
        if run_type != "sim_trade":
            raise RuntimeError(
                "MODE=JQ仅允许聚宽模拟交易，当前run_type={}".format(
                    run_type or "<empty>"
                )
            )
        runtime_profile = _load_runtime_profile(profile_module, profile, strategy_id)
        state = _build_strategy_runtime_state(
            mode=mode,
            run_type=run_type,
            strategy_id=strategy_id,
            profile=profile,
            profile_module=profile_module,
        )
    else:
        if run_type != "sim_trade":
            raise RuntimeError(
                "MODE={} 仅允许聚宽模拟交易，当前run_type={}".format(
                    mode, run_type or "<empty>"
                )
            )
        runtime_profile = _load_runtime_profile(profile_module, profile, strategy_id)
        blocked_mutations = _install_runtime_guards(namespace, mode)
        state = _build_strategy_runtime_state(
            mode=mode,
            run_type=run_type,
            strategy_id=strategy_id,
            profile=profile,
            profile_module=profile_module,
            blocked_mutations=blocked_mutations,
        )

    _active_signature = signature
    _active_state = dict(state)
    _active_namespace = namespace
    if mode in ("JQ", "QMT_REMOTE") or validate_remote:
        _active_profile = runtime_profile
    namespace[_RUNTIME_STATE_KEY] = {"token": _MODULE_TOKEN, "mode": mode}
    return dict(state)


class JoinQuantRuntime:
    """Small strategy-facing facade shared by JQ and QMT_REMOTE modes."""

    def __init__(self, state: Dict[str, Any]) -> None:
        self.state = dict(state)
        self.mode = RuntimeMode(self.state["mode"])

    def portfolio(self, context: Any) -> Any:
        return runtime_portfolio(context)

    def ensure_ready(self, initial_capital: Any, context: Any) -> Any:
        global _active_state
        portfolio = ensure_runtime_ready(initial_capital, context)
        if self.mode is RuntimeMode.QMT_REMOTE:
            self.state["production_ready"] = True
            if _active_state is not None:
                _active_state["production_ready"] = True
        return portfolio

    def submit_targets(
        self,
        context: Any,
        weights: Dict[str, Any],
        marks: Dict[str, Any],
        idempotency_key: str,
        execution: ExecutionRequest,
    ) -> Dict[str, Any]:
        return submit_runtime_targets(
            context, weights, marks, idempotency_key, execution
        )

    def advance_targets(self, context: Any) -> bool:
        return advance_runtime_targets(context)

    def cancel_targets(self) -> bool:
        return cancel_runtime_targets()

    def cancel_orders(self) -> int:
        return cancel_runtime_orders()

    def order_target(self, security: str, amount: int) -> Any:
        return runtime_order_target(security, amount)

    def order_target_value(
        self,
        security: str,
        target_value: float,
        limit_price: Optional[float] = None,
    ) -> Any:
        return runtime_order_target_value(
            security, target_value, limit_price=limit_price
        )

    def notify_target_buy_plan(
        self, items: Any, occurred_at: Any = None
    ) -> Dict[str, Any]:
        return notify_target_buy_plan(items, occurred_at=occurred_at)


def install_joinquant_runtime(
    namespace: Dict[str, Any],
    *,
    context: Any,
    profile: str,
    strategy_id: str,
    initial_capital: Any,
    profile_module: str = "jq_runtime_config",
    validate_remote_during_backtest: bool = False,
    expected_api_version: int = STRATEGY_RUNTIME_API_VERSION,
) -> JoinQuantRuntime:
    """Resolve the platform run type, install one mode, and run optional preflight."""

    run_type = str(_run_type_from_context(context) or "").strip().lower()
    if run_type in ("simple_backtest", "full_backtest"):
        mode = RuntimeMode.BACKTEST
        validate_remote = validate_remote_during_backtest
    elif run_type == "sim_trade":
        mode = RuntimeMode(
            get_configured_execution_mode(strategy_id, profile_module)
        )
        validate_remote = False
    else:
        raise RuntimeError(
            "不支持的聚宽run_type: {}".format(run_type or "<empty>")
        )
    state = install_strategy_runtime(
        namespace,
        context=context,
        profile=profile,
        mode=mode.value,
        strategy_id=strategy_id,
        expected_api_version=expected_api_version,
        profile_module=profile_module,
        validate_remote=validate_remote,
    )
    runtime = JoinQuantRuntime(state)
    if not validate_remote:
        return runtime
    ensured = ensure_account(initial_capital)
    reconciliation = ensured.get("reconciliation", {})
    if reconciliation.get("state") != "READY":
        raise RuntimeError(
            "回测远程预检对账未就绪: {}".format(
                reconciliation.get("details", {}).get("blockers", [])
            )
        )
    portfolio = get_portfolio()
    latest = get_reconciliation().get("reconciliation", {})
    if latest.get("state") != "READY":
        raise RuntimeError(
            "回测远程预检快照对账未就绪: {}".format(
                latest.get("details", {}).get("blockers", [])
            )
        )
    runtime.state["remote_validation"] = {
        "cash": portfolio.available_cash,
        "positions_value": portfolio.positions_value,
        "total_value": portfolio.total_value,
        "position_count": len(portfolio.positions),
        "reconciliation": "READY",
    }
    return runtime
