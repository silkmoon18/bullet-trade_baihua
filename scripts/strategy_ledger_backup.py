"""Small SQLite backup/restore utility for the personal StrategyLedger."""

from __future__ import annotations

import argparse
import os
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional, Sequence


def _check(connection: sqlite3.Connection) -> None:
    result = connection.execute("PRAGMA quick_check").fetchone()
    if result is None or result[0] != "ok":
        raise RuntimeError("SQLite quick_check failed: {}".format(result))


def backup_database(database: Path, output_dir: Path) -> Path:
    database = database.resolve()
    output_dir = output_dir.resolve()
    if not database.is_file():
        raise FileNotFoundError("ledger database not found: {}".format(database))
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    target = output_dir / "strategy-ledger-{}.db".format(stamp)
    temporary = output_dir / ".{}.tmp".format(target.name)
    source = sqlite3.connect(str(database))
    destination = sqlite3.connect(str(temporary))
    try:
        _check(source)
        source.backup(destination)
        _check(destination)
    finally:
        destination.close()
        source.close()
    os.replace(str(temporary), str(target))
    return target


def restore_database(backup: Path, database: Path) -> Path:
    backup = backup.resolve()
    database = database.resolve()
    if not backup.is_file():
        raise FileNotFoundError("backup not found: {}".format(backup))
    if database.exists():
        raise FileExistsError(
            "restore target already exists; stop the server and move it aside first"
        )
    database.parent.mkdir(parents=True, exist_ok=True)
    source = sqlite3.connect(str(backup))
    destination = sqlite3.connect(str(database))
    try:
        _check(source)
        source.backup(destination)
        _check(destination)
    except BaseException:
        destination.close()
        source.close()
        if database.exists():
            database.unlink()
        raise
    destination.close()
    source.close()
    return database


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    backup_parser = subparsers.add_parser("backup")
    backup_parser.add_argument("--database", type=Path, required=True)
    backup_parser.add_argument("--output-dir", type=Path, required=True)
    restore_parser = subparsers.add_parser("restore")
    restore_parser.add_argument("--backup", type=Path, required=True)
    restore_parser.add_argument("--database", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "backup":
        result = backup_database(args.database, args.output_dir)
    else:
        result = restore_database(args.backup, args.database)
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
