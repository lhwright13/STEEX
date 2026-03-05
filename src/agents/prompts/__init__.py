"""System prompts for each agent role."""

from .data import DATA_AGENT_PROMPT
from .risk import RISK_AGENT_PROMPT
from .analysis import ANALYSIS_AGENT_PROMPT
from .manager import MANAGER_AGENT_PROMPT
from .execution import EXECUTION_AGENT_PROMPT
from .research import RESEARCH_AGENT_PROMPT
from .report import REPORT_AGENT_PROMPT

__all__ = [
    "DATA_AGENT_PROMPT",
    "RISK_AGENT_PROMPT",
    "ANALYSIS_AGENT_PROMPT",
    "MANAGER_AGENT_PROMPT",
    "EXECUTION_AGENT_PROMPT",
    "RESEARCH_AGENT_PROMPT",
    "REPORT_AGENT_PROMPT",
]
