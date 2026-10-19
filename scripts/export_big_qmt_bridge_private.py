"""Build a private QMT paste-file; credentials never belong in the public helper.

Reads the existing server env without connecting to a broker, changing a gate,
or starting a client. Writes only inside the chosen ignored .data directory.
"""
from __future__ import annotations

import argparse
import ast
import json
import os
from pathlib import Path

from bullet_trade.server.config import _parse_accounts
from bullet_trade.utils.env_loader import load_env


ROOT = Path(__file__).resolve().parents[1]


def render(source: str, *, account_id: str, token: str, port: int,
           enable_trading: bool = False, enable_cancel: bool = False) -> str:
    if not account_id.strip() or not token.strip() or not 1 <= port <= 65535:
        raise ValueError('Private bridge account/token/port is missing or invalid')
    replacements = dict(ACCOUNT_ID=account_id, BRIDGE_TOKEN=token,
                        ACCOUNT_TYPE='STOCK', BRIDGE_PORT=port,
                        ENABLE_TRADING=enable_trading, ENABLE_CANCEL=enable_cancel)
    lines = source.splitlines(keepends=True)
    seen = set()
    for node in ast.parse(source).body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        name = getattr(node.targets[0], 'id', '')
        if name in replacements:
            if node.lineno != node.end_lineno or name in seen:
                raise ValueError('Unexpected bridge configuration layout')
            lines[node.lineno - 1] = '{} = {}\n'.format(name, repr(replacements[name]))
            seen.add(name)
    if seen != set(replacements):
        raise ValueError('Public bridge configuration fields changed')
    result = ''.join(lines)
    result.encode('ascii')  # The deployed bridge is safe in GBK and UTF-8.
    compile(result, 'private_bridge.py', 'exec')
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env-file', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--project-root', type=Path, default=ROOT)
    parser.add_argument('--template', type=Path)
    parser.add_argument('--account-key')
    parser.add_argument('--enable-trading', action='store_true')
    parser.add_argument('--enable-cancel', action='store_true')
    args = parser.parse_args(argv)
    output = args.output.resolve()
    project_root = args.project_root.resolve()
    private_root = (project_root / '.data').resolve()
    try:
        output.relative_to(private_root)
        inside_private_root = True
    except ValueError:
        inside_private_root = False
    if not inside_private_root or output.suffix.lower() != '.py':
        parser.error('Output must be a .py file inside this checkout\'s ignored .data directory')
    if output.exists():
        parser.error('Output already exists; keep the existing private artifact and choose a new path')
    if not args.env_file.is_file():
        parser.error('The existing server env file was not found')
    load_env(str(args.env_file.resolve()), override=True, verbose=False)
    accounts = _parse_accounts(os.environ.get('QMT_SERVER_ACCOUNTS'))
    if accounts:
        key = args.account_key
        if key is None and len(accounts) == 1:
            key = next(iter(accounts))
        if key not in accounts:
            parser.error('Multiple accounts or unknown account key; select the intended --account-key explicitly')
        selected = accounts[key]
        if selected.account_type.lower() != 'stock':
            parser.error('The selected server account is not STOCK')
        account_id = selected.account_id
    else:
        if args.account_key not in (None, 'default'):
            parser.error('Unknown account key')
        if os.environ.get('QMT_ACCOUNT_TYPE', 'stock').lower() != 'stock':
            parser.error('The server account is not STOCK')
        account_id = os.environ.get('QMT_ACCOUNT_ID', '').strip()
    try:
        template = args.template or project_root / 'helpers/big_qmt_bridge.py'
        result = render(template.read_text(encoding='ascii'),
                        account_id=account_id,
                        token=os.environ.get('BIG_QMT_GATEWAY_PASSWORD', ''),
                        port=int(os.environ.get('BIG_QMT_BRIDGE_PORT', '9001')),
                        enable_trading=args.enable_trading, enable_cancel=args.enable_cancel)
    except (ValueError, SyntaxError):
        parser.error('Could not validate private bridge settings; no credential values were printed')
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('xb') as stream:
        stream.write(result.encode('ascii'))
    print(json.dumps({'private_file': str(output), 'credentials_filled': True,
                      'trading': args.enable_trading, 'cancel': args.enable_cancel,
                      'client_started': False}))


if __name__ == '__main__':
    main()
