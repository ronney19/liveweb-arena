"""Taostats plugin for Bittensor network data"""

from .taostats import TaostatsPlugin

# Import templates to register them
from . import templates

__all__ = ["TaostatsPlugin"]
