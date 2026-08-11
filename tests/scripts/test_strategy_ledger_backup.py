import sqlite3

import pytest

from scripts.strategy_ledger_backup import backup_database, restore_database


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
