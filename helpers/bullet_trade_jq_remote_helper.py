"""
聚宽远程辅助模块（短连接版）

使用方法：
1. 将本文件复制到聚宽研究环境根目录；
2. 新策略优先使用版本化运行入口（连接信息来自私有 jq_runtime_config.py）：
   import bullet_trade_jq_remote_helper as bt
   bt.install_strategy_runtime(
       globals(), context=context, profile='good_etf-prod', mode='SHADOW',
       strategy_id='good_etf', expected_api_version=1)
3. 兼容旧策略时也可以直接配置底层客户端：
   import bullet_trade_jq_remote_helper as bt
   bt.configure(host='你的IP', token='你的token', port=58620, account_key='main', sub_account_id='demo@main')
   acct = bt.get_account()
   oid = bt.order('000001.XSHE', amount=100, price=None, side='BUY', wait_timeout=10)
   bt.cancel_order(oid)
4. 如果希望聚宽模拟盘里尽量不改原策略下单代码，可在 process_initialize 里调用：
   bt.install_jq_compat(globals(), context=context, host='你的IP', token='你的token')

特点：
- 每次调用都会重新建立 TCP 连接，适合聚宽频繁重启。
- 服务端统一处理：最小手数/步进取整、停牌检查、价格笼子、涨跌停校验、可卖数量检查。
- 支持同步/异步：wait_timeout>0 时轮询订单状态，否则立即返回。
- 提供 account/positions/order_status/orders/cancel/order_value/order_target 等常见聚宽风格 API。
- install_jq_compat 在回测中不接管；在聚宽模拟盘中接管账户状态和同名下单函数，默认同步等待 16 秒。
- install_strategy_runtime 提供 BACKTEST/SHADOW/LIVE、profile schema和API版本边界；三种模式均先建立进程门禁再读取context，BACKTEST不联网，当前LIVE只校验配置并保持交易关闭。
"""

# importlib.reload()会复用原module字典。以下入口只是误用检测和失败关闭防线，
# 不是受支持的热更新接口；生产升级必须停止旧进程并冷启动。正常控制流下，
# 它会在本文件的import和新代初始化前调用上一代闭包锚定的关闭入口。
_early_public_runtime_reload_bootstrap = globals().get(
    "_run_runtime_reload_bootstrap"
)
if "_STRATEGY_RUNTIME_MODULE_GENERATION" in globals():
    _early_runtime_reload_bootstrap = None
    _early_failure_namespaces = []
    try:
        _early_primitive_anchor_getter = globals().get(
            "_get_runtime_primitive_anchor"
        )
        if type(_early_primitive_anchor_getter) is not type(lambda: None):
            raise RuntimeError(
                "策略运行helper缺少可信primitive anchor；必须使用干净运行进程重启"
            )
        _early_primitive_anchor = _early_primitive_anchor_getter()
        if (
            type(_early_primitive_anchor) is not tuple
            or len(_early_primitive_anchor) != 10
            or type(_early_primitive_anchor[9]) is not tuple
            or len(_early_primitive_anchor[9]) != 3
            or type(_early_primitive_anchor[9][0]) is not object
            or type(_early_primitive_anchor[9][1]) is not type(lambda: None)
            or type(_early_primitive_anchor[9][2]) is not type(lambda: None)
        ):
            raise RuntimeError(
                "策略运行helper的reload entry anchor无效；必须使用干净运行进程重启"
            )
        _early_runtime_reload_bootstrap = _early_primitive_anchor[9][1]()
        if (
            type(_early_runtime_reload_bootstrap) is not type(lambda: None)
            or _early_public_runtime_reload_bootstrap
            is not _early_runtime_reload_bootstrap
        ):
            raise RuntimeError(
                "策略运行helper的公开reload入口与封存入口不一致；必须使用干净运行进程重启"
            )
        # 在bootstrap可能清除commit anchor之前保存namespace identity；若其在
        # FAILED发布与record删除之间中断，fallback仍能完成guard/record清理。
        _early_transition_namespace = globals().get(
            "_STRATEGY_RUNTIME_TRANSITION_NAMESPACE"
        )
        if type(_early_transition_namespace) is dict:
            list.append(_early_failure_namespaces, _early_transition_namespace)
        _early_commit_anchor_getter = globals().get("_get_runtime_commit_anchor")
        if type(_early_commit_anchor_getter) is type(lambda: None):
            _early_commit_capsule = _early_commit_anchor_getter()
            if type(_early_commit_capsule) is tuple and len(
                _early_commit_capsule
            ) == 10:
                _early_committed_namespace = _early_commit_capsule[8]
                if type(_early_committed_namespace) is dict and all(
                    value is not _early_committed_namespace
                    for value in _early_failure_namespaces
                ):
                    list.append(
                        _early_failure_namespaces,
                        _early_committed_namespace,
                    )
        _early_runtime_reload_bootstrap()
    except BaseException:
        # 在异常能进入本fallback的正常控制流下，只使用内建原语执行最小失败
        # 关闭并传播首个异常；任意异步中断不能靠纯Python证明完整清理。
        _early_failure_namespaces = globals().get("_early_failure_namespaces")
        if type(_early_failure_namespaces) is not list:
            _early_failure_namespaces = []
        # 防御性重试同一锚定闭包；无论结果如何，调用方都必须终止本次进程，
        # 不得捕获异常后继续旧调用栈。
        try:
            if type(_early_runtime_reload_bootstrap) is type(lambda: None):
                _early_runtime_reload_bootstrap()
        except BaseException:
            pass
        try:
            _early_namespace_failure_publisher = globals().get(
                "_mark_runtime_failed"
            )
            if type(_early_namespace_failure_publisher) is type(lambda: None):
                for _early_failure_namespace in _early_failure_namespaces:
                    _early_namespace_failure_publisher(_early_failure_namespace)
        except BaseException:
            pass
        try:
            _early_gate_authority = globals().get(
                "_STRATEGY_RUNTIME_SOCKET_GATE_AUTHORITY"
            )
            if (
                type(_early_gate_authority) is tuple
                and len(_early_gate_authority) == 7
                and type(_early_gate_authority[2]) is type(lambda: None)
            ):
                _early_gate_authority[2]()
        except BaseException:
            pass
        try:
            _early_failed_state_publisher = globals().get(
                "_set_runtime_failed_process_state"
            )
            if type(_early_failed_state_publisher) is type(lambda: None):
                _early_failed_state_publisher()
        except BaseException:
            pass
        _STRATEGY_RUNTIME_ACTIVE_MODE = "FAILED"
        _STRATEGY_RUNTIME_RELOAD_IN_PROGRESS = True
        _early_contract_generation = globals().get(
            "_STRATEGY_RUNTIME_CONTRACT_GENERATION"
        )
        _STRATEGY_RUNTIME_CONTRACT_GENERATION = (
            _early_contract_generation
            if type(_early_contract_generation) is int
            and _early_contract_generation >= 1
            else 1
        )
        _STRATEGY_RUNTIME_PROCESS_SIGNATURE = None
        _STRATEGY_RUNTIME_CANONICAL_STATE = None
        _STRATEGY_RUNTIME_COMMIT_CAPSULE = None
        _STRATEGY_RUNTIME_INFLIGHT_REQUESTS = 0
        _STRATEGY_RUNTIME_TRANSITION_OWNER = None
        _STRATEGY_RUNTIME_TRANSITION_NAMESPACE = None
        _STRATEGY_RUNTIME_TRANSITION_MODE = None
        _CLIENT = None
        _DATA_CLIENT = None
        _BROKER_CLIENT = None
        raise

import _thread
import ast
import functools
import hashlib
import json
import math
import os
import socket
import ssl
import struct
import sys
import threading
import time
import traceback
import types
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

import pandas as pd


def _create_runtime_socket_gate_authority(thread_ident_getter):
    """用闭包保存单向reload latch、attempt identity及transport许可。"""

    reload_requested = False
    active_attempts = {}

    def snapshot():
        return reload_requested, tuple(dict.items(active_attempts))

    def close_for_reload():
        nonlocal reload_requested
        reload_requested = True
        return len(active_attempts)

    def start_attempt(attempt_token):
        if type(attempt_token) is not object:
            return False
        owner_thread = thread_ident_getter()
        if type(owner_thread) is not int:
            return False
        # 正常跨线程执行以本行为attempt登记线性化点；生产禁止在trace/signal
        # 回调中递归reload并捕获后续跑旧栈。
        return False if reload_requested else dict.__setitem__(active_attempts, attempt_token, owner_thread) is None  # noqa: E501

    def finish_attempt(attempt_token):
        if type(attempt_token) is not object:
            return False, len(active_attempts)
        removed_owner = dict.pop(active_attempts, attempt_token, None)
        return type(removed_owner) is int, len(active_attempts)

    def open_transport(
        attempt_token,
        owner_thread,
        connector,
        address,
        timeout,
        resource_state,
    ):
        """校验当前permit并在同一调用边界进入transport。"""

        if (
            type(attempt_token) is not object
            or type(owner_thread) is not int
            or type(resource_state) is not list
            or len(resource_state) != 1
            or resource_state[0] is not None
        ):
            return False
        # 正常跨线程执行以latch/token读取为transport许可线性化点。任意opcode
        # 异步回调后的catch-and-resume不属于支持边界，必须终止运行进程。
        return False if reload_requested or dict.get(active_attempts, attempt_token) != owner_thread else list.__setitem__(resource_state, 0, connector(address, timeout=timeout)) is None  # noqa: E501

    def run_remote_effect(
        effect,
        effect_args,
        effect_kwargs,
        handoff_state,
    ):
        """在线性化permit后执行TLS/send；mutation先武装不确定性交付。"""

        if (
            type(effect_args) is not tuple
            or type(effect_kwargs) is not dict
            or (
                handoff_state is not None
                and (
                    type(handoff_state) is not list
                    or len(handoff_state) != 1
                    or type(handoff_state[0]) is not bool
                )
            )
        ):
            return False, None
        # 正常跨线程执行中，reload先关latch则拒绝effect；先读到open则effect
        # 属于已线性化在途操作。mutation handoff在effect调用前同表达式武装。
        return (False, None) if reload_requested else (True, ((list.__setitem__(handoff_state, 0, True) if handoff_state is not None else None), effect(*effect_args, **effect_kwargs))[1])  # noqa: E501

    return (
        object(),
        snapshot,
        close_for_reload,
        start_attempt,
        finish_attempt,
        open_transport,
        run_remote_effect,
    )


def _create_runtime_quarantine_anchor():
    """永久保留FAILED路径中的不可信对象，避免其析构器在锁存期间执行。"""

    retained_values = []

    def retain(value):
        list.append(retained_values, value)
        return len(retained_values)

    return retain


def _create_runtime_reload_entry_authority():
    """一次性封存下一次reload所需入口，公开同名函数不能替换其identity。"""

    reload_entry = None

    def get_entry():
        return reload_entry

    def set_entry_once(value):
        nonlocal reload_entry
        if reload_entry is not None:
            return False
        reload_entry = value
        return True

    return object(), get_entry, set_entry_once


# importlib.reload()会复用原module字典。旧状态只通过精确内建类型读取，任何
# poison值都不得在FAILED状态和client清理完成前中断新模块初始化。
_HAD_PREVIOUS_RUNTIME_MODULE = "_STRATEGY_RUNTIME_MODULE_GENERATION" in globals()
_previous_runtime_primitive_anchor = None
_previous_runtime_primitive_anchor_getter = globals().get(
    "_get_runtime_primitive_anchor"
)
if (
    _HAD_PREVIOUS_RUNTIME_MODULE
    and type(_previous_runtime_primitive_anchor_getter) is types.FunctionType
):
    try:
        _candidate_runtime_primitive_anchor = (
            _previous_runtime_primitive_anchor_getter()
        )
        if (
            type(_candidate_runtime_primitive_anchor) is tuple
            and len(_candidate_runtime_primitive_anchor) == 10
        ):
            _previous_runtime_primitive_anchor = _candidate_runtime_primitive_anchor
    except BaseException:
        _previous_runtime_primitive_anchor = None
_previous_commit_anchor_setter = globals().get("_set_runtime_commit_anchor")
_previous_failed_anchor = globals().get("_STRATEGY_RUNTIME_FAILED_ANCHOR")
_reload_gate_condition = None
_reload_gate_authority = None
_reload_gate_lock = None
_reload_quarantine_retain = None
_previous_condition_lock = None
if (
    type(_previous_runtime_primitive_anchor) is tuple
    and len(_previous_runtime_primitive_anchor) == 10
    and type(_previous_runtime_primitive_anchor[5]) is threading.Condition
):
    try:
        _previous_condition_lock = object.__getattribute__(
            _previous_runtime_primitive_anchor[5],
            "_lock",
        )
    except BaseException:
        _previous_condition_lock = None
if (
    _HAD_PREVIOUS_RUNTIME_MODULE
    and type(_previous_runtime_primitive_anchor) is tuple
    and type(_previous_runtime_primitive_anchor[0]) is _thread.RLock
    and type(_previous_runtime_primitive_anchor[1]) is _thread.LockType
    and _previous_runtime_primitive_anchor[0]
    is globals().get("_STRATEGY_RUNTIME_LOCK")
    and _previous_runtime_primitive_anchor[1]
    is globals().get("_STRATEGY_RUNTIME_OWNER_LOCK")
    and type(_previous_runtime_primitive_anchor[5]) is threading.Condition
    and _previous_runtime_primitive_anchor[5]
    is globals().get("_STRATEGY_RUNTIME_SOCKET_CONDITION")
    and type(_previous_runtime_primitive_anchor[6]) is tuple
    and len(_previous_runtime_primitive_anchor[6]) == 7
    and _previous_runtime_primitive_anchor[6]
    is globals().get("_STRATEGY_RUNTIME_SOCKET_GATE_AUTHORITY")
    and type(_previous_runtime_primitive_anchor[6][0]) is object
    and all(
        type(function) is types.FunctionType
        for function in _previous_runtime_primitive_anchor[6][1:]
    )
    and type(_previous_runtime_primitive_anchor[7]) is _thread.RLock
    and _previous_runtime_primitive_anchor[7]
    is globals().get("_STRATEGY_RUNTIME_SOCKET_LOCK")
    and _previous_condition_lock is _previous_runtime_primitive_anchor[7]
    and type(_previous_runtime_primitive_anchor[8]) is types.FunctionType
    and _previous_runtime_primitive_anchor[8]
    is globals().get("_STRATEGY_RUNTIME_QUARANTINE_RETAIN")
    and type(_previous_runtime_primitive_anchor[9]) is tuple
    and len(_previous_runtime_primitive_anchor[9]) == 3
    and _previous_runtime_primitive_anchor[9]
    is globals().get("_STRATEGY_RUNTIME_RELOAD_ENTRY_AUTHORITY")
    and type(_previous_runtime_primitive_anchor[9][0]) is object
    and all(
        type(function) is types.FunctionType
        for function in _previous_runtime_primitive_anchor[9][1:]
    )
):
    _reload_gate_condition = _previous_runtime_primitive_anchor[5]
    _reload_gate_authority = _previous_runtime_primitive_anchor[6]
    _reload_gate_lock = _previous_runtime_primitive_anchor[7]
    _reload_quarantine_retain = _previous_runtime_primitive_anchor[8]
_reload_gate_error = None


def _publish_early_runtime_reload_latch():
    global _STRATEGY_RUNTIME_ACTIVE_MODE
    global _STRATEGY_RUNTIME_RELOAD_IN_PROGRESS
    global _STRATEGY_RUNTIME_CONTRACT_GENERATION
    global _STRATEGY_RUNTIME_TRANSITION_OWNER
    global _STRATEGY_RUNTIME_TRANSITION_NAMESPACE
    global _STRATEGY_RUNTIME_TRANSITION_MODE
    global _CLIENT, _DATA_CLIENT, _BROKER_CLIENT

    if _HAD_PREVIOUS_RUNTIME_MODULE:
        _STRATEGY_RUNTIME_ACTIVE_MODE = "FAILED"
        _STRATEGY_RUNTIME_RELOAD_IN_PROGRESS = True
        _reload_contract_generation = globals().get(
            "_STRATEGY_RUNTIME_CONTRACT_GENERATION"
        )
        _STRATEGY_RUNTIME_CONTRACT_GENERATION = (
            _reload_contract_generation
            if type(_reload_contract_generation) is int
            and _reload_contract_generation >= 1
            else 1
        )
        _STRATEGY_RUNTIME_TRANSITION_OWNER = None
        _STRATEGY_RUNTIME_TRANSITION_NAMESPACE = None
        _STRATEGY_RUNTIME_TRANSITION_MODE = None
        _CLIENT = None
        _DATA_CLIENT = None
        _BROKER_CLIENT = None
        if (
            type(_previous_commit_anchor_setter) is types.FunctionType
            and type(_previous_failed_anchor) is object
        ):
            try:
                _previous_commit_anchor_setter(_previous_failed_anchor)
            except BaseException:
                pass
    else:
        _STRATEGY_RUNTIME_RELOAD_IN_PROGRESS = False


_reload_gate_done = _reload_gate_lock is None
while not _reload_gate_done:
    try:
        # socket-gate RLock的with先建立释放处理，再执行后续Python行。
        with _reload_gate_lock:
            gate_snapshot = _reload_gate_authority[1]
            gate_close_for_reload = _reload_gate_authority[2]
            try:
                gate_close_for_reload()
                while True:
                    gate_state = gate_snapshot()
                    if (
                        type(gate_state) is not tuple
                        or len(gate_state) != 2
                        or type(gate_state[0]) is not bool
                        or not gate_state[0]
                        or type(gate_state[1]) is not tuple
                        or any(
                            type(entry) is not tuple
                            or len(entry) != 2
                            or type(entry[0]) is not object
                            or type(entry[1]) is not int
                            for entry in gate_state[1]
                        )
                    ):
                        raise RuntimeError("策略运行socket gate闭包状态无效")
                    if not gate_state[1]:
                        break
                    try:
                        threading.Condition.wait(_reload_gate_condition)
                    except BaseException as exc:
                        if _reload_gate_error is None:
                            _reload_gate_error = exc
                        break
            except BaseException as exc:
                if _reload_gate_error is None:
                    _reload_gate_error = exc
            _publish_early_runtime_reload_latch()
            _reload_gate_done = True
    except BaseException as exc:
        if _reload_gate_error is None:
            _reload_gate_error = exc
if _reload_gate_lock is None:
    try:
        _publish_early_runtime_reload_latch()
    except BaseException as exc:
        if _reload_gate_error is None:
            _reload_gate_error = exc
if _reload_gate_error is not None:
    raise _reload_gate_error
_previous_module_generation = globals().get("_STRATEGY_RUNTIME_MODULE_GENERATION")
_PREVIOUS_RUNTIME_GENERATION = (
    _previous_module_generation
    if type(_previous_module_generation) is int
    and _previous_module_generation >= 1
    else (1 if _HAD_PREVIOUS_RUNTIME_MODULE else 0)
)
_STRATEGY_RUNTIME_MODULE_GENERATION = _PREVIOUS_RUNTIME_GENERATION + 1
_runtime_rlock_type = type(threading.RLock())
_existing_runtime_lock = globals().get("_STRATEGY_RUNTIME_LOCK")
_STRATEGY_RUNTIME_LOCK = (
    _existing_runtime_lock
    if type(_existing_runtime_lock) is _runtime_rlock_type
    and type(_previous_runtime_primitive_anchor) is tuple
    and _previous_runtime_primitive_anchor[0] is _existing_runtime_lock
    else threading.RLock()
)
_runtime_owner_lock_type = type(threading.Lock())
_existing_runtime_owner_lock = globals().get("_STRATEGY_RUNTIME_OWNER_LOCK")
_STRATEGY_RUNTIME_OWNER_LOCK = (
    _existing_runtime_owner_lock
    if type(_existing_runtime_owner_lock) is _runtime_owner_lock_type
    and type(_previous_runtime_primitive_anchor) is tuple
    and _previous_runtime_primitive_anchor[1] is _existing_runtime_owner_lock
    else threading.Lock()
)
_STRATEGY_RUNTIME_SOCKET_LOCK = (
    _reload_gate_lock
    if type(_reload_gate_lock) is _runtime_rlock_type
    else threading.RLock()
)
_STRATEGY_RUNTIME_SOCKET_CONDITION = (
    _reload_gate_condition
    if type(_reload_gate_condition) is threading.Condition
    else threading.Condition(_STRATEGY_RUNTIME_SOCKET_LOCK)
)
_STRATEGY_RUNTIME_SOCKET_GATE_AUTHORITY = (
    _reload_gate_authority
    if type(_reload_gate_authority) is tuple
    else _create_runtime_socket_gate_authority(threading.get_ident)
)
_STRATEGY_RUNTIME_QUARANTINE_RETAIN = (
    _reload_quarantine_retain
    if type(_reload_quarantine_retain) is types.FunctionType
    else _create_runtime_quarantine_anchor()
)
_STRATEGY_RUNTIME_INSTANCE_TOKEN = object()
_STRATEGY_RUNTIME_REQUEST_LEASES: Set[object] = set()
_previous_inflight_requests = globals().get("_STRATEGY_RUNTIME_INFLIGHT_REQUESTS")
_STRATEGY_RUNTIME_INFLIGHT_REQUESTS = (
    _previous_inflight_requests
    if not _HAD_PREVIOUS_RUNTIME_MODULE
    and type(_previous_inflight_requests) is int
    and _previous_inflight_requests >= 0
    else 0
)
_previous_contract_generation = globals().get("_STRATEGY_RUNTIME_CONTRACT_GENERATION")
_safe_previous_contract_generation = (
    _previous_contract_generation
    if type(_previous_contract_generation) is int
    and _previous_contract_generation >= 0
    else 0
)
_STRATEGY_RUNTIME_CONTRACT_GENERATION = (
    max(1, _safe_previous_contract_generation)
    if _HAD_PREVIOUS_RUNTIME_MODULE
    else _safe_previous_contract_generation
)
_STRATEGY_RUNTIME_TRANSITION_OWNER = None
_STRATEGY_RUNTIME_TRANSITION_NAMESPACE = None
_STRATEGY_RUNTIME_TRANSITION_MODE = None

_CLIENT: Optional["_ShortLivedClient"] = None
_DATA_CLIENT: Optional["RemoteDataClient"] = None
_BROKER_CLIENT: Optional["RemoteBrokerClient"] = None
_STRATEGY_RUNTIME_ACTIVE_MODE: Optional[str] = (
    "FAILED" if _HAD_PREVIOUS_RUNTIME_MODULE else None
)
_STRATEGY_RUNTIME_PROCESS_SIGNATURE: Optional[Tuple[Any, ...]] = None
_STRATEGY_RUNTIME_CANONICAL_STATE: Optional[Dict[str, Any]] = None
_STRATEGY_RUNTIME_COMMIT_CAPSULE: Optional[Tuple[Any, ...]] = None
_STRATEGY_RUNTIME_FAILED_ANCHOR = (
    _previous_failed_anchor
    if _HAD_PREVIOUS_RUNTIME_MODULE and type(_previous_failed_anchor) is object
    else object()
)


def _create_runtime_commit_anchor(initial_anchor=None):
    """用闭包保存胶囊identity，避免仅替换模块全局即可伪造提交。"""

    anchored_capsule = initial_anchor

    def get_anchor():
        return anchored_capsule

    def set_anchor(value):
        nonlocal anchored_capsule
        anchored_capsule = value

    return get_anchor, set_anchor


def _create_runtime_primitive_anchor(
    runtime_lock,
    owner_lock,
    instance_token,
    module_generation,
    request_leases,
    socket_condition,
    socket_gate_authority,
    socket_lock,
    quarantine_retain,
    reload_entry_authority,
):
    """锚定模块实例、runtime/owner/socket原语，拒绝只替换全局的伪造状态。"""

    anchored_values = (
        runtime_lock,
        owner_lock,
        instance_token,
        module_generation,
        request_leases,
        socket_condition,
        socket_gate_authority,
        socket_lock,
        quarantine_retain,
        reload_entry_authority,
    )

    def get_anchor():
        return anchored_values

    return get_anchor


(
    _get_runtime_commit_anchor,
    _set_runtime_commit_anchor,
) = _create_runtime_commit_anchor(
    _STRATEGY_RUNTIME_FAILED_ANCHOR if _HAD_PREVIOUS_RUNTIME_MODULE else None
)
_STRATEGY_RUNTIME_RELOAD_ENTRY_AUTHORITY = _create_runtime_reload_entry_authority()
_get_runtime_primitive_anchor = _create_runtime_primitive_anchor(
    _STRATEGY_RUNTIME_LOCK,
    _STRATEGY_RUNTIME_OWNER_LOCK,
    _STRATEGY_RUNTIME_INSTANCE_TOKEN,
    _STRATEGY_RUNTIME_MODULE_GENERATION,
    _STRATEGY_RUNTIME_REQUEST_LEASES,
    _STRATEGY_RUNTIME_SOCKET_CONDITION,
    _STRATEGY_RUNTIME_SOCKET_GATE_AUTHORITY,
    _STRATEGY_RUNTIME_SOCKET_LOCK,
    _STRATEGY_RUNTIME_QUARANTINE_RETAIN,
    _STRATEGY_RUNTIME_RELOAD_ENTRY_AUTHORITY,
)

# 全局调试开关
_DEBUG: bool = True
HELPER_PROTOCOL_VERSION: int = 1
STRATEGY_RUNTIME_API_VERSION: int = 1
STRATEGY_RUNTIME_HELPER_MARKER: str = "bullet-trade-joinquant-runtime-helper-v1"
PROFILE_SCHEMA_VERSION: int = 1
STRATEGY_RUNTIME_STATE_SCHEMA_VERSION: int = 1
DEFAULT_RPC_TIMEOUT_SECONDS: float = 60.0
DEFAULT_PLACE_ORDER_TIMEOUT_MARGIN_SECONDS: float = 30.0
DEFAULT_JQ_COMPAT_WAIT_TIMEOUT_SECONDS: float = 16.0


