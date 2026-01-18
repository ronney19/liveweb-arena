"""Taostats question templates"""

from .subnet import SubnetInfoTemplate
from .network import NetworkTemplate
from .price import PriceTemplate
from .comparison import ComparisonTemplate
from .analysis import AnalysisTemplate
from .ranking import RankingTemplate
from .variables import SubnetVariable, MetricVariable, SubnetMetric, SubnetSpec, MetricSpec

__all__ = [
    "SubnetInfoTemplate",
    "NetworkTemplate",
    "PriceTemplate",
    "ComparisonTemplate",
    "AnalysisTemplate",
    "RankingTemplate",
    "SubnetVariable",
    "MetricVariable",
    "SubnetMetric",
    "SubnetSpec",
    "MetricSpec",
]
