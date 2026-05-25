# Tier 1+2 Trading Upgrades - Test Suite

## Overview

Comprehensive test suite for the parallel variant analysis system (Tier 1) and regime-adaptive screening (Tier 2).

**4 test files | 220+ test cases | 0 external dependencies** (mocks all agent execution)

---

## Running the Tests

### Run All Tests
```bash
python -m pytest tests/agents/test_parallel_execution.py tests/agents/test_variant_mcp_tools.py tests/agents/test_tier1_integration.py tests/conclusions/test_meta_analysis_conclusion.py -v
```

### Run by Category

**Parallel Execution & Graph Building**
```bash
python -m pytest tests/agents/test_parallel_execution.py -v
```

**MCP Tools & Parameter Handling**
```bash
python -m pytest tests/agents/test_variant_mcp_tools.py -v
```

**Consensus Synthesis Logic**
```bash
python -m pytest tests/conclusions/test_meta_analysis_conclusion.py -v
```

**End-to-End Integration**
```bash
python -m pytest tests/agents/test_tier1_integration.py -v
```

### Run Specific Test Class
```bash
python -m pytest tests/agents/test_parallel_execution.py::TestGraphBuildingParallel -v
```

### Run with Coverage
```bash
python -m pytest tests/agents/test_parallel_execution.py tests/agents/test_variant_mcp_tools.py tests/agents/test_tier1_integration.py tests/conclusions/test_meta_analysis_conclusion.py --cov=src.agents --cov=src.conclusions --cov-report=html
```

---

## What Each Test File Covers

### 1. `test_parallel_execution.py` (90+ tests)
**Location:** `tests/agents/test_parallel_execution.py`

**Covers:**
- ✓ Graph building with parallel agents
- ✓ Entry point configuration
- ✓ Fan-out node dispatch
- ✓ State reducer (Annotated[list, add])
- ✓ Variant agent node factories
- ✓ Merge variants node
- ✓ Consensus rules (3/3, 2/3, 1/3)
- ✓ Agent configuration resolution
- ✓ Prompt loading
- ✓ Conclusion type resolution

**Test Classes:**
- `TestGraphBuildingParallel` - Graph structure verification
- `TestFanOutNode` - Fan-out dispatch logic
- `TestStateReducer` - State merging via reducer
- `TestVariantAgentNode` - Variant node factories
- `TestMergeVariantsNode` - Meta-analysis merge
- `TestConsensusRules` - Consensus synthesis logic
- `TestAgentConfigResolution` - Registry and configuration

### 2. `test_variant_mcp_tools.py` (40+ tests)
**Location:** `tests/agents/test_variant_mcp_tools.py`

**Covers:**
- ✓ Variant parameter presets (conservative/aggressive/momentum)
- ✓ Regime parameter mapping (risk_on/cautious/risk_off/crisis)
- ✓ Tool registration and structure
- ✓ Error handling (invalid variants, missing regime)
- ✓ Parameter validation (all optional weights)
- ✓ Output JSON structure
- ✓ Integration with QuantManager globals
- ✓ Settings consistency

**Test Classes:**
- `TestVariantParameterPresets` - Parameter correctness
- `TestRegimeParameterMapping` - Regime overrides
- `TestVariantToolStructure` - Tool registration
- `TestToolErrorHandling` - Error cases
- `TestToolParameterHandling` - Optional params
- `TestToolOutputStructure` - JSON responses
- `TestToolIntegrationPoints` - System integration

### 3. `test_meta_analysis_conclusion.py` (50+ tests)
**Location:** `tests/conclusions/test_meta_analysis_conclusion.py`

**Covers:**
- ✓ ConsensusStock model validation
- ✓ MetaAnalysisConclusion structure
- ✓ Consensus rules (high/medium/speculative)
- ✓ Score averaging across variants
- ✓ Position sizing by conviction level
- ✓ Edge cases (all fail, only speculative, single pick)
- ✓ JSON serialization
- ✓ Compatibility with existing flows

**Test Classes:**
- `TestConsensusStock` - ConsensusStock model
- `TestMetaAnalysisConclusion` - Conclusion structure
- `TestConsensusRules` - Consensus logic (3/3, 2/3, 1/3)
- `TestConsensusScoring` - Score averaging
- `TestPositionSizingByConviction` - Position size allocation
- `TestConsensusEdgeCases` - Edge case handling
- `TestMetaAnalysisCompatibility` - Integration compatibility

### 4. `test_tier1_integration.py` (40+ tests)
**Location:** `tests/agents/test_tier1_integration.py`

**Covers:**
- ✓ Screen mode graph structure
- ✓ Parallel dispatch flow
- ✓ Variant conclusions accumulation
- ✓ Consensus synthesis from variants
- ✓ Full state flow through pipeline
- ✓ Error handling (partial failures)
- ✓ Configuration consistency
- ✓ Agent registry integration
- ✓ Prompt loading

