from concurrent.futures import ThreadPoolExecutor

import pytest

from bullet_trade.server.strategy.capital import (
    BrokerCashMismatchError,
    CapitalConfigurationError,
    SQLiteCapitalService,
)
from bullet_trade.server.strategy.repository import (
    LedgerInvariantError,
    RepositoryError,
    SQLiteStrategyRepository,
)
from bullet_trade.server.strategy.schema import connect_database


INITIAL_CAPITAL = 100_000_000
BROKER_CASH = 150_000_000


@pytest.fixture
def capital_service(tmp_path):
    database_path = tmp_path / "capital.db"
    ledger = SQLiteStrategyRepository(database_path)
    ledger.initialize()
    ledger.create_physical_account("qmt-main", "QMT", "account-1")
    return SQLiteCapitalService(database_path)


def _pool(service):
    connection = connect_database(service.database_path)
    try:
        return tuple(
            connection.execute(
                """
                SELECT unallocated_cash_units, reserved_cash_units, version
                FROM cash_pools WHERE physical_account_id = 'qmt-main'
                """
            ).fetchone()
        )
    finally:
        connection.close()


def _ensure(service):
    return service.ensure_strategy_account(
        "good-etf",
        "good_etf",
        "qmt-main",
        INITIAL_CAPITAL,
    )


def test_broker_cash_calibration_and_initial_allocation(capital_service):
    assert capital_service.calibrate_broker_available_cash(
        "qmt-main", BROKER_CASH
    ) == BROKER_CASH
    created = _ensure(capital_service)

    assert created.created is True
    assert created.account.cash_units == INITIAL_CAPITAL
    assert _pool(capital_service) == (50_000_000, 0, 2)


def test_insufficient_real_cash_rejects_initial_account(capital_service):
    capital_service.calibrate_broker_available_cash(
        "qmt-main", INITIAL_CAPITAL - 1
    )
    with pytest.raises(LedgerInvariantError, match="real account has insufficient"):
        _ensure(capital_service)

    connection = connect_database(capital_service.database_path)
    try:
        assert connection.execute("SELECT COUNT(*) FROM strategy_accounts").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM capital_flows").fetchone()[0] == 0
    finally:
        connection.close()


def test_repeat_ensure_returns_original_and_rejects_silent_reset(capital_service):
    capital_service.calibrate_broker_available_cash("qmt-main", BROKER_CASH)
    first = _ensure(capital_service)
    second = _ensure(capital_service)

    assert second.created is False
    assert second.account == first.account
    with pytest.raises(CapitalConfigurationError, match="does not match"):
        capital_service.ensure_strategy_account(
            "good-etf",
            "good_etf",
            "qmt-main",
            INITIAL_CAPITAL + 1,
        )
    assert _pool(capital_service) == (50_000_000, 0, 2)


def test_concurrent_ensure_allocates_initial_capital_once(capital_service):
    capital_service.calibrate_broker_available_cash("qmt-main", BROKER_CASH)
    with ThreadPoolExecutor(max_workers=12) as executor:
        results = list(executor.map(lambda _: _ensure(capital_service), range(50)))

    assert sum(result.created for result in results) == 1
    assert _pool(capital_service) == (50_000_000, 0, 2)


def test_existing_account_calibration_rebases_external_cash_changes(capital_service):
    capital_service.calibrate_broker_available_cash("qmt-main", BROKER_CASH)
    account = _ensure(capital_service).account

    assert capital_service.calibrate_broker_available_cash(
        "qmt-main", BROKER_CASH
    ) == BROKER_CASH
    assert capital_service.calibrate_broker_available_cash(
        "qmt-main", BROKER_CASH - 1
    ) == BROKER_CASH - 1
    assert _pool(capital_service) == (50_000_000 - 1, 0, 3)
    assert capital_service.calibrate_broker_available_cash(
        "qmt-main", BROKER_CASH + 25_000_000
    ) == BROKER_CASH + 25_000_000
    assert _pool(capital_service) == (75_000_000, 0, 4)
    assert capital_service._ledger.get_strategy_account("good-etf") == account


def test_existing_account_calibration_rejects_cash_below_strategy_commitments(
    capital_service,
):
    capital_service.calibrate_broker_available_cash("qmt-main", BROKER_CASH)
    _ensure(capital_service)

    with pytest.raises(BrokerCashMismatchError, match="cannot cover") as exc_info:
        capital_service.calibrate_broker_available_cash(
            "qmt-main", INITIAL_CAPITAL - 1
        )

    message = str(exc_info.value)
    assert "physical_account_id=qmt-main" in message
    assert "broker_available_cash=9999.9999" in message
    assert "ledger_strategy_available_cash=10000.0000" in message
    assert "shortfall=0.0001" in message
    assert "ledger_unallocated_available_cash_before=5000.0000" in message
    assert "ledger_expected_available_cash_before=15000.0000" in message
    assert "difference=-5000.0001" in message
    assert _pool(capital_service) == (50_000_000, 0, 2)


