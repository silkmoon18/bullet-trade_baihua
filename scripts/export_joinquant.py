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
from typing import Dict, Iterable, List, Mapping, Optional, Sequence


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

_ARTIFACT_ROLES = frozenset(spec.role for spec in ARTIFACTS)

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
    "tls_cert",
    "token",
    "webhook",
    "webhook_url",
}

_PROFILE_REQUIRED_FIELDS = {"host", "token"}
_PROFILE_OPTIONAL_FIELDS = {
    "port",
    "account_key",
    "tls_cert",
    "rpc_timeout",
}
_PROFILE_ALLOWED_FIELDS = _PROFILE_REQUIRED_FIELDS | _PROFILE_OPTIONAL_FIELDS
_STRATEGY_ALLOWED_FIELDS = {"profile", "mode"}

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


def _is_sensitive_literal_name(name: str) -> bool:
    lowered = name.lower()
    return lowered in _SENSITIVE_NAMES or lowered.endswith(
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


def _validate_sensitive_literals(
    tree: ast.Module,
    text: str,
    source_name: str,
) -> None:
    for pattern in _FORBIDDEN_TEXT_PATTERNS:
        if pattern.search(text):
            raise ValidationError(
                "{} contains a forbidden webhook or bearer credential".format(
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
        raise ValidationError(
            "{} contains a literal private value for {}".format(
                source_name, name
            )
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
            if name not in {
                "PROFILE_SCHEMA_VERSION",
                "DEFAULT_PROFILE",
                "STRATEGIES",
                "PROFILES",
            }:
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
        or assignments.get("PROFILE_SCHEMA_VERSION") != 2
    ):
        raise ValidationError("{} has unsupported profile schema".format(source_name))
    profiles = assignments.get("PROFILES")
    if type(profiles) is not dict or not profiles:
        raise ValidationError("{} must define non-empty PROFILES".format(source_name))
    default_profile = assignments.get("DEFAULT_PROFILE")
    if (
        type(default_profile) is not str
        or not default_profile
        or default_profile != default_profile.strip()
    ):
        raise ValidationError(
            "{} has invalid DEFAULT_PROFILE".format(source_name)
        )
    if default_profile not in profiles:
        raise ValidationError(
            "{} DEFAULT_PROFILE is missing from PROFILES".format(source_name)
        )
    strategies = assignments.get("STRATEGIES")
    if type(strategies) is not dict:
        raise ValidationError("{} STRATEGIES must be a dictionary".format(source_name))
    for strategy_id, settings in strategies.items():
        if (
            type(strategy_id) is not str
            or not strategy_id
            or strategy_id != strategy_id.strip()
            or len(strategy_id) > 128
            or not all(
                str.isalnum(char) or char in "._-" for char in strategy_id
            )
        ):
            raise ValidationError("{} has invalid strategy id".format(source_name))
        if type(settings) is not dict:
            raise ValidationError("{} has invalid strategy settings".format(source_name))
        if not set(settings).issubset(_STRATEGY_ALLOWED_FIELDS):
            raise ValidationError("{} strategy contains unknown fields".format(source_name))
        profile_name = settings.get("profile", default_profile)
        if type(profile_name) is not str or profile_name not in profiles:
            raise ValidationError("{} strategy has invalid profile".format(source_name))
        mode = settings.get("mode", "JQ")
        if type(mode) is not str or mode not in ("JQ", "QMT_REMOTE"):
            raise ValidationError("{} strategy has invalid mode".format(source_name))
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
        if type(port) is not int or not 1 <= port <= 65535:
            raise ValidationError("{} profile has invalid port".format(source_name))
        for field, minimum, maximum in (("rpc_timeout", 5.0, 300.0),):
            if field not in profile:
                continue
            value = profile[field]
            if type(value) not in (int, float) or not minimum <= value <= maximum:
                raise ValidationError(
                    "{} profile has invalid {}".format(source_name, field)
                )
        for field in ("account_key", "tls_cert"):
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


def _validate_helper_imports(tree: ast.Module, source_name: str) -> None:
    """Reject modules known to be absent from the JoinQuant runtime."""

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules = [alias.name.split(".", 1)[0] for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            modules = [(node.module or "").split(".", 1)[0]]
        else:
            continue
        if "dataclasses" in modules:
            raise ValidationError(
                "{} imports unavailable JoinQuant module dataclasses".format(
                    source_name
                )
            )


def _validate_source_data(
    role: str,
    data: bytes,
    source_name: str,
) -> Dict[str, object]:
    if role not in _ARTIFACT_ROLES:
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
    _validate_sensitive_literals(tree, text, source_name)
    if role == "profile-template":
        _validate_profile_template(tree, source_name)
    elif role == "helper":
        _validate_helper_imports(tree, source_name)
    return {"bytes": len(data), "sha256": _sha256(data)}


def validate_source(role: str, path: Path) -> Dict[str, object]:
    return _validate_source_data(role, path.read_bytes(), str(path))


def _strategy_identity(text: str, source_name: str) -> Dict[str, object]:
    tree = ast.parse(
        text,
        filename=source_name,
        mode="exec",
        feature_version=(3, 8),
    )
    values: Dict[str, object] = {}
    for node in tree.body:
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
        ):
            name = node.targets[0].id
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            name = node.target.id
        else:
            continue
        if name != "STRATEGY_ID" or name in values:
            continue
        try:
            values[name] = ast.literal_eval(node.value)
        except (TypeError, ValueError, SyntaxError):
            values[name] = None
    identity: Dict[str, object] = {}
    for field, key in (("STRATEGY_ID", "strategy_id"),):
        value = values.get(field)
        if (
            type(value) is not str
            or not value
            or value != str.strip(value)
            or len(value) > 128
            or not all(str.isalnum(char) or char in "._-" for char in value)
        ):
            raise ValidationError("strategy {} is invalid".format(field))
        identity[key] = value
    return identity


def validate_private_profile(
    path: Path,
    identity: Mapping[str, object],
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
    strategy_id = identity["strategy_id"]
    settings = assignments["STRATEGIES"].get(strategy_id, {})
    profile_name = settings.get("profile", assignments["DEFAULT_PROFILE"])
    mode = settings.get("mode", "JQ")
    return {
        "profile": profile_name,
        "profile_schema_version": assignments["PROFILE_SCHEMA_VERSION"],
        "strategy_id": strategy_id,
        "mode": mode,
    }


def _repository_snapshot(
    repo: Path,
) -> tuple:
    repo = repo.resolve()
    results = []
    contents: Dict[str, bytes] = {}
    identity: Dict[str, object] = {}
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
        if spec.role == "strategy":
            identity = _strategy_identity(data.decode("utf-8"), spec.source)
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
    return results, contents, identity


def validate_repository(repo: Path) -> List[Dict[str, object]]:
    results, _, _ = _repository_snapshot(repo)
    return results


def _manifest(files: List[Dict[str, object]]) -> Dict[str, object]:
    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "artifact_kind": ARTIFACT_KIND,
        "production_ready": False,
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
    files, contents, identity = _repository_snapshot(repo)
    if private_profile is not None:
        validate_private_profile(private_profile, identity)
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
        manifest = _manifest(files)
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
            files, _, identity = _repository_snapshot(repo)
            if private_path is not None:
                private_result = validate_private_profile(private_path, identity)
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
    sys.exit(main())
