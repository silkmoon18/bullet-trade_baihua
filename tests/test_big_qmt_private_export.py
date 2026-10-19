"""Synthetic credentials only. No server calls, environment changes or client launch."""
import ast
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('private_bridge_export', ROOT / 'scripts/export_big_qmt_bridge_private.py')
exporter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(exporter)


def test_private_render_fills_only_declared_config_and_keeps_gates_explicit():
    source = (ROOT / 'helpers/big_qmt_bridge.py').read_text(encoding='ascii')
    rendered = exporter.render(source, account_id='test-account', token='test-token', port=9001)
    values = {n.targets[0].id: ast.literal_eval(n.value) for n in ast.parse(rendered).body
              if isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Name)
              and isinstance(n.value, ast.Constant)}
    assert values['ACCOUNT_ID'] == 'test-account' and values['BRIDGE_TOKEN'] == 'test-token'
    assert values['ENABLE_TRADING'] is False and values['ENABLE_CANCEL'] is False
    public_functions = [ast.dump(n) for n in ast.parse(source).body if isinstance(n, (ast.FunctionDef, ast.ClassDef))]
    assert public_functions == [ast.dump(n) for n in ast.parse(rendered).body if isinstance(n, (ast.FunctionDef, ast.ClassDef))]
    assert 'test-account' not in source and 'test-token' not in source


def test_private_output_is_ignored_and_never_prints_credential(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(exporter, 'ROOT', tmp_path)
    (tmp_path / 'helpers').mkdir()
    (tmp_path / 'helpers/big_qmt_bridge.py').write_bytes((ROOT / 'helpers/big_qmt_bridge.py').read_bytes())
    for key in ('QMT_SERVER_ACCOUNTS', 'QMT_ACCOUNT_ID', 'QMT_ACCOUNT_TYPE', 'BIG_QMT_GATEWAY_PASSWORD', 'BIG_QMT_BRIDGE_PORT'):
        monkeypatch.setenv(key, '')
    env = tmp_path / 'server.env'
    env.write_text('QMT_ACCOUNT_ID=test-account\nQMT_ACCOUNT_TYPE=STOCK\nBIG_QMT_GATEWAY_PASSWORD=test-secret\nBIG_QMT_BRIDGE_PORT=9001\n', encoding='utf8')
    output = tmp_path / '.data/private/BT_BRIDGE_REAL.py'
    exporter.main(['--env-file', str(env), '--output', str(output), '--enable-trading', '--enable-cancel'])
    text = capsys.readouterr().out
    assert output.is_file() and 'test-secret' in output.read_text(encoding='ascii')
    assert 'test-secret' not in text and 'test-account' not in text
    assert 'ENABLE_TRADING = True' in output.read_text(encoding='ascii')
    with pytest.raises(SystemExit):
        exporter.main(['--env-file', str(env), '--output', str(output)])
    with pytest.raises(SystemExit):
        exporter.main(['--env-file', str(env), '--output', str(tmp_path / 'public.py')])
