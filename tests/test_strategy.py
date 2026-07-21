from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from almanak.framework.market import TokenBalance
from almanak.framework.teardown import TeardownMode
from strategy import GMXEMATrendWETHStrategy, PositionState


@pytest.fixture
def base_config() -> dict:
    return {
        "chain": "arbitrum",
        "protocol": "gmx_v2",
        "market": "WETH/USD",
        "collateral_token": "USDC",
        "positioning": {
            "direction": "long_only",
            "max_concurrent_positions": 1,
            "leverage": "1",
        },
        "signal": {
            "timeframe": "1h",
            "indicators": [{"name": "ema", "period": 20}, {"name": "ema", "period": 50}],
        },
        "execution": {
            "evaluate_on_candle_close_only": True,
            "allow_intrabar_entries": False,
            "allow_short": False,
        },
        "sizing": {"position_size_pct_of_available_collateral": "95"},
        "risk": {
            "max_slippage_bps": "40",
            "min_order_notional_usd": "10",
            "close_on_bear_cross": True,
        },
        "teardown": {"policy": "close_open_long_if_any"},
        "force_action": "",
    }


@pytest.fixture
def strategy(base_config: dict) -> GMXEMATrendWETHStrategy:
    return GMXEMATrendWETHStrategy(
        config=base_config,
        chain="arbitrum",
        wallet_address="0x" + "1" * 40,
    )


def make_market(
    *,
    ts: datetime,
    ema_fast: Decimal,
    ema_slow: Decimal,
    balance_usd: Decimal = Decimal("1000"),
    collateral_price: Decimal = Decimal("1"),
) -> MagicMock:
    market = MagicMock()
    market.timestamp = ts

    def ema_side_effect(token: str, period: int, timeframe: str | None = None):
        if period == 20:
            return SimpleNamespace(value=ema_fast)
        if period == 50:
            return SimpleNamespace(value=ema_slow)
        raise ValueError("Unsupported period")

    market.ema.side_effect = ema_side_effect
    market.balance.return_value = TokenBalance(
        symbol="USDC",
        balance=balance_usd,
        balance_usd=balance_usd,
        address="0xaf88d065e77c8cc2239327c5edb3a432268e5831",
    )

    def price_side_effect(token: str, quote: str = "USD"):
        if token == "USDC":
            return collateral_price
        if token == "WETH":
            return Decimal("3000")
        raise ValueError("Unknown token")

    market.price.side_effect = price_side_effect
    return market


def test_warmup_holds(strategy: GMXEMATrendWETHStrategy) -> None:
    market = make_market(
        ts=datetime(2026, 1, 1, 10, 0, tzinfo=UTC),
        ema_fast=Decimal("100"),
        ema_slow=Decimal("101"),
    )

    intent = strategy.decide(market)
    assert intent.intent_type.value == "HOLD"
    assert strategy._prev_ema_diff == Decimal("-1")


def test_open_long_on_bull_cross(strategy: GMXEMATrendWETHStrategy) -> None:
    t0 = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)
    t1 = t0 + timedelta(hours=1)

    strategy.decide(make_market(ts=t0, ema_fast=Decimal("100"), ema_slow=Decimal("101")))
    intent = strategy.decide(make_market(ts=t1, ema_fast=Decimal("102"), ema_slow=Decimal("101")))

    assert intent.intent_type.value == "PERP_OPEN"
    assert intent.is_long is True
    assert intent.protocol == "gmx_v2"


def test_hold_when_no_bull_cross_flat(strategy: GMXEMATrendWETHStrategy) -> None:
    t0 = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)
    t1 = t0 + timedelta(hours=1)

    strategy.decide(make_market(ts=t0, ema_fast=Decimal("101"), ema_slow=Decimal("100")))
    intent = strategy.decide(make_market(ts=t1, ema_fast=Decimal("102"), ema_slow=Decimal("101")))

    assert intent.intent_type.value == "HOLD"


def test_never_open_short_on_bear_cross_while_flat(strategy: GMXEMATrendWETHStrategy) -> None:
    t0 = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)
    t1 = t0 + timedelta(hours=1)

    strategy.decide(make_market(ts=t0, ema_fast=Decimal("102"), ema_slow=Decimal("101")))
    intent = strategy.decide(make_market(ts=t1, ema_fast=Decimal("100"), ema_slow=Decimal("101")))

    assert intent.intent_type.value == "HOLD"


def test_close_long_on_bear_cross(strategy: GMXEMATrendWETHStrategy) -> None:
    strategy._position_state = PositionState.LONG_OPEN
    strategy._open_size_usd = Decimal("950")
    strategy._prev_ema_diff = Decimal("1")

    intent = strategy.decide(
        make_market(
            ts=datetime(2026, 1, 1, 11, 0, tzinfo=UTC),
            ema_fast=Decimal("100"),
            ema_slow=Decimal("101"),
        )
    )

    assert intent.intent_type.value == "PERP_CLOSE"
    assert intent.is_long is True
    assert intent.size_usd is None


