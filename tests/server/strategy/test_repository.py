import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from bullet_trade.server.strategy.repository import (
    LedgerInvariantError,
    RepositoryError,
    SQLiteStrategyRepository,
    VersionConflictError,
)
from bullet_trade.server.strategy.schema import connect_database


INITIAL_CAPITAL = 100_000_000


@pytest.fixture
def repository(tmp_path):
    repo = SQLiteStrategyRepository(tmp_path / "repository.db")
    repo.initialize()
    repo.create_physical_account(
        "qmt-main",
        "QMT",
        "account-1",
        unallocated_cash_units=500_000_000,
    )
    repo.create_strategy_account(
        "good-etf",
        "good_etf",
        "qmt-main",
        INITIAL_CAPITAL,
    )
    return repo


def test_strategy_account_creation_allocates_from_physical_cash_pool(tmp_path):
    repo = SQLiteStrategyRepository(tmp_path / "allocation.db")
    repo.initialize()
    repo.create_physical_account(
        "qmt-main",
        "QMT",
        "account-1",
        unallocated_cash_units=500_000_000,
    )

    account = repo.create_strategy_account(
        "good-etf",
        "good_etf",
        "qmt-main",
        INITIAL_CAPITAL,
    )

    assert account.cash_units == INITIAL_CAPITAL
    connection = connect_database(repo.database_path)
    try:
        pool = connection.execute(
            "SELECT unallocated_cash_units, version FROM cash_pools"
        ).fetchone()
        flow = connection.execute(
            """
            SELECT strategy_account_id, flow_type, amount_units, external_ref
            FROM capital_flows
            """
        ).fetchone()
    finally:
        connection.close()
    assert tuple(pool) == (400_000_000, 1)
    assert tuple(flow) == (
        "good-etf",
        "ALLOCATE",
        INITIAL_CAPITAL,
        "initial:good_etf",
    )


def test_insufficient_initial_allocation_rolls_back_all_records(tmp_path):
    repo = SQLiteStrategyRepository(tmp_path / "allocation-failure.db")
    repo.initialize()
    repo.create_physical_account(
        "qmt-main",
        "QMT",
        "account-1",
        unallocated_cash_units=INITIAL_CAPITAL - 1,
    )

    with pytest.raises(LedgerInvariantError, match="insufficient"):
        repo.create_strategy_account(
            "good-etf",
            "good_etf",
            "qmt-main",
            INITIAL_CAPITAL,
        )

    connection = connect_database(repo.database_path)
    try:
        pool = connection.execute(
            "SELECT unallocated_cash_units, version FROM cash_pools"
        ).fetchone()
        account_count = connection.execute(
            "SELECT COUNT(*) FROM strategy_accounts"
        ).fetchone()[0]
        flow_count = connection.execute("SELECT COUNT(*) FROM capital_flows").fetchone()[0]
    finally:
        connection.close()
    assert tuple(pool) == (INITIAL_CAPITAL - 1, 0)
    assert account_count == 0
    assert flow_count == 0


def test_initial_allocation_cannot_spend_reserved_pool_cash(tmp_path):
    repo = SQLiteStrategyRepository(tmp_path / "reserved-allocation.db")
    repo.initialize()
    repo.create_physical_account(
        "qmt-main",
        "QMT",
        "account-1",
        unallocated_cash_units=INITIAL_CAPITAL,
    )
    connection = connect_database(repo.database_path)
    try:
        connection.execute(
            "UPDATE cash_pools SET reserved_cash_units = 1 WHERE physical_account_id = ?",
            ("qmt-main",),
        )
    finally:
        connection.close()

    with pytest.raises(LedgerInvariantError, match="insufficient"):
        repo.create_strategy_account(
            "good-etf",
            "good_etf",
            "qmt-main",
            INITIAL_CAPITAL,
        )

    connection = connect_database(repo.database_path)
    try:
        pool = connection.execute(
            """
            SELECT unallocated_cash_units, reserved_cash_units, version
            FROM cash_pools
            """
        ).fetchone()
    finally:
        connection.close()
    assert tuple(pool) == (INITIAL_CAPITAL, 1, 0)


def test_strategy_account_insert_failure_rolls_back_pool_debit(tmp_path):
    repo = SQLiteStrategyRepository(tmp_path / "allocation-insert-failure.db")
    repo.initialize()
    repo.create_physical_account(
        "qmt-main",
        "QMT",
        "account-1",
        unallocated_cash_units=500_000_000,
    )
    connection = connect_database(repo.database_path)
    try:
        connection.execute(
            """
            CREATE TRIGGER test_abort_strategy_account_insert
            BEFORE INSERT ON strategy_accounts
            BEGIN
                SELECT RAISE(ABORT, 'injected strategy account failure');
            END
            """
        )
    finally:
        connection.close()

    with pytest.raises(RepositoryError, match="create strategy account"):
        repo.create_strategy_account(
            "good-etf",
            "good_etf",
            "qmt-main",
            INITIAL_CAPITAL,
        )

    connection = connect_database(repo.database_path)
    try:
        pool = connection.execute(
            "SELECT unallocated_cash_units, version FROM cash_pools"
        ).fetchone()
        account_count = connection.execute(
            "SELECT COUNT(*) FROM strategy_accounts"
        ).fetchone()[0]
        flow_count = connection.execute("SELECT COUNT(*) FROM capital_flows").fetchone()[0]
    finally:
        connection.close()
    assert tuple(pool) == (500_000_000, 0)
    assert account_count == 0
    assert flow_count == 0


