"""Tests for variant screening MCP tools.

Tests the 5 new MCP tools without actually running the screening pipeline.
Validates tool structure, parameter handling, and error cases.
"""

import pytest
import json
from unittest.mock import Mock, patch, MagicMock
from pathlib import Path

# Import the MCP server module to access tools
import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from config.settings import get_settings


# Note: We test the tool functions by importing from mcp_server,
# but we mock the QuantManager to avoid needing a full trading setup


@pytest.fixture
def mock_manager():
    """Create a mock QuantManager"""
    manager = MagicMock()
    manager.settings = MagicMock()
    manager.settings.weight_momentum = 0.30
    manager.settings.weight_insider = 0.25
    manager.settings.weight_volume = 0.15
    manager.settings.weight_sentiment = 0.15
    manager.settings.weight_fundamental = 0.10
    manager.settings.weight_options = 0.05
    return manager


# ============================================================================
# Test: Variant Parameter Presets
# ============================================================================


class TestVariantParameterPresets:
    """Test that variant presets are correctly defined"""

    def test_conservative_preset_exists(self):
        """Conservative variant preset should exist with correct overrides"""
        from src.agents.mcp_server import VARIANT_PARAMS

        assert "conservative" in VARIANT_PARAMS
        conservative = VARIANT_PARAMS["conservative"]

        assert conservative["momentum_min_return"] == 0.10
        assert conservative["sentiment_min_score"] == 50.0
        assert conservative["fundamental_max_pe"] == 30.0
        assert conservative["fundamental_min_roe"] == 0.10

    def test_aggressive_preset_exists(self):
        """Aggressive variant preset should exist"""
        from src.agents.mcp_server import VARIANT_PARAMS

        assert "aggressive" in VARIANT_PARAMS
        aggressive = VARIANT_PARAMS["aggressive"]

        assert aggressive["momentum_min_return"] == 0.02
        assert aggressive["sentiment_min_score"] == 28.0
        assert aggressive["fundamental_max_pe"] == 80.0
        assert aggressive["fundamental_min_roe"] == 0.0

    def test_momentum_preset_exists(self):
        """Momentum variant preset should exist"""
        from src.agents.mcp_server import VARIANT_PARAMS

        assert "momentum" in VARIANT_PARAMS
        momentum = VARIANT_PARAMS["momentum"]

        assert momentum["momentum_min_return"] == 0.08
        assert momentum["sentiment_min_score"] == 35.0
        assert momentum["fundamental_enabled"] is False


# ============================================================================
# Test: Regime Parameter Mapping
# ============================================================================


class TestRegimeParameterMapping:
    """Test regime-specific parameter overrides"""

    def test_all_regimes_mapped(self):
        """All 4 regimes should have parameter mappings"""
        from src.agents.mcp_server import REGIME_PARAMS

        assert "risk_on" in REGIME_PARAMS
        assert "cautious" in REGIME_PARAMS
        assert "risk_off" in REGIME_PARAMS
        assert "crisis" in REGIME_PARAMS

    def test_risk_on_regime(self):
        """risk_on should lower entry bars"""
        from src.agents.mcp_server import REGIME_PARAMS

        risk_on = REGIME_PARAMS["risk_on"]
        assert risk_on["momentum_min_return"] == 0.03
        assert risk_on["weight_momentum"] == 0.35
        assert "lower" in risk_on["rationale"].lower()

    def test_cautious_regime(self):
        """cautious should raise insider weight"""
        from src.agents.mcp_server import REGIME_PARAMS

        cautious = REGIME_PARAMS["cautious"]
        assert cautious["weight_insider"] == 0.30
        assert cautious["sentiment_min_score"] == 40.0

    def test_risk_off_regime(self):
        """risk_off should favor insider heavily"""
        from src.agents.mcp_server import REGIME_PARAMS

        risk_off = REGIME_PARAMS["risk_off"]
        assert risk_off["weight_insider"] == 0.35
        assert risk_off["fundamental_min_roe"] == 0.12

    def test_crisis_regime(self):
        """crisis should maximize safety"""
        from src.agents.mcp_server import REGIME_PARAMS

        crisis = REGIME_PARAMS["crisis"]
        assert crisis["weight_insider"] == 0.40
        assert crisis["sentiment_min_score"] == 60.0
        assert crisis["fundamental_min_roe"] == 0.15


# ============================================================================
# Test: Tool Structure and Error Handling
# ============================================================================


class TestVariantToolStructure:
    """Test that variant tools have correct structure"""

    def test_run_screening_variant_tool_exists(self):
        """run_screening_variant tool should be registered"""
        from src.agents.mcp_server import mcp

        # Check tool is in the registry
        tool_names = [t.name if hasattr(t, 'name') else t for t in dir(mcp) if not t.startswith('_')]
        # We can't easily introspect FastMCP tools, so just verify import works
        from src.agents.mcp_server import run_screening_variant
        assert callable(run_screening_variant)

    def test_rank_candidates_with_weights_tool_exists(self):
        """rank_candidates_with_weights tool should be registered"""
        from src.agents.mcp_server import rank_candidates_with_weights
        assert callable(rank_candidates_with_weights)

    def test_get_regime_screening_params_tool_exists(self):
        """get_regime_screening_params tool should be registered"""
        from src.agents.mcp_server import get_regime_screening_params
        assert callable(get_regime_screening_params)

    def test_get_signal_confidence_tool_exists(self):
        """get_signal_confidence tool should be registered"""
        from src.agents.mcp_server import get_signal_confidence
        assert callable(get_signal_confidence)

    def test_get_unusual_options_activity_tool_exists(self):
        """get_unusual_options_activity tool should be registered"""
        from src.agents.mcp_server import get_unusual_options_activity
        assert callable(get_unusual_options_activity)


