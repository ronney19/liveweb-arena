"""
Open Meteo Plugin.

Uses the Open Meteo docs page as the browsable interface.
The agent navigates to open-meteo.com/en/docs with location coordinates
encoded as both query params (for cache uniqueness) and hash fragment
(for client-side JS form state).

API data is fetched via the Open Meteo forecast API for GT extraction.
"""

from typing import Any, Dict, List
from urllib.parse import urlparse, parse_qs, unquote

from liveweb_arena.plugins.base import BasePlugin
from .api_client import fetch_forecast


class OpenMeteoPlugin(BasePlugin):
    """
    Open Meteo plugin for weather forecast data.

    Handles the docs page:
    - https://open-meteo.com/en/docs?latitude=35.68&longitude=139.65#...

    Query params provide unique cache keys (normalize_url preserves them).
    Hash fragment configures the client-side JS form/chart.

    API data includes: current weather, hourly forecasts, daily aggregates,
    sunrise/sunset times.
    """

    name = "openmeteo"

    allowed_domains = [
        "open-meteo.com",
    ]

    def get_blocked_patterns(self) -> List[str]:
        """No blocks — the docs page JS needs api.open-meteo.com for chart rendering."""
        return []

    def needs_api_data(self, url: str) -> bool:
        """Only docs pages with coordinates need API data."""
        lat, lon = self._extract_coords(url)
        return lat is not None

    async def fetch_api_data(self, url: str) -> Dict[str, Any]:
        """
        Fetch forecast data for the location encoded in the URL.

        Extracts latitude/longitude from query params or hash fragment,
        then calls the Open Meteo forecast API.
        """
        lat, lon = self._extract_coords(url)
        if lat is None or lon is None:
            return {}

        data = await fetch_forecast(lat, lon)
        # Add a location key for GT collector identification
        data["_location_key"] = f"{lat:.2f},{lon:.2f}"
        return data

    def _extract_coords(self, url: str) -> tuple:
        """
        Extract latitude and longitude from URL.

        Tries query params first (preserved by normalize_url), then hash
        fragment as fallback. Both formats are present in docs_url().
        """
        parsed = urlparse(url)

        # Query params first (reliable — survive normalize_url)
        params = parse_qs(parsed.query)
        lat_vals = params.get("latitude")
        lon_vals = params.get("longitude")
        if lat_vals and lon_vals:
            try:
                return float(lat_vals[0]), float(lon_vals[0])
            except (ValueError, IndexError):
                pass

        # Fall back to hash fragment (client-side only, stripped by cache)
        fragment = parsed.fragment
        if fragment:
            lat, lon = self._parse_coord_params(fragment)
            if lat is not None:
                return lat, lon

        return None, None

    @staticmethod
    def _parse_coord_params(fragment: str) -> tuple:
        """Parse latitude and longitude from a URL fragment like 'latitude=35.68&longitude=139.65&...'"""
        lat = None
        lon = None
        for part in fragment.split("&"):
            if "=" not in part:
                continue
            key, val = part.split("=", 1)
            key = unquote(key).strip()
            val = unquote(val).strip()
            try:
                if key == "latitude":
                    lat = float(val)
                elif key == "longitude":
                    lon = float(val)
            except ValueError:
                continue
        if lat is not None and lon is not None:
            return lat, lon
        return None, None
