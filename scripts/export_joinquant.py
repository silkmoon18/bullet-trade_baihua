"""Validate and export deterministic JoinQuant upload artifacts."""

from __future__ import print_function

import argparse
import ast
import hashlib
import json
import os
import re
import shutil
import stat
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Set


MANIFEST_SCHEMA_VERSION = 1
ARTIFACT_KIND = "bullet-trade-joinquant-upload"


class ValidationError(RuntimeError):
    """Raised when a source file is unsafe or violates the export contract."""


@dataclass(frozen=True)
class ArtifactSpec:
    role: str
    source: str
    output: str
    upload_name: str
    requires_private_values: bool = False


ARTIFACTS: Sequence[ArtifactSpec] = (
    ArtifactSpec(
        role="strategy",
        source="strategies/joinquant/good_etf.py",
        output="good_etf.py",
        upload_name="good_etf.py",
    ),
    ArtifactSpec(
        role="helper",
        source="helpers/bullet_trade_jq_remote_helper.py",
        output="bullet_trade_jq_remote_helper.py",
        upload_name="bullet_trade_jq_remote_helper.py",
    ),
    ArtifactSpec(
        role="profile-template",
        source="jq_runtime/jq_runtime_config.example.py",
        output="jq_runtime_config.example.py",
        upload_name="jq_runtime_config.py",
        requires_private_values=True,
    ),
)


_ALLOWED_IMPORT_ROOTS: Mapping[str, Set[str]] = {
    "strategy": {
        "datetime",
        "sys",
        "types",
        "typing",
        "jqdata",
        "joinquant_typing",
        "bullet_trade_jq_remote_helper",
    },
    "helper": {
        "_thread",
        "ast",
        "functools",
        "hashlib",
        "json",
        "math",
        "os",
        "pandas",
        "socket",
        "ssl",
        "struct",
        "sys",
        "threading",
        "time",
        "traceback",
        "types",
        "typing",
    },
    "profile-template": set(),
}

_STRATEGY_DANGEROUS_ROOTS = {
    "aiohttp",
    "ftplib",
    "http",
    "multiprocessing",
    "os",
    "pathlib",
    "requests",
    "shutil",
    "socket",
    "ssl",
    "subprocess",
    "tempfile",
    "urllib",
}

_DANGEROUS_CALL_NAMES = {
    "__import__",
    "breakpoint",
    "compile",
    "eval",
    "exec",
    "input",
    "open",
    "delattr",
    "setattr",
}

_SENSITIVE_POSITIONAL_CALLS: Mapping[str, Mapping[int, str]] = {
    "configure": {0: "host", 1: "token"},
}

_SENSITIVE_NAMES = {
    "account_key",
    "api_key",
    "auth_token",
    "bearer_token",
    "credential",
    "credentials",
    "host",
    "password",
    "secret",
    "sub_account_id",
    "tls_cert",
    "token",
    "webhook",
    "webhook_url",
}

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

_SENSITIVE_NAME_SUFFIXES = (
    "_account_key",
    "_api_key",
    "_credential",
    "_credentials",
    "_host",
    "_password",
    "_secret",
    "_token",
    "_webhook",
    "_webhook_url",
)

_CONTRACT_FIELDS: Mapping[str, Set[str]] = {
    "strategy": {
        "MODE",
        "PROFILE",
        "STRATEGY_ID",
        "_EXPECTED_RUNTIME_API_VERSION",
        "_EXPECTED_RUNTIME_HELPER_MARKER",
        "_EXPECTED_PROFILE_SCHEMA_VERSION",
        "_EXPECTED_RUNTIME_PROFILE_MODULE",
    },
    "helper": {
        "STRATEGY_RUNTIME_API_VERSION",
        "STRATEGY_RUNTIME_HELPER_MARKER",
        "PROFILE_SCHEMA_VERSION",
    },
    "profile-template": {"PROFILE_SCHEMA_VERSION", "PROFILES"},
}

_NAMESPACE_MUTATOR_NAMES = {
    "__delitem__",
    "__setitem__",
    "clear",
    "pop",
    "popitem",
    "setdefault",
    "update",
}

_SAFE_PLACEHOLDERS = {
    "",
    "<replace-me>",
    "replace-me",
    "your-host",
    "your-token",
    "你的ip",
    "你的token",
}

_FORBIDDEN_TEXT_PATTERNS = (
    re.compile(r"https://open\.feishu\.cn/open-apis/bot/v2/hook/", re.I),
    re.compile(r"https://hooks\.slack\.com/services/", re.I),
    re.compile(r"https://discord(?:app)?\.com/api/webhooks/", re.I),
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{16,}", re.I),
)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _module_root(name: str) -> str:
    return name.split(".", 1)[0]


def _static_callable_name(node: ast.AST) -> Optional[str]:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    if not isinstance(node, ast.Call):
        return None
    if (
        isinstance(node.func, ast.Name)
        and node.func.id == "getattr"
        and len(node.args) >= 2
    ):
        name = _static_string(node.args[1])
        return name if isinstance(name, str) else None
    if (
        isinstance(node.func, ast.Attribute)
        and node.func.attr == "__getattribute__"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id in {"object", "type"}
        and len(node.args) >= 2
    ):
        name = _static_string(node.args[1])
        return name if isinstance(name, str) else None
    return None


