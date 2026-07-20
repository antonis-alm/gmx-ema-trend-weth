# GMXEMATrendWETHStrategy - Agent Guide

> AI coding agent context for the `g_m_x_e_m_a_trend_w_e_t_h` strategy.

## Overview

- **Template:** perps
- **Chain:** arbitrum
- **Class:** `GMXEMATrendWETHStrategy` in `strategy.py`
- **Config:** `config.json`

Dependencies are declared in `pyproject.toml`.

## Files

| File | Purpose |
|------|---------|
| `strategy.py` | Main strategy - edit `decide()` to change trading logic |
| `config.json` | Runtime parameters (tokens, thresholds, chain) |
| `pyproject.toml` | Dependencies plus metadata (`framework`, `version`, `run.interval`) |
| `.env` | Secrets (private key, API keys) - never commit this |
| `.gitignore` | Git ignore rules (excludes `.venv/`, `.env`, etc.) |
| `.python-version` | Python version pin (3.12) |
| `tests/test_strategy.py` | Unit tests for the strategy |

## How to Run

```bash
# Single iteration on Anvil fork (safe, no real funds)
almanak strat run --network anvil --once

# Single iteration on mainnet
almanak strat run --once

# Continuous with 30s interval
almanak strat run --network anvil --interval 30

# Dry run (no transactions)
almanak strat run --dry-run --once
```

## Adding Dependencies

Edit the `dependencies` list in `pyproject.toml`.

## Config Parameters

| Parameter | Type | Description |
|-----------|------|-------------|
| `perp_market` | string | Perpetual market (e.g. ETH/USD) |
| `collateral_token` | string | Collateral token (e.g. USDC) |
| `collateral_amount` | string (Decimal) | Collateral per trade |
| `position_size_usd` | string (Decimal) | Position size in USD |
| `leverage` | string (Decimal) | Leverage multiplier |
| `take_profit_pct` | string (Decimal) | Take profit percentage (e.g. 0.05 = 5%) |
| `stop_loss_pct` | string (Decimal) | Stop loss percentage (e.g. 0.03 = 3%) |
| `base_token` | string | Token for price checks (e.g. ETH) |


All values in `config.json` are read via `self.config.get("key", default)` in `__init__`.
String-typed Decimals (e.g. `"0.005"`) are used to avoid floating-point precision issues.

## Intent Types Used

This strategy uses these intent types:

- `Intent.perp_open(market, collateral_token, collateral_amount, size_usd, is_long=True)`
- `Intent.perp_close(market, collateral_token, is_long, size_usd=None)`
- `Intent.hold(reason="...")`

All intents are created via `from almanak.framework.intents import Intent`.

## Key Patterns

- `decide(market)` receives a `MarketSnapshot` with `market.price()`, `market.balance()`, `market.rsi()`, etc.
- Return an `Intent` object or `Intent.hold(reason=...)` from `decide()`
- Always wrap `decide()` logic in try/except, returning `Intent.hold()` on error
- Config values are read via `self.config.get("key", default)` in `__init__`
- State persists between iterations via `self.state` dict

## Teardown (Required)

Every `IntentStrategy` **must** implement two abstract teardown methods.
Strategies that hold no positions can extend `StatelessStrategy` instead.

| Method | Purpose |
|--------|---------|
| `get_open_positions() -> TeardownPositionSummary` | List positions to close (query on-chain state, not cache) |
| `generate_teardown_intents(mode, market) -> list[Intent]` | Return ordered intents to unwind positions |

**Execution order** (if multiple position types): PERP -> BORROW -> SUPPLY -> LP -> TOKEN

The generated `strategy.py` includes teardown stubs with TODO comments -- fill them in.
See `docs/internal/blueprints/14-teardown-system.md` for the full teardown system reference.

## Testing

```bash
# Unit tests
pytest tests/ -v

# Lifecycle + teardown on a managed Anvil fork
# (drives each force_action through the production code path, then runs teardown)
almanak strat test --actions <csv> --teardown --json

# Paper trade (Anvil fork with PnL tracking)
almanak strat backtest paper --duration 3600 --interval 60

# PnL backtest (historical prices)
almanak strat backtest pnl --start 2024-01-01 --end 2024-06-01
```

## Full SDK Reference

For the complete intent vocabulary, market data API, and advanced patterns,
install the full agent skill:

```bash
almanak agent install
```

Or read the bundled skill directly:

```bash
almanak docs agent-skill --dump
```
