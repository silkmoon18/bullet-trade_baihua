"""Type contract for the standalone JoinQuant remote helper."""

from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple, TypedDict

from joinquant_typing import Context

STRATEGY_RUNTIME_API_VERSION: int
STRATEGY_RUNTIME_HELPER_MARKER: str
PROFILE_SCHEMA_VERSION: int


class _StrategyRuntimeRequiredState(TypedDict):
    api_version: int
    profile_schema_version: int
    profile: str
    mode: str
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


class _StrategyRuntimeState(_StrategyRuntimeRequiredState, _StrategyRuntimeOptionalState):
    pass


class MarketOrderStyle:
    limit_price: Optional[float]
    def __init__(self, limit_price: Optional[float] = ...) -> None: ...


class LimitOrderStyle:
    limit_price: float
    price: float
    def __init__(self, limit_price: float) -> None: ...


class RemoteOrder:
    order_id: str
    status: str
    security: str
    amount: int
    price: Optional[float]
    actual_amount: int
    actual_price: Optional[float]
    filled: int
    is_buy: Optional[bool]
    order_remark: Optional[str]
    strategy_name: Optional[str]
    timed_out: bool
    async_tracking: bool
    last_snapshot: Dict[str, Any]
    raw_response: Dict[str, Any]
    def __init__(
        self,
        order_id: str,
        status: str,
        security: str,
        amount: int,
        price: Optional[float] = ...,
        actual_amount: Optional[int] = ...,
        actual_price: Optional[float] = ...,
        filled: int = ...,
        is_buy: Optional[bool] = ...,
        order_remark: Optional[str] = ...,
        strategy_name: Optional[str] = ...,
        timed_out: bool = ...,
        async_tracking: bool = ...,
        last_snapshot: Optional[Dict[str, Any]] = ...,
        raw_response: Optional[Dict[str, Any]] = ...,
    ) -> None: ...


class RemoteTrade:
    trade_id: str
    order_id: str
    security: str
    amount: int
    price: float
    time: datetime
    commission: float
    tax: float
    def __init__(
        self,
        trade_id: str,
        order_id: str,
        security: str,
        amount: int,
        price: float,
        time: Any,
        commission: float = ...,
        tax: float = ...,
    ) -> None: ...


class RemotePosition:
    security: str
    amount: int
    avg_cost: float
    market_value: float
    available: int
    frozen: int
    market: Optional[str]
    def __init__(
        self,
        security: str,
        amount: int,
        avg_cost: float,
        market_value: float,
        available: Optional[int] = ...,
        frozen: Optional[int] = ...,
        market: Optional[str] = ...,
    ) -> None: ...


class RemoteAccount:
    available_cash: float
    total_value: float
    def __init__(self, available_cash: float, total_value: float) -> None: ...


class RemoteDataClient:
    def __init__(self, client: Any) -> None: ...
    def get_price(self, security: str, **kwargs: Any) -> Any: ...
    def get_trade_days(self, start: str, end: str) -> List[Any]: ...
    def get_snapshot(self, security: str) -> Dict[str, Any]: ...
    def get_last_price(self, security: str) -> Optional[float]: ...