def _static_callable_owner(node: ast.AST) -> Optional[ast.AST]:
    if isinstance(node, ast.Attribute):
        return node.value
    if not isinstance(node, ast.Call):
        return None
    if (
        isinstance(node.func, ast.Name)
        and node.func.id == "getattr"
        and len(node.args) >= 2
    ):
        return node.args[0]
    if (
        isinstance(node.func, ast.Attribute)
        and node.func.attr == "__getattribute__"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id in {"object", "type"}
        and len(node.args) >= 2
    ):
        return node.args[0]
    return None


def _dynamic_import_target(node: ast.Call) -> Optional[ast.AST]:
    if any(keyword.arg is None for keyword in node.keywords):
        return None
    named_targets = [
        keyword.value for keyword in node.keywords if keyword.arg == "name"
    ]
    if node.args:
        return None if named_targets else node.args[0]
    if len(named_targets) == 1:
        return named_targets[0]
    return None


def _iter_imports(
    node: ast.AST,
    type_checking_guarded: bool = False,
) -> Iterable[tuple]:
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        yield node, type_checking_guarded
        return
    if isinstance(node, ast.If):
        guarded_body = (
            isinstance(node.test, ast.Name) and node.test.id == "TYPE_CHECKING"
        )
        for child in node.body:
            yield from _iter_imports(
                child, type_checking_guarded or guarded_body
            )
        for child in node.orelse:
            yield from _iter_imports(child, type_checking_guarded)
        return
    for child in ast.iter_child_nodes(node):
        yield from _iter_imports(child, type_checking_guarded)


def _validate_type_checking_binding(tree: ast.Module, source_name: str) -> None:
    exact_imports = 0
    top_level_nodes = {id(node) for node in tree.body}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.level:
                raise ValidationError(
                    "{} uses a relative import".format(source_name)
                )
            if node.module == "typing":
                for alias in node.names:
                    if alias.name == "*":
                        raise ValidationError(
                            "{} uses a wildcard typing import".format(source_name)
                        )
                    if alias.name == "TYPE_CHECKING" and alias.asname in {
                        None,
                        "TYPE_CHECKING",
                    }:
                        if id(node) not in top_level_nodes:
                            raise ValidationError(
                                "{} must import TYPE_CHECKING at module top level".format(
                                    source_name
                                )
                            )
                        exact_imports += 1
                    elif alias.asname == "TYPE_CHECKING":
                        raise ValidationError(
                            "{} binds TYPE_CHECKING from an invalid source".format(
                                source_name
                            )
                        )
            elif any(
                (alias.asname or alias.name) == "TYPE_CHECKING"
                for alias in node.names
            ):
                raise ValidationError(
                    "{} binds TYPE_CHECKING from an invalid source".format(
                        source_name
                    )
                )
        elif isinstance(node, ast.Import):
            if any(
                (alias.asname or _module_root(alias.name)) == "TYPE_CHECKING"
                for alias in node.names
            ):
                raise ValidationError(
                    "{} binds TYPE_CHECKING from an invalid source".format(
                        source_name
                    )
                )
        elif isinstance(node, ast.Name):
            if node.id == "TYPE_CHECKING" and isinstance(
                node.ctx, (ast.Store, ast.Del)
            ):
                raise ValidationError(
                    "{} rebinds TYPE_CHECKING".format(source_name)
                )
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if node.name == "TYPE_CHECKING":
                raise ValidationError(
                    "{} rebinds TYPE_CHECKING".format(source_name)
                )
        elif isinstance(node, ast.arg) and node.arg == "TYPE_CHECKING":
            raise ValidationError(
                "{} shadows TYPE_CHECKING".format(source_name)
            )
        elif isinstance(node, ast.ExceptHandler) and node.name == "TYPE_CHECKING":
            raise ValidationError(
                "{} rebinds TYPE_CHECKING".format(source_name)
            )
    if exact_imports != 1:
        raise ValidationError(
            "{} must import TYPE_CHECKING exactly once from typing".format(
                source_name
            )
        )


def _import_roots(node: ast.AST) -> Iterable[str]:
    if isinstance(node, ast.Import):
        for alias in node.names:
            yield _module_root(alias.name)
    elif isinstance(node, ast.ImportFrom) and node.module:
        yield _module_root(node.module)


def _validate_imports(role: str, tree: ast.Module, source_name: str) -> None:
    if role == "strategy":
        _validate_type_checking_binding(tree, source_name)
    allowed = _ALLOWED_IMPORT_ROOTS[role]
    found_joinquant_typing = False
    for node, type_checking_guarded in _iter_imports(tree):
        if isinstance(node, ast.ImportFrom) and node.level:
            raise ValidationError("{} uses a relative import".format(source_name))
        for root in _import_roots(node):
            if root == "bullet_trade":
                raise ValidationError(
                    "{} imports server package bullet_trade".format(source_name)
                )
            if root not in allowed:
                raise ValidationError(
                    "{} has forbidden import for {}: {}".format(
                        source_name, role, root
                    )
                )
            if role in {"strategy", "profile-template"} and root in _STRATEGY_DANGEROUS_ROOTS:
                raise ValidationError(
                    "{} imports dangerous module: {}".format(source_name, root)
                )
            if role == "strategy" and root == "joinquant_typing":
                found_joinquant_typing = True
                if not type_checking_guarded:
                    raise ValidationError(
                        "{} imports joinquant_typing at runtime".format(source_name)
                    )
    if role == "strategy" and not found_joinquant_typing:
        raise ValidationError(
            "{} must import joinquant_typing only below TYPE_CHECKING".format(
                source_name
            )
        )


