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
    # Note: GITHUB_REPO removed - not all subnets have subnet_identity set
    # Note: REGISTRATION_COST removed - get_subnet_burn_cost unreliable (StateDiscardedError)
    # Note: EMISSION removed - SDK returns TAO value, website shows percentage (incompatible)
    # Note: TEMPO removed - not displayed on taostats.io


@dataclass
class SubnetSpec:
    """Specification for a subnet"""
    subnet_id: int
    display_name: str
    subnet_name: str = ""  # Real subnet name from chain


@dataclass
class MetricSpec:
    """Specification for a subnet metric"""
    metric: SubnetMetric
    display_name: str
    unit: str = ""
    is_numeric: bool = False
    tolerance_pct: float = 10.0  # Percentage tolerance for numeric validation


# Cache for subnet data to avoid repeated network calls
_subnet_ids_cache: Optional[List[int]] = None
_subnet_names_cache: Dict[int, str] = {}


def _fetch_active_subnet_ids() -> List[int]:
    """Fetch active subnet IDs from Bittensor network."""
    global _subnet_ids_cache
    if _subnet_ids_cache is not None:
        return _subnet_ids_cache

    try:
        import bittensor as bt
        subtensor = bt.Subtensor(network="finney")
        # Get all subnet netuids (max 128 possible)
        netuids = subtensor.get_subnets()
        # Filter out root network (0) and return as list
        _subnet_ids_cache = [n for n in netuids if n > 0]
        return _subnet_ids_cache
    except Exception:
        # Fallback: use range 1-128 (max possible subnets)
        return list(range(1, 129))


def _fetch_subnet_name(subnet_id: int) -> str:
    """Fetch subnet name from Bittensor network with caching."""
    global _subnet_names_cache
    if subnet_id in _subnet_names_cache:
        return _subnet_names_cache[subnet_id]

    try:
        import bittensor as bt
        subtensor = bt.Subtensor(network="finney")
        info = subtensor.subnet(subnet_id)
        name = info.subnet_name or (
            info.subnet_identity.subnet_name if info.subnet_identity else ""
        )
        _subnet_names_cache[subnet_id] = name
        return name
    except Exception:
        return ""


class SubnetVariable(Variable):
    """
    Variable for Bittensor subnet selection.

    Dynamically fetches active subnets from the Bittensor network.
    Bittensor supports up to 128 subnets (netuid 0-127, where 0 is root).
    """

    def __init__(self, subnet_ids: List[int] = None):
        """
        Initialize subnet variable.

        Args:
            subnet_ids: Specific subnet IDs to sample from (if None, fetches from network)
        """
        super().__init__("subnet", VariableType.NUMERIC)
        if subnet_ids:
            self.subnet_ids = subnet_ids
        else:
            # Dynamically fetch active subnets from network
            self.subnet_ids = _fetch_active_subnet_ids()

    def sample(self, rng: random.Random) -> SubnetSpec:
        subnet_id = rng.choice(self.subnet_ids)
        # Vary display format
        formats = [f"subnet {subnet_id}", f"SN{subnet_id}", f"Subnet {subnet_id}"]
        display = rng.choice(formats)
        # Get real subnet name from chain
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
    }

    def __init__(self, allowed_metrics: List[SubnetMetric] = None):
        super().__init__("metric", VariableType.TEXT)
        self.allowed_metrics = allowed_metrics or list(self.METRICS.keys())

    def sample(self, rng: random.Random) -> MetricSpec:
        metric_type = rng.choice(self.allowed_metrics)
        return self.METRICS[metric_type]

    def get_display_value(self, value: MetricSpec) -> str:
        return value.display_name

    def get_api_value(self, value: MetricSpec) -> str:
        return value.metric.value
