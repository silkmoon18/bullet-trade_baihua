import sqlite3

import pytest

from bullet_trade.server.strategy.schema import (
    LATEST_SCHEMA_VERSION,
    MIGRATIONS,
    Migration,
    SchemaMigrationError,
    apply_migrations,
    connect_database,
    migration_checksum,
    migrate_database,
)


EXPECTED_TABLES = {
    "broker_order_history",
    "broker_trade_history",
    "capital_flows",
    "cash_pools",
    "corporate_actions",
    "fills",
    "ledger_entries",
    "outbox",
    "physical_accounts",
    "portfolio_intents",
    "position_lots",
    "positions",
    "reconciliation_runs",
    "schema_migrations",
    "strategy_accounts",
    "strategy_events",
    "strategy_orders",
    "strategy_operations",
}


def _table_names(connection):
    rows = connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    return {row[0] for row in rows}


def _insert_account(connection):
    connection.execute(
        """
        INSERT INTO physical_accounts(
            physical_account_id, broker_kind, broker_account_ref, status, created_at, updated_at
        ) VALUES (
            'qmt-main', 'QMT', 'account-1', 'READY',
            '2026-08-10T09:00:00+08:00', '2026-08-10T09:00:00+08:00'
        )
        """
    )
    connection.execute(
        """
        INSERT INTO strategy_accounts(
            strategy_account_id, strategy_id, physical_account_id,
            initial_capital_units, cash_units, reserved_cash_units,
            ledger_version, event_seq, status, created_at, updated_at
        ) VALUES (
            'good-etf', 'good_etf', 'qmt-main',
            100000000, 100000000, 0,
            0, 0, 'ACTIVE', '2026-08-10T09:00:00+08:00', '2026-08-10T09:00:00+08:00'
        )
        """
    )


def test_empty_database_migrates_and_repeat_is_noop(tmp_path):
    database = tmp_path / "ledger.db"
    assert migrate_database(database) == LATEST_SCHEMA_VERSION
    assert migrate_database(database) == LATEST_SCHEMA_VERSION
    connection = connect_database(database)
    try:
        assert _table_names(connection) == EXPECTED_TABLES
        assert connection.execute("PRAGMA user_version").fetchone()[0] == LATEST_SCHEMA_VERSION
        versions = connection.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall()
        assert [row[0] for row in versions] == [1, 2, 3, 4, 5]
        assert connection.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        assert connection.execute("PRAGMA synchronous").fetchone()[0] == 2
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    finally:
        connection.close()


def test_version_one_database_upgrades_to_latest(tmp_path):
    database = tmp_path / "old.db"
    assert migrate_database(database, target_version=1) == 1
    connection = connect_database(database)
    try:
        assert "strategy_orders" not in _table_names(connection)
    finally:
        connection.close()
    assert migrate_database(database) == LATEST_SCHEMA_VERSION
    connection = connect_database(database)
    try:
        assert "strategy_orders" in _table_names(connection)
        assert connection.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0] == 5
    finally:
        connection.close()


def test_database_constraints_reject_invalid_balances_state_and_float(tmp_path):
    connection = connect_database(tmp_path / "constraints.db")
    try:
        apply_migrations(connection)
        _insert_account(connection)
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE strategy_accounts SET reserved_cash_units = cash_units + 1 "
                "WHERE strategy_account_id = 'good-etf'"
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE strategy_accounts SET cash_units = 1.5 "
                "WHERE strategy_account_id = 'good-etf'"
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE strategy_accounts SET status = 'UNKNOWN' "
                "WHERE strategy_account_id = 'good-etf'"
            )
    finally:
        connection.close()


def test_failed_migration_rolls_back_its_schema_changes():
    connection = connect_database(":memory:")
    broken = (
        Migration(
            1,
            "broken",
            (
                "CREATE TABLE should_rollback (id INTEGER PRIMARY KEY)",
                "CREATE TABLE invalid SQL",
            ),
        ),
    )
    try:
        with pytest.raises(SchemaMigrationError, match="migration 1"):
            apply_migrations(connection, migrations=broken)
        assert "should_rollback" not in _table_names(connection)
        assert connection.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0] == 0
    finally:
        connection.close()


def test_migration_history_mismatch_is_rejected(tmp_path):
    connection = connect_database(tmp_path / "tampered.db")
    try:
        apply_migrations(connection, target_version=1)
        connection.execute("UPDATE schema_migrations SET name = 'wrong' WHERE version = 1")
        with pytest.raises(SchemaMigrationError, match="does not match"):
            apply_migrations(connection, migrations=MIGRATIONS)
    finally:
        connection.close()


def test_migration_sql_drift_is_rejected_even_when_name_is_unchanged():
    connection = connect_database(":memory:")
    original = (Migration(1, "same", ("CREATE TABLE sample (a INTEGER)",)),)
    changed = (Migration(1, "same", ("CREATE TABLE sample (a INTEGER, b INTEGER)",)),)
    try:
        apply_migrations(connection, migrations=original)
        stored = connection.execute(
            "SELECT checksum FROM schema_migrations WHERE version = 1"
        ).fetchone()[0]
        assert stored == migration_checksum(original[0])
        with pytest.raises(SchemaMigrationError, match="checksum"):
            apply_migrations(connection, migrations=changed)
        columns = connection.execute("PRAGMA table_info(sample)").fetchall()
        assert [column[1] for column in columns] == ["a"]
    finally:
        connection.close()


def test_user_version_must_match_migration_history(tmp_path):
    connection = connect_database(tmp_path / "user-version.db")
    try:
        apply_migrations(connection, target_version=1)
        connection.execute("PRAGMA user_version = 0")
        with pytest.raises(SchemaMigrationError, match="user_version"):
            apply_migrations(connection)
    finally:
        connection.close()