def _validate_calls(role: str, tree: ast.Module, source_name: str) -> None:
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "vars"
            and node.args
        ):
            raise ValidationError(
                "{} obtains an object namespace with vars".format(source_name)
            )
        if (
            isinstance(node, ast.Name)
            and isinstance(node.ctx, ast.Load)
            and node.id == "__builtins__"
        ):
            raise ValidationError(
                "{} accesses forbidden builtins namespace".format(source_name)
            )
        if (
            isinstance(node, ast.Subscript)
            and _is_dynamic_namespace_call(node.value)
        ):
            raise ValidationError(
                "{} directly indexes a dynamic namespace".format(source_name)
            )
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
        ):
            namespace = node.func.value
            key_index = 0
            if (
                isinstance(namespace, ast.Name)
                and namespace.id == "dict"
                and node.args
            ):
                namespace = node.args[0]
                key_index = 1
            if (
                _is_dynamic_namespace_call(namespace)
                and len(node.args) > key_index
                and _static_string(node.args[key_index])
                in {"__builtins__", "__import__"}
            ):
                raise ValidationError(
                    "{} accesses a forbidden dynamic namespace key".format(
                        source_name
                    )
                )
        if (
            isinstance(node, ast.Call)
            and _static_callable_name(node.func) == "__getitem__"
        ):
            namespace_arguments = [
                argument
                for argument in [
                    _static_callable_owner(node.func),
                    *node.args,
                ]
                if argument is not None
            ]
            if any(
                _is_dynamic_namespace_call(argument)
                for argument in namespace_arguments
            ) and any(
                _static_string(argument) in {"__builtins__", "__import__"}
                for argument in node.args
            ):
                raise ValidationError(
                    "{} accesses a forbidden dynamic namespace key".format(
                        source_name
                    )
                )
        if _is_sys_modules_lookup(node):
            raise ValidationError(
                "{} indexes forbidden module registry".format(source_name)
            )
        if isinstance(node, ast.Call) and _static_callable_name(
            node.func
        ) == "__import__":
            import_target = _dynamic_import_target(node)
            if import_target is None:
                raise ValidationError(
                    "{} uses an uncontrolled dynamic import".format(source_name)
                )
            imported_name = _static_string(import_target)
            if isinstance(imported_name, str):
                root = _module_root(imported_name)
                if root == "bullet_trade":
                    raise ValidationError(
                        "{} dynamically imports server package bullet_trade".format(
                            source_name
                        )
                    )
                if root not in _ALLOWED_IMPORT_ROOTS[role]:
                    raise ValidationError(
                        "{} has forbidden dynamic import for {}: {}".format(
                            source_name, role, root
                        )
                    )
                if role == "strategy" and root == "joinquant_typing":
                    raise ValidationError(
                        "{} imports joinquant_typing at runtime".format(
                            source_name
                        )
                    )
            elif not (
                role == "helper"
                and isinstance(node.func, ast.Name)
                and node.func.id == "__import__"
                and isinstance(import_target, ast.Name)
                and import_target.id == "profile_module"
            ):
                raise ValidationError(
                    "{} uses an uncontrolled dynamic import".format(source_name)
                )
    if role == "helper":
        helper_forbidden = _DANGEROUS_CALL_NAMES - {
            "__import__",
            "delattr",
            "setattr",
        }
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Name)
                and isinstance(node.ctx, ast.Load)
                and node.id in helper_forbidden
            ):
                raise ValidationError(
                    "{} references forbidden builtin: {}".format(
                        source_name, node.id
                    )
                )
        return
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Subscript)
            and isinstance(node.value, ast.Attribute)
            and isinstance(node.value.value, ast.Name)
            and node.value.value.id == "sys"
            and node.value.attr == "modules"
        ):
            raise ValidationError(
                "{} indexes forbidden module registry".format(source_name)
            )
        if (
            isinstance(node, ast.Name)
            and isinstance(node.ctx, ast.Load)
            and node.id in _DANGEROUS_CALL_NAMES
        ):
            raise ValidationError(
                "{} references forbidden builtin: {}".format(
                    source_name, node.id
                )
            )
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name) and node.func.id in _DANGEROUS_CALL_NAMES:
            raise ValidationError(
                "{} calls forbidden builtin: {}".format(source_name, node.func.id)
            )
        if isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name):
            if node.func.value.id in _STRATEGY_DANGEROUS_ROOTS:
                raise ValidationError(
                    "{} calls forbidden capability: {}.{}".format(
                        source_name, node.func.value.id, node.func.attr
                    )
                )


def _is_dynamic_namespace_call(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in {"globals", "locals", "vars"}
        and not node.args
        and not node.keywords
    )


def _is_raw_object_namespace(node: ast.AST) -> bool:
    if (
        isinstance(node, ast.Attribute)
        and node.attr == "__dict__"
    ):
        return True
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "vars"
        and bool(node.args)
    ):
        return True
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "getattr"
        and len(node.args) >= 2
        and _static_string(node.args[1]) == "__dict__"
    ):
        return True
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "__getattribute__"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id in {"object", "type"}
        and len(node.args) >= 2
        and _static_string(node.args[1]) == "__dict__"
    )