def _runtime_primitive_anchor_snapshot() -> Optional[Tuple[Any, ...]]:
    """返回经identity校验的模块原语锚；全程不比较候选对象的值。"""

    try:
        anchor = _get_runtime_primitive_anchor()
    except BaseException:
        return None
    gate_authority_valid = False
    condition_lock_valid = False
    if type(anchor) is tuple and len(anchor) == 10 and type(anchor[5]) is threading.Condition:
        try:
            condition_lock_valid = (
                object.__getattribute__(anchor[5], "_lock") is anchor[7]
            )
        except BaseException:
            condition_lock_valid = False
    if type(anchor) is tuple and len(anchor) == 10 and type(anchor[6]) is tuple:
        gate_authority_valid = (
            len(anchor[6]) == 7
            and type(anchor[6][0]) is object
            and all(type(function) is types.FunctionType for function in anchor[6][1:])
        )
    if (
        type(anchor) is not tuple
        or len(anchor) != 10
        or anchor[0] is not _STRATEGY_RUNTIME_LOCK
        or anchor[1] is not _STRATEGY_RUNTIME_OWNER_LOCK
        or type(anchor[2]) is not object
        or anchor[2] is not _STRATEGY_RUNTIME_INSTANCE_TOKEN
        or type(anchor[3]) is not int
        or anchor[3] < 1
        or type(_STRATEGY_RUNTIME_MODULE_GENERATION) is not int
        or _STRATEGY_RUNTIME_MODULE_GENERATION != anchor[3]
        or type(anchor[4]) is not set
        or anchor[4] is not _STRATEGY_RUNTIME_REQUEST_LEASES
        or type(anchor[5]) is not threading.Condition
        or anchor[5] is not _STRATEGY_RUNTIME_SOCKET_CONDITION
        or type(anchor[6]) is not tuple
        or anchor[6] is not _STRATEGY_RUNTIME_SOCKET_GATE_AUTHORITY
        or type(anchor[7]) is not _thread.RLock
        or anchor[7] is not _STRATEGY_RUNTIME_SOCKET_LOCK
        or type(anchor[8]) is not types.FunctionType
        or anchor[8] is not _STRATEGY_RUNTIME_QUARANTINE_RETAIN
        or type(anchor[9]) is not tuple
        or anchor[9] is not _STRATEGY_RUNTIME_RELOAD_ENTRY_AUTHORITY
        or len(anchor[9]) != 3
        or type(anchor[9][0]) is not object
        or any(type(function) is not types.FunctionType for function in anchor[9][1:])
        or not condition_lock_valid
        or not gate_authority_valid
    ):
        return None
    return anchor


def _runtime_socket_gate_authority_snapshot(
    anchor: Any,
) -> Optional[Tuple[bool, Tuple[Tuple[object, int], ...]]]:
    """读取闭包权威gate；公开active/reload镜像不能重新打开该单向latch。"""

    if (
        type(anchor) is not tuple
        or len(anchor) != 10
        or type(anchor[6]) is not tuple
        or len(anchor[6]) != 7
        or type(anchor[6][1]) is not types.FunctionType
    ):
        return None
    try:
        state = anchor[6][1]()
    except BaseException:
        return None
    if (
        type(state) is not tuple
        or len(state) != 2
        or type(state[0]) is not bool
        or type(state[1]) is not tuple
        or any(
            type(entry) is not tuple
            or len(entry) != 2
            or type(entry[0]) is not object
            or type(entry[1]) is not int
            for entry in state[1]
        )
    ):
        return None
    return state


def _require_trusted_runtime_lock(
    namespace: Optional[Dict[str, Any]] = None,
):
    anchor = _runtime_primitive_anchor_snapshot()
    if anchor is None:
        _fail_runtime_generation_drift(namespace)
        raise RuntimeError(
            "策略运行同步原语或helper代际无效；必须使用干净运行进程重启"
        )
    return anchor[0]


def _require_trusted_runtime_owner_lock(
    namespace: Optional[Dict[str, Any]] = None,
):
    anchor = _runtime_primitive_anchor_snapshot()
    if anchor is None:
        _fail_runtime_generation_drift(namespace)
        raise RuntimeError(
            "策略运行同步原语或helper代际无效；必须使用干净运行进程重启"
        )
    return anchor[1]


def _runtime_transition_snapshot(
) -> Tuple[bool, Optional[int], Optional[Dict[str, Any]], Optional[str]]:
    """只接受固定内建类型的transition三元组，避免候选值参与魔术比较。"""

    owner = _STRATEGY_RUNTIME_TRANSITION_OWNER
    namespace = _STRATEGY_RUNTIME_TRANSITION_NAMESPACE
    mode = _STRATEGY_RUNTIME_TRANSITION_MODE
    if owner is None:
        return namespace is None and mode is None, None, None, None
    if (
        type(owner) is not int
        or (namespace is not None and type(namespace) is not dict)
        or (
            mode is not None
            and (
                type(mode) is not str
                or mode not in {"BACKTEST", "SHADOW", "LIVE"}
            )
        )
    ):
        return False, None, None, None
    return True, owner, namespace, mode


def _runtime_request_registry_snapshot(
    registry: Any,
) -> Optional[Tuple[object, ...]]:
    """只通过精确set迭代和元素类型/identity观察lease，不执行元素hash/eq。"""

    if type(registry) is not set:
        return None
    try:
        values = tuple(set.__iter__(registry))
    except BaseException:
        return None
    if any(type(value) is not object for value in values):
        return None
    return values


def _runtime_request_registry_contains_identity(
    registry_snapshot: Optional[Tuple[object, ...]],
    request_token: Any,
) -> bool:
    return (
        type(registry_snapshot) is tuple
        and type(request_token) is object
        and any(value is request_token for value in registry_snapshot)
    )


def _discard_runtime_request_lease_by_identity(
    registry: Any,
    request_token: Any,
) -> bool:
    snapshot = _runtime_request_registry_snapshot(registry)
    if type(request_token) is not object or snapshot is None:
        return False
    for value in snapshot:
        if value is request_token:
            set.remove(registry, value)
            return True
    return False


def _clear_or_quarantine_runtime_request_registry(
    registry: Any,
    quarantine_retain: Any,
) -> None:
    """清空可信token；不可信元素先保留强引用，避免执行其析构器。"""

    if type(registry) is not set:
        return
    try:
        values = tuple(set.__iter__(registry))
    except BaseException:
        return
    if all(type(value) is object for value in values):
        set.clear(registry)
        return
    if type(quarantine_retain) is not types.FunctionType:
        return
    try:
        retained_count = quarantine_retain(values)
    except BaseException:
        return
    if type(retained_count) is int and retained_count > 0:
        set.clear(registry)


def _serialise_runtime_boundary(function: Callable[..., Any]) -> Callable[..., Any]:
    """串行化配置、旧兼容安装和runtime状态切换。"""

    boundary_instance_token = _STRATEGY_RUNTIME_INSTANCE_TOKEN
    boundary_module_generation = _STRATEGY_RUNTIME_MODULE_GENERATION
    boundary_operation = function.__name__
    is_runtime_install = boundary_operation == "install_strategy_runtime"

    def locked_impl(boundary_attempt_state, *args, **kwargs):
        global _STRATEGY_RUNTIME_TRANSITION_OWNER
        global _STRATEGY_RUNTIME_TRANSITION_NAMESPACE
        global _STRATEGY_RUNTIME_TRANSITION_MODE
        global _STRATEGY_RUNTIME_ACTIVE_MODE
        global _STRATEGY_RUNTIME_CONTRACT_GENERATION

        current_thread = threading.get_ident()
        namespace = None
        if args and type(args[0]) is dict:
            namespace = args[0]
        elif type(kwargs.get("namespace")) is dict:
            namespace = kwargs["namespace"]
        requested_mode = kwargs.get("mode")
        requested_remote_mode = (
            str.upper(str.strip(requested_mode))
            if type(requested_mode) is str
            else None
        )
        invalid_runtime_state = False
        if type(current_thread) is not int or not _runtime_module_generation_matches(
            boundary_instance_token,
            boundary_module_generation,
        ):
            _fail_runtime_generation_drift(namespace)
            raise RuntimeError(
                "策略运行helper代际或线程状态无效；必须使用干净运行进程重启"
            )

        def call_with_generation_check():
            generation_drift = False
            if not _runtime_module_generation_matches(
                boundary_instance_token,
                boundary_module_generation,
            ):
                _fail_runtime_generation_drift(namespace)
                raise RuntimeError(
                    "策略运行helper代际无效；必须使用干净运行进程重启"
                )
            try:
                if not is_runtime_install:
                    kwargs["_runtime_boundary_attempt_state"] = (
                        boundary_attempt_state
                    )
                result = function(*args, **kwargs)
            except BaseException:
                if not _runtime_module_generation_matches(
                    boundary_instance_token,
                    boundary_module_generation,
                ):
                    generation_drift = True
                else:
                    raise
            if generation_drift:
                _fail_runtime_generation_drift(namespace)
                # 必须离开except作用域后再抛出，确保异常对象本身不保留
                # 可能含凭据的__context__引用。
                raise RuntimeError(
                    "策略运行helper在调用期间发生重载；必须使用干净运行进程重启"
                )
            if not _runtime_module_generation_matches(
                boundary_instance_token,
                boundary_module_generation,
            ):
                _fail_runtime_generation_drift(namespace)
                raise RuntimeError(
                    "策略运行helper在调用期间发生重载；必须使用干净运行进程重启"
                )
            # reload bootstrap先单调关闭权威gate、再等待runtime锁。该检查位于
            # 最终代际检查之后，确保已开始的reload不能跨过成功返回边界。
            try:
                _assert_runtime_reload_latch_open("返回策略运行边界")
            except BaseException:
                _fail_runtime_generation_drift(namespace)
                raise
            return result

        if not is_runtime_install:
            transition_valid, owner, _, _ = _runtime_transition_snapshot()
            if not transition_valid:
                _fail_runtime_generation_drift(namespace)
                raise RuntimeError(
                    "策略运行transition状态无效；必须使用干净运行进程重启"
                )
            if owner is not None:
                _assert_runtime_mutation_allowed(boundary_operation)
                raise RuntimeError("策略运行模式正在切换；拒绝并发配置、请求或重复安装")
            runtime_lock = _require_trusted_runtime_lock(namespace)
            with runtime_lock:
                transition_valid, owner, _, _ = _runtime_transition_snapshot()
                if not transition_valid:
                    _fail_runtime_generation_drift(namespace)
                    raise RuntimeError(
                        "策略运行transition状态无效；必须使用干净运行进程重启"
                    )
                if owner is not None:
                    _assert_runtime_mutation_allowed(boundary_operation)
                    raise RuntimeError(
                        "策略运行模式正在切换；拒绝并发配置、请求或重复安装"
                    )
                # 先执行无副作用的模式策略检查；SHADOW/BACKTEST等预期拒绝不应
                # 污染已提交runtime。通过后才登记不可重入mutation reservation。
                _assert_runtime_mutation_allowed(boundary_operation)
                owner_lock = _require_trusted_runtime_owner_lock(namespace)
                with owner_lock:
                    transition_valid, owner, _, _ = _runtime_transition_snapshot()
                    if not transition_valid or owner is not None:
                        _fail_runtime_generation_drift(namespace)
                        raise RuntimeError(
                            "策略运行mutation reservation无效；必须使用干净运行进程重启"
                        )
                    boundary_attempt_state[0] = True
                    _STRATEGY_RUNTIME_TRANSITION_OWNER = current_thread
                    _STRATEGY_RUNTIME_TRANSITION_NAMESPACE = namespace
                    _STRATEGY_RUNTIME_TRANSITION_MODE = None
                mutation_call_completed = False
                try:
                    result = call_with_generation_check()
                    mutation_call_completed = True
                    return result
                finally:
                    owner_lock = _require_trusted_runtime_owner_lock(namespace)
                    with owner_lock:
                        (
                            transition_valid,
                            final_owner,
                            final_namespace,
                            final_mode,
                        ) = _runtime_transition_snapshot()
                        if (
                            not transition_valid
                            or final_owner != current_thread
                            or final_namespace is not namespace
                            or final_mode is not None
                        ):
                            _fail_runtime_generation_drift(namespace)
                            if mutation_call_completed:
                                raise RuntimeError(
                                    "策略运行mutation reservation漂移；必须使用干净运行进程重启"
                                )
                        _STRATEGY_RUNTIME_TRANSITION_OWNER = None
                        _STRATEGY_RUNTIME_TRANSITION_NAMESPACE = None
                        _STRATEGY_RUNTIME_TRANSITION_MODE = None

        # owner reservation使用独立短锁：两个同时到达的安装调用不能都观察到
        # owner=None后在主锁上排队，否则后一个调用会污染前一个已返回的成功状态。
        owner_lock = _require_trusted_runtime_owner_lock(namespace)
        with owner_lock:
            (
                transition_valid,
                owner,
                transition_namespace,
                transition_mode,
            ) = _runtime_transition_snapshot()
            if not transition_valid:
                _fail_runtime_generation_drift(namespace)
                raise RuntimeError(
                    "策略运行transition状态无效；必须使用干净运行进程重启"
                )
            if owner is None:
                # 从首次generation/active/namespace变更前武装attempt；owner锁已
                # 排除了并发调用，递归/并发拒绝分支不会误清理别人的reservation。
                boundary_attempt_state[0] = True
                if (
                    namespace is not None
                    and requested_remote_mode in {"BACKTEST", "SHADOW", "LIVE"}
                ):
                    active_mode = _STRATEGY_RUNTIME_ACTIVE_MODE
                    process_signature = _STRATEGY_RUNTIME_PROCESS_SIGNATURE
                    canonical_state = _STRATEGY_RUNTIME_CANONICAL_STATE
                    commit_capsule = _STRATEGY_RUNTIME_COMMIT_CAPSULE
                    anchored_capsule = _get_runtime_commit_anchor()
                    runtime_record_present = False
                    runtime_record = None
                    try:
                        runtime_record_present = dict.__contains__(
                            namespace,
                            _STRATEGY_RUNTIME_STATE_KEY,
                        )
                        if runtime_record_present:
                            runtime_record = dict.get(
                                namespace,
                                _STRATEGY_RUNTIME_STATE_KEY,
                            )
                    except BaseException:
                        invalid_runtime_state = True

                    if (
                        not invalid_runtime_state
                        and not _runtime_contract_constants_are_valid()
                    ):
                        invalid_runtime_state = True

                    if active_mode is None and not invalid_runtime_state:
                        if (
                            type(_STRATEGY_RUNTIME_CONTRACT_GENERATION) is not int
                            or _STRATEGY_RUNTIME_CONTRACT_GENERATION != 0
                            or process_signature is not None
                            or canonical_state is not None
                            or commit_capsule is not None
                            or anchored_capsule is not None
                            or runtime_record_present
                        ):
                            invalid_runtime_state = True
                        else:
                            _advance_runtime_contract_generation()
                            _STRATEGY_RUNTIME_ACTIVE_MODE = "TRANSITIONING"
                    elif (
                        type(active_mode) is str
                        and active_mode in {"BACKTEST", "SHADOW", "LIVE_BLOCKED"}
                    ):
                        try:
                            invalid_runtime_state = not _runtime_authority_is_consistent(
                                active_mode,
                                process_signature,
                                canonical_state,
                                runtime_record,
                                commit_capsule,
                                anchored_capsule,
                                _STRATEGY_RUNTIME_CONTRACT_GENERATION,
                                _STRATEGY_RUNTIME_INSTANCE_TOKEN,
                                _STRATEGY_RUNTIME_MODULE_GENERATION,
                                namespace,
                            )
                        except BaseException:
                            invalid_runtime_state = True
                    elif type(active_mode) is str and active_mode == "FAILED":
                        pass
                    else:
                        invalid_runtime_state = True

                    if invalid_runtime_state:
                        _advance_runtime_contract_generation()
                        _STRATEGY_RUNTIME_ACTIVE_MODE = "FAILED"
                if namespace is not None and (
                    invalid_runtime_state
                    or requested_remote_mode in {"SHADOW", "LIVE"}
                ):
                    _install_runtime_guards(
                        namespace,
                        "FAILED" if invalid_runtime_state else "TRANSITIONING",
                    )
                _STRATEGY_RUNTIME_TRANSITION_OWNER = current_thread
                _STRATEGY_RUNTIME_TRANSITION_NAMESPACE = namespace
                _STRATEGY_RUNTIME_TRANSITION_MODE = requested_remote_mode
                owns_transition = True
            else:
                owns_transition = False
        if not owns_transition:
            if (
                namespace is not None
                and namespace is not transition_namespace
                and (
                    requested_remote_mode in {"SHADOW", "LIVE"}
                    or transition_mode in {"SHADOW", "LIVE"}
                )
            ):
                _install_runtime_guards(namespace, "TRANSITIONING")
            if owner == current_thread:
                raise RuntimeError("策略运行模式安装不允许递归调用")
            raise RuntimeError("策略运行模式正在切换；拒绝并发配置、请求或重复安装")

        call_completed = False
        runtime_lock = _require_trusted_runtime_lock(namespace)
        with runtime_lock:
            try:
                if invalid_runtime_state:
                    _mark_runtime_failed(namespace)
                    raise RuntimeError(
                        "策略运行进程状态无效；必须使用干净运行进程重启"
                    )
                result = call_with_generation_check()
                call_completed = True
                return result
            finally:
                try:
                    owner_lock = _require_trusted_runtime_owner_lock(namespace)
                    with owner_lock:
                        (
                            transition_valid,
                            final_owner,
                            final_namespace,
                            final_mode,
                        ) = _runtime_transition_snapshot()
                        if not transition_valid:
                            _fail_runtime_generation_drift(namespace)
                            raise RuntimeError(
                                "策略运行transition状态无效；必须使用干净运行进程重启"
                            )
                        if final_owner is None:
                            if call_completed:
                                _fail_runtime_generation_drift(namespace)
                                raise RuntimeError(
                                    "策略运行安装reservation在返回前失效；必须使用干净运行进程重启"
                                )
                        elif final_owner == current_thread:
                            anchor = _runtime_primitive_anchor_snapshot()
                            socket_lock = anchor[7] if anchor is not None else None
                            if socket_lock is None:
                                _fail_runtime_generation_drift(namespace)
                                raise RuntimeError(
                                    "策略运行安装返回原语无效；必须使用干净运行进程重启"
                                )
                            with socket_lock:
                                gate_state = _runtime_socket_gate_authority_snapshot(
                                    anchor
                                )
                                if (
                                    not _runtime_module_generation_matches(
                                        boundary_instance_token,
                                        boundary_module_generation,
                                    )
                                    or gate_state != (False, ())
                                    or type(_STRATEGY_RUNTIME_RELOAD_IN_PROGRESS)
                                    is not bool
                                    or _STRATEGY_RUNTIME_RELOAD_IN_PROGRESS
                                    or final_namespace is not namespace
                                    or final_mode != requested_remote_mode
                                ):
                                    _fail_runtime_generation_drift(namespace)
                                    raise RuntimeError(
                                        "策略运行安装在返回前发生重载或reservation漂移；必须使用干净运行进程重启"
                                    )
                                _STRATEGY_RUNTIME_TRANSITION_OWNER = None
                                _STRATEGY_RUNTIME_TRANSITION_NAMESPACE = None
                                _STRATEGY_RUNTIME_TRANSITION_MODE = None
                        else:
                            _fail_runtime_generation_drift(namespace)
                            raise RuntimeError(
                                "策略运行transition owner发生漂移；必须使用干净运行进程重启"
                            )
                except BaseException:
                    # owner/socket上下文已经释放；runtime RLock仍由外层持有。
                    # 任意异步中断都必须撤销刚提交的namespace状态后再传播。
                    _fail_runtime_generation_drift(namespace)
                    raise

    @functools.wraps(function)
    def locked(*args, **kwargs):
        boundary_attempt_state = [False]
        boundary_result_sentinel = object()
        boundary_result = boundary_result_sentinel
        call_completed = False
        namespace = None
        if args and type(args[0]) is dict:
            namespace = args[0]
        elif type(kwargs.get("namespace")) is dict:
            namespace = kwargs["namespace"]
        try:
            boundary_result = locked_impl(
                boundary_attempt_state,
                *args,
                **kwargs,
            )
            call_completed = True
            boundary_attempt_state[0] = False
            return boundary_result
        except BaseException:
            if (
                boundary_attempt_state[0]
                or boundary_result is not boundary_result_sentinel
                or call_completed
            ):
                _fail_runtime_generation_drift(namespace)
                boundary_attempt_state[0] = False
            raise

    return locked


def _track_runtime_request(function: Callable[..., Any]) -> Callable[..., Any]:
    """登记锁外RPC；runtime切换发现已有请求时必须失败关闭。"""

    request_instance_token = _STRATEGY_RUNTIME_INSTANCE_TOKEN
    request_module_generation = _STRATEGY_RUNTIME_MODULE_GENERATION
    request_registry = _STRATEGY_RUNTIME_REQUEST_LEASES
    request_quarantine_retain = _STRATEGY_RUNTIME_QUARANTINE_RETAIN
    request_runtime_lock = _STRATEGY_RUNTIME_LOCK

    def tracked_impl(
        self,
        action,
        request_token,
        request_handoff_state,
        request_resource_state,
        *args,
        **kwargs,
    ):
        global _STRATEGY_RUNTIME_INFLIGHT_REQUESTS

        current_thread = threading.get_ident()
        if type(current_thread) is not int or not _runtime_module_generation_matches(
            request_instance_token,
            request_module_generation,
        ):
            _fail_runtime_generation_drift(None)
            raise RuntimeError(
                "策略运行远程请求helper代际无效；必须使用干净运行进程重启"
            )
        transition_valid, owner, _, _ = _runtime_transition_snapshot()
        if not transition_valid:
            _fail_runtime_generation_drift(None)
            raise RuntimeError(
                "策略运行远程请求transition状态无效；必须使用干净运行进程重启"
            )
        _assert_runtime_remote_allowed(action)
        if owner is not None:
            raise RuntimeError("策略运行模式正在切换；禁止远程访问: {}".format(action))
        runtime_lock = _require_trusted_runtime_lock()
        with runtime_lock:
            if not _runtime_module_generation_matches(
                request_instance_token,
                request_module_generation,
            ):
                _fail_runtime_generation_drift(None)
                raise RuntimeError(
                    "策略运行远程请求helper代际无效；必须使用干净运行进程重启"
                )
            transition_valid, owner, _, _ = _runtime_transition_snapshot()
            if not transition_valid:
                _fail_runtime_generation_drift(None)
                raise RuntimeError(
                    "策略运行远程请求transition状态无效；必须使用干净运行进程重启"
                )
            _assert_runtime_remote_allowed(action)
            if owner is not None:
                raise RuntimeError(
                    "策略运行模式正在切换；禁止远程访问: {}".format(action)
                )
            current_generation = _STRATEGY_RUNTIME_CONTRACT_GENERATION
            current_inflight = _STRATEGY_RUNTIME_INFLIGHT_REQUESTS
            registry_snapshot = _runtime_request_registry_snapshot(request_registry)
            if (
                type(current_generation) is not int
                or current_generation != 0
                or type(current_inflight) is not int
                or current_inflight < 0
                or type(request_registry) is not set
                or request_registry is not _STRATEGY_RUNTIME_REQUEST_LEASES
                or registry_snapshot is None
                or current_inflight != len(registry_snapshot)
            ):
                _fail_runtime_generation_drift(None)
                raise RuntimeError(
                    "策略运行远程请求状态无效；必须使用干净运行进程重启"
                )
            lease_generation = current_generation
            set.add(request_registry, request_token)
            _STRATEGY_RUNTIME_INFLIGHT_REQUESTS = len(request_registry)
        request_result_sentinel = object()
        request_result = request_result_sentinel
        request_primary_exception = None
        try:
            kwargs["_runtime_lease_generation"] = lease_generation
            kwargs["_runtime_lease_instance_token"] = request_instance_token
            kwargs["_runtime_lease_module_generation"] = request_module_generation
            kwargs["_runtime_lease_registry"] = request_registry
            kwargs["_runtime_lease_token"] = request_token
            kwargs["_runtime_lease_handoff_state"] = request_handoff_state
            kwargs["_runtime_lease_resource_state"] = request_resource_state
            request_result = function(self, action, *args, **kwargs)
            return request_result
        except BaseException as exc:
            request_primary_exception = exc
            raise
        finally:
            if request_result is not request_result_sentinel:
                request_handoff_state[0] = True
            runtime_lock = _require_trusted_runtime_lock()
            with runtime_lock:
                if not _runtime_module_generation_matches(
                    request_instance_token,
                    request_module_generation,
                ):
                    if not _discard_runtime_request_lease_by_identity(
                        request_registry,
                        request_token,
                    ) and type(request_registry) is set:
                        _clear_or_quarantine_runtime_request_registry(
                            request_registry,
                            request_quarantine_retain,
                        )
                    raise RuntimeError(
                        "策略运行远程请求属于旧helper代际；已拒绝修改当前计数"
                    )
                current_generation = _STRATEGY_RUNTIME_CONTRACT_GENERATION
                current_inflight = _STRATEGY_RUNTIME_INFLIGHT_REQUESTS
                registry_snapshot = _runtime_request_registry_snapshot(
                    request_registry
                )
                token_registered = _runtime_request_registry_contains_identity(
                    registry_snapshot,
                    request_token,
                )
                active_mode = _STRATEGY_RUNTIME_ACTIVE_MODE
                transition_valid, owner, _, _ = _runtime_transition_snapshot()
                failed_latched = False
                try:
                    failed_latched = (
                        type(active_mode) is str
                        and active_mode == "FAILED"
                        and _get_runtime_commit_anchor()
                        is _STRATEGY_RUNTIME_FAILED_ANCHOR
                    )
                except BaseException:
                    failed_latched = False
                if (
                    failed_latched
                    and type(_STRATEGY_RUNTIME_RELOAD_IN_PROGRESS) is bool
                    and _STRATEGY_RUNTIME_RELOAD_IN_PROGRESS
                ):
                    _clear_runtime_request_leases()
                    _STRATEGY_RUNTIME_INFLIGHT_REQUESTS = 0
                    if (
                        request_primary_exception is not None
                        and isinstance(request_primary_exception, BaseException)
                        and not isinstance(request_primary_exception, Exception)
                    ):
                        raise request_primary_exception
                    raise RuntimeError(
                        "策略运行helper正在重载；旧请求不得修改新代际计数"
                    )
                if (
                    failed_latched
                    and type(current_generation) is int
                    and current_generation != lease_generation
                    and request_registry is _STRATEGY_RUNTIME_REQUEST_LEASES
                    and registry_snapshot is not None
                    and not token_registered
                ):
                    raise RuntimeError(
                        "策略运行远程请求契约已失效；已拒绝修改当前计数"
                    )
                if (
                    active_mode is not None
                    or type(_STRATEGY_RUNTIME_RELOAD_IN_PROGRESS) is not bool
                    or _STRATEGY_RUNTIME_RELOAD_IN_PROGRESS
                    or not transition_valid
                    or owner is not None
                ):
                    _fail_runtime_generation_drift(None)
                    raise RuntimeError(
                        "策略运行远程请求收尾状态无效；必须使用干净运行进程重启"
                    )
                _assert_runtime_request_lease_current(
                    action,
                    lease_generation,
                    request_instance_token,
                    request_module_generation,
                    request_registry,
                    request_token,
                )
                if (
                    type(current_generation) is not int
                    or current_generation != lease_generation
                    or type(current_inflight) is not int
                    or current_inflight <= 0
                    or request_registry is not _STRATEGY_RUNTIME_REQUEST_LEASES
                    or registry_snapshot is None
                    or not token_registered
                    or current_inflight != len(registry_snapshot)
                ):
                    _fail_runtime_generation_drift(None)
                    raise RuntimeError(
                        "策略运行远程请求释放状态无效；必须使用干净运行进程重启"
                    )
                if not _discard_runtime_request_lease_by_identity(
                    request_registry,
                    request_token,
                ):
                    _fail_runtime_generation_drift(None)
                    raise RuntimeError(
                        "策略运行远程请求lease identity无效；必须使用干净运行进程重启"
                    )
                remaining_leases = _runtime_request_registry_snapshot(request_registry)
                if remaining_leases is None:
                    _fail_runtime_generation_drift(None)
                    raise RuntimeError(
                        "策略运行远程请求registry无效；必须使用干净运行进程重启"
                    )
                _STRATEGY_RUNTIME_INFLIGHT_REQUESTS = len(remaining_leases)

    def emergency_release_request(request_token):
        """核对token与计数；覆盖释放token后、更新计数前的中断窗口。"""

        with request_runtime_lock:
            registry_snapshot = _runtime_request_registry_snapshot(
                request_registry
            )
            token_registered = _runtime_request_registry_contains_identity(
                registry_snapshot,
                request_token,
            )
            if token_registered and not _discard_runtime_request_lease_by_identity(
                request_registry,
                request_token,
            ):
                _clear_or_quarantine_runtime_request_registry(
                    request_registry,
                    request_quarantine_retain,
                )
            current_generation_matches = _runtime_module_generation_matches(
                request_instance_token,
                request_module_generation,
            )
            if (
                current_generation_matches
                and request_registry is _STRATEGY_RUNTIME_REQUEST_LEASES
            ):
                remaining_leases = _runtime_request_registry_snapshot(
                    request_registry
                )
                current_inflight = _STRATEGY_RUNTIME_INFLIGHT_REQUESTS
                if (
                    token_registered
                    or remaining_leases is None
                    or type(current_inflight) is not int
                    or current_inflight != len(remaining_leases)
                ):
                    _set_runtime_failed_process_state()

    def emergency_close_request_resource(request_resource_state):
        """关闭尚未由请求体确认释放的socket，不执行候选对象的布尔协议。"""

        if type(request_resource_state) is not list or len(request_resource_state) != 1:
            return
        pending_resource = request_resource_state[0]
        request_resource_state[0] = None
        if pending_resource is not None:
            try:
                pending_resource.close()
            except BaseException:
                pass

    @functools.wraps(function)
    def tracked(self, action, *args, **kwargs):
        request_token = object()
        request_handoff_state = [False]
        request_resource_state = [None]
        request_result_sentinel = object()
        request_result = request_result_sentinel
        try:
            request_result = tracked_impl(
                self,
                action,
                request_token,
                request_handoff_state,
                request_resource_state,
                *args,
                **kwargs,
            )
            request_handoff_state[0] = False
            return request_result
        except BaseException:
            emergency_close_request_resource(request_resource_state)
            emergency_release_request(request_token)
            if (
                request_handoff_state[0]
                or request_result is not request_result_sentinel
            ) and _runtime_module_generation_matches(
                request_instance_token,
                request_module_generation,
            ):
                _set_runtime_failed_process_state()
            raise

    return tracked


