# -*- coding: utf-8 -*-
"""BulletTrade 聚宽策略运行 helper（L00 精简版）。

上传到聚宽研究根目录后，策略通过版本化入口安装运行模式：

    import bullet_trade_jq_remote_helper as bt

    state = bt.install_strategy_runtime(
        globals(),
        context=context,
        profile=PROFILE,
        mode=MODE,
        strategy_id=STRATEGY_ID,
    )

职责边界：

- 版本校验：helper marker、运行时 API 版本和 profile schema 版本固定校验，
  不匹配即失败关闭。
- 模式校验：BACKTEST 只校验聚宽回测 run_type，不读取 profile、不联网、
  不接管下单函数；SHADOW 校验 sim_trade run_type、加载并校验私有 profile，
  并把聚宽交易函数替换为失败关闭的 guard；LIVE 在 StrategyLedger 实盘
  闭环完成前保持阻断（enabled=False，交易函数同样被 guard）。
- 冷启动升级：helper/config/策略文件变更必须先停止聚宽策略、确认旧进程
  退出，再由平台启动全新进程。同一进程内重复安装仅在签名完全一致时幂等
  返回；签名漂移或检测到上一代 helper 遗留记录即失败关闭。
- 本 helper 运行在用户自有、可信的策略进程中，不防御同进程恶意 Python
  代码、monkey patch 或热重载攻击（docs/live-ledger/02-decisions.md D021）。
"""

import math
from typing import Any, Callable, Dict, Optional, Tuple

__all__ = [
    "STRATEGY_RUNTIME_API_VERSION",
    "STRATEGY_RUNTIME_HELPER_MARKER",
    "PROFILE_SCHEMA_VERSION",
    "install_strategy_runtime",
]

STRATEGY_RUNTIME_API_VERSION = 1
STRATEGY_RUNTIME_HELPER_MARKER = "bullet-trade-joinquant-runtime-helper-v1"
PROFILE_SCHEMA_VERSION = 1

DEFAULT_RPC_TIMEOUT_SECONDS = 60.0
DEFAULT_PLACE_ORDER_TIMEOUT_MARGIN_SECONDS = 30.0
DEFAULT_JQ_COMPAT_WAIT_TIMEOUT_SECONDS = 16.0

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
        "sub_account_id",
        "tls_cert",
        "retries",
        "retry_interval",
        "rpc_timeout",
        "place_order_timeout_margin",
        "default_wait_timeout",
        "debug",
    }
)
_PROFILE_ALLOWED_FIELDS = _PROFILE_REQUIRED_FIELDS | _PROFILE_OPTIONAL_FIELDS

# 本代 helper 实例标记；namespace 记录中的 token 不同即为上一代遗留。
_MODULE_TOKEN = object()

# 模块级安装记录：同一进程只允许一种安装签名。
_active_signature = None  # type: Optional[Tuple[Any, ...]]
_active_state = None  # type: Optional[Dict[str, Any]]


def _run_type_from_context(context: Any) -> Optional[str]:
    run_params = getattr(context, "run_params", None)
    if isinstance(run_params, dict):
        return run_params.get("type")
    return getattr(run_params, "type", None)


def _normalise_runtime_mode(mode: Any) -> str:
    if type(mode) is not str:
        raise RuntimeError("运行模式必须是普通字符串 BACKTEST、SHADOW 或 LIVE")
    value = str.upper(str.strip(mode))
    if value not in ("BACKTEST", "SHADOW", "LIVE"):
        raise RuntimeError("运行模式必须是 BACKTEST、SHADOW 或 LIVE")
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