def test_calibration_uses_available_cash_after_strategy_reservation(capital_service):
    capital_service.calibrate_broker_available_cash("qmt-main", BROKER_CASH)
    _ensure(capital_service)
    capital_service.reserve_cash(
        "good-etf", 30_000_000, expected_ledger_version=0, order_id="order-1"
    )

    # The broker also excludes the working order's frozen cash from available_cash.
    assert capital_service.calibrate_broker_available_cash(
        "qmt-main", BROKER_CASH - 30_000_000
    ) == BROKER_CASH - 30_000_000
    assert _pool(capital_service) == (50_000_000, 0, 2)


def test_reserve_and_release_change_available_cash_with_ledger_events(capital_service):
    capital_service.calibrate_broker_available_cash("qmt-main", BROKER_CASH)
    _ensure(capital_service)

    reserved = capital_service.reserve_cash(
        "good-etf", 30_000_000, expected_ledger_version=0, order_id="order-1"
    )
    assert reserved.reserved_cash_units == 30_000_000
    assert reserved.available_cash_units == 70_000_000
    released = capital_service.release_cash(
        "good-etf", 10_000_000, expected_ledger_version=1, order_id="order-1"
    )
    assert released.reserved_cash_units == 20_000_000
    assert released.available_cash_units == 80_000_000
    assert capital_service._ledger.replay_account("good-etf") == released


def test_release_cannot_consume_another_orders_reservation(capital_service):
    capital_service.calibrate_broker_available_cash("qmt-main", BROKER_CASH)
    _ensure(capital_service)

    capital_service.reserve_cash(
        "good-etf", 60_000_000, expected_ledger_version=0, order_id="order-a"
    )
    capital_service.reserve_cash(
        "good-etf", 30_000_000, expected_ledger_version=1, order_id="order-b"
    )
    released = capital_service.release_cash(
        "good-etf", 60_000_000, expected_ledger_version=2, order_id="order-a"
    )

    with pytest.raises(LedgerInvariantError, match="order reserved cash"):
        capital_service.release_cash(
            "good-etf", 1, expected_ledger_version=3, order_id="order-a"
        )
    assert capital_service._ledger.get_strategy_account("good-etf") == released
    assert released.reserved_cash_units == 30_000_000


def test_explicit_allocate_withdraw_and_replay_are_idempotent(capital_service):
    capital_service.calibrate_broker_available_cash("qmt-main", BROKER_CASH)
    _ensure(capital_service)

    allocated = capital_service.adjust_capital(
        "good-etf",
        "ALLOCATE",
        20_000_000,
        expected_ledger_version=0,
        external_ref="adjust-1",
        reason="increase strategy capital",
    )
    assert allocated.replayed is False
    assert allocated.account.cash_units == 120_000_000
    replayed = capital_service.adjust_capital(
        "good-etf",
        "ALLOCATE",
        20_000_000,
        expected_ledger_version=0,
        external_ref="adjust-1",
        reason="increase strategy capital",
    )
    assert replayed.replayed is True
    withdrawn = capital_service.adjust_capital(
        "good-etf",
        "WITHDRAW",
        10_000_000,
        expected_ledger_version=1,
        external_ref="adjust-2",
        reason="reduce strategy capital",
    )
    assert withdrawn.account.cash_units == 110_000_000
    assert _pool(capital_service) == (40_000_000, 0, 4)
    assert capital_service._ledger.replay_account("good-etf") == withdrawn.account


def test_withdraw_cannot_use_reserved_strategy_cash(capital_service):
    capital_service.calibrate_broker_available_cash("qmt-main", BROKER_CASH)
    _ensure(capital_service)
    capital_service.reserve_cash(
        "good-etf", 90_000_000, expected_ledger_version=0, order_id="order-1"
    )

    with pytest.raises(LedgerInvariantError, match="insufficient available"):
        capital_service.adjust_capital(
            "good-etf",
            "WITHDRAW",
            20_000_000,
            expected_ledger_version=1,
            external_ref="adjust-1",
            reason="invalid withdrawal",
        )


def test_capital_flow_insert_failure_rolls_back_pool_account_and_ledger(capital_service):
    capital_service.calibrate_broker_available_cash("qmt-main", BROKER_CASH)
    original = _ensure(capital_service).account
    original_pool = _pool(capital_service)
    connection = connect_database(capital_service.database_path)
    try:
        connection.execute(
            """
            CREATE TRIGGER test_abort_capital_flow
            BEFORE INSERT ON capital_flows
            BEGIN
                SELECT RAISE(ABORT, 'injected capital flow failure');
            END
            """
        )
    finally:
        connection.close()

    with pytest.raises(RepositoryError, match="adjust strategy capital"):
        capital_service.adjust_capital(
            "good-etf",
            "ALLOCATE",
            10_000_000,
            expected_ledger_version=0,
            external_ref="adjust-failure",
            reason="injected failure",
        )
    assert capital_service._ledger.get_strategy_account("good-etf") == original
    assert _pool(capital_service) == original_pool
    assert capital_service._ledger.list_ledger_entries("good-etf") == []