def _is_sys_modules_lookup(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Subscript)
        and isinstance(node.value, ast.Attribute)
        and isinstance(node.value.value, ast.Name)
        and node.value.value.id == "sys"
        and node.value.attr == "modules"
    )


def _attribute_mutation_field(node: ast.Call) -> Optional[ast.AST]:
    called_name = _static_callable_name(node.func)
    owner = _static_callable_owner(node.func)
    if called_name in {"setattr", "delattr"} and (
        isinstance(node.func, ast.Name)
        or (isinstance(owner, ast.Name) and owner.id == "builtins")
    ):
        return node.args[1] if len(node.args) >= 2 else None
    if called_name not in {"__setattr__", "__delattr__"}:
        return None
    is_unbound_builtin = (
        isinstance(owner, ast.Name) and owner.id in {"object", "type"}
    ) or (
        isinstance(owner, ast.Attribute)
        and isinstance(owner.value, ast.Name)
        and owner.value.id == "builtins"
        and owner.attr in {"object", "type"}
    )
    field_index = 1 if is_unbound_builtin else 0
    return node.args[field_index] if len(node.args) > field_index else None


def _validate_dynamic_namespace_mutations(
    role: str,
    tree: ast.Module,
    source_name: str,
) -> None:
    protected = set(_CONTRACT_FIELDS[role])
    if role == "strategy":
        protected.add("TYPE_CHECKING")
    for node in ast.walk(tree):
        if isinstance(node, ast.Subscript) and isinstance(
            node.ctx, (ast.Store, ast.Del)
        ):
            if _is_raw_object_namespace(node.value):
                raise ValidationError(
                    "{} mutates a raw object namespace".format(source_name)
                )
            if _is_dynamic_namespace_call(node.value):
                raise ValidationError(
                    "{} mutates a dynamic namespace".format(source_name)
                )
            key = _static_string(node.slice)
            if isinstance(key, str) and key in protected:
                raise ValidationError(
                    "{} mutates a protected contract field".format(source_name)
                )
        if isinstance(node, ast.Attribute) and isinstance(
            node.ctx, (ast.Store, ast.Del)
        ):
            if node.attr in protected:
                raise ValidationError(
                    "{} mutates a protected module field".format(source_name)
                )
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.NamedExpr)):
            value = node.value
            if _is_dynamic_namespace_call(value):
                raise ValidationError(
                    "{} stores a dynamic namespace".format(source_name)
                )
            if _is_raw_object_namespace(value):
                raise ValidationError(
                    "{} stores a raw object namespace".format(source_name)
                )
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in _NAMESPACE_MUTATOR_NAMES
        ):
            namespace_arguments = [node.func.value] + list(node.args)
            if any(
                _is_dynamic_namespace_call(argument)
                for argument in namespace_arguments
            ):
                raise ValidationError(
                    "{} mutates a dynamic namespace".format(source_name)
                )
            if any(
                _is_raw_object_namespace(argument)
                for argument in namespace_arguments
            ):
                raise ValidationError(
                    "{} mutates a raw object namespace".format(source_name)
                )
            argument_keys = {
                value
                for value in (_static_string(argument) for argument in node.args)
                if isinstance(value, str)
            }
            for argument in node.args:
                if isinstance(argument, ast.Dict):
                    argument_keys.update(
                        key
                        for key in (
                            _static_string(item) for item in argument.keys
                            if item is not None
                        )
                        if isinstance(key, str)
                    )
            keyword_keys = {
                keyword.arg
                for keyword in node.keywords
                if keyword.arg is not None
            }
            if protected.intersection(argument_keys | keyword_keys):
                raise ValidationError(
                    "{} mutates a protected contract field".format(source_name)
                )
        if isinstance(node, ast.Call):
            field_node = _attribute_mutation_field(node)
            if field_node is None:
                continue
            field = _static_string(field_node)
            if field is None:
                raise ValidationError(
                    "{} uses a computed attribute mutation name".format(
                        source_name
                    )
                )
            if isinstance(field, str) and field in protected:
                raise ValidationError(
                    "{} mutates a protected contract field".format(source_name)
                )


def _is_sensitive_name(name: str, include_config_suffixes: bool = True) -> bool:
    lowered = name.lower()
    return (
        lowered in _SENSITIVE_NAMES
        or (
            include_config_suffixes
            and lowered.endswith(_SENSITIVE_NAME_SUFFIXES)
            and (name == name.upper() or lowered.startswith(("bt_", "remote_")))
        )
    )


def _is_sensitive_literal_name(name: str) -> bool:
    return _is_sensitive_name(name, True) or name.lower().endswith(
        _SENSITIVE_NAME_SUFFIXES
    )


def _literal_sensitive_values(tree: ast.Module) -> Iterable[tuple]:
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    yield target.id, node.value.value
        if isinstance(node, ast.AnnAssign) and isinstance(node.value, ast.Constant):
            if isinstance(node.target, ast.Name):
                yield node.target.id, node.value.value
        if isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values):
                if (
                    isinstance(key, ast.Constant)
                    and isinstance(key.value, str)
                    and isinstance(value, ast.Constant)
                ):
                    yield key.value, value.value
        if isinstance(node, ast.Call):
            call_name = None
            if isinstance(node.func, ast.Name):
                call_name = node.func.id
            elif isinstance(node.func, ast.Attribute):
                call_name = node.func.attr
            positions = _SENSITIVE_POSITIONAL_CALLS.get(call_name or "", {})
            for index, name in positions.items():
                if index < len(node.args) and isinstance(
                    node.args[index], ast.Constant
                ):
                    yield name, node.args[index].value
            for keyword in node.keywords:
                if (
                    keyword.arg is not None
                    and isinstance(keyword.value, ast.Constant)
                ):
                    yield keyword.arg, keyword.value.value


