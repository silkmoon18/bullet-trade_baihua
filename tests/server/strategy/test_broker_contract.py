from dataclasses import replace
import json

import pytest

from bullet_trade.server.adapters.big_qmt import _normalize_trade
from bullet_trade.server.adapters.big_qmt import BigQmtBrokerAdapter
from bullet_trade.server.adapters.qmt import QmtBrokerAdapter
from bullet_trade.server.strategy.broker_contract import (
    BIG_QMT_CAPABILITIES,
    MINI_QMT_CAPABILITIES,
    BrokerContractError,
    CapabilityState,
    normalize_trade_batch,
    normalize_trade_evidence,
    require_strategy_ledger_v1,
    strategy_ledger_v1_blockers,
    load_verified_capabilities,
)
from bullet_trade.server.strategy.domain import OrderSide


def _verified_profile(profile):
    return replace(
        profile,
        client_tag_roundtrip=CapabilityState.SUPPORTED,
        stable_order_id=CapabilityState.SUPPORTED,
        stable_trade_id=CapabilityState.SUPPORTED,
        trade_order_link=CapabilityState.SUPPORTED,
        order_side_for_trade=CapabilityState.SUPPORTED,
        fee_fields=CapabilityState.SUPPORTED,
        order_status=CapabilityState.SUPPORTED,
        current_orders_query=CapabilityState.SUPPORTED,
        current_trades_query=CapabilityState.SUPPORTED,
        working_orders_query=CapabilityState.SUPPORTED,
        order_lookback_days=1,
        trade_lookback_days=1,
    )


def test_qmt_adapters_expose_their_strategy_ledger_profiles():
    assert QmtBrokerAdapter.strategy_ledger_capabilities() is MINI_QMT_CAPABILITIES
    assert BigQmtBrokerAdapter.strategy_ledger_capabilities() is BIG_QMT_CAPABILITIES


@pytest.mark.parametrize("profile", [MINI_QMT_CAPABILITIES, BIG_QMT_CAPABILITIES])
def test_unprobed_qmt_profile_is_not_live_ready(profile):
    blockers = strategy_ledger_v1_blockers(profile)
    assert "stable_trade_id=PROBE_REQUIRED" in blockers
    assert "order_lookback_days<1" in blockers
    with pytest.raises(BrokerContractError, match="does not satisfy"):
        require_strategy_ledger_v1(profile)


@pytest.mark.parametrize("profile", [MINI_QMT_CAPABILITIES, BIG_QMT_CAPABILITIES])
def test_verified_qmt_profile_can_use_order_mapping_for_trade_side(profile):
    verified = _verified_profile(profile)
    assert verified.direct_trade_side is CapabilityState.UNSUPPORTED
    assert strategy_ledger_v1_blockers(verified) == ()
    require_strategy_ledger_v1(verified)


def test_verified_capability_evidence_loads_for_matching_adapter(tmp_path):
    path = tmp_path / "capabilities.json"
    states = {
        "client_tag_roundtrip": True,
        "stable_order_id": True,
        "stable_trade_id": True,
        "trade_order_link": True,
        "direct_trade_side": False,
        "order_side_for_trade": True,
        "fee_fields": True,
        "order_status": True,
        "current_orders_query": True,
        "current_trades_query": True,
        "working_orders_query": True,
    }
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "adapter_kind": "MINI_QMT",
                "verified_at": "2026-08-11T20:00:00+08:00",
                "probe_report": "runtime-probe/probe_report.json",
                "order_lookback_days": 1,
                "trade_lookback_days": 1,
                "capabilities": states,
            }
        ),
        encoding="utf-8",
    )
    profile = load_verified_capabilities(path, "MINI_QMT")
    assert strategy_ledger_v1_blockers(profile) == ()
    with pytest.raises(BrokerContractError, match="adapter does not match"):
        load_verified_capabilities(path, "BIG_QMT")


def test_incomplete_capability_evidence_is_rejected(tmp_path):
    path = tmp_path / "capabilities.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "adapter_kind": "MINI_QMT",
                "verified_at": "2026-08-11",
                "probe_report": "probe.json",
                "capabilities": {},
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(BrokerContractError, match="non-boolean"):
        load_verified_capabilities(path, "MINI_QMT")


def test_trade_order_link_without_order_side_does_not_satisfy_side_contract():
    profile = replace(
        _verified_profile(MINI_QMT_CAPABILITIES),
        order_side_for_trade=CapabilityState.PROBE_REQUIRED,
    )
    assert "trade_side_has_no_order_mapping" in strategy_ledger_v1_blockers(profile)


def test_trade_side_maps_only_through_its_broker_order_id():
    evidence = normalize_trade_evidence(
        {
            "trade_id": "T-1",
            "trade_id_source": "broker",
            "order_id": "O-1",
            "security": "510050.XSHG",
            "amount": 100,
            "price": 2.5,
            "commission_fee": 1.25,
            "commission_known": True,
            "tax": 0.0,
            "tax_known": True,
            "time": "2026-08-10 10:00:00",
        },
        {"O-1": {"order_id": "O-1", "is_buy": False}},
    )
    assert evidence.broker_trade_id == "T-1"
    assert evidence.side is OrderSide.SELL
    assert evidence.commission_units == 12_500
    assert evidence.tax_units == 0


