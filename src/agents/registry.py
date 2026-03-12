"""Config-driven agent registry.

Loads agent definitions and mode sequences from config/agents.yaml.
Resolves prompts (disk override then code default) and conclusion types.

Adding a new agent:
1. Add entry in config/agents.yaml
2. Create data/agents/prompts/{name}.md (or src/agents/prompts/{name}.py)
3. Add Pydantic model to conclusions.py
4. Add it to a mode's sub_agents list
"""

import importlib
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Type

import yaml
from pydantic import BaseModel

from . import conclusions as _conclusions_mod

logger = logging.getLogger("steex.registry")

_PROJECT_ROOT = Path(__file__).parent.parent.parent


@dataclass
class AgentConfig:
    """Configuration for a single agent."""

    name: str
    prompt_key: str
    conclusion_name: str
    max_turns: int = 15
    needs_tools: bool = True
    allowed_tools: List[str] = field(default_factory=list)
    external_servers: List[str] = field(default_factory=list)


@dataclass
class ModeConfig:
    """Configuration for a mode's agent sequence."""

    name: str
    sub_agents: List[str] = field(default_factory=list)
    manager: str = "manager"
    critical_agents: List[str] = field(default_factory=list)
    executor: Optional[str] = None
    pre_actions: List[str] = field(default_factory=list)
    post_actions: List[str] = field(default_factory=list)
    fallback: str = ""


class AgentRegistry:
    """Loads agent/mode config and resolves prompts + conclusion types."""

    def __init__(self, config_path: Optional[str] = None):
        self.agents: Dict[str, AgentConfig] = {}
        self.modes: Dict[str, ModeConfig] = {}
        path = Path(config_path) if config_path else _PROJECT_ROOT / "config" / "agents.yaml"
        self._load(path)

    def _load(self, path: Path):
        if not path.exists():
            logger.warning("Agent config not found: %s", path)
            return

        with open(path) as f:
            data = yaml.safe_load(f) or {}

        for name, cfg in data.get("agents", {}).items():
            self.agents[name] = AgentConfig(
                name=name,
                prompt_key=cfg.get("prompt", name),
                conclusion_name=cfg.get("conclusion", ""),
                max_turns=cfg.get("max_turns", 15),
                needs_tools=cfg.get("needs_tools", True),
                allowed_tools=cfg.get("allowed_tools", []),
                external_servers=cfg.get("external_servers", []),
            )

        for name, cfg in data.get("modes", {}).items():
            self.modes[name] = ModeConfig(
                name=name,
                sub_agents=cfg.get("sub_agents", []),
                manager=cfg.get("manager", "manager"),
                critical_agents=cfg.get("critical_agents", []),
                executor=cfg.get("executor"),
                pre_actions=cfg.get("pre_actions", []),
                post_actions=cfg.get("post_actions", []),
                fallback=cfg.get("fallback", name),
            )

        logger.info(
            "Loaded %d agents and %d modes from %s",
            len(self.agents), len(self.modes), path,
        )

    def get_agent(self, name: str) -> Optional[AgentConfig]:
        return self.agents.get(name)

    def get_mode(self, name: str) -> Optional[ModeConfig]:
        return self.modes.get(name)

    def resolve_prompt(self, agent_name: str, data_dir: str = "data") -> str:
        """Resolve the prompt for an agent.

        Priority:
        1. Disk override: data/agents/prompts/{name}.md
        2. Code default: src/agents/prompts/{name}.py (the *_AGENT_PROMPT var)
        """
        agent = self.agents.get(agent_name)
        if agent is None:
            raise ValueError(f"Unknown agent: {agent_name}")

        prompt_key = agent.prompt_key

        # Check disk override first
        disk_path = Path(data_dir) / "agents" / "prompts" / f"{prompt_key}.md"
        if disk_path.exists():
            text = disk_path.read_text().strip()
            if text:
                logger.info("Using disk prompt override for %s: %s", agent_name, disk_path)
                return text

        # Fall back to code default
        var_name = f"{prompt_key.upper()}_AGENT_PROMPT"
        try:
            mod = importlib.import_module(f"src.agents.prompts.{prompt_key}")
            prompt = getattr(mod, var_name, None)
            if prompt:
                return prompt
        except (ImportError, AttributeError):
            pass

        raise ValueError(
            f"No prompt found for agent '{agent_name}' "
            f"(checked {disk_path} and src/agents/prompts/{prompt_key}.py::{var_name})"
        )

    def resolve_conclusion_type(self, agent_name: str) -> Type[BaseModel]:
        """Resolve the Pydantic conclusion model for an agent."""
        agent = self.agents.get(agent_name)
        if agent is None:
            raise ValueError(f"Unknown agent: {agent_name}")

        cls = getattr(_conclusions_mod, agent.conclusion_name, None)
        if cls is None:
            raise ValueError(
                f"Conclusion model '{agent.conclusion_name}' not found in conclusions.py"
            )
        return cls
