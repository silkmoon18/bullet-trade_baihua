import sqlite3
from datetime import datetime
from pathlib import Path

import pytest

from scripts.strategy_ledger_backup import backup_database, restore_database
from scripts import strategy_ledger_backup as backup_module


@pytest.fixture
def fixed_clock(monkeypatch):
    class FixedDateTime(datetime):
        @classmethod
        def now(cls):
            return cls(2026, 10, 8, 12, 0, 0)

    monkeypatch.setattr(backup_module, "datetime", FixedDateTime)


def _database(path):
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE sample(value TEXT)")
        connection.execute("INSERT INTO sample VALUES ('retained')")
    return path


def test_backup_and_restore_round_trip(tmp_path):
    database = tmp_path / "ledger.db"
    connection = sqlite3.connect(database)
    connection.execute("CREATE TABLE sample(value TEXT NOT NULL)")
    connection.execute("INSERT INTO sample VALUES ('real-fill')")
    connection.commit()
    connection.close()

    backup = backup_database(database, tmp_path / "backups")
    restored = restore_database(backup, tmp_path / "restored" / "ledger.db")

    connection = sqlite3.connect(restored)
    try:
        assert connection.execute("SELECT value FROM sample").fetchone()[0] == "real-fill"
        assert connection.execute("PRAGMA quick_check").fetchone()[0] == "ok"
    finally:
        connection.close()


def test_restore_refuses_to_overwrite_existing_database(tmp_path):
    backup = tmp_path / "backup.db"
    sqlite3.connect(backup).close()
    target = tmp_path / "ledger.db"
    target.write_bytes(b"keep-me")

    with pytest.raises(FileExistsError, match="move it aside"):
        restore_database(backup, target)

    assert target.read_bytes() == b"keep-me"


def test_retention_only_removes_marked_automatic_files_in_calendar_window(tmp_path, fixed_clock):
    expired = tmp_path / "strategy-ledger-auto-20261001-180000-000001.db"
    retained_names = (
        "strategy-ledger-auto-20261002-000000-000001.db",
        "strategy-ledger-auto-20261009-180000-000001.db",
        "strategy-ledger-20260901-180000-000001.db",
        "strategy-ledger-pre-real-20260901.sqlite",
        "strategy-ledger-auto-20261301-180000-000001.db",
        "strategy-ledger.db",
        ".strategy-ledger-auto-20260901-180000-000001.db.tmp",
    )
    expired.write_bytes(b"expired")
    for name in retained_names:
        (tmp_path / name).write_bytes(b"keep")
    nested = tmp_path / "manual-snapshot"
    nested.mkdir()
    nested_file = nested / expired.name
    nested_file.write_bytes(b"keep nested")

    assert backup_module.prune_automatic_backups(tmp_path, 7) == 1
    assert not expired.exists()
    assert all((tmp_path / name).read_bytes() == b"keep" for name in retained_names)
    assert nested_file.read_bytes() == b"keep nested"


def test_automatic_backup_is_checked_before_pruning(tmp_path, fixed_clock):
    output = tmp_path / "backups"
    output.mkdir()
    expired = output / "strategy-ledger-auto-20260901-180000-000001.db"
    expired.write_bytes(b"expired")

    backup = backup_database(_database(tmp_path / "ledger.db"), output, retain_days=7)

    assert backup.name.startswith("strategy-ledger-auto-20261008-")
    assert not expired.exists()
    with sqlite3.connect(backup) as connection:
        assert connection.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        assert connection.execute("SELECT value FROM sample").fetchone()[0] == "retained"


def test_manual_backup_does_not_expire_automatic_or_manual_copies(tmp_path, fixed_clock):
    output = tmp_path / "backups"
    output.mkdir()
    old = output / "strategy-ledger-auto-20260901-180000-000001.db"
    old.write_bytes(b"keep")

    backup = backup_database(_database(tmp_path / "ledger.db"), output)

    assert backup.name.startswith("strategy-ledger-20261008-")
    assert old.read_bytes() == b"keep"


def test_failed_backup_keeps_old_copies_and_removes_its_temporary_file(
    tmp_path, fixed_clock, monkeypatch
):
    output = tmp_path / "backups"
    output.mkdir()
    old = output / "strategy-ledger-auto-20260901-180000-000001.db"
    old.write_bytes(b"keep")
    database = _database(tmp_path / "ledger.db")

    def fail_check(connection):
        raise RuntimeError("backup verification failed")

    monkeypatch.setattr(backup_module, "_check", fail_check)
    with pytest.raises(RuntimeError, match="verification failed"):
        backup_database(database, output, retain_days=7)

    assert list(output.iterdir()) == [old]
    assert old.read_bytes() == b"keep"


@pytest.mark.parametrize("retain_days", [0, -1, True, 1.5])
def test_invalid_retention_is_rejected_before_creating_files(tmp_path, retain_days):
    output = tmp_path / "backups"
    with pytest.raises(ValueError, match="positive integer"):
        backup_database(tmp_path / "missing.db", output, retain_days=retain_days)
    assert not output.exists()


def test_cli_accepts_retention_and_uses_automatic_filename(tmp_path, fixed_clock):
    output = tmp_path / "backups"
    assert backup_module.main([
        "backup", "--database", str(_database(tmp_path / "ledger.db")),
        "--output-dir", str(output), "--retain-days", "7",
    ]) == 0
    assert len(list(output.glob("strategy-ledger-auto-*.db"))) == 1


def test_scheduled_backup_defaults_to_seven_days():
    script = Path(__file__).resolve().parents[2] / "scripts/windows/register_strategy_tasks.ps1"
    source = script.read_text(encoding="utf-8")
    assert "[int]$BackupRetainDays = 7" in source
    assert "--retain-days $BackupRetainDays" in source
