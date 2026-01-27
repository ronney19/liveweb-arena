"""Taostats plugin for Bittensor network data"""

# Pre-import bittensor with logging protection
# bittensor overrides global logging config on import, so we save and restore it
def _safe_import_bittensor():
    """Import bittensor without disrupting logging configuration."""
    try:
        import logging
        # Save current logging config
        saved_handlers = logging.root.handlers[:]
        saved_level = logging.root.level

        import bittensor as bt

        # Restore logging config
        logging.root.handlers = saved_handlers
        logging.root.level = saved_level

        return bt
    except ImportError:
        return None


# Import bittensor once at module load to prevent repeated logging disruption
bt = _safe_import_bittensor()

from .taostats import TaostatsPlugin

__all__ = ["TaostatsPlugin", "bt"]
