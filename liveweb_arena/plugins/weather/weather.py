"""
Weather Plugin.

Plugin for weather data from wttr.in.
"""

import re
from typing import Any, Dict, List
from urllib.parse import urlparse, unquote

from liveweb_arena.plugins.base import BasePlugin
from .api_client import fetch_single_location_data


class WeatherPlugin(BasePlugin):
    """
    Weather plugin for wttr.in data.

    Handles pages like:
    - https://wttr.in/London
    - https://wttr.in/New+York

    API data includes: current_condition, 3-day forecast, astronomy, etc.
    """

    name = "weather"

    allowed_domains = [
        "wttr.in",
    ]

    @property
    def usage_hint(self) -> str:
        """Usage hint for weather data access."""
        return (
            "Use wttr.in to find weather information. "
            "Add ?format=j1 to get JSON data with detailed fields like humidity, wind speed, etc. "
            "(e.g., wttr.in/London?format=j1)"
        )

    def get_blocked_patterns(self) -> List[str]:
        """Block v2 version which uses images instead of ASCII art."""
        return [
            "*v2.wttr.in*",    # Block v2 version (uses images instead of ASCII art)
        ]

    def needs_api_data(self, url: str) -> bool:                                                                                                                   
        """Only location pages need API data."""                                                                                                                  
        return bool(self._extract_location(url))    

    async def fetch_api_data(self, url: str) -> Dict[str, Any]:
        """
        Fetch API data for a wttr.in weather page.

        Extracts location from URL and fetches weather data.

        Args:
            url: Page URL (e.g., https://wttr.in/London)

        Returns:
            Complete weather JSON data from wttr.in
        """
        location = self._extract_location(url)
        if not location:
            return {}

        data = await fetch_single_location_data(location)
        if data:
            # Add location key for GT collector
            data["location"] = location
        return data if data else {}

    def _extract_location(self, url: str) -> str:
        """
        Extract location from wttr.in URL.

        Examples:
            https://wttr.in/London -> London
            https://wttr.in/New+York -> New+York
        """
        parsed = urlparse(url)
        path = parsed.path.strip("/")

        if not path:
            return ""

        # Decode URL encoding
        location = unquote(path)

        # Remove format suffix if present
        location = re.sub(r'\?.*$', '', location)

        return location
