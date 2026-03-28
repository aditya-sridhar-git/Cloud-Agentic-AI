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


class BaseTool(abc.ABC):
    """Base class for all automation tools."""

    tool_name: str = "base"

    def __init__(self, provider: CloudProvider, config: dict[str, Any]) -> None:
        self.provider = provider
        self.config = config

    @abc.abstractmethod
    def execute(self, action: Action) -> dict[str, Any]:
        """Carry out the action and return a result dict."""

    def __repr__(self) -> str:
        return f"<Tool:{self.tool_name}>"