def _static_string(node: ast.AST) -> object:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _static_string(node.left)
        right = _static_string(node.right)
        if isinstance(left, str) and isinstance(right, str):
            return left + right
    if isinstance(node, ast.JoinedStr):
        parts = []
        for value in node.values:
            if not isinstance(value, ast.Constant) or not isinstance(
                value.value, str
            ):
                return None
            parts.append(value.value)
        return "".join(parts)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        base = _static_string(node.func.value)
        if not isinstance(base, str):
            return None
        if node.func.attr == "join" and len(node.args) == 1 and not node.keywords:
            values = node.args[0]
            if not isinstance(values, (ast.List, ast.Tuple, ast.Set)):
                return None
            parts = [_static_string(value) for value in values.elts]
            if all(isinstance(value, str) for value in parts):
                return base.join(parts)
        if node.func.attr == "format":
            args = [_static_string(value) for value in node.args]
            keywords = {
                keyword.arg: _static_string(keyword.value)
                for keyword in node.keywords
                if keyword.arg is not None
            }
            if all(isinstance(value, str) for value in args) and all(
                isinstance(value, str) for value in keywords.values()
            ):
                try:
                    return str.format(base, *args, **keywords)
                except (IndexError, KeyError, ValueError):
                    return None
    return None


def _computed_sensitive_values(
    tree: ast.Module,
    strict_expressions: bool,
) -> Iterable[str]:
    def is_computed(value: ast.AST) -> bool:
        if strict_expressions:
            return not isinstance(value, ast.Constant)
        return isinstance(value, (ast.BinOp, ast.JoinedStr))

    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            value = node.value
            for target in targets:
                if (
                    isinstance(target, ast.Name)
                    and _is_sensitive_name(target.id, strict_expressions)
                    and is_computed(value)
                ):
                    yield target.id
        if isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values):
                if (
                    isinstance(key, ast.Constant)
                    and isinstance(key.value, str)
                    and _is_sensitive_name(key.value, strict_expressions)
                    and is_computed(value)
                ):
                    yield key.value
        if isinstance(node, ast.Call):
            for keyword in node.keywords:
                if (
                    keyword.arg is not None
                    and _is_sensitive_name(keyword.arg, strict_expressions)
                    and is_computed(keyword.value)
                ):
                    yield keyword.arg
            call_name = None
            if isinstance(node.func, ast.Name):
                call_name = node.func.id
            elif isinstance(node.func, ast.Attribute):
                call_name = node.func.attr
            if strict_expressions:
                positions = _SENSITIVE_POSITIONAL_CALLS.get(call_name or "", {})
                for index, name in positions.items():
                    if index < len(node.args) and not isinstance(
                        node.args[index], ast.Constant
                    ):
                        yield name


def _validate_sensitive_literals(
    tree: ast.Module,
    text: str,
    source_name: str,
    role: str,
) -> None:
    for pattern in _FORBIDDEN_TEXT_PATTERNS:
        if pattern.search(text):
            raise ValidationError(
                "{} contains a forbidden webhook or bearer credential".format(
                    source_name
                )
            )
    for node in ast.walk(tree):
        static_value = _static_string(node)
        if not isinstance(static_value, str):
            continue
        if any(pattern.search(static_value) for pattern in _FORBIDDEN_TEXT_PATTERNS):
            raise ValidationError(
                "{} constructs a forbidden webhook or bearer credential".format(
                    source_name
                )
            )
    for name, value in _literal_sensitive_values(tree):
        if not _is_sensitive_literal_name(name):
            continue
        if value is None:
            continue
        if isinstance(value, str) and value.strip().lower() in _SAFE_PLACEHOLDERS:
            continue
        else:
            raise ValidationError(
                "{} contains a literal private value for {}".format(
                    source_name, name
                )
            )
    for name in _computed_sensitive_values(tree, role != "helper"):
        raise ValidationError(
            "{} constructs a private value for {}".format(source_name, name)
        )


def _profile_assignments(tree: ast.Module, source_name: str) -> Dict[str, object]:
    assignments: Dict[str, object] = {}
    for node in tree.body:
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
            if isinstance(node.value.value, str):
                continue
        if not isinstance(node, ast.Assign):
            raise ValidationError(
                "{} profile template must contain assignments only".format(source_name)
            )
        if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
            raise ValidationError(
                "{} has a non-name profile assignment".format(source_name)
            )
        try:
            name = node.targets[0].id
            if name in assignments:
                raise ValidationError(
                    "{} has a duplicate profile assignment".format(source_name)
                )
            if name not in {"PROFILE_SCHEMA_VERSION", "PROFILES"}:
                raise ValidationError(
                    "{} has an unknown profile assignment".format(source_name)
                )
            assignments[name] = ast.literal_eval(node.value)
        except (TypeError, ValueError, SyntaxError):
            raise ValidationError(
                "{} profile values must be literal data".format(source_name)
            )
    return assignments


