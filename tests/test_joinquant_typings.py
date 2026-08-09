import ast
import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


def _module(path):
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _literal_all(path):
    tree = _module(path)
    for node in tree.body:
        if isinstance(node, ast.Assign):
            if any(isinstance(target, ast.Name) and target.id == "__all__" for target in node.targets):
                return set(ast.literal_eval(node.value))
    raise AssertionError("missing literal __all__ in {}".format(path))


def _stub_exports(path):
    exports = set()
    for node in _module(path).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if not node.name.startswith("_"):
                exports.add(node.name)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if not node.target.id.startswith("_"):
                exports.add(node.target.id)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and not target.id.startswith("_"):
                    exports.add(target.id)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                if alias.asname == alias.name and not alias.name.startswith("_"):
                    exports.add(alias.name)
    return exports


def _argument_shapes(args):
    positional = list(args.posonlyargs) + list(args.args)
    first_default = len(positional) - len(args.defaults)
    shape = []
    for index, arg in enumerate(args.posonlyargs):
        shape.append(("posonly", arg.arg, index >= first_default))
    offset = len(args.posonlyargs)
    for index, arg in enumerate(args.args, start=offset):
        shape.append(("positional", arg.arg, index >= first_default))
    if args.vararg is not None:
        shape.append(("vararg", args.vararg.arg, False))
    for arg, default in zip(args.kwonlyargs, args.kw_defaults):
        shape.append(("kwonly", arg.arg, default is not None))
    if args.kwarg is not None:
        shape.append(("kwarg", args.kwarg.arg, False))
    return tuple(shape)


def _function_shapes(path):
    result = {}
    for node in _module(path).body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        result[node.name] = _argument_shapes(node.args)
    return result


def _class_method_shapes(path):
    result = {}
    for node in _module(path).body:
        if not isinstance(node, ast.ClassDef):
            continue
        methods = {}
        for child in node.body:
            if not isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if child.name.startswith("_") and child.name != "__init__":
                continue
            methods[child.name] = _argument_shapes(child.args)
        result[node.name] = methods
    return result


def _typed_dict_fields(path, class_names):
    fields = {}
    for node in _module(path).body:
        if not isinstance(node, ast.ClassDef) or node.name not in class_names:
            continue
        for child in node.body:
            if isinstance(child, ast.AnnAssign) and isinstance(child.target, ast.Name):
                fields[child.target.id] = child.annotation
    return fields


def _is_tuple_of_str_ellipsis(annotation):
    if not isinstance(annotation, ast.Subscript):
        return False
    if not isinstance(annotation.value, ast.Name) or annotation.value.id != "Tuple":
        return False
    slice_node = annotation.slice
    if isinstance(slice_node, ast.Index):
        slice_node = slice_node.value
    return (
        isinstance(slice_node, ast.Tuple)
        and len(slice_node.elts) == 2
        and isinstance(slice_node.elts[0], ast.Name)
        and slice_node.elts[0].id == "str"
        and isinstance(slice_node.elts[1], ast.Constant)
        and slice_node.elts[1].value is Ellipsis
    )


def _runtime_state_fields(path):
    tree = _module(path)
    builder = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "_build_strategy_runtime_state"
    )
    fields = set()
    for node in ast.walk(builder):
        if not isinstance(node, ast.Dict):
            continue
        for key in node.keys:
            if isinstance(key, ast.Constant) and isinstance(key.value, str):
                fields.add(key.value)
    return fields


def test_jqdata_stub_exports_match_runtime_contract():
    runtime = _literal_all(ROOT / "bullet_trade" / "compat" / "jqdata.py")
    stub = _stub_exports(ROOT / "jqdata.pyi")
    assert stub == runtime


def test_helper_stub_exports_match_runtime_contract():
    runtime = _literal_all(ROOT / "helpers" / "bullet_trade_jq_remote_helper.py")
    stub = _stub_exports(ROOT / "helpers" / "bullet_trade_jq_remote_helper.pyi")
    assert stub == runtime


def test_helper_critical_signatures_do_not_drift():
    runtime = _function_shapes(ROOT / "helpers" / "bullet_trade_jq_remote_helper.py")
    stub = _function_shapes(ROOT / "helpers" / "bullet_trade_jq_remote_helper.pyi")
    exported = _literal_all(ROOT / "helpers" / "bullet_trade_jq_remote_helper.py")
    guarded = exported & runtime.keys()
    assert guarded <= runtime.keys()
    assert guarded <= stub.keys()
    assert {name: stub[name] for name in guarded} == {
        name: runtime[name] for name in guarded
    }


def test_helper_public_class_method_signatures_do_not_drift():
    path = ROOT / "helpers" / "bullet_trade_jq_remote_helper.py"
    stub_path = ROOT / "helpers" / "bullet_trade_jq_remote_helper.pyi"
    runtime = _class_method_shapes(path)
    stub = _class_method_shapes(stub_path)
    exported = _literal_all(path)
    guarded_classes = exported & runtime.keys()
    for class_name in guarded_classes:
        assert stub[class_name] == runtime[class_name]