class MarketOrderStyle:
    """聚宽风格市价单样式，可选保护价。"""

    def __init__(self, limit_price: Optional[float] = None):
        self.limit_price = limit_price


class LimitOrderStyle:
    """聚宽风格限价单样式。"""

    def __init__(self, limit_price: float):
        self.limit_price = limit_price
        self.price = limit_price


def _style_class_name(style: Any) -> str:
    return style.__class__.__name__ if style is not None else ""


def _is_order_style(value: Any) -> bool:
    name = _style_class_name(value)
    return bool(name and "OrderStyle" in name)


def _extract_style_price(style: Any) -> Optional[float]:
    for attr in ("limit_price", "price"):
        if hasattr(style, attr):
            value = getattr(style, attr)
            if value is not None:
                return float(value)
    return None


def _resolve_price_market(
    price: Optional[float] = None,
    style: Optional[Any] = None,
    market: Optional[bool] = None,
) -> Tuple[Optional[float], Optional[bool]]:
    """解析聚宽 style 和旧 helper price/market 语义。"""

    if style is None and _is_order_style(price):
        style = price
        price = None
    if style is None:
        return price, market

    name = _style_class_name(style)
    if name in ("StopMarketOrderStyle", "StopLimitOrderStyle"):
        raise NotImplementedError(f"{name} 暂不支持远程实盘接管")
    if "Stop" in name and "OrderStyle" in name:
        raise NotImplementedError(f"{name} 暂不支持远程实盘接管")
    style_price = _extract_style_price(style)
    effective_price = style_price if style_price is not None else price
    if "MarketOrderStyle" in name:
        return effective_price, True
    if "LimitOrderStyle" in name:
        if effective_price is None:
            raise ValueError("限价单缺少价格")
        return effective_price, False
    if "OrderStyle" in name:
        raise NotImplementedError(f"{name} 暂不支持远程实盘接管")
    return price, market


def _validate_jq_trade_scope(
    side: Optional[str] = None,
    pindex: int = 0,
    close_today: bool = False,
) -> None:
    if pindex not in (0, None):
        raise NotImplementedError("聚宽兼容接管第一版仅支持 pindex=0")
    if close_today:
        raise NotImplementedError("聚宽兼容接管第一版暂不支持 close_today=True")
    if side is None:
        return
    side_text = str(side).strip().lower()
    if side_text == "short":
        raise NotImplementedError("聚宽兼容接管第一版暂不支持 side='short'")


def _normalise_side(side: Optional[str], signed_value: float) -> str:
    if side is not None:
        side_text = str(side).strip().lower()
        if side_text == "short":
            raise NotImplementedError("聚宽兼容接管第一版暂不支持 side='short'")
        if side_text in ("buy", "b"):
            return "BUY"
        if side_text in ("sell", "s"):
            return "SELL"
    return "BUY" if signed_value > 0 else "SELL"


def _coerce_wait_timeout(value: Optional[float], default_wait_timeout: float) -> float:
    if value is None:
        return float(default_wait_timeout)
    return float(value)


def _now_ns() -> int:
    time_ns = getattr(time, "time_ns", None)
    if time_ns is not None:
        return int(time_ns())
    return int(time.time() * 1_000_000_000)


def _log(level: str, msg: str, *args, **kwargs):
    """
    统一的日志输出函数。
    
    所有日志都通过此函数输出，受全局 _DEBUG 开关控制。
    输出到 stderr，避免干扰 stdout。
    """
    if not _DEBUG:
        return
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
    formatted_msg = msg.format(*args, **kwargs) if args or kwargs else msg
    print(f"[{timestamp}] [{level}] {formatted_msg}", file=sys.stderr)


def _warn(msg: str, *args, **kwargs):
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
    formatted_msg = msg.format(*args, **kwargs) if args or kwargs else msg
    print(f"[{timestamp}] [WARN] {formatted_msg}", file=sys.stderr)


def _configure_remote_clients(
    host: str,
    token: str,
    port: int,
    account_key: Optional[str],
    sub_account_id: Optional[str],
    tls_cert: Optional[str],
    retries: int,
    retry_interval: float,
    rpc_timeout: float,
    place_order_timeout_margin: float,
    debug: bool,
) -> None:
    """在已持有runtime mutation reservation时原子发布远程client组。"""

    global _CLIENT, _DATA_CLIENT, _BROKER_CLIENT, _DEBUG

    _DEBUG = debug
    _log(
        "INFO",
        "初始化远程连接: host={}, port={}, retries={}, debug={}",
        host,
        port,
        retries,
        debug,
    )
    client = _ShortLivedClient(
        host,
        port,
        token,
        tls_cert=tls_cert,
        retries=retries,
        retry_interval=retry_interval,
        rpc_timeout=rpc_timeout,
    )
    data_client = RemoteDataClient(client)
    broker_client = RemoteBrokerClient(
        client,
        account_key=account_key,
        sub_account_id=sub_account_id,
        place_order_timeout_margin=place_order_timeout_margin,
    )
    broker_client.bind_data_client(data_client)
    _CLIENT = client
    _DATA_CLIENT = data_client
    _BROKER_CLIENT = broker_client
    _log("INFO", "初始化完成")


@_serialise_runtime_boundary
def configure(
    host: str,
    token: str,
    *,
    port: int = 58620,
    account_key: Optional[str] = None,
    sub_account_id: Optional[str] = None,
    tls_cert: Optional[str] = None,
    retries: int = 2,
    retry_interval: float = 0.5,
    rpc_timeout: float = DEFAULT_RPC_TIMEOUT_SECONDS,
    place_order_timeout_margin: float = DEFAULT_PLACE_ORDER_TIMEOUT_MARGIN_SECONDS,
    debug: bool = True,
    _runtime_boundary_attempt_state: Optional[List[bool]] = None,
) -> None:
    """
    初始化远程访问参数；聚宽环境无法常驻进程，因此每次调用都会短连接访问。
    
    Args:
        host: 服务器主机名或 IP 地址
        token: 认证令牌
        port: 服务器端口，默认 58620
        account_key: 账户键，可选
        sub_account_id: 子账户 ID，可选
        tls_cert: TLS 证书文件路径，可选
        retries: 失败重试次数，默认 2
        retry_interval: 重试间隔（秒），默认 0.5
        rpc_timeout: RPC 超时时间（秒），默认 60.0
        place_order_timeout_margin: 下单请求超时相对 wait_timeout 的安全余量，默认 30.0
        debug: 是否启用调试日志，默认 True
    """
    _assert_runtime_mutation_allowed(
        "configure",
        threading.get_ident(),
    )
    _assert_no_inflight_runtime_requests("configure")
    if (
        type(_runtime_boundary_attempt_state) is not list
        or len(_runtime_boundary_attempt_state) != 1
    ):
        _fail_runtime_generation_drift(None)
        raise RuntimeError(
            "策略运行配置attempt状态无效；必须使用干净运行进程重启"
    )
    _runtime_boundary_attempt_state[0] = True
    _configure_remote_clients(
        host,
        token,
        port,
        account_key,
        sub_account_id,
        tls_cert,
        retries,
        retry_interval,
        rpc_timeout,
        place_order_timeout_margin,
        debug,
    )


def get_data_client() -> "RemoteDataClient":
    if not _DATA_CLIENT:
        raise RuntimeError("尚未调用 configure() 初始化")
    return _DATA_CLIENT


def get_broker_client() -> "RemoteBrokerClient":
    if not _BROKER_CLIENT:
        raise RuntimeError("尚未调用 configure() 初始化")
    return _BROKER_CLIENT


# --------- 数据客户端 ----------
class RemoteDataClient:
    def __init__(self, client: "_ShortLivedClient") -> None:
        self._client = client

    def get_price(self, security: str, **kwargs) -> pd.DataFrame:
        payload = {"security": security}
        payload.update(kwargs)
        resp = self._client.request("data.history", payload)
        return _df_from_payload(resp)

    def get_trade_days(self, start: str, end: str) -> List[pd.Timestamp]:
        resp = self._client.request("data.trade_days", {"start": start, "end": end})
        values = resp.get("value") or resp.get("values") or []
        return [pd.to_datetime(v) for v in values]

    def get_snapshot(self, security: str) -> Dict[str, Any]:
        return self._client.request("data.snapshot", {"security": security})

    def get_last_price(self, security: str) -> Optional[float]:
        snap = self.get_snapshot(security)
        price = snap.get("last_price") or snap.get("lastPrice") or snap.get("price")
        if price is not None:
            try:
                return float(price)
            except Exception:
                return None
        hist = self._client.request("data.history", {"security": security, "count": 1, "frequency": "1m"})
        records = hist.get("records") or []
        if records and isinstance(records[-1], (list, tuple)) and len(records[-1]) >= 2:
            try:
                return float(records[-1][-1])
            except Exception:
                return None
        return None


# --------- 券商客户端 ----------
class RemoteOrder:
    """
    远程订单对象。
    
    属性：
    - order_id: 订单ID
    - status: 订单状态
    - security: 证券代码
    - amount: 请求数量
    - price: 请求价格
    - actual_amount: 服务端实际执行数量（可能因最小手数/步进取整而不同）
    - actual_price: 服务端实际委托价格（市价单会由服务端计算）
    - filled: 已成交数量
    - is_buy: 是否买入
    - order_remark: 订单备注
    - strategy_name: 策略标识
    - timed_out/async_tracking/last_snapshot: 新版 server 返回的等待超时追踪字段；旧 server 没有时保持默认值
    """
    def __init__(
        self,
        order_id: str,
        status: str,
        security: str,
        amount: int,
        price: Optional[float] = None,
        actual_amount: Optional[int] = None,
        actual_price: Optional[float] = None,
        filled: int = 0,
        is_buy: Optional[bool] = None,
        order_remark: Optional[str] = None,
        strategy_name: Optional[str] = None,
        timed_out: bool = False,
        async_tracking: bool = False,
        last_snapshot: Optional[Dict[str, Any]] = None,
        raw_response: Optional[Dict[str, Any]] = None,
    ):
        self.order_id = order_id
        self.status = status
        self.security = security
        self.amount = amount
        self.price = price
        # 服务端返回的实际执行数量和价格
        self.actual_amount = actual_amount if actual_amount is not None else amount
        self.actual_price = actual_price if actual_price is not None else price
        self.filled = filled
        self.is_buy = is_buy
        self.order_remark = order_remark
        self.strategy_name = strategy_name
        self.timed_out = bool(timed_out)
        self.async_tracking = bool(async_tracking)
        self.last_snapshot = dict(last_snapshot or {})
        self.raw_response = dict(raw_response or {})


class RemoteTrade:
    """
    远程成交对象（聚宽风格）。
    """
    def __init__(
        self,
        trade_id: str,
        order_id: str,
        security: str,
        amount: int,
        price: float,
        time: pd.Timestamp,
        commission: float = 0.0,
        tax: float = 0.0,
    ):
        self.trade_id = trade_id
        self.order_id = order_id
        self.security = security
        self.amount = amount
        self.price = price
        self.time = time.to_pydatetime() if isinstance(time, pd.Timestamp) else time
        self.commission = commission
        self.tax = tax


class RemotePosition:
    def __init__(
        self,
        security: str,
        amount: int,
        avg_cost: float,
        market_value: float,
        available: Optional[int] = None,
        frozen: Optional[int] = None,
        market: Optional[str] = None,
    ):
        self.security = security
        self.amount = amount
        self.avg_cost = avg_cost
        self.market_value = market_value
        self.available = available if available is not None else amount
        self.frozen = frozen if frozen is not None else 0
        self.market = market


class RemoteAccount:
    def __init__(self, available_cash: float, total_value: float):
        self.available_cash = available_cash
        self.total_value = total_value


