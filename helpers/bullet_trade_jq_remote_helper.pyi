"""Type contract for the standalone JoinQuant strategy runtime helper."""

from typing import Any, Dict, Optional, Tuple, TypedDict

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
