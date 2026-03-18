"""Open Meteo plugin package"""

from .openmeteo import OpenMeteoPlugin

# Import templates to register them
from . import templates

__all__ = ["OpenMeteoPlugin"]