def _validate_profile_shape(
    assignments: Mapping[str, object],
    source_name: str,
    require_private_values: bool,
) -> Mapping[str, object]:
    if (
        type(assignments.get("PROFILE_SCHEMA_VERSION")) is not int
        or assignments.get("PROFILE_SCHEMA_VERSION") != 1
    ):
        raise ValidationError("{} has unsupported profile schema".format(source_name))
    profiles = assignments.get("PROFILES")
    if type(profiles) is not dict or not profiles:
        raise ValidationError("{} must define non-empty PROFILES".format(source_name))
    for profile_name, profile in profiles.items():
        if (
            type(profile_name) is not str
            or not profile_name
            or profile_name != str.strip(profile_name)
            or len(profile_name) > 128
            or not all(str.isalnum(char) or char in "._-" for char in profile_name)
        ):
            raise ValidationError("{} has an invalid profile name".format(source_name))
        if type(profile) is not dict:
            raise ValidationError("{} has invalid profile data".format(source_name))
        keys = set(profile)
        if not _PROFILE_REQUIRED_FIELDS.issubset(keys):
            raise ValidationError("{} profile is missing required fields".format(source_name))
        if not keys.issubset(_PROFILE_ALLOWED_FIELDS):
            raise ValidationError("{} profile contains unknown fields".format(source_name))
        strategy_id = profile.get("strategy_id")
        if (
            type(strategy_id) is not str
            or not strategy_id
            or strategy_id != str.strip(strategy_id)
            or len(strategy_id) > 128
            or not all(str.isalnum(char) or char in "._-" for char in strategy_id)
        ):
            raise ValidationError("{} profile has invalid strategy_id".format(source_name))
        host = profile.get("host")
        token = profile.get("token")
        if require_private_values:
            if (
                type(host) is not str
                or not host
                or host != str.strip(host)
                or any(str.isspace(char) for char in host)
                or len(host) > 255
                or host.lower() in _SAFE_PLACEHOLDERS
            ):
                raise ValidationError("{} profile has invalid host".format(source_name))
            if (
                type(token) is not str
                or not token
                or token != str.strip(token)
                or token.lower() in _SAFE_PLACEHOLDERS
            ):
                raise ValidationError("{} profile has invalid token".format(source_name))
        elif host != "" or token != "":
            raise ValidationError(
                "{} template host/token must remain empty".format(source_name)
            )
        port = profile.get("port", 58620)
        retries = profile.get("retries", 2)
        if type(port) is not int or not 1 <= port <= 65535:
            raise ValidationError("{} profile has invalid port".format(source_name))
        if type(retries) is not int or not 0 <= retries <= 10:
            raise ValidationError("{} profile has invalid retries".format(source_name))
        for field, minimum, maximum in (
            ("retry_interval", 0.1, 30.0),
            ("rpc_timeout", 5.0, 300.0),
            ("place_order_timeout_margin", 0.0, 300.0),
            ("default_wait_timeout", 0.0, 300.0),
        ):
            if field not in profile:
                continue
            value = profile[field]
            if type(value) not in (int, float) or not minimum <= value <= maximum:
                raise ValidationError(
                    "{} profile has invalid {}".format(source_name, field)
                )
        if type(profile.get("debug", True)) is not bool:
            raise ValidationError("{} profile has invalid debug".format(source_name))
        for field in ("account_key", "sub_account_id", "tls_cert"):
            value = profile.get(field)
            if value is not None and (
                type(value) is not str
                or not value
                or value != str.strip(value)
            ):
                raise ValidationError(
                    "{} profile has invalid {}".format(source_name, field)
                )
    return profiles


def _validate_profile_template(tree: ast.Module, source_name: str) -> None:
    assignments = _profile_assignments(tree, source_name)
    _validate_profile_shape(assignments, source_name, False)


def _validate_source_data(
    role: str,
    data: bytes,
    source_name: str,
) -> Dict[str, object]:
    if role not in _ALLOWED_IMPORT_ROOTS:
        raise ValidationError("unknown artifact role: {}".format(role))
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise ValidationError("{} is not UTF-8".format(source_name))
    try:
        tree = ast.parse(
            text,
            filename=source_name,
            mode="exec",
            feature_version=(3, 8),
        )
    except SyntaxError as exc:
        raise ValidationError(
            "{} is not Python 3.8 compatible: {}".format(source_name, exc)
        )
    _validate_imports(role, tree, source_name)
    _validate_calls(role, tree, source_name)
    _validate_dynamic_namespace_mutations(role, tree, source_name)
    _validate_sensitive_literals(tree, text, source_name, role)
    if role == "profile-template":
        _validate_profile_template(tree, source_name)
    return {"bytes": len(data), "sha256": _sha256(data)}


def validate_source(role: str, path: Path) -> Dict[str, object]:
    return _validate_source_data(role, path.read_bytes(), str(path))


