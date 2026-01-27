"""Plugin system for LiveWeb Arena"""

import importlib
from typing import Optional, Type, Set

from .base import BasePlugin


def get_all_plugin_names() -> Set[str]:
    """Get all registered plugin names from TaskRegistry."""
    from liveweb_arena.core.task_registry import TaskRegistry

    plugin_names = set()
    for plugin_name, template_name in TaskRegistry.TEMPLATES.values():
        plugin_names.add(plugin_name)
    return plugin_names


def get_plugin_class(name: str) -> Optional[Type[BasePlugin]]:
    """
    Get plugin class by name using dynamic import.

    Uses the plugin's __all__ export to find the class name.

    Args:
        name: Plugin name (e.g., "coingecko", "stooq", "hybrid")

    Returns:
        Plugin class or None if not found
    """
    module_path = f"liveweb_arena.plugins.{name}"
    try:
        module = importlib.import_module(module_path)
        # Get class name from __all__
        if hasattr(module, "__all__") and module.__all__:
            class_name = module.__all__[0]
            return getattr(module, class_name)
        return None
    except (ImportError, AttributeError):
        return None