class RemoteBrokerClient:
    def __init__(
        self,
        client: "_ShortLivedClient",
        *,
        account_key: Optional[str] = None,
        sub_account_id: Optional[str] = None,
        place_order_timeout_margin: float = DEFAULT_PLACE_ORDER_TIMEOUT_MARGIN_SECONDS,
    ) -> None:
        self._client = client
        self.account_key = account_key
        self.sub_account_id = sub_account_id
        self._data_client: Optional[RemoteDataClient] = None
        self.place_order_timeout_margin = max(0.0, float(place_order_timeout_margin))

    def bind_data_client(self, data_client: RemoteDataClient) -> None:
        self._data_client = data_client

    # ----- 聚宽风格入口 -----
    def order(
        self,
        security: str,
        amount: int,
        price: Optional[float] = None,
        side: Optional[str] = None,
        wait_timeout: float = 0,
        *,
        style: Optional[Any] = None,
        market: Optional[bool] = None,
        remark: Optional[str] = None,
        order_remark: Optional[str] = None,
        idempotency_key: Optional[str] = None,
    ) -> str:
        """
        按数量下单。
        
        :param security: 证券代码
        :param amount: 数量（正数买入，负数卖出；如果指定了 side 则取绝对值）
        :param price: 委托价格，None 时服务端自动使用市价单
        :param side: 方向 BUY/SELL，None 时根据 amount 正负判断
        :param wait_timeout: 等待超时秒数，0 表示异步返回
        :param market: True 表示市价单；price 同时传入时作为保护价。None 时保持旧行为
        :param remark/order_remark: 订单备注，透传到服务端/QMT
        :param idempotency_key: 幂等键；不传时 helper 会为本次短连接请求自动生成
        :return: 订单 ID
        
        注意：服务端会自动处理最小手数/步进取整、停牌检查、价格笼子等。
        """
        _assert_runtime_mutation_allowed("broker.order")
        if amount == 0:
            return ""
        price, market = _resolve_price_market(price=price, style=style, market=market)
        actual_side = _normalise_side(side, amount)
        qty = abs(int(amount))
        # 服务端会自动处理最小手数/步进取整
        order = self._place_order(
            security,
            qty,
            price,
            actual_side,
            wait_timeout=wait_timeout,
            market=market,
            remark=remark,
            order_remark=order_remark,
            idempotency_key=idempotency_key,
        )
        return order.order_id

    def order_value(
        self,
        security: str,
        value: float,
        price: Optional[float] = None,
        wait_timeout: float = 0,
        *,
        style: Optional[Any] = None,
        side: Optional[str] = None,
        pindex: int = 0,
        close_today: bool = False,
        market: Optional[bool] = None,
        remark: Optional[str] = None,
        order_remark: Optional[str] = None,
        idempotency_key: Optional[str] = None,
    ) -> str:
        """
        按市值下单。
        
        :param security: 证券代码
        :param value: 目标市值（正数买入，负数卖出）
        :param price: 委托价格，None 时服务端自动使用市价单
        :param wait_timeout: 等待超时秒数，0 表示异步返回
        :param market: True 表示市价单；price 同时传入时作为保护价。None 时保持旧行为
        :param remark/order_remark: 订单备注，透传到服务端/QMT
        :param idempotency_key: 幂等键；不传时 helper 会为本次短连接请求自动生成
        :return: 订单 ID
        
        注意：服务端会自动处理最小手数/步进取整，实际成交市值可能与请求略有偏差。
        """
        _assert_runtime_mutation_allowed("broker.order_value")
        _validate_jq_trade_scope(side=side, pindex=pindex, close_today=close_today)
        if value == 0:
            return ""
        price, market = _resolve_price_market(price=price, style=style, market=market)
        # 获取参考价格用于计算数量
        p = price or self._infer_price(security)
        if not p:
            raise RuntimeError("无法获取价格，无法按市值下单")
        # 计算大致数量，服务端会自动按最小手数/步进取整
        qty = int(abs(value) / p)
        actual_side = _normalise_side(side, value)
        order = self._place_order(
            security,
            qty,
            price,
            actual_side,
            wait_timeout=wait_timeout,
            market=market,
            remark=remark,
            order_remark=order_remark,
            idempotency_key=idempotency_key,
        )
        return order.order_id

    def order_percent(
        self,
        security: str,
        percent: float,
        price: Optional[float] = None,
        wait_timeout: float = 0,
        *,
        style: Optional[Any] = None,
        side: Optional[str] = None,
        pindex: int = 0,
        close_today: bool = False,
        market: Optional[bool] = None,
        remark: Optional[str] = None,
        order_remark: Optional[str] = None,
        idempotency_key: Optional[str] = None,
    ) -> str:
        """按当前远程账户总资产的一定比例下单。"""

        _assert_runtime_mutation_allowed("broker.order_percent")
        account = self.get_account()
        return self.order_value(
            security,
            float(account.total_value) * float(percent),
            price=price,
            wait_timeout=wait_timeout,
            style=style,
            side=side,
            pindex=pindex,
            close_today=close_today,
            market=market,
            remark=remark,
            order_remark=order_remark,
            idempotency_key=idempotency_key,
        )

    def order_target(
        self,
        security: str,
        target: int,
        price: Optional[float] = None,
        wait_timeout: float = 0,
        *,
        style: Optional[Any] = None,
        side: Optional[str] = None,
        pindex: int = 0,
        close_today: bool = False,
        market: Optional[bool] = None,
        remark: Optional[str] = None,
        order_remark: Optional[str] = None,
        idempotency_key: Optional[str] = None,
    ) -> str:
        """
        调仓到目标数量。
        
        :param security: 证券代码
        :param target: 目标持仓数量
        :param price: 委托价格，None 时服务端自动使用市价单
        :param wait_timeout: 等待超时秒数，0 表示异步返回
        :param market: True 表示市价单；price 同时传入时作为保护价。None 时保持旧行为
        :param remark/order_remark: 订单备注，透传到服务端/QMT
        :param idempotency_key: 幂等键；不传时 helper 会为本次短连接请求自动生成
        :return: 订单 ID（如果不需要交易则返回空字符串）
        
        注意：建议 target 为 100 的整数倍，服务端会自动取整。
        """
        _assert_runtime_mutation_allowed("broker.order_target")
        _validate_jq_trade_scope(side=side, pindex=pindex, close_today=close_today)
        price, market = _resolve_price_market(price=price, style=style, market=market)
        current = self._current_amount(security)
        delta = target - current
        if delta == 0:
            return ""
        return self.order(
            security,
            delta,
            price=price,
            side=side,
            wait_timeout=wait_timeout,
            style=style,
            market=market,
            remark=remark,
            order_remark=order_remark,
            idempotency_key=idempotency_key,
        )

    def order_target_value(
        self,
        security: str,
        target_value: Optional[float] = None,
        price: Optional[float] = None,
        wait_timeout: float = 0,
        *,
        value: Optional[float] = None,
        style: Optional[Any] = None,
        side: Optional[str] = None,
        pindex: int = 0,
        close_today: bool = False,
        market: Optional[bool] = None,
        remark: Optional[str] = None,
        order_remark: Optional[str] = None,
        idempotency_key: Optional[str] = None,
    ) -> str:
        """
        调仓到目标市值。
        
        :param security: 证券代码
        :param target_value: 目标持仓市值
        :param price: 委托价格，None 时服务端自动使用市价单
        :param wait_timeout: 等待超时秒数，0 表示异步返回
        :param market: True 表示市价单；price 同时传入时作为保护价。None 时保持旧行为
        :param remark/order_remark: 订单备注，透传到服务端/QMT
        :param idempotency_key: 幂等键；不传时 helper 会为本次短连接请求自动生成
        :return: 订单 ID（如果不需要交易则返回空字符串）
        
        注意：服务端会自动处理最小手数/步进取整，实际市值可能与目标略有偏差。
        """
        _assert_runtime_mutation_allowed("broker.order_target_value")
        _validate_jq_trade_scope(side=side, pindex=pindex, close_today=close_today)
        if target_value is None:
            if value is None:
                raise TypeError("order_target_value() missing required argument: 'target_value' or 'value'")
            target_value = value
        elif value is not None:
            raise TypeError("order_target_value() got both 'target_value' and 'value'")
        price, market = _resolve_price_market(price=price, style=style, market=market)
        p = price or self._infer_price(security)
        if not p:
            raise RuntimeError("无法获取价格，无法按目标市值下单")
        # 计算目标数量，服务端会自动按最小手数/步进取整
        target_amount = int(target_value / p)
        return self.order_target(
            security,
            target_amount,
            price=price,
            wait_timeout=wait_timeout,
            style=style,
            side=side,
            pindex=pindex,
            close_today=close_today,
            market=market,
            remark=remark,
            order_remark=order_remark,
            idempotency_key=idempotency_key,
        )

    def order_target_percent(
        self,
        security: str,
        percent: float,
        price: Optional[float] = None,
        wait_timeout: float = 0,
        *,
        style: Optional[Any] = None,
        side: Optional[str] = None,
        pindex: int = 0,
        close_today: bool = False,
        market: Optional[bool] = None,
        remark: Optional[str] = None,
        order_remark: Optional[str] = None,
        idempotency_key: Optional[str] = None,
    ) -> str:
        """调仓到当前远程账户总资产的一定比例。"""

        _assert_runtime_mutation_allowed("broker.order_target_percent")
        account = self.get_account()
        return self.order_target_value(
            security,
            float(account.total_value) * float(percent),
            price=price,
            wait_timeout=wait_timeout,
            style=style,
            side=side,
            pindex=pindex,
            close_today=close_today,
            market=market,
            remark=remark,
            order_remark=order_remark,
            idempotency_key=idempotency_key,
        )

    # ----- 基础接口 -----
    def get_account(self) -> RemoteAccount:
        payload = self._base_payload()
        resp = self._client.request("broker.account", payload) or {}
        value = resp.get("value") or resp
        return RemoteAccount(
            available_cash=float(value.get("available_cash", 0.0)),
            total_value=float(value.get("total_value", value.get("total_asset", 0.0))),
        )

    def get_positions(self) -> List[RemotePosition]:
        payload = self._base_payload()
        rows = self._client.request("broker.positions", payload)
        positions = []
        for row in rows or []:
            # 解析数量和可用数量
            amount = int(row.get("amount") or 0)
            # 优先读取 closeable_amount（服务端 QMT 返回的字段名）
            available = int(
                row.get("available")
                or row.get("closeable_amount")
                or row.get("can_sell_amount")
                or row.get("sellable")
                or row.get("can_use_amount")
                or row.get("current_amount")
                or row.get("qty")
                or row.get("volume")
                or row.get("position", 0)
            )
            # frozen 优先读取服务端返回值，如果没有则用 amount - available 计算
            frozen_raw = row.get("frozen") or row.get("lock_amount")
            frozen = int(frozen_raw) if frozen_raw is not None else (amount - available)
            positions.append(
                RemotePosition(
                    security=row.get("security"),
                    amount=amount,
                    avg_cost=float(row.get("avg_cost") or 0.0),
                    market_value=float(row.get("market_value") or 0.0),
                    available=available,
                    frozen=frozen,
                    market=row.get("market"),
                )
            )
        return positions

    def get_orders(
        self,
        order_id: Optional[str] = None,
        security: Optional[str] = None,
        status: Optional[object] = None,
        from_broker: bool = False,
    ) -> Dict[str, RemoteOrder]:
        payload = self._base_payload()
        if order_id:
            payload["order_id"] = order_id
        if security:
            payload["security"] = security
        if status is not None:
            payload["status"] = getattr(status, "value", status)
        if from_broker:
            payload["from_broker"] = True
        rows = self._client.request("broker.orders", payload) or []
        result: Dict[str, RemoteOrder] = {}
        for row in rows:
            order = self._build_order_snapshot(row)
            if not order:
                continue
            result[order.order_id] = order
        return result

    def get_open_orders(self) -> Dict[str, RemoteOrder]:
        orders = self.get_orders()
        if not orders:
            return {}
        open_states = {"new", "submitted", "open", "filling", "canceling"}
        return {oid: order for oid, order in orders.items() if str(order.status) in open_states}

    def get_trades(
        self,
        order_id: Optional[str] = None,
        security: Optional[str] = None,
    ) -> Dict[str, RemoteTrade]:
        payload = self._base_payload()
        if order_id:
            payload["order_id"] = order_id
        if security:
            payload["security"] = security
        rows = self._client.request("broker.trades", payload) or []
        result: Dict[str, RemoteTrade] = {}
        for row in rows:
            trade = self._build_trade_snapshot(row)
            if not trade:
                continue
            result[trade.trade_id] = trade
        return result

    def get_order_status(self, order_id: str) -> Dict[str, Any]:
        payload = self._base_payload()
        payload["order_id"] = order_id
        return self._client.request("broker.order_status", payload)

    def cancel_order(self, order_id: str) -> Dict[str, Any]:
        _assert_runtime_mutation_allowed("broker.cancel_order")
        payload = self._base_payload()
        payload["order_id"] = order_id
        return self._client.request("broker.cancel_order", payload)

    def _build_order_snapshot(self, row: Dict[str, Any]) -> Optional[RemoteOrder]:
        if not isinstance(row, dict):
            return None
        order_id = row.get("order_id")
        if not order_id:
            return None
        amount = row.get("amount") or row.get("volume") or 0
        price = row.get("price")
        status = row.get("status") or row.get("state") or "open"
        filled = row.get("filled")
        if filled is None:
            filled = row.get("traded_volume") or 0
        is_buy = row.get("is_buy")
        order_remark = row.get("order_remark") or row.get("remark")
        strategy_name = row.get("strategy_name")
        return RemoteOrder(
            order_id=str(order_id),
            status=str(status),
            security=row.get("security"),
            amount=int(amount or 0),
            price=float(price) if price is not None else None,
            actual_amount=int(amount or 0),
            actual_price=float(price) if price is not None else None,
            filled=int(filled or 0),
            is_buy=bool(is_buy) if is_buy is not None else None,
            order_remark=str(order_remark) if order_remark is not None else None,
            strategy_name=str(strategy_name) if strategy_name is not None else None,
            timed_out=bool(row.get("timed_out")),
            async_tracking=bool(row.get("async_tracking")),
            last_snapshot=row.get("last_snapshot") if isinstance(row.get("last_snapshot"), dict) else None,
            raw_response=dict(row),
        )

    def _build_trade_snapshot(self, row: Dict[str, Any]) -> Optional[RemoteTrade]:
        if not isinstance(row, dict):
            return None
        trade_id = row.get("trade_id") or row.get("id") or row.get("trade_no")
        order_id = row.get("order_id") or row.get("entrust_id")
        security = row.get("security")
        if not trade_id and not order_id:
            return None
        amount = row.get("amount") or row.get("volume") or 0
        price = row.get("price") or 0.0
        raw_time = row.get("time") or row.get("trade_time")
        if isinstance(raw_time, pd.Timestamp):
            trade_time = raw_time
        elif raw_time:
            trade_time = pd.to_datetime(raw_time)
        else:
            trade_time = pd.Timestamp.now()
        if not trade_id:
            base = f"{order_id}-{trade_time}-{amount}-{price}"
            trade_id = hashlib.md5(base.encode("utf-8")).hexdigest()[:16]
        return RemoteTrade(
            trade_id=str(trade_id),
            order_id=str(order_id) if order_id is not None else "",
            security=str(security) if security else "",
            amount=int(amount or 0),
            price=float(price or 0.0),
            time=trade_time,
            commission=float(row.get("commission") or 0.0),
            tax=float(row.get("tax") or 0.0),
        )

    # ----- 内部 -----
    def _place_order(
        self,
        security: str,
        amount: int,
        price: Optional[float],
        side: str,
        wait_timeout: float,
        *,
        market: Optional[bool] = None,
        remark: Optional[str] = None,
        order_remark: Optional[str] = None,
        idempotency_key: Optional[str] = None,
    ) -> RemoteOrder:
        """
        发送下单请求到服务端。
        
        服务端会统一处理：
        - 最小手数/步进取整
        - 停牌检查
        - 市价单价格笼子计算
        - 限价单涨跌停校验
        - 卖出可卖数量检查
        """
        _assert_runtime_mutation_allowed("broker.place_order")
        try:
            _log("INFO", "[下单] 准备下单: security={}, amount={}, price={}, side={}, wait_timeout={}", 
                 security, amount, price, side, wait_timeout)
            
            payload = self._base_payload()
            
            effective_market = bool(price is None) if market is None else bool(market)
            if effective_market:
                style = {"type": "market"}
                if price is not None:
                    style["protect_price"] = float(price)
            else:
                if price is None:
                    raise ValueError("限价单缺少价格；请传入 price 或设置 market=True")
                style = {"type": "limit", "price": float(price)}
            
            payload.update({
                "security": security,
                "side": side,
                "amount": amount,
                "style": style,
                "idempotency_key": idempotency_key or self._make_idempotency_key(security, amount, side, style),
            })
            if wait_timeout is not None:
                payload["wait_timeout"] = wait_timeout
            if effective_market:
                payload["market"] = True
            effective_remark = order_remark if order_remark is not None else remark
            if effective_remark:
                payload["order_remark"] = effective_remark
            
            _log("DEBUG", "[下单] 发送下单请求: payload={}", payload)
            try:
                resp = self._request_place_order(
                    "broker.place_order",
                    payload,
                    timeout=self._resolve_place_order_rpc_timeout(wait_timeout),
                )
            except Exception as exc:
                if self._is_submit_unknown_timeout_error(exc):
                    error_msg = (
                        "下单请求超时，状态=submit_unknown，需要后续核对远端订单: "
                        f"security={security}, amount={amount}, side={side}, error={exc}"
                    )
                    _log("ERROR", "[下单错误] {}", error_msg)
                    raise RuntimeError(error_msg) from exc
                raise
            _log("DEBUG", "[下单] 收到下单响应: resp={}", resp)
            
            # 处理服务端警告
            warning = resp.get("warning") if isinstance(resp, dict) else None
            if warning:
                _log("WARN", "[远程警告] {}", warning)
            
            # 服务端返回实际执行的数量和价格（可能因取整/价格笼子而不同）
            actual_amount = resp.get("amount") if isinstance(resp, dict) else None
            actual_price = resp.get("price") if isinstance(resp, dict) else None
            
            # 检查订单 ID
            order_id = resp.get("order_id") if isinstance(resp, dict) else None
            if not order_id:
                error_msg = f"服务端未返回 order_id，响应: {resp}"
                _log("ERROR", "[下单错误] {}", error_msg)
                raise RuntimeError(error_msg)
            
            # 【逻辑变更】如果订单 ID 是 -1，说明 QMT 下单失败，抛出异常而非静默返回
            # 原因：-1 是 QMT 返回的错误码，表示下单失败，应该让调用方知道
            if order_id == "-1" or (isinstance(order_id, (int, float)) and order_id < 0):
                error_msg = f"下单失败，服务端返回错误订单号: {order_id}, 响应: {resp}"
                _log("ERROR", "[下单错误] {}", error_msg)
                raise RuntimeError(error_msg)

            status = str(resp.get("status") or resp.get("order_status") or "").strip().lower()
            if status == "submit_unknown":
                error_msg = f"下单提交状态未知，需要后续核对远端订单: order_id={order_id}, 响应: {resp}"
                _log("ERROR", "[下单错误] {}", error_msg)
                raise RuntimeError(error_msg)
            if status in {"rejected", "canceled", "cancelled", "failed", "error"}:
                error_msg = f"下单失败，服务端返回终态失败: order_id={order_id}, status={status}, 响应: {resp}"
                _log("ERROR", "[下单错误] {}", error_msg)
                raise RuntimeError(error_msg)
            
            # 如果服务端返回了不同的数量，提示用户
            if actual_amount is not None and actual_amount != amount:
                _log("INFO", "[下单] {} 数量已从 {} 调整为 {}（最小手数/步进取整）", 
                     security, amount, actual_amount)
            
            order = RemoteOrder(
                order_id=str(order_id),
                status=status or "submitted",
                security=security,
                amount=amount,
                price=price,
                actual_amount=actual_amount,
                actual_price=actual_price,
                timed_out=bool(resp.get("timed_out")) if isinstance(resp, dict) else False,
                async_tracking=bool(resp.get("async_tracking")) if isinstance(resp, dict) else False,
                last_snapshot=resp.get("last_snapshot") if isinstance(resp.get("last_snapshot"), dict) else None,
                raw_response=dict(resp) if isinstance(resp, dict) else {},
            )
            
            if order.status in {"open", "submitted", "new", "filling"} or order.timed_out or order.async_tracking:
                _log("INFO", "[下单] 订单已提交，等待成交确认: order_id={}, status={}", order.order_id, order.status)
            else:
                _log("INFO", "[下单] 订单创建成功: order_id={}, status={}", order.order_id, order.status)
            
            if wait_timeout and order.order_id and not (order.timed_out or order.async_tracking):
                _log("DEBUG", "[下单] 开始等待订单状态 (timeout={}s)", wait_timeout)
                self._wait_order(order.order_id, wait_timeout)
            
            return order
        except Exception as e:
            _log("ERROR", "[下单错误] 下单过程异常: security={}, amount={}, side={}, error={}", 
                 security, amount, side, e)
            _log("ERROR", "[下单错误] 堆栈:\n{}", traceback.format_exc())
            raise

    def _resolve_place_order_rpc_timeout(self, wait_timeout: float) -> float:
        """解析下单 RPC 请求超时时间。

        Args:
            wait_timeout: 本次下单等待终态秒数。

        Returns:
            float: 单次 RPC 接收响应超时时间。
        """
        try:
            wait_seconds = float(wait_timeout or 0)
        except (TypeError, ValueError):
            wait_seconds = 0.0
        rpc_timeout = max(5.0, float(getattr(self._client, "rpc_timeout", DEFAULT_RPC_TIMEOUT_SECONDS)))
        if wait_seconds <= 0:
            return rpc_timeout
        return max(rpc_timeout, wait_seconds + self.place_order_timeout_margin)

    def _request_place_order(
        self,
        action: str,
        payload: Dict[str, Any],
        *,
        timeout: float,
    ) -> Dict[str, Any]:
        """发送下单请求，并兼容旧版 request 签名。

        Args:
            action: 远程 action 名称。
            payload: 请求载荷。
            timeout: 新版短连接客户端支持的单次请求超时。

        Returns:
            Dict[str, Any]: 远程响应。

        兼容性:
            部分外部用户或测试桩只实现 `request(action, payload)`，不接受
            `timeout` 关键字。此处只在签名不兼容时回退旧调用，避免破坏旧 helper
            使用方式；其他 TypeError 继续抛出。
        """

        try:
            return self._client.request(action, payload, timeout=timeout)
        except TypeError as exc:
            message = str(exc)
            if "timeout" not in message or "unexpected keyword" not in message:
                raise
            return self._client.request(action, payload)

    @staticmethod
    def _is_submit_unknown_timeout_error(exc: Exception) -> bool:
        """判断下单异常是否属于无订单号的提交状态未知。

        Args:
            exc: 下单请求阶段抛出的异常。

        Returns:
            bool: True 表示应映射为 submit_unknown 风险；False 表示保留原异常语义。
        """

        if isinstance(exc, TimeoutError):
            return True
        message = str(exc).lower()
        return "timeout" in message or "超时" in message

    def _wait_order(self, order_id: str, timeout: float) -> None:
        start = time.time()
        interval = 1.0
        while time.time() - start < timeout:
            try:
                status = self.get_order_status(order_id)
                st = str(status.get("status") or "").lower()
                if st in {
                    "filled",
                    "cancelled",
                    "canceled",
                    "rejected",
                    "partly_canceled",
                    "failed",
                    "error",
                }:
                    return
            except Exception:
                pass
            time.sleep(interval)

    def _current_amount(self, security: str) -> int:
        for pos in self.get_positions():
            if pos.security == security:
                return int(pos.amount)
        return 0

    def _infer_price(self, security: str) -> Optional[float]:
        if self._data_client:
            return self._data_client.get_last_price(security)
        return None

    def _base_payload(self) -> Dict[str, Any]:
        return {"account_key": self.account_key, "sub_account_id": self.sub_account_id}

    def _make_idempotency_key(self, security: str, amount: int, side: str, style: Dict[str, Any]) -> str:
        raw = f"{security}|{amount}|{side}|{style}|{_now_ns()}|{os.urandom(8).hex()}"
        return "bt-helper-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


# --------- TCP 客户端 ----------
class _ShortLivedClient:
    """
    简单的 TCP+JSON 客户端：每次请求都会重新连接、握手、发送请求并等待响应；失败会按配置重试。
    
    注意：每次 request() 调用都会建立新的 TCP 连接，连接后立即握手、发送请求、接收响应、关闭连接。
    这种设计适合聚宽环境频繁重启的场景，但会产生较多连接开销。
    """

    def __init__(
        self,
        host: str,
        port: int,
        token: str,
        *,
        tls_cert: Optional[str] = None,
        retries: int = 2,
        retry_interval: float = 0.5,
        rpc_timeout: float = DEFAULT_RPC_TIMEOUT_SECONDS,
    ):
        self.host = host
        self.port = port
        self.token = token
        self.tls_cert = tls_cert
        self.retries = max(0, retries)
        self.retry_interval = max(0.1, float(retry_interval))
        self.rpc_timeout = max(5.0, float(rpc_timeout))

    @_track_runtime_request
    def request(
        self,
        action: str,
        payload: Dict[str, Any],
        timeout: Optional[float] = None,
        *,
        _runtime_lease_generation: Optional[int] = None,
        _runtime_lease_instance_token: Optional[object] = None,
        _runtime_lease_module_generation: Optional[int] = None,
        _runtime_lease_registry: Optional[Set[object]] = None,
        _runtime_lease_token: Optional[object] = None,
        _runtime_lease_handoff_state: Optional[List[bool]] = None,
        _runtime_lease_resource_state: Optional[List[Optional[Any]]] = None,
    ) -> Dict[str, Any]:
        """
        发送 RPC 请求（每次调用都会建立新的 TCP 连接）。
        
        Args:
            action: RPC 动作名称，如 "broker.place_order"
            payload: 请求载荷
            timeout: 本次请求超时；不传时使用客户端默认 rpc_timeout
            
        Returns:
            响应字典
            
        Raises:
            RuntimeError: 所有重试都失败后抛出最后一个异常
        """
        _assert_runtime_remote_allowed(action)
        if (
            type(_runtime_lease_handoff_state) is not list
            or len(_runtime_lease_handoff_state) != 1
            or type(_runtime_lease_resource_state) is not list
            or len(_runtime_lease_resource_state) != 1
            or _runtime_lease_resource_state[0] is not None
        ):
            _fail_runtime_generation_drift(None)
            raise RuntimeError(
                "策略运行远程请求交付状态无效；必须使用干净运行进程重启"
            )
        request_anchor = _runtime_primitive_anchor_snapshot()
        if (
            request_anchor is None
            or type(request_anchor[6]) is not tuple
            or len(request_anchor[6]) != 7
            or type(request_anchor[6][6]) is not types.FunctionType
        ):
            _fail_runtime_generation_drift(None)
            raise RuntimeError(
                "策略运行远程effect authority无效；必须使用干净运行进程重启"
            )
        run_remote_effect = request_anchor[6][6]
        request_socket_lock = request_anchor[7]
        effective_timeout = max(5.0, float(timeout or self.rpc_timeout))
        last_error: Optional[Exception] = None
        attempts = self.retries + 1
        request_start_time = time.time()
        mutation_action = type(action) is str and action in {
            "broker.place_order",
            "broker.cancel_order",
        }
        mutation_request_started = False
        
        _log("INFO", "[RPC] 开始请求: action={}, host={}, port={}, attempts={}", action, self.host, self.port, attempts)
        
        for attempt in range(1, attempts + 1):
            # runtime切换会让已有lease停止后续重试；当前已开始的attempt可收尾，
            # 但不得在TRANSITIONING/FAILED后再创建新socket。
            _assert_runtime_request_lease_current(
                action,
                _runtime_lease_generation,
                _runtime_lease_instance_token,
                _runtime_lease_module_generation,
                _runtime_lease_registry,
                _runtime_lease_token,
            )
            sock: Optional[socket.socket] = None
            connect_start_time = time.time()
            
            try:
                # ========== 1. 建立 TCP 连接 ==========
                _log("DEBUG", "[RPC] [尝试 {}/{}] 正在连接 TCP: {}:{}", attempt, attempts, self.host, self.port)

                try:
                    # 日志/计时之后再通过共享socket gate原子登记attempt起点；
                    # reload关闭同一gate并等待已登记的create_connection返回。
                    sock = _create_runtime_socket_with_lease(
                        (self.host, self.port),
                        10,
                        action,
                        _runtime_lease_generation,
                        _runtime_lease_instance_token,
                        _runtime_lease_module_generation,
                        _runtime_lease_registry,
                        _runtime_lease_token,
                        _runtime_lease_resource_state,
                    )
                    _assert_runtime_request_lease_current(
                        action,
                        _runtime_lease_generation,
                        _runtime_lease_instance_token,
                        _runtime_lease_module_generation,
                        _runtime_lease_registry,
                        _runtime_lease_token,
                    )
                    connect_duration = time.time() - connect_start_time
                    _log("DEBUG", "[RPC] [尝试 {}/{}] TCP 连接成功，耗时 {:.3f}s", attempt, attempts, connect_duration)
                except socket.gaierror as e:
                    # DNS 解析失败（这就是 "Name or service not known" 错误的来源）
                    error_msg = f"DNS 解析失败: host={self.host}, error={e}"
                    _log("ERROR", "[RPC] [尝试 {}/{}] {}", attempt, attempts, error_msg)
                    _log("ERROR", "[RPC] [尝试 {}/{}] 堆栈:\n{}", attempt, attempts, traceback.format_exc())
                    last_error = RuntimeError(error_msg)
                    if attempt < attempts:
                        _log("INFO", "[RPC] [尝试 {}/{}] {}s 后重试...", attempt, attempts, self.retry_interval)
                        time.sleep(self.retry_interval)
                    continue
                except socket.timeout as e:
                    error_msg = f"连接超时: host={self.host}, port={self.port}, timeout=10s"
                    _log("ERROR", "[RPC] [尝试 {}/{}] {}", attempt, attempts, error_msg)
                    _log("ERROR", "[RPC] [尝试 {}/{}] 堆栈:\n{}", attempt, attempts, traceback.format_exc())
                    last_error = RuntimeError(error_msg)
                    if attempt < attempts:
                        _log("INFO", "[RPC] [尝试 {}/{}] {}s 后重试...", attempt, attempts, self.retry_interval)
                        time.sleep(self.retry_interval)
                    continue
                except (ConnectionRefusedError, OSError) as e:
                    error_msg = f"连接被拒绝或网络错误: host={self.host}, port={self.port}, error={e}"
                    _log("ERROR", "[RPC] [尝试 {}/{}] {}", attempt, attempts, error_msg)
                    _log("ERROR", "[RPC] [尝试 {}/{}] 堆栈:\n{}", attempt, attempts, traceback.format_exc())
                    last_error = RuntimeError(error_msg)
                    if attempt < attempts:
                        _log("INFO", "[RPC] [尝试 {}/{}] {}s 后重试...", attempt, attempts, self.retry_interval)
                        time.sleep(self.retry_interval)
                    continue
                
                # ========== 2. TLS 握手（如果启用） ==========
                if self.tls_cert:
                    try:
                        _log("DEBUG", "[RPC] [尝试 {}/{}] 开始 TLS 握手", attempt, attempts)
                        _assert_runtime_request_lease_current(
                            action,
                            _runtime_lease_generation,
                            _runtime_lease_instance_token,
                            _runtime_lease_module_generation,
                            _runtime_lease_registry,
                            _runtime_lease_token,
                        )
                        context = ssl.create_default_context(cafile=self.tls_cert)
                        with request_socket_lock:
                            effect_allowed, wrapped_socket = run_remote_effect(
                                context.wrap_socket,
                                (sock,),
                                {"server_hostname": self.host},
                                None,
                            )
                        if not effect_allowed or wrapped_socket is None:
                            raise RuntimeError(
                                "策略运行reload已撤销TLS transport许可"
                            )
                        _runtime_lease_resource_state[0] = wrapped_socket
                        sock = wrapped_socket
                        _log("DEBUG", "[RPC] [尝试 {}/{}] TLS 握手成功", attempt, attempts)
                    except Exception as e:
                        error_msg = f"TLS 握手失败: {e}"
                        _log("ERROR", "[RPC] [尝试 {}/{}] {}", attempt, attempts, error_msg)
                        _log("ERROR", "[RPC] [尝试 {}/{}] 堆栈:\n{}", attempt, attempts, traceback.format_exc())
                        last_error = RuntimeError(error_msg)
                        if attempt < attempts:
                            _log("INFO", "[RPC] [尝试 {}/{}] {}s 后重试...", attempt, attempts, self.retry_interval)
                            time.sleep(self.retry_interval)
                        continue
                
                sock.settimeout(effective_timeout)
                _assert_runtime_request_lease_current(
                    action,
                    _runtime_lease_generation,
                    _runtime_lease_instance_token,
                    _runtime_lease_module_generation,
                    _runtime_lease_registry,
                    _runtime_lease_token,
                )
                
                # ========== 3. 应用层握手 ==========
                try:
                    _log("DEBUG", "[RPC] [尝试 {}/{}] 发送应用层握手", attempt, attempts)
                    handshake_msg = {
                        "type": "handshake",
                        "protocol": HELPER_PROTOCOL_VERSION,
                        "token": self.token,
                        "features": [],
                    }
                    _assert_runtime_request_lease_current(
                        action,
                        _runtime_lease_generation,
                        _runtime_lease_instance_token,
                        _runtime_lease_module_generation,
                        _runtime_lease_registry,
                        _runtime_lease_token,
                    )
                    with request_socket_lock:
                        effect_allowed, _ = run_remote_effect(
                            self._send,
                            (sock, handshake_msg),
                            {},
                            None,
                        )
                    if not effect_allowed:
                        raise RuntimeError(
                            "策略运行reload已撤销handshake send许可"
                        )
                    ack = self._recv(sock)
                    _log("DEBUG", "[RPC] [尝试 {}/{}] 收到握手响应: {}", attempt, attempts, ack.get("type"))
                    
                    if ack.get("type") != "handshake_ack":
                        raise RuntimeError(f"远程服务拒绝握手: {ack}")
                    server_protocol = ack.get("protocol")
                    server_protocol_value = None
                    if server_protocol is not None:
                        try:
                            server_protocol_value = int(server_protocol)
                        except (TypeError, ValueError):
                            server_protocol_value = None
                    if server_protocol_value is not None and server_protocol_value > HELPER_PROTOCOL_VERSION:
                        _warn(
                            "远程服务协议版本 {} 高于本地 helper 版本 {}，建议升级 helper",
                            server_protocol_value,
                            HELPER_PROTOCOL_VERSION,
                        )
                    _log("DEBUG", "[RPC] [尝试 {}/{}] 应用层握手成功", attempt, attempts)
                except Exception as e:
                    error_msg = f"应用层握手失败: {e}"
                    _log("ERROR", "[RPC] [尝试 {}/{}] {}", attempt, attempts, error_msg)
                    _log("ERROR", "[RPC] [尝试 {}/{}] 堆栈:\n{}", attempt, attempts, traceback.format_exc())
                    last_error = RuntimeError(error_msg)
                    if attempt < attempts:
                        _log("INFO", "[RPC] [尝试 {}/{}] {}s 后重试...", attempt, attempts, self.retry_interval)
                        time.sleep(self.retry_interval)
                    continue
                
                # ========== 4. 发送 RPC 请求 ==========
                req_id = str(id(payload) ^ int.from_bytes(os.urandom(4), "big"))
                request_msg = {"type": "request", "id": req_id, "action": action, "payload": payload}
                
                _log("DEBUG", "[RPC] [尝试 {}/{}] 发送 RPC 请求: action={}, req_id={}, payload_keys={}", 
                     attempt, attempts, action, req_id, list(payload.keys()) if isinstance(payload, dict) else "N/A")
                
                try:
                    _assert_runtime_request_lease_current(
                        action,
                        _runtime_lease_generation,
                        _runtime_lease_instance_token,
                        _runtime_lease_module_generation,
                        _runtime_lease_registry,
                        _runtime_lease_token,
                    )
                    with request_socket_lock:
                        effect_allowed, _ = run_remote_effect(
                            self._send,
                            (sock, request_msg),
                            {},
                            _runtime_lease_handoff_state
                            if mutation_action
                            else None,
                        )
                    if mutation_action and _runtime_lease_handoff_state[0]:
                        mutation_request_started = True
                    if not effect_allowed:
                        raise RuntimeError(
                            "策略运行reload已撤销RPC send许可"
                        )
                    _log("DEBUG", "[RPC] [尝试 {}/{}] RPC 请求已发送", attempt, attempts)
                except Exception as e:
                    if mutation_action and _runtime_lease_handoff_state[0]:
                        mutation_request_started = True
                    error_msg = f"发送 RPC 请求失败: {e}"
                    _log("ERROR", "[RPC] [尝试 {}/{}] {}", attempt, attempts, error_msg)
                    _log("ERROR", "[RPC] [尝试 {}/{}] 堆栈:\n{}", attempt, attempts, traceback.format_exc())
                    last_error = RuntimeError(error_msg)
                    if mutation_request_started:
                        raise last_error
                    if attempt < attempts:
                        _log("INFO", "[RPC] [尝试 {}/{}] {}s 后重试...", attempt, attempts, self.retry_interval)
                        time.sleep(self.retry_interval)
                    continue
                
                # ========== 5. 接收响应 ==========
                response_start_time = time.time()
                try:
                    _log("DEBUG", "[RPC] [尝试 {}/{}] 等待 RPC 响应 (timeout={}s)", attempt, attempts, effective_timeout)
                    while True:
                        message = self._recv(sock)
                        msg_type = message.get("type")
                        _log("DEBUG", "[RPC] [尝试 {}/{}] 收到消息: type={}, id={}", 
                             attempt, attempts, msg_type, message.get("id"))
                        
                        if msg_type == "response" and message.get("id") == req_id:
                            response_duration = time.time() - response_start_time
                            response_payload = message.get("payload") or {}
                            _log("INFO", "[RPC] [尝试 {}/{}] RPC 请求成功: action={}, 耗时 {:.3f}s, response_keys={}", 
                                 attempt, attempts, action, response_duration, 
                                 list(response_payload.keys()) if isinstance(response_payload, dict) else "N/A")
                            return response_payload
                        
                        if msg_type == "error":
                            error_payload = message.get("payload") or {}
                            error_message = message.get("message", "server error")
                            _log("ERROR", "[RPC] [尝试 {}/{}] 服务器返回错误: message={}, payload={}", 
                                 attempt, attempts, error_message, error_payload)
                            raise RuntimeError(f"服务器错误: {error_message}")
                            
                except socket.timeout as e:
                    error_msg = f"接收响应超时: timeout={effective_timeout}s"
                    _log("ERROR", "[RPC] [尝试 {}/{}] {}", attempt, attempts, error_msg)
                    _log("ERROR", "[RPC] [尝试 {}/{}] 堆栈:\n{}", attempt, attempts, traceback.format_exc())
                    last_error = RuntimeError(error_msg)
                    if mutation_request_started:
                        raise last_error
                    if attempt < attempts:
                        _log("INFO", "[RPC] [尝试 {}/{}] {}s 后重试...", attempt, attempts, self.retry_interval)
                        time.sleep(self.retry_interval)
                    continue
                except Exception as e:
                    error_msg = f"接收响应失败: {e}"
                    _log("ERROR", "[RPC] [尝试 {}/{}] {}", attempt, attempts, error_msg)
                    _log("ERROR", "[RPC] [尝试 {}/{}] 堆栈:\n{}", attempt, attempts, traceback.format_exc())
                    last_error = RuntimeError(error_msg)
                    if mutation_request_started:
                        raise last_error
                    if attempt < attempts:
                        _log("INFO", "[RPC] [尝试 {}/{}] {}s 后重试...", attempt, attempts, self.retry_interval)
                        time.sleep(self.retry_interval)
                    continue
                
            except Exception as exc:
                # 捕获所有其他未预期的异常
                if mutation_request_started:
                    raise
                error_msg = f"未预期的异常: {exc}"
                _log("ERROR", "[RPC] [尝试 {}/{}] {}", attempt, attempts, error_msg)
                _log("ERROR", "[RPC] [尝试 {}/{}] 堆栈:\n{}", attempt, attempts, traceback.format_exc())
                last_error = exc
                if attempt < attempts:
                    _log("INFO", "[RPC] [尝试 {}/{}] {}s 后重试...", attempt, attempts, self.retry_interval)
                    time.sleep(self.retry_interval)
            finally:
                # ========== 6. 关闭连接 ==========
                pending_socket = _runtime_lease_resource_state[0]
                if pending_socket is not None and pending_socket is not sock:
                    _runtime_lease_handoff_state[0] = True
                    try:
                        pending_socket.close()
                    except BaseException:
                        pass
                if sock is not None:
                    try:
                        _log("DEBUG", "[RPC] [尝试 {}/{}] 关闭 TCP 连接", attempt, attempts)
                        sock.close()
                    except Exception as e:
                        _log("WARN", "[RPC] [尝试 {}/{}] 关闭连接时出错: {}", attempt, attempts, e)
                _runtime_lease_resource_state[0] = None
        
        # 所有重试都失败
        total_duration = time.time() - request_start_time
        final_error_msg = f"远程请求失败（已重试 {attempts} 次，总耗时 {total_duration:.3f}s）: {last_error}"
        _log("ERROR", "[RPC] {}", final_error_msg)
        if last_error:
            _log("ERROR", "[RPC] 最后一次错误的堆栈:\n{}", traceback.format_exc())
        raise RuntimeError(final_error_msg)

    def _send(self, sock: socket.socket, message: Dict[str, Any]) -> None:
        """发送消息到服务器"""
        try:
            body = json.dumps(message, ensure_ascii=False).encode("utf-8")
            header = struct.pack(">I", len(body))
            sock.sendall(header + body)
            _log("DEBUG", "[RPC] 已发送消息: type={}, size={} bytes", message.get("type"), len(body))
        except Exception as e:
            _log("ERROR", "[RPC] 发送消息失败: {}, 堆栈:\n{}", e, traceback.format_exc())
            raise

    def _recv(self, sock: socket.socket) -> Dict[str, Any]:
        """从服务器接收消息"""
        try:
            header = self._read_exact(sock, 4)
            size = struct.unpack(">I", header)[0]
            _log("DEBUG", "[RPC] 收到消息头: size={} bytes", size)
            payload = self._read_exact(sock, size)
            message = json.loads(payload.decode("utf-8"))
            _log("DEBUG", "[RPC] 已解析消息: type={}", message.get("type"))
            return message
        except Exception as e:
            _log("ERROR", "[RPC] 接收消息失败: {}, 堆栈:\n{}", e, traceback.format_exc())
            raise

    def _read_exact(self, sock: socket.socket, size: int) -> bytes:
        """精确读取指定字节数"""
        buf = b""
        read_start = time.time()
        while len(buf) < size:
            remaining = size - len(buf)
            try:
                chunk = sock.recv(remaining)
                if not chunk:
                    raise RuntimeError(f"连接中断（已读取 {len(buf)}/{size} 字节）")
                buf += chunk
            except socket.timeout:
                elapsed = time.time() - read_start
                raise RuntimeError(f"读取超时（已读取 {len(buf)}/{size} 字节，耗时 {elapsed:.3f}s）")
        return buf