def _strict_contract_assignments_text(
    text: str,
    source_name: str,
    role: str,
) -> Dict[str, object]:
    tree = ast.parse(
        text,
        filename=source_name,
        mode="exec",
        feature_version=(3, 8),
    )
    required = _CONTRACT_FIELDS[role]
    write_counts = {name: 0 for name in required}
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Name)
            and node.id in write_counts
            and isinstance(node.ctx, (ast.Store, ast.Del))
        ):
            write_counts[node.id] += 1
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if node.name in write_counts:
                write_counts[node.name] += 1
        if isinstance(node, ast.arg) and node.arg in write_counts:
            write_counts[node.arg] += 1
        if isinstance(node, ast.ExceptHandler) and node.name in write_counts:
            write_counts[node.name] += 1
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                bound_name = alias.asname or _module_root(alias.name)
                if bound_name in write_counts:
                    write_counts[bound_name] += 1

    assignments: Dict[str, object] = {}
    for node in tree.body:
        target = None
        value = None
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
        ):
            target = node.targets[0]
            value = node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            target = node.target
            value = node.value
        if target is None or value is None:
            continue
        if target.id not in required:
            continue
        try:
            assignments[target.id] = ast.literal_eval(value)
        except (TypeError, ValueError, SyntaxError):
            raise ValidationError(
                "{} contract field {} must be a literal".format(
                    source_name, target.id
                )
            )
    for name in sorted(required):
        if write_counts[name] != 1 or name not in assignments:
            raise ValidationError(
                "{} contract field {} must have one literal assignment".format(
                    source_name, name
                )
            )
    return assignments


def _validate_cross_contract_values(
    strategy: Mapping[str, object],
    helper: Mapping[str, object],
    profile_module: Mapping[str, object],
) -> Dict[str, object]:
    profile_name = strategy.get("PROFILE")
    strategy_id = strategy.get("STRATEGY_ID")
    mode = strategy.get("MODE")
    profiles = profile_module.get("PROFILES")
    for field, value in (("PROFILE", profile_name), ("STRATEGY_ID", strategy_id)):
        if (
            type(value) is not str
            or not value
            or value != str.strip(value)
            or len(value) > 128
            or not all(str.isalnum(char) or char in "._-" for char in value)
        ):
            raise ValidationError("strategy {} is invalid".format(field))
    if type(mode) is not str or mode not in {"BACKTEST", "SHADOW", "LIVE"}:
        raise ValidationError("strategy MODE is invalid")
    if type(profiles) is not dict or profile_name not in profiles:
        raise ValidationError("strategy PROFILE is missing from profile template")
    profile = profiles[profile_name]
    if not isinstance(profile, dict) or profile.get("strategy_id") != strategy_id:
        raise ValidationError("profile strategy_id does not match strategy")
    expected_api = strategy.get("_EXPECTED_RUNTIME_API_VERSION")
    helper_api = helper.get("STRATEGY_RUNTIME_API_VERSION")
    if (
        type(expected_api) is not int
        or type(helper_api) is not int
        or expected_api != 1
        or helper_api != 1
    ):
        raise ValidationError("strategy/helper runtime API versions do not match")
    expected_marker = strategy.get("_EXPECTED_RUNTIME_HELPER_MARKER")
    helper_marker = helper.get("STRATEGY_RUNTIME_HELPER_MARKER")
    if (
        type(expected_marker) is not str
        or type(helper_marker) is not str
        or expected_marker != "bullet-trade-joinquant-runtime-helper-v1"
        or helper_marker != "bullet-trade-joinquant-runtime-helper-v1"
    ):
        raise ValidationError("strategy/helper runtime markers do not match")
    expected_schema = strategy.get("_EXPECTED_PROFILE_SCHEMA_VERSION")
    profile_schema = profile_module.get("PROFILE_SCHEMA_VERSION")
    helper_schema = helper.get("PROFILE_SCHEMA_VERSION")
    if (
        type(expected_schema) is not int
        or type(profile_schema) is not int
        or type(helper_schema) is not int
        or expected_schema != 1
        or profile_schema != 1
        or helper_schema != 1
    ):
        raise ValidationError("profile schema versions do not match")
    if strategy.get("_EXPECTED_RUNTIME_PROFILE_MODULE") != "jq_runtime_config":
        raise ValidationError("strategy runtime profile module must be jq_runtime_config")
    return {
        "strategy_id": strategy_id,
        "mode": mode,
        "profile": profile_name,
        "runtime_api_version": expected_api,
        "runtime_helper_marker": expected_marker,
        "profile_schema_version": expected_schema,
        "profile_module": "jq_runtime_config",
    }


def validate_private_profile(
    path: Path,
    contracts: Mapping[str, object],
) -> Dict[str, object]:
    path = path.absolute()
    if _has_reparse_component(path) or not path.is_file():
        raise ValidationError("private profile must be a regular file")
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        raise ValidationError("private profile is not UTF-8")
    try:
        tree = ast.parse(
            text,
            filename=str(path),
            mode="exec",
            feature_version=(3, 8),
        )
    except SyntaxError as exc:
        raise ValidationError(
            "private profile is not Python 3.8 compatible: {}".format(exc)
        )
    assignments = _profile_assignments(tree, "private profile")
    profiles = _validate_profile_shape(assignments, "private profile", True)
    profile_name = contracts["profile"]
    strategy_id = contracts["strategy_id"]
    if profile_name not in profiles:
        raise ValidationError("private profile is missing the strategy profile")
    selected = profiles[profile_name]
    if selected.get("strategy_id") != strategy_id:
        raise ValidationError("private profile strategy_id does not match strategy")
    return {
        "profile": profile_name,
        "profile_schema_version": assignments["PROFILE_SCHEMA_VERSION"],
        "strategy_id": strategy_id,
    }