class RemoteBrokerClient:
    account_key: Optional[str]
    sub_account_id: Optional[str]
    place_order_timeout_margin: float
    def __init__(
        self,
        client: Any,
        *,
        account_key: Optional[str] = ...,
        sub_account_id: Optional[str] = ...,
        place_order_timeout_margin: float = ...,
    ) -> None: ...
    def bind_data_client(self, data_client: RemoteDataClient) -> None: ...
    def order(
        self,
        security: str,
        amount: int,
        price: Optional[float] = ...,
        side: Optional[str] = ...,
        wait_timeout: float = ...,
        *,
        style: Optional[Any] = ...,
        market: Optional[bool] = ...,
        remark: Optional[str] = ...,
        order_remark: Optional[str] = ...,
        idempotency_key: Optional[str] = ...,
    ) -> str: ...
    def order_value(
        self,
        security: str,
        value: float,
        price: Optional[float] = ...,
        wait_timeout: float = ...,
        *,
        style: Optional[Any] = ...,
        side: Optional[str] = ...,
        pindex: int = ...,
        close_today: bool = ...,
        market: Optional[bool] = ...,
        remark: Optional[str] = ...,
        order_remark: Optional[str] = ...,
        idempotency_key: Optional[str] = ...,
    ) -> str: ...
    def order_percent(
        self,
        security: str,
        percent: float,
        price: Optional[float] = ...,
        wait_timeout: float = ...,
        *,
        style: Optional[Any] = ...,
        side: Optional[str] = ...,
        pindex: int = ...,
        close_today: bool = ...,
        market: Optional[bool] = ...,
        remark: Optional[str] = ...,
        order_remark: Optional[str] = ...,
        idempotency_key: Optional[str] = ...,
    ) -> str: ...
    def order_target(
        self,
        security: str,
        target: int,
        price: Optional[float] = ...,
        wait_timeout: float = ...,
        *,
        style: Optional[Any] = ...,
        side: Optional[str] = ...,
        pindex: int = ...,
        close_today: bool = ...,
        market: Optional[bool] = ...,
        remark: Optional[str] = ...,
        order_remark: Optional[str] = ...,
        idempotency_key: Optional[str] = ...,
    ) -> str: ...
    def order_target_value(
        self,
        security: str,
        target_value: Optional[float] = ...,
        price: Optional[float] = ...,
        wait_timeout: float = ...,
        *,
        value: Optional[float] = ...,
        style: Optional[Any] = ...,
        side: Optional[str] = ...,
        pindex: int = ...,
        close_today: bool = ...,
        market: Optional[bool] = ...,
        remark: Optional[str] = ...,
        order_remark: Optional[str] = ...,
        idempotency_key: Optional[str] = ...,
    ) -> str: ...
    def order_target_percent(
        self,
        security: str,
        percent: float,
        price: Optional[float] = ...,
        wait_timeout: float = ...,
        *,
        style: Optional[Any] = ...,
        side: Optional[str] = ...,
        pindex: int = ...,
        close_today: bool = ...,
        market: Optional[bool] = ...,
        remark: Optional[str] = ...,
        order_remark: Optional[str] = ...,
        idempotency_key: Optional[str] = ...,
    ) -> str: ...
    def get_account(self) -> RemoteAccount: ...
    def get_positions(self) -> List[RemotePosition]: ...
    def get_orders(self, order_id: Optional[str] = ..., security: Optional[str] = ..., status: Optional[object] = ..., from_broker: bool = ...) -> Dict[str, RemoteOrder]: ...
    def get_open_orders(self) -> Dict[str, RemoteOrder]: ...
    def get_trades(self, order_id: Optional[str] = ..., security: Optional[str] = ...) -> Dict[str, RemoteTrade]: ...
    def get_order_status(self, order_id: str) -> Dict[str, Any]: ...
    def cancel_order(self, order_id: str) -> Dict[str, Any]: ...


def configure(
    host: str,
    token: str,
    *,
    port: int = ...,
    account_key: Optional[str] = ...,
    sub_account_id: Optional[str] = ...,
    tls_cert: Optional[str] = ...,
    retries: int = ...,
    retry_interval: float = ...,
    rpc_timeout: float = ...,
    place_order_timeout_margin: float = ...,
    debug: bool = ...,
    _runtime_boundary_attempt_state: Optional[List[bool]] = ...,
) -> None: ...
def install_strategy_runtime(
    namespace: Dict[str, Any],
    *,
    context: Context,
    profile: str,
    mode: str,
    strategy_id: str,
    expected_api_version: int = ...,
    profile_module: str = ...,
) -> _StrategyRuntimeState: ...
def install_jq_compat(
    namespace: Dict[str, Any],
    *,
    context: Context,
    host: str,
    token: str,
    port: int = ...,
    account_key: Optional[str] = ...,
    sub_account_id: Optional[str] = ...,
    mirror_jq_orders: bool = ...,
    default_wait_timeout: float = ...,
    tls_cert: Optional[str] = ...,
    retries: int = ...,
    retry_interval: float = ...,
    rpc_timeout: float = ...,
    place_order_timeout_margin: float = ...,
    debug: bool = ...,
    _runtime_boundary_attempt_state: Optional[List[bool]] = ...,
) -> Dict[str, Any]: ...
def get_data_client() -> RemoteDataClient: ...
def get_broker_client() -> RemoteBrokerClient: ...

