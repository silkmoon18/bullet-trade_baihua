"""Type contract for the standalone JoinQuant strategy runtime helper."""

from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Literal, Optional, Tuple, TypedDict, Union

from joinquant_typing import Context

STRATEGY_RUNTIME_API_VERSION: int
STRATEGY_RUNTIME_HELPER_MARKER: str
PROFILE_SCHEMA_VERSION: int

_RuntimeMode = Literal["BACKTEST", "JQ", "QMT_REMOTE"]


class ExecutionType(str, Enum):
    LIMIT: str
    CONDITIONAL_LIMIT: str
    MARKET: str
    MARKETABLE_LIMIT: str


class RuntimeMode(str, Enum):
    BACKTEST: str
    JQ: str
    QMT_REMOTE: str


class FollowUpPolicy(str, Enum):
    NONE: str
    UNTIL_FILLED_TODAY: str


class RepricingPolicy(str, Enum):
    KEEP_ORIGINAL: str
    RECOMPUTE: str


class ConditionalLimitPriceMode(str, Enum):
    BOUNDARY: str
    COUNTERPARTY: str


@dataclass(frozen=True)
class LimitExecution:
    price_band_ppm: int = ...


@dataclass(frozen=True)
class ConditionalLimitExecution:
    price_band_ppm: int = ...
    price_mode: ConditionalLimitPriceMode = ...


@dataclass(frozen=True)
class MarketExecution:
    protect_price_band_ppm: int = ...


@dataclass(frozen=True)
class MarketableLimitExecution:
    price_band_ppm: int = ...


_ExecutionStyle = Union[
    LimitExecution,
    ConditionalLimitExecution,
    MarketExecution,
    MarketableLimitExecution,
]


@dataclass(frozen=True)
class ExecutionRequest:
    style: _ExecutionStyle = ...
    follow_up: FollowUpPolicy = ...
    repricing: RepricingPolicy = ...


class PositionView:
    def __init__(self, payload: Dict[str, Any]) -> None: ...

    security: str
    total_amount: int
    closeable_amount: int
    avg_cost: float
    price: float
    value: float
    unrealized_pnl: float


class PortfolioView:
    def __init__(self, payload: Dict[str, Any]) -> None: ...

    account_id: str
    as_of: str
    snapshot_version: str
    ledger_version: int
    cash: float
    reserved_cash: float
    available_cash: float
    positions_value: float
    total_value: float
    starting_cash: float
    total_pnl: float
    realized_pnl: float
    unrealized_pnl: float
    fees: float
    nav: float
    returns: float
    performance_ready: bool
    positions: Dict[str, PositionView]


class _StrategyRuntimeRequiredState(TypedDict):
    api_version: int
    profile_schema_version: int
    profile: str
    mode: _RuntimeMode
    run_type: str
    strategy_id: str
    enabled: bool
    orders_enabled: bool
    production_ready: bool
    reason: str


class _StrategyRuntimeOptionalState(TypedDict, total=False):
    profile_module: Optional[str]
    blocked_mutations: Tuple[str, ...]
    mirror_jq_orders: bool
    remote_validation_enabled: bool


class _StrategyRuntimeState(_StrategyRuntimeRequiredState, _StrategyRuntimeOptionalState):
    pass


def install_strategy_runtime(
    namespace: Dict[str, Any],
    *,
    context: Context,
    profile: str,
    mode: _RuntimeMode,
    strategy_id: str,
    expected_api_version: int = ...,
    profile_module: str = ...,
    validate_remote: bool = ...,
) -> _StrategyRuntimeState: ...


class JoinQuantRuntime:
    def __init__(self, state: Dict[str, Any]) -> None: ...
    state: Dict[str, Any]
    mode: RuntimeMode

    def portfolio(self, context: Context) -> Any: ...
    def ensure_ready(self, initial_capital: Any, context: Context) -> Any: ...
    def submit_targets(
        self,
        context: Context,
        weights: Dict[str, Any],
        marks: Dict[str, Any],
        idempotency_key: str,
        execution: ExecutionRequest,
    ) -> Dict[str, Any]: ...
    def advance_targets(self, context: Context) -> bool: ...
    def cancel_targets(self) -> bool: ...
    def cancel_orders(self) -> int: ...
    def order_target(self, security: str, amount: int) -> Any: ...
    def order_target_value(
        self,
        security: str,
        target_value: float,
        limit_price: Optional[float] = ...,
    ) -> Any: ...
    def notify_target_buy_plan(
        self, items: Any, occurred_at: Any = ...
    ) -> Dict[str, Any]: ...


def install_joinquant_runtime(
    namespace: Dict[str, Any],
    *,
    context: Context,
    profile: str,
    strategy_id: str,
    initial_capital: Any,
    profile_module: str = ...,
    validate_remote_during_backtest: bool = ...,
    expected_api_version: int = ...,
) -> JoinQuantRuntime: ...


def ensure_account(initial_capital: Any = ...) -> Dict[str, Any]: ...
def get_portfolio(
    marks: Optional[Dict[str, Any]] = ...,
    as_of: Any = ...,
) -> PortfolioView: ...
def submit_targets(
    weights: Dict[str, Any],
    idempotency_key: str,
    marks: Optional[Dict[str, Any]] = ...,
    as_of: Any = ...,
    execution: Optional[ExecutionRequest] = ...,
) -> Dict[str, Any]: ...
def notify_target_buy_plan(
    items: Any,
    occurred_at: Any = ...,
) -> Dict[str, Any]: ...
def get_intent(
    intent_id: Optional[str] = ...,
    idempotency_key: Optional[str] = ...,
) -> Dict[str, Any]: ...
def get_reconciliation() -> Dict[str, Any]: ...
def get_configured_execution_mode(
    strategy_id: str,
    profile_module: str = ...,
) -> _RuntimeMode: ...
def runtime_portfolio(context: Context) -> Any: ...
def ensure_runtime_ready(initial_capital: Any, context: Context) -> Any: ...
def submit_runtime_targets(
    context: Context,
    weights: Dict[str, Any],
    marks: Dict[str, Any],
    idempotency_key: str,
    execution: ExecutionRequest,
) -> Dict[str, Any]: ...
def advance_runtime_targets(context: Context) -> bool: ...
def cancel_runtime_targets() -> bool: ...
def cancel_runtime_orders() -> int: ...
def runtime_order_target(security: str, amount: int) -> Any: ...
def runtime_order_target_value(
    security: str,
    target_value: float,
    limit_price: Optional[float] = ...,
) -> Any: ...