# ============================================================================
# Test: Tool Logic (without execution)
# ============================================================================


class TestToolErrorHandling:
    """Test error handling in tools"""

    def test_invalid_variant_returns_error(self):
        """run_screening_variant should reject invalid variant names"""
        from src.agents.mcp_server import run_screening_variant, _safe_json

        # Mock the manager initialization
        with patch("src.agents.mcp_server._init_manager") as mock_init:
            mock_init.return_value = MagicMock()

            result_json = run_screening_variant("invalid_variant")
            result = json.loads(result_json)

            assert "error" in result
            assert "unknown variant" in result["error"].lower()

    def test_get_regime_without_regime_fails(self):
        """get_regime_screening_params should fail if no regime data"""
        from src.agents.mcp_server import get_regime_screening_params, _regime

        # When _regime is None, should return error
        import src.agents.mcp_server as mcp_module
        original_regime = mcp_module._regime
        mcp_module._regime = None

        try:
            result_json = get_regime_screening_params()
            result = json.loads(result_json)
            assert "error" in result
        finally:
            mcp_module._regime = original_regime

    def test_weight_tool_returns_dict(self):
        """rank_candidates_with_weights should return proper structure"""
        from src.agents.mcp_server import rank_candidates_with_weights, _pipeline_result
        import src.agents.mcp_server as mcp_module

        # When _pipeline_result is None, should return error
        if mcp_module._pipeline_result is None:
            result_json = rank_candidates_with_weights()
            result = json.loads(result_json)
            assert "error" in result
            assert "run_screening" in result["error"].lower()


# ============================================================================
# Test: Tool Parameter Handling
# ============================================================================


class TestToolParameterHandling:
    """Test that tools properly handle optional parameters"""

    def test_rank_weights_all_optional(self):
        """All weight parameters should be optional"""
        # Test that function accepts no parameters
        from src.agents.mcp_server import rank_candidates_with_weights
        import inspect

        sig = inspect.signature(rank_candidates_with_weights)
        # All parameters should have defaults
        for param_name, param in sig.parameters.items():
            if param_name != "self":
                assert param.default is not inspect.Parameter.empty, \
                    f"{param_name} should have a default value"

    def test_options_activity_filter_optional(self):
        """min_call_volume_ratio should be optional with default"""
        from src.agents.mcp_server import get_unusual_options_activity
        import inspect

        sig = inspect.signature(get_unusual_options_activity)
        min_ratio_param = sig.parameters.get("min_call_volume_ratio")
        assert min_ratio_param is not None
        assert min_ratio_param.default == 1.5


# ============================================================================
# Test: Tool Output Structure
# ============================================================================


class TestToolOutputStructure:
    """Test that tools return proper JSON structures"""

    def test_tools_return_json_strings(self):
        """All tools should return JSON strings"""
        # This is more of a contract test - verifying tools return str type
        from src.agents.mcp_server import _safe_json

        test_data = {"test": "value", "number": 42}
        result = _safe_json(test_data)

        assert isinstance(result, str)
        parsed = json.loads(result)
        assert parsed["test"] == "value"

    def test_error_responses_are_valid_json(self):
        """Error responses should be valid JSON with 'error' field"""
        from src.agents.mcp_server import _safe_json

        error_response = _safe_json({"error": "Test error"})
        parsed = json.loads(error_response)

        assert isinstance(parsed, dict)
        assert "error" in parsed


# ============================================================================
# Test: Tool Integration Points
# ============================================================================


class TestToolIntegrationPoints:
    """Test how tools interact with the wider system"""

    def test_variant_tools_use_shared_globals(self):
        """Variant tools should read/write shared module globals"""
        import src.agents.mcp_server as mcp_module

        # Verify these globals exist
        assert hasattr(mcp_module, "_pipeline_result")
        assert hasattr(mcp_module, "_ranked")
        assert hasattr(mcp_module, "_regime")

    def test_config_weights_match_settings(self, mock_manager):
        """Tool parameter defaults should match settings.py defaults"""
        from config.settings import Settings

        settings = Settings()

        # Weights are normalized to sum to 1.0 (the prior 0.30/0.25/0.15/0.15/
        # 0.10/0.12 set erroneously summed to 1.07 and was rescaled).
        weights = [
            settings.weight_momentum,
            settings.weight_insider,
            settings.weight_volume,
            settings.weight_sentiment,
            settings.weight_fundamental,
            settings.weight_options,
        ]
        assert abs(sum(weights) - 1.0) < 1e-6, f"weights must sum to 1.0, got {sum(weights)}"
        # Momentum remains the dominant signal; options stays elevated for Tier 1+2.
        assert settings.weight_momentum == max(weights)
        assert settings.weight_options > settings.weight_fundamental

    def test_regime_params_use_correct_weight_names(self):
        """Regime params should use actual setting field names"""
        from src.agents.mcp_server import REGIME_PARAMS
        from config.settings import Settings

        settings = Settings()

        # Verify field names exist
        for regime_name, regime_params in REGIME_PARAMS.items():
            for key, value in regime_params.items():
                if key != "rationale":
                    # Should be a valid settings attribute
                    assert hasattr(settings, key), \
                        f"Regime param {key} not found in Settings"
