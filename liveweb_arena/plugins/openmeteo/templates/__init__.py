"""Open Meteo question templates"""

from .current_weather import OpenMeteoCurrentWeatherTemplate
from .comparison import OpenMeteoComparisonTemplate
from .hourly_extrema import OpenMeteoHourlyExtremaTemplate
from .forecast_trend import OpenMeteoForecastTrendTemplate

__all__ = [
    "OpenMeteoCurrentWeatherTemplate",
    "OpenMeteoComparisonTemplate",
    "OpenMeteoHourlyExtremaTemplate",
    "OpenMeteoForecastTrendTemplate",
]
