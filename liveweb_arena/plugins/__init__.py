"""
Plugin Auto-Discovery System.

Automatically discovers and loads plugins from subdirectories.

Usage:
    from liveweb_arena.plugins import get_plugin, get_all_plugins

    # Get a specific plugin
    plugin = get_plugin("coingecko")

    # Get all plugins
    plugins = get_all_plugins()
"""

import importlib
import logging
from pathlib import Path
from typing import Dict, List, Optional, Type

from .base import BasePlugin

logger = logging.getLogger(__name__)

# Plugin registry: {plugin_name: plugin_class}
_plugins: Dict[str, Type[BasePlugin]] = {}

# Temporarily disabled plugins (e.g. external service unavailable).
# Remove entries here when the service comes back online.
DISABLED_PLUGINS: set = set()


def _discover_plugins():
    """Discover and load all plugins from subdirectories."""
    plugins_dir = Path(__file__).parent

    for subdir in plugins_dir.iterdir():
        if not subdir.is_dir():
            continue
        if subdir.name.startswith("_") or subdir.name.startswith("."):
            continue
        if subdir.name == "__pycache__":
            continue
        if subdir.name in DISABLED_PLUGINS:
            logger.info(f"Skipping disabled plugin: {subdir.name}")
            continue

        # Check if it's a valid plugin directory (has __init__.py)
        init_file = subdir / "__init__.py"
        if not init_file.exists():
            continue

        _load_plugin(subdir.name)


def _load_plugin(name: str) -> Optional[Type[BasePlugin]]:
    """Load a single plugin by name."""
    if name in _plugins:
        return _plugins[name]

    module_path = f"liveweb_arena.plugins.{name}"

    try:
        module = importlib.import_module(module_path)

        # Find plugin class from __all__
        if hasattr(module, "__all__") and module.__all__:
            class_name = module.__all__[0]
            plugin_class = getattr(module, class_name, None)

            if plugin_class and isinstance(plugin_class, type) and issubclass(plugin_class, BasePlugin):
                # Get plugin name from class attribute or directory name
                plugin_name = getattr(plugin_class, "name", name)
                _plugins[plugin_name] = plugin_class

                # Also load templates
                _load_templates(name)

                return plugin_class

    except Exception as e:
        logger.warning(f"Failed to load plugin {name}: {e}")

    return None


def _load_templates(plugin_name: str):
    """Load templates for a plugin."""
    templates_module = f"liveweb_arena.plugins.{plugin_name}.templates"

    try:
        importlib.import_module(templates_module)
    except ImportError:
        # No templates module, that's OK
        pass
    except Exception as e:
        logger.warning(f"Failed to load templates for {plugin_name}: {e}")


def get_plugin(name: str) -> Optional[Type[BasePlugin]]:
    """
    Get a plugin class by name.

    Args:
        name: Plugin name (e.g., "coingecko", "stooq")

    Returns:
        Plugin class or None if not found
    """
    if not _plugins:
        _discover_plugins()

    return _plugins.get(name)


def get_all_plugins() -> Dict[str, Type[BasePlugin]]:
    """
    Get all registered plugins.

    Returns:
        {plugin_name: plugin_class} mapping
    """
    if not _plugins:
        _discover_plugins()

    return dict(_plugins)


def get_plugin_names() -> List[str]:
    """
    Get all plugin names.

    Returns:
        List of plugin names
    """
    if not _plugins:
        _discover_plugins()

    return list(_plugins.keys())


def reload_plugins():
    """Reload all plugins (useful for development)."""
    _plugins.clear()
    _discover_plugins()


# Backward compatibility
def get_plugin_class(name: str) -> Optional[Type[BasePlugin]]:
    """Deprecated: Use get_plugin() instead."""
    return get_plugin(name)


def get_all_plugin_names():
    """Deprecated: Use get_plugin_names() instead."""
    return set(get_plugin_names())
