"""Variables for Taostats question templates"""

import random
from dataclasses import dataclass
from enum import Enum
from typing import Dict, List, Optional

from liveweb_arena.core.validators.base import Variable, VariableType


class SubnetMetric(Enum):
    """Metrics that can be queried for a subnet - only those with verifiable ground truth"""
    NAME = "name"
    OWNER = "owner"
    PRICE = "price"  # Alpha token price
    TAO_IN = "tao_in"  # TAO staked
    MARKET_CAP = "market_cap"
    # Price changes
    PRICE_CHANGE_1H = "price_change_1h"
    PRICE_CHANGE_24H = "price_change_24h"
    PRICE_CHANGE_1W = "price_change_1w"
    PRICE_CHANGE_1M = "price_change_1m"


@dataclass
class SubnetSpec:
    """Specification for a subnet"""
    subnet_id: int
    display_name: str
    subnet_name: str = ""  # Real subnet name from API


@dataclass
class MetricSpec:
    """Specification for a subnet metric"""
    metric: SubnetMetric
    display_name: str
    unit: str = ""
    is_numeric: bool = False
    tolerance_pct: float = 10.0  # Percentage tolerance for numeric validation


# Cache for subnet data to avoid repeated API calls
_subnet_ids_cache: Optional[List[int]] = None
_subnet_names_cache: Dict[int, str] = {}


def _is_usable_name(name: str) -> bool:
    """Check if a subnet name is usable in question text.

    A usable name must contain at least one alphanumeric character.
    Names like '⚒' (single special char) are not usable because they may
    render as invisible/blank in some contexts, making questions unreadable.
    """
    return any(c.isalnum() for c in name)


def _fetch_active_subnet_ids() -> List[int]:
    """Fetch active subnet IDs from taostats API.

    Only returns subnets with usable names (contain alphanumeric characters).

    Raises:
        RuntimeError: If subnet data is not available (API must be called first)
    """
    global _subnet_ids_cache
    if _subnet_ids_cache is not None:
        return _subnet_ids_cache

    from ..api_client import get_cached_subnets

    subnets = get_cached_subnets()
    if not subnets:
        raise RuntimeError(
            "Taostats subnet data not available. Set TAOSTATS_API_KEY environment variable."
        )

    _subnet_ids_cache = [
        int(k) for k, v in subnets.items()
        if k != "0" and _is_usable_name(v.get("name", ""))
    ]
    return _subnet_ids_cache


def _fetch_top_subnet_ids(top_n: int = 10) -> List[int]:
    """
    Fetch top N subnet IDs sorted by Emission (matches taostats.io default sort).

    Args:
        top_n: Number of top subnets to return (default 10 for first page visibility)

    Raises:
        RuntimeError: If subnet data is not available
    """
    from ..api_client import get_cached_subnets

    subnets = get_cached_subnets()
    if not subnets:
        raise RuntimeError(
            "Taostats subnet data not available. Set TAOSTATS_API_KEY environment variable."
        )

    # Sort by emission descending - matches taostats.io default page sort
    sorted_subnets = sorted(
        [
            (int(k), float(v["emission"]) if v.get("emission") is not None else 0.0)
            for k, v in subnets.items()
            if k != "0" and _is_usable_name(v.get("name", ""))
        ],
        key=lambda x: x[1],
        reverse=True
    )
    return [netuid for netuid, _ in sorted_subnets[:top_n]]


def _fetch_subnet_name(subnet_id: int) -> str:
    """Fetch subnet name from cache.

    Args:
        subnet_id: Subnet ID to look up

    Returns:
        Subnet name, or empty string if subnet exists but has no name

    Raises:
        RuntimeError: If subnet data is not available
    """
    global _subnet_names_cache
    if subnet_id in _subnet_names_cache:
        return _subnet_names_cache[subnet_id]

    from ..api_client import get_cached_subnets

    subnets = get_cached_subnets()
    if not subnets:
        raise RuntimeError(
            "Taostats subnet data not available. Set TAOSTATS_API_KEY environment variable."
        )

    subnet = subnets.get(str(subnet_id), {})
    name = str(subnet.get("name", "") or "").strip()
    if not name:
        raise RuntimeError(
            f"Subnet {subnet_id} has empty name in cached data. "
            f"This indicates corrupted cache or a bug in _parse_subnet_data."
        )
    _subnet_names_cache[subnet_id] = name
    return name


