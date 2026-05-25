"""LangGraph state definitions and runner context.

PipelineState is the immutable state dict passed through the LangGraph StateGraph.
RunnerContext carries runtime configuration and dependencies for node functions.
"""

from dataclasses import dataclass
from operator import add
from pathlib import Path
from typing import Annotated, Optional
from typing_extensions import TypedDict

from config.settings import Settings
from .registry import AgentRegistry
from .evolution import PromptEvolver


@dataclass
class RunnerContext:
    """Runtime configuration and dependencies passed to node functions."""
    settings: Settings
    paper: bool
    dry_run: bool
    auto_confirm: bool
    verbose: bool
    registry: AgentRegistry
    evolver: PromptEvolver
    project_root: Path
    mcp_config_path: Optional[str] = None


class PipelineState(TypedDict):
    """Immutable state dict flowing through the LangGraph StateGraph.

    Nodes return partial dicts that are merged into state. All values must be
    JSON-serializable for LangSmith tracing.

    variant_conclusions uses Annotated[list, add] reducer: when parallel nodes
    each return {"variant_conclusions": [item]}, they are concatenated into a
    single list instead of overwriting.
    """
    mode: str
    task_context: str
    today: str
    run_id: str
    conclusions: dict[str, dict]
    variant_conclusions: Annotated[list[dict], add]
    traces: list[dict]
    manager_decision: Optional[dict]
    screen_data: Optional[dict]
    abort: bool
    abort_reason: Optional[str]
