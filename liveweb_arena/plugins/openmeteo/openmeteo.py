"""
Open Meteo Plugin.

Uses the Open Meteo docs page as the browsable interface.
The agent navigates to open-meteo.com/en/docs with location coordinates
encoded as both query params (for cache uniqueness) and hash fragment
(for client-side JS form state).

API data is fetched via the Open Meteo forecast API for GT extraction.

Cache support: The docs page is a SvelteKit SPA whose weather data renders
in canvas charts (inaccessible to screen readers). setup_page_for_cache()
injects the API data as readable HTML tables so the cached DOM snapshot
contains accessible weather values without needing JS hydration.
"""

import logging
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse, parse_qs, unquote

from liveweb_arena.plugins.base import BasePlugin
from .api_client import fetch_forecast

logger = logging.getLogger(__name__)


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

    async def setup_page_for_cache(self, page, url: str) -> None:
        """Inject weather data as readable HTML tables for cache mode.

        The SvelteKit docs page renders weather data in canvas charts that
        produce no useful accessibility tree text. This method fetches the
        API data and prepends it as HTML tables so the cached DOM snapshot
        contains all values the agent needs to read.
        """
        lat, lon = self._extract_coords(url)
        if lat is None:
            return

        try:
            data = await fetch_forecast(lat, lon)
        except Exception as e:
            logger.warning("setup_page_for_cache: API fetch failed: %s", e)
            return

        html = self._build_data_html(data)
        escaped = html.replace("\\", "\\\\").replace("`", "\\`").replace("$", "\\$")

        await page.evaluate(f"""
        (() => {{
            const div = document.createElement('div');
            div.id = 'liveweb-weather-data';
            div.setAttribute('role', 'region');
            div.setAttribute('aria-label', 'Weather Data');
            div.innerHTML = `{escaped}`;
            document.body.prepend(div);
        }})()
        """)

    @staticmethod
    def _build_data_html(data: dict) -> str:
        """Format API data as readable HTML tables."""
        parts: list = []
        cw = data.get("current_weather", {})
        if cw:
            parts.append(
                "<h2>Current Weather</h2><table>"
                f"<tr><td>Temperature</td><td>{cw.get('temperature', 'N/A')} C</td></tr>"
                f"<tr><td>Wind Speed</td><td>{cw.get('windspeed', 'N/A')} km/h</td></tr>"
                f"<tr><td>Wind Direction</td><td>{cw.get('winddirection', 'N/A')} deg</td></tr>"
                "</table>"
            )

        daily = data.get("daily", {})
        times = daily.get("time", [])
        if times:
            rows = []
            t_max = daily.get("temperature_2m_max", [])
            t_min = daily.get("temperature_2m_min", [])
            p_max = daily.get("precipitation_probability_max", [])
            for i, t in enumerate(times):
                mx = t_max[i] if i < len(t_max) else "N/A"
                mn = t_min[i] if i < len(t_min) else "N/A"
                pp = p_max[i] if i < len(p_max) else "N/A"
                rows.append(f"<tr><td>{t}</td><td>{mx} C</td><td>{mn} C</td><td>{pp}%</td></tr>")
            parts.append(
                "<h2>Daily Forecast</h2><table>"
                "<tr><th>Date</th><th>Max Temp</th><th>Min Temp</th><th>Precip Prob</th></tr>"
                + "".join(rows) + "</table>"
            )

        hourly = data.get("hourly", {})
        h_times = hourly.get("time", [])
        if h_times:
            today = times[0] if times else h_times[0].split("T")[0]
            rows = []
            h_temp = hourly.get("temperature_2m", [])
            h_hum = hourly.get("relative_humidity_2m", [])
            h_wind = hourly.get("wind_speed_10m", [])
            h_prec = hourly.get("precipitation_probability", [])
            for i, ht in enumerate(h_times):
                if not ht.startswith(today):
                    continue
                tm = h_temp[i] if i < len(h_temp) else ""
                hu = h_hum[i] if i < len(h_hum) else ""
                ws = h_wind[i] if i < len(h_wind) else ""
                pp = h_prec[i] if i < len(h_prec) else ""
                rows.append(
                    f"<tr><td>{ht}</td><td>{tm} C</td><td>{hu}%</td>"
                    f"<td>{ws} km/h</td><td>{pp}%</td></tr>"
                )
            parts.append(
                "<h2>Hourly Forecast (Today)</h2><table>"
                "<tr><th>Time</th><th>Temp</th><th>Humidity</th>"
                "<th>Wind Speed</th><th>Precip Prob</th></tr>"
                + "".join(rows) + "</table>"
            )

        return "".join(parts)

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
