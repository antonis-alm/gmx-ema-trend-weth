"""GMX-EMA-Trend-WETH strategy.

Long-only GMX V2 perp strategy on Arbitrum:
- Open long on EMA(20) cross above EMA(50)
- Close long on EMA(20) cross below EMA(50)
- Evaluate only once per confirmed 1h candle close
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from almanak.framework.data import BalanceUnavailableError, MarketSnapshotError, PriceUnavailableError
from almanak.framework.intents import Intent
from almanak.framework.market import MarketSnapshot
from almanak.framework.strategies import IntentStrategy, almanak_strategy

if TYPE_CHECKING:
    from almanak.framework.teardown import TeardownMode, TeardownPositionSummary

logger = logging.getLogger(__name__)

_DATA_UNAVAILABLE_ERRORS = (
    PriceUnavailableError,
    BalanceUnavailableError,
    MarketSnapshotError,
    ValueError,
)


class PositionState(StrEnum):
    FLAT = "flat"
    LONG_OPEN = "long_open"


@almanak_strategy(
    name="g_m_x_e_m_a_trend_w_e_t_h",
    description="Long-only GMX EMA(20/50) trend strategy on 1h closes",
    version="1.0.0",
    author="Almanak",
    tags=["gmx", "perps", "ema", "trend", "long-only"],
    supported_chains=["arbitrum"],
    default_chain="arbitrum",
    supported_protocols=["gmx_v2"],
    intent_types=["PERP_OPEN", "PERP_CLOSE", "HOLD"],
    quote_asset="USD",
)
class GMXEMATrendWETHStrategy(IntentStrategy):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)

        if hasattr(self.config, "to_dict"):
            cfg = self.config.to_dict()
        elif isinstance(self.config, dict):
            cfg = self.config
        else:
            cfg = {}
        self.config_chain = str(cfg.get("chain", "arbitrum"))
        self.protocol = str(cfg.get("protocol", "gmx_v2"))
        self.market = str(cfg.get("market", "WETH/USD"))
        self.base_token = self.market.split("/")[0].strip()
        self.collateral_token = str(cfg.get("collateral_token", "USDC"))

        positioning = cfg.get("positioning", {})
        self.direction = str(positioning.get("direction", "long_only"))
        self.max_concurrent_positions = int(positioning.get("max_concurrent_positions", 1))
        self.leverage = Decimal(str(positioning.get("leverage", "1")))

        signal = cfg.get("signal", {})
        self.timeframe = str(signal.get("timeframe", "1h"))
        indicators = signal.get("indicators", [])
        self.ema_fast_period = int(indicators[0]["period"]) if len(indicators) > 0 else 20
        self.ema_slow_period = int(indicators[1]["period"]) if len(indicators) > 1 else 50

        execution = cfg.get("execution", {})
        self.evaluate_on_candle_close_only = bool(execution.get("evaluate_on_candle_close_only", True))
        self.allow_intrabar_entries = bool(execution.get("allow_intrabar_entries", False))
        self.allow_short = bool(execution.get("allow_short", False))

        sizing = cfg.get("sizing", {})
        self.position_size_pct = Decimal(str(sizing.get("position_size_pct_of_available_collateral", "95")))

        risk = cfg.get("risk", {})
        self.max_slippage_bps = Decimal(str(risk.get("max_slippage_bps", "40")))
        self.min_order_notional_usd = Decimal(str(risk.get("min_order_notional_usd", "10")))
        self.close_on_bear_cross = bool(risk.get("close_on_bear_cross", True))

        teardown = cfg.get("teardown", {})
        self.teardown_policy = str(teardown.get("policy", "close_open_long_if_any"))

        self.force_action = str(cfg.get("force_action", "") or "").strip().lower()
        self.execution_leverage = self.leverage if self.leverage >= Decimal("1.1") else Decimal("1.1")

        self._position_state: PositionState = PositionState.FLAT
        self._open_size_usd: Decimal | None = None
        self._entry_price: Decimal | None = None
        self._prev_ema_diff: Decimal | None = None
        self._last_processed_candle_hour: str | None = None

    def decide(self, market: MarketSnapshot) -> Intent | None:
        if self.force_action:
            return self._forced_intent(market)

        if self.chain != self.config_chain:
            return Intent.hold(reason=f"Configured chain {self.config_chain} does not match runtime chain {self.chain}")
        if self.direction != "long_only" or self.allow_short:
            return Intent.hold(reason="Only long_only mode is supported")
        if self.max_concurrent_positions != 1:
            return Intent.hold(reason="Only max_concurrent_positions=1 is supported")

        if self.evaluate_on_candle_close_only and not self.allow_intrabar_entries:
            ts = market.timestamp
            candle_hour = self._hour_bucket(ts)
            if self._last_processed_candle_hour == candle_hour:
                return Intent.hold(reason="Waiting for next 1h candle close")
            self._last_processed_candle_hour = candle_hour

        try:
            ema_fast = Decimal(str(market.ema(self.base_token, period=self.ema_fast_period, timeframe=self.timeframe).value))
            ema_slow = Decimal(str(market.ema(self.base_token, period=self.ema_slow_period, timeframe=self.timeframe).value))
        except _DATA_UNAVAILABLE_ERRORS as exc:
            return Intent.hold(reason=f"EMA data unavailable: {exc}")

        ema_diff = ema_fast - ema_slow
        if self._prev_ema_diff is None:
            self._prev_ema_diff = ema_diff
            return Intent.hold(reason="EMA warmup")

        bull_cross = self._prev_ema_diff <= 0 and ema_diff > 0
        bear_cross = self._prev_ema_diff >= 0 and ema_diff < 0
        self._prev_ema_diff = ema_diff

        if self._position_state == PositionState.FLAT:
            if bull_cross:
                return self._build_open_long_intent(market)
            return Intent.hold(reason="No bullish crossover")

        if self._position_state == PositionState.LONG_OPEN:
            if bear_cross and self.close_on_bear_cross:
                return self._build_close_long_intent(reason="Bearish crossover")
            return Intent.hold(reason="Long open; waiting for bearish crossover")

        return Intent.hold(reason="Unknown state")

    @staticmethod
    def _hour_bucket(ts: datetime) -> str:
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        return ts.astimezone(UTC).replace(minute=0, second=0, microsecond=0).isoformat()

    def _compute_sizing(self, market: MarketSnapshot) -> tuple[Decimal, Decimal] | None:
        try:
            collateral = market.balance(self.collateral_token)
        except _DATA_UNAVAILABLE_ERRORS as exc:
            logger.warning("Balance unavailable: %s", exc)
            return None

        available_usd = Decimal(str(collateral.balance_usd))
        if available_usd <= 0:
            return None

        collateral_budget_usd = available_usd * (self.position_size_pct / Decimal("100"))
        size_usd = collateral_budget_usd * self.leverage
        if size_usd < self.min_order_notional_usd:
            return None

        try:
            collateral_price = Decimal(str(market.price(self.collateral_token)))
        except _DATA_UNAVAILABLE_ERRORS as exc:
            logger.warning("Collateral price unavailable: %s", exc)
            return None

        if collateral_price <= 0:
            return None

        collateral_amount = collateral_budget_usd / collateral_price
        if collateral_amount <= 0:
            return None

        return collateral_amount, size_usd

    def _build_open_long_intent(self, market: MarketSnapshot) -> Intent:
        sizing = self._compute_sizing(market)
        if sizing is None:
            return Intent.hold(reason="Cannot size long position from current collateral")

        collateral_amount, size_usd = sizing
        try:
            self._entry_price = Decimal(str(market.price(self.base_token)))
        except _DATA_UNAVAILABLE_ERRORS:
            self._entry_price = None

        return Intent.perp_open(
            market=self.market,
            collateral_token=self.collateral_token,
            collateral_amount=collateral_amount,
            size_usd=size_usd,
            is_long=True,
            leverage=self.execution_leverage,
            max_slippage=self.max_slippage_bps / Decimal("10000"),
            protocol=self.protocol,
        )

    def _build_close_long_intent(self, *, reason: str) -> Intent:
        return Intent.perp_close(
            market=self.market,
            collateral_token=self.collateral_token,
            is_long=True,
            size_usd=self._open_size_usd,
            max_slippage=self.max_slippage_bps / Decimal("10000"),
            protocol=self.protocol,
        )

    def _forced_intent(self, market: MarketSnapshot) -> Intent:
        if self.force_action == "open_long":
            if self._position_state == PositionState.LONG_OPEN:
                return Intent.hold(reason="Force open ignored: long already open")
            return self._build_open_long_intent(market)

        if self.force_action == "close":
            if self._position_state == PositionState.FLAT:
                return Intent.hold(reason="Force close ignored: no open long")
            return self._build_close_long_intent(reason="Force close")

        return Intent.hold(reason=f"Unsupported force_action: {self.force_action}")

    def on_intent_executed(self, intent: Any, success: bool, result: Any) -> None:
        if not success:
            return

        intent_type = getattr(intent, "intent_type", None)
        type_value = intent_type.value if hasattr(intent_type, "value") else str(intent_type)

        if type_value == "PERP_OPEN":
            self._position_state = PositionState.LONG_OPEN
            self._open_size_usd = Decimal(str(getattr(intent, "size_usd", "0")))
            extracted = getattr(result, "extracted_data", {}) or {}
            entry_price = extracted.get("entry_price") if isinstance(extracted, dict) else None
            if entry_price is not None:
                self._entry_price = Decimal(str(entry_price))
        elif type_value == "PERP_CLOSE":
            self._position_state = PositionState.FLAT
            self._open_size_usd = None
            self._entry_price = None

    def get_persistent_state(self) -> dict[str, Any]:
        return {
            "position_state": self._position_state.value,
            "open_size_usd": str(self._open_size_usd) if self._open_size_usd is not None else None,
            "entry_price": str(self._entry_price) if self._entry_price is not None else None,
            "prev_ema_diff": str(self._prev_ema_diff) if self._prev_ema_diff is not None else None,
            "last_processed_candle_hour": self._last_processed_candle_hour,
        }

    def load_persistent_state(self, state: dict[str, Any]) -> None:
        if not state:
            return
        self._position_state = PositionState(state.get("position_state", PositionState.FLAT.value))

        open_size_usd = state.get("open_size_usd")
        self._open_size_usd = Decimal(str(open_size_usd)) if open_size_usd is not None else None

        entry_price = state.get("entry_price")
        self._entry_price = Decimal(str(entry_price)) if entry_price is not None else None

        prev_ema_diff = state.get("prev_ema_diff")
        self._prev_ema_diff = Decimal(str(prev_ema_diff)) if prev_ema_diff is not None else None

        self._last_processed_candle_hour = state.get("last_processed_candle_hour")

    def get_status(self) -> dict[str, Any]:
        return {
            "strategy": "g_m_x_e_m_a_trend_w_e_t_h",
            "chain": self.chain,
            "market": self.market,
            "position_state": self._position_state.value,
            "open_size_usd": str(self._open_size_usd) if self._open_size_usd is not None else None,
            "entry_price": str(self._entry_price) if self._entry_price is not None else None,
        }

    def supports_teardown(self) -> bool:
        return True

    def get_open_positions(self) -> "TeardownPositionSummary":
        from almanak.framework.teardown import PositionInfo, PositionType, TeardownPositionSummary

        positions: list[PositionInfo] = []
        if self._position_state == PositionState.LONG_OPEN:
            positions.append(
                PositionInfo(
                    position_type=PositionType.PERP,
                    position_id=f"gmx-v2-{self.market}-long",
                    chain=self.chain,
                    protocol=self.protocol,
                    value_usd=self._open_size_usd or Decimal("0"),
                    details={
                        "market": self.market,
                        "is_long": True,
                        "policy": self.teardown_policy,
                    },
                )
            )

        return TeardownPositionSummary(
            deployment_id=getattr(self, "deployment_id", self.STRATEGY_NAME),
            timestamp=datetime.now(UTC),
            positions=positions,
        )

    def generate_teardown_intents(self, mode: "TeardownMode", market: MarketSnapshot | None = None) -> list[Intent]:
        from almanak.framework.teardown import TeardownMode

        if self._position_state != PositionState.LONG_OPEN:
            return []

        soft = self.max_slippage_bps / Decimal("10000")
        hard = max(soft, Decimal("0.02"))
        slippage = hard if mode == TeardownMode.HARD else soft

        return [
            Intent.perp_close(
                market=self.market,
                collateral_token=self.collateral_token,
                is_long=True,
                size_usd=self._open_size_usd,
                max_slippage=slippage,
                protocol=self.protocol,
            )
        ]