def test_account_events_commit_and_replay_to_same_snapshot(repository):
    reserved = repository.append_account_event(
        "good-etf",
        expected_ledger_version=0,
        entry_type="CASH_RESERVED",
        amount_units=0,
        reserved_after_units=30_000_000,
        event_type="ORDER_FUNDS_RESERVED",
        payload={"order_id": "order-1", "amount_units": 30_000_000},
        reference_type="order",
        reference_id="order-1",
    )
    assert reserved.available_cash_units == 70_000_000
    booked = repository.append_account_event(
        "good-etf",
        expected_ledger_version=1,
        entry_type="BUY_FILL_BOOKED",
        amount_units=-20_000_000,
        reserved_after_units=0,
        event_type="FILL_BOOKED",
        payload={"order_id": "order-1", "fill_id": "fill-1"},
        reference_type="fill",
        reference_id="fill-1",
    )
    assert booked.cash_units == 80_000_000
    assert booked.ledger_version == 2
    assert booked.event_seq == 2
    assert repository.replay_account("good-etf") == booked
    assert [entry.event_seq for entry in repository.list_ledger_entries("good-etf")] == [1, 2]
    assert [event.event_seq for event in repository.list_events("good-etf")] == [1, 2]


def test_replay_uses_one_read_snapshot_during_concurrent_append(repository, monkeypatch):
    ledger_read = threading.Event()
    writer_committed = threading.Event()
    original_reader = SQLiteStrategyRepository._read_ledger_entries

    def interleaving_reader(connection, account_id):
        entries = original_reader(connection, account_id)
        ledger_read.set()
        assert writer_committed.wait(timeout=5)
        return entries

    monkeypatch.setattr(
        SQLiteStrategyRepository,
        "_read_ledger_entries",
        staticmethod(interleaving_reader),
    )
    with ThreadPoolExecutor(max_workers=1) as executor:
        replay_future = executor.submit(repository.replay_account, "good-etf")
        assert ledger_read.wait(timeout=5)
        updated = repository.append_account_event(
            "good-etf",
            expected_ledger_version=0,
            entry_type="ADJUSTMENT",
            amount_units=100,
            reserved_after_units=0,
            event_type="ADJUSTED",
            payload={},
        )
        writer_committed.set()
        replayed = replay_future.result(timeout=5)

    assert replayed.ledger_version == 0
    assert replayed.cash_units == INITIAL_CAPITAL
    assert updated.ledger_version == 1


def test_two_writers_with_same_version_only_commit_once(repository):
    barrier = threading.Barrier(2)

    def write_once(reference_id):
        barrier.wait()
        try:
            account = repository.append_account_event(
                "good-etf",
                expected_ledger_version=0,
                entry_type="ADJUSTMENT",
                amount_units=-100,
                reserved_after_units=0,
                event_type="ADJUSTED",
                payload={"reference_id": reference_id},
                reference_type="test",
                reference_id=reference_id,
            )
            return account
        except VersionConflictError as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(write_once, ("a", "b")))
    successes = [result for result in results if not isinstance(result, Exception)]
    conflicts = [result for result in results if isinstance(result, VersionConflictError)]
    assert len(successes) == 1
    assert len(conflicts) == 1
    account = repository.get_strategy_account("good-etf")
    assert account.ledger_version == 1
    assert account.cash_units == INITIAL_CAPITAL - 100
    assert len(repository.list_ledger_entries("good-etf")) == 1


def test_failed_event_insert_rolls_back_account_and_ledger(repository):
    connection = connect_database(repository.database_path)
    try:
        connection.execute(
            """
            CREATE TRIGGER test_abort_event_insert
            BEFORE INSERT ON strategy_events
            BEGIN
                SELECT RAISE(ABORT, 'injected event failure');
            END
            """
        )
    finally:
        connection.close()

    with pytest.raises(RepositoryError, match="append account event"):
        repository.append_account_event(
            "good-etf",
            expected_ledger_version=0,
            entry_type="ADJUSTMENT",
            amount_units=-100,
            reserved_after_units=0,
            event_type="ADJUSTED",
            payload={},
        )
    account = repository.get_strategy_account("good-etf")
    assert account.cash_units == INITIAL_CAPITAL
    assert account.ledger_version == 0
    assert repository.list_ledger_entries("good-etf") == []
    assert repository.list_events("good-etf") == []


def test_invalid_balance_is_rejected_without_partial_write(repository):
    with pytest.raises(LedgerInvariantError, match="negative"):
        repository.append_account_event(
            "good-etf",
            expected_ledger_version=0,
            entry_type="BAD",
            amount_units=-(INITIAL_CAPITAL + 1),
            reserved_after_units=0,
            event_type="BAD",
            payload={},
        )
    assert repository.get_strategy_account("good-etf").ledger_version == 0
    assert repository.list_ledger_entries("good-etf") == []


def test_ledger_and_event_tables_are_append_only(repository):
    repository.append_account_event(
        "good-etf",
        expected_ledger_version=0,
        entry_type="ADJUSTMENT",
        amount_units=1,
        reserved_after_units=0,
        event_type="ADJUSTED",
        payload={},
    )
    connection = connect_database(repository.database_path)
    try:
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute("UPDATE ledger_entries SET amount_units = 99")
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute("DELETE FROM strategy_events")
    finally:
        connection.close()