def test_helper_runtime_state_fields_match_builder():
    runtime_path = ROOT / "helpers" / "bullet_trade_jq_remote_helper.py"
    stub_path = ROOT / "helpers" / "bullet_trade_jq_remote_helper.pyi"
    stub_fields = _typed_dict_fields(
        stub_path,
        {"_StrategyRuntimeRequiredState", "_StrategyRuntimeOptionalState"},
    )
    assert set(stub_fields) == _runtime_state_fields(runtime_path)
    assert _is_tuple_of_str_ellipsis(stub_fields["blocked_mutations"])


def test_jqdata_strategy_signatures_do_not_drift():
    stub = _function_shapes(ROOT / "jqdata.pyi")
    sources = {}
    for relative in (
        "bullet_trade/core/orders.py",
        "bullet_trade/core/api.py",
        "bullet_trade/core/settings.py",
        "bullet_trade/core/scheduler.py",
        "bullet_trade/data/api.py",
    ):
        sources.update(_function_shapes(ROOT / relative))
    guarded = {
        "order",
        "order_value",
        "order_target",
        "order_target_value",
        "cancel_order",
        "cancel_all_orders",
        "get_open_orders",
        "get_orders",
        "get_trades",
        "set_benchmark",
        "set_option",
        "set_order_cost",
        "set_slippage",
        "run_daily",
        "run_weekly",
        "run_monthly",
        "unschedule_all",
        "history",
        "attribute_history",
        "get_extras",
        "get_current_data",
        "get_all_securities",
    }
    assert guarded <= sources.keys()
    assert guarded <= stub.keys()
    assert {name: stub[name] for name in guarded} == {
        name: sources[name] for name in guarded
    }


def test_joinquant_type_configs_are_isolated_and_strict():
    mypy_config = (ROOT / "mypy.joinquant.ini").read_text(encoding="utf-8")
    pyright_config = (ROOT / "pyrightconfig.joinquant.json").read_text(encoding="utf-8")
    assert "strict = True" in mypy_config
    assert "ignore_missing_imports = False" in mypy_config
    assert "strategies/joinquant/good_etf.py" in mypy_config
    assert '"typeCheckingMode": "strict"' in pyright_config
    assert '"pythonVersion": "3.8"' in pyright_config


def test_strategy_typing_import_is_runtime_guarded():
    tree = _module(ROOT / "strategies" / "joinquant" / "good_etf.py")
    guarded_import = False
    for node in tree.body:
        if not isinstance(node, ast.If):
            continue
        if isinstance(node.test, ast.Name) and node.test.id == "TYPE_CHECKING":
            guarded_import = any(
                isinstance(child, ast.ImportFrom)
                and child.module == "joinquant_typing"
                for child in node.body
            )
    assert guarded_import


def test_setup_script_installs_repo_and_helper_source_paths():
    setup_source = (ROOT / "scripts" / "setup_joinquant_dev.py").read_text(
        encoding="utf-8"
    )
    assert "bullet_trade_joinquant_dev.pth" in setup_source
    assert 'os.path.join(repo, "helpers")' in setup_source
    assert '"--no-binary=mypy"' in setup_source
    assert '"pip>=21.3"' in setup_source


def _load_setup_module():
    path = ROOT / "scripts" / "setup_joinquant_dev.py"
    spec = importlib.util.spec_from_file_location("setup_joinquant_dev_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_setup_script_rejects_non_venv_and_mismatched_prefix(tmp_path):
    setup = _load_setup_module()
    target = tmp_path / "target"
    with pytest.raises(RuntimeError, match="not a virtual environment"):
        setup._validate_venv_metadata(str(target), str(target), str(target))
    with pytest.raises(RuntimeError, match="does not match"):
        setup._validate_venv_metadata(
            str(target), str(tmp_path / "different"), str(tmp_path / "base")
        )


def test_setup_script_installs_pth_in_target_purelib(tmp_path, monkeypatch):
    setup = _load_setup_module()
    target = tmp_path / "target"
    purelib = target / "Lib" / "site-packages"
    purelib.mkdir(parents=True)
    monkeypatch.setattr(
        setup.subprocess,
        "check_output",
        lambda *args, **kwargs: str(purelib),
    )

    repo = tmp_path / "repo"
    setup._install_source_paths("python", str(repo), str(target))

    path_file = purelib / "bullet_trade_joinquant_dev.pth"
    assert path_file.read_text(encoding="utf-8").splitlines() == [
        str(repo),
        str(repo / "helpers"),
    ]


def test_setup_script_rejects_pth_outside_target_venv(tmp_path, monkeypatch):
    setup = _load_setup_module()
    target = tmp_path / "target"
    outside = tmp_path / "outside"
    monkeypatch.setattr(
        setup.subprocess,
        "check_output",
        lambda *args, **kwargs: str(outside),
    )

    with pytest.raises(RuntimeError, match="outside target virtual environment"):
        setup._install_source_paths("python", str(tmp_path / "repo"), str(target))

    assert not setup._is_within(str(tmp_path / "outside"), str(target))
