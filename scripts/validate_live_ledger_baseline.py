#!/usr/bin/env python3
"""Validate the local-only S00 migration baseline without storing old secrets."""

import argparse
import ast
import re
import subprocess
import sys
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_UPSTREAM_HEAD = "be0451be09b1de3516d3959e70008031824103cb"
EXPECTED_SOURCE_BLOBS = {
    "jq_platform/good_etf.py": "39c165cd3eaead36345ed6d87c652c527595ae05",
    "jq_platform/bullet_trade_jq_remote_helper.py": "cbd7644e868bde4fb99bfc0ef72575d2aaf65939",
}
LOCAL_HOSTS = {"", "127.0.0.1", "localhost", "REPLACE_ME", "your.server.ip"}
SECRET_NAMES = {"BT_REMOTE_TOKEN", "FEISHU_WEBHOOK_URL", "BT_REMOTE_HOST"}


class CheckFailure(RuntimeError):
    pass


def _run(
    args: Sequence[str],
    *,
    cwd: Path = ROOT,
    check: bool = True,
) -> subprocess.CompletedProcess:
    result = subprocess.run(
        list(args),
        cwd=str(cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if check and result.returncode != 0:
        rendered = " ".join(args[:3])
        raise CheckFailure("command failed ({}): {}".format(result.returncode, rendered))
    return result


def _iter_files(paths: Iterable[Path], suffixes: Sequence[str]) -> Iterable[Path]:
    for path in paths:
        if path.is_file() and path.suffix in suffixes:
            yield path
        elif path.is_dir():
            for candidate in path.rglob("*"):
                if candidate.is_file() and candidate.suffix in suffixes:
                    yield candidate


def check_python_syntax() -> None:
    strategy = ROOT / "strategies" / "joinquant" / "good_etf.py"
    compile(strategy.read_text(encoding="utf-8"), str(strategy), "exec")


def check_markdown_links() -> None:
    docs_root = ROOT / "docs" / "live-ledger"
    pattern = re.compile(r"\[[^\]]+\]\(([^)]+)\)")
    missing: List[str] = []
    for doc in docs_root.glob("*.md"):
        for target in pattern.findall(doc.read_text(encoding="utf-8")):
            if target.startswith(("http://", "https://", "#")):
                continue
            relative = target.split("#", 1)[0]
            if relative and not (doc.parent / relative).exists():
                missing.append("{} -> {}".format(doc.relative_to(ROOT), target))
    if missing:
        raise CheckFailure("missing Markdown links: {}".format(", ".join(missing)))


def _assigned_strings(source: str) -> Dict[str, str]:
    values: Dict[str, str] = {}
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Name) or target.id not in SECRET_NAMES:
            continue
        value = node.value
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            values[target.id] = value.value
    return values


def check_current_tree_secrets() -> None:
    risky_urls = re.compile(r"https?://[^\s'\"]+(?:hook|webhook)[^\s'\"]{12,}", re.I)
    ipv4 = re.compile(r"(?<!\d)(?:\d{1,3}\.){3}\d{1,3}(?!\d)")
    findings: List[str] = []
    scan_roots = [ROOT / "strategies" / "joinquant", ROOT / "docs" / "live-ledger"]
    for path in _iter_files(scan_roots, (".py", ".md")):
        text = path.read_text(encoding="utf-8")
        if risky_urls.search(text):
            findings.append("{}: webhook-like URL".format(path.relative_to(ROOT)))
        for match in ipv4.findall(text):
            if match != "127.0.0.1":
                findings.append("{}: non-loopback IPv4".format(path.relative_to(ROOT)))
        if path.suffix == ".py":
            for name, value in _assigned_strings(text).items():
                if name in {"BT_REMOTE_TOKEN", "FEISHU_WEBHOOK_URL"} and value:
                    findings.append("{}: non-empty {}".format(path.relative_to(ROOT), name))
                if name == "BT_REMOTE_HOST" and value not in LOCAL_HOSTS:
                    findings.append("{}: non-local BT_REMOTE_HOST".format(path.relative_to(ROOT)))
    if findings:
        raise CheckFailure("current tree secret scan failed: {}".format("; ".join(findings)))


def check_ignore_rules() -> None:
    paths = [
        "runtime/state.json",
        "runtime/strategy-ledger.sqlite3-wal",
        "dist/joinquant/good_etf.py",
        "jq_runtime/jq_runtime_config.py",
        "jq_runtime/prod.local.py",
        ".idea/workspace.xml",
    ]
    for path in paths:
        result = _run(["git", "check-ignore", "-q", path], check=False)
        if result.returncode != 0:
            raise CheckFailure("expected ignored path: {}".format(path))


def check_upstream() -> None:
    fetch_url = _run(["git", "remote", "get-url", "upstream"]).stdout.strip()
    push_url = _run(["git", "remote", "get-url", "--push", "upstream"]).stdout.strip()
    if fetch_url != "https://github.com/BulletTrade/bullet-trade.git":
        raise CheckFailure("unexpected upstream fetch URL")
    if push_url != "DISABLED":
        raise CheckFailure("upstream push must be disabled")
    head = _run(["git", "rev-parse", "upstream/main"]).stdout.strip()
    if head != EXPECTED_UPSTREAM_HEAD:
        raise CheckFailure("upstream/main is not the reviewed v0.9.2 commit")
    tag = _run(["git", "describe", "--tags", "--exact-match", "upstream/main"]).stdout.strip()
    if tag != "v0.9.2":
        raise CheckFailure("upstream/main is not tagged v0.9.2")


def check_source_checkpoint(bt_quant: Path) -> None:
    for relative, expected in EXPECTED_SOURCE_BLOBS.items():
        actual = _run(
            ["git", "rev-parse", "e6462dd:{}".format(relative)], cwd=bt_quant
        ).stdout.strip()
        if actual != expected:
            raise CheckFailure("source checkpoint blob mismatch: {}".format(relative))


def check_reachable_history_for_old_values(bt_quant: Path) -> None:
    source = _run(
        ["git", "show", "e6462dd:jq_platform/good_etf.py"], cwd=bt_quant
    ).stdout
    values = _assigned_strings(source)
    commits = _run(["git", "rev-list", "v0.9.2..HEAD"]).stdout.splitlines()
    for name, value in values.items():
        if not value or value in LOCAL_HOSTS:
            continue
        for commit in commits:
            result = _run(
                ["git", "grep", "-F", "-l", "-e", value, commit, "--", "."],
                check=False,
            )
            if result.returncode == 0:
                raise CheckFailure(
                    "reachable history contains old {} at commit {}".format(name, commit[:12])
                )
            if result.returncode not in (0, 1):
                raise CheckFailure("history scan failed for {}".format(name))


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--bt-quant",
        type=Path,
        help="Path to the preserved bt_quant repository for source/hash/history checks.",
    )
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    try:
        check_python_syntax()
        check_markdown_links()
        check_current_tree_secrets()
        check_ignore_rules()
        check_upstream()
        if args.bt_quant:
            source_repo = args.bt_quant.resolve()
            check_source_checkpoint(source_repo)
            check_reachable_history_for_old_values(source_repo)
    except (CheckFailure, OSError, SyntaxError, ValueError) as exc:
        print("S00_BASELINE_CHECK_FAILED: {}".format(exc), file=sys.stderr)
        return 1
    print("S00_BASELINE_CHECK_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