**Test Classes:**
- `TestScreenModeGraphIntegration` - Graph structure
- `TestParallelDispatchFlow` - Fan-out mechanics
- `TestVariantConclusionsMerging` - State accumulation
- `TestConsensusSynthesis` - Consensus picking
- `TestStateFlowThroughPipeline` - State progression
- `TestErrorHandlingThroughPipeline` - Error scenarios
- `TestConfigurationConsistency` - Parameter validation
- `TestAgentRegistryIntegration` - Agent registration

---

## Expected Test Results

### All Tests Should Pass ✓

```
collected 220+ items

tests/agents/test_parallel_execution.py ............................ [25%]
tests/agents/test_variant_mcp_tools.py .......................... [50%]
tests/conclusions/test_meta_analysis_conclusion.py .............. [75%]
tests/agents/test_tier1_integration.py ......................... [100%]

======================== 220+ passed in 4.5s =========================
```

### Failure Scenarios

**If tests fail, it indicates:**
- Missing implementation (check Step 1-9 completion)
- Configuration file syntax errors (check agents.yaml, config.yaml)
- Import errors (verify venv and dependencies)
- Graph structure mismatch (verify graph.py wiring)

---

## What Tests DO NOT Cover

These tests **don't require** actual claude CLI execution:

- ✗ Agent reasoning output (mocked)
- ✗ Subprocess invocation (mocked)
- ✗ Actual screening pipeline (mocked)
- ✗ Trade execution (mocked)
- ✗ MCP server communication (mocked)

**To test actual execution**, manually run:
```bash
python run_manager.py screen --agent --paper --dry-run --verbose
```

---

## Test Dependencies

**Required:**
- pytest
- pytest-cov (optional, for coverage)
- langgraph
- langsmith
- pydantic

**Already in venv:**
All required packages are in `venv/lib/python3.14/site-packages/`

---

## Debugging Test Failures

### Verbose Output
```bash
pytest tests/agents/test_parallel_execution.py -vv
```

### Show Print Statements
```bash
pytest tests/agents/test_parallel_execution.py -s
```

### Stop on First Failure
```bash
pytest tests/agents/test_parallel_execution.py -x
```

### Run Only Tests Matching Pattern
```bash
pytest tests/agents/test_parallel_execution.py -k "TestGraphBuilding" -v
```

### Show Full Traceback
```bash
pytest tests/agents/test_parallel_execution.py --tb=long
```

---

## Test Structure Example

```python
# Each test file follows this pattern:

class TestFeatureName:
    """Test group for a specific feature"""
    
    @pytest.fixture
    def setup_data(self):
        """Create test fixtures"""
        return {"test": "data"}
    
    def test_specific_behavior(self, setup_data):
        """Test one specific behavior"""
        assert setup_data["test"] == "data"
    
    def test_error_case(self):
        """Test error handling"""
        with pytest.raises(ValueError):
            raise ValueError("Test error")
```

---

## Running in CI/CD

For GitHub Actions or other CI:

```yaml
- name: Run Tier 1+2 Tests
  run: |
    cd /path/to/STEEX
    python -m pytest tests/agents/test_parallel_execution.py \
                     tests/agents/test_variant_mcp_tools.py \
                     tests/agents/test_tier1_integration.py \
                     tests/conclusions/test_meta_analysis_conclusion.py \
                     -v --tb=short
```

---

## Test Metrics

**Lines of Test Code:** ~2,500+
**Test Classes:** 28
**Individual Test Cases:** 220+
**Coverage Areas:** 8 major components

**By Category:**
- Graph & Nodes: 60 tests
- MCP Tools: 40 tests
- Consensus Logic: 50 tests
- Integration: 40 tests
- Configuration: 30 tests

---

## When to Run Tests

1. **After changes to `src/agents/nodes.py`** → Run `test_parallel_execution.py`
2. **After changes to `src/agents/mcp_server.py`** → Run `test_variant_mcp_tools.py`
3. **After changes to `src/agents/conclusions.py`** → Run `test_meta_analysis_conclusion.py`
4. **After changes to `config/agents.yaml`** → Run `test_tier1_integration.py`
5. **Before running actual pipeline** → Run all tests

---

## Troubleshooting

### Import Error: "No module named src.agents"
```bash
cd /path/to/STEEX
python -m pytest tests/...
```

### No tests collected
```bash
# Ensure __init__.py exists in test directories
touch tests/__init__.py
touch tests/agents/__init__.py
touch tests/conclusions/__init__.py
```

### ModuleNotFoundError in test
```bash
# Rebuild your venv and reinstall project
python -m venv venv
source venv/bin/activate
pip install -e .
```

### Tests pass but warnings appear
This is expected (Pydantic v1 compatibility warnings). Tests still pass.

---

## Next Steps

After all tests pass:
1. ✓ All imports work
2. ✓ Graph builds correctly
3. ✓ State reducer functions
4. ✓ Consensus logic validates
5. ✓ Configuration is consistent

**Then manually test:**
```bash
python run_manager.py screen --agent --paper --dry-run --verbose
```

This will exercise the actual claude CLI and verify end-to-end execution.