# --------- 工具函数 ----------
_PRICE_FIELD_NAMES = {
    "time",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "money",
    "amount",
    "avg",
    "price",
    "highlimit",
    "lowlimit",
    "paused",
    "preclose",
    "pre_close",
    "suspendflag",
    "suspend_flag",
    "openinterest",
    "open_interest",
    "settlementprice",
    "settelementprice",
}


def _price_field_tokens(values) -> Set[str]:
    return {str(value).replace(" ", "").replace("_", "").lower() for value in values}


def _normalise_price_multiindex_columns(columns: pd.MultiIndex) -> pd.MultiIndex:
    if columns.nlevels != 2:
        return columns
    level0 = _price_field_tokens(columns.get_level_values(0))
    level1 = _price_field_tokens(columns.get_level_values(1))
    if (level1 & _PRICE_FIELD_NAMES) and not (level0 & _PRICE_FIELD_NAMES):
        columns = columns.swaplevel(0, 1)
        columns.names = ["field", "code"]
    elif (level0 & _PRICE_FIELD_NAMES) and not (level1 & _PRICE_FIELD_NAMES):
        columns.names = ["field", "code"]
    return columns


def _multiindex_from_payload_columns(column_tuples, names) -> pd.MultiIndex:
    tuples = [tuple(items) for items in column_tuples]
    index = pd.MultiIndex.from_tuples(tuples)
    if names and len(names) == index.nlevels:
        index.names = list(names)
    return index


def _parse_legacy_tuple_columns(columns):
    parsed = []
    for column in columns:
        if not isinstance(column, str) or not column.startswith("("):
            return columns
        try:
            value = ast.literal_eval(column)
        except Exception:
            return columns
        if not isinstance(value, tuple) or len(value) != 2:
            return columns
        parsed.append(value)
    return pd.MultiIndex.from_tuples(parsed) if parsed else columns


def _df_from_payload(payload: Dict[str, Any]) -> pd.DataFrame:
    if not payload or payload.get("dtype") != "dataframe":
        return pd.DataFrame()
    columns = payload.get("columns") or []
    column_tuples = payload.get("column_tuples") or None
    records = payload.get("records") or []
    if column_tuples:
        columns = _multiindex_from_payload_columns(column_tuples, payload.get("column_index_names"))
    else:
        columns = _parse_legacy_tuple_columns(columns)
    df = pd.DataFrame(records, columns=columns)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = _normalise_price_multiindex_columns(df.columns)
    return df


# --------- 聚宽策略零改兼容层 ----------
_JQ_COMPAT_STATE_KEY = "__bt_jq_compat_state__"
_STRATEGY_RUNTIME_STATE_KEY = "__bt_strategy_runtime_state__"
_JQ_COMPAT_FUNCTIONS = [
    "order",
    "order_value",
    "order_percent",
    "order_target",
    "order_target_value",
    "order_target_percent",
    "cancel_order",
    "get_open_orders",
    "get_orders",
    "get_trades",
]


class _RemoteJQPosition:
    """聚宽风格持仓对象，字段来自远程真实账户。"""

    def __init__(self, source: Optional[RemotePosition] = None, security: Optional[str] = None):
        if source is None:
            self.security = security or ""
            self.total_amount = 0
            self.closeable_amount = 0
            self.locked_amount = 0
            self.value = 0.0
            self.price = 0.0
            self.avg_cost = 0.0
            self.hold_cost = 0.0
            self.market = None
            return
        self.security = source.security
        self.total_amount = int(source.amount or 0)
        self.closeable_amount = int(source.available or 0)
        self.locked_amount = int(source.frozen or 0)
        self.value = float(source.market_value or 0.0)
        self.price = self.value / self.total_amount if self.total_amount else 0.0
        self.avg_cost = float(source.avg_cost or 0.0)
        self.hold_cost = self.avg_cost
        self.market = source.market


class _RemotePositionDict(dict):
    """不存在的持仓返回空仓位，兼容聚宽常见写法。"""

    def __missing__(self, key):
        return _RemoteJQPosition(security=str(key))


class _RemoteSnapshotCache:
    def __init__(self, broker: RemoteBrokerClient, ttl_seconds: float = 1.0):
        self.broker = broker
        self.ttl_seconds = max(0.0, float(ttl_seconds))
        self._snapshot: Optional[Dict[str, Any]] = None
        self._snapshot_at = 0.0

    def invalidate(self) -> None:
        self._snapshot = None
        self._snapshot_at = 0.0

    def snapshot(self) -> Dict[str, Any]:
        now = time.time()
        if self._snapshot is not None and now - self._snapshot_at <= self.ttl_seconds:
            return self._snapshot
        account = self.broker.get_account()
        raw_positions = self.broker.get_positions()
        positions = _RemotePositionDict()
        for pos in raw_positions:
            positions[pos.security] = _RemoteJQPosition(pos)
        positions_value = sum(float(pos.value or 0.0) for pos in positions.values())
        self._snapshot = {
            "account": account,
            "positions": positions,
            "positions_value": positions_value,
        }
        self._snapshot_at = now
        return self._snapshot


class _RemoteJQPortfolio:
    _bt_remote_portfolio_marker = "bullet-trade-remote-jq-portfolio-v1"

    def __init__(self, cache: _RemoteSnapshotCache):
        self._cache = cache
        self.subportfolios = []

    @property
    def available_cash(self) -> float:
        return float(self._cache.snapshot()["account"].available_cash)

    @property
    def total_value(self) -> float:
        return float(self._cache.snapshot()["account"].total_value)

    @property
    def positions_value(self) -> float:
        return float(self._cache.snapshot()["positions_value"])

    @property
    def positions(self) -> _RemotePositionDict:
        return self._cache.snapshot()["positions"]


class _RemoteJQSubPortfolio(_RemoteJQPortfolio):
    @property
    def long_positions(self) -> _RemotePositionDict:
        return self.positions

    @property
    def short_positions(self) -> Dict[str, _RemoteJQPosition]:
        return {}

    @property
    def transferable_cash(self) -> float:
        return self.available_cash

    @property
    def locked_cash(self) -> float:
        return 0.0

    @property
    def type(self) -> str:
        return "stock"


def _run_type_from_context(context: Any) -> Optional[str]:
    run_params = getattr(context, "run_params", None)
    if isinstance(run_params, dict):
        return run_params.get("type")
    return getattr(run_params, "type", None)