def order(
    security: str,
    amount: int,
    price: Optional[float] = ...,
    side: Optional[str] = ...,
    wait_timeout: float = ...,
    *,
    style: Optional[Any] = ...,
    market: Optional[bool] = ...,
    remark: Optional[str] = ...,
    order_remark: Optional[str] = ...,
    idempotency_key: Optional[str] = ...,
) -> str: ...
def order_value(
    security: str,
    value: float,
    price: Optional[float] = ...,
    wait_timeout: float = ...,
    *,
    style: Optional[Any] = ...,
    side: Optional[str] = ...,
    pindex: int = ...,
    close_today: bool = ...,
    market: Optional[bool] = ...,
    remark: Optional[str] = ...,
    order_remark: Optional[str] = ...,
    idempotency_key: Optional[str] = ...,
) -> str: ...
def order_percent(
    security: str,
    percent: float,
    price: Optional[float] = ...,
    wait_timeout: float = ...,
    *,
    style: Optional[Any] = ...,
    side: Optional[str] = ...,
    pindex: int = ...,
    close_today: bool = ...,
    market: Optional[bool] = ...,
    remark: Optional[str] = ...,
    order_remark: Optional[str] = ...,
    idempotency_key: Optional[str] = ...,
) -> str: ...
def order_target(
    security: str,
    target: int,
    price: Optional[float] = ...,
    wait_timeout: float = ...,
    *,
    style: Optional[Any] = ...,
    side: Optional[str] = ...,
    pindex: int = ...,
    close_today: bool = ...,
    market: Optional[bool] = ...,
    remark: Optional[str] = ...,
    order_remark: Optional[str] = ...,
    idempotency_key: Optional[str] = ...,
) -> str: ...
def order_target_value(
    security: str,
    target_value: Optional[float] = ...,
    price: Optional[float] = ...,
    wait_timeout: float = ...,
    *,
    value: Optional[float] = ...,
    style: Optional[Any] = ...,
    side: Optional[str] = ...,
    pindex: int = ...,
    close_today: bool = ...,
    market: Optional[bool] = ...,
    remark: Optional[str] = ...,
    order_remark: Optional[str] = ...,
    idempotency_key: Optional[str] = ...,
) -> str: ...
def order_target_percent(
    security: str,
    percent: float,
    price: Optional[float] = ...,
    wait_timeout: float = ...,
    *,
    style: Optional[Any] = ...,
    side: Optional[str] = ...,
    pindex: int = ...,
    close_today: bool = ...,
    market: Optional[bool] = ...,
    remark: Optional[str] = ...,
    order_remark: Optional[str] = ...,
    idempotency_key: Optional[str] = ...,
) -> str: ...
def cancel_order(order_id: str) -> Dict[str, Any]: ...
def get_order_status(order_id: str) -> Dict[str, Any]: ...
def get_open_orders() -> Dict[str, RemoteOrder]: ...
def get_orders(order_id: Optional[str] = ..., security: Optional[str] = ..., status: Optional[object] = ..., from_broker: bool = ...) -> Dict[str, RemoteOrder]: ...
def get_trades(order_id: Optional[str] = ..., security: Optional[str] = ...) -> Dict[str, RemoteTrade]: ...
def get_account() -> RemoteAccount: ...
def get_positions() -> List[RemotePosition]: ...