def _repository_snapshot(
    repo: Path,
) -> tuple:
    repo = repo.resolve()
    results = []
    contents: Dict[str, bytes] = {}
    contract_values: Dict[str, Mapping[str, object]] = {}
    for spec in ARTIFACTS:
        source = repo / spec.source
        if not source.is_file():
            raise ValidationError("missing export source: {}".format(spec.source))
        try:
            inside_repo = os.path.commonpath(
                [str(source.resolve()), str(repo)]
            ) == str(repo)
        except ValueError:
            inside_repo = False
        if source.is_symlink() or not inside_repo:
            raise ValidationError(
                "export source must be a regular repository file: {}".format(
                    spec.source
                )
            )
        data = source.read_bytes()
        result = _validate_source_data(spec.role, data, spec.source)
        contents[spec.source] = data
        contract_values[spec.role] = _strict_contract_assignments_text(
            data.decode("utf-8"), spec.source, spec.role
        )
        result.update(
            {
                "role": spec.role,
                "source": spec.source,
                "output": spec.output,
                "upload_name": spec.upload_name,
                "requires_private_values": spec.requires_private_values,
            }
        )
        results.append(result)
    contracts = _validate_cross_contract_values(
        contract_values["strategy"],
        contract_values["helper"],
        contract_values["profile-template"],
    )
    return results, contents, contracts


def validate_repository(repo: Path) -> List[Dict[str, object]]:
    results, _, _ = _repository_snapshot(repo)
    return results


def validate_cross_contract(repo: Path) -> Dict[str, object]:
    _, _, contracts = _repository_snapshot(repo)
    return contracts


def _manifest(
    files: List[Dict[str, object]],
    contracts: Dict[str, object],
) -> Dict[str, object]:
    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "artifact_kind": ARTIFACT_KIND,
        "production_ready": False,
        "contracts": contracts,
        "files": files,
        "notes": [
            "Fill jq_runtime_config.py privately; never commit or export credentials.",
            "This bundle is a validated upload candidate, not live-trading approval.",
        ],
    }


def _has_reparse_component(path: Path) -> bool:
    current = path
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    while True:
        if current.is_symlink():
            return True
        try:
            path_stat = os.lstat(str(current))
        except FileNotFoundError:
            path_stat = None
        except OSError as exc:
            raise ValidationError(
                "cannot inspect destination path component"
            ) from exc
        if path_stat is not None:
            attributes = getattr(path_stat, "st_file_attributes", 0)
            if reparse_flag and attributes & reparse_flag:
                return True
        parent = current.parent
        if parent == current:
            return False
        current = parent


def export_joinquant(
    repo: Path,
    destination: Path,
    private_profile: Optional[Path] = None,
) -> Dict[str, object]:
    repo = repo.resolve()
    destination = destination.absolute()
    if _has_reparse_component(destination):
        raise ValidationError("destination must not use a symlink or reparse point")
    destination = destination.resolve()
    files, contents, contracts = _repository_snapshot(repo)
    if private_profile is not None:
        validate_private_profile(private_profile, contracts)
    if destination.exists():
        raise ValidationError("destination must not exist")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(
        tempfile.mkdtemp(prefix=".joinquant-export-", dir=str(destination.parent))
    )
    try:
        for spec, entry in zip(ARTIFACTS, files):
            target = temporary / spec.output
            with target.open("wb") as stream:
                stream.write(contents[spec.source])
            output_data = target.read_bytes()
            if _sha256(output_data) != entry["sha256"]:
                raise ValidationError("export changed source bytes: {}".format(spec.source))
        manifest = _manifest(files, contracts)
        manifest_path = temporary / "manifest.json"
        with manifest_path.open("w", encoding="utf-8", newline="\n") as stream:
            stream.write(
                json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True)
                + "\n"
            )
        os.replace(str(temporary), str(destination))
        return manifest
    except BaseException:
        shutil.rmtree(str(temporary), ignore_errors=True)
        raise


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        default="dist/joinquant",
        help="new export directory (default: dist/joinquant)",
    )
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument(
        "--private-profile",
        help="validate a private jq_runtime_config.py without copying it",
    )
    args = parser.parse_args()

    repo = Path(__file__).resolve().parents[1]
    try:
        private_path = (
            Path(args.private_profile).absolute() if args.private_profile else None
        )
        if args.validate_only:
            files, _, contracts = _repository_snapshot(repo)
            if private_path is not None:
                private_result = validate_private_profile(private_path, contracts)
                print(
                    "JOINQUANT_PRIVATE_PROFILE_OK profile={} strategy_id={}".format(
                        private_result["profile"], private_result["strategy_id"]
                    )
                )
            print("JOINQUANT_EXPORT_VALIDATION_OK {} files".format(len(files)))
            return 0
        destination = Path(args.output)
        if not destination.is_absolute():
            destination = repo / destination
        export_joinquant(repo, destination, private_profile=private_path)
        if private_path is not None:
            print("JOINQUANT_PRIVATE_PROFILE_OK")
        print("JOINQUANT_EXPORT_OK {}".format(destination.resolve()))
        return 0
    except ValidationError as exc:
        print("JOINQUANT_EXPORT_ERROR: {}".format(exc), file=sys.stderr)
        return 2
    except OSError:
        print("JOINQUANT_EXPORT_IO_ERROR", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