def _load_runtime_profile(
    profile_module: str,
    profile: str,
    strategy_id: str,
) -> Dict[str, Any]:
    """加载并校验聚宽私有运行 profile；错误信息不回显凭据或未知字段名。"""

    load_failed = False
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

    retries = raw.get("retries", 2)
    if type(retries) is not int or not 0 <= retries <= 10:
        raise RuntimeError("profile.retries 必须是0到10之间的整数")

    numeric_rules = {
        "retry_interval": (0.5, 0.1, 30.0),
        "rpc_timeout": (DEFAULT_RPC_TIMEOUT_SECONDS, 5.0, 300.0),
        "place_order_timeout_margin": (
            DEFAULT_PLACE_ORDER_TIMEOUT_MARGIN_SECONDS,
            0.0,
            300.0,
        ),
        "default_wait_timeout": (DEFAULT_JQ_COMPAT_WAIT_TIMEOUT_SECONDS, 0.0, 300.0),
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

    debug = raw.get("debug", True)
    if type(debug) is not bool:
        raise RuntimeError("profile.debug 必须是布尔值")

    optional_strings = {}  # type: Dict[str, Optional[str]]
    for field in ("account_key", "sub_account_id", "tls_cert"):
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
        "sub_account_id": optional_strings["sub_account_id"],
        "tls_cert": optional_strings["tls_cert"],
        "retries": retries,
        "retry_interval": numeric_values["retry_interval"],
        "rpc_timeout": numeric_values["rpc_timeout"],
        "place_order_timeout_margin": numeric_values["place_order_timeout_margin"],
        "default_wait_timeout": numeric_values["default_wait_timeout"],
        "debug": debug,
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
) -> Dict[str, Any]:
    state = {
        "api_version": STRATEGY_RUNTIME_API_VERSION,
        "profile_schema_version": PROFILE_SCHEMA_VERSION,
        "profile": profile,
        "mode": mode,
        "run_type": run_type,
        "strategy_id": strategy_id,
        "enabled": False,
        "orders_enabled": mode == "BACKTEST",
        "production_ready": False,
        "reason": "backtest",
    }
    if mode == "SHADOW":
        state.update(
            {
                "profile_module": profile_module,
                "enabled": True,
                "orders_enabled": False,
                "reason": "shadow_read_only",
                "blocked_mutations": blocked_mutations,
            }
        )
    elif mode == "LIVE":
        state.update(
            {
                "profile_module": profile_module,
                "orders_enabled": False,
                "reason": "live_blocked_until_strategy_ledger",
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
) -> Dict[str, Any]:
    """安装策略运行模式并返回运行时状态；任何校验失败都抛出异常。

    同一进程内重复安装仅在签名完全一致时幂等返回原状态；签名漂移、
    上一代 helper 遗留记录或记录缺失均失败关闭，必须使用干净进程重启。
    """

    global _active_signature, _active_state

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
    signature = (mode, profile, strategy_id, profile_module, expected_api_version, run_type)
    if _active_state is not None:
        if _active_signature != signature:
            raise RuntimeError("策略运行安装签名漂移；必须使用干净运行进程重启")
        if mode != "BACKTEST":
            # 幂等重装仍补齐交易 guard，防止平台重建 namespace 后 guard 丢失
            _install_runtime_guards(namespace, mode)
        return dict(_active_state)

    blocked_mutations = ()  # type: Tuple[str, ...]
    if mode == "BACKTEST":
        if run_type not in ("simple_backtest", "full_backtest"):
            raise RuntimeError(
                "MODE=BACKTEST 仅允许聚宽回测，当前run_type={}".format(
                    run_type or "<empty>"
                )
            )
        state = _build_strategy_runtime_state(
            mode=mode,
            run_type=run_type,
            strategy_id=strategy_id,
            profile=profile,
        )
    else:
        if run_type != "sim_trade":
            raise RuntimeError(
                "MODE={} 仅允许聚宽模拟交易，当前run_type={}".format(
                    mode, run_type or "<empty>"
                )
            )
        _load_runtime_profile(profile_module, profile, strategy_id)
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
    namespace[_RUNTIME_STATE_KEY] = {"token": _MODULE_TOKEN, "mode": mode}
    return dict(state)
