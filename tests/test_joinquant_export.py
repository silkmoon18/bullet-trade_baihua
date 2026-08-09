import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
EXPORT_SCRIPT = ROOT / "scripts" / "export_joinquant.py"


def _load_export_module():
    spec = importlib.util.spec_from_file_location("export_joinquant_test", EXPORT_SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _run_cleanroom(code, *, isolated=False, no_site=False):
    command = [sys.executable]
    if isolated:
        command.append("-I")
    if no_site:
        command.append("-S")
    command.extend(["-X", "utf8", "-c", code])
    return subprocess.run(
        command,
        cwd=str(ROOT),
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def _copy_export_sources(exporter, destination):
    for artifact in exporter.ARTIFACTS:
        target = destination / artifact.source
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / artifact.source, target)


def test_export_is_deterministic_and_preserves_source_bytes(tmp_path):
    exporter = _load_export_module()
    first = tmp_path / "first"
    second = tmp_path / "second"

    first_manifest = exporter.export_joinquant(ROOT, first)
    second_manifest = exporter.export_joinquant(ROOT, second)

    assert first_manifest == second_manifest
    assert (first / "manifest.json").read_bytes() == (
        second / "manifest.json"
    ).read_bytes()
    assert {path.name for path in first.iterdir()} == {
        "good_etf.py",
        "bullet_trade_jq_remote_helper.py",
        "jq_runtime_config.example.py",
        "manifest.json",
    }
    manifest_text = (first / "manifest.json").read_text(encoding="utf-8")
    assert str(ROOT) not in manifest_text
    assert first_manifest["production_ready"] is False

    for spec in exporter.ARTIFACTS:
        source = ROOT / spec.source
        output = first / spec.output
        assert output.read_bytes() == source.read_bytes()


@pytest.mark.parametrize("with_content", [False, True])
def test_export_refuses_any_existing_destination(tmp_path, with_content):
    exporter = _load_export_module()
    destination = tmp_path / "existing"
    destination.mkdir()
    if with_content:
        (destination / "keep.txt").write_text("user data", encoding="utf-8")

    with pytest.raises(exporter.ValidationError, match="must not exist"):
        exporter.export_joinquant(ROOT, destination)

    assert destination.is_dir()
    if with_content:
        assert (destination / "keep.txt").read_text(encoding="utf-8") == "user data"


def test_export_replace_failure_preserves_absent_destination(tmp_path, monkeypatch):
    exporter = _load_export_module()
    destination = tmp_path / "export"

    def fail_replace(source, target):
        raise OSError("simulated replace failure")

    monkeypatch.setattr(exporter.os, "replace", fail_replace)
    with pytest.raises(OSError, match="simulated replace failure"):
        exporter.export_joinquant(ROOT, destination)

    assert not destination.exists()
    assert not list(tmp_path.glob(".joinquant-export-*"))


@pytest.mark.parametrize(
    ("source", "message"),
    [
        ("import bullet_trade\n", "server package"),
        ("import socket\n", "forbidden import"),
        ("open('private.txt')\n", "forbidden builtin"),
        ("from joinquant_typing import Portfolio\n", "at runtime"),
        ("configure(token='hard-coded-real-token')\n", "literal private value"),
        ("TYPE_CHECKING = True\n", "rebinds TYPE_CHECKING"),
        (
            "try:\n    1 / 0\nexcept Exception as TYPE_CHECKING:\n    pass\n",
            "rebinds TYPE_CHECKING",
        ),
        (
            "globals()['TYPE_CHECKING'] = True\n",
            "dynamic namespace",
        ),
        ("from . import socket\n", "relative import"),
        ("opener = open\nopener('private.txt')\n", "forbidden builtin"),
        (
            "opener = __builtins__['open']\nopener('private.txt')\n",
            "builtins namespace",
        ),
        ("sys.modules['os'].system('whoami')\n", "module registry"),
        ("credential = 'hard-coded-real-token'\n", "literal private value"),
        ("BT_REMOTE_TOKEN = 'hard-coded-real-token'\n", "literal private value"),
        ("api_token = 'hard-coded-real-token'\n", "literal private value"),
        ("server_host = '10.0.0.8'\n", "literal private value"),
        (
            "BT_REMOTE_TOKEN = ''.join(['hard-coded-', 'real-token'])\n",
            "constructs a private value",
        ),
        (
            "configure('10.0.0.8', 'hard-coded-real-token')\n",
            "literal private value",
        ),
        (
            "bt.configure('10.0.0.8', 'hard-coded-real-token')\n",
            "literal private value",
        ),
        (
            "def configure_runtime(host_value, token_value):\n"
            "    bt.configure(host_value, token_value)\n",
            "constructs a private value",
        ),
        (
            "url = 'https://open.feishu.cn/open-apis/' + 'bot/v2/hook/secret'\n",
            "constructs a forbidden webhook",
        ),
        (
            "url = ''.join(['https://open.feishu.cn/open-apis/', "
            "'bot/v2/hook/secret'])\n",
            "constructs a forbidden webhook",
        ),
        (
            "url = 'https://open.feishu.cn/open-apis/{}/{}'.format("
            "'bot/v2', 'hook/secret')\n",
            "constructs a forbidden webhook",
        ),
        ("match value:\n    case 1:\n        pass\n", "Python 3.8 compatible"),
    ],
)
def test_strategy_validation_rejects_server_and_dangerous_capabilities(
    tmp_path, source, message
):
    exporter = _load_export_module()
    path = tmp_path / "strategy.py"
    path.write_text(
        "from typing import TYPE_CHECKING\n"
        "if TYPE_CHECKING:\n"
        "    from joinquant_typing import Context\n"
        + source,
        encoding="utf-8",
    )

    with pytest.raises(exporter.ValidationError, match=message):
        exporter.validate_source("strategy", path)


@pytest.mark.parametrize(
    ("source", "message"),
    [
        (
            "def bind_flag():\n"
            "    from typing import TYPE_CHECKING\n"
            "if TYPE_CHECKING:\n"
            "    from joinquant_typing import Context\n",
            "module top level",
        ),
        (
            "if False:\n"
            "    from typing import TYPE_CHECKING\n"
            "if TYPE_CHECKING:\n"
            "    from joinquant_typing import Context\n",
            "module top level",
        ),
        (
            "from typing import TYPE_CHECKING\n"
            "from typing import *\n"
            "if TYPE_CHECKING:\n"
            "    from joinquant_typing import Context\n",
            "wildcard typing import",
        ),
    ],
)
def test_strategy_requires_unconditional_top_level_type_checking_binding(
    tmp_path, source, message
):
    exporter = _load_export_module()
    path = tmp_path / "strategy.py"
    path.write_text(source, encoding="utf-8")

    with pytest.raises(exporter.ValidationError, match=message):
        exporter.validate_source("strategy", path)


def test_profile_validation_rejects_private_values_and_executable_code(tmp_path):
    exporter = _load_export_module()
    private_profile = tmp_path / "private.py"
    private_profile.write_text(
        "PROFILE_SCHEMA_VERSION = 1\n"
        "PROFILES = {'prod': {'host': '10.0.0.8', 'token': 'real-token'}}\n",
        encoding="utf-8",
    )
    with pytest.raises(exporter.ValidationError, match="literal private value"):
        exporter.validate_source("profile-template", private_profile)

    executable_profile = tmp_path / "executable.py"
    executable_profile.write_text(
        "PROFILE_SCHEMA_VERSION = 1\n"
        "PROFILES = {'prod': {'host': '', 'token': ''}}\n"
        "print('side effect')\n",
        encoding="utf-8",
    )
    with pytest.raises(exporter.ValidationError, match="assignments only"):
        exporter.validate_source("profile-template", executable_profile)

    computed_profile = tmp_path / "computed.py"
    computed_profile.write_text(
        "PROFILE_SCHEMA_VERSION = 1\n"
        "PROFILES = {'prod': {'host': '', 'token': 'real-' + 'token'}}\n",
        encoding="utf-8",
    )
    with pytest.raises(exporter.ValidationError, match="constructs a private value"):
        exporter.validate_source("profile-template", computed_profile)

    numeric_private_profile = tmp_path / "numeric-private.py"
    numeric_private_profile.write_text(
        "PROFILE_SCHEMA_VERSION = 1\n"
        "PROFILES = {'prod': {'host': '', 'token': '', 'account_key': 123}}\n",
        encoding="utf-8",
    )
    with pytest.raises(exporter.ValidationError, match="literal private value"):
        exporter.validate_source("profile-template", numeric_private_profile)

    unknown_field_profile = tmp_path / "unknown-field.py"
    unknown_field_profile.write_text(
        "PROFILE_SCHEMA_VERSION = 1\n"
        "PROFILES = {'prod': {'strategy_id': 'good_etf', 'host': '', "
        "'token': '', 'credential': None}}\n",
        encoding="utf-8",
    )
    with pytest.raises(exporter.ValidationError, match="unknown fields"):
        exporter.validate_source("profile-template", unknown_field_profile)


def test_cross_contract_rejects_strategy_profile_drift(tmp_path):
    exporter = _load_export_module()
    repo = tmp_path / "repo"
    _copy_export_sources(exporter, repo)
    strategy = repo / "strategies" / "joinquant" / "good_etf.py"
    source = strategy.read_text(encoding="utf-8")
    strategy.write_text(
        source.replace("PROFILE = 'good_etf-prod'", "PROFILE = 'missing-profile'"),
        encoding="utf-8",
    )

    with pytest.raises(exporter.ValidationError, match="missing from profile template"):
        exporter.validate_repository(repo)


def test_cross_contract_rejects_dynamic_contract_reassignment(tmp_path):
    exporter = _load_export_module()
    repo = tmp_path / "repo"
    _copy_export_sources(exporter, repo)
    strategy = repo / "strategies" / "joinquant" / "good_etf.py"
    strategy.write_text(
        strategy.read_text(encoding="utf-8")
        + "\nPROFILE = 'missing-' + 'profile'\n",
        encoding="utf-8",
    )

    with pytest.raises(exporter.ValidationError, match="must be a literal"):
        exporter.validate_repository(repo)


@pytest.mark.parametrize(
    ("source", "message"),
    [
        ("for MODE in ('LIVE',):\n    pass\n", "one literal assignment"),
        (
            "for PROFILE in ('missing-profile',):\n    pass\n",
            "one literal assignment",
        ),
        (
            "try:\n    1 / 0\nexcept Exception as PROFILE:\n    pass\n",
            "one literal assignment",
        ),
        ("with manager as MODE:\n    pass\n", "one literal assignment"),
        ("globals()['MODE'] = 'LIVE'\n", "dynamic namespace"),
        ("globals().update(MODE='LIVE')\n", "dynamic namespace"),
        ("vars()['MODE'] = 'LIVE'\n", "dynamic namespace"),
        ("vars().update(MODE='LIVE')\n", "dynamic namespace"),
        (
            "namespace = globals()\nnamespace['MODE'] = 'LIVE'\n",
            "stores a dynamic namespace",
        ),
        (
            "sys.modules[__name__].MODE = 'LIVE'\n",
            "module registry",
        ),
    ],
)
def test_cross_contract_rejects_supported_runtime_rebinding_forms(
    tmp_path, source, message
):
    exporter = _load_export_module()
    repo = tmp_path / "repo"
    _copy_export_sources(exporter, repo)
    strategy = repo / "strategies" / "joinquant" / "good_etf.py"
    strategy.write_text(
        strategy.read_text(encoding="utf-8") + "\n" + source,
        encoding="utf-8",
    )

    with pytest.raises(exporter.ValidationError, match=message):
        exporter.validate_repository(repo)


@pytest.mark.parametrize(
    "source",
    [
        "globals()['STRATEGY_RUNTIME_API_VERSION'] = 2\n",
        "vars()['STRATEGY_RUNTIME_API_VERSION'] = 2\n",
        "globals().update(STRATEGY_RUNTIME_API_VERSION=2)\n",
        "globals().clear()\n",
        "namespace = globals()\nnamespace['STRATEGY_RUNTIME_API_VERSION'] = 2\n",
    ],
)
def test_helper_contract_rejects_dynamic_namespace_rebinding(tmp_path, source):
    exporter = _load_export_module()
    repo = tmp_path / "repo"
    _copy_export_sources(exporter, repo)
    helper = repo / "helpers" / "bullet_trade_jq_remote_helper.py"
    helper.write_text(
        helper.read_text(encoding="utf-8") + "\n" + source,
        encoding="utf-8",
    )

    with pytest.raises(exporter.ValidationError, match="dynamic namespace"):
        exporter.validate_repository(repo)


@pytest.mark.parametrize(
    "source",
    [
        "vars(sys.modules[__name__])['STRATEGY_RUNTIME_API_VERSION'] = 2\n",
        "module_ref = sys.modules[__name__]\n"
        "module_ref.STRATEGY_RUNTIME_API_VERSION = 2\n",
        "setattr(sys.modules[__name__], 'STRATEGY_RUNTIME_API_VERSION', 2)\n",
        "namespace.__setitem__('STRATEGY_RUNTIME_API_VERSION', 2)\n",
        "dict.update(globals(), {'STRATEGY_RUNTIME_API_VERSION': 2})\n",
        "dict.__setitem__(globals(), 'STRATEGY_RUNTIME_API_VERSION', 2)\n",
    ],
)
def test_helper_contract_rejects_protected_field_mutations(tmp_path, source):
    exporter = _load_export_module()
    repo = tmp_path / "repo"
    _copy_export_sources(exporter, repo)
    helper = repo / "helpers" / "bullet_trade_jq_remote_helper.py"
    helper.write_text(
        helper.read_text(encoding="utf-8") + "\n" + source,
        encoding="utf-8",
    )

    with pytest.raises(
        exporter.ValidationError,
        match="protected|module registry|object namespace|dynamic namespace",
    ):
        exporter.validate_repository(repo)


@pytest.mark.parametrize(
    "source",
    [
        "field = ''.join(['STRATEGY_RUNTIME_API_', 'VERSION'])\n"
        "setattr(sys.modules[__name__], field, 2)\n",
        "namespace = vars(sys.modules[__name__])\n"
        "field = 'STRATEGY_RUNTIME_API_VERSION'\nnamespace[field] = 2\n",
        "namespace = sys.modules[__name__].__dict__\n"
        "field = 'STRATEGY_RUNTIME_API_VERSION'\nnamespace[field] = 2\n",
    ],
)
def test_helper_rejects_direct_module_namespace_access(tmp_path, source):
    exporter = _load_export_module()
    repo = tmp_path / "repo"
    _copy_export_sources(exporter, repo)
    helper = repo / "helpers" / "bullet_trade_jq_remote_helper.py"
    helper.write_text(
        helper.read_text(encoding="utf-8") + "\n" + source,
        encoding="utf-8",
    )

    with pytest.raises(
        exporter.ValidationError, match="module registry|object namespace"
    ):
        exporter.validate_repository(repo)


@pytest.mark.parametrize(
    "source",
    [
        "namespace = vars(sys.modules.get(__name__))\n"
        "field = ''.join(['STRATEGY_RUNTIME_API_', 'VERSION'])\n"
        "namespace[field] = 2\n",
        "namespace = sys.modules.get(__name__).__dict__\n"
        "field = 'STRATEGY_RUNTIME_API_VERSION'\nnamespace[field] = 2\n",
        "field = ''.join(['STRATEGY_RUNTIME_API_', 'VERSION'])\n"
        "sys.modules.get(__name__).__dict__[field] = 2\n",
        "namespace = getattr(sys.modules.get(__name__), '__dict__')\n"
        "field = ''.join(['STRATEGY_RUNTIME_API_', 'VERSION'])\n"
        "namespace[field] = 2\n",
        "field = ''.join(['STRATEGY_RUNTIME_API_', 'VERSION'])\n"
        "getattr(sys.modules.get(__name__), '__dict__')[field] = 2\n",
        "namespace = object.__getattribute__("
        "sys.modules.get(__name__), '__dict__')\n"
        "field = 'STRATEGY_RUNTIME_API_VERSION'\nnamespace[field] = 2\n",
    ],
)
def test_helper_rejects_raw_object_namespace_mutations(tmp_path, source):
    exporter = _load_export_module()
    repo = tmp_path / "repo"
    _copy_export_sources(exporter, repo)
    helper = repo / "helpers" / "bullet_trade_jq_remote_helper.py"
    helper.write_text(
        helper.read_text(encoding="utf-8") + "\n" + source,
        encoding="utf-8",
    )

    with pytest.raises(exporter.ValidationError, match="object namespace"):
        exporter.validate_repository(repo)


@pytest.mark.parametrize(
    ("source", "message"),
    [
        (
            "field = ''.join(['STRATEGY_RUNTIME_API_', 'VERSION'])\n"
            "dict.__setitem__(globals(), field, 2)\n",
            "dynamic namespace",
        ),
        (
            "field = ''.join(['STRATEGY_RUNTIME_API_', 'VERSION'])\n"
            "dict.update(globals(), {field: 2})\n",
            "dynamic namespace",
        ),
        (
            "field = ''.join(['STRATEGY_RUNTIME_API_', 'VERSION'])\n"
            "dict.pop(globals(), field)\n",
            "dynamic namespace",
        ),
        (
            "field = ''.join(['STRATEGY_RUNTIME_API_', 'VERSION'])\n"
            "dict.__setitem__(getattr(sys.modules.get(__name__), "
            "'__dict__'), field, 2)\n",
            "object namespace",
        ),
        (
            "field = ''.join(['STRATEGY_RUNTIME_API_', 'VERSION'])\n"
            "builtins.dict.update(object.__getattribute__("
            "sys.modules.get(__name__), '__dict__'), {field: 2})\n",
            "object namespace",
        ),
    ],
)
def test_helper_rejects_unbound_dict_namespace_mutations(
    tmp_path, source, message
):
    exporter = _load_export_module()
    repo = tmp_path / "repo"
    _copy_export_sources(exporter, repo)
    helper = repo / "helpers" / "bullet_trade_jq_remote_helper.py"
    helper.write_text(
        helper.read_text(encoding="utf-8") + "\n" + source,
        encoding="utf-8",
    )

    with pytest.raises(exporter.ValidationError, match=message):
        exporter.validate_repository(repo)


@pytest.mark.parametrize(
    "source",
    [
        "field = ''.join(['STRATEGY_RUNTIME_API_', 'VERSION'])\n"
        "type({}).__setitem__(globals(), field, 2)\n",
        "field = ''.join(['STRATEGY_RUNTIME_API_', 'VERSION'])\n"
        "globals().__class__.__setitem__(globals(), field, 2)\n",
        "field = ''.join(['STRATEGY_RUNTIME_API_', 'VERSION'])\n"
        "dict.__mro__[0].__setitem__(globals(), field, 2)\n",
        "field = ''.join(['STRATEGY_RUNTIME_API_', 'VERSION'])\n"
        "getattr(__import__('builtins'), 'dict').__setitem__("
        "globals(), field, 2)\n",
    ],
)
def test_helper_rejects_derived_dict_namespace_mutations(tmp_path, source):
    exporter = _load_export_module()
    repo = tmp_path / "repo"
    _copy_export_sources(exporter, repo)
    helper = repo / "helpers" / "bullet_trade_jq_remote_helper.py"
    helper.write_text(
        helper.read_text(encoding="utf-8") + "\n" + source,
        encoding="utf-8",
    )

    with pytest.raises(
        exporter.ValidationError,
        match="dynamic namespace|forbidden dynamic import",
    ):
        exporter.validate_repository(repo)


@pytest.mark.parametrize(
    "source",
    [
        "field = ''.join(['STRATEGY_RUNTIME_API_', 'VERSION'])\n"
        "object.__setattr__(sys.modules.get(__name__), field, 2)\n",
        "field = ''.join(['STRATEGY_RUNTIME_API_', 'VERSION'])\n"
        "sys.modules.get(__name__).__setattr__(field, 2)\n",
        "field = ''.join(['STRATEGY_RUNTIME_API_', 'VERSION'])\n"
        "object.__delattr__(sys.modules.get(__name__), field)\n",
        "field = ''.join(['STRATEGY_RUNTIME_API_', 'VERSION'])\n"
        "type(sys.modules.get(__name__)).__setattr__("
        "sys.modules.get(__name__), field, 2)\n",
    ],
)
def test_helper_rejects_dunder_attribute_contract_mutations(tmp_path, source):
    exporter = _load_export_module()
    repo = tmp_path / "repo"
    _copy_export_sources(exporter, repo)
    helper = repo / "helpers" / "bullet_trade_jq_remote_helper.py"
    helper.write_text(
        helper.read_text(encoding="utf-8") + "\n" + source,
        encoding="utf-8",
    )

    with pytest.raises(exporter.ValidationError, match="computed attribute"):
        exporter.validate_repository(repo)


@pytest.mark.parametrize(
    "source",
    [
        "field = ''.join(['STRATEGY_RUNTIME_API_', 'VERSION'])\n"
        "getattr(object, '__setattr__')("
        "sys.modules.get(__name__), field, 2)\n",
        "field = ''.join(['STRATEGY_RUNTIME_API_', 'VERSION'])\n"
        "getattr(sys.modules.get(__name__), '__setattr__')(field, 2)\n",
        "field = ''.join(['STRATEGY_RUNTIME_API_', 'VERSION'])\n"
        "object.__getattribute__("
        "sys.modules.get(__name__), '__setattr__')(field, 2)\n",
        "field = ''.join(['STRATEGY_RUNTIME_API_', 'VERSION'])\n"
        "getattr(object, '__delattr__')("
        "sys.modules.get(__name__), field)\n",
    ],
)
def test_helper_rejects_static_getter_attribute_mutations(tmp_path, source):
    exporter = _load_export_module()
    repo = tmp_path / "repo"
    _copy_export_sources(exporter, repo)
    helper = repo / "helpers" / "bullet_trade_jq_remote_helper.py"
    helper.write_text(
        helper.read_text(encoding="utf-8") + "\n" + source,
        encoding="utf-8",
    )

    with pytest.raises(exporter.ValidationError, match="computed attribute"):
        exporter.validate_repository(repo)


@pytest.mark.parametrize("module_name", ["subprocess", "pathlib", "requests"])
def test_helper_rejects_static_dynamic_import_outside_role_allowlist(
    tmp_path, module_name
):
    exporter = _load_export_module()
    helper = tmp_path / "helper.py"
    helper.write_text(
        "__import__({!r})\n".format(module_name), encoding="utf-8"
    )

    with pytest.raises(exporter.ValidationError, match="forbidden dynamic import"):
        exporter.validate_source("helper", helper)


@pytest.mark.parametrize(
    "source",
    [
        "__import__(name='subprocess')\n",
        "__import__(name='bullet_trade')\n",
        "__import__(**{'name': 'requests'})\n",
        "builtins.__import__('subprocess')\n",
        "getattr(builtins, '__import__')('subprocess')\n",
        "__builtins__['__import__']('subprocess')\n",
    ],
)
def test_helper_rejects_alternate_forbidden_dynamic_import_calls(
    tmp_path, source
):
    exporter = _load_export_module()
    helper = tmp_path / "helper.py"
    helper.write_text(source, encoding="utf-8")

    with pytest.raises(
        exporter.ValidationError,
        match="dynamic import|server package|builtins namespace",
    ):
        exporter.validate_source("helper", helper)


@pytest.mark.parametrize(
    "source",
    [
        "globals()['__builtins__']['__import__']('subprocess')\n",
        "globals().get('__builtins__')['__import__']('subprocess')\n",
        "dict.get(globals(), '__builtins__')['__import__']('subprocess')\n",
        "dict.__getitem__(globals(), '__builtins__')"
        "['__import__']('subprocess')\n",
        "globals().__getitem__('__builtins__')"
        "['__import__']('subprocess')\n",
        "getattr(globals(), '__getitem__')('__builtins__')"
        "['__import__']('subprocess')\n",
    ],
)
def test_helper_rejects_dynamic_namespace_builtin_lookup(tmp_path, source):
    exporter = _load_export_module()
    helper = tmp_path / "helper.py"
    helper.write_text(source, encoding="utf-8")

    with pytest.raises(exporter.ValidationError, match="dynamic namespace"):
        exporter.validate_source("helper", helper)


def test_helper_allows_static_dynamic_import_within_role_allowlist(tmp_path):
    exporter = _load_export_module()
    helper = tmp_path / "helper.py"
    helper.write_text("__import__('json')\n", encoding="utf-8")

    exporter.validate_source("helper", helper)


def test_helper_allows_keyword_dynamic_import_within_role_allowlist(tmp_path):
    exporter = _load_export_module()
    helper = tmp_path / "helper.py"
    helper.write_text("__import__(name='json')\n", encoding="utf-8")

    exporter.validate_source("helper", helper)


def test_helper_rejects_computed_attribute_mutation_name(tmp_path):
    exporter = _load_export_module()
    helper = tmp_path / "helper.py"
    helper.write_text(
        "field = ''.join(['STRATEGY_RUNTIME_API_', 'VERSION'])\n"
        "setattr(target, field, 2)\n",
        encoding="utf-8",
    )

    with pytest.raises(exporter.ValidationError, match="computed attribute"):
        exporter.validate_source("helper", helper)


def test_helper_rejects_literal_connection_config_suffix(tmp_path):
    exporter = _load_export_module()
    helper = tmp_path / "helper.py"
    helper.write_text("BT_REMOTE_TOKEN = 'hard-coded-real-token'\n", encoding="utf-8")

    with pytest.raises(exporter.ValidationError, match="literal private value"):
        exporter.validate_source("helper", helper)


def test_helper_rejects_literal_dynamic_server_import(tmp_path):
    exporter = _load_export_module()
    helper = tmp_path / "helper.py"
    helper.write_text(
        "__import__('bullet_' + 'trade')\n",
        encoding="utf-8",
    )

    with pytest.raises(exporter.ValidationError, match="server package"):
        exporter.validate_source("helper", helper)


@pytest.mark.parametrize("replacement", ["True", "1.0", "2"])
def test_cross_contract_requires_fixed_exact_runtime_api(tmp_path, replacement):
    exporter = _load_export_module()
    repo = tmp_path / "repo"
    _copy_export_sources(exporter, repo)
    strategy = repo / "strategies" / "joinquant" / "good_etf.py"
    strategy.write_text(
        strategy.read_text(encoding="utf-8").replace(
            "_EXPECTED_RUNTIME_API_VERSION = 1",
            "_EXPECTED_RUNTIME_API_VERSION = {}".format(replacement),
        ),
        encoding="utf-8",
    )
    if replacement == "2":
        helper = repo / "helpers" / "bullet_trade_jq_remote_helper.py"
        helper.write_text(
            helper.read_text(encoding="utf-8").replace(
                "STRATEGY_RUNTIME_API_VERSION: int = 1",
                "STRATEGY_RUNTIME_API_VERSION: int = 2",
            ),
            encoding="utf-8",
        )

    with pytest.raises(exporter.ValidationError, match="API versions"):
        exporter.validate_repository(repo)


@pytest.mark.parametrize("value", [" bad", "bad value", "x" * 129])
def test_cross_contract_rejects_runtime_invalid_identifiers(tmp_path, value):
    exporter = _load_export_module()
    repo = tmp_path / "repo"
    _copy_export_sources(exporter, repo)
    strategy = repo / "strategies" / "joinquant" / "good_etf.py"
    strategy.write_text(
        strategy.read_text(encoding="utf-8").replace(
            "STRATEGY_ID = 'good_etf'", "STRATEGY_ID = {!r}".format(value)
        ),
        encoding="utf-8",
    )

    with pytest.raises(exporter.ValidationError, match="STRATEGY_ID is invalid"):
        exporter.validate_repository(repo)


@pytest.mark.parametrize("value", ["shadow", "LIVE ", "DISABLED", True])
def test_cross_contract_rejects_noncanonical_mode(tmp_path, value):
    exporter = _load_export_module()
    repo = tmp_path / "repo"
    _copy_export_sources(exporter, repo)
    strategy = repo / "strategies" / "joinquant" / "good_etf.py"
    strategy.write_text(
        strategy.read_text(encoding="utf-8").replace(
            "MODE = 'BACKTEST'", "MODE = {!r}".format(value)
        ),
        encoding="utf-8",
    )

    with pytest.raises(exporter.ValidationError, match="MODE is invalid"):
        exporter.validate_repository(repo)


def test_export_uses_one_immutable_source_snapshot(tmp_path, monkeypatch):
    exporter = _load_export_module()
    repo = tmp_path / "repo"
    _copy_export_sources(exporter, repo)
    strategy = repo / "strategies" / "joinquant" / "good_etf.py"
    original = strategy.read_bytes()
    original_validate = exporter._validate_source_data
    mutated = []

    def mutate_after_read(role, data, source_name):
        result = original_validate(role, data, source_name)
        if role == "strategy" and not mutated:
            strategy.write_bytes(data + b"\n# changed after snapshot\n")
            mutated.append(True)
        return result

    monkeypatch.setattr(exporter, "_validate_source_data", mutate_after_read)
    destination = tmp_path / "export"
    manifest = exporter.export_joinquant(repo, destination)

    assert strategy.read_bytes() != original
    assert (destination / "good_etf.py").read_bytes() == original
    strategy_entry = next(
        item for item in manifest["files"] if item["role"] == "strategy"
    )
    assert strategy_entry["sha256"] == exporter._sha256(original)
    assert manifest["contracts"]["runtime_api_version"] == 1


def test_export_rejects_reparse_destination_component(tmp_path, monkeypatch):
    exporter = _load_export_module()
    monkeypatch.setattr(exporter, "_has_reparse_component", lambda path: True)

    with pytest.raises(exporter.ValidationError, match="reparse point"):
        exporter.export_joinquant(ROOT, tmp_path / "export")


def test_reparse_check_rejects_dangling_link_before_exists(tmp_path, monkeypatch):
    exporter = _load_export_module()
    destination = tmp_path / "dangling-link"
    original_is_symlink = exporter.Path.is_symlink

    def dangling_link_model(path):
        if path == destination:
            return True
        return original_is_symlink(path)

    monkeypatch.setattr(exporter.Path, "is_symlink", dangling_link_model)

    assert not destination.exists()
    assert exporter._has_reparse_component(destination) is True


def test_private_profile_is_validated_without_exposing_or_copying_secrets(tmp_path):
    exporter = _load_export_module()
    contracts = exporter.validate_cross_contract(ROOT)
    private_profile = tmp_path / "jq_runtime_config.py"
    private_profile.write_text(
        "PROFILE_SCHEMA_VERSION = 1\n"
        "PROFILES = {'good_etf-prod': {"
        "'strategy_id': 'good_etf', 'host': '10.0.0.8', "
        "'token': 'hard-coded-real-token', 'port': 58620, "
        "'account_key': None, 'sub_account_id': None, 'tls_cert': None, "
        "'retries': 2, 'retry_interval': 0.5, 'rpc_timeout': 60.0, "
        "'place_order_timeout_margin': 30.0, "
        "'default_wait_timeout': 16.0, 'debug': False}}\n",
        encoding="utf-8",
    )

    result = exporter.validate_private_profile(private_profile, contracts)

    assert result == {
        "profile": "good_etf-prod",
        "profile_schema_version": 1,
        "strategy_id": "good_etf",
    }
    assert list(tmp_path.iterdir()) == [private_profile]
    assert "hard-coded-real-token" not in repr(result)


@pytest.mark.parametrize(
    ("body", "message"),
    [
        (
            "PROFILE_SCHEMA_VERSION = 1\n"
            "PROFILES = {'good_etf-prod': {'strategy_id': 'other', "
            "'host': '10.0.0.8', 'token': 'real-token'}}\n",
            "strategy_id does not match",
        ),
        (
            "PROFILE_SCHEMA_VERSION = 1\n"
            "PROFILES = {'good_etf-prod': {'strategy_id': 'good_etf', "
            "'host': '10.0.0.8', 'token': 'real-token', "
            "'credential': 'leak'}}\n",
            "unknown fields",
        ),
        (
            "PROFILE_SCHEMA_VERSION = 1\n"
            "PROFILES = make_profile()\n",
            "literal data",
        ),
        (
            "PROFILE_SCHEMA_VERSION = 1\n"
            "PROFILES = {'good_etf-prod': {'strategy_id': 'good_etf', "
            "'host': '10.0.0.8', 'token': 'real-token', "
            "'retry_interval': None}}\n",
            "invalid retry_interval",
        ),
        (
            "PROFILE_SCHEMA_VERSION = 1\n"
            "PROFILES = {'good_etf-prod': {'strategy_id': 'good_etf', "
            "'host': 'your-host', 'token': 'your-token'}}\n",
            "invalid host",
        ),
        (
            "PROFILE_SCHEMA_VERSION = 1\n"
            "PROFILES = {'good_etf-prod': {'strategy_id': 'good_etf', "
            "'host': '10.0.0.8', 'token': 'replace-me'}}\n",
            "invalid token",
        ),
    ],
)
def test_private_profile_rejects_contract_drift_and_code(tmp_path, body, message):
    exporter = _load_export_module()
    private_profile = tmp_path / "jq_runtime_config.py"
    private_profile.write_text(body, encoding="utf-8")

    with pytest.raises(exporter.ValidationError, match=message):
        exporter.validate_private_profile(
            private_profile, exporter.validate_cross_contract(ROOT)
        )


def test_cli_reports_stable_io_error_on_stderr(monkeypatch, capsys):
    exporter = _load_export_module()

    def fail_export(repo, destination, private_profile=None):
        raise OSError("sensitive local path")

    monkeypatch.setattr(exporter, "export_joinquant", fail_export)
    monkeypatch.setattr(sys, "argv", [str(EXPORT_SCRIPT), "--output", "new-output"])

    assert exporter.main() == 3
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "JOINQUANT_EXPORT_IO_ERROR\n"
    assert "sensitive local path" not in captured.err


def test_cleanroom_import_uses_only_exported_runtime_files(tmp_path):
    exporter = _load_export_module()
    destination = tmp_path / "export"
    exporter.export_joinquant(ROOT, destination)
    code = """
import importlib.util
import runpy
import sys
import types
from pathlib import Path

root = Path({root!r})
sys.path.insert(0, str(root))
jqdata = types.ModuleType('jqdata')
jqdata.__all__ = []
sys.modules['jqdata'] = jqdata
spec = importlib.util.spec_from_file_location('clean_good_etf', root / 'good_etf.py')
assert spec is not None and spec.loader is not None
strategy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(strategy)
helper = sys.modules['bullet_trade_jq_remote_helper']
assert helper.STRATEGY_RUNTIME_API_VERSION == 1
profile = runpy.run_path(str(root / 'jq_runtime_config.example.py'))
assert profile['PROFILE_SCHEMA_VERSION'] == 1
assert profile['PROFILES']['good_etf-prod']['host'] == ''
print('CLEANROOM_IMPORT_OK')
""".format(root=str(destination))

    result = _run_cleanroom(code, isolated=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "CLEANROOM_IMPORT_OK" in result.stdout


def test_cleanroom_missing_helper_fails_shadow_closed(tmp_path):
    exporter = _load_export_module()
    destination = tmp_path / "export"
    exporter.export_joinquant(ROOT, destination)
    strategy_path = destination / "good_etf.py"
    code = """
import importlib.util
import sys
import types

jqdata = types.ModuleType('jqdata')
jqdata.__all__ = []
sys.modules['jqdata'] = jqdata
spec = importlib.util.spec_from_file_location('missing_helper_strategy', {path!r})
strategy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(strategy)
strategy.MODE = 'SHADOW'
context = types.SimpleNamespace(run_params=types.SimpleNamespace(type='sim_trade'))
try:
    strategy._install_runtime(context)
except RuntimeError as exc:
    assert 'bullet_trade_jq_remote_helper.py' in str(exc)
else:
    raise AssertionError('SHADOW accepted missing helper')
print('MISSING_HELPER_FAIL_CLOSED_OK')
""".format(path=str(strategy_path))

    result = _run_cleanroom(code, isolated=True, no_site=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "MISSING_HELPER_FAIL_CLOSED_OK" in result.stdout


def test_cleanroom_version_mismatch_does_not_call_helper(tmp_path):
    exporter = _load_export_module()
    destination = tmp_path / "export"
    exporter.export_joinquant(ROOT, destination)
    strategy_path = destination / "good_etf.py"
    code = """
import importlib.util
import sys
import types

jqdata = types.ModuleType('jqdata')
jqdata.__all__ = []
sys.modules['jqdata'] = jqdata
called = []
helper = types.ModuleType('bullet_trade_jq_remote_helper')
helper.STRATEGY_RUNTIME_HELPER_MARKER = 'bullet-trade-joinquant-runtime-helper-v1'
helper.STRATEGY_RUNTIME_API_VERSION = 2
helper.install_strategy_runtime = lambda *args, **kwargs: called.append(True)
sys.modules['bullet_trade_jq_remote_helper'] = helper
spec = importlib.util.spec_from_file_location('bad_version_strategy', {path!r})
strategy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(strategy)
strategy.MODE = 'SHADOW'
context = types.SimpleNamespace(run_params=types.SimpleNamespace(type='sim_trade'))
try:
    strategy._install_runtime(context)
except RuntimeError as exc:
    assert 'API' in str(exc)
else:
    raise AssertionError('accepted mismatched helper API')
assert called == []
print('VERSION_MISMATCH_FAIL_CLOSED_OK')
""".format(path=str(strategy_path))

    result = _run_cleanroom(code, isolated=True, no_site=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "VERSION_MISMATCH_FAIL_CLOSED_OK" in result.stdout


def test_cleanroom_missing_private_profile_fails_shadow_closed(tmp_path):
    exporter = _load_export_module()
    destination = tmp_path / "export"
    exporter.export_joinquant(ROOT, destination)
    code = """
import importlib.util
import sys
import types
from pathlib import Path

root = Path({root!r})
sys.path.insert(0, str(root))
jqdata = types.ModuleType('jqdata')
jqdata.__all__ = []
sys.modules['jqdata'] = jqdata
sys.modules.pop('jq_runtime_config', None)
spec = importlib.util.spec_from_file_location('missing_profile_strategy', root / 'good_etf.py')
strategy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(strategy)
strategy.MODE = 'SHADOW'
context = types.SimpleNamespace(run_params=types.SimpleNamespace(type='sim_trade'))
try:
    strategy._install_runtime(context)
except RuntimeError as exc:
    assert '运行配置模块' in str(exc)
else:
    raise AssertionError('SHADOW accepted missing private profile')
print('MISSING_PROFILE_FAIL_CLOSED_OK')
""".format(root=str(destination))

    result = _run_cleanroom(code, isolated=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "MISSING_PROFILE_FAIL_CLOSED_OK" in result.stdout


def test_manifest_matches_exported_file_hashes(tmp_path):
    exporter = _load_export_module()
    destination = tmp_path / "export"
    exporter.export_joinquant(ROOT, destination)
    manifest = json.loads((destination / "manifest.json").read_text(encoding="utf-8"))

    assert manifest["schema_version"] == 1
    assert manifest["contracts"] == {
        "mode": "BACKTEST",
        "profile": "good_etf-prod",
        "profile_module": "jq_runtime_config",
        "profile_schema_version": 1,
        "runtime_api_version": 1,
        "runtime_helper_marker": "bullet-trade-joinquant-runtime-helper-v1",
        "strategy_id": "good_etf",
    }
    assert [entry["output"] for entry in manifest["files"]] == [
        spec.output for spec in exporter.ARTIFACTS
    ]
    for entry in manifest["files"]:
        data = (destination / entry["output"]).read_bytes()
        assert len(data) == entry["bytes"]
        assert exporter._sha256(data) == entry["sha256"]
