"""
BaseTool abstract class and tool registry.

Each automation tool inherits :class:`BaseTool` and uses the
``@register_tool`` decorator so the orchestrator can discover them
automatically.
"""

from __future__ import annotations

import abc
from typing import Any

from cloud_agent.agent.baseagent import Action
from cloud_agent.cloud.provider import CloudProvider
from cloud_agent.utils.logger import get_logger

logger = get_logger(__name__)

# Global tool registry
_TOOL_REGISTRY: dict[str, type["BaseTool"]] = {}


def register_tool(name: str):
    """Class decorator that registers a tool under *name*."""

    def wrapper(cls: type[BaseTool]) -> type[BaseTool]:
        _TOOL_REGISTRY[name] = cls
        cls.tool_name = name
        return cls

    return wrapper


def get_tool_registry() -> dict[str, type["BaseTool"]]:
    """Return the global tool registry."""
    return dict(_TOOL_REGISTRY)


def load_all_tools() -> dict[str, type["BaseTool"]]:
    """Import every module in ``cloud_agent.tools`` so all tools register."""
    import importlib
    import pkgutil

    import cloud_agent.tools as tools_pkg

    for mod in pkgutil.iter_modules(tools_pkg.__path__):
        if mod.name != "base_tool":
            importlib.import_module(f"{tools_pkg.__name__}.{mod.name}")
    return get_tool_registry()


# Action types that only read state. Everything else changes infrastructure
# and is therefore blocked in dry-run mode.
READ_ONLY_ACTIONS: frozenset[str] = frozenset({
    "check", "report", "recommend", "alert", "diagnose", "full_scan",
    "check_security_groups", "check_s3_public", "check_ebs_encryption",
    "correlate", "analyze",
})


def is_mutating(action: Action) -> bool:
    """Return True if executing *action* would change cloud resources."""
    return action.action_type not in READ_ONLY_ACTIONS


class BaseTool(abc.ABC):
    """Base class for all automation tools."""

    tool_name: str = "base"

    def __init__(self, provider: CloudProvider, config: dict[str, Any]) -> None:
        self.provider = provider
        self.config = config

    @property
    def dry_run(self) -> bool:
        """Whether the agent is running in dry-run mode."""
        return self.config.get("agent", {}).get("dry_run", True)

    @abc.abstractmethod
    def execute(self, action: Action) -> dict[str, Any]:
        """Carry out the action and return a result dict."""

    def __repr__(self) -> str:
        return f"<Tool:{self.tool_name}>"