_PROFILE_REQUIRED_FIELDS = {"strategy_id", "host", "token"}
_PROFILE_OPTIONAL_FIELDS = {
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
_PROFILE_ALLOWED_FIELDS = _PROFILE_REQUIRED_FIELDS | _PROFILE_OPTIONAL_FIELDS
_RUNTIME_MUTATION_NAMES = {
    "order",
    "order_value",
    "order_percent",
    "order_target",
    "order_target_value",
    "order_target_percent",
    "cancel_order",
}


def _runtime_importlib_reload_in_progress() -> bool:
    """观察CPython importlib的稳定进程级reload登记，封住并发/递归首行窗口。"""

    try:
        importlib_module = dict.get(sys.modules, "importlib")
        if type(importlib_module) is not types.ModuleType:
            return False
        importlib_namespace = object.__getattribute__(
            importlib_module,
            "__dict__",
        )
        reloading = dict.get(importlib_namespace, "_RELOADING")
        module_name = globals().get("__name__")
        if type(reloading) is not dict or type(module_name) is not str:
            return False
        current_module = dict.get(sys.modules, module_name)
        return current_module is not None and dict.get(
            reloading,
            module_name,
        ) is current_module
    except BaseException:
        return False


def _assert_runtime_mutation_allowed(
    operation: str,
    reservation_owner: Optional[int] = None,
) -> None:
    if _runtime_importlib_reload_in_progress():
        _set_runtime_failed_process_state()
        raise RuntimeError(
            "策略运行helper正在由importlib重载；禁止交易变更: {}".format(
                operation
            )
        )
    active_mode = _STRATEGY_RUNTIME_ACTIVE_MODE
    if active_mode is not None:
        if type(active_mode) is not str or active_mode not in {
            "TRANSITIONING",
            "BACKTEST",
            "SHADOW",
            "LIVE_BLOCKED",
            "FAILED",
        }:
            _fail_runtime_generation_drift(None)
            raise RuntimeError(
                "INVALID模式禁止交易变更；必须使用干净运行进程重启"
            )
        raise RuntimeError(
            "{}模式禁止交易变更: {}".format(active_mode, operation)
        )
    transition_valid, transition_owner, _, _ = _runtime_transition_snapshot()
    if not transition_valid:
        _fail_runtime_generation_drift(None)
        raise RuntimeError(
            "策略运行transition状态无效；必须使用干净运行进程重启"
        )
    if transition_owner is not None and (
        type(reservation_owner) is not int
        or transition_owner != reservation_owner
    ):
        raise RuntimeError(
            "策略运行mutation reservation在途；禁止交易变更: {}".format(
                operation
            )
        )
    return


def _assert_runtime_reload_latch_open(operation: str) -> None:
    """任何旧调用栈在reload锁存后都不得继续发布运行状态。"""

    importlib_reload_in_progress = _runtime_importlib_reload_in_progress()
    reload_in_progress = _STRATEGY_RUNTIME_RELOAD_IN_PROGRESS
    active_mode = _STRATEGY_RUNTIME_ACTIVE_MODE
    primitive_anchor = _runtime_primitive_anchor_snapshot()
    gate_state = _runtime_socket_gate_authority_snapshot(primitive_anchor)
    if (
        primitive_anchor is None
        or gate_state is None
        or gate_state[0]
        or importlib_reload_in_progress
        or type(reload_in_progress) is not bool
        or reload_in_progress
        or (type(active_mode) is str and active_mode == "FAILED")
    ):
        _set_runtime_failed_process_state()
        raise RuntimeError(
            "策略运行helper已重载或失败锁存；禁止继续状态变更: {}".format(
                operation
            )
        )


def _assert_runtime_remote_allowed(operation: str) -> None:
    if _runtime_importlib_reload_in_progress():
        _set_runtime_failed_process_state()
        raise RuntimeError(
            "策略运行helper正在由importlib重载；禁止远程访问: {}".format(
                operation
            )
        )
    active_mode = _STRATEGY_RUNTIME_ACTIVE_MODE
    if active_mode is None:
        return
    if type(active_mode) is not str or active_mode not in {
        "TRANSITIONING",
        "BACKTEST",
        "SHADOW",
        "LIVE_BLOCKED",
        "FAILED",
    }:
        _fail_runtime_generation_drift(None)
        raise RuntimeError(
            "INVALID模式禁止远程访问；必须使用干净运行进程重启"
        )
    raise RuntimeError("{}模式禁止远程访问: {}".format(active_mode, operation))


def _assert_no_inflight_runtime_requests(operation: str) -> None:
    inflight_requests = _STRATEGY_RUNTIME_INFLIGHT_REQUESTS
    anchor = _runtime_primitive_anchor_snapshot()
    request_leases = anchor[4] if anchor is not None else None
    registry_snapshot = _runtime_request_registry_snapshot(request_leases)
    if (
        type(inflight_requests) is not int
        or inflight_requests < 0
        or type(request_leases) is not set
        or registry_snapshot is None
        or inflight_requests != len(registry_snapshot)
    ):
        _fail_runtime_generation_drift(None)
        raise RuntimeError("远程请求计数状态无效；必须使用干净运行进程重启")
    if inflight_requests != 0:
        raise RuntimeError(
            "{}时仍有{}个远程请求在途；必须使用干净运行进程重启".format(
                operation,
                inflight_requests,
            )
        )


def _assert_runtime_request_lease_current(
    operation: str,
    lease_generation: Optional[int],
    lease_instance_token: Optional[object],
    lease_module_generation: Optional[int],
    lease_registry: Optional[Set[object]],
    lease_token: Optional[object],
) -> None:
    runtime_lock = _require_trusted_runtime_lock()
    with runtime_lock:
        current_generation = _STRATEGY_RUNTIME_CONTRACT_GENERATION
        current_inflight = _STRATEGY_RUNTIME_INFLIGHT_REQUESTS
        registry_snapshot = _runtime_request_registry_snapshot(lease_registry)
        if (
            not _runtime_module_generation_matches(
                lease_instance_token,
                lease_module_generation,
            )
            or type(lease_generation) is not int
            or lease_generation < 0
            or type(current_generation) is not int
            or current_generation < 0
            or type(current_inflight) is not int
            or current_inflight <= 0
            or type(lease_registry) is not set
            or lease_registry is not _STRATEGY_RUNTIME_REQUEST_LEASES
            or type(lease_token) is not object
            or registry_snapshot is None
            or not _runtime_request_registry_contains_identity(
                registry_snapshot,
                lease_token,
            )
            or current_inflight != len(registry_snapshot)
        ):
            _fail_runtime_generation_drift(None)
            raise RuntimeError(
                "策略运行远程请求代际无效；必须使用干净运行进程重启"
            )
        transition_valid, owner, _, _ = _runtime_transition_snapshot()
        if not transition_valid:
            _fail_runtime_generation_drift(None)
            raise RuntimeError(
                "策略运行远程请求transition状态无效；必须使用干净运行进程重启"
            )
        if owner is not None:
            raise RuntimeError(
                "策略运行模式正在切换；禁止旧请求继续远程访问: {}".format(operation)
            )
        if lease_generation != current_generation:
            _fail_runtime_generation_drift(None)
            raise RuntimeError(
                "策略运行契约已切换；禁止旧请求继续远程访问: {}".format(operation)
            )
        _assert_runtime_remote_allowed(operation)


def _create_runtime_socket_with_lease(
    address: Tuple[str, int],
    timeout: float,
    operation: str,
    lease_generation: Optional[int],
    lease_instance_token: Optional[object],
    lease_module_generation: Optional[int],
    lease_registry: Optional[Set[object]],
    lease_token: Optional[object],
    socket_handoff_state: List[Optional[Any]],
):
    """用共享socket gate线性化最终lease检查与attempt起点。"""

    anchor = _runtime_primitive_anchor_snapshot()
    if anchor is None:
        _fail_runtime_generation_drift(None)
        raise RuntimeError("策略运行socket gate无效；必须使用干净运行进程重启")
    runtime_lock = anchor[0]
    socket_condition = anchor[5]
    socket_gate_authority = anchor[6]
    socket_lock = anchor[7]
    gate_start_attempt = socket_gate_authority[3]
    gate_finish_attempt = socket_gate_authority[4]
    gate_open_transport = socket_gate_authority[5]
    attempt_token = object()
    attempt_started = False
    created_socket = None
    gate_invalid = False
    gate_cleanup_error = None
    gate_cleanup_done = False

    def finish_socket_attempt():
        """幂等收尾；外层异常边界保证首条清理字节码中断后仍会重试。"""

        nonlocal gate_invalid, gate_cleanup_error, gate_cleanup_done

        cleanup_attempts = 0
        while not gate_cleanup_done and cleanup_attempts < 3:
            cleanup_attempts += 1
            try:
                with socket_lock:
                    finish_result = gate_finish_attempt(attempt_token)
                    if (
                        type(finish_result) is not tuple
                        or len(finish_result) != 2
                        or type(finish_result[0]) is not bool
                        or type(finish_result[1]) is not int
                        or finish_result[1] < 0
                    ):
                        gate_invalid = True
                    elif attempt_started and not finish_result[0]:
                        gate_invalid = True
                    threading.Condition.notify_all(socket_condition)
                gate_cleanup_done = True
            except BaseException as exc:
                if gate_cleanup_error is None:
                    gate_cleanup_error = exc
        if not gate_cleanup_done:
            gate_cleanup_done = True
            gate_invalid = True
        if (
            gate_invalid or gate_cleanup_error is not None
        ) and created_socket is not None:
            try:
                created_socket.close()
            except BaseException:
                pass
        if gate_invalid:
            _fail_runtime_generation_drift(None)
        if gate_cleanup_error is not None:
            raise gate_cleanup_error
        if gate_invalid:
            raise RuntimeError(
                "策略运行socket gate释放状态无效；必须使用干净运行进程重启"
            )

    try:
        try:
            # 全局锁序固定为runtime -> socket gate；最外层异常边界在登记attempt前
            # 已建立，因此finally首条字节码中断也会进入幂等应急清理。
            with runtime_lock:
                with socket_lock:
                    _assert_runtime_request_lease_current(
                        operation,
                        lease_generation,
                        lease_instance_token,
                        lease_module_generation,
                        lease_registry,
                        lease_token,
                    )
                    start_result = gate_start_attempt(attempt_token)
                    if type(start_result) is not bool:
                        _fail_runtime_generation_drift(None)
                        raise RuntimeError(
                            "策略运行socket gate登记结果无效；必须使用干净运行进程重启"
                        )
                    if not start_result:
                        raise RuntimeError(
                            "策略运行helper正在重载；禁止开始新的socket attempt"
                        )
                    attempt_started = True
            current_thread = threading.get_ident()
            open_result = gate_open_transport(
                attempt_token,
                current_thread,
                socket.create_connection,
                address,
                timeout,
                socket_handoff_state,
            )
            if type(socket_handoff_state) is list and len(socket_handoff_state) == 1:
                created_socket = socket_handoff_state[0]
            if (
                type(open_result) is not bool
                or not open_result
                or created_socket is None
            ):
                _fail_runtime_generation_drift(None)
                raise RuntimeError(
                    "策略运行socket transport许可已撤销；必须使用干净运行进程重启"
                )
            # connector返回后先释放gate attempt并唤醒reload，再获取runtime锁做
            # 交付校验；否则reload持runtime等待attempt、当前线程等runtime会锁环。
            finish_socket_attempt()
            _assert_runtime_request_lease_current(
                operation,
                lease_generation,
                lease_instance_token,
                lease_module_generation,
                lease_registry,
                lease_token,
            )
            return created_socket
        finally:
            finish_socket_attempt()
    except BaseException:
        if not gate_cleanup_done:
            try:
                finish_socket_attempt()
            except BaseException:
                pass
        if created_socket is not None:
            try:
                created_socket.close()
            except BaseException:
                pass
        elif type(socket_handoff_state) is list and len(socket_handoff_state) == 1:
            handed_off_socket = socket_handoff_state[0]
            if handed_off_socket is not None:
                try:
                    handed_off_socket.close()
                except BaseException:
                    pass
        raise


def _assert_runtime_install_generation_current(
    instance_token: object,
    module_generation: int,
    expected_active_mode: str,
) -> None:
    """拒绝在安装期间发生的helper reload或状态代际漂移。"""

    if (
        not _runtime_module_generation_matches(instance_token, module_generation)
        or type(expected_active_mode) is not str
        or type(_STRATEGY_RUNTIME_ACTIVE_MODE) is not str
        or _STRATEGY_RUNTIME_ACTIVE_MODE != expected_active_mode
    ):
        raise RuntimeError("策略运行helper在安装期间发生重载或状态漂移；必须使用干净运行进程重启")


def _capture_runtime_install_lease(
    namespace: Dict[str, Any],
    mode: str,
    instance_token: object,
    module_generation: int,
) -> Tuple[Any, ...]:
    """捕获用户可执行边界前的完整安装reservation。"""

    current_thread = threading.get_ident()
    anchor = _runtime_primitive_anchor_snapshot()
    if anchor is None:
        _set_runtime_failed_process_state()
        raise RuntimeError("策略运行安装原语无效；必须使用干净运行进程重启")
    runtime_lock = anchor[0]
    socket_lock = anchor[7]
    with runtime_lock:
        with socket_lock:
            gate_state = _runtime_socket_gate_authority_snapshot(anchor)
            transition_valid, owner, transition_namespace, transition_mode = (
                _runtime_transition_snapshot()
            )
            active_mode = _STRATEGY_RUNTIME_ACTIVE_MODE
            contract_generation = _STRATEGY_RUNTIME_CONTRACT_GENERATION
            registry_snapshot = _runtime_request_registry_snapshot(
                _STRATEGY_RUNTIME_REQUEST_LEASES
            )
            if (
                not _runtime_module_generation_matches(
                    instance_token,
                    module_generation,
                )
                or type(namespace) is not dict
                or type(mode) is not str
                or mode not in {"BACKTEST", "SHADOW", "LIVE"}
                or type(current_thread) is not int
                or not transition_valid
                or owner != current_thread
                or transition_namespace is not namespace
                or transition_mode != mode
                or type(active_mode) is not str
                or active_mode
                not in {"TRANSITIONING", "BACKTEST", "SHADOW", "LIVE_BLOCKED"}
                or type(contract_generation) is not int
                or contract_generation < 1
                or type(_STRATEGY_RUNTIME_RELOAD_IN_PROGRESS) is not bool
                or _STRATEGY_RUNTIME_RELOAD_IN_PROGRESS
                or gate_state is None
                or gate_state != (False, ())
                or type(_STRATEGY_RUNTIME_INFLIGHT_REQUESTS) is not int
                or _STRATEGY_RUNTIME_INFLIGHT_REQUESTS != 0
                or registry_snapshot != ()
            ):
                _set_runtime_failed_process_state()
                raise RuntimeError(
                    "策略运行安装reservation无效；必须使用干净运行进程重启"
                )
            return (
                object(),
                instance_token,
                module_generation,
                current_thread,
                namespace,
                mode,
                contract_generation,
                active_mode,
                anchor[6],
            )


def _assert_runtime_install_lease_current(
    install_lease: Any,
    operation: str,
) -> None:
    """用户代码返回后，在任何发布前验证完整安装reservation未漂移。"""

    anchor = _runtime_primitive_anchor_snapshot()
    if type(install_lease) is not tuple or len(install_lease) != 9 or anchor is None:
        _set_runtime_failed_process_state()
        raise RuntimeError("策略运行安装lease无效；必须使用干净运行进程重启")
    (
        lease_token,
        instance_token,
        module_generation,
        owner_thread,
        namespace,
        mode,
        contract_generation,
        active_mode,
        gate_authority,
    ) = install_lease
    current_thread = threading.get_ident()
    runtime_lock = anchor[0]
    socket_lock = anchor[7]
    with runtime_lock:
        with socket_lock:
            gate_state = _runtime_socket_gate_authority_snapshot(anchor)
            transition_valid, owner, transition_namespace, transition_mode = (
                _runtime_transition_snapshot()
            )
            registry_snapshot = _runtime_request_registry_snapshot(
                _STRATEGY_RUNTIME_REQUEST_LEASES
            )
            if (
                type(lease_token) is not object
                or not _runtime_module_generation_matches(
                    instance_token,
                    module_generation,
                )
                or type(owner_thread) is not int
                or type(current_thread) is not int
                or current_thread != owner_thread
                or type(namespace) is not dict
                or type(mode) is not str
                or type(contract_generation) is not int
                or contract_generation < 1
                or type(active_mode) is not str
                or gate_authority is not anchor[6]
                or gate_state is None
                or gate_state != (False, ())
                or type(_STRATEGY_RUNTIME_RELOAD_IN_PROGRESS) is not bool
                or _STRATEGY_RUNTIME_RELOAD_IN_PROGRESS
                or type(_STRATEGY_RUNTIME_CONTRACT_GENERATION) is not int
                or _STRATEGY_RUNTIME_CONTRACT_GENERATION != contract_generation
                or type(_STRATEGY_RUNTIME_ACTIVE_MODE) is not str
                or _STRATEGY_RUNTIME_ACTIVE_MODE != active_mode
                or not transition_valid
                or owner != owner_thread
                or transition_namespace is not namespace
                or transition_mode != mode
                or type(_STRATEGY_RUNTIME_INFLIGHT_REQUESTS) is not int
                or _STRATEGY_RUNTIME_INFLIGHT_REQUESTS != 0
                or registry_snapshot != ()
            ):
                _set_runtime_failed_process_state()
                raise RuntimeError(
                    "策略运行安装lease已失效；禁止继续状态变更: {}".format(
                        operation
                    )
                )


def _runtime_module_generation_matches(
    instance_token: object,
    module_generation: int,
) -> bool:
    anchor = _runtime_primitive_anchor_snapshot()
    return (
        anchor is not None
        and type(instance_token) is object
        and anchor[2] is instance_token
        and type(module_generation) is int
        and module_generation >= 1
        and anchor[3] == module_generation
    )


def _normalise_runtime_mode(mode: Any) -> str:
    if type(mode) is not str:
        raise RuntimeError("运行模式必须是普通字符串 BACKTEST、SHADOW 或 LIVE")
    value = str.upper(str.strip(mode))
    if value not in {"BACKTEST", "SHADOW", "LIVE"}:
        raise RuntimeError("运行模式必须是 BACKTEST、SHADOW 或 LIVE")
    return value


def _normalise_run_type(context: Any) -> str:
    return str(_run_type_from_context(context) or "").strip().lower()


def _validate_runtime_identifier(value: Any, field: str) -> str:
    if type(value) is not str:
        raise RuntimeError("{} 必须是非空字符串".format(field))
    normalised = str.strip(value)
    if not normalised or normalised != value or len(normalised) > 128:
        raise RuntimeError("{} 必须是非空、无首尾空白且不超过128字符的字符串".format(field))
    if not all(str.isalnum(char) or char in "._-" for char in normalised):
        raise RuntimeError("{} 只能包含字母、数字、点、下划线和连字符".format(field))
    return normalised


def _validate_profile_module_name(value: Any) -> str:
    if type(value) is not str:
        raise RuntimeError("profile_module 必须是Python模块名")
    module_name = str.strip(value)
    if not module_name or module_name != value:
        raise RuntimeError("profile_module 必须是Python模块名")
    if not all(str.isidentifier(part) for part in str.split(module_name, ".")):
        raise RuntimeError("profile_module 必须是合法的Python模块名")
    return module_name


def _load_runtime_profile(profile_module: str, profile: str) -> Dict[str, Any]:
    """延迟加载并严格校验聚宽私有运行profile。"""

    profile_module = _validate_profile_module_name(profile_module)
    profile = _validate_runtime_identifier(profile, "profile")
    load_failed = False
    try:
        module = __import__(profile_module, fromlist=["*"])
        schema_version = getattr(module, "PROFILE_SCHEMA_VERSION", None)
        profiles = getattr(module, "PROFILES", None)
    except BaseException:
        # 配置模块导入或属性读取异常都可能包含密钥。先离开except作用域，
        # 再抛出固定边界错误，确保新异常的__context__不保留原异常对象。
        load_failed = True
    if load_failed:
        raise RuntimeError(
            "无法加载运行配置模块 {}；请确认文件已上传且配置可读取".format(
                profile_module
            )
        )

    if type(schema_version) is not int or schema_version != PROFILE_SCHEMA_VERSION:
        safe_actual_version = (
            schema_version
            if type(schema_version) is int and -1_000_000 <= schema_version <= 1_000_000
            else "<invalid>"
        )
        raise RuntimeError(
            "运行配置schema版本不匹配: expected={} actual={}".format(
                PROFILE_SCHEMA_VERSION,
                safe_actual_version,
            )
        )

    # 只接受精确内建dict，并先通过dict基类方法快照。后续校验仅对精确
    # str/int/float/bool执行，避免恶意子类的魔术方法在错误边界泄露凭据。
    if type(profiles) is not dict:
        raise RuntimeError("运行配置模块必须定义字典 PROFILES")
    profile_items = tuple(dict.items(profiles))
    if any(type(name) is not str for name, _ in profile_items):
        raise RuntimeError("运行配置 PROFILES 的profile名称必须是普通字符串")
    matching_profiles = [value for name, value in profile_items if name == profile]
    if not matching_profiles:
        raise RuntimeError("运行配置中不存在profile: {}".format(profile))
    raw = matching_profiles[0]
    if type(raw) is not dict:
        raise RuntimeError("profile {} 必须是字典".format(profile))

    raw_items = tuple(dict.items(raw))
    if any(type(key) is not str for key, _ in raw_items):
        raise RuntimeError("profile {} 包含非普通字符串字段".format(profile))
    raw_values = {key: value for key, value in raw_items}
    if any(key not in _PROFILE_ALLOWED_FIELDS for key in raw_values):
        raise RuntimeError("profile {} 包含未知字段；字段名不予回显".format(profile))
    missing = sorted(_PROFILE_REQUIRED_FIELDS - set(raw_values))
    if missing:
        raise RuntimeError("profile {} 缺少必填字段: {}".format(profile, ", ".join(missing)))

    configured_strategy_id = _validate_runtime_identifier(
        raw_values.get("strategy_id"), "profile.strategy_id"
    )
    host = raw_values.get("host")
    if (
        type(host) is not str
        or not host
        or host != str.strip(host)
        or any(str.isspace(ch) for ch in host)
    ):
        raise RuntimeError("profile.host 必须是非空且不含空白的字符串")
    if len(host) > 255:
        raise RuntimeError("profile.host 长度不能超过255字符")
    token = raw_values.get("token")
    if type(token) is not str or not token or token != str.strip(token):
        raise RuntimeError("profile.token 必须是非空且无首尾空白的字符串")

    port = raw_values.get("port", 58620)
    if type(port) is not int or not 1 <= port <= 65535:
        raise RuntimeError("profile.port 必须是1到65535之间的整数")

    retries = raw_values.get("retries", 2)
    if type(retries) is not int or not 0 <= retries <= 10:
        raise RuntimeError("profile.retries 必须是0到10之间的整数")

    numeric_rules = {
        "retry_interval": (0.5, 0.1, 30.0),
        "rpc_timeout": (DEFAULT_RPC_TIMEOUT_SECONDS, 5.0, 300.0),
        "place_order_timeout_margin": (DEFAULT_PLACE_ORDER_TIMEOUT_MARGIN_SECONDS, 0.0, 300.0),
        "default_wait_timeout": (DEFAULT_JQ_COMPAT_WAIT_TIMEOUT_SECONDS, 0.0, 300.0),
    }
    numeric_values: Dict[str, float] = {}
    for field, (default, minimum, maximum) in numeric_rules.items():
        value = raw_values.get(field, default)
        if type(value) not in (int, float):
            raise RuntimeError("profile.{} 必须是有限数值".format(field))
        if type(value) is float and not math.isfinite(value):
            raise RuntimeError("profile.{} 必须是有限数值".format(field))
        if value < minimum or value > maximum:
            raise RuntimeError(
                "profile.{} 必须在{}到{}之间".format(field, minimum, maximum)
            )
        numeric_values[field] = float(value)

    debug = raw_values.get("debug", True)
    if type(debug) is not bool:
        raise RuntimeError("profile.debug 必须是布尔值")

    optional_strings: Dict[str, Optional[str]] = {}
    for field in ("account_key", "sub_account_id", "tls_cert"):
        value = raw_values.get(field)
        if value is not None and (
            type(value) is not str or not value or value != str.strip(value)
        ):
            raise RuntimeError("profile.{} 必须是非空且无首尾空白的字符串或None".format(field))
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


def _is_runtime_mutation_name(name: str) -> bool:
    if type(name) is not str:
        return False
    return (
        name in _RUNTIME_MUTATION_NAMES
        or str.startswith(name, "order_")
        or name == "cancel"
        or str.startswith(name, "cancel_")
        or name in {"place_order", "submit_order"}
    )


def _runtime_mutation_guard(name: str, active_mode: str) -> Callable[..., Any]:
    def blocked(*args, **kwargs):
        raise RuntimeError("{}模式禁止交易变更: {}".format(active_mode, name))

    blocked.__name__ = "runtime_blocked_{}_{}".format(active_mode.lower(), name)
    setattr(blocked, "_bt_runtime_mutation_guard", True)
    return blocked


def _callable_references_runtime_mutation(
    value: Callable[..., Any],
    candidates: List[Callable[..., Any]],
) -> bool:
    """识别直接别名及常见partial/wrapped/closure形式的交易函数引用。"""

    pending: List[Callable[..., Any]] = [value]
    seen: Set[int] = set()
    while pending and len(seen) < 64:
        current = pending.pop()
        if any(current is candidate for candidate in candidates):
            return True
        current_id = id(current)
        if current_id in seen:
            continue
        seen.add(current_id)

        try:
            callable_name = None
            if type(current) is types.FunctionType:
                callable_name = current.__name__
            elif type(current) is types.MethodType:
                callable_name = current.__func__.__name__
            elif type(current) in {
                types.BuiltinFunctionType,
                types.BuiltinMethodType,
            }:
                callable_name = current.__name__
            if type(callable_name) is str and _is_runtime_mutation_name(callable_name):
                return True

            if isinstance(current, functools.partial):
                partial_type = functools.partial
                partial_func = partial_type.func.__get__(current, type(current))
                partial_args = partial_type.args.__get__(current, type(current))
                partial_keywords = partial_type.keywords.__get__(current, type(current))
                pending.append(partial_func)
                pending.extend(item for item in partial_args if callable(item))
                if partial_keywords:
                    pending.extend(
                        item
                        for item in dict.values(partial_keywords)
                        if callable(item)
                    )
            elif type(current) is types.MethodType:
                pending.append(current.__func__)
            elif type(current) is types.FunctionType:
                wrapped = dict.get(current.__dict__, "__wrapped__")
                if callable(wrapped):
                    pending.append(wrapped)
                closure = current.__closure__ or ()
                for cell in closure:
                    try:
                        cell_value = cell.cell_contents
                    except ValueError:
                        continue
                    if callable(cell_value):
                        pending.append(cell_value)
                pending.extend(
                    item for item in (current.__defaults__ or ()) if callable(item)
                )
                kwdefaults = current.__kwdefaults__ or {}
                pending.extend(
                    item for item in dict.values(kwdefaults) if callable(item)
                )
        except BaseException:
            # namespace中的未知callable元数据不可阻断基础交易guard；无法安全
            # 识别的任意callable-object间接引用不属于namespace门禁保证范围。
            continue
    return False


def _install_runtime_guards(
    namespace: Dict[str, Any],
    active_mode: str,
    extra_mutation_callables: Tuple[Callable[..., Any], ...] = (),
) -> Tuple[str, ...]:
    # 同一个原生/兼容交易函数可能被保存为trade_alias等非标准名称。
    # 先按标准mutation键收集对象identity，再把namespace内的直接别名一并替换。
    snapshot = dict.copy(namespace)
    mutation_callables = list(extra_mutation_callables)
    mutation_callables.extend(
        value
        for name, value in snapshot.items()
        if (
            type(name) is str
            and callable(value)
            and _is_runtime_mutation_name(name)
        )
    )
    names = set(_RUNTIME_MUTATION_NAMES)
    names.update(
        name
        for name, value in snapshot.items()
        if type(name) is str and callable(value) and _is_runtime_mutation_name(name)
    )
    names.update(
        name
        for name, value in snapshot.items()
        if (
            type(name) is str
            and type(value) is types.FunctionType
            and dict.get(value.__dict__, "_bt_runtime_mutation_guard") is True
        )
    )
    names.update(
        name
        for name, value in snapshot.items()
        if (
            type(name) is str
            and callable(value)
            and _callable_references_runtime_mutation(value, mutation_callables)
        )
    )
    for name in names:
        dict.__setitem__(namespace, name, _runtime_mutation_guard(name, active_mode))
    return tuple(sorted(names))


def _runtime_active_mode(mode: str) -> str:
    return "LIVE_BLOCKED" if mode == "LIVE" else mode


def _clear_runtime_clients() -> None:
    global _CLIENT, _DATA_CLIENT, _BROKER_CLIENT

    _CLIENT = None
    _DATA_CLIENT = None
    _BROKER_CLIENT = None


def _advance_runtime_contract_generation() -> None:
    """递增单调哨兵；篡改值不得通过算术魔术方法获得执行机会。"""

    global _STRATEGY_RUNTIME_CONTRACT_GENERATION

    current = _STRATEGY_RUNTIME_CONTRACT_GENERATION
    _STRATEGY_RUNTIME_CONTRACT_GENERATION = (
        current + 1 if type(current) is int and current >= 0 else 1
    )


def _clear_runtime_request_leases() -> None:
    """清空可信token；不可信元素先永久隔离，避免析构器在FAILED发布期间执行。"""

    anchored_leases = None
    quarantine_retain = None
    try:
        anchor = _get_runtime_primitive_anchor()
        if type(anchor) is tuple and len(anchor) == 10 and type(anchor[4]) is set:
            anchored_leases = anchor[4]
            quarantine_retain = anchor[8]
            _clear_or_quarantine_runtime_request_registry(
                anchored_leases,
                quarantine_retain,
            )
    except BaseException:
        pass
    visible_leases = _STRATEGY_RUNTIME_REQUEST_LEASES
    if type(visible_leases) is set and visible_leases is not anchored_leases:
        _clear_or_quarantine_runtime_request_registry(
            visible_leases,
            quarantine_retain,
        )


def _set_runtime_failed_process_state() -> None:
    """幂等发布进程期FAILED latch，不依赖任何可替换锁。"""

    global _STRATEGY_RUNTIME_ACTIVE_MODE, _STRATEGY_RUNTIME_CANONICAL_STATE
    global _STRATEGY_RUNTIME_PROCESS_SIGNATURE, _STRATEGY_RUNTIME_COMMIT_CAPSULE
    global _STRATEGY_RUNTIME_INFLIGHT_REQUESTS
    global _STRATEGY_RUNTIME_TRANSITION_OWNER
    global _STRATEGY_RUNTIME_TRANSITION_NAMESPACE
    global _STRATEGY_RUNTIME_TRANSITION_MODE
    global _STRATEGY_RUNTIME_RELOAD_IN_PROGRESS

    already_failed = False
    authoritative_reload_latched = False
    try:
        raw_anchor = _get_runtime_primitive_anchor()
        gate_state = _runtime_socket_gate_authority_snapshot(raw_anchor)
        authoritative_reload_latched = gate_state is not None and gate_state[0]
        already_failed = (
            type(_STRATEGY_RUNTIME_CONTRACT_GENERATION) is int
            and _STRATEGY_RUNTIME_CONTRACT_GENERATION >= 1
            and (
                authoritative_reload_latched
                or (
                    type(_STRATEGY_RUNTIME_ACTIVE_MODE) is str
                    and _STRATEGY_RUNTIME_ACTIVE_MODE == "FAILED"
                    and (
                        (
                            type(_STRATEGY_RUNTIME_RELOAD_IN_PROGRESS) is bool
                            and _STRATEGY_RUNTIME_RELOAD_IN_PROGRESS
                        )
                        or _get_runtime_commit_anchor()
                        is _STRATEGY_RUNTIME_FAILED_ANCHOR
                    )
                )
            )
        )
    except BaseException:
        already_failed = False
    if not already_failed:
        _advance_runtime_contract_generation()
    _STRATEGY_RUNTIME_ACTIVE_MODE = "FAILED"
    _STRATEGY_RUNTIME_PROCESS_SIGNATURE = None
    _STRATEGY_RUNTIME_CANONICAL_STATE = None
    _STRATEGY_RUNTIME_COMMIT_CAPSULE = None
    _STRATEGY_RUNTIME_INFLIGHT_REQUESTS = 0
    _STRATEGY_RUNTIME_TRANSITION_OWNER = None
    _STRATEGY_RUNTIME_TRANSITION_NAMESPACE = None
    _STRATEGY_RUNTIME_TRANSITION_MODE = None
    if authoritative_reload_latched:
        _STRATEGY_RUNTIME_RELOAD_IN_PROGRESS = True
    elif type(_STRATEGY_RUNTIME_RELOAD_IN_PROGRESS) is not bool:
        _STRATEGY_RUNTIME_RELOAD_IN_PROGRESS = False
    _clear_runtime_request_leases()
    _set_runtime_commit_anchor(_STRATEGY_RUNTIME_FAILED_ANCHOR)
    _clear_runtime_clients()


def _fail_runtime_generation_drift(namespace: Optional[Dict[str, Any]]) -> None:
    """旧调用栈跨helper reload返回时，撤销其刚发布的任何运行状态。"""

    if type(namespace) is dict:
        _mark_runtime_failed(namespace)
        return
    _set_runtime_failed_process_state()


def _quarantine_legacy_jq_compat(
    namespace: Dict[str, Any],
) -> Tuple[bool, Tuple[Callable[..., Any], ...]]:
    """移除旧兼容状态，并返回其中需要按identity封锁的mutation函数。"""

    if not dict.__contains__(namespace, _JQ_COMPAT_STATE_KEY):
        return False, ()

    state = dict.get(namespace, _JQ_COMPAT_STATE_KEY)
    mutation_callables: List[Callable[..., Any]] = []
    if isinstance(state, dict):
        originals = dict.get(state, "originals")
        if isinstance(originals, dict):
            mutation_callables.extend(
                value
                for name, value in dict.items(originals)
                if (
                    type(name) is str
                    and _is_runtime_mutation_name(name)
                    and callable(value)
                )
            )
            dict.clear(originals)
        dict.clear(state)
        dict.__setitem__(state, "quarantined", True)
    dict.pop(namespace, _JQ_COMPAT_STATE_KEY, None)
    return True, tuple(mutation_callables)


def _arm_remote_runtime_gate(
    namespace: Dict[str, Any],
    mode: str,
) -> Tuple[str, ...]:
    """在导入任何运行配置前建立进程和策略namespace双重门禁。"""

    global _STRATEGY_RUNTIME_ACTIVE_MODE, _STRATEGY_RUNTIME_CONTRACT_GENERATION

    _assert_runtime_reload_latch_open("安装{}运行模式".format(mode))
    active_mode = _runtime_active_mode(mode)
    _advance_runtime_contract_generation()
    _STRATEGY_RUNTIME_ACTIVE_MODE = "TRANSITIONING"
    _clear_runtime_clients()
    legacy_compat, legacy_callables = _quarantine_legacy_jq_compat(namespace)
    blocked_names = _install_runtime_guards(
        namespace,
        active_mode,
        legacy_callables,
    )
    _assert_no_inflight_runtime_requests("安装{}运行模式".format(mode))
    if legacy_compat:
        raise RuntimeError("检测到旧聚宽兼容层状态；必须使用干净运行进程重启")
    _STRATEGY_RUNTIME_ACTIVE_MODE = active_mode
    return blocked_names


def _mark_runtime_failed(namespace: Dict[str, Any]) -> None:
    """保持失败关闭；后续显式重试安装前，不允许复用任何交易入口。"""

    failure_namespaces = []
    try:
        commit_capsule = _get_runtime_commit_anchor()
        if type(commit_capsule) is tuple and len(commit_capsule) == 10:
            committed_namespace = commit_capsule[8]
            if type(committed_namespace) is dict:
                list.append(failure_namespaces, committed_namespace)
    except BaseException:
        pass
    if type(namespace) is dict and all(
        candidate is not namespace for candidate in failure_namespaces
    ):
        list.append(failure_namespaces, namespace)

    # 先清理capsule原namespace和当前调用namespace，再清空commit anchor。
    # 若清理被异步打断，外层重试仍能从anchor恢复原namespace identity。
    for failure_namespace in failure_namespaces:
        dict.pop(failure_namespace, _STRATEGY_RUNTIME_STATE_KEY, None)
        legacy_callables: Tuple[Callable[..., Any], ...] = ()
        try:
            _, legacy_callables = _quarantine_legacy_jq_compat(
                failure_namespace
            )
        finally:
            _install_runtime_guards(
                failure_namespace,
                "FAILED",
                legacy_callables,
            )
    _set_runtime_failed_process_state()


def _create_runtime_reload_bootstrap(
    runtime_lock,
    socket_lock,
    socket_condition,
    socket_gate_authority,
    condition_wait,
    get_commit_anchor,
    set_failed_process_state,
    mark_runtime_failed,
    thread_ident_getter,
):
    """锚定上一代原语，为误用reload提供import前的失败关闭防线。"""

    class RuntimeReloadAbort(BaseException):
        """递归reload必须终止旧栈，不能被常规except Exception吞掉。"""

    gate_snapshot = socket_gate_authority[1]
    gate_close_for_reload = socket_gate_authority[2]

    def bootstrap():
        bootstrap_error = None
        gate_closed = False
        gate_close_attempts = 0
        reload_thread = None
        socket_lock_owned_at_entry = False
        socket_lock_ownership_probe_complete = False
        try:
            candidate_thread = thread_ident_getter()
            if type(candidate_thread) is not int:
                raise RuntimeError("策略运行reload thread identity无效")
            reload_thread = candidate_thread
            if type(socket_lock) is _thread.RLock:
                candidate_owned = object.__getattribute__(
                    socket_lock,
                    "_is_owned",
                )()
                if type(candidate_owned) is not bool:
                    raise RuntimeError(
                        "策略运行reload socket lock ownership无效"
                    )
                socket_lock_owned_at_entry = candidate_owned
            socket_lock_ownership_probe_complete = True
        except BaseException as exc:
            bootstrap_error = exc

        # 第一阶段只短暂持有gate锁并关闭单向latch。不能先等待runtime锁，
        # 否则持锁安装无法观察到reload已经开始并在最终返回点失败。
        while not gate_closed:
            try:
                gate_close_attempts += 1
                with socket_lock:
                    close_result = gate_close_for_reload()
                    # closure latch已经关闭；即使返回值本身无效也不得重复等待。
                    gate_closed = True
                    if type(close_result) is not int or close_result < 0:
                        raise RuntimeError("策略运行reload gate关闭结果无效")
            except BaseException as exc:
                if bootstrap_error is None:
                    bootstrap_error = exc
                if gate_close_attempts >= 3:
                    # 捕获的函数和lock来自可信旧代anchor；连续失败时只能依靠
                    # 下方公开FAILED发布，不能把reload线程永久困在此处。
                    gate_closed = True

        # 同线程从socket-only临界区递归reload时，继续等待runtime会与另一个
        # 持runtime并等待socket的线程形成锁环。权威gate已经单向关闭；此处只做
        # 无锁FAILED发布并终止reload，外层旧调用随后必须观察closed gate退出。
        if (
            socket_lock_owned_at_entry
            or not socket_lock_ownership_probe_complete
        ):
            if bootstrap_error is None:
                bootstrap_error = RuntimeReloadAbort(
                    "策略运行reload从socket临界区递归进入；必须使用干净运行进程重启"
                )
            try:
                set_failed_process_state()
            except BaseException as exc:
                if bootstrap_error is None:
                    bootstrap_error = exc
            raise bootstrap_error

        runtime_closed = False
        while not runtime_closed:
            try:
                # 全局锁序保持runtime -> socket gate。已登记socket attempt的
                # 清理只需要gate锁，因此这里等待它们退出不会形成锁环。
                with runtime_lock:
                    try:
                        with socket_lock:
                            while True:
                                gate_state = gate_snapshot()
                                if (
                                    type(gate_state) is not tuple
                                    or len(gate_state) != 2
                                    or type(gate_state[0]) is not bool
                                    or not gate_state[0]
                                    or type(gate_state[1]) is not tuple
                                    or any(
                                        type(entry) is not tuple
                                        or len(entry) != 2
                                        or type(entry[0]) is not object
                                        or type(entry[1]) is not int
                                        for entry in gate_state[1]
                                    )
                                ):
                                    raise RuntimeError(
                                        "策略运行reload gate闭包状态无效"
                                    )
                                if not gate_state[1]:
                                    break
                                if reload_thread is None or any(
                                    entry[1] == reload_thread
                                    for entry in gate_state[1]
                                ):
                                    if bootstrap_error is None:
                                        bootstrap_error = RuntimeReloadAbort(
                                            "策略运行reload不能等待当前线程自己的socket attempt"
                                        )
                                    break
                                try:
                                    condition_wait(socket_condition)
                                except BaseException as exc:
                                    if bootstrap_error is None:
                                        bootstrap_error = exc
                                    break
                    except BaseException as exc:
                        if bootstrap_error is None:
                            bootstrap_error = exc

                    transition_namespace = globals().get(
                        "_STRATEGY_RUNTIME_TRANSITION_NAMESPACE"
                    )
                    committed_namespace = None
                    try:
                        commit_capsule = get_commit_anchor()
                        if type(commit_capsule) is tuple and len(commit_capsule) == 10:
                            candidate_namespace = commit_capsule[8]
                            if type(candidate_namespace) is dict:
                                committed_namespace = candidate_namespace
                    except BaseException as exc:
                        if bootstrap_error is None:
                            bootstrap_error = exc

                    failure_namespaces = []
                    if type(transition_namespace) is dict:
                        list.append(failure_namespaces, transition_namespace)
                    if type(committed_namespace) is dict and all(
                        value is not committed_namespace for value in failure_namespaces
                    ):
                        list.append(failure_namespaces, committed_namespace)

                    if failure_namespaces:
                        for failure_namespace in failure_namespaces:
                            try:
                                mark_runtime_failed(failure_namespace)
                            except BaseException as exc:
                                if bootstrap_error is None:
                                    bootstrap_error = exc
                    else:
                        try:
                            set_failed_process_state()
                        except BaseException as exc:
                            if bootstrap_error is None:
                                bootstrap_error = exc
                runtime_closed = True
            except BaseException as exc:
                if bootstrap_error is None:
                    bootstrap_error = exc
                # 确定性无效状态不得让bootstrap无限循环。gate已经单向关闭；
                # 这里再尝试最小FAILED发布，然后终止本阶段并传播首个异常。
                try:
                    set_failed_process_state()
                except BaseException as fail_exc:
                    if bootstrap_error is None:
                        bootstrap_error = fail_exc
                runtime_closed = True

        if bootstrap_error is not None:
            raise bootstrap_error

    return bootstrap


def _create_runtime_reload_dispatch(bootstrap):
    """防御性重试bootstrap并传播最初异常；该入口不支持进程内升级。"""

    def dispatch():
        try: bootstrap()  # noqa: E701
        except BaseException:
            try:
                bootstrap()
            except BaseException:
                pass
            raise

    return dispatch


def _context_uses_remote_snapshot(context: Any) -> bool:
    def is_remote_portfolio(value: Any) -> bool:
        value_type = type(value)
        module_basename = str(getattr(value_type, "__module__", "")).split(".")[-1]
        return (
            isinstance(value, _RemoteJQPortfolio)
            or getattr(value_type, "_bt_remote_portfolio_marker", None)
            == "bullet-trade-remote-jq-portfolio-v1"
            or (
                getattr(value_type, "__name__", "")
                in {"_RemoteJQPortfolio", "_RemoteJQSubPortfolio"}
                and module_basename == "bullet_trade_jq_remote_helper"
            )
        )

    portfolio = getattr(context, "portfolio", None)
    subportfolios = getattr(context, "subportfolios", None) or []
    return (
        is_remote_portfolio(portfolio)
        or any(is_remote_portfolio(item) for item in subportfolios)
    )


def _enforce_remote_postconditions(
    namespace: Dict[str, Any],
    context: Any,
    active_mode: str,
    install_lease: Tuple[Any, ...],
) -> Tuple[str, ...]:
    """修复远程模式保护，并清除一切可复用的远程客户端。"""

    _assert_runtime_install_lease_current(
        install_lease,
        "读取远程context前",
    )
    _assert_runtime_reload_latch_open("发布{}运行后置条件".format(active_mode))
    inherited_remote_context = _context_uses_remote_snapshot(context)
    _assert_runtime_install_lease_current(
        install_lease,
        "读取远程context后",
    )
    _assert_runtime_reload_latch_open("发布{}运行后置条件".format(active_mode))
    legacy_compat, legacy_callables = _quarantine_legacy_jq_compat(namespace)
    _clear_runtime_clients()
    blocked_names = _install_runtime_guards(
        namespace,
        active_mode,
        legacy_callables,
    )
    if legacy_compat:
        raise RuntimeError("检测到旧聚宽兼容层状态；必须使用干净运行进程重启")
    if inherited_remote_context:
        raise RuntimeError(
            "{}不能复用已被远程兼容层接管的context；请在干净运行进程中重启策略".format(
                active_mode
            )
        )
    return blocked_names


def _prepare_backtest_postconditions(
    namespace: Dict[str, Any],
    context: Any,
    install_instance_token: object,
    install_module_generation: int,
) -> Tuple[Any, ...]:
    """BACKTEST只接受从未被旧远程兼容层或client污染的干净进程。"""

    global _STRATEGY_RUNTIME_ACTIVE_MODE, _STRATEGY_RUNTIME_CONTRACT_GENERATION

    _assert_runtime_reload_latch_open("安装BACKTEST运行模式")
    _advance_runtime_contract_generation()
    _STRATEGY_RUNTIME_ACTIVE_MODE = "TRANSITIONING"
    _assert_no_inflight_runtime_requests("安装BACKTEST运行模式")
    callback_lease = _capture_runtime_install_lease(
        namespace,
        "BACKTEST",
        install_instance_token,
        install_module_generation,
    )
    inherited_remote_context = _context_uses_remote_snapshot(context)
    _assert_runtime_install_lease_current(
        callback_lease,
        "读取BACKTEST context后",
    )
    _assert_runtime_reload_latch_open("安装BACKTEST运行模式")
    if (
        dict.__contains__(namespace, _JQ_COMPAT_STATE_KEY)
        or inherited_remote_context
        or _CLIENT is not None
        or _DATA_CLIENT is not None
        or _BROKER_CLIENT is not None
    ):
        _mark_runtime_failed(namespace)
        raise RuntimeError("BACKTEST检测到旧远程运行状态；必须使用干净运行进程重启")
    _clear_runtime_clients()
    _STRATEGY_RUNTIME_ACTIVE_MODE = "BACKTEST"
    return _capture_runtime_install_lease(
        namespace,
        "BACKTEST",
        install_instance_token,
        install_module_generation,
    )


def _runtime_contract_constants_are_valid() -> bool:
    """公开版本常量被篡改时必须在任何context getter前失败。"""

    return (
        type(STRATEGY_RUNTIME_API_VERSION) is int
        and STRATEGY_RUNTIME_API_VERSION == 1
        and type(PROFILE_SCHEMA_VERSION) is int
        and PROFILE_SCHEMA_VERSION == 1
        and type(STRATEGY_RUNTIME_STATE_SCHEMA_VERSION) is int
        and STRATEGY_RUNTIME_STATE_SCHEMA_VERSION == 1
        and type(STRATEGY_RUNTIME_HELPER_MARKER) is str
        and STRATEGY_RUNTIME_HELPER_MARKER
        == "bullet-trade-joinquant-runtime-helper-v1"
    )


def _safe_runtime_state_snapshot(state: Any) -> Optional[Tuple[Any, ...]]:
    """只读取已提交状态中的普通内建值，避免篡改对象执行魔术方法。"""

    if type(state) is not dict:
        return None
    state_keys = tuple(dict.keys(state))
    if any(type(key) is not str for key in state_keys):
        return None

    base_state_keys = {
        "api_version",
        "profile_schema_version",
        "profile",
        "mode",
        "run_type",
        "strategy_id",
        "enabled",
        "orders_enabled",
        "production_ready",
        "reason",
    }

    mode = dict.get(state, "mode")
    if type(mode) is not str:
        return None
    if mode == "BACKTEST":
        expected_keys = base_state_keys
        expected_flags = (False, True, False, "backtest")
    elif mode == "SHADOW":
        expected_keys = base_state_keys | {
            "profile_module",
            "blocked_mutations",
        }
        expected_flags = (True, False, False, "shadow_read_only")
    elif mode == "LIVE":
        expected_keys = base_state_keys | {
            "profile_module",
            "blocked_mutations",
            "mirror_jq_orders",
        }
        expected_flags = (
            False,
            False,
            False,
            "live_blocked_until_strategy_ledger",
        )
    else:
        return None
    if len(state_keys) != len(expected_keys) or set(state_keys) != expected_keys:
        return None

    api_version = dict.get(state, "api_version")
    profile_schema_version = dict.get(state, "profile_schema_version")
    profile = dict.get(state, "profile")
    run_type = dict.get(state, "run_type")
    strategy_id = dict.get(state, "strategy_id")
    enabled = dict.get(state, "enabled")
    orders_enabled = dict.get(state, "orders_enabled")
    production_ready = dict.get(state, "production_ready")
    reason = dict.get(state, "reason")
    if (
        type(api_version) is not int
        or api_version != 1
        or type(profile_schema_version) is not int
        or profile_schema_version != 1
        or type(profile) is not str
        or type(run_type) is not str
        or type(strategy_id) is not str
        or type(enabled) is not bool
        or type(orders_enabled) is not bool
        or type(production_ready) is not bool
        or type(reason) is not str
        or (enabled, orders_enabled, production_ready, reason) != expected_flags
    ):
        return None

    profile_module = None
    blocked_mutations: Tuple[str, ...] = ()
    mirror_jq_orders = None
    if mode in {"SHADOW", "LIVE"}:
        profile_module = dict.get(state, "profile_module")
        blocked_mutations = dict.get(state, "blocked_mutations")
        if (
            type(profile_module) is not str
            or type(blocked_mutations) is not tuple
            or any(type(name) is not str for name in blocked_mutations)
        ):
            return None
    if mode == "LIVE":
        mirror_jq_orders = dict.get(state, "mirror_jq_orders")
        if type(mirror_jq_orders) is not bool or mirror_jq_orders is not False:
            return None

    return (
        api_version,
        profile_schema_version,
        profile,
        mode,
        run_type,
        strategy_id,
        enabled,
        orders_enabled,
        production_ready,
        reason,
        profile_module,
        blocked_mutations,
        mirror_jq_orders,
    )


def _runtime_authority_is_consistent(
    active_mode: Any,
    process_signature: Any,
    canonical_state: Any,
    runtime_record: Any,
    commit_capsule: Any,
    anchored_capsule: Any,
    contract_generation: Any,
    instance_token: Any,
    module_generation: Any,
    expected_namespace: Any,
    allow_transition_generation: bool = False,
) -> bool:
    """在读取context前验证进程权威和namespace副本是同一已提交状态。"""

    if (
        not _runtime_contract_constants_are_valid()
        or type(active_mode) is not str
        or type(process_signature) is not tuple
        or len(process_signature) != 7
        or type(canonical_state) is not dict
        or type(runtime_record) is not dict
        or type(commit_capsule) is not tuple
        or commit_capsule is not anchored_capsule
        or len(commit_capsule) != 10
        or type(contract_generation) is not int
        or contract_generation <= 0
        or type(instance_token) is not object
        or type(module_generation) is not int
        or module_generation < 1
        or type(expected_namespace) is not dict
        or type(allow_transition_generation) is not bool
    ):
        return False
    (
        commit_token,
        capsule_instance_token,
        capsule_module_generation,
        capsule_signature,
        capsule_state,
        capsule_record,
        capsule_record_state,
        capsule_generation,
        capsule_namespace,
        committed_state_snapshot,
    ) = commit_capsule
    if (
        type(commit_token) is not object
        or type(capsule_instance_token) is not object
        or capsule_instance_token is not instance_token
        or type(capsule_module_generation) is not int
        or capsule_module_generation != module_generation
        or capsule_signature is not process_signature
        or capsule_state is not canonical_state
        or capsule_record is not runtime_record
        or dict.get(runtime_record, "state") is not capsule_record_state
        or type(capsule_generation) is not int
        or capsule_generation <= 0
        or type(capsule_namespace) is not dict
        or capsule_namespace is not expected_namespace
        or dict.get(capsule_namespace, _STRATEGY_RUNTIME_STATE_KEY)
        is not capsule_record
        or contract_generation
        != capsule_generation + (1 if allow_transition_generation else 0)
    ):
        return False

    record_keys = tuple(dict.keys(runtime_record))
    expected_record_keys = {
        "schema_version",
        "runtime_instance_token",
        "signature",
        "state",
    }
    if (
        any(type(key) is not str for key in record_keys)
        or len(record_keys) != len(expected_record_keys)
        or set(record_keys) != expected_record_keys
        or type(dict.get(runtime_record, "schema_version")) is not int
        or dict.get(runtime_record, "schema_version") != 1
        or dict.get(runtime_record, "runtime_instance_token")
        is not capsule_instance_token
        or dict.get(runtime_record, "signature") is not process_signature
    ):
        return False

    (
        signature_mode,
        signature_run_type,
        signature_strategy_id,
        signature_profile,
        signature_profile_module,
        signature_api_version,
        signature_context_id,
    ) = process_signature
    if (
        type(signature_mode) is not str
        or signature_mode not in {"BACKTEST", "SHADOW", "LIVE"}
        or type(signature_run_type) is not str
        or type(signature_strategy_id) is not str
        or type(signature_profile) is not str
        or type(signature_profile_module) is not str
        or type(signature_api_version) is not int
        or signature_api_version != 1
        or type(signature_context_id) is not int
    ):
        return False

    canonical_snapshot = _safe_runtime_state_snapshot(canonical_state)
    record_snapshot = _safe_runtime_state_snapshot(dict.get(runtime_record, "state"))
    if (
        type(committed_state_snapshot) is not tuple
        or canonical_snapshot is None
        or canonical_snapshot != committed_state_snapshot
        or record_snapshot != committed_state_snapshot
    ):
        return False
    if (
        canonical_snapshot[2] != signature_profile
        or canonical_snapshot[3] != signature_mode
        or canonical_snapshot[4] != signature_run_type
        or canonical_snapshot[5] != signature_strategy_id
        or (
            signature_mode in {"SHADOW", "LIVE"}
            and canonical_snapshot[10] != signature_profile_module
        )
    ):
        return False
    expected_active_mode = (
        "LIVE_BLOCKED" if signature_mode == "LIVE" else signature_mode
    )
    return active_mode == expected_active_mode


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


def _commit_strategy_runtime_state(
    namespace: Dict[str, Any],
    signature: Tuple[Any, ...],
    state: Dict[str, Any],
) -> None:
    global _STRATEGY_RUNTIME_PROCESS_SIGNATURE, _STRATEGY_RUNTIME_CANONICAL_STATE
    global _STRATEGY_RUNTIME_COMMIT_CAPSULE

    _assert_runtime_reload_latch_open("提交策略运行状态")
    primitive_anchor = _runtime_primitive_anchor_snapshot()
    if primitive_anchor is None:
        raise RuntimeError("策略运行helper代际或同步原语无效；必须使用干净运行进程重启")
    instance_token = primitive_anchor[2]
    module_generation = primitive_anchor[3]
    canonical_state = dict(state)
    record_state = dict(state)
    runtime_record = {
        "schema_version": 1,
        "runtime_instance_token": instance_token,
        "signature": signature,
        "state": record_state,
    }
    committed_state_snapshot = _safe_runtime_state_snapshot(canonical_state)
    if (
        committed_state_snapshot is None
        or _safe_runtime_state_snapshot(record_state) != committed_state_snapshot
    ):
        raise RuntimeError("策略运行内部提交状态无效；必须使用干净运行进程重启")
    commit_capsule = (
        object(),
        instance_token,
        module_generation,
        signature,
        canonical_state,
        runtime_record,
        record_state,
        _STRATEGY_RUNTIME_CONTRACT_GENERATION,
        namespace,
        committed_state_snapshot,
    )
    _STRATEGY_RUNTIME_PROCESS_SIGNATURE = signature
    _STRATEGY_RUNTIME_CANONICAL_STATE = canonical_state
    _STRATEGY_RUNTIME_COMMIT_CAPSULE = commit_capsule
    _set_runtime_commit_anchor(commit_capsule)
    dict.__setitem__(namespace, _STRATEGY_RUNTIME_STATE_KEY, runtime_record)


def _validate_existing_strategy_runtime_state(
    existing: Any,
    signature: Tuple[Any, ...],
    expected_active_mode: str,
    expected_namespace: Dict[str, Any],
) -> Dict[str, Any]:
    if (
        not _runtime_authority_is_consistent(
            _STRATEGY_RUNTIME_ACTIVE_MODE,
            _STRATEGY_RUNTIME_PROCESS_SIGNATURE,
            _STRATEGY_RUNTIME_CANONICAL_STATE,
            existing,
            _STRATEGY_RUNTIME_COMMIT_CAPSULE,
            _get_runtime_commit_anchor(),
            _STRATEGY_RUNTIME_CONTRACT_GENERATION,
            _STRATEGY_RUNTIME_INSTANCE_TOKEN,
            _STRATEGY_RUNTIME_MODULE_GENERATION,
            expected_namespace,
            True,
        )
        or _STRATEGY_RUNTIME_PROCESS_SIGNATURE != signature
        or _STRATEGY_RUNTIME_ACTIVE_MODE != expected_active_mode
    ):
        raise RuntimeError("策略运行时缓存、进程契约或helper实例不一致；必须使用干净运行进程重启")
    return dict(_STRATEGY_RUNTIME_CANONICAL_STATE)


def _install_strategy_runtime_impl(
    namespace: Dict[str, Any],
    *,
    context: Any,
    profile: str,
    mode: str,
    strategy_id: str,
    expected_api_version: int = STRATEGY_RUNTIME_API_VERSION,
    profile_module: str = "jq_runtime_config",
    _runtime_install_lease: Tuple[Any, ...],
) -> Tuple[Dict[str, Any], Tuple[Any, ...]]:
    """安装版本化的聚宽策略运行入口。"""

    if type(namespace) is not dict:
        raise RuntimeError("namespace 必须是策略globals()字典")
    normalised_mode = _normalise_runtime_mode(mode)
    run_type = _normalise_run_type(context)
    _assert_runtime_install_lease_current(
        _runtime_install_lease,
        "读取策略运行context后",
    )
    _assert_runtime_reload_latch_open("读取策略运行context")
    current_install_lease = _runtime_install_lease
    install_instance_token = current_install_lease[1]
    install_module_generation = current_install_lease[2]
    normalised_strategy_id = _validate_runtime_identifier(strategy_id, "strategy_id")
    if type(expected_api_version) is not int or expected_api_version != STRATEGY_RUNTIME_API_VERSION:
        safe_expected_api_version = (
            expected_api_version
            if type(expected_api_version) is int
            and -1_000_000 <= expected_api_version <= 1_000_000
            else "<invalid>"
        )
        safe_actual_api_version = (
            STRATEGY_RUNTIME_API_VERSION
            if type(STRATEGY_RUNTIME_API_VERSION) is int
            and -1_000_000 <= STRATEGY_RUNTIME_API_VERSION <= 1_000_000
            else "<invalid>"
        )
        raise RuntimeError(
            "策略运行时API版本不匹配: expected={} actual={}".format(
                safe_expected_api_version,
                safe_actual_api_version,
            )
        )

    if normalised_mode == "BACKTEST":
        if run_type not in {"simple_backtest", "full_backtest"}:
            raise RuntimeError(
                "BACKTEST模式仅允许聚宽回测，当前run_type={}".format(run_type or "<empty>")
            )
        if type(profile) is not str:
            raise RuntimeError("profile 必须是普通字符串")
        if type(profile_module) is not str:
            raise RuntimeError("profile_module 必须是普通字符串")
        normalised_profile = profile
        normalised_profile_module = profile_module
        _assert_runtime_install_lease_current(
            current_install_lease,
            "准备BACKTEST运行后置条件前",
        )
        current_install_lease = _prepare_backtest_postconditions(
            namespace,
            context,
            install_instance_token,
            install_module_generation,
        )
    else:
        if run_type != "sim_trade":
            raise RuntimeError(
                "{}模式仅允许聚宽模拟盘运行，当前run_type={}".format(
                    normalised_mode,
                    run_type or "<empty>",
                )
            )
        normalised_profile = _validate_runtime_identifier(profile, "profile")
        normalised_profile_module = _validate_profile_module_name(profile_module)
        inherited_remote_context = _context_uses_remote_snapshot(context)
        _assert_runtime_install_lease_current(
            current_install_lease,
            "检查远程context后",
        )
        if inherited_remote_context:
            raise RuntimeError(
                "{}不能复用已被远程兼容层接管的context；请在干净运行进程中重启策略".format(
                    _runtime_active_mode(normalised_mode)
                )
            )

    install_signature = (
        normalised_mode,
        run_type,
        normalised_strategy_id,
        normalised_profile,
        normalised_profile_module,
        expected_api_version,
        id(context),
    )
    existing = dict.get(namespace, _STRATEGY_RUNTIME_STATE_KEY)

    if _STRATEGY_RUNTIME_PROCESS_SIGNATURE is None:
        if existing is not None or _STRATEGY_RUNTIME_CANONICAL_STATE is not None:
            raise RuntimeError("发现无进程权威状态的策略运行缓存；必须使用干净运行进程重启")
    else:
        if existing is None:
            raise RuntimeError("策略运行时进程契约存在但namespace缓存缺失；必须使用干净运行进程重启")
        state = _validate_existing_strategy_runtime_state(
            existing,
            install_signature,
            _runtime_active_mode(normalised_mode),
            namespace,
        )
        if normalised_mode != "BACKTEST":
            blocked_names = _enforce_remote_postconditions(
                namespace,
                context,
                _runtime_active_mode(normalised_mode),
                current_install_lease,
            )
            state = _build_strategy_runtime_state(
                mode=normalised_mode,
                run_type=run_type,
                strategy_id=normalised_strategy_id,
                profile=normalised_profile,
                profile_module=normalised_profile_module,
                blocked_mutations=blocked_names,
            )
        _assert_runtime_install_lease_current(
            current_install_lease,
            "提交既有策略运行状态前",
        )
        _commit_strategy_runtime_state(namespace, install_signature, state)
        return dict(state), current_install_lease

    if normalised_mode == "BACKTEST":
        state = _build_strategy_runtime_state(
            mode=normalised_mode,
            run_type=run_type,
            strategy_id=normalised_strategy_id,
            profile=normalised_profile,
        )
    else:
        config = _load_runtime_profile(normalised_profile_module, normalised_profile)
        _assert_runtime_install_lease_current(
            current_install_lease,
            "加载策略运行profile后",
        )
        _assert_runtime_reload_latch_open("加载策略运行profile")
        if config["strategy_id"] != normalised_strategy_id:
            raise RuntimeError("profile.strategy_id 与策略声明的 strategy_id 不一致")
        blocked_names = _enforce_remote_postconditions(
            namespace,
            context,
            _runtime_active_mode(normalised_mode),
            current_install_lease,
        )
        state = _build_strategy_runtime_state(
            mode=normalised_mode,
            run_type=run_type,
            strategy_id=normalised_strategy_id,
            profile=normalised_profile,
            profile_module=normalised_profile_module,
            blocked_mutations=blocked_names,
        )

    _assert_runtime_install_lease_current(
        current_install_lease,
        "提交策略运行状态前",
    )
    _commit_strategy_runtime_state(namespace, install_signature, state)
    return dict(state), current_install_lease


@_serialise_runtime_boundary
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
    """安装版本化的聚宽策略运行入口，并保证远程模式全程失败关闭。"""

    if type(namespace) is not dict:
        raise RuntimeError("namespace 必须是策略globals()字典")
    if _STRATEGY_RUNTIME_ACTIVE_MODE == "FAILED":
        _mark_runtime_failed(namespace)
        raise RuntimeError("策略运行进程状态无效：安装已经失败；必须使用干净运行进程重启")
    install_instance_token = _STRATEGY_RUNTIME_INSTANCE_TOKEN
    install_module_generation = _STRATEGY_RUNTIME_MODULE_GENERATION

    try:
        normalised_mode = _normalise_runtime_mode(mode)
        if not _runtime_module_generation_matches(
            install_instance_token,
            install_module_generation,
        ):
            raise RuntimeError("策略运行helper在安装期间发生重载或状态漂移；必须使用干净运行进程重启")
        expected_active_mode = _runtime_active_mode(normalised_mode)
        if normalised_mode == "BACKTEST" and _STRATEGY_RUNTIME_ACTIVE_MODE in {
            "SHADOW",
            "LIVE_BLOCKED",
        }:
            raise RuntimeError("远程运行模式已启动；切换BACKTEST必须使用干净运行进程")
        if normalised_mode in {"SHADOW", "LIVE"}:
            # profile是可执行Python模块；在其导入发生任何副作用前先阻断进程、
            # 缓存客户端和策略namespace中的所有交易变更入口。
            _arm_remote_runtime_gate(namespace, normalised_mode)
        _assert_no_inflight_runtime_requests(
            "安装{}运行模式".format(normalised_mode)
        )
        install_lease = _capture_runtime_install_lease(
            namespace,
            normalised_mode,
            install_instance_token,
            install_module_generation,
        )
        state, final_install_lease = _install_strategy_runtime_impl(
            namespace,
            context=context,
            profile=profile,
            mode=normalised_mode,
            strategy_id=strategy_id,
            expected_api_version=expected_api_version,
            profile_module=profile_module,
            _runtime_install_lease=install_lease,
        )
        _assert_runtime_install_lease_current(
            final_install_lease,
            "完成策略运行安装前",
        )
        _assert_runtime_install_generation_current(
            install_instance_token,
            install_module_generation,
            expected_active_mode,
        )
        return state
    except BaseException:
        _mark_runtime_failed(namespace)
        raise


def _restore_jq_compat(namespace: Dict[str, Any]) -> None:
    state = namespace.get(_JQ_COMPAT_STATE_KEY)
    if not isinstance(state, dict) or not state.get("installed"):
        return
    originals = state.get("originals") or {}
    for name, original in originals.items():
        if original is None:
            namespace.pop(name, None)
        else:
            namespace[name] = original
    state["installed"] = False


def _install_remote_context(context: Any, cache: _RemoteSnapshotCache) -> None:
    portfolio = _RemoteJQPortfolio(cache)
    subportfolio = _RemoteJQSubPortfolio(cache)
    portfolio.subportfolios = [subportfolio]
    setattr(context, "portfolio", portfolio)
    setattr(context, "subportfolios", [subportfolio])


def _extract_order_id(order_or_id: Any) -> str:
    if hasattr(order_or_id, "order_id"):
        return str(getattr(order_or_id, "order_id"))
    return str(order_or_id)


def _remote_order_result(
    order_id: str,
    security: str,
    amount: int,
    price: Optional[float],
    is_buy: bool,
) -> Optional[RemoteOrder]:
    if not order_id:
        return None
    return RemoteOrder(
        order_id=str(order_id),
        status="submitted",
        security=security,
        amount=abs(int(amount or 0)),
        price=float(price) if price is not None else None,
        is_buy=bool(is_buy),
    )


def _style_for_jq_mirror(
    style: Optional[Any],
    price: Optional[float],
    market: Optional[bool],
) -> Optional[Any]:
    if style is not None:
        return style
    if price is None:
        return None
    if market:
        return MarketOrderStyle(price)
    return LimitOrderStyle(price)


def _mirror_jq_order(
    original: Optional[Callable],
    args,
    kwargs,
) -> None:
    if not callable(original):
        return
    try:
        original(*args, **kwargs)
    except Exception as exc:
        _warn("聚宽镜像下单失败，仅影响聚宽页面展示，不影响远程真实订单: {}", exc)


@_serialise_runtime_boundary
def install_jq_compat(
    namespace: Dict[str, Any],
    *,
    context: Any,
    host: str,
    token: str,
    port: int = 58620,
    account_key: Optional[str] = None,
    sub_account_id: Optional[str] = None,
    mirror_jq_orders: bool = False,
    default_wait_timeout: float = DEFAULT_JQ_COMPAT_WAIT_TIMEOUT_SECONDS,
    tls_cert: Optional[str] = None,
    retries: int = 2,
    retry_interval: float = 0.5,
    rpc_timeout: float = DEFAULT_RPC_TIMEOUT_SECONDS,
    place_order_timeout_margin: float = DEFAULT_PLACE_ORDER_TIMEOUT_MARGIN_SECONDS,
    debug: bool = True,
    _runtime_boundary_attempt_state: Optional[List[bool]] = None,
) -> Dict[str, Any]:
    """安装聚宽模拟盘完全接管兼容层。

    回测环境不接管；仅在 `context.run_params.type == "sim_trade"` 时接管
    `context.portfolio`、`context.subportfolios` 和聚宽同名交易函数。
    """

    if type(namespace) is not dict:
        raise RuntimeError("namespace 必须是策略globals()字典")
    _assert_runtime_mutation_allowed(
        "install_jq_compat",
        threading.get_ident(),
    )
    _assert_no_inflight_runtime_requests("install_jq_compat")

    if (
        type(_runtime_boundary_attempt_state) is not list
        or len(_runtime_boundary_attempt_state) != 1
    ):
        _fail_runtime_generation_drift(namespace)
        raise RuntimeError(
            "聚宽兼容层attempt状态无效；必须使用干净运行进程重启"
    )
    _runtime_boundary_attempt_state[0] = True
    run_type = _run_type_from_context(context)
    state = namespace.get(_JQ_COMPAT_STATE_KEY)
    if not isinstance(state, dict):
        state = {
            "originals": {name: namespace.get(name) for name in _JQ_COMPAT_FUNCTIONS},
            "installed": False,
        }
        namespace[_JQ_COMPAT_STATE_KEY] = state

    if run_type in ("simple_backtest", "full_backtest"):
        _restore_jq_compat(namespace)
        _log("INFO", "聚宽兼容层检测到回测环境 {}，不接管远程交易", run_type)
        return {"enabled": False, "run_type": run_type, "reason": "backtest"}

    if run_type != "sim_trade":
        _restore_jq_compat(namespace)
        _warn("聚宽兼容层未识别运行环境 run_params.type={}，默认不接管远程交易", run_type)
        return {"enabled": False, "run_type": run_type, "reason": "unsupported_run_type"}

    _configure_remote_clients(
        host,
        token,
        port,
        account_key,
        sub_account_id,
        tls_cert,
        retries,
        retry_interval,
        rpc_timeout,
        place_order_timeout_margin,
        debug,
    )
    broker = get_broker_client()
    cache = _RemoteSnapshotCache(broker)
    _install_remote_context(context, cache)
    originals = state.get("originals") or {}

    def compat_order(
        security: str,
        amount: int,
        style: Optional[Any] = None,
        side: str = "long",
        pindex: int = 0,
        close_today: bool = False,
        **kwargs,
    ) -> Optional[RemoteOrder]:
        _validate_jq_trade_scope(side=side, pindex=pindex, close_today=close_today)
        price = kwargs.pop("price", None)
        wait_timeout = _coerce_wait_timeout(kwargs.pop("wait_timeout", None), default_wait_timeout)
        market = kwargs.pop("market", None)
        remark = kwargs.pop("remark", None)
        order_remark = kwargs.pop("order_remark", None)
        idempotency_key = kwargs.pop("idempotency_key", None)
        price, market = _resolve_price_market(price=price, style=style, market=market)
        order_id = broker.order(
            security,
            amount,
            price=price,
            side=_normalise_side(side, amount),
            wait_timeout=wait_timeout,
            market=market,
            remark=remark,
            order_remark=order_remark,
            idempotency_key=idempotency_key,
        )
        cache.invalidate()
        if mirror_jq_orders and order_id:
            mirror_style = _style_for_jq_mirror(style, price, market)
            _mirror_jq_order(
                originals.get("order"),
                (security, amount, mirror_style),
                {"side": side, "pindex": pindex, "close_today": close_today},
            )
        return _remote_order_result(order_id, security, amount, price, amount > 0)

    def compat_order_value(
        security: str,
        value: float,
        style: Optional[Any] = None,
        side: str = "long",
        pindex: int = 0,
        close_today: bool = False,
        **kwargs,
    ) -> Optional[RemoteOrder]:
        _validate_jq_trade_scope(side=side, pindex=pindex, close_today=close_today)
        price = kwargs.pop("price", None)
        wait_timeout = _coerce_wait_timeout(kwargs.pop("wait_timeout", None), default_wait_timeout)
        market = kwargs.pop("market", None)
        price, market = _resolve_price_market(price=price, style=style, market=market)
        order_id = broker.order_value(
            security,
            value,
            price=price,
            wait_timeout=wait_timeout,
            style=style,
            side=side,
            pindex=pindex,
            close_today=close_today,
            market=market,
            remark=kwargs.pop("remark", None),
            order_remark=kwargs.pop("order_remark", None),
            idempotency_key=kwargs.pop("idempotency_key", None),
        )
        cache.invalidate()
        if mirror_jq_orders and order_id:
            mirror_style = _style_for_jq_mirror(style, price, market)
            _mirror_jq_order(
                originals.get("order_value"),
                (security, value, mirror_style),
                {"side": side, "pindex": pindex, "close_today": close_today},
            )
        return _remote_order_result(order_id, security, int(value), price, value > 0)

    def compat_order_percent(
        security: str,
        percent: float,
        style: Optional[Any] = None,
        side: str = "long",
        pindex: int = 0,
        close_today: bool = False,
        **kwargs,
    ) -> Optional[RemoteOrder]:
        _validate_jq_trade_scope(side=side, pindex=pindex, close_today=close_today)
        value = float(cache.snapshot()["account"].total_value) * float(percent)
        price = kwargs.pop("price", None)
        wait_timeout = _coerce_wait_timeout(kwargs.pop("wait_timeout", None), default_wait_timeout)
        market = kwargs.pop("market", None)
        price, market = _resolve_price_market(price=price, style=style, market=market)
        order_id = broker.order_value(
            security,
            value,
            price=price,
            wait_timeout=wait_timeout,
            style=style,
            side=side,
            pindex=pindex,
            close_today=close_today,
            market=market,
            remark=kwargs.pop("remark", None),
            order_remark=kwargs.pop("order_remark", None),
            idempotency_key=kwargs.pop("idempotency_key", None),
        )
        cache.invalidate()
        if mirror_jq_orders and order_id:
            mirror_style = _style_for_jq_mirror(style, price, market)
            _mirror_jq_order(
                originals.get("order_percent"),
                (security, percent, mirror_style),
                {"side": side, "pindex": pindex, "close_today": close_today},
            )
        return _remote_order_result(order_id, security, int(value), price, value > 0)

    def compat_order_target(
        security: str,
        amount: int,
        style: Optional[Any] = None,
        side: str = "long",
        pindex: int = 0,
        close_today: bool = False,
        **kwargs,
    ) -> Optional[RemoteOrder]:
        _validate_jq_trade_scope(side=side, pindex=pindex, close_today=close_today)
        price = kwargs.pop("price", None)
        wait_timeout = _coerce_wait_timeout(kwargs.pop("wait_timeout", None), default_wait_timeout)
        market = kwargs.pop("market", None)
        price, market = _resolve_price_market(price=price, style=style, market=market)
        current = broker._current_amount(security)
        order_id = broker.order_target(
            security,
            amount,
            price=price,
            wait_timeout=wait_timeout,
            style=style,
            side=side,
            pindex=pindex,
            close_today=close_today,
            market=market,
            remark=kwargs.pop("remark", None),
            order_remark=kwargs.pop("order_remark", None),
            idempotency_key=kwargs.pop("idempotency_key", None),
        )
        cache.invalidate()
        if mirror_jq_orders and order_id:
            mirror_style = _style_for_jq_mirror(style, price, market)
            _mirror_jq_order(
                originals.get("order_target"),
                (security, amount, mirror_style),
                {"side": side, "pindex": pindex, "close_today": close_today},
            )
        return _remote_order_result(order_id, security, amount - current, price, amount >= current)

    def compat_order_target_value(
        security: str,
        value: Optional[float] = None,
        style: Optional[Any] = None,
        side: str = "long",
        pindex: int = 0,
        close_today: bool = False,
        **kwargs,
    ) -> Optional[RemoteOrder]:
        _validate_jq_trade_scope(side=side, pindex=pindex, close_today=close_today)
        has_target_value = "target_value" in kwargs
        target_value = kwargs.pop("target_value", value)
        if has_target_value and value is not None:
            raise TypeError("order_target_value() got both 'value' and 'target_value'")
        if target_value is None:
            raise TypeError("order_target_value() missing required argument: 'value'")
        current_value = float(cache.snapshot()["positions"][security].value)
        price = kwargs.pop("price", None)
        wait_timeout = _coerce_wait_timeout(kwargs.pop("wait_timeout", None), default_wait_timeout)
        market = kwargs.pop("market", None)
        price, market = _resolve_price_market(price=price, style=style, market=market)
        order_id = broker.order_target_value(
            security,
            target_value,
            price=price,
            wait_timeout=wait_timeout,
            style=style,
            side=side,
            pindex=pindex,
            close_today=close_today,
            market=market,
            remark=kwargs.pop("remark", None),
            order_remark=kwargs.pop("order_remark", None),
            idempotency_key=kwargs.pop("idempotency_key", None),
        )
        cache.invalidate()
        if mirror_jq_orders and order_id:
            mirror_style = _style_for_jq_mirror(style, price, market)
            _mirror_jq_order(
                originals.get("order_target_value"),
                (security, target_value, mirror_style),
                {"side": side, "pindex": pindex, "close_today": close_today},
            )
        return _remote_order_result(
            order_id,
            security,
            int(float(target_value) - current_value),
            price,
            float(target_value) >= current_value,
        )

    def compat_order_target_percent(
        security: str,
        percent: float,
        style: Optional[Any] = None,
        side: str = "long",
        pindex: int = 0,
        close_today: bool = False,
        **kwargs,
    ) -> Optional[RemoteOrder]:
        _validate_jq_trade_scope(side=side, pindex=pindex, close_today=close_today)
        snapshot = cache.snapshot()
        target_value = float(snapshot["account"].total_value) * float(percent)
        current_value = float(snapshot["positions"][security].value)
        price = kwargs.pop("price", None)
        wait_timeout = _coerce_wait_timeout(kwargs.pop("wait_timeout", None), default_wait_timeout)
        market = kwargs.pop("market", None)
        price, market = _resolve_price_market(price=price, style=style, market=market)
        order_id = broker.order_target_value(
            security,
            target_value,
            price=price,
            wait_timeout=wait_timeout,
            style=style,
            side=side,
            pindex=pindex,
            close_today=close_today,
            market=market,
            remark=kwargs.pop("remark", None),
            order_remark=kwargs.pop("order_remark", None),
            idempotency_key=kwargs.pop("idempotency_key", None),
        )
        cache.invalidate()
        if mirror_jq_orders and order_id:
            mirror_style = _style_for_jq_mirror(style, price, market)
            _mirror_jq_order(
                originals.get("order_target_percent"),
                (security, percent, mirror_style),
                {"side": side, "pindex": pindex, "close_today": close_today},
            )
        return _remote_order_result(
            order_id,
            security,
            int(target_value - current_value),
            price,
            target_value >= current_value,
        )

    def compat_cancel_order(order_or_id: Any) -> Dict[str, Any]:
        result = broker.cancel_order(_extract_order_id(order_or_id))
        cache.invalidate()
        return result

    namespace.update(
        {
            "order": compat_order,
            "order_value": compat_order_value,
            "order_percent": compat_order_percent,
            "order_target": compat_order_target,
            "order_target_value": compat_order_target_value,
            "order_target_percent": compat_order_target_percent,
            "cancel_order": compat_cancel_order,
            "get_open_orders": lambda: broker.get_open_orders(),
            "get_orders": broker.get_orders,
            "get_trades": broker.get_trades,
        }
    )
    state.update(
        {
            "installed": True,
            "run_type": run_type,
            "cache": cache,
            "context": context,
            "mirror_jq_orders": bool(mirror_jq_orders),
            "default_wait_timeout": float(default_wait_timeout),
        }
    )
    _log("INFO", "聚宽模拟盘完全接管已启用: account_key={}, sub_account_id={}", account_key, sub_account_id)
    return {"enabled": True, "run_type": run_type}


# --------- 便捷函数（JQ 兼容） ----------
def order(
    security: str,
    amount: int,
    price: Optional[float] = None,
    side: Optional[str] = None,
    wait_timeout: float = 0,
    *,
    style: Optional[Any] = None,
    market: Optional[bool] = None,
    remark: Optional[str] = None,
    order_remark: Optional[str] = None,
    idempotency_key: Optional[str] = None,
) -> str:
    _assert_runtime_mutation_allowed("order")
    return get_broker_client().order(
        security,
        amount,
        price=price,
        side=side,
        wait_timeout=wait_timeout,
        style=style,
        market=market,
        remark=remark,
        order_remark=order_remark,
        idempotency_key=idempotency_key,
    )


def order_value(
    security: str,
    value: float,
    price: Optional[float] = None,
    wait_timeout: float = 0,
    *,
    style: Optional[Any] = None,
    side: Optional[str] = None,
    pindex: int = 0,
    close_today: bool = False,
    market: Optional[bool] = None,
    remark: Optional[str] = None,
    order_remark: Optional[str] = None,
    idempotency_key: Optional[str] = None,
) -> str:
    _assert_runtime_mutation_allowed("order_value")
    return get_broker_client().order_value(
        security,
        value,
        price=price,
        wait_timeout=wait_timeout,
        style=style,
        side=side,
        pindex=pindex,
        close_today=close_today,
        market=market,
        remark=remark,
        order_remark=order_remark,
        idempotency_key=idempotency_key,
    )


def order_percent(
    security: str,
    percent: float,
    price: Optional[float] = None,
    wait_timeout: float = 0,
    *,
    style: Optional[Any] = None,
    side: Optional[str] = None,
    pindex: int = 0,
    close_today: bool = False,
    market: Optional[bool] = None,
    remark: Optional[str] = None,
    order_remark: Optional[str] = None,
    idempotency_key: Optional[str] = None,
) -> str:
    _assert_runtime_mutation_allowed("order_percent")
    return get_broker_client().order_percent(
        security,
        percent,
        price=price,
        wait_timeout=wait_timeout,
        style=style,
        side=side,
        pindex=pindex,
        close_today=close_today,
        market=market,
        remark=remark,
        order_remark=order_remark,
        idempotency_key=idempotency_key,
    )


def order_target(
    security: str,
    target: int,
    price: Optional[float] = None,
    wait_timeout: float = 0,
    *,
    style: Optional[Any] = None,
    side: Optional[str] = None,
    pindex: int = 0,
    close_today: bool = False,
    market: Optional[bool] = None,
    remark: Optional[str] = None,
    order_remark: Optional[str] = None,
    idempotency_key: Optional[str] = None,
) -> str:
    _assert_runtime_mutation_allowed("order_target")
    return get_broker_client().order_target(
        security,
        target,
        price=price,
        wait_timeout=wait_timeout,
        style=style,
        side=side,
        pindex=pindex,
        close_today=close_today,
        market=market,
        remark=remark,
        order_remark=order_remark,
        idempotency_key=idempotency_key,
    )


def order_target_value(
    security: str,
    target_value: Optional[float] = None,
    price: Optional[float] = None,
    wait_timeout: float = 0,
    *,
    value: Optional[float] = None,
    style: Optional[Any] = None,
    side: Optional[str] = None,
    pindex: int = 0,
    close_today: bool = False,
    market: Optional[bool] = None,
    remark: Optional[str] = None,
    order_remark: Optional[str] = None,
    idempotency_key: Optional[str] = None,
) -> str:
    _assert_runtime_mutation_allowed("order_target_value")
    return get_broker_client().order_target_value(
        security,
        target_value,
        price=price,
        wait_timeout=wait_timeout,
        value=value,
        style=style,
        side=side,
        pindex=pindex,
        close_today=close_today,
        market=market,
        remark=remark,
        order_remark=order_remark,
        idempotency_key=idempotency_key,
    )


def order_target_percent(
    security: str,
    percent: float,
    price: Optional[float] = None,
    wait_timeout: float = 0,
    *,
    style: Optional[Any] = None,
    side: Optional[str] = None,
    pindex: int = 0,
    close_today: bool = False,
    market: Optional[bool] = None,
    remark: Optional[str] = None,
    order_remark: Optional[str] = None,
    idempotency_key: Optional[str] = None,
) -> str:
    _assert_runtime_mutation_allowed("order_target_percent")
    return get_broker_client().order_target_percent(
        security,
        percent,
        price=price,
        wait_timeout=wait_timeout,
        style=style,
        side=side,
        pindex=pindex,
        close_today=close_today,
        market=market,
        remark=remark,
        order_remark=order_remark,
        idempotency_key=idempotency_key,
    )


def cancel_order(order_id: str) -> Dict[str, Any]:
    _assert_runtime_mutation_allowed("cancel_order")
    return get_broker_client().cancel_order(order_id)


def get_order_status(order_id: str) -> Dict[str, Any]:
    return get_broker_client().get_order_status(order_id)


def get_open_orders() -> Dict[str, RemoteOrder]:
    return get_broker_client().get_open_orders()


def get_orders(
    order_id: Optional[str] = None,
    security: Optional[str] = None,
    status: Optional[object] = None,
    from_broker: bool = False,
) -> Dict[str, RemoteOrder]:
    return get_broker_client().get_orders(
        order_id=order_id,
        security=security,
        status=status,
        from_broker=from_broker,
    )


def get_trades(
    order_id: Optional[str] = None,
    security: Optional[str] = None,
) -> Dict[str, RemoteTrade]:
    return get_broker_client().get_trades(order_id=order_id, security=security)


def get_account() -> RemoteAccount:
    return get_broker_client().get_account()


def get_positions() -> List[RemotePosition]:
    return get_broker_client().get_positions()


# 封存reload误用检测入口。闭包直接锚定本代锁、gate和清理函数；生产更新
# 必须冷重启，不能把该防线当作进程内热更新协议。
_runtime_reload_bootstrap_impl = _create_runtime_reload_bootstrap(
    _STRATEGY_RUNTIME_LOCK,
    _STRATEGY_RUNTIME_SOCKET_LOCK,
    _STRATEGY_RUNTIME_SOCKET_CONDITION,
    _STRATEGY_RUNTIME_SOCKET_GATE_AUTHORITY,
    threading.Condition.wait,
    _get_runtime_commit_anchor,
    _set_runtime_failed_process_state,
    _mark_runtime_failed,
    threading.get_ident,
)
_run_runtime_reload_bootstrap = _create_runtime_reload_dispatch(
    _runtime_reload_bootstrap_impl
)
_runtime_reload_entry_publish_result = _STRATEGY_RUNTIME_RELOAD_ENTRY_AUTHORITY[2](
    _run_runtime_reload_bootstrap
)
if (
    type(_runtime_reload_entry_publish_result) is not bool
    or not _runtime_reload_entry_publish_result
):
    _set_runtime_failed_process_state()
    raise RuntimeError(
        "策略运行helper无法封存reload入口；必须使用干净运行进程重启"
    )


__all__ = [
    "STRATEGY_RUNTIME_API_VERSION",
    "STRATEGY_RUNTIME_HELPER_MARKER",
    "PROFILE_SCHEMA_VERSION",
    "configure",
    "install_strategy_runtime",
    "install_jq_compat",
    "get_data_client",
    "get_broker_client",
    "order",
    "order_value",
    "order_percent",
    "order_target",
    "order_target_value",
    "order_target_percent",
    "cancel_order",
    "get_order_status",
    "get_open_orders",
    "get_orders",
    "get_trades",
    "get_account",
    "get_positions",
    "MarketOrderStyle",
    "LimitOrderStyle",
    "RemoteAccount",
    "RemoteOrder",
    "RemoteTrade",
    "RemotePosition",
    "RemoteDataClient",
    "RemoteBrokerClient",
]