class SubnetVariable(Variable):
    """
    Variable for Bittensor subnet selection.

    Uses taostats API to get active subnets.
    With ALL rows visible on the page, can sample from all subnets.
    """

    def __init__(self, subnet_ids: List[int] = None, use_all: bool = True, top_n: int = 10):
        """
        Initialize subnet variable.

        Args:
            subnet_ids: Specific subnet IDs to sample from (if None, uses all or top N)
            use_all: If True, use all active subnets (default). If False, use top N.
            top_n: Number of top subnets to use (only if use_all=False)
        """
        super().__init__("subnet", VariableType.NUMERIC)
        if subnet_ids:
            self.subnet_ids = subnet_ids
        elif use_all:
            # Use all active subnets (page shows ALL rows now)
            self.subnet_ids = _fetch_active_subnet_ids()
        else:
            # Use top N subnets by emission
            self.subnet_ids = _fetch_top_subnet_ids(top_n)

    def sample(self, rng: random.Random) -> SubnetSpec:
        subnet_id = rng.choice(self.subnet_ids)
        # Vary display format
        formats = [f"subnet {subnet_id}", f"SN{subnet_id}", f"Subnet {subnet_id}"]
        display = rng.choice(formats)
        # Get subnet name from cache
        subnet_name = _fetch_subnet_name(subnet_id)
        return SubnetSpec(subnet_id=subnet_id, display_name=display, subnet_name=subnet_name)

    def get_display_value(self, value: SubnetSpec) -> str:
        return value.display_name

    def get_api_value(self, value: SubnetSpec) -> str:
        return str(value.subnet_id)


class MetricVariable(Variable):
    """Variable for subnet metrics - focused on reliable, visible data"""

    METRICS: Dict[SubnetMetric, MetricSpec] = {
        SubnetMetric.NAME: MetricSpec(
            SubnetMetric.NAME, "name", is_numeric=False
        ),
        SubnetMetric.OWNER: MetricSpec(
            SubnetMetric.OWNER, "owner", is_numeric=False
        ),
        SubnetMetric.PRICE: MetricSpec(
            SubnetMetric.PRICE, "alpha price", unit="τ", is_numeric=True,
            tolerance_pct=10.0
        ),
        SubnetMetric.TAO_IN: MetricSpec(
            SubnetMetric.TAO_IN, "TAO staked", unit="τ", is_numeric=True,
            tolerance_pct=10.0
        ),
        SubnetMetric.MARKET_CAP: MetricSpec(
            SubnetMetric.MARKET_CAP, "market cap", unit="τ", is_numeric=True,
            tolerance_pct=10.0
        ),
        SubnetMetric.PRICE_CHANGE_1H: MetricSpec(
            SubnetMetric.PRICE_CHANGE_1H, "1-hour price change", unit="%", is_numeric=True,
            tolerance_pct=20.0  # Higher tolerance for volatile metrics
        ),
        SubnetMetric.PRICE_CHANGE_24H: MetricSpec(
            SubnetMetric.PRICE_CHANGE_24H, "24-hour price change", unit="%", is_numeric=True,
            tolerance_pct=20.0
        ),
        SubnetMetric.PRICE_CHANGE_1W: MetricSpec(
            SubnetMetric.PRICE_CHANGE_1W, "1-week price change", unit="%", is_numeric=True,
            tolerance_pct=20.0
        ),
        SubnetMetric.PRICE_CHANGE_1M: MetricSpec(
            SubnetMetric.PRICE_CHANGE_1M, "1-month price change", unit="%", is_numeric=True,
            tolerance_pct=20.0
        ),
    }

    def __init__(self, allowed_metrics: List[SubnetMetric] = None):
        super().__init__("metric", VariableType.TEXT)
        self.allowed_metrics = allowed_metrics or list(self.METRICS.keys())

    def sample(self, rng: random.Random) -> MetricSpec:
        metric_type = rng.choice(self.allowed_metrics)
        return self.METRICS[metric_type]

    def sample_by_index(self, index: int) -> MetricSpec:
        """
        Sample a specific metric by index.

        Args:
            index: Index into allowed_metrics list (0-based, wraps around)

        Returns:
            MetricSpec for the selected metric
        """
        metric_type = self.allowed_metrics[index % len(self.allowed_metrics)]
        return self.METRICS[metric_type]

    def get_display_value(self, value: MetricSpec) -> str:
        return value.display_name

    def get_api_value(self, value: MetricSpec) -> str:
        return value.metric.value
