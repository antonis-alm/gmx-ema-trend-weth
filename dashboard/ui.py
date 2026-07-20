"""GMX EMA Trend WETH dashboard."""

from typing import Any

try:
    import streamlit as st
except ModuleNotFoundError:  # pragma: no cover - fallback for test environments
    class _StreamlitFallback:
        @staticmethod
        def title(*args: Any, **kwargs: Any) -> None:
            return None

    st = _StreamlitFallback()


def _get_gmx_v2_config(market: str, collateral_token: str, chain: str) -> Any:
    from almanak.framework.dashboard.templates import get_gmx_v2_config

    return get_gmx_v2_config(
        market=market,
        collateral_token=collateral_token,
        chain=chain,
    )


def _render_perp_dashboard(
    deployment_id: str,
    strategy_config: dict[str, Any],
    session_state: dict[str, Any],
    config: Any,
) -> None:
    from almanak.framework.dashboard.templates import render_perp_dashboard

    render_perp_dashboard(deployment_id, strategy_config, session_state, config)


def render_custom_dashboard(
    deployment_id: str,
    strategy_config: dict[str, Any],
    api_client: Any,
    session_state: dict[str, Any],
) -> None:
    st.title("GMX EMA Trend WETH")

    config = _get_gmx_v2_config(
        market=str(strategy_config.get("perp_market", strategy_config.get("market", "ETH/USD"))),
        collateral_token=str(strategy_config.get("collateral_token", "USDC")),
        chain=str(strategy_config.get("chain", "arbitrum")),
    )

    _render_perp_dashboard(deployment_id, strategy_config, session_state, config)
