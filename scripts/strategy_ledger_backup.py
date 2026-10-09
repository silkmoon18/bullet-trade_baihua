"""Small SQLite backup/restore utility for the personal StrategyLedger."""

from __future__ import annotations

import argparse
import os
import re
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Sequence


_AUTOMATIC_BACKUP = re.compile(r"strategy-ledger-auto-(\d{8}-\d{6}-\d{6})\.db")


def prune_automatic_backups(output_dir: Path, retain_days: int) -> int:
    """Remove only marked automatic backups older than the retained calendar days."""
    if type(retain_days) is not int or retain_days < 1:
        raise ValueError("retain_days must be a positive integer")
    directory = output_dir.resolve(strict=True)
    cutoff = datetime.now().date() - timedelta(days=retain_days - 1)
    removed = 0
    for candidate in directory.iterdir():
        match = _AUTOMATIC_BACKUP.fullmatch(candidate.name)
        if match is None or candidate.is_symlink() or not candidate.is_file():
            continue
        try:
            created = datetime.strptime(match.group(1), "%Y%m%d-%H%M%S-%f")
        except ValueError:
            continue
        if created.date() < cutoff and candidate.resolve().parent == directory:
            candidate.unlink()
            removed += 1
    return removed


def _check(connection: sqlite3.Connection) -> None:
    result = connection.execute("PRAGMA quick_check").fetchone()
    if result is None or result[0] != "ok":
        raise RuntimeError("SQLite quick_check failed: {}".format(result))


def backup_database(
    database: Path, output_dir: Path, retain_days: Optional[int] = None
) -> Path:
    if retain_days is not None and (type(retain_days) is not int or retain_days < 1):
        raise ValueError("retain_days must be a positive integer")
    database = database.resolve()
    output_dir = output_dir.resolve()
    if not database.is_file():
        raise FileNotFoundError("ledger database not found: {}".format(database))
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    prefix = "strategy-ledger-auto" if retain_days is not None else "strategy-ledger"
    target = output_dir / "{}-{}.db".format(prefix, stamp)
    temporary = output_dir / ".{}.tmp".format(target.name)
    source = sqlite3.connect(str(database))
    destination = None
    try:
        try:
            destination = sqlite3.connect(str(temporary))
            _check(source)
            source.backup(destination)
            _check(destination)
        finally:
            if destination is not None:
                destination.close()
            source.close()
        os.replace(str(temporary), str(target))
    except BaseException:
        if temporary.exists():
            temporary.unlink()
        raise
    if retain_days is not None:
        # A failed backup must never delete the last usable recovery copies.
        prune_automatic_backups(output_dir, retain_days)
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
    backup_parser.add_argument(
        "--retain-days", type=int,
        help="mark as automatic and retain this many calendar days; manual backups are untouched",
    )
    restore_parser = subparsers.add_parser("restore")
    restore_parser.add_argument("--backup", type=Path, required=True)
    restore_parser.add_argument("--database", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "backup":
        result = backup_database(args.database, args.output_dir, retain_days=args.retain_days)
    else:
        result = restore_database(args.backup, args.database)
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
