from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from dashboard.ui import render_custom_dashboard


def test_render_custom_dashboard_uses_gmx_template() -> None:
    strategy_config = {
        "market": "ETH/USD",
        "collateral_token": "USDC",
        "chain": "arbitrum",
    }
    session_state = {"has_position": False}
    fake_config = SimpleNamespace(name="gmx")

    with (
        patch("dashboard.ui.st.title") as mock_title,
        patch("dashboard.ui._get_gmx_v2_config", return_value=fake_config) as mock_get_config,
        patch("dashboard.ui._render_perp_dashboard") as mock_render,
    ):
        render_custom_dashboard("dep-1", strategy_config, api_client=None, session_state=session_state)

    mock_title.assert_called_once_with("GMX EMA Trend WETH")
    mock_get_config.assert_called_once_with(
        market="ETH/USD",
        collateral_token="USDC",
        chain="arbitrum",
    )
    mock_render.assert_called_once_with("dep-1", strategy_config, session_state, fake_config)


def test_render_custom_dashboard_falls_back_to_defaults() -> None:
    fake_config = SimpleNamespace(name="gmx")

    with (
        patch("dashboard.ui.st.title"),
        patch("dashboard.ui._get_gmx_v2_config", return_value=fake_config) as mock_get_config,
        patch("dashboard.ui._render_perp_dashboard"),
    ):
        render_custom_dashboard("dep-2", {}, api_client=None, session_state={})

    mock_get_config.assert_called_once_with(
        market="ETH/USD",
        collateral_token="USDC",
        chain="arbitrum",
    )


def test_render_custom_dashboard_prefers_perp_market_override() -> None:
    strategy_config = {
        "perp_market": "WETH/USD",
        "market": "ETH/USD",
        "collateral_token": "USDC",
        "chain": "arbitrum",
    }

    with (
        patch("dashboard.ui.st.title"),
        patch("dashboard.ui._get_gmx_v2_config") as mock_get_config,
        patch("dashboard.ui._render_perp_dashboard"),
    ):
        render_custom_dashboard("dep-3", strategy_config, api_client=None, session_state={})

    mock_get_config.assert_called_once_with(
        market="WETH/USD",
        collateral_token="USDC",
        chain="arbitrum",
    )
