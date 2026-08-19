import pytest

from bullet_trade.server.strategy import (
    ConditionalLimitExecution,
    ConditionalLimitPriceMode,
    ExecutionRequest,
    FollowUpPolicy,
    LimitExecution,
    MarketExecution,
    MarketableLimitExecution,
    RepricingPolicy,
    execution_request_from_wire,
    execution_request_to_wire,
)


@pytest.mark.parametrize(
    "execution_request",
    [
        ExecutionRequest(style=LimitExecution(2_000)),
        ExecutionRequest(
            style=ConditionalLimitExecution(
                3_000, ConditionalLimitPriceMode.BOUNDARY
            ),
            follow_up=FollowUpPolicy.UNTIL_FILLED_TODAY,
            repricing=RepricingPolicy.KEEP_ORIGINAL,
        ),
        ExecutionRequest(
            style=MarketExecution(15_000),
            follow_up=FollowUpPolicy.NONE,
        ),
        ExecutionRequest(
            style=ConditionalLimitExecution(2_000),
            sell_style=MarketExecution(15_000),
        ),
        ExecutionRequest(style=MarketableLimitExecution(10_000)),
    ],
)
def test_execution_request_wire_round_trip(execution_request):
    wire = execution_request_to_wire(execution_request)

    assert wire["schema_version"] == 2
    assert execution_request_from_wire(wire) == execution_request


def test_wire_rejects_unknown_fields_and_versions():
    wire = execution_request_to_wire(ExecutionRequest())
    wire["credential"] = "unexpected"
    with pytest.raises(ValueError, match="unknown fields"):
        execution_request_from_wire(wire)

    wire = execution_request_to_wire(ExecutionRequest())
    wire["schema_version"] = 3
    with pytest.raises(ValueError, match="schema_version"):
        execution_request_from_wire(wire)


def test_legacy_schema_one_decodes_without_sell_override():
    wire = execution_request_to_wire(ExecutionRequest())
    wire["schema_version"] = 1
    wire.pop("sell_style")

    decoded = execution_request_from_wire(wire)

    assert decoded.sell_style is None
    assert decoded.style == ExecutionRequest().style


@pytest.mark.parametrize("value", [-1, 100_001, True, 1.5])
def test_price_band_is_bounded_integer(value):
    with pytest.raises((TypeError, ValueError)):
        ConditionalLimitExecution(value)  # type: ignore[arg-type]


def test_execution_request_rejects_plain_string_enums():
    with pytest.raises(TypeError, match="follow_up"):
        ExecutionRequest(follow_up="NONE")  # type: ignore[arg-type]

    with pytest.raises(TypeError, match="sell_style"):
        ExecutionRequest(sell_style="MARKET")  # type: ignore[arg-type]