def test_trade_without_matching_side_is_rejected():
    with pytest.raises(BrokerContractError, match="side cannot be mapped"):
        normalize_trade_evidence(
            {
                "trade_id": "T-1",
                "trade_id_source": "broker",
                "order_id": "O-missing",
                "security": "510050.XSHG",
                "amount": 100,
                "price": 2.5,
                "commission_fee": 0,
                "tax": 0,
                "time": "2026-08-10 10:00:00",
            },
            {},
        )


@pytest.mark.parametrize(
    "trade, message",
    [
        (
            {
                "trade_id": "synthetic-id",
                "trade_id_source": "synthetic",
                "order_id": "O-1",
                "security": "510050.XSHG",
                "amount": 100,
                "price": 2.5,
                "side": "BUY",
                "commission_fee": 0,
                "tax": 0,
                "time": "2026-08-10 10:00:00",
            },
            "missing or synthetic",
        ),
        (
            {
                "trade_id": "T-1",
                "trade_id_source": "broker",
                "order_id": "O-1",
                "security": "510050.XSHG",
                "amount": 100,
                "price": 2.5,
                "side": "BUY",
                "commission_fee": 0,
                "commission_known": False,
                "tax": 0,
                "tax_known": True,
                "time": "2026-08-10 10:00:00",
            },
            "fee fields are incomplete",
        ),
    ],
)
def test_synthetic_trade_id_and_missing_fee_evidence_are_rejected(trade, message):
    with pytest.raises(BrokerContractError, match=message):
        normalize_trade_evidence(trade, {})


@pytest.mark.parametrize(
    "fee_field, fee_value",
    [("commission_fee", -1), ("tax", "-0.1")],
)
def test_negative_broker_fees_are_rejected(fee_field, fee_value):
    trade = {
        "trade_id": "T-1",
        "trade_id_source": "broker",
        "order_id": "O-1",
        "security": "510050.XSHG",
        "amount": 100,
        "price": 2.5,
        "side": "BUY",
        "commission_fee": 0,
        "tax": 0,
        "time": "2026-08-10 10:00:00",
    }
    trade[fee_field] = fee_value
    with pytest.raises(BrokerContractError, match="cannot be negative"):
        normalize_trade_evidence(trade, {})


def test_duplicate_identical_trades_are_one_fill_but_conflicts_are_rejected():
    first = {
        "trade_id": "T-1",
        "trade_id_source": "broker",
        "order_id": "O-1",
        "security": "510050.XSHG",
        "amount": 100,
        "price": "2.50",
        "commission_fee": "1.00",
        "tax": "0",
        "time": "2026-08-10 10:00:00",
    }
    second = dict(first)
    trades = normalize_trade_batch(
        [first, second],
        [{"order_id": "O-1", "side": "BUY"}],
    )
    assert len(trades) == 1
    assert trades[0].side is OrderSide.BUY

    conflicting = dict(first, amount=200)
    with pytest.raises(BrokerContractError, match="conflicting"):
        normalize_trade_batch(
            [first, conflicting],
            [{"order_id": "O-1", "side": "BUY"}],
        )


def test_big_qmt_normalization_marks_native_ids_and_fee_presence():
    normalized = _normalize_trade(
        {
            "m_strTradeID": "T-1",
            "m_strOrderSysID": "O-1",
            "m_dCommission": 0.0,
            "m_dTax": 0.0,
        }
    )
    assert normalized["trade_id"] == "T-1"
    assert normalized["trade_id_source"] == "broker"
    assert normalized["commission_known"] is True
    assert normalized["tax_known"] is True

    xttrade = _normalize_trade(
        {
            "traded_id": "XT-T-1",
            "order_id": "XT-O-1",
            "used_commission": 5.25,
        }
    )
    assert xttrade["trade_id"] == "XT-T-1"
    assert xttrade["trade_id_source"] == "broker"
    assert xttrade["commission_fee"] == 5.25
    assert xttrade["commission_known"] is True

    native_time = _normalize_trade(
        {
            "m_strTradeID": "T-time",
            "m_strTradeDate": "20260810",
            "m_strTradeTime": "100000",
        }
    )
    assert native_time["time"] == "2026-08-10 10:00:00"

    missing = _normalize_trade({"m_strOrderSysID": "O-2"})
    assert missing["trade_id_source"] == "missing"
    assert missing["commission_known"] is False
    assert missing["tax_known"] is False

    upstream_default = _normalize_trade(
        {
            "trade_id": "T-2",
            "commission_fee": 0,
            "commission_known": False,
            "tax": 0,
            "tax_known": False,
        }
    )
    assert upstream_default["commission_known"] is False
    assert upstream_default["tax_known"] is False


def test_hhmmss_without_trade_date_is_not_misread_as_unix_time():
    with pytest.raises(BrokerContractError, match="trade time is invalid"):
        normalize_trade_evidence(
            {
                "trade_id": "T-1",
                "trade_id_source": "broker",
                "order_id": "O-1",
                "security": "510050.XSHG",
                "amount": 100,
                "price": 2.5,
                "side": "BUY",
                "commission_fee": 0,
                "tax": 0,
                "time": 100000,
            },
            {},
        )