def test_same_hour_is_ignored(strategy: GMXEMATrendWETHStrategy) -> None:
    ts = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)

    strategy.decide(make_market(ts=ts, ema_fast=Decimal("100"), ema_slow=Decimal("101")))
    intent = strategy.decide(make_market(ts=ts, ema_fast=Decimal("101"), ema_slow=Decimal("100")))

    assert intent.intent_type.value == "HOLD"
    assert "Waiting for next 1h candle close" in intent.reason


def test_ema_unavailable_returns_hold(strategy: GMXEMATrendWETHStrategy) -> None:
    market = MagicMock()
    market.timestamp = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)
    market.ema.side_effect = ValueError("ema unavailable")

    intent = strategy.decide(market)
    assert intent.intent_type.value == "HOLD"


def test_insufficient_collateral_holds(strategy: GMXEMATrendWETHStrategy) -> None:
    t0 = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)
    t1 = t0 + timedelta(hours=1)

    strategy.decide(make_market(ts=t0, ema_fast=Decimal("100"), ema_slow=Decimal("101"), balance_usd=Decimal("5")))
    intent = strategy.decide(make_market(ts=t1, ema_fast=Decimal("102"), ema_slow=Decimal("101"), balance_usd=Decimal("5")))

    assert intent.intent_type.value == "HOLD"


def test_force_open_long(strategy: GMXEMATrendWETHStrategy) -> None:
    strategy.force_action = "open_long"
    intent = strategy.decide(
        make_market(
            ts=datetime(2026, 1, 1, 10, 0, tzinfo=UTC),
            ema_fast=Decimal("100"),
            ema_slow=Decimal("101"),
        )
    )
    assert intent.intent_type.value == "PERP_OPEN"


def test_force_close(strategy: GMXEMATrendWETHStrategy) -> None:
    strategy.force_action = "close"
    strategy._position_state = PositionState.LONG_OPEN
    strategy._open_size_usd = Decimal("100")

    intent = strategy.decide(
        make_market(
            ts=datetime(2026, 1, 1, 10, 0, tzinfo=UTC),
            ema_fast=Decimal("100"),
            ema_slow=Decimal("101"),
        )
    )
    assert intent.intent_type.value == "PERP_CLOSE"
    assert intent.size_usd is None


def test_on_intent_executed_transitions(strategy: GMXEMATrendWETHStrategy) -> None:
    open_intent = SimpleNamespace(intent_type=SimpleNamespace(value="PERP_OPEN"), size_usd=Decimal("100"))
    result = SimpleNamespace(extracted_data={"entry_price": "3000"})
    strategy.on_intent_executed(open_intent, success=True, result=result)

    assert strategy._position_state == PositionState.LONG_OPEN
    assert strategy._open_size_usd == Decimal("100")

    close_intent = SimpleNamespace(intent_type=SimpleNamespace(value="PERP_CLOSE"))
    strategy.on_intent_executed(close_intent, success=True, result=SimpleNamespace(extracted_data={}))
    assert strategy._position_state == PositionState.FLAT


def test_teardown_open_position(strategy: GMXEMATrendWETHStrategy) -> None:
    strategy._position_state = PositionState.LONG_OPEN
    strategy._open_size_usd = Decimal("321")

    summary = strategy.get_open_positions()
    assert len(summary.positions) == 1

    intents = strategy.generate_teardown_intents(mode=TeardownMode.SOFT)
    assert len(intents) == 1
    assert intents[0].intent_type.value == "PERP_CLOSE"
    assert intents[0].size_usd is None


def test_teardown_flat_returns_empty(strategy: GMXEMATrendWETHStrategy) -> None:
    strategy._position_state = PositionState.FLAT
    assert strategy.get_open_positions().positions == []
    assert strategy.generate_teardown_intents(mode=TeardownMode.HARD) == []


def test_persistence_roundtrip(strategy: GMXEMATrendWETHStrategy, base_config: dict) -> None:
    strategy._position_state = PositionState.LONG_OPEN
    strategy._open_size_usd = Decimal("123")
    strategy._entry_price = Decimal("3010")
    strategy._prev_ema_diff = Decimal("2")
    strategy._last_processed_candle_hour = "2026-01-01T10:00:00+00:00"

    state = strategy.get_persistent_state()

    restored = GMXEMATrendWETHStrategy(
        config=base_config,
        chain="arbitrum",
        wallet_address="0x" + "2" * 40,
    )
    restored.load_persistent_state(state)

    assert restored._position_state == PositionState.LONG_OPEN
    assert restored._open_size_usd == Decimal("123")
    assert restored._entry_price == Decimal("3010")
    assert restored._prev_ema_diff == Decimal("2")
