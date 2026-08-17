"""Type contract for the standalone JoinQuant strategy runtime helper."""

from typing import Any, Dict, Literal, Optional, Tuple, TypedDict

from joinquant_typing import Context

STRATEGY_RUNTIME_API_VERSION: int
STRATEGY_RUNTIME_HELPER_MARKER: str
PROFILE_SCHEMA_VERSION: int

RuntimeMode = Literal["BACKTEST", "JQ_PAPER", "SIGNAL_ONLY", "QMT_REMOTE"]


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
    mode: RuntimeMode
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
    mode: RuntimeMode,
    strategy_id: str,
    expected_api_version: int = ...,
    profile_module: str = ...,
    validate_remote: bool = ...,
) -> _StrategyRuntimeState: ...


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
