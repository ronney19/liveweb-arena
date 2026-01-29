"""Variables for Taostats question templates"""

import asyncio
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


def _run_async(coro):
    """Run async function synchronously."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # If we're already in an async context, we can't use run_until_complete
            # Return empty result and let the caller handle it
            return None
        return loop.run_until_complete(coro)
    except RuntimeError:
        # No event loop, create a new one
        return asyncio.run(coro)


def _fetch_active_subnet_ids() -> List[int]:
    """Fetch active subnet IDs from taostats API.

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
            "Subnet data not available. Ensure taostats API is called before generating questions."
        )

    _subnet_ids_cache = [int(k) for k in subnets.keys() if k != "0"]
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
            "Subnet data not available. Ensure taostats API is called before generating questions."
        )

    # Sort by emission descending - matches taostats.io default page sort
    sorted_subnets = sorted(
        [(int(k), float(v.get("emission", 0) or 0)) for k, v in subnets.items() if k != "0"],
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
            "Subnet data not available. Ensure taostats API is called before generating questions."
        )

    subnet = subnets.get(str(subnet_id), {})
    name = subnet.get("name", "")
    if name:
        _subnet_names_cache[subnet_id] = name
    return name


class SubnetVariable(Variable):
    """
    Variable for Bittensor subnet selection.

    Uses taostats API to get active subnets.
    """

    def __init__(self, subnet_ids: List[int] = None):
        """
        Initialize subnet variable.

        Args:
            subnet_ids: Specific subnet IDs to sample from (if None, fetches from API)
        """
        super().__init__("subnet", VariableType.NUMERIC)
        if subnet_ids:
            self.subnet_ids = subnet_ids
        else:
            self.subnet_ids = _fetch_active_subnet_ids()

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
